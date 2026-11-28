# WebP·PNG 변환 엔진 — 연동·이식 가이드

이 문서는 **다른 프로그램(예: 이미지 뷰어)에 이 변환기의 기능을 넣으려는 개발자나 Claude Code** 를 위한 것이다.
동작 방식, 알고리즘, 실측으로 정한 수치를 빠짐없이 적었다. 이 문서만 보고도 같은 결과를 재현할 수 있게 하는 것이 목표다.

- 저장소: https://github.com/flyingdtcsgpt-wq/webp-converter
- 핵심 파일: `webp_core.py` (엔진, GUI 의존 없음), `crop_editor.py` (개별 자르기 창, tkinter), `app.py` (GUI)
- 언어/런타임: Python 3.10+ (배포판은 3.12로 빌드)
- 이 문서 기준 버전: v1.1 이후 `main` (메모리 이미지용 `encode_image()` 추가)

---

## 0. 먼저 결정할 것: 어떻게 넣을까

| 뷰어 기술 | 추천 방법 | 비고 |
|---|---|---|
| **Python** (PySide/PyQt/tkinter 등) | **A. `webp_core.py` 를 그대로 가져다 import** | 가장 쉽고 결과가 100% 같다 |
| Electron / Node / C# / C++ 등 | **B. 엔진을 별도 프로세스로 실행** (Python 동봉) 또는 **C. 알고리즘 이식** | B는 결과 동일, C는 §3~§8 수치를 그대로 옮기면 거의 같아진다 |

> 뷰어에서는 변환을 **항상 백그라운드(작업 스레드/프로세스)** 에서 돌려야 한다. 사진 한 장(9MP→1280px)에 0.3~0.6초,
> 원본 크기 4천만 화소는 6~15초 걸린다. UI 스레드에서 부르면 뷰어가 멈춘다.

---

## 1. 방법 A — Python 에서 바로 쓰기

### 1.1 설치

```bash
pip install "Pillow>=10.0" "numpy>=1.24" "pyoxipng>=9.0" "imagequant>=1.1" "py7zr>=0.21" "pillow-heif>=0.16"
```

- 필수: `Pillow`, `numpy`
- 선택(없으면 자동으로 대체 동작): `pyoxipng`(PNG 무손실 최적화), `imagequant`(PNG 색상 줄이기), `py7zr`(7z), `pillow-heif`(아이폰 HEIC)
- `webp_core.py` 파일 하나를 뷰어 프로젝트에 복사하면 된다. tkinter 에 의존하지 않는다.

### 1.2 파일 변환 (가장 기본)

```python
import webp_core as core

s = core.Settings(
    out_format="webp",          # "webp" | "png"
    mode="smart",               # "smart"(자동 최적화) | "quality"(고정 품질) | "lossless"
    smart_level="best",         # "best" | "balanced" | "small"
    resize_mode="long", long_side=1280,
    crop_ratio=(16, 9),         # None 이면 자르지 않음
    output_dir="/path/to/out",  # None 이면 원본과 같은 폴더
)
r = core.convert("/path/photo.jpg", s)                 # 일괄 설정대로
r = core.convert("/path/photo.jpg", s, crop="none")    # 이 파일만 자르지 않음
r = core.convert("/path/photo.jpg", s, crop=(0.1, 0.2, 0.7, 0.8))  # 이 파일만 직접 지정 (0~1 비율 좌표)

if r.error:
    print("실패:", r.error)
else:
    print(r.dst, r.orig_bytes, "→", r.new_bytes, r.new_size, r.detail)
```

### 1.3 메모리에 있는 이미지 변환 (뷰어용)

뷰어가 이미 이미지를 열어 두었거나, 화면에서 편집(회전 등)한 결과를 저장할 때 쓴다.

```python
from PIL import Image, ImageOps
import webp_core as core

src = Image.open(path)
img = ImageOps.exif_transpose(src)        # encode_image 는 회전을 적용하지 않으므로 화면 방향으로 맞춰서 넘긴다
data, detail, size = core.encode_image(
    img, core.Settings(smart_level="best"),
    crop=None,                             # None | "none" | (x0, y0, x1, y1)
    lossless_source=path.lower().endswith((".png", ".bmp", ".gif", ".tif", ".tiff")),
    metadata_from=src,                     # EXIF·ICC 를 원본에서 가져옴 (keep_metadata=True 일 때)
)
open(out_path, "wb").write(data)
```

- 움직이는 GIF/WebP 는 `encode_image` 로는 첫 장면만 변환된다. 전체 프레임은 `convert()` 를 쓴다.

### 1.4 여러 장 일괄 변환 (스레드 풀)

```python
from concurrent.futures import ThreadPoolExecutor
import webp_core as core

files = core.collect_files(["/folder"])             # 이미지 + 압축 파일. 폴더는 하위까지
images = [f for f in files if not core.is_archive(f)]
workers = core.cpu_workers()                        # 실제 쓸 수 있는 코어 수 (최대 16)
core.set_big_image_limit(2)                         # 2,500만 화소 이상은 동시에 2장까지만 (메모리 보호)
if s.out_format == "png":
    core.start_png_pool(workers)                    # PNG 최적화(oxipng)를 프로세스로 병렬화 (§7.2)

with ThreadPoolExecutor(workers) as ex:
    for r in ex.map(lambda f: core.convert(f, s), images):
        ...                                         # 진행 상황 갱신 (UI 스레드로 넘겨서)

core.release_names()                                # 배치가 끝나면 '이번 배치에서 쓴 파일명' 예약 해제
core.stop_png_pool()                                # 앱 종료 시
```

