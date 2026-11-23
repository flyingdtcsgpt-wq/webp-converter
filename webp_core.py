"""WebP·PNG 변환 엔진.

- 리사이즈 (긴 변 / 가로 / 세로 / 퍼센트 / 상자 안에 맞춤)
- 스마트 최적화: 결과를 원본과 비교(SSIM, 구조적 유사도)하면서 "눈으로 구분이 거의 안 되는" 선에서
  가장 작은 파일이 되는 품질값을 찾음. 탐색 중 통과한 결과물을 그대로 최종 파일로 사용.
  큰 이미지는 표본 타일로 탐색하고 전체 이미지에서 다시 측정해 보정.
  PNG 등 무손실 원본은 무손실 WebP와도 비교해 더 작은 쪽을 선택.
- PNG 출력: 무손실 최적화(oxipng) / 색상 줄이기(libimagequant)
- EXIF/ICC 색상 프로파일 보존, EXIF 회전 자동 적용, 투명도·애니메이션 GIF 지원
- 압축 파일(ZIP/CBZ/7Z/TAR) 안의 이미지 일괄 변환
"""
from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageSequence, UnidentifiedImageError

try:  # 아이폰 HEIC 사진 지원 (선택)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # pragma: no cover
    HEIF_SUPPORTED = False

try:  # PNG 무손실 최적화 (선택, 없으면 Pillow 기본 압축)
    import oxipng

    HAS_OXIPNG = True
except Exception:  # pragma: no cover
    HAS_OXIPNG = False

try:  # PNG 색상 줄이기 - pngquant 와 같은 libimagequant (선택, 없으면 Pillow 양자화)
    import imagequant

    HAS_IMAGEQUANT = True
except Exception:  # pragma: no cover
    HAS_IMAGEQUANT = False

Image.MAX_IMAGE_PIXELS = 400_000_000  # 매우 큰 사진도 허용

SUPPORTED_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp",
    ".tga", ".ico", ".ppm", ".jfif",
}
if HEIF_SUPPORTED:
    SUPPORTED_EXTS |= {".heic", ".heif"}

LOSSLESS_SOURCE_EXTS = {".png", ".bmp", ".gif", ".tif", ".tiff", ".tga", ".ppm", ".ico"}
WEBP_MAX_DIM = 16383

# 스마트 최적화 단계 → 목표 SSIM
# (목표 밝기 SSIM, 최소 품질). 색차는 4:2:0 서브샘플링으로 품질과 무관하게
# 포화되고 사람 눈도 둔감하므로 밝기(Y) 채널로 판단한다.
SMART_LEVELS = {
    "best": (0.993, 85),      # 최고 화질 (시각적 무손실, 대략 q95 수준)
    "balanced": (0.985, 72),  # 균형 (추천, 대략 q88~90 수준)
    "small": (0.975, 60),     # 작은 용량
}
# PNG 팔레트(색상 줄이기) 판정 기준: 디더링 잡음은 눈에 잘 안 띄므로 살짝 흐리게 한 뒤 비교.
# 띠 현상(계단)은 흐려도 남기 때문에 잡아낼 수 있음. WebP 보다 엄격하게 설정.
# (흐리게 한 비교 기준, 그대로 비교 기준) - 둘 다 만족해야 팔레트 사용.
# 흐린 비교만 쓰면 사진에서 어두운 털·나뭇잎이 뭉개지는 걸 놓쳐서 두 번째 기준을 둠.
PNG_LEVELS = {"best": (0.995, 0.990), "balanced": (0.990, 0.975), "small": (0.985, 0.960)}
PNG_COLOR_STEPS = (256, 128, 64, 32)

# 애니메이션은 SSIM 탐색 대신 고정 품질 사용
ANIM_QUALITY = {"best": 92, "balanced": 85, "small": 75}


@dataclass
class Settings:
    out_format: str = "webp"       # webp | png
    mode: str = "smart"            # smart | quality | lossless
    smart_level: str = "best"      # best | balanced | small
    quality: int = 85              # mode == quality 일 때
    resize_mode: str = "none"      # none | long | width | height | percent | fit | cover
    width: int = 0
    height: int = 0
    long_side: int = 1920
    percent: float = 100.0
    no_upscale: bool = True
    keep_metadata: bool = True
    output_dir: Optional[str] = None  # None 이면 원본과 같은 폴더
    overwrite: bool = False
    # 자르기 (일괄): 비율 (가로, 세로) 예) (16, 9). None 이면 자르지 않음
    crop_ratio: Optional[tuple] = None
    crop_anchor: str = "center"      # center | smart (디테일이 많은 쪽을 남김)
    crop_flip_portrait: bool = True  # 세로 사진에는 비율을 뒤집어 적용 (16:9 → 9:16)


@dataclass
class Result:
    src: Path
    dst: Optional[Path] = None
    orig_bytes: int = 0
    new_bytes: int = 0
    orig_size: tuple = (0, 0)
    new_size: tuple = (0, 0)
    detail: str = ""
    error: Optional[str] = None

    @property
    def saved_ratio(self) -> float:
        if not self.orig_bytes:
            return 0.0
        return 1.0 - self.new_bytes / self.orig_bytes


# ---------------------------------------------------------------- 리사이즈
def target_size(w: int, h: int, s: Settings) -> tuple[int, int]:
    m = s.resize_mode
    if m == "none":
        nw, nh = w, h
    elif m == "cover" and s.width > 0 and s.height > 0 and abs(w / h - s.width / s.height) < 0.01:
        # 꽉 채우기: 이미 W:H 비율로 잘린 상태 → 정확히 W×H 로 (확대 금지면 원본보다 크게는 안 함)
        k = s.width / w
        if s.no_upscale and k > 1:
            nw, nh = w, h
        else:
            nw, nh = s.width, s.height
    else:
        # 꽉 채우기인데 비율이 다른 경우(이 사진만 '자르지 않기'로 지정 등)는 상자 안에 맞춤으로 처리
        if m in ("fit", "cover") and s.width > 0 and s.height > 0:  # 가로×세로 상자 안에 비율 유지
            k = min(s.width / w, s.height / h)
        elif m == "long" and s.long_side > 0:
            k = s.long_side / max(w, h)
        elif m in ("width", "fit") and s.width > 0:
            k = s.width / w
        elif m in ("height", "fit") and s.height > 0:
            k = s.height / h
        elif m == "percent" and s.percent > 0:
            k = s.percent / 100.0
        else:
            k = 1.0
        if s.no_upscale:
            k = min(k, 1.0)
        nw, nh = w * k, h * k
    # WebP 최대 크기 제한
    k = min(1.0, WEBP_MAX_DIM / max(nw, nh))
    return max(1, round(nw * k)), max(1, round(nh * k))


def _resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    if img.size == size:
        return img
    # RGBA 는 Pillow 가 내부적으로 premultiplied 처리 → 가장자리 번짐 없음
    return img.resize(size, Image.LANCZOS, reducing_gap=3.0)


# ---------------------------------------------------------------- 자르기
# 자르기 영역은 '회전을 반영한 원본' 기준의 비율 좌표 (x0, y0, x1, y1), 각 0~1 로 다룬다.
# 그래서 JPEG 를 축소 디코딩했거나 미리보기처럼 작은 이미지에서도 같은 영역을 가리킨다.