- **스레드로 충분하다**: Pillow 의 인코딩·디코딩·리사이즈가 GIL 을 풀기 때문에 4코어에서 스레드 4개가 프로세스 4개와 같은 속도였다(실측).
  예외는 PNG 의 `oxipng` 뿐이다 (§7.2).
- **취소**: 아직 시작 안 한 작업을 건너뛰는 방식으로 구현한다(작업 함수 첫 줄에서 취소 플래그 확인). 실행 중인 한 장은 끝까지 간다.
- **PNG 프로세스 풀을 쓰면** 앱 진입점에 반드시 `if __name__ == "__main__": multiprocessing.freeze_support(); main()` 가 있어야 한다.
  없으면 실행 파일(PyInstaller)에서 작업 프로세스가 앱 창을 다시 띄운다.

### 1.5 압축 파일(ZIP/CBZ/7Z/TAR) 안의 이미지 변환

```python
job = core.ArchiveJob(path, s, as_folder=False)   # as_folder=True 면 결과를 폴더로
n = job.prepare(cancel_event)                     # 임시 폴더에 안전하게 풀기 → 변환할 이미지 수
with ThreadPoolExecutor(workers) as ex:
    results = list(ex.map(job.run_task, range(n)))
r = job.finish(results, cancelled=False)          # 다시 묶어서 저장 + 임시 폴더 삭제 → 요약 Result
```

- 앱 시작 시 `core.cleanup_stale_temp()` 를 한 번 불러서, 강제 종료로 남은 임시 폴더(`webpconv_*`, 6시간 이상)를 지운다.

### 1.6 API 요약

| 이름 | 설명 |
|---|---|
| `Settings` | 모든 옵션 (§2) |
| `convert(src, s, dst=None, crop=None) -> Result` | 파일 하나 변환·저장. `dst` 를 주면 그 경로에 저장 |
| `encode_image(img, s, crop=None, lossless_source=False, metadata_from=None) -> (bytes, detail, size)` | 메모리 이미지 변환 (저장은 호출 쪽) |
| `Result` | `src, dst, orig_bytes, new_bytes, orig_size, new_size, detail, error, saved_ratio` |
| `auto_crop_box(img, s) -> (x0,y0,x1,y1) or None` | 일괄 자르기 설정이 고를 영역 (미리보기 표시용) |
| `crop_ratio_for(w, h, s)` | 이 이미지에 실제 적용될 비율 (방향 뒤집기 반영) |
| `target_size(w, h, s)` | 크기 조절 결과 크기 |
| `collect_files(paths)`, `is_archive(p)`, `archive_listing(p)` | 파일 모으기, 압축 파일 판별·목록 |
| `find_unsupported_archives(paths)` | RAR 등 지원하지 않는 압축 파일 (사용자 안내용) |
| `ArchiveJob` | 압축 파일 처리 (§1.5) |
| `cpu_workers()`, `set_big_image_limit(n)` | 병렬 개수, 큰 이미지 동시 처리 제한 |
| `start_png_pool(n)`, `stop_png_pool()` | PNG 최적화 프로세스 풀 |
| `release_names()`, `cleanup_stale_temp()` | 파일명 예약 해제, 남은 임시 폴더 정리 |
| `UNREACHABLE_NOTE` | 결과 설명(`detail`)에 이 문구가 있으면 "화질이 떨어질 수 있음" 경고 표시용 |
| `SUPPORTED_EXTS`, `ARCHIVE_EXTS`, `HEIF_SUPPORTED`, `HAS_OXIPNG`, `HAS_IMAGEQUANT`, `HAS_7Z` | 지원 형식·선택 라이브러리 유무 |

---

## 2. Settings 전체 옵션

| 필드 | 기본값 | 의미 |
|---|---|---|
| `out_format` | `"webp"` | `"webp"` \| `"png"` |
| `mode` | `"smart"` | `"smart"` 자동 최적화 \| `"quality"` 고정 품질 \| `"lossless"` 무손실 |
| `smart_level` | `"best"` | `"best"` \| `"balanced"` \| `"small"` (§4.1) |
| `quality` | `85` | `mode="quality"` 일 때 1~100 (PNG 에서는 색상 줄이기 품질) |
| `resize_mode` | `"none"` | `none` \| `long`(긴 변) \| `width` \| `height` \| `percent` \| `fit`(상자 안에 맞춤) \| `cover`(정확히 W×H, 넘치면 자름) |
| `width`, `height`, `long_side`, `percent` | | 크기 조절 값 |
| `no_upscale` | `True` | 원본보다 크게 확대하지 않음 |
| `keep_metadata` | `True` | EXIF(GPS 포함)·ICC 유지. **공유용이면 False 권장** |
| `output_dir` | `None` | 저장 폴더. None 이면 원본 옆 |
| `overwrite` | `False` | 같은 이름 덮어쓰기. False 면 `이름_1.webp` 식으로 |
| `crop_ratio` | `None` | 일괄 자르기 비율 `(가로, 세로)`, 예: `(16, 9)` |
| `crop_anchor` | `"center"` | `"center"` \| `"smart"`(디테일이 많은 쪽을 남김) |
| `crop_flip_portrait` | `True` | 사진 방향과 비율 방향이 다르면 비율을 뒤집음 (가로 사진에 9:16 → 16:9) |

---

## 3. 처리 순서 (파이프라인)

```
열기 ─► (JPEG 이고 크게 줄일 때) 축소 디코딩 ─► EXIF 회전 반영 ─► 색 모드 정리 ─► 자르기 ─► 크기 조절 ─► 인코딩 ─► 저장
```

1. **축소 디코딩(JPEG draft)**: 결과 크기의 배율 `k < 0.5` 이면 `im.draft("RGB", 원본×k×2)` 로 1/2·1/4·1/8 크기로 디코딩한다.
   최종 크기의 **2배 이상은 남긴다**. 그보다 공격적으로 줄이면(예: 1.25배) 2~3배 빨라지지만, 결과가 LANCZOS 축소와
   눈에 띄게 달라져서(SSIM 0.992~0.998, 가장자리 최대 차 27) 쓰지 않는다. 자르기가 있으면 '자른 뒤 크기' 기준으로 판단한다.
2. **EXIF 회전**: 방향 태그(0x0112)가 1이 아닐 때만 `ImageOps.exif_transpose` (회전이 필요 없을 때 부르면 이미지 전체를 복사만 한다).
   저장할 EXIF 의 방향 태그는 1로 바꾼다 (이미 픽셀에 반영했으므로).
3. **색 모드 정리**: RGB/RGBA 로 맞춘다. 16비트는 8비트로, CMYK·팔레트·흑백은 RGB(투명 있으면 RGBA)로.
   **알파가 전부 255(완전 불투명)면 알파를 버린다** (용량 절약).
4. **자르기** (§5) → 5. **크기 조절**: `Image.resize(size, LANCZOS, reducing_gap=3.0)`. RGBA 는 Pillow 가 premultiplied 로 처리해 가장자리 번짐이 없다.
   목표 크기는 '원본 해상도로 자른 크기' 기준으로 계산한다 (축소 디코딩을 했어도 결과 크기가 같도록).
   WebP 는 한 변 최대 16,383px 이라 넘으면 줄인다.
6. **인코딩** (§4, §7)
7. **저장**: `이름.tmp` 에 쓰고 `os.replace` 로 바꿔치기 (중간에 꺼져도 깨진 파일이 남지 않게).

---

## 4. 스마트 최적화 (WebP) — 핵심 알고리즘

**목표**: 원본(자르기·크기 조절까지 끝난 이미지)과 비교한 화질 점수가 목표 이상인 **가장 낮은 WebP 품질값 q** 를 찾는다.
사진마다 적정 품질이 다르다 (잔디 사진은 q76이면 충분, 인물은 q90 필요). 그래서 고정 품질보다 용량과 화질이 모두 낫다.

### 4.1 단계별 목표

| 단계 | 목표 SSIM | 최저 품질 | 탐색 시작 품질 | 느낌 |
|---|---|---|---|---|
| `best` (기본) | 0.993 | q85 | 95 | 3배 확대해도 차이를 찾기 어려움 |
| `balanced` | 0.985 | q72 | 88 | 원래 크기로는 같고 확대하면 약간 뭉개짐 |
| `small` | 0.975 | q60 | 82 | 웹 업로드용 |

- 최저 품질 아래로는 절대 내려가지 않는다. SSIM 은 **하늘 같은 매끈한 그라데이션의 블록 얼룩을 잘 못 잡는다**.
  실험에서 SSIM 은 거의 같은데 q85 대신 더 낮추면 얼룩이 보였다.

### 4.2 화질 점수 (SSIM) — 계산 방식

- **밝기(Y)만** 비교한다: `Y = 0.299R + 0.587G + 0.114B` (Pillow `convert("F")`). 색차(Cb/Cr)는 WebP 의 4:2:0 서브샘플링 때문에
  품질을 올려도 점수가 포화되고, 사람 눈도 둔감하다.
- **투명 이미지**: 양쪽 모두 회색(128,128,128) 배경에 합성한 뒤 비교한다 (보이지 않는 영역 차이 무시).
- **SSIM 공식**: 창마다 `((2μaμb + C1)(2σab + C2)) / ((μa² + μb² + C1)(σa² + σb² + C2))`, `C1 = (0.01·255)²`, `C2 = (0.03·255)²`, 창 평균을 낸다.
- **창 배치(속도 핵심)**: 7×7 창을 **겹치지 않게 7픽셀 간격**으로 놓는다. 블록 합은 `reshape(h/7, 7, w/7, 7)` + `einsum("ikjl->ij")`.
  - 모든 위치를 1픽셀씩 미는 정식 슬라이딩 창과 비교: 평균 차 0.00001 미만, 표준편차 0.0001 이하, **약 10배 빠름**.
  - 창 개수가 적은 작은 이미지는 위치를 어긋나게 여러 번 잰다: 창 3만 개 이상 → 1번(0), 1.2만 이상 → 2번(0, 3), 그 미만 → 4번(0, 2, 4, 5).
  - 창 크기를 8×8로 바꾸면 값이 +0.0005 치우친다. 목표값이 7×7 기준으로 정해져 있으므로 7×7 유지.