def crop_ratio_for(w: int, h: int, s: Settings) -> Optional[tuple]:
    """이 이미지에 적용할 자르기 비율. 없으면 None."""
    if s.resize_mode == "cover" and s.width > 0 and s.height > 0:
        return (s.width, s.height)  # 꽉 채우기는 W:H 로 자르는 것과 같다 (비율 뒤집기 없음)
    if not s.crop_ratio:
        return None
    rw, rh = s.crop_ratio
    if s.crop_flip_portrait and (rw - rh) * (w - h) < 0:  # 가로 비율인데 세로 사진(또는 반대)
        rw, rh = rh, rw
    return rw, rh


def _crop_extent(w: int, h: int, ratio) -> tuple[float, float]:
    """비율대로 자를 때 남는 가로·세로 비율 (1 이면 그 방향은 안 자름)."""
    R = ratio[0] / ratio[1]
    if abs(w / h - R) < 1e-3:
        return 1.0, 1.0
    if w / h > R:
        return (h * R) / w, 1.0
    return 1.0, (w / R) / h


def _smart_offset(img: Image.Image, horizontal: bool, frac: float) -> float:
    """잘라낼 방향(가로/세로)에서 디테일(경계선)이 가장 많이 들어오는 위치 (0~1-frac).
    가운데 쪽을 약간 선호해서, 고르게 복잡한 사진은 가운데를 남긴다."""
    g = img.convert("L")
    g.thumbnail((256, 256))
    a = np.asarray(g, dtype=np.float32)
    if min(a.shape) < 3:
        return (1 - frac) / 2
    e = np.zeros_like(a)
    e[:, 1:] += np.abs(np.diff(a, axis=1))
    e[1:, :] += np.abs(np.diff(a, axis=0))
    prof = e.sum(axis=0) if horizontal else e.sum(axis=1)
    L = prof.size
    n = max(1, min(L, round(L * frac)))
    if n >= L:
        return 0.0
    cs = np.concatenate([[0.0], np.cumsum(prof, dtype=np.float64)])
    sums = cs[n:] - cs[:-n]  # 위치별 창 안의 디테일 합
    pos = np.arange(sums.size)
    centre = (L - n) / 2
    weight = 1 - 0.15 * np.abs(pos - centre) / max(centre, 1)
    best = int(np.argmax(sums * weight))
    return best / L


def auto_crop_box(img: Image.Image, s: Settings) -> Optional[tuple]:
    """일괄 설정에 따른 자르기 영역 (비율 좌표). 자를 필요가 없으면 None."""
    w, h = img.size
    ratio = crop_ratio_for(w, h, s)
    if not ratio:
        return None
    fw, fh = _crop_extent(w, h, ratio)
    if fw >= 1 and fh >= 1:
        return None
    if fw < 1:
        x0 = _smart_offset(img, True, fw) if s.crop_anchor == "smart" else (1 - fw) / 2
        return (x0, 0.0, x0 + fw, 1.0)
    y0 = _smart_offset(img, False, fh) if s.crop_anchor == "smart" else (1 - fh) / 2
    return (0.0, y0, 1.0, y0 + fh)


def _crop_fraction(w: int, h: int, s: Settings, crop) -> tuple[float, float]:
    """자른 뒤 남는 가로·세로 비율 (이미지 내용을 보지 않고 계산 - 축소 디코딩 판단용)."""
    if crop == "none":
        return 1.0, 1.0
    if isinstance(crop, tuple):
        return crop[2] - crop[0], crop[3] - crop[1]
    ratio = crop_ratio_for(w, h, s)
    return _crop_extent(w, h, ratio) if ratio else (1.0, 1.0)


def _apply_crop(img: Image.Image, box) -> Image.Image:
    if not box:
        return img
    w, h = img.size
    x0, y0 = int(round(box[0] * w)), int(round(box[1] * h))
    x1, y1 = int(round(box[2] * w)), int(round(box[3] * h))
    x0, y0 = min(max(x0, 0), w - 1), min(max(y0, 0), h - 1)
    x1, y1 = min(max(x1, x0 + 1), w), min(max(y1, y0 + 1), h)
    if (x0, y0, x1, y1) == (0, 0, w, h):
        return img
    return img.crop((x0, y0, x1, y1))


def _crop_label(box, s: Settings, crop) -> str:
    if not box:
        return ""
    if isinstance(crop, tuple):
        return " · ✂ 직접 지정"
    if s.resize_mode == "cover":
        return ""
    return " · ✂ 자름"


# ---------------------------------------------------------------- 화질 측정 (SSIM)
# 7x7 창의 SSIM 평균을 구하되, 창을 한 칸씩 밀지 않고 7픽셀 간격으로 겹치지 않게 놓는다.
# 모든 위치를 재는 방식(슬라이딩 창)과 값이 사실상 같으면서(평균 차 0.00001, 표준편차 0.0001 이하)
# 누적합 대신 블록 합 한 번이면 되어 10배쯤 빠르다. 창 개수가 적은 작은 이미지는 위치를 어긋나게
# 여러 번 재서 표본 수를 확보한다. (창 크기를 8x8 로 바꾸면 값이 +0.0005 치우치므로 7x7 유지)
_C1, _C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
_WIN = 7