- 정밀도: 밝기에서 128을 빼서(중심화) float32 로 두고, 창 합은 float64 로 계산한다.
- **원본 쪽 통계(μa, σa²)는 한 번만 계산해 재사용**한다 (탐색 중 원본은 변하지 않음).

### 4.3 품질 탐색 — `_search_min_q`

정답(목표를 넘는 최소 q)을 브루트포스와 똑같이 찾되, 인코딩 횟수를 최소화한다 (무작위 3,000개 곡선 테스트에서 평균 2.66회, 불일치 0).

1. 시작점: 같은 단계의 **최근 결과 15개의 중앙값** (없으면 §4.1의 시작 품질). 직전 값 하나만 쓰면 종류가 섞인 배치에서 오히려 느려졌다.
2. 측정값이 하나뿐이면 **품질 1당 SSIM 약 0.0012** 라는 경험적 기울기로 목표까지 한 번에 이동 (한 번에 1~12 범위).
3. 측정값이 둘 이상이면 목표를 사이에 둔 두 점(없으면 목표에 가장 가까운 두 점)으로 **선형 보간**해 다음 후보를 고른다.
4. 후보가 이미 잰 값이면 남은 구간의 가운데(이분 탐색)로 바꾼다.
5. `통과한 최소 q == 실패한 최대 q + 1` 이 되면 끝. 최고 품질(100)도 실패면 100을 돌려준다.

### 4.4 표본으로 시작점 잡기 (60만 화소 이상)

이미지에서 **112px 타일 4×4개를 고르게 뽑은 모자이크**(면적 15~20%)로 먼저 §4.3 탐색을 해서 대략적인 q 를 얻고,
그 값을 이미지 전체 탐색의 시작점으로 쓴다. 최종 확인은 전체 이미지로 하므로 **결과는 완전히 같고 시간만 4~22% 준다**.

### 4.5 탐색 결과물을 그대로 최종 파일로

이미지 전체로 탐색하는 경우(250만 화소 이하, 즉 대부분의 축소 변환)는 **탐색에 쓴 인코딩 설정이 최종 설정과 같아서**(method 5),
통과한 결과물을 그대로 저장한다. "찾은 품질로 다시 인코딩 → 재확인" 단계가 없다.

### 4.6 큰 이미지 (250만 화소 초과, 줄이지 않고 변환할 때)

1. **표본 모자이크로 탐색**: 112px 타일을 **10×10개** 고르게 뽑는다 (125만 화소).
   - 같은 면적이면 큰 타일 몇 개(예: 336px 16개)보다 **작은 타일 여러 개가 표본 오차가 1/2 이하**였다.
   - 112 = 7(측정 창) × 16(WebP 매크로블록) 의 공배수라 창과 블록이 타일 이음매에 걸치지 않는다.
   - **이음매 옆 창(타일 테두리 1줄)은 점수에서 뺀다**. 이음매는 원본에 없는 가짜 경계라 그 주변 인코딩이 실제보다 나빠서,
     포함하면 SSIM 이 최대 0.01~0.02 낮게 나왔다.
2. 찾은 q 로 **전체를 인코딩**하고 **이미지 전체에서 실제 SSIM** 을 잰다 (메모리 때문에 가로 띠 672행 단위, 띠 가로는 7의 배수로 잘라 복사 없이 계산).
3. 표본 추정과 실제의 차이 `d = 실제 − 표본` 은 같은 이미지 안에서 거의 일정하다. 그래서
   - **목표 미달**: 표본에서 `목표 − d` 를 목표로 다시 탐색(싸다) → 그 q 로 전체 재인코딩. 통과할 때까지 반복.
   - **여유**: 한 단계 아래가 보정 목표(+0.0002)를 넘고, 표본 용량 기준 **10% 이상 줄 것 같을 때만** 내려서 다시 인코딩.
     (5% 기준은 시간 +18%에 용량 −1.7%, 10% 기준은 +7%에 −1.1%였다. 9~40MP 13장 실측)
4. 표본에서 q100 이 목표보다 0.004 넘게 모자라면(표본 오차 최대 약 0.0035보다 큼) q100 전체 인코딩을 건너뛰고 바로 §4.7.

### 4.7 목표에 도달할 수 없을 때 (노이즈 많은 사진)

q100으로도 목표 SSIM 에 못 미치면 **q85 로 저장**하고 결과 설명에 `UNREACHABLE_NOTE`("목표 화질 도달 불가 → 화질이 떨어질 수 있음")를 붙인다.
q100 은 용량이 5~10배 커지는데 화질 이득은 거의 없다 (예: SSIM 0.9885 → 0.9886).
q85 의 실제 SSIM 은 사진에 따라 0.91~0.98로 차이가 크다. UI 에서 이 파일들을 경고 색으로 표시하는 것을 권장한다.

### 4.8 무손실 원본(PNG·BMP·GIF·TIFF 등)일 때

- **먼저 표본으로 판단**: 최대 40만 화소 표본(타일 4×4)에서 `빠른 무손실(method 1, q20) 크기 ≤ 0.8 × 최저 품질 손실 크기` 이면
  손실 탐색을 아예 하지 않고 무손실로 저장한다 (스크린샷·선화·도표에서 2~3배 빨라짐).
- 아니면 손실 탐색 후, 표본에서 잰 '무손실/손실' 크기 비율로 전체 무손실 크기를 추정해 손실 결과의 1.15배 미만일 때만 실제로 무손실 인코딩해 비교한다.
- 손실 결과가 목표 미달이면 무손실 결과가 원본 파일보다 작을 때 무손실을 고른다 (화질 보존).
- **JPEG·WebP·HEIC 같은 손실 원본은 무손실로 저장하지 않는다**. 화질 이득 없이 용량만 몇 배로 커진다.

### 4.9 WebP 인코딩 설정 (Pillow `save(..., "WEBP")`)

| 용도 | 설정 |
|---|---|
| 손실 (탐색·최종 모두) | `quality=q, method=5`, RGBA 면 `alpha_quality=100` (투명 경계 보존) |
| 무손실 | `lossless=True, quality=90, method=5, exact=False` (800만 화소 초과는 `quality=80, method=4`) |
| 무손실 빠른 추정용 | `lossless=True, quality=20, method=1` |
| 움직이는 이미지 | 스마트 모드면 `allow_mixed=True`, 품질 고정(best 92 / balanced 85 / small 75), `method=4`, `save_all=True`, 프레임별 duration·loop 유지 |

- **method 5 를 쓰는 이유**: 같은 화질 기준으로 method 6 보다 용량이 0.7~1.8%만 크고 약 35% 빠르다.
  method 4 는 3~6% 크다.
- method 6 / 무손실 quality 100 은 수십 배 느린데 이득이 1% 미만이라 쓰지 않는다.
- 참고: 4천만 화소급은 특정 품질값에서 인코딩이 3배 느려진다 (q96 12초 vs q97 3.4초). WebP 내부 한도(예측 정보 512KB)를 넘으면
  libwebp 가 전체를 다시 인코딩하기 때문이며 바꿀 수 없다.

---

## 5. 자르기

### 5.1 좌표 체계

자르기 영역은 **'회전을 반영한 원본' 기준 비율 좌표 `(x0, y0, x1, y1)`, 각 0~1** 로 다룬다.
그래서 축소 디코딩한 이미지, 미리보기 썸네일, 원본 어디에 적용해도 같은 영역을 가리킨다. 뷰어의 확대/이동 상태에서 바로 만들 수 있다:

```python
# 뷰어 화면에서 사용자가 그린 사각형 (화면 좌표) → 비율 좌표
box = ((sx0 - img_left) / img_w_on_screen, (sy0 - img_top) / img_h_on_screen,
       (sx1 - img_left) / img_w_on_screen, (sy1 - img_top) / img_h_on_screen)
```

사진별 상태는 세 가지다: `None` = 일괄 설정대로, `"none"` = 이 사진은 자르지 않음, `(x0,y0,x1,y1)` = 직접 지정 (일괄보다 우선).

### 5.2 비율로 자를 때

1. 적용할 비율: `crop_ratio`. `crop_flip_portrait` 이고 `(비율가로−비율세로) × (이미지가로−이미지세로) < 0` 이면 비율을 뒤집는다.
   정사각형 이미지나 1:1 비율은 그대로.
2. 남길 크기: 이미지 비율이 더 넓으면 가로를 `h × R / w` 만큼만 남기고, 더 길면 세로를 `(w / R) / h` 만큼 남긴다 (R = 가로/세로).
3. 위치:
   - `center`: 정가운데.
   - `smart`: 흑백으로 바꿔 최대 256px 로 줄이고, 디테일 지도 `|가로 차분| + |세로 차분|` 을 자를 방향으로 합쳐 1차원 분포를 만든다.
     창(남길 폭)을 밀면서 합이 최대인 위치를 고르되 **가운데에서 멀수록 최대 15% 감점**한다
     (고르게 복잡한 사진은 가운데를 남기도록). 얼굴 인식이 아니라서 가끔 빗나간다 → 개별 조정 UI 가 필요한 이유.
4. `resize_mode="cover"`(꽉 채우기)는 `crop_ratio = (width, height)` 로 자른 뒤 정확히 W×H 로 만든다. 방향 뒤집기는 하지 않는다.
   이 파일만 `"none"` 이면 비율이 맞지 않으므로 '상자 안에 맞춤(fit)' 으로 처리한다.
   `no_upscale` 이고 원본이 더 작으면 확대하지 않는다 (비율만 맞춤).