def _luma(img: Image.Image) -> np.ndarray:
    """밝기(Y) 를 float32 로. 분산 계산 정밀도를 위해 128 을 빼서 중심화."""
    if img.mode == "RGBA":  # 투명 영역은 비교에서 영향 없도록 회색 배경에 합성
        bg = Image.new("RGBA", img.size, (128, 128, 128, 255))
        img = Image.alpha_composite(bg, img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.asarray(img.convert("F")) - np.float32(128.0)  # Pillow 가 C 로 계산 (0.299R+0.587G+0.114B)


def _phases(shape) -> tuple:
    """창 격자를 몇 번(어느 위치에서) 놓을지. 창이 많으면 한 번이면 충분."""
    n = (shape[0] // _WIN) * (shape[1] // _WIN)
    if n >= 30000 or min(shape) < _WIN * 2:
        return (0,)
    return (0, 3) if n >= 12000 else (0, 2, 4, 5)


def _win_mean(x: np.ndarray, off: int) -> np.ndarray:
    """off 만큼 밀어서 놓은 7x7 창들의 평균 (창끼리 겹치지 않음)."""
    x = x[off:, off:]
    hb, wb = x.shape[0] // _WIN, x.shape[1] // _WIN
    if hb == 0 or wb == 0:  # 7픽셀보다 작은 이미지: 전체를 창 하나로
        return np.array([[x.mean(dtype=np.float64)]])
    blocks = x[:hb * _WIN, :wb * _WIN].reshape(hb, _WIN, wb, _WIN)
    return np.einsum("ikjl->ij", blocks, dtype=np.float64) * (1.0 / (_WIN * _WIN))


_TILE = 112  # 표본 타일 한 변. 7(측정 창)과 16(WebP 매크로블록)의 공배수라 창·블록이 이음매에 걸치지 않음


def _sample_tiles(img: Image.Image, max_px: int = 2_500_000, n: int = 10) -> Image.Image:
    """큰 이미지는 원본 해상도 그대로 작은 타일을 고르게 뽑아 모자이크로 만든다 (탐색용, 속도/메모리).
    같은 면적이면 큰 타일 몇 개보다 작은 타일 여러 개가 이미지 전체를 훨씬 잘 대표한다
    (실측: 16개×336px 대비 표본 오차 1/2 이하)."""
    w, h = img.size
    nx, ny = min(n, w // _TILE), min(n, h // _TILE)
    if w * h <= max_px or nx < 2 or ny < 2:
        return img
    xs = np.linspace(0, w - _TILE, nx).astype(int)
    ys = np.linspace(0, h - _TILE, ny).astype(int)
    mosaic = Image.new(img.mode, (_TILE * nx, _TILE * ny))
    for i, y in enumerate(ys):
        for j, x in enumerate(xs):
            mosaic.paste(img.crop((x, y, x + _TILE, y + _TILE)), (j * _TILE, i * _TILE))
    return mosaic


def _inner_mask(shape) -> np.ndarray:
    """모자이크에서 타일 테두리(이음매에 붙은) 창을 뺀 마스크. 이음매는 원본에 없는 가짜 경계라
    그 주변은 인코딩 품질이 실제보다 나쁘게 나온다 (포함하면 SSIM 이 최대 0.01~0.02 낮게 측정됨)."""
    per = _TILE // _WIN
    i, j = np.arange(shape[0]) % per, np.arange(shape[1]) % per
    return np.outer((i >= 1) & (i < per - 1), (j >= 1) & (j < per - 1))


def _ssim_map(a, b, ab, bb, mu_a, saa, off: int) -> np.ndarray:
    mu_b = _win_mean(b, off)
    sbb = _win_mean(bb, off) - mu_b * mu_b
    sab = _win_mean(ab, off) - mu_a * mu_b
    ma, mb = mu_a + 128.0, mu_b + 128.0
    return ((2 * ma * mb + _C1) * (2 * sab + _C2)) / ((ma * ma + mb * mb + _C1) * (saa + sbb + _C2))


class _Reference:
    """이미지 하나의 화질 측정기.
    - 작은 이미지: 이미지 전체로 측정 (proxy 가 img 자신)
    - 큰 이미지: 탐색은 표본 타일 모자이크(proxy)로 싸게 하고, 최종 결과는 score_full() 로 이미지 전체를 측정"""
    blur = 0.0

    def __init__(self, img: Image.Image, proxy: Image.Image | None = None):
        self.img = img
        self.proxy = _sample_tiles(img) if proxy is None else proxy
        self.whole = self.proxy is img
        a = self.a = _luma(self._prep(self.proxy))
        aa = a * a
        self.offs = _phases(a.shape) if self.whole else (0,)
        self.mask = None if self.whole else _inner_mask((a.shape[0] // _WIN, a.shape[1] // _WIN))
        self.stats = []  # 원본 쪽 통계는 한 번만 계산해 재사용
        for off in self.offs:
            mu = _win_mean(a, off)
            self.stats.append((mu, _win_mean(aa, off) - mu * mu))

    def _prep(self, im: Image.Image) -> Image.Image:
        return im.filter(ImageFilter.GaussianBlur(self.blur)) if self.blur else im

    def _open(self, data) -> Image.Image:
        dec = data if isinstance(data, Image.Image) else Image.open(io.BytesIO(data))
        # 모드가 같을 때 convert() 를 부르면 통째로 복사만 하므로 건너뜀
        return dec if dec.mode == self.img.mode else dec.convert(self.img.mode)

    def score(self, data: bytes | Image.Image) -> float:
        """proxy 를 인코딩한 결과(또는 전체 크기 결과 - 그 경우 같은 타일을 뽑아서)의 점수."""
        dec = self._open(data)
        if not self.whole and dec.size == self.img.size:
            dec = _sample_tiles(dec)
        a, b = self.a, _luma(self._prep(dec))
        ab, bb = a * b, b * b
        total, count = 0.0, 0
        for off, (mu_a, saa) in zip(self.offs, self.stats):
            m = _ssim_map(a, b, ab, bb, mu_a, saa, off)
            if self.mask is not None:
                m = m[self.mask]
            total += float(m.sum())
            count += m.size
        return total / count

    def score_full(self, data: bytes | Image.Image, rows: int = _WIN * 96) -> float:
        """이미지 전체의 실제 점수. 메모리를 아끼려고 가로 띠 단위로 계산한다 (4천만 화소도 띠당 100MB 미만).
        띠의 가로·세로를 7의 배수로 잘라 두면 블록 합을 복사 없이(뷰로) 계산할 수 있어 훨씬 빠르다."""
        dec = self._open(data)
        w, h = self.img.size
        w7 = (w // _WIN) * _WIN
        total, count = 0.0, 0
        for y in range(0, h, rows):
            y2 = min(h, y + rows)
            y2 = y + ((y2 - y) // _WIN) * _WIN  # 마지막 띠: 7행 미만 자투리는 버림 (창이 안 만들어짐)
            if y2 <= y:
                break
            box = (0, y, w7, y2)
            a = _luma(self._prep(self.img.crop(box)))
            b = _luma(self._prep(dec.crop(box)))
            mu_a = _win_mean(a, 0)
            saa = _win_mean(a * a, 0) - mu_a * mu_a
            m = _ssim_map(a, b, a * b, b * b, mu_a, saa, 0)
            total += float(m.sum())
            count += m.size
        return total / count


class _SoftReference(_Reference):
    """디더링 평가용: 사람 눈처럼 아주 미세한 점 무늬는 뭉개서 비교."""
    blur = 0.8


# ---------------------------------------------------------------- 인코딩
def _encode(img: Image.Image, extra: dict, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", **extra, **kw)
    return buf.getvalue()


FINAL_METHOD = 5


def _lossy(img, extra, q, method=None) -> bytes:
    if method is None:
        method = FINAL_METHOD
    kw = dict(quality=q, method=method)
    if img.mode == "RGBA":
        kw.update(alpha_quality=100)
    return _encode(img, extra, **kw)


def _lossless(img, extra, fast: bool = False) -> bytes:
    # method 6 / quality 100 은 수십 배 느리면서 이득이 1% 미만이라 5/90 을 사용
    if fast:
        method, q = 1, 20
    elif img.size[0] * img.size[1] > 8_000_000:
        method, q = 4, 80
    else:
        method, q = 5, 90
    return _encode(img, extra, lossless=True, quality=q, method=method, exact=False)


# 노이즈가 많은 사진처럼 q100 으로도 목표 화질에 못 미치는 경우에 저장할 품질.
# q100 은 용량이 5~10배 커지는 데 비해 화질 이득이 거의 없어서(예: SSIM 0.9885 → 0.9886) 이 값으로 저장하고,
# 화질이 떨어질 수 있다는 안내를 결과 설명에 붙인다. (q85 의 실제 SSIM 은 사진에 따라 0.91~0.98)
UNREACHABLE_Q = 85
UNREACHABLE_NOTE = "목표 화질 도달 불가 → 화질이 떨어질 수 있음"

_q_hist: dict = {}  # 단계별 최근 결과 품질 (탐색 시작점을 정하는 데 사용)
_q_lock = threading.Lock()  # 여러 작업 스레드가 함께 읽고 씀
_START_Q = {"best": 95, "balanced": 88, "small": 82}


def _start_q(level: str) -> int:
    with _q_lock:
        hist = sorted(_q_hist.get(level, ()))
    if hist:
        return hist[len(hist) // 2]  # 중앙값: 종류가 섞인 배치에서도 안정적
    return _START_Q.get(level, 90)


def _remember_q(level: str, q: int):
    from collections import deque
    with _q_lock:
        _q_hist.setdefault(level, deque(maxlen=15)).append(q)


def _search_min_q(score, target: float, lo: int, start: int, hi: int = 100) -> int:
    """score(q) >= target 인 최소 q 를 찾음 (score 는 q 에 대해 거의 단조 증가).
    측정한 SSIM 값으로 목표 지점을 보간해서 다음 후보를 고르므로 보통 3~4회면 끝남.
    목표를 만족하는 q 가 없으면 hi 반환."""
    pts: dict[int, float] = {}
    fail, good = lo - 1, None  # fail 이하는 미달, good 은 만족 확인된 최소값
    q = min(max(start, lo), hi)
    for _ in range(16):
        if q not in pts:
            pts[q] = score(q)
        if pts[q] >= target:
            good = q if good is None else min(good, q)
        else:
            fail = max(fail, q)
        if good is not None and good == fail + 1:
            return good
        if good is None and fail >= hi:
            return hi
        b_lo, b_hi = fail + 1, (good - 1 if good is not None else hi)
        # 다음 후보: 목표를 사이에 둔 두 점(없으면 가장 최근 두 점)으로 선형 보간
        if good is not None and fail in pts:
            (q0, s0), (q1, s1) = (fail, pts[fail]), (good, pts[good])
        else:
            known = sorted(pts.items(), key=lambda kv: abs(kv[1] - target))[:2]
            (q0, s0), (q1, s1) = (known + known)[:2]
        if q1 != q0 and abs(s1 - s0) > 1e-6:
            est = q0 + (target - s0) * (q1 - q0) / (s1 - s0)
            cand = int(np.ceil(est))
        else:  # 점이 하나뿐: 경험적 기울기(품질 1당 SSIM 약 0.0012)로 목표까지 한 번에 이동
            step = (target - pts[q]) / 0.0012
            step = max(1, min(12, int(np.ceil(abs(step)))))
            cand = q - step if pts[q] >= target else q + step
        cand = min(max(cand, b_lo), b_hi)
        if cand in pts:
            cand = (b_lo + b_hi) // 2
        q = cand
    return good if good is not None else hi


# 큰 이미지에서 품질을 내려 다시 인코딩할 최소 예상 용량 절감 비율.
# 실측(9~40MP 13장): 5% 기준은 시간 +18% 에 용량 -1.7%, 10% 기준은 시간 +7% 에 용량 -1.1%, 안 하면 가장 빠름.
# 표본 추정이 크게 빗나간 사진에서 파일이 필요 이상 커지는 것만 막는 용도라 10% 로 둔다.
# (탐색 목표에 여유를 두거나 미세한 미달을 허용하는 방식도 실측했지만 시간·용량 차이가 없어 쓰지 않는다)
_DOWN_MIN_SAVING = 0.10


def _finish_large(img, extra, ref, score, sizes: dict, target: float, floor_q: int, q: int):
    """큰 이미지 마무리: 표본으로 찾은 품질 q 로 전체를 인코딩하고 이미지 전체에서 실제 화질을 잰다.
    표본 추정은 실제와 보통 ±0.001(품질 1단계쯤) 어긋나는데, 그 차이는 같은 이미지 안에서 품질값이
    달라도 거의 일정하다. 그래서 한 번 잰 차이를 보정값으로 써서 목표에 못 미치면 올리고,
    용량이 충분히(10% 이상) 줄 것 같으면 내린다. (전체 인코딩은 비싸므로 보통 1번, 많아야 2~3번)"""
    data = _lossy(img, extra, q)
    s = ref.score_full(data)
    failed_q = floor_q - 1  # 전체 인코딩으로 미달이 확인된 가장 높은 품질
    while s < target and q < 100:  # 부족: 보정된 목표로 표본에서 다시 찾아(싸다) 전체를 다시 인코딩
        failed_q = q
        d = s - score(q)
        q = _search_min_q(score, target - d, q + 1, q + 1)
        data = _lossy(img, extra, q)
        s = ref.score_full(data)
    if s >= target and q - 1 > failed_q:
        # 여유가 있는지: 보정값을 적용하면 한 단계 아래(탐색 때 이미 잰 값)도 통과하는가?
        t2 = target - (s - score(q)) + 0.0002
        if score(q - 1) >= t2:
            q2 = q - 1 if q - 2 <= failed_q else _search_min_q(score, t2, failed_q + 1, q - 2, hi=q - 1)
            if sizes[q2] <= (1 - _DOWN_MIN_SAVING) * sizes[q]:
                data2 = _lossy(img, extra, q2)
                s2 = ref.score_full(data2)
                if s2 >= target:
                    data, s, q = data2, s2, q2
    return data, s, q


MINI_GUIDE_MIN_PX = 600_000  # 이 크기 이상인 이미지는 작은 표본으로 탐색 시작점을 먼저 잡는다


def _guided_start(img: Image.Image, target: float, floor_q: int, level: str) -> int:
    """정밀 탐색을 시작할 품질값. 이미지가 어느 정도 크면 4×4 표본 타일(면적 15~20%)로 대략 찾아서,
    정밀 탐색이 정답 근처에서 시작하게 한다. 최종 결과는 정밀 탐색이 이미지 전체로 확인하므로
    시작점이 빗나가도 결과는 같고 시간만 조금 더 든다."""
    start = _start_q(level)
    if not MINI_GUIDE or img.size[0] * img.size[1] < MINI_GUIDE_MIN_PX:
        return start
    mini = _sample_tiles(img, max_px=0, n=4)
    if mini is img:
        return start
    ref = _Reference(img, proxy=mini)
    cache: dict[int, float] = {}

    def score(q: int) -> float:
        if q not in cache:
            cache[q] = ref.score(_lossy(mini, {}, q))
        return cache[q]

    return _search_min_q(score, target, floor_q, start)


MINI_GUIDE = True


def _smart(img: Image.Image, extra: dict, level: str, lossless_src: bool,
           orig_bytes: int) -> tuple[bytes, str]:
    target, floor_q = SMART_LEVELS.get(level, SMART_LEVELS["balanced"])
    # 큰 이미지는 원본 해상도의 표본 타일 모자이크로 품질을 탐색하고(WebP 품질은 국소적으로 작동하므로
    # 결과가 거의 같고 훨씬 빠름), 전체 인코딩 결과는 이미지 전체에서 다시 측정해 보정한다.
    ll_sample = sample = None
    if lossless_src:
        # 무손실 원본(PNG 등)은 무손실 WebP 가 더 작을 수 있다 (스크린샷·선화·도표).
        # 작은 표본으로 먼저 판단: 무손실이 '가장 낮은 품질의 손실'보다도 작으면 손실 탐색은 할 필요가 없다.
        # (빠른 무손실 설정은 최종 무손실보다 크게 나오고, 최저 품질 손실은 최종 손실보다 작게 나오므로
        #  이 비교를 통과하면 무손실이 확실히 이긴다)
        sample = _sample_tiles(img, max_px=400_000, n=4)
        ll_sample = len(_lossless(sample, {}, fast=True))
        if ll_sample <= 0.8 * len(_lossy(sample, {}, floor_q)):
            return _lossless(img, extra), "무손실 (원본과 픽셀 동일)"

    ref = _Reference(img)
    proxy, whole = ref.proxy, ref.whole
    scores: dict[int, float] = {}
    sizes: dict[int, int] = {}
    blobs: dict[int, bytes] = {}

    # 탐색도 최종본과 같은 설정으로 인코딩한다. 이미지 전체로 탐색하는 경우(대부분) 통과한 결과물을
    # 그대로 최종 파일로 쓰므로 '최종 인코딩 + 재확인 + 품질 올려 재시도' 단계가 없어진다.
    def score(q: int) -> float:
        if q not in scores:
            data = _lossy(proxy, extra if whole else {}, q)
            sizes[q] = len(data)
            if whole:
                blobs[q] = data
            scores[q] = ref.score(data)
        return scores[q]

    fallback_q = max(UNREACHABLE_Q, floor_q)
    if whole:
        best_q = _search_min_q(score, target, floor_q, _guided_start(img, target, floor_q, level))
        final_ssim = score(best_q)
        lossy = blobs[best_q]
        lossy_ok = final_ssim >= target
        if not lossy_ok and best_q > fallback_q:
            best_q = fallback_q
            final_ssim = score(best_q)
            lossy = blobs[best_q]
    else:
        best_q = _search_min_q(score, target, floor_q, _start_q(level))
        if best_q == 100 and score(100) < target - 0.004:
            # 표본 추정 오차(최대 약 0.0035)를 넘게 모자라면 확실히 도달 불가 → q100 전체 인코딩은 건너뜀
            best_q = fallback_q
            lossy = _lossy(img, extra, best_q)
            final_ssim = ref.score_full(lossy)
            lossy_ok = False
        else:
            lossy, final_ssim, best_q = _finish_large(img, extra, ref, score, sizes, target, floor_q, best_q)
            lossy_ok = final_ssim >= target
            if not lossy_ok and best_q > fallback_q:
                best_q = fallback_q
                lossy = _lossy(img, extra, best_q)
                final_ssim = ref.score_full(lossy)
    blobs.clear()
    if lossy_ok:
        _remember_q(level, best_q)
    detail = f"손실 q{best_q} (SSIM {final_ssim:.4f})"
    if not lossy_ok:
        detail += f" · {UNREACHABLE_NOTE}"

    # 무손실 원본인데 위에서 바로 결정되지 않은 경우(사진·혼합): 손실 결과와 크기를 비교.
    # JPEG 같은 손실 원본은 무손실로 저장해도 화질 이득 없이 용량만 커지므로 애초에 대상이 아니다.
    if lossless_src:
        if not lossy_ok:
            limit = max(orig_bytes, len(lossy))  # 목표 화질 미달 → 원본보다 작으면 무손실
        else:
            limit = len(lossy)
        # 표본에서 잰 '무손실/손실' 비율로 전체 무손실 크기를 추정해, 가망 없으면 본 인코딩을 건너뜀
        # (사진은 무손실이 대개 3~5배 큼)
        if sample is img:
            est = ll_sample
        else:
            est = ll_sample / len(_lossy(sample, {}, best_q)) * len(lossy)
        if est < limit * 1.15:
            ll = _lossless(img, extra)
            if len(ll) <= limit:
                return ll, "무손실 (원본과 픽셀 동일)"
    return lossy, detail


# ---------------------------------------------------------------- PNG
def _oxipng_job(raw: bytes, level: int, keep_meta: bool) -> bytes:
    """oxipng 실행 (별도 프로세스에서도 호출됨 - 최상위 함수여야 함)."""
    import oxipng as _ox
    strip = _ox.StripChunks.none() if keep_meta else _ox.StripChunks.safe()
    return _ox.optimize_from_memory(raw, level=level, strip=strip)


# oxipng 는 실행 중 GIL 을 놓지 않아 스레드로는 한 번에 하나씩만 돌아감 → 프로세스 풀로 병렬화.
# (spawn 방식: 윈도우·맥 기본값이고, 리눅스의 fork 는 oxipng 내부 스레드와 충돌해 멈출 수 있음)
_png_pool = None
_png_pool_lock = threading.Lock()


def start_png_pool(workers: int):
    global _png_pool
    if not HAS_OXIPNG:
        return
    with _png_pool_lock:
        if _png_pool is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            try:
                _png_pool = ProcessPoolExecutor(max(1, workers), mp_context=mp.get_context("spawn"))
            except Exception:
                _png_pool = None


def stop_png_pool():
    global _png_pool
    with _png_pool_lock:
        if _png_pool is not None:
            _png_pool.shutdown(wait=False, cancel_futures=True)
            _png_pool = None


def _png_save(img: Image.Image, extra: dict) -> bytes:
    buf = io.BytesIO()
    kw = {k: v for k, v in extra.items() if k in ("icc_profile", "exif")}
    # Pillow 의 PNG 저장은 icc_profile 을 안 넘기면 원본에 있던 것을 알아서 넣는다.
    # '메타데이터 유지'를 껐을 때도 남지 않도록 명시적으로 None 을 넘김.
    kw.setdefault("icc_profile", None)
    if HAS_OXIPNG:
        img.save(buf, "PNG", compress_level=1, **kw)  # 최종 압축은 oxipng 가 담당
        px = img.size[0] * img.size[1]
        level = 2 if px <= 8_000_000 else 1  # 큰 이미지는 속도 우선
        pool = _png_pool
        if pool is not None:
            try:
                return pool.submit(_oxipng_job, buf.getvalue(), level, bool(extra)).result()
            except Exception:
                pass  # 프로세스 풀 문제 시 현재 스레드에서 처리
        return _oxipng_job(buf.getvalue(), level, bool(extra))
    img.save(buf, "PNG", optimize=True, **kw)
    return buf.getvalue()


def _quantize(img: Image.Image, colors: int = 256, quality: int = 100) -> Image.Image:
    """색상 수를 줄인 팔레트 이미지 (투명도 유지, 디더링 적용)."""
    if HAS_IMAGEQUANT:
        # imagequant.quantize_pil_image 는 픽셀을 파이썬 반복문으로 변환해서 매우 느리고
        # GIL 을 잡아 다른 작업까지 멈추므로, Pillow 의 tobytes() 로 바로 넘김 (수십 배 빠름)
        rgba = img.convert("RGBA")
        data, palette = imagequant.quantize_raw_rgba_bytes(
            rgba.tobytes(), rgba.width, rgba.height, dithering_level=1.0,
            max_colors=colors, min_quality=0, max_quality=quality)
        q = Image.frombytes("P", rgba.size, data)
        q.putpalette(palette, rawmode="RGBA")
    else:
        q = img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                         dither=Image.Dither.FLOYDSTEINBERG)
    if img.mode == "RGB" and "transparency" in q.info:  # 불투명 이미지에 생긴 투명색 제거
        q.info.pop("transparency", None)
    return q


def _png_smart(img: Image.Image, extra: dict, level: str) -> tuple[bytes, str]:
    t_soft, t_raw = PNG_LEVELS.get(level, PNG_LEVELS["balanced"])
    # 이미 256색 이하인 그래픽은 무손실이 최선 (oxipng 가 자동으로 팔레트 PNG 로 바꿈)
    if img.getcolors(256) is not None:
        lossless = _png_save(img, extra)
        if not HAS_OXIPNG and img.mode == "RGB":  # oxipng 가 없으면 직접 무손실 팔레트 변환
            p = img.convert("P", palette=Image.ADAPTIVE, colors=256)
            if p.convert("RGB").tobytes() == img.tobytes():
                pal = _png_save(p, extra)
                if len(pal) < len(lossless):
                    lossless = pal
        return lossless, "무손실 (원본과 픽셀 동일)"
    soft, raw = _SoftReference(img), _Reference(img)
    chosen = None
    for n in PNG_COLOR_STEPS:  # 색 수를 줄여가며 두 기준을 모두 만족하는 가장 적은 색 수
        q = _quantize(img, n)
        if raw.score(q) < t_raw:  # 사진은 대개 여기서 탈락 → 흐림 처리가 필요한 두 번째 검사는 생략
            break
        sc = soft.score(q)
        if sc < t_soft:
            break
        chosen = (q, n, sc)
    # 압축(oxipng)은 최종 결과에 한 번만. 팔레트가 통과하면 무손실은 만들 필요 없음
    # (256색 초과 이미지에서 팔레트 PNG 는 무손실보다 항상 훨씬 작음)
    if chosen:
        q, n, sc = chosen
        return _png_save(q, extra), f"팔레트 {n}색 (유사도 {sc:.4f})"
    return _png_save(img, extra), "무손실 (원본과 픽셀 동일)"


def _convert_png(img: Image.Image, s: Settings, extra: dict) -> tuple[bytes, str]:
    if s.mode == "lossless":
        return _png_save(img, extra), "무손실"
    if s.mode == "quality":
        q = max(1, min(100, int(s.quality)))
        if not HAS_IMAGEQUANT:  # 대체 방식: 품질값을 색 수로 환산
            n = max(2, round(256 * q / 100))
            return _png_save(_quantize(img, n), extra), f"팔레트 {n}색"
        return _png_save(_quantize(img, 256, q), extra), f"팔레트 (품질 {q})"
    return _png_smart(img, extra, s.smart_level)


def _normalize_mode(img: Image.Image) -> Image.Image:
    if img.mode in ("RGB", "RGBA"):
        out = img
    elif img.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(img, dtype=np.float64)
        mx = arr.max() or 1
        out = Image.fromarray((arr / mx * 255).clip(0, 255).astype(np.uint8)).convert("RGB")
    else:
        has_alpha = img.mode in ("LA", "PA", "La", "RGBa") or "transparency" in img.info
        out = img.convert("RGBA" if has_alpha else "RGB")
    # 알파가 전부 불투명이면 제거 (용량 절약)
    if out.mode == "RGBA" and out.getchannel("A").getextrema() == (255, 255):
        out = out.convert("RGB")
    return out


def _metadata(img: Image.Image, keep: bool) -> dict:
    if not keep:
        return {}
    extra = {}
    icc = img.info.get("icc_profile")
    if icc:
        extra["icc_profile"] = icc
    try:
        exif = Image.Exif()
        exif.load(img.getexif().tobytes())  # 사본 (원본 객체를 건드리면 회전 정보가 사라짐)
        if exif:
            exif[0x0112] = 1  # 회전은 이미 픽셀에 적용했으므로 방향 태그 초기화
            extra["exif"] = exif.tobytes()
    except Exception:
        pass
    return extra


_name_lock = threading.Lock()
_reserved: set[str] = set()  # 동시에 변환 중인 파일끼리 같은 이름(a.jpg/a.png → a.webp) 방지


def output_path(src: Path, s: Settings, reserve: bool = False) -> Path:
    out_dir = Path(s.output_dir) if s.output_dir else src.parent
    ext = ".png" if s.out_format == "png" else ".webp"
    dst = out_dir / (src.stem + ext)
    if dst.resolve() == src.resolve():
        dst = out_dir / (src.stem + "_opt" + ext)
    base, i = dst.with_suffix(""), 1
    with _name_lock:
        def taken(p: Path) -> bool:
            return str(p.resolve()).lower() in _reserved or (not s.overwrite and p.exists())
        while taken(dst):
            dst = Path(f"{base}_{i}{ext}")
            i += 1
        if reserve:
            _reserved.add(str(dst.resolve()).lower())
    return dst


def release_names():
    """한 번의 일괄 변환이 끝나면 호출 (다음 작업에서 덮어쓰기 허용)."""
    with _name_lock:
        _reserved.clear()


def cpu_workers() -> int:
    """실제로 쓸 수 있는 CPU 코어 수 (컨테이너·작업 관리자 제한 반영). 코어보다 많은 스레드는 오히려 느림."""
    n = None
    if hasattr(os, "process_cpu_count"):
        n = os.process_cpu_count()
    elif hasattr(os, "sched_getaffinity"):
        n = len(os.sched_getaffinity(0))
    return max(1, min(16, n or os.cpu_count() or 2))


BIG_IMAGE_PX = 25_000_000
_big_sem = threading.BoundedSemaphore(2)  # 2,500만 화소 이상은 동시에 2장까지만 (메모리 보호)


def set_big_image_limit(n: int):
    global _big_sem
    _big_sem = threading.BoundedSemaphore(max(1, n))


def _jpeg_draft(im: Image.Image, s: Settings, crop=None):
    """JPEG 를 크게 줄일 때는 처음부터 1/2·1/4·1/8 크기로 디코딩 (화질 차이 없이 2~4배 빠름).
    최종 크기의 2배 이상은 남겨서 LANCZOS 축소 화질을 유지. (자르기가 있으면 자른 영역 기준)"""
    if im.format not in ("JPEG", "MPO") or s.resize_mode == "none":
        return
    w, h = im.size
    try:
        swap = im.getexif().get(0x0112, 1) in (5, 6, 7, 8)
    except Exception:
        swap = False
    tw, th = (h, w) if swap else (w, h)
    fw, fh = _crop_fraction(tw, th, s, crop)
    cw, ch = max(1, round(tw * fw)), max(1, round(th * fh))
    nw, nh = target_size(cw, ch, s)
    k = max(nw / cw, nh / ch)
    if k < 0.5:
        im.draft("RGB", (int(w * k * 2) + 1, int(h * k * 2) + 1))


def convert(src: Path | str, s: Settings, dst: Path | None = None, crop=None) -> Result:
    """src 를 변환해 저장. dst 를 주면 그 경로에 저장 (압축 파일 내부 처리용).
    crop: None = 일괄 설정대로, "none" = 이 파일은 자르지 않음, (x0, y0, x1, y1) = 직접 지정한 영역(0~1)"""
    src = Path(src)
    res = Result(src=src)
    try:
        res.orig_bytes = src.stat().st_size
        with Image.open(src) as im:
            res.orig_size = im.size
            _jpeg_draft(im, s, crop)
            big = im.size[0] * im.size[1] >= BIG_IMAGE_PX
            if big:
                _big_sem.acquire()
            try:
                data, detail, new_size, res.orig_size = _convert_image(im, src, s, res, crop)
            finally:
                if big:
                    _big_sem.release()

        if dst is None:
            Path(s.output_dir or src.parent).mkdir(parents=True, exist_ok=True)
            dst = output_path(src, s, reserve=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dst)
        res.dst, res.new_bytes, res.new_size, res.detail = dst, len(data), new_size, detail
    except UnidentifiedImageError:
        res.error = "이미지 파일이 아니거나 손상되었습니다"
    except MemoryError:
        res.error = "메모리가 부족합니다 (이미지가 너무 큼)"
    except PermissionError:
        res.error = "파일 접근 권한이 없습니다"
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}"
    return res


def _needs_transpose(im: Image.Image) -> bool:
    try:
        return im.getexif().get(0x0112, 1) not in (0, 1, None)
    except Exception:
        return True  # 판단할 수 없으면 Pillow 에 맡김


def _convert_image(im: Image.Image, src: Path, s: Settings, res: Result, crop=None):
    """열린 이미지를 변환. (데이터, 설명, 결과 크기, 회전 반영된 원본 크기) 반환."""
    full_w, full_h = res.orig_size  # draft 적용 전 원본 크기
    im.load()
    extra = _metadata(im, s.keep_metadata)
    # MPO(니콘·소니·아이폰 JPEG 의 내장 미리보기)나 다중 페이지 TIFF 는 애니메이션이 아님
    animated = (im.format in ("GIF", "PNG", "WEBP")
                and getattr(im, "is_animated", False) and im.n_frames > 1)
    if animated:
        data, detail, new_size = _convert_animated(im, s, extra, crop)
        return data, detail, new_size, (full_w, full_h)

    try:
        swap = im.getexif().get(0x0112, 1) in (5, 6, 7, 8)
    except Exception:
        swap = False
    orig_size = (full_h, full_w) if swap else (full_w, full_h)  # 회전 반영된 원본 크기
    # 회전이 필요 없으면 exif_transpose 는 이미지를 통째로 복사만 한다 → 필요할 때만 호출
    img = ImageOps.exif_transpose(im) if _needs_transpose(im) else im
    img = _normalize_mode(img)
    box = None if crop == "none" else (crop if isinstance(crop, tuple) else auto_crop_box(img, s))
    img = _apply_crop(img, box)
    # 목표 크기는 '원본 해상도로 자른 크기' 기준 (draft 로 줄어든 상태여도 결과는 같아야 함)
    cw = max(1, round(orig_size[0] * (box[2] - box[0]))) if box else orig_size[0]
    ch = max(1, round(orig_size[1] * (box[3] - box[1]))) if box else orig_size[1]
    img = _resize(img, target_size(cw, ch, s))
    new_size = img.size
    lossless_src = src.suffix.lower() in LOSSLESS_SOURCE_EXTS
    if s.out_format == "png":
        data, detail = _convert_png(img, s, extra)
    elif s.mode == "lossless":
        data, detail = _lossless(img, extra), "무손실"
    elif s.mode == "quality":
        q = max(1, min(100, int(s.quality)))
        data, detail = _lossy(img, extra, q), f"손실 q{q}"
    else:
        data, detail = _smart(img, extra, s.smart_level, lossless_src, res.orig_bytes)
    return data, detail + _crop_label(box, s, crop), new_size, orig_size


def _convert_animated(im: Image.Image, s: Settings, extra: dict, crop=None):
    frames, durations = [], []
    first = im.convert("RGBA")
    box = None if crop == "none" else (crop if isinstance(crop, tuple) else auto_crop_box(first, s))
    size = target_size(*_apply_crop(first, box).size, s)  # 모든 프레임을 같은 영역으로 자름
    for fr in ImageSequence.Iterator(im):
        durations.append(fr.info.get("duration", im.info.get("duration", 100)) or 100)
        frames.append(_resize(_apply_crop(fr.convert("RGBA"), box), size))
    extra = {k: v for k, v in extra.items() if k != "exif"}
    if s.out_format == "png":  # 움직이는 PNG (APNG)
        detail = "애니메이션 PNG 무손실"
        total_px = size[0] * size[1] * len(frames)
        if s.mode != "lossless" and total_px <= 60_000_000:
            # 모든 프레임이 한 팔레트를 공유하도록 이어붙여서 한 번에 색상 줄이기
            # (GIF 원본은 이미 256색이라 사실상 손실 없음, 용량은 RGBA 대비 1/3 수준)
            has_alpha = any(f.getchannel("A").getextrema()[0] < 255 for f in frames)
            fw, fh = size
            strip = Image.new("RGBA" if has_alpha else "RGB", (fw, fh * len(frames)))
            for i, f in enumerate(frames):
                strip.paste(f if has_alpha else f.convert("RGB"), (0, i * fh))
            q = _quantize(strip, 256)
            frames = [q.crop((0, i * fh, fw, (i + 1) * fh)) for i in range(len(frames))]
            detail = "애니메이션 PNG 256색"
        buf = io.BytesIO()
        extra.setdefault("icc_profile", None)  # 안 넘기면 Pillow 가 원본 것을 넣음
        frames[0].save(buf, "PNG", save_all=True, append_images=frames[1:], duration=durations,
                       loop=im.info.get("loop", 0), optimize=True, **extra)
        return buf.getvalue(), f"{detail}, {len(frames)}프레임" + _crop_label(box, s, crop), size
    kw = dict(save_all=True, append_images=frames[1:], duration=durations,
              loop=im.info.get("loop", 0), method=4)
    if s.mode == "lossless":
        kw.update(lossless=True, quality=100)
        detail = "애니메이션 무손실"
    elif s.mode == "quality":
        kw.update(quality=int(s.quality))
        detail = f"애니메이션 q{int(s.quality)}"
    else:
        q = ANIM_QUALITY.get(s.smart_level, 85)
        kw.update(quality=q, allow_mixed=True)
        detail = f"애니메이션 혼합 q{q}"
    extra = {k: v for k, v in extra.items() if k != "exif"}
    return _encode(frames[0], extra, **kw), f"{detail}, {len(frames)}프레임" + _crop_label(box, s, crop), size


def _accept(f: Path) -> bool:
    return f.suffix.lower() in SUPPORTED_EXTS or is_archive(f)


def collect_files(paths, recursive: bool = True) -> list[Path]:
    """이미지 파일과 압축 파일(ZIP 등)을 모음. 폴더는 하위 폴더까지."""
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out += sorted(f for f in it if f.is_file() and _accept(f))
        elif p.is_file() and _accept(p):
            out.append(p)
    seen, uniq = set(), []
    for f in out:
        k = str(f.resolve())
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ================================================================ 압축 파일(ZIP/CBZ/7Z/TAR) 처리
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import PurePosixPath

try:
    import py7zr

    HAS_7Z = True
except Exception:  # pragma: no cover
    HAS_7Z = False

ARCHIVE_EXTS = {".zip", ".cbz", ".tar", ".tgz", ".cbt"} | ({".7z", ".cb7"} if HAS_7Z else set())
_COMIC_EXTS = {".cbz", ".cb7", ".cbt", ".cbr"}
_STORE_EXTS = {".webp", ".png", ".jpg", ".jpeg", ".gif"}  # 이미 압축된 형식은 다시 압축하지 않음


UNSUPPORTED_ARCHIVE_EXTS = {".rar", ".cbr", ".r00", ".ace", ".arj", ".lzh", ".cab"}


def find_unsupported_archives(paths) -> list[Path]:
    """RAR 등 지원하지 않는 압축 파일 (사용자에게 건너뛰었다고 알려주기 위함)."""
    out: list[Path] = []
    for p in map(Path, paths):
        try:
            if p.is_dir():
                out += [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in UNSUPPORTED_ARCHIVE_EXTS]
            elif p.is_file() and p.suffix.lower() in UNSUPPORTED_ARCHIVE_EXTS:
                out.append(p)
        except OSError:
            continue
    return out


def is_archive(p: Path) -> bool:
    name = p.name.lower()
    return p.suffix.lower() in ARCHIVE_EXTS or name.endswith(".tar.gz")


def _safe_rel(name: str) -> Optional[PurePosixPath]:
    """압축 파일 안 경로를 안전한 상대 경로로 (../ 나 절대 경로로 밖에 쓰는 것 방지)."""
    parts = [p.replace(":", "_") for p in PurePosixPath(name.replace("\\", "/")).parts
             if p not in ("", ".", "..", "/")]
    if not parts or parts[0] == "__MACOSX" or parts[-1].startswith("._") or parts[-1] in (".DS_Store", "Thumbs.db"):
        return None
    return PurePosixPath(*parts)


def _zip_name(info: zipfile.ZipInfo) -> str:
    """윈도우에서 만든 ZIP 의 한글 파일명(CP949) 깨짐 복구."""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _is_image_name(rel: PurePosixPath) -> bool:
    return PurePosixPath(rel).suffix.lower() in SUPPORTED_EXTS


def archive_listing(path: Path) -> tuple[int, int]:
    """(이미지 수, 기타 파일 수) - 목록 표시용, 압축은 풀지 않음."""
    names = []
    sfx = path.suffix.lower()
    if sfx in (".zip", ".cbz"):
        with zipfile.ZipFile(path) as z:
            names = [_zip_name(i) for i in z.infolist() if not i.is_dir()]
    elif sfx in (".7z", ".cb7"):
        with py7zr.SevenZipFile(path) as z:
            names = [i.filename for i in z.list() if not i.is_directory]
    else:
        with tarfile.open(path) as t:
            names = [m.name for m in t.getmembers() if m.isfile()]
    rels = [r for r in map(_safe_rel, names) if r is not None]
    imgs = sum(1 for r in rels if _is_image_name(r))
    return imgs, len(rels) - imgs


def _extract(path: Path, dest: Path, cancel: Optional[threading.Event] = None) -> list[PurePosixPath]:
    """dest 에 안전하게 풀고, 풀린 파일들의 상대 경로 목록을 반환."""
    out: list[PurePosixPath] = []
    sfx = path.suffix.lower()

    def write(rel, read_fn):
        target = dest.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            shutil.copyfileobj(read_fn(), f, 1 << 20)
        out.append(rel)

    if sfx in (".zip", ".cbz"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if cancel and cancel.is_set():
                    break
                rel = None if info.is_dir() else _safe_rel(_zip_name(info))
                if rel is not None:
                    write(rel, lambda: z.open(info))
    elif sfx in (".7z", ".cb7"):
        raw = dest / ".__7z_raw"
        with py7zr.SevenZipFile(path) as z:
            z.extractall(path=raw)
        for f in sorted(p for p in raw.rglob("*") if p.is_file()):
            rel = _safe_rel(f.relative_to(raw).as_posix())
            if rel is not None:
                target = dest.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(f, target)
                out.append(rel)
        shutil.rmtree(raw, ignore_errors=True)
    else:
        with tarfile.open(path) as t:
            for m in t.getmembers():
                if cancel and cancel.is_set():
                    break
                rel = _safe_rel(m.name) if m.isfile() else None
                if rel is not None:
                    write(rel, lambda: t.extractfile(m))
    return out


def archive_output_path(path: Path, s: Settings, as_folder: bool) -> Path:
    out_dir = Path(s.output_dir) if s.output_dir else path.parent
    stem = path.name[:-7] if path.name.lower().endswith(".tar.gz") else path.stem
    stem = f"{stem}_{s.out_format}"
    if as_folder:
        ext = ""
    else:
        ext = ".cbz" if path.suffix.lower() in _COMIC_EXTS else ".zip"
    dst, i = out_dir / (stem + ext), 1
    with _name_lock:
        while str(dst.resolve()).lower() in _reserved or (not s.overwrite and dst.exists()):
            dst = out_dir / f"{stem}_{i}{ext}"
            i += 1
        _reserved.add(str(dst.resolve()).lower())
    return dst


def cleanup_stale_temp(max_age_hours: float = 6):
    """프로그램이 강제 종료돼서 남은 임시 폴더 정리 (실행 중인 다른 작업 것은 건드리지 않도록 오래된 것만)."""
    import time as _t
    now = _t.time()
    for d in Path(tempfile.gettempdir()).glob("webpconv_*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age_hours * 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


class ArchiveJob:
    """압축 파일 하나: 풀기 → (이미지 각각을 작업 풀에서 변환) → 다시 묶기."""

    def __init__(self, path: Path, s: Settings, as_folder: bool = False, keep_others: bool = True):
        self.path, self.s, self.as_folder, self.keep_others = Path(path), s, as_folder, keep_others
        self.tmp: Optional[Path] = None
        self.out_root: Optional[Path] = None
        self.tasks: list[tuple[Path, Path, str]] = []  # (풀린 원본, 변환 결과 경로, 압축 내부 이름)
        self.results: list[Result] = []
        self.others: list[PurePosixPath] = []
        self.dst: Optional[Path] = None

    def prepare(self, cancel: Optional[threading.Event] = None) -> int:
        """압축을 풀고 변환할 이미지 목록을 만듦. 이미지 수 반환."""
        self.tmp = Path(tempfile.mkdtemp(prefix="webpconv_"))
        src_root = self.tmp / "src"
        try:
            rels = _extract(self.path, src_root, cancel)
        except Exception as e:
            self.cleanup()
            if isinstance(e, (zipfile.BadZipFile, tarfile.TarError)) or type(e).__name__ in ("Bad7zFile",):
                raise ValueError("압축 파일이 손상되었거나 읽을 수 없습니다") from e
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                raise ValueError("암호가 걸린 압축 파일은 지원하지 않습니다") from e
            raise
        self.dst = archive_output_path(self.path, self.s, self.as_folder)
        self.out_root = self.dst if self.as_folder else self.tmp / "out"
        ext = ".png" if self.s.out_format == "png" else ".webp"
        used: set[str] = set()
        for rel in sorted(rels, key=lambda r: r.as_posix().lower()):
            if _is_image_name(rel):
                base = rel.with_suffix("")
                name, i = base.as_posix() + ext, 1
                while name.lower() in used:  # 01.jpg / 01.png 가 같은 폴더에 있는 경우
                    name = f"{base.as_posix()}_{i}{ext}"
                    i += 1
                used.add(name.lower())
                self.tasks.append((src_root.joinpath(*rel.parts), self.out_root.joinpath(*PurePosixPath(name).parts), name))
            elif self.keep_others:
                self.others.append(rel)
                used.add(rel.as_posix().lower())
        return len(self.tasks)

    def run_task(self, i: int) -> Result:
        src, dst, _ = self.tasks[i]
        r = convert(src, self.s, dst=dst)
        try:
            src.unlink()  # 디스크 공간 절약
        except OSError:
            pass
        return r

    def finish(self, results: list[Optional[Result]], cancelled: bool = False) -> Result:
        """변환 결과를 묶어서 저장하고 요약 Result 반환."""
        res = Result(src=self.path, orig_bytes=self.path.stat().st_size)
        try:
            ok = [r for r in results if r and not r.error]
            errs = [r for r in results if r and r.error]
            if cancelled:
                res.error = "취소됨"
                return res
            src_root = self.tmp / "src"
            for rel in self.others:  # 이미지가 아닌 파일 (설명서, ComicInfo.xml 등) 그대로 복사
                target = self.out_root.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_root.joinpath(*rel.parts), target)
            if self.as_folder:
                res.new_bytes = sum(f.stat().st_size for f in self.out_root.rglob("*") if f.is_file())
            else:
                self.dst.parent.mkdir(parents=True, exist_ok=True)
                tmp_zip = self.dst.with_name(self.dst.name + ".tmp")
                with zipfile.ZipFile(tmp_zip, "w") as z:
                    entries = [(n, d) for _, d, n in self.tasks if d.exists()]
                    entries += [(rel.as_posix(), self.out_root.joinpath(*rel.parts)) for rel in self.others]
                    for name, f in sorted(entries, key=lambda e: e[0].lower()):
                        comp = zipfile.ZIP_STORED if f.suffix.lower() in _STORE_EXTS else zipfile.ZIP_DEFLATED
                        z.write(f, name, compress_type=comp)
                os.replace(tmp_zip, self.dst)
                res.new_bytes = self.dst.stat().st_size
            res.dst = self.dst
            res.detail = f"이미지 {len(ok)}장" + (f", 기타 파일 {len(self.others)}개" if self.others else "")
            if errs:
                res.detail += f" · 실패 {len(errs)}장 (예: {PurePosixPath(errs[0].src.name)} - {errs[0].error})"
            if not ok and self.tasks:
                res.error = "변환된 이미지가 없습니다" + (f" ({errs[0].error})" if errs else "")
        except Exception as e:  # noqa: BLE001
            res.error = f"{type(e).__name__}: {e}"
        finally:
            self.cleanup()
        return res

    def cleanup(self):
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None