5. 움직이는 이미지는 첫 장면으로 영역을 정하고 **모든 프레임을 같은 영역으로** 자른다.

### 5.3 개별 자르기 UI (`crop_editor.py` 참고 구현, tkinter)

- 썸네일을 캔버스에 맞춰 표시하고, **잘려 나갈 부분은 밝기 35%로 어둡게 한 이미지를 깔고, 박스 안만 원본 밝기 이미지를 덮는다**.
  (macOS 의 Tk 는 반투명 칠하기(stipple)를 지원하지 않아서 이 방식을 씀. Qt/웹이면 반투명 사각형으로 충분)
- 박스 안 드래그 = 이동, 모서리(10px 이내) 드래그 = 반대쪽 모서리 고정 크기 조절, 박스 밖 드래그 = 새로 그리기.
- 비율 고정 시: `너비 = max(|dx|, |dy| × R)`, `높이 = 너비 / R`, 이미지 밖으로 나가면 그 방향 최대치로 줄인다. 최소 8px.
- 3분할선, 원본 픽셀 기준 '자를 영역 W×H' 표시.
- ←/→ 로 다음 사진, 넘길 때 바뀐 내용 자동 저장. 박스가 사실상 전체(각 변 0.2% 이내)면 `"none"` 으로 저장.
- 압축 파일 안 이미지는 개별 지정 대상에서 뺐다 (일괄만 적용).

---

## 6. 메타데이터

- `keep_metadata=True`: EXIF(촬영 정보·**GPS 포함**)와 ICC 색상 프로파일을 유지. EXIF 는 사본을 만들어 방향 태그만 1로 바꿔 저장.
  (원본 이미지의 EXIF 객체를 직접 고치면 이후 `exif_transpose` 가 회전을 안 해서 버그가 났었다)
- `keep_metadata=False`: 둘 다 제거. **Pillow 의 PNG 저장은 `icc_profile` 을 안 넘기면 원본 것을 자동으로 넣으므로 반드시 `icc_profile=None` 을 넘긴다**.
- ICC 를 빼면 넓은 색 영역(Display P3 등)으로 찍은 사진은 색이 약간 달라 보일 수 있다.
- 결과 파일에서 확인한 조합: 형식 2 × 유지 여부 2 × 모드 3 × 원본 6종(회전·ICC·GPS·움직이는 GIF 등) = 72건 모두 의도대로.

---

## 7. PNG 출력

### 7.1 압축 방식

- **이미 256색 이하인 그래픽**(`img.getcolors(256)` 가 None 이 아님): 무손실. oxipng 가 알아서 팔레트 PNG 로 바꾼다.
  oxipng 가 없으면 `convert("P", palette=ADAPTIVE, colors=256)` 후 픽셀이 완전히 같은지 확인하고 더 작으면 사용.
- **스마트**: libimagequant 로 색상 수를 **256 → 128 → 64 → 32** 로 줄여 보며(디더링 1.0), 두 기준을 모두 넘는 가장 적은 색 수를 고른다.
  - 그대로 비교한 SSIM ≥ `t_raw` (사진의 어두운 털·나뭇잎이 뭉개지는 것을 잡음) — **먼저 검사** (사진은 대개 여기서 탈락, 두 번째 검사 생략)
  - 살짝 흐리게(가우시안 0.8) 한 뒤 비교한 SSIM ≥ `t_soft` (디더링 점 무늬는 무시하고, 계단(띠) 현상은 잡음)
  - 기준: best (0.995, 0.990), balanced (0.990, 0.975), small (0.985, 0.960) — 순서는 (t_soft, t_raw)
  - 통과하는 게 없으면 무손실. **oxipng 는 최종 결과에 한 번만** 실행한다.
  - 처음에는 흐리게 한 비교만 썼는데, 3배 확대해 보니 벌의 검은 털이 뭉개진 결과가 통과했다. 그래서 두 번째 기준을 추가했다.
- 품질 직접 지정: libimagequant `max_quality=q`, 256색.
- 무손실: oxipng. 800만 화소 이하는 `level=2`, 초과는 `level=1` (속도 우선). 메타데이터를 안 남길 때는 `StripChunks.safe()`.
- 움직이는 GIF → APNG: 모든 프레임을 세로로 이어 붙여 **한 번에 양자화해 팔레트 하나를 공유**시킨다 (RGBA APNG 대비 약 1/3, 원본 GIF 보다도 작음). 총 6천만 화소 초과면 무손실 RGBA.

### 7.2 성능상 주의 (중요)

- **`imagequant.quantize_pil_image()` 를 쓰지 말 것.** 내부에서 픽셀을 파이썬 반복문으로 변환해 매우 느리고 GIL 을 잡아 다른 스레드까지 멈춘다.
  대신 `quantize_raw_rgba_bytes(img.convert("RGBA").tobytes(), w, h, ...)` 를 직접 부르고
  `Image.frombytes("P", size, data)` + `putpalette(palette, rawmode="RGBA")` 로 만든다 (결과 픽셀 동일, 수십 배 빠름).
- **`oxipng` 는 실행 중 GIL 을 놓지 않는다** → 스레드 4개로도 한 번에 하나씩만 돈다. 그래서 **spawn 방식 프로세스 풀**에서 실행한다 (2.5 → 6.8회/초).
  - 반드시 `multiprocessing.get_context("spawn")`. 리눅스 기본값인 fork 는 oxipng 내부 스레드와 충돌해 멈췄다.
  - 작업 함수는 모듈 최상위 함수여야 한다 (`_oxipng_job`).
- 결과: 640px 사진 PNG 스마트 변환이 4코어 기준 약 2.8장/초 → 약 8장/초.

---

## 8. 압축 파일 처리

- 지원: `.zip .cbz .tar .tgz .tar.gz .cbt`, `py7zr` 가 있으면 `.7z .cb7`. **RAR/CBR 은 미지원** (외부 unrar 필요) → 건너뛰고 사용자에게 알림.
- **한글 파일명 복구**: ZIP 항목에 UTF-8 플래그(0x800)가 없으면 이름을 `cp437` 로 다시 인코딩한 바이트를 UTF-8 → CP949 순으로 해석한다.
- **안전**: `..`·절대 경로를 제거한 상대 경로로만 풀고(Zip Slip 방지), `__MACOSX/`, `._*`, `.DS_Store`, `Thumbs.db` 는 제외.
- 손상·암호 압축 파일은 친절한 오류 메시지로 바꿔 보여주고 임시 폴더를 즉시 지운다.
- 동시에 풀어 두는 압축 파일은 **최대 2개** (디스크 보호). 변환이 끝난 원본 이미지는 바로 지운다.
- 다시 묶을 때 이미 압축된 형식(`.webp .png .jpg .gif`)은 `ZIP_STORED`, 나머지는 `ZIP_DEFLATED`. 이미지가 아닌 파일(설명서, ComicInfo.xml 등)은 그대로 넣는다.
- 결과 이름: `원래이름_webp.zip` (CBZ 계열은 `.cbz`), 폴더 모드면 `원래이름_webp/`. 압축 안에서 `01.jpg` 와 `01.png` 가 겹치면 `01_1.webp`.

---

## 9. 파일 이름·동시성·메모리

- 결과 이름: `원본이름.webp`. 원본과 같은 경로가 되면(같은 폴더의 .webp 원본) `원본이름_opt.webp`.
  `overwrite=False` 면 `_1`, `_2` … 를 붙인다.
- **여러 스레드가 동시에 같은 이름을 고르지 않도록** 모듈 전역 예약 집합 + 락으로 이름을 예약한다 (`a.jpg` 와 `a.png` → `a.webp`, `a_1.webp`).
  배치가 끝나면 `release_names()`.
- 2,500만 화소 이상 이미지는 세마포어로 **동시에 2장**까지만 디코딩한다 (4천만 화소 1장 처리에 최대 약 650MB).
- 작업 스레드 수: `os.process_cpu_count()` → 없으면 `sched_getaffinity` → `cpu_count()`, 최대 16. 코어보다 많은 스레드는 오히려 약 20% 느렸다.
- 품질 탐색 기록(시작점용 최근 15개)은 락으로 보호한다.

---

## 10. 실측 성능 (4코어 리눅스, 스마트 최적화 best)

| 작업 | 속도 |
|---|---|
| 800px 사진 → 640px WebP | 약 34장/초 |
| 7~10MP 사진 → 1280px WebP | 약 7장/초 |
| 4,000만 화소 1장 → 1280px | 약 0.6초 |
| 4,000만 화소 1장 원본 크기 | 약 6~15초 (§4.9 참고) |
| 800px 사진 → 640px PNG | 약 8장/초 |
| PNG 스크린샷·그래픽 → WebP | 약 12장/초 |

시간이 쓰이는 곳(1280px WebP 기준): WebP 인코딩 약 60%, 리사이즈 약 13%, 디코딩·SSIM 채점 나머지.

### 해 봤지만 넣지 않은 것 (같은 실수를 반복하지 않도록)

| 시도 | 결과 | 이유 |
|---|---|---|
| JPEG 를 목표의 1.25배로 축소 디코딩 | 2~3배 빠름 | 결과가 LANCZOS 와 달라짐 (화질 기준 변경) |
| 통과 직후 아래 품질 확인 생략(조기 확정) | 인코딩 수 감소 | 용량 +0.7~3.8%, 정답 아닌 q 를 고름 |
| 직전 이미지 q 를 시작점으로 | 종류가 섞이면 오히려 느림 | 최근 15개 중앙값으로 교체 |
| 노이즈 사진을 아주 낮은 품질로 | 용량 80KB → 7KB | SSIM 은 같은데 하늘에 얼룩이 보임 → q85 하한 유지 |
| 큰 타일 16개 표본 | 표본 오차 큼 | 작은 타일 100개 + 이음매 제외로 교체 |
| 탐색 목표에 여유값 / 미세 미달 허용 | 차이 없음 | 복잡도만 증가 |

---

## 11. 다른 언어로 이식할 때 (방법 C) — 대응표

결과를 이 엔진과 최대한 같게 하려면 §4~§8 수치를 그대로 쓰고, **libwebp 를 같은 설정(method 5 등)으로** 부르면 된다.
아래는 대응되는 도구 예시이며 이 저장소에서 테스트하지는 않았다.

| 기능 | Python (이 엔진) | Node/Electron 예 | C#/.NET 예 |
|---|---|---|---|
| WebP 인코딩 | Pillow `save("WEBP", quality, method=5, alpha_quality=100)` | `sharp().webp({ quality, effort: 5, alphaQuality: 100 })` | libwebp 바인딩 / SkiaSharp `SKWebpEncoderOptions` |
| 무손실 WebP | `lossless=True, quality=90, method=5, exact=False` | `webp({ lossless: true, effort: 5 })` | |
| 디코딩 → 밝기 배열 | Pillow `convert("F")` | `sharp().greyscale().raw()` (주의: 감마 처리 차이 확인) | |
| SSIM | §4.2 직접 구현 (7×7 비중첩 창) | 직접 구현 (TypedArray) | 직접 구현 |
| 리사이즈 | Pillow LANCZOS, reducing_gap=3.0 | `sharp().resize({ kernel: "lanczos3" })` | |
| EXIF 회전 | `ImageOps.exif_transpose` | `sharp().rotate()` (인자 없이) | |
| PNG 색상 줄이기 | libimagequant | `sharp().png({ palette: true, colours, dither: 1 })` (libimagequant 내장) | |
| PNG 무손실 최적화 | oxipng | `oxipng` 실행 파일 / wasm | `oxipng` 실행 파일 |

- SSIM 기준값(0.993 등)은 **밝기 계산식·창 배치·C1/C2 가 §4.2 와 같아야** 의미가 같다. 다르게 구현하면 목표값을 다시 보정해야 한다.
- 이식 후 검증: 같은 사진 몇 장으로 이 엔진(`convert`)과 품질값 q·용량이 비슷한지 비교한다.

---

## 12. 뷰어 UI 에 넣을 때 권장 사항

- **진입점**: 사진 오른쪽 클릭 "WebP 로 저장/최적화", 폴더 "일괄 변환…", 그리고 **현재 보고 있는 영역으로 자르기**
  (뷰어의 확대·이동 상태 → §5.1 비율 좌표로 바로 변환).
- 설정 기본값: WebP · 스마트 · best · 크기 유지 · 메타데이터 유지 · 덮어쓰기 끔.
- **변환 전 확인창**(이 앱의 동작): 손실 원본(JPG·WebP·HEIC)을 크기 변경 없이 PNG/무손실로 저장(용량 증가), 덮어쓰기 켬,
  EXIF(GPS) 유지, 압축 파일(임시 공간 필요).
- **결과 표시**: 원본/결과 크기, 절감률, `detail`. 용량이 늘어난 파일과 `UNREACHABLE_NOTE` 가 붙은 파일은 경고 색.
- 수천 장: 목록은 백그라운드에서 모으고 300개씩 끊어 UI 에 넣는다. 진행 메시지는 큐로 받아 한 번에 최대 400개씩 처리해야 UI 가 안 끊긴다.
  진행 표시에 장/초와 남은 시간을 보여주면 좋다.
- 원본은 절대 수정하지 않는다. 저장은 임시 파일 → 바꿔치기.

---

## 13. 라이선스 (배포 시 확인)

| 패키지 | 라이선스 | 비고 |
|---|---|---|
| Pillow | MIT-CMU | |
| numpy | BSD-3-Clause 등 | |
| pyoxipng | MIT | |
| imagequant (파이썬 래퍼) | BSD-3-Clause | **포함된 libimagequant 엔진은 GPLv3** — 비공개 상용 배포라면 빼거나 상용 라이선스 필요. 빼면 Pillow 양자화로 자동 대체(화질 약간 낮음) |
| py7zr | LGPL-2.1-or-later | |
| pillow-heif | BSD-3-Clause | 포함된 libheif 등 네이티브 라이브러리 라이선스는 별도 확인 |
| tkinterdnd2 | MIT | GUI 에만 필요 |
| PyInstaller | GPLv2+ (예외 조항) | 빌드 도구. 만든 프로그램 배포는 제한 없음 |

---

## 14. 테스트로 확인한 범위 (이식·수정 후 같은 항목을 다시 확인할 것)

- 회전된 사진(EXIF 6), CMYK, 16비트, 흑백, 팔레트 GIF, 투명 PNG(알파 오차 0), 움직이는 GIF(WebP/APNG), 손상 파일, 1×1~7픽셀 초소형 이미지
- 자르기: 1:1/16:9, 방향 뒤집기, 꽉 채우기(정확한 크기, 확대 금지), 직접 지정(영역 내용 일치), 회전 사진 좌표, 자동 위치
- 스마트 결과가 실제로 목표 SSIM 을 넘는지 (엔진과 독립적인 정식 슬라이딩 창 SSIM 으로 검증)
- 동시 변환 63장(4천만 화소 포함) 파일명 충돌 없음, 압축 파일 4종(한글 CP949 ZIP 포함), 임시 폴더 정리
- 메타데이터 72개 조합
