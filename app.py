"""WebP 변환기 - Windows / macOS 공용 GUI."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import webp_core as core
from crop_editor import CropEditor

try:  # 드래그 앤 드롭 (선택)
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND = True
except Exception:  # pragma: no cover
    DND = False

APP_NAME = "WebP·PNG 변환기"

LOSSY_SRC_EXTS = {".jpg", ".jpeg", ".jfif", ".webp", ".heic", ".heif"}


# 옵션 옆 [?] 를 누르면 나오는 설명 (제목, 본문)
HELP_TOPICS = {
    "add": ("파일 추가하는 방법",
            "• [파일 추가] / [폴더 추가] 버튼을 쓰거나, 파일·폴더를 창에 끌어다 놓으세요.\n"
            "• 폴더는 하위 폴더까지 모두 포함됩니다.\n"
            "• 압축 파일(ZIP, CBZ, 7Z, CB7, TAR, TAR.GZ)을 넣으면 안의 이미지를 전부 변환합니다. (🗜 표시)\n"
            "• 목록에서 Delete 키로 선택한 항목을 지웁니다. 사진을 더블클릭하면 개별 자르기 창이 열리고, "
            "오른쪽 클릭 메뉴에서 결과·원본 파일 위치를 열 수 있습니다.\n"
            "• 설정을 고른 뒤 [▶ 변환 시작]을 누르세요. 결과가 원본보다 커진 파일은 주황색으로 표시됩니다."),
    "fmt": ("저장 형식",
            "• WebP: 같은 화질에서 용량이 가장 작습니다. 사진·웹 업로드에 추천합니다. "
            "오래된 프로그램이나 일부 사이트는 WebP를 못 열 수 있습니다.\n\n"
            "• PNG: 어디서나 열리고 투명 배경도 지원합니다. 스크린샷·로고·도표에 알맞고, "
            "사진은 같은 화질에서 WebP보다 용량이 3~8배 큽니다."),
    "smart": ("스마트 최적화 (추천)",
              "이미지마다 원본과 비교하면서 '화질 차이가 거의 없는 가장 작은 용량'을 자동으로 찾습니다. "
              "사진마다 알맞은 품질이 달라서 고정 품질보다 효율적입니다.\n\n"
              "• 최고 화질 (기본값): 거의 무손실. 확대해도 차이를 찾기 어렵습니다.\n"
              "• 균형: 원래 크기로 보면 거의 같지만, 확대하면 약간 뭉개질 수 있습니다. 용량은 더 작습니다.\n"
              "• 작은 용량: 웹 업로드용. 확대하면 차이가 보입니다.\n\n"
              "PNG를 고르면 색상 수를 줄이는 방식으로 압축하고, 색 변화가 많은 사진은 화질을 지키려고 "
              "무손실로 저장합니다."),
    "quality": ("품질 직접 지정",
                "슬라이더로 1~100 사이 품질을 직접 정합니다. 모든 이미지에 같은 값이 적용됩니다.\n\n"
                "• 85~95: 일반적으로 무난합니다.\n"
                "• 낮을수록 용량이 작아지고 화질은 떨어집니다.\n\n"
                "어떤 값이 좋을지 모르겠다면 '스마트 최적화'를 쓰세요. (PNG에서는 색상 줄이기 품질로 쓰입니다)"),
    "lossless": ("무손실",
                 "픽셀이 원본과 100% 똑같이 저장됩니다. 화질 손실이 없는 대신 용량은 크게 줄지 않습니다.\n\n"
                 "※ JPG 사진을 무손실로 바꾸면 용량이 오히려 몇 배로 커집니다. "
                 "스크린샷·그래픽·편집용 원본에 쓰세요."),
    "resize": ("크기 조절",
               "비율은 항상 유지됩니다. (잘라내지 않습니다)\n\n"
               "• 원본 크기 유지: 해상도를 바꾸지 않습니다.\n"
               "• 긴 변 기준: 가로·세로 중 긴 쪽을 입력값에 맞춥니다. 예) 1280 → 가로 사진은 가로 1280px\n"
               "• 가로 기준 / 세로 기준: 해당 변을 입력값에 맞춥니다.\n"
               "• 비율(%): 예) 50이면 가로·세로가 절반이 됩니다.\n"
               "• 가로×세로 안에 맞춤: 입력한 상자 안에 들어가게 줄입니다. 예) 1280×720\n"
               "• 가로×세로로 꽉 채우기: 입력한 크기에 정확히 맞추고 넘치는 부분은 자릅니다. "
               "예) 1280×720 → 어떤 사진이든 정확히 1280×720 (자르는 위치는 '자르기'의 위치 설정)\n\n"
               "해상도를 줄이면 용량이 가장 크게 줄어듭니다. 수천만 화소 사진도 1280~1920px이면 화면용으로 충분합니다."),
    "crop": ("자르기",
             "원하는 비율로 사진을 자릅니다. 자른 뒤에 크기 조절이 적용됩니다.\n"
             "예) 16:9 + '긴 변 1280' → 정확히 1280×720\n\n"
             "• 일괄: 여기서 비율을 고르면 목록의 모든 사진(압축 파일 안 이미지 포함)에 적용됩니다.\n"
             "• 위치: '가운데'는 정가운데를 남기고, '자동(피사체)'는 디테일이 많은 쪽(보통 피사체)을 남깁니다. "
             "얼굴을 인식하는 기능은 아니라서 가끔 엉뚱하게 자를 수 있습니다.\n"
             "• 사진 방향에 맞춰 비율 뒤집기: 가로·세로 사진이 섞여 있을 때, 세로 사진에는 16:9 대신 9:16 처럼 "
             "뒤집어 적용합니다 (반대로 세로 비율을 고르면 가로 사진에는 가로로).\n\n"
             "• 개별: 목록에서 사진을 더블클릭(또는 [✂ 개별 자르기])하면 미리보기 창이 열립니다. "
             "박스를 끌어 위치·크기를 정하면 그 사진만 그렇게 자릅니다 (목록에 ✂ 표시). "
             "일부 사진만 자르려면 일괄은 '자르지 않음'으로 두고, 자를 사진만 개별로 지정하세요.\n"
             "• 오른쪽 클릭 메뉴: '이 사진은 자르지 않기', '일괄 설정대로 되돌리기'\n\n"
             "원본 파일은 잘리지 않습니다. 결과 파일만 잘립니다."),
    "noup": ("원본보다 크게 확대하지 않음",
             "켜져 있으면 입력한 크기보다 작은 이미지는 확대하지 않고 그대로 둡니다. "
             "확대하면 용량만 늘고 화질은 좋아지지 않기 때문에, 보통 켜 두세요."),
    "save": ("저장 위치",
             "• 원본과 같은 폴더: 원본 옆에 같은 이름으로 .webp / .png 파일을 저장합니다. 원본은 그대로 남습니다.\n"
             "• 지정 폴더: 선택한 폴더에 모아서 저장합니다.\n\n"
             "압축 파일은 '이름_webp.zip' 형태로 저장됩니다. 같은 이름의 파일이 있으면 '_1'을 붙입니다."),
    "overwrite": ("같은 이름 파일 덮어쓰기",
                  "• 끄면 (권장): 같은 이름의 파일이 이미 있으면 '이름_1.webp'처럼 번호를 붙여 새로 저장합니다.\n"
                  "• 켜면: 같은 이름의 결과 파일을 복구할 수 없게 덮어씁니다. "
                  "같은 설정으로 여러 번 다시 변환할 때만 사용하세요."),
    "meta": ("EXIF·색상 프로파일 유지",
             "EXIF는 촬영 날짜·카메라 정보·촬영 위치(GPS) 같은 사진 정보입니다.\n\n"
             "• 켜면: 이 정보와 색상 프로파일(색이 다르게 보이지 않게 해주는 정보)을 그대로 유지합니다.\n"
             "• 끄면: 정보가 모두 지워져서 개인정보가 보호되고 용량도 조금 줄어듭니다. "
             "다만 색상 프로파일도 빠지므로 넓은 색 영역으로 찍은 사진은 색이 약간 달라 보일 수 있습니다.\n\n"
             "※ 인터넷에 올리거나 남에게 보낼 사진은 끄세요. 위치 정보가 남을 수 있습니다. "
             "(사진 방향은 이 옵션과 상관없이 항상 올바르게 저장됩니다)"),
    "arcfolder": ("압축 파일은 폴더로 풀어서 저장",
                  "• 끄면 (기본): 변환된 이미지를 다시 ZIP으로 묶어 '이름_webp.zip'(CBZ는 .cbz)으로 저장합니다.\n"
                  "• 켜면: 묶지 않고 '이름_webp' 폴더 안에 파일을 그대로 풀어서 저장합니다.\n\n"
                  "압축 파일을 넣지 않았다면 이 옵션은 영향이 없습니다."),
}

HELP_TEXT = """\
■ 기본 안내
• 원본 파일은 수정하거나 삭제하지 않습니다. 결과는 새 파일로 저장됩니다.
• 같은 이름이 있으면 '이름_1.webp'처럼 번호를 붙여 저장합니다. ('같은 이름 파일 덮어쓰기'를 켜면 기존 파일을 덮어씁니다)
• 목록에서 사진을 더블클릭하면 개별 자르기 창이 열립니다. 결과 파일 위치는 오른쪽 클릭 메뉴에서 열 수 있습니다.
• 각 옵션 옆의 파란 [?] 버튼을 누르면 그 항목의 설명이 나옵니다. (F1 키: 이 도움말 열기)

■ 화질·용량
• WebP는 손실 압축입니다. '최고 화질'도 픽셀이 100% 같지는 않고 눈으로 구분하기 어려운 수준입니다. 완전히 같게 저장하려면 '무손실'을 고르세요.
• 이미 변환한 WebP를 다시 변환하면 화질이 한 번 더 나빠집니다. 원본으로 한 번만 변환하세요.
• JPG처럼 이미 압축된 사진을 PNG나 WebP 무손실로 바꾸면 용량이 오히려 커집니다. 용량을 줄이는 게 목적이면 WebP 스마트 최적화를 쓰세요.
• 이미 많이 압축된 JPG를 '최고 화질'로 변환하면 원본보다 커질 수 있습니다. 이럴 땐 '균형'을 쓰세요. 용량이 늘어난 파일은 주황색(+%)으로 표시됩니다.
• 노이즈가 많은 사진(고감도·어두운 곳에서 찍은 사진 등)은 '최고 화질' 목표에 도달할 수 없어 품질 85로 저장하고, 상태에 '화질이 떨어질 수 있음'이라고 표시합니다(주황색). 용량은 크게 줄지만 화질이 눈에 띄게 떨어질 수 있으니, 그런 파일은 '무손실'이나 '품질 직접 지정'(높은 값)으로 다시 변환하세요.
• 사진은 WebP, 스크린샷·로고·투명 이미지는 PNG가 잘 맞습니다. PNG 사진은 색상을 줄여서 저장하며(스마트 최적화), 색 변화가 많은 사진은 화질을 지키려고 무손실로 저장해서 용량이 크게 줄지 않습니다.

■ 크기 조절
• 원본보다 크게 확대하지 않는 옵션이 켜져 있으면, 이미 작은 이미지는 크기가 그대로입니다.
• 비율은 항상 유지됩니다. 가로×세로 상자를 지정하면 그 안에 맞춰집니다 (잘라내지 않음).
• WebP는 한 변이 16,383px을 넘을 수 없어 그보다 크면 자동으로 줄입니다.

■ 자르기
• '자르기'에서 비율을 고르면 모든 사진을 그 비율로 자른 뒤 크기를 조절합니다. (원본은 그대로)
• 사진별로 다르게 자르려면 목록에서 더블클릭하세요. 미리보기에서 박스를 끌어 지정하고, ←/→ 로 다음 사진으로 넘깁니다.
• '자동(피사체)' 위치는 디테일이 많은 쪽을 남기는 방식이라 가끔 엉뚱하게 자를 수 있습니다. 중요한 사진은 개별로 확인하세요.
• 압축 파일 안의 이미지는 일괄 설정만 적용됩니다.

■ 개인정보
• 'EXIF·색상 프로파일 유지'를 켜면 촬영 위치(GPS)·카메라 정보도 파일에 남습니다. 인터넷에 올리거나 남에게 보낼 사진은 이 옵션을 끄세요.
• 사진의 회전 정보는 항상 반영되어 저장됩니다. 흑백(16비트)·CMYK 이미지는 일반 색상(RGB)으로 바뀝니다.

■ 포터블 (설치 없이 사용)
• 설치가 필요 없습니다. 폴더(또는 USB)째 옮겨서 어느 PC에서나 실행하세요. 지우려면 파일만 삭제하면 됩니다.
• 설정은 프로그램 바로 옆의 'WebPConverter.settings.json'에 저장됩니다. 윈도우 레지스트리나 사용자 폴더에는 아무것도 남기지 않습니다. (압축 파일 처리 중에만 임시 폴더를 쓰고 끝나면 지웁니다)
• 프로그램 폴더에 쓸 수 없으면(읽기 전용 USB, 'Program Files' 등) 설정이 사용자 폴더에 저장됩니다.
• 맥: 다운로드한 앱을 압축 폴더 안에서 바로 실행하면 보안 때문에 읽기 전용이 되어 설정이 저장되지 않을 수 있습니다. 앱을 다른 폴더로 옮긴 뒤 실행하세요.

■ 압축 파일 (ZIP, CBZ, 7Z, CB7, TAR, TAR.GZ)
• 안의 이미지를 모두 변환해서 '이름_webp.zip'으로 저장합니다. 이미지가 아닌 파일은 그대로 들어갑니다.
• RAR/CBR 파일은 지원하지 않습니다 (목록에 추가되지 않음). 반디집 등으로 ZIP으로 바꿔서 넣어주세요.
• 암호가 걸린 압축 파일, 손상된 압축 파일은 변환되지 않고 오류로 표시됩니다.
• 처리 중 임시로 압축을 풉니다. 압축 파일 2개 분량의 빈 공간(압축 해제 크기 기준)이 시스템 드라이브에 필요합니다. 끝나면 자동으로 지워집니다.
• 안에서 같은 이름의 이미지(01.jpg와 01.png)가 있으면 '01_1.webp'처럼 번호가 붙습니다.

■ 대량 처리
• 수천 장도 처리할 수 있지만 시간이 걸립니다. 진행 중에는 [■ 중지]로 멈출 수 있고, 이미 변환된 파일은 그대로 남습니다.
• 변환 중에는 목록을 바꿀 수 없습니다. 창을 닫으면 작업이 중단됩니다.
• 2,500만 화소가 넘는 큰 사진은 메모리 때문에 동시에 2장까지만 처리합니다. 컴퓨터 메모리가 부족하면 해당 파일이 오류로 표시됩니다.
• PNG 저장은 WebP보다 4배쯤 오래 걸립니다.

■ 호환성
• WebP는 최신 브라우저·휴대폰·포토샵(2022 이후)에서 열립니다. 오래된 프로그램이나 일부 사이트 업로드·인쇄 서비스는 WebP를 받지 않을 수 있으니, 그럴 땐 PNG로 저장하세요.
• 움직이는 GIF는 움직이는 WebP/PNG로 저장됩니다. 움직이는 PNG는 일부 오래된 뷰어에서 첫 장면만 보일 수 있습니다.
• 아이폰 HEIC 사진도 변환할 수 있습니다.

■ 처음 실행할 때 보안 경고
• Windows: '추가 정보 → 실행'을 누르세요.
• macOS: 앱을 우클릭 → 열기 (또는 터미널에서 xattr -cr 앱경로).
"""

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


def _app_dir() -> Path:
    """실행 파일(.exe) 또는 맥 앱(.app)이 들어 있는 폴더."""
    exe = Path(sys.executable).resolve()
    if IS_MAC and ".app/Contents/MacOS" in exe.as_posix():
        return exe.parents[3]  # X.app/Contents/MacOS/X → X.app 을 담은 폴더
    return exe.parent


def _find_config() -> tuple[Path, bool]:
    """(설정 파일 경로, 포터블 여부).
    포터블(빌드된 실행 파일): 프로그램 옆 'WebPConverter.settings.json' → 폴더째 옮겨도 설정이 따라가고,
    PC 에는 아무 흔적도 남지 않음. 프로그램 폴더에 쓸 수 없으면(읽기 전용 USB 등) 사용자 폴더로 대체.
    개발 중(파이썬으로 실행)에는 사용자 폴더 사용."""
    forced = os.environ.get("WEBPCONV_CONFIG_DIR")
    if forced or getattr(sys, "frozen", False):
        d = Path(forced) if forced else _app_dir()
        f = d / "WebPConverter.settings.json"
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(f, "a", encoding="utf-8"):  # 쓰기 가능한지 실제로 확인
                pass
            return f, True
        except OSError:
            pass
    return Path.home() / ".webp_converter.json", False


CONFIG, PORTABLE = _find_config()

LEVELS = [("최고 화질 (추천·거의 무손실)", "best"), ("균형", "balanced"), ("작은 용량", "small")]
RESIZE = [
    ("원본 크기 유지", "none"),
    ("긴 변 기준 (px)", "long"),
    ("가로 기준 (px)", "width"),
    ("세로 기준 (px)", "height"),
    ("비율 (%)", "percent"),
    ("가로×세로 안에 맞춤", "fit"),
    ("가로×세로 꽉 채우기", "cover"),
]
CROPS = [
    ("자르지 않음", None), ("1:1 정사각형", (1, 1)), ("4:3", (4, 3)), ("3:2", (3, 2)), ("16:9", (16, 9)),
    ("4:5 (인스타 세로)", (4, 5)), ("2:3", (2, 3)), ("9:16 (세로 영상)", (9, 16)), ("직접 입력", "custom"),
]


def open_in_file_manager(path: Path):
    try:
        if IS_WIN:
            if path.is_file():
                subprocess.run(["explorer", "/select,", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.run(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path if path.is_dir() else path.parent)])
    except Exception as e:  # noqa: BLE001
        messagebox.showerror(APP_NAME, f"폴더를 열 수 없습니다.\n{e}")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME + (" (포터블)" if PORTABLE else ""))
        root.geometry("1000x740")
        root.minsize(860, 620)

        self.items: dict[str, dict] = {}  # tree item id -> {path, kind, units, key, err}
        self.crops: dict[str, object] = {}  # 사진별 자르기: "none" 또는 (x0, y0, x1, y1). 없으면 일괄 설정대로
        self.known: set[str] = set()      # 중복 추가 방지
        self.results: dict[str, core.Result] = {}
        self.adding = False
        self._last_warn_key = None
        self.notice = None
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.cancel = threading.Event()

        self._vars()
        self._load_config()
        self._save_config()
        self._build()
        self._update_mode_widgets()
        self._update_resize_widgets()
        for v in (self.v_fmt, self.v_mode):  # 값이 어디서 바뀌든 화면 안내 갱신
            v.trace_add("write", lambda *_: self._update_mode_widgets())
        root.bind("<F1>", lambda e: self.show_help())
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)

    # ------------------------------------------------------------ 상태 변수
    def _vars(self):
        self.v_fmt = tk.StringVar(value="webp")
        self.v_mode = tk.StringVar(value="smart")
        self.v_level = tk.StringVar(value=LEVELS[0][0])
        self.v_quality = tk.IntVar(value=85)
        self.v_resize = tk.StringVar(value=RESIZE[0][0])
        self.v_w = tk.StringVar(value="1920")
        self.v_h = tk.StringVar(value="1080")
        self.v_long = tk.StringVar(value="1920")
        self.v_pct = tk.StringVar(value="50")
        self.v_noup = tk.BooleanVar(value=True)
        self.v_meta = tk.BooleanVar(value=True)
        self.v_outmode = tk.StringVar(value="same")
        self.v_outdir = tk.StringVar(value="")
        self.v_overwrite = tk.BooleanVar(value=False)
        self.v_arc_folder = tk.BooleanVar(value=False)
        self.v_crop = tk.StringVar(value=CROPS[0][0])
        self.v_crop_w = tk.StringVar(value="3")
        self.v_crop_h = tk.StringVar(value="2")
        self.v_crop_anchor = tk.StringVar(value="center")
        self.v_crop_flip = tk.BooleanVar(value=True)
        self.v_status = tk.StringVar(value="이미지나 압축 파일을 추가하세요.")

    def _cfg_vars(self):
        return {
            "fmt": self.v_fmt, "mode": self.v_mode, "level": self.v_level, "quality": self.v_quality,
            "resize": self.v_resize, "w": self.v_w, "h": self.v_h, "long": self.v_long,
            "pct": self.v_pct, "noup": self.v_noup, "meta": self.v_meta,
            "outmode": self.v_outmode, "outdir": self.v_outdir, "overwrite": self.v_overwrite,
            "arc_folder": self.v_arc_folder, "crop": self.v_crop, "crop_w": self.v_crop_w,
            "crop_h": self.v_crop_h, "crop_anchor": self.v_crop_anchor, "crop_flip": self.v_crop_flip,
        }

    def _load_config(self):
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            for k, v in self._cfg_vars().items():
                if k in data:
                    v.set(data[k])
        except Exception:
            pass

    def _save_config(self):
        try:
            CONFIG.write_text(json.dumps({k: v.get() for k, v in self._cfg_vars().items()},
                                         ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------ 화면 구성
    def _build(self):
        pad = dict(padx=8, pady=4)
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Button(top, text="파일 추가", command=self.add_files).pack(side="left")
        ttk.Button(top, text="폴더 추가", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="선택 삭제", command=self.remove_selected).pack(side="left")
        ttk.Button(top, text="✂ 개별 자르기", command=self.open_crop_editor).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="목록 비우기", command=self.clear).pack(side="left", padx=4)
        ttk.Button(top, text="도움말·주의사항", command=self.show_help).pack(side="left", padx=4)
        self._qmark(top, "add").pack(side="right", padx=(6, 0))
        hint = ("파일·폴더·ZIP 끌어다 놓기 가능" if DND
                else "끌어다 놓기를 쓸 수 없어요 · 위 버튼으로 추가하세요")
        ttk.Label(top, text=hint, foreground="#888").pack(side="right")

        # 파일 목록
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=True, padx=10)
        cols = ("name", "dim", "orig", "new", "saved", "status")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, t, w, anchor in [
            ("name", "파일", 240, "w"), ("dim", "해상도", 200, "center"),
            ("orig", "원본", 85, "e"), ("new", "결과", 85, "e"),
            ("saved", "절감", 60, "e"), ("status", "상태", 260, "w"),
        ]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c in ("name", "status")))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.tag_configure("err", foreground="#c0392b")
        self.tree.tag_configure("done", foreground="#1e8449")
        self.tree.tag_configure("warn", foreground="#b9770e")
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", lambda e: self.open_crop_editor())
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="✂ 자르기 조정…", command=self.open_crop_editor)
        self.menu.add_command(label="이 사진은 자르지 않기", command=lambda: self._set_crop_selected("none"))
        self.menu.add_command(label="자르기를 일괄 설정대로 되돌리기", command=lambda: self._set_crop_selected(None))
        self.menu.add_separator()
        self.menu.add_command(label="결과 파일 위치 열기", command=lambda: self._open_selected(True))
        self.menu.add_command(label="원본 파일 위치 열기", command=lambda: self._open_selected(False))
        for seq in (("<Button-2>", "<Control-Button-1>") if IS_MAC else ("<Button-3>",)):
            self.tree.bind(seq, self._popup)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<BackSpace>", lambda e: self.remove_selected())
        if DND:
            for w in (self.tree, self.root):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

        # 설정
        opts = ttk.Frame(self.root)
        opts.pack(fill="x", padx=10, pady=6)
        opts.columnconfigure((0, 1, 2), weight=1, uniform="o")

        def row(parent, topic=None, indent=8, pady=(2, 2)):
            """한 줄 (왼쪽에 옵션, 오른쪽 끝에 [?])"""
            r = ttk.Frame(parent)
            r.pack(anchor="w", fill="x", padx=(indent, 8), pady=pady)
            return r

        def qm(r, topic):
            self._qmark(r, topic).pack(side="right", padx=(6, 0))

        def section(col, title, topic, padx):
            hdr = ttk.Frame(opts)
            ttk.Label(hdr, text=" " + title + " ").pack(side="left")
            self._qmark(hdr, topic).pack(side="left", padx=(0, 4))
            f = ttk.LabelFrame(opts, labelwidget=hdr)
            f.grid(row=0, column=col, sticky="nsew", padx=padx)
            return f

        f1 = section(0, "형식 · 압축", "fmt", (0, 6))
        r = row(f1, pady=(4, 0))
        ttk.Label(r, text="저장 형식:").pack(side="left")
        for t, v in (("WebP", "webp"), ("PNG", "png")):
            ttk.Radiobutton(r, text=t, value=v, variable=self.v_fmt,
                            command=self._update_mode_widgets).pack(side="left", padx=(8, 0))
        self.lb_fmt = ttk.Label(f1, foreground="#888")
        self.lb_fmt.pack(anchor="w", padx=8)
        r = row(f1)
        ttk.Radiobutton(r, text="스마트 최적화 (추천)", value="smart", variable=self.v_mode,
                        command=self._update_mode_widgets).pack(side="left")
        qm(r, "smart")
        self.cb_level = ttk.Combobox(f1, values=[l for l, _ in LEVELS], textvariable=self.v_level,
                                     state="readonly", width=24)
        self.cb_level.pack(anchor="w", padx=28)
        r = row(f1)
        ttk.Radiobutton(r, text="품질 직접 지정", value="quality", variable=self.v_mode,
                        command=self._update_mode_widgets).pack(side="left")
        qm(r, "quality")
        qrow = ttk.Frame(f1)
        qrow.pack(anchor="w", padx=28, fill="x")
        self.sc_q = ttk.Scale(qrow, from_=1, to=100, orient="horizontal", length=150,
                              command=lambda v: self.v_quality.set(int(float(v))))
        self.sc_q.set(self.v_quality.get())
        self.sc_q.pack(side="left")
        ttk.Label(qrow, textvariable=self.v_quality, width=4).pack(side="left", padx=4)
        r = row(f1, pady=(2, 6))
        ttk.Radiobutton(r, text="무손실 (픽셀 100% 동일)", value="lossless", variable=self.v_mode,
                        command=self._update_mode_widgets).pack(side="left")
        qm(r, "lossless")

        f2 = section(1, "크기 · 자르기", "resize", (6, 6))
        cb = ttk.Combobox(f2, values=[l for l, _ in RESIZE], textvariable=self.v_resize,
                          state="readonly", width=24)
        cb.pack(anchor="w", padx=8, pady=(6, 4))
        cb.bind("<<ComboboxSelected>>", lambda e: self._update_resize_widgets())
        self.rz_row = ttk.Frame(f2)
        self.rz_row.pack(anchor="w", padx=8, pady=4)
        r = row(f2)
        ttk.Checkbutton(r, text="원본보다 크게 확대하지 않음", variable=self.v_noup).pack(side="left")
        qm(r, "noup")
        ttk.Separator(f2).pack(fill="x", padx=8, pady=(4, 2))
        r = row(f2)
        ttk.Label(r, text="자르기:").pack(side="left")
        self.cb_crop = ttk.Combobox(r, values=[l for l, _ in CROPS], textvariable=self.v_crop,
                                    state="readonly", width=17)
        self.cb_crop.pack(side="left", padx=(4, 0))
        self.cb_crop.bind("<<ComboboxSelected>>", lambda e: self._update_crop_widgets())
        qm(r, "crop")
        self.crop_custom = ttk.Frame(f2)
        ttk.Entry(self.crop_custom, textvariable=self.v_crop_w, width=5, justify="right").pack(side="left")
        ttk.Label(self.crop_custom, text=":").pack(side="left", padx=2)
        ttk.Entry(self.crop_custom, textvariable=self.v_crop_h, width=5, justify="right").pack(side="left")
        ttk.Label(self.crop_custom, text="(가로:세로)", foreground="#888").pack(side="left", padx=4)
        self.crop_opts = ttk.Frame(f2)
        r = ttk.Frame(self.crop_opts)
        r.pack(anchor="w", fill="x")
        ttk.Label(r, text="위치:").pack(side="left")
        ttk.Radiobutton(r, text="가운데", value="center", variable=self.v_crop_anchor).pack(side="left", padx=(4, 0))
        ttk.Radiobutton(r, text="자동(피사체)", value="smart", variable=self.v_crop_anchor).pack(side="left", padx=(6, 0))
        self.crop_flip_row = ttk.Frame(self.crop_opts)
        self.crop_flip_row.pack(anchor="w", fill="x", pady=(2, 0))
        ttk.Checkbutton(self.crop_flip_row, text="사진 방향에 맞춰 비율 뒤집기",
                        variable=self.v_crop_flip).pack(side="left")
        self.crop_hint = ttk.Label(f2, foreground="#888", wraplength=280, justify="left")
        self._update_crop_widgets()

        f3 = section(2, "저장", "save", (6, 0))
        r = row(f3, pady=(4, 0))
        ttk.Radiobutton(r, text="원본과 같은 폴더", value="same", variable=self.v_outmode,
                        command=self._update_out_widgets).pack(side="left")
        orow = ttk.Frame(f3)
        orow.pack(anchor="w", fill="x", padx=8)
        ttk.Radiobutton(orow, text="지정 폴더", value="dir", variable=self.v_outmode,
                        command=self._update_out_widgets).pack(side="left")
        self.bt_out = ttk.Button(orow, text="찾아보기…", command=self.choose_outdir)
        self.bt_out.pack(side="left", padx=6)
        self.lb_out = ttk.Label(f3, textvariable=self.v_outdir, foreground="#666", width=34)
        self.lb_out.pack(anchor="w", padx=28)
        r = row(f3)
        ttk.Checkbutton(r, text="같은 이름 파일 덮어쓰기", variable=self.v_overwrite).pack(side="left")
        qm(r, "overwrite")
        r = row(f3, pady=(0, 2))
        ttk.Checkbutton(r, text="EXIF·색상 프로파일 유지", variable=self.v_meta).pack(side="left")
        qm(r, "meta")
        r = row(f3, pady=(2, 6))
        ttk.Checkbutton(r, text="압축 파일은 폴더로 풀어서 저장", variable=self.v_arc_folder).pack(side="left")
        qm(r, "arcfolder")
        self._update_out_widgets()

        # 하단
        bot = ttk.Frame(self.root)
        bot.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Style().configure("Horizontal.TProgressbar", background="#3b82f6")
        self.pb = ttk.Progressbar(bot, mode="determinate")
        self.pb.pack(fill="x")
        row = ttk.Frame(bot)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, textvariable=self.v_status).pack(side="left")
        self.bt_start = ttk.Button(row, text="▶ 변환 시작", command=self.start)
        self.bt_start.pack(side="right")
        ttk.Button(row, text="결과 폴더 열기", command=self.open_output).pack(side="right", padx=6)

    def _update_mode_widgets(self):
        self.lb_fmt.configure(text=(
            "용량이 가장 작음 (사진·웹용 추천)" if self.v_fmt.get() == "webp" else
            "어디서나 열림 · 그래픽/투명 이미지에 적합"))
        m = self.v_mode.get()
        self.cb_level.configure(state="readonly" if m == "smart" else "disabled")
        self.sc_q.state(["!disabled"] if m == "quality" else ["disabled"])

    def _update_out_widgets(self):
        self.bt_out.state(["!disabled"] if self.v_outmode.get() == "dir" else ["disabled"])

    def _resize_key(self) -> str:
        return dict(RESIZE).get(self.v_resize.get(), "none")

    def _update_resize_widgets(self):
        for w in self.rz_row.winfo_children():
            w.destroy()
        k = self._resize_key()

        def entry(var, label):
            ttk.Entry(self.rz_row, textvariable=var, width=7, justify="right").pack(side="left")
            ttk.Label(self.rz_row, text=label).pack(side="left", padx=(2, 6))

        if k == "long":
            entry(self.v_long, "px")
        elif k == "width":
            entry(self.v_w, "px")
        elif k == "height":
            entry(self.v_h, "px")
        elif k == "percent":
            entry(self.v_pct, "%")
        elif k in ("fit", "cover"):
            entry(self.v_w, "×")
            entry(self.v_h, "px")
        else:
            ttk.Label(self.rz_row, text="해상도를 바꾸지 않습니다", foreground="#888").pack(side="left")
        if hasattr(self, "cb_crop"):
            self._update_crop_widgets()

    def _crop_key(self):
        return dict(CROPS).get(self.v_crop.get())

    def _update_crop_widgets(self):
        for w in (self.crop_custom, self.crop_opts, self.crop_hint):
            w.pack_forget()
        if self._resize_key() == "cover":
            self.cb_crop.state(["disabled"])
            self.crop_hint.configure(text="꽉 채우기는 입력한 크기에 맞춰 넘치는 부분을 자릅니다. 자르는 위치:")
            self.crop_hint.pack(anchor="w", padx=8)
            self.crop_flip_row.pack_forget()  # 꽉 채우기는 항상 입력한 가로×세로 그대로
            self.crop_opts.pack(anchor="w", fill="x", padx=8, pady=(0, 4))
            return
        self.cb_crop.state(["!disabled"])
        self.crop_flip_row.pack(anchor="w", fill="x", pady=(2, 0))
        k = self._crop_key()
        if k == "custom":
            self.crop_custom.pack(anchor="w", padx=60, pady=(0, 2))
        if k:
            self.crop_opts.pack(anchor="w", fill="x", padx=8, pady=(0, 4))
        else:
            self.crop_hint.configure(text="사진별로 자르려면 목록에서 더블클릭하세요.")
            self.crop_hint.pack(anchor="w", padx=8, pady=(0, 4))

    # ------------------------------------------------------------ 안내·경고
    def _qmark(self, parent, topic):
        """옵션 옆에 붙는 작은 [?] 버튼 - 누르면 해당 항목 설명이 열림."""
        lb = tk.Label(parent, text="?", width=2, cursor="hand2", relief="solid", bd=1,
                      font=("TkDefaultFont", 8, "bold"), fg="#1d4ed8", bg="#e8f0fe")
        lb.bind("<Button-1>", lambda e: self.show_topic(topic))
        lb.bind("<Enter>", lambda e: lb.configure(bg="#c7dafc"))
        lb.bind("<Leave>", lambda e: lb.configure(bg="#e8f0fe"))
        return lb

    def show_topic(self, key):
        title, body = HELP_TOPICS[key]
        old = getattr(self, "topic_win", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        win = self.topic_win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack()
        ttk.Label(frm, text=title, font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, text=body, justify="left", wraplength=460).pack(anchor="w", pady=(10, 14))
        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="전체 도움말", command=lambda: (win.destroy(), self.show_help())).pack(side="left")
        close = ttk.Button(btns, text="닫기", command=win.destroy)
        close.pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: win.destroy())
        win.update_idletasks()  # 메인 창 가운데에 표시
        x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        close.focus_set()

    def show_help(self):
        if getattr(self, "help_win", None) and self.help_win.winfo_exists():
            self.help_win.lift()
            return
        win = self.help_win = tk.Toplevel(self.root)
        win.title("도움말·주의사항")
        win.geometry("720x620")
        win.transient(self.root)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=10, pady=10)
        txt = tk.Text(frm, wrap="word", padx=10, pady=8, spacing1=2, spacing3=3, relief="flat",
                      background="#fafafa")
        sb = ttk.Scrollbar(frm, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.tag_configure("h", font=("TkDefaultFont", 11, "bold"), spacing1=12, spacing3=4)
        for line in HELP_TEXT.splitlines():
            txt.insert("end", line + "\n", "h" if line.startswith("■") else ())
        txt.configure(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        ttk.Button(win, text="닫기", command=win.destroy).pack(pady=(0, 10))

    def _risk_warnings(self, jobs, s: core.Settings) -> list[str]:
        """변환을 시작하기 전에 알려야 할 주의사항 (없으면 빈 목록)."""
        w = []
        files = [it["path"] for _, it in jobs if it["kind"] == "file"]
        lossy_src = [f for f in files if f.suffix.lower() in LOSSY_SRC_EXTS]
        archives = [it for _, it in jobs if it["kind"] == "archive"]
        bigger_risk = s.out_format == "png" or s.mode == "lossless"
        if bigger_risk and lossy_src and s.resize_mode == "none":
            fmt = "PNG" if s.out_format == "png" else "WebP 무손실"
            w.append(f"JPG·WebP·HEIC 사진 {len(lossy_src):,}장을 크기 변경 없이 {fmt}로 저장하면 "
                     "용량이 원본보다 커질 수 있습니다. (용량을 줄이려면 WebP 스마트 최적화를 추천합니다)")
        if s.overwrite:
            w.append("'같은 이름 파일 덮어쓰기'가 켜져 있습니다. 저장 위치에 같은 이름의 결과 파일이 있으면 "
                     "복구할 수 없게 덮어씁니다.")
        if s.keep_metadata and (files or archives):
            w.append("'EXIF 유지'가 켜져 있어 사진의 촬영 위치(GPS)가 결과에도 남습니다. "
                     "공유·업로드용이면 옵션을 끄세요.")
        if archives:
            n_img = sum(it["units"] for it in archives)
            w.append(f"압축 파일 {len(archives)}개(안에 이미지 {n_img:,}장)는 임시로 풀어서 처리합니다. "
                     "시스템 드라이브에 여유 공간이 필요합니다.")
        return w

    def _confirm_start(self, jobs, s) -> bool:
        w = self._risk_warnings(jobs, s)
        if not w:
            return True
        # 매번 같은 경고를 반복하지 않도록 '개인정보·임시공간' 안내는 한 번만 묻고,
        # 용량 증가·덮어쓰기처럼 되돌릴 수 없거나 손해가 생길 수 있는 경고는 항상 묻는다.
        key = tuple(x[:12] for x in w)
        important = [x for x in w if x.startswith(("JPG", "'같은 이름"))]
        if not important and key == self._last_warn_key:
            return True
        self._last_warn_key = key
        body = "\n\n".join("• " + x for x in w)
        return messagebox.askokcancel(APP_NAME, f"변환하기 전에 확인해 주세요.\n\n{body}\n\n계속할까요?")

    # ------------------------------------------------------------ 파일 목록
    def add_paths(self, paths):
        """폴더 검색·압축 파일 목록 읽기는 백그라운드에서 → 수만 개를 넣어도 창이 멈추지 않음."""
        if self.running or self.adding:
            return
        self.adding = True
        self.v_status.set("파일 목록을 읽는 중…")

        def scan():
            batch = []
            skipped = core.find_unsupported_archives(paths)
            if skipped:
                self.q.put(("notice", None, skipped))
            for f in core.collect_files(paths):
                try:
                    key = str(f.resolve())
                    size = f.stat().st_size
                    if core.is_archive(f):
                        try:
                            n_img, n_other = core.archive_listing(f)
                            batch.append((key, f, "archive", size, n_img, None))
                        except Exception:
                            batch.append((key, f, "archive", size, 0, "압축 파일이 손상되었거나 읽을 수 없습니다"))
                    else:
                        batch.append((key, f, "file", size, 1, None))
                except OSError:
                    continue
                if len(batch) >= 300:
                    self.q.put(("add", None, batch))
                    batch = []
            self.q.put(("add", None, batch))
            self.q.put(("add_end", None, None))

        threading.Thread(target=scan, daemon=True).start()

    def _insert_rows(self, batch):
        for key, f, kind, size, n_img, err in batch:
            if key in self.known:
                continue
            self.known.add(key)
            if kind == "archive":
                values = (f"🗜 {f.name}", f"이미지 {n_img:,}장", core.human(size), "", "",
                          err or "대기 (압축 파일)")
            else:
                values = (f.name, "", core.human(size), "", "", "대기")
            iid = self.tree.insert("", "end", values=values, tags=("err",) if err else ())
            self.items[iid] = {"path": f, "kind": kind, "units": max(1, n_img), "key": key, "err": err}
        self._update_count()

    def _update_count(self):
        n_file = sum(1 for it in self.items.values() if it["kind"] == "file")
        arcs = [it for it in self.items.values() if it["kind"] == "archive"]
        msg = f"이미지 {n_file:,}개"
        if arcs:
            msg += f" + 압축 파일 {len(arcs)}개 (안에 이미지 {sum(it['units'] for it in arcs):,}장)"
        self.v_status.set(msg if self.items else "이미지나 압축 파일을 추가하세요.")

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(core.SUPPORTED_EXTS | core.ARCHIVE_EXTS))
        if IS_MAC:  # macOS 대화상자는 대문자 확장자를 따로 적어야 하는 경우가 있음
            exts += " " + exts.upper()
        paths = filedialog.askopenfilenames(
            title="이미지 또는 압축 파일 선택",
            filetypes=[("이미지·압축 파일", exts), ("모든 파일", "*")])
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        d = filedialog.askdirectory(title="폴더 선택 (하위 폴더 포함)")
        if d:
            self.add_paths([d])

    def _on_drop(self, event):
        self.add_paths(self.root.tk.splitlist(event.data))

    def remove_selected(self):
        if self.running or self.adding:
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            it = self.items.pop(iid, None)
            if it:
                self.known.discard(it["key"])
            self.results.pop(iid, None)
            self.crops.pop(iid, None)
        self._update_count()

    def clear(self):
        if self.running or self.adding:
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.known.clear()
        self.results.clear()
        self.crops.clear()
        self.pb["value"] = 0
        self._update_count()

    def choose_outdir(self):
        d = filedialog.askdirectory(title="저장할 폴더")
        if d:
            self.v_outdir.set(d)
            self.v_outmode.set("dir")
            self._update_out_widgets()

    def _on_double(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.items:
            return
        if self.items[iid]["kind"] == "file" and not self.running:
            self.open_crop_editor(iid)  # 사진: 개별 자르기
            return
        r = self.results.get(iid)  # 압축 파일: 결과 위치 열기
        if r and r.dst:
            open_in_file_manager(r.dst)
        else:
            open_in_file_manager(self.items[iid]["path"])

    def open_output(self):
        if self.v_outmode.get() == "dir" and self.v_outdir.get():
            open_in_file_manager(Path(self.v_outdir.get()))
            return
        for r in self.results.values():
            if r.dst:
                open_in_file_manager(r.dst)
                return
        if self.items:
            open_in_file_manager(next(iter(self.items.values()))["path"].parent)

    # ------------------------------------------------------------ 변환
    def _settings(self) -> core.Settings:
        def num(var, name, lo=1, hi=100000, cast=int):
            try:
                v = cast(str(var.get()).strip())
            except ValueError:
                raise ValueError(f"{name} 값을 숫자로 입력하세요.")
            if not lo <= v <= hi:
                raise ValueError(f"{name} 값은 {lo}~{hi} 사이여야 합니다.")
            return v

        k = self._resize_key()
        s = core.Settings(
            out_format=self.v_fmt.get(),
            mode=self.v_mode.get(),
            smart_level=dict(LEVELS).get(self.v_level.get(), "best"),
            quality=int(self.v_quality.get()),
            resize_mode=k,
            no_upscale=self.v_noup.get(),
            keep_metadata=self.v_meta.get(),
            overwrite=self.v_overwrite.get(),
        )
        if k == "long":
            s.long_side = num(self.v_long, "긴 변")
        if k in ("width", "fit"):
            s.width = num(self.v_w, "가로")
        if k in ("height", "fit"):
            s.height = num(self.v_h, "세로")
        if k == "percent":
            s.percent = num(self.v_pct, "비율", 1, 1000, float)
        if k == "cover":
            s.width, s.height = num(self.v_w, "가로"), num(self.v_h, "세로")
        self._apply_crop_settings(s, num)
        if self.v_outmode.get() == "dir":
            if not self.v_outdir.get():
                raise ValueError("저장할 폴더를 선택하세요.")
            s.output_dir = self.v_outdir.get()
        return s

    def _apply_crop_settings(self, s: core.Settings, num):
        c = self._crop_key()
        if c == "custom":
            c = (num(self.v_crop_w, "자르기 비율 가로", 1, 1000), num(self.v_crop_h, "자르기 비율 세로", 1, 1000))
        s.crop_ratio = c if self._resize_key() != "cover" else None
        s.crop_anchor = self.v_crop_anchor.get()
        s.crop_flip_portrait = self.v_crop_flip.get()

    def _crop_settings(self) -> core.Settings:
        """개별 자르기 창에서 '일괄 설정대로' 영역을 보여주기 위한 설정 (입력 오류가 있으면 자르기 없음)."""
        s = core.Settings(resize_mode=self._resize_key())

        def num(var, name, lo=1, hi=100000, cast=int):
            v = cast(str(var.get()).strip())
            if not lo <= v <= hi:
                raise ValueError(name)
            return v
        try:
            if s.resize_mode == "cover":
                s.width, s.height = num(self.v_w, "w"), num(self.v_h, "h")
            self._apply_crop_settings(s, num)
        except ValueError:
            s = core.Settings()
        return s

    # ------------------------------------------------------------ 개별 자르기
    def _file_rows(self) -> list:
        return [i for i in self.tree.get_children() if self.items.get(i, {}).get("kind") == "file"]

    def open_crop_editor(self, iid=None):
        if self.running or self.adding:
            return
        rows = self._file_rows()
        if not rows:
            messagebox.showinfo(APP_NAME, "자를 이미지를 먼저 추가하세요. (압축 파일 안의 이미지는 일괄 설정만 적용됩니다)")
            return
        if iid is None:
            sel = [i for i in self.tree.selection() if i in rows]
            iid = sel[0] if sel else rows[0]
        if iid not in rows:
            messagebox.showinfo(APP_NAME, "압축 파일 안의 이미지는 개별로 자를 수 없습니다. (일괄 설정은 적용됩니다)")
            return
        entries = [(i, self.items[i]["path"]) for i in rows]
        CropEditor(self.root, entries, rows.index(iid), self.crops.get, self._set_crop, self._crop_settings())

    def _set_crop(self, iid, value):
        if value is None:
            self.crops.pop(iid, None)
        else:
            self.crops[iid] = value
        self._refresh_row(iid)

    def _set_crop_selected(self, value):
        if self.running:
            return
        for iid in self.tree.selection():
            if self.items.get(iid, {}).get("kind") == "file":
                self._set_crop(iid, value)

    def _refresh_row(self, iid):
        if not self.tree.exists(iid) or iid not in self.items:
            return
        it = self.items[iid]
        st = self.crops.get(iid)
        self.tree.set(iid, "name", ("✂ " if isinstance(st, tuple) else "") + it["path"].name)
        if not self.running and iid not in self.results:
            self.tree.set(iid, "status", "대기" + (" · ✂ 직접 지정" if isinstance(st, tuple)
                                                  else " · 자르지 않음" if st == "none" else ""))

    def _popup(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu.tk_popup(e.x_root, e.y_root)

    def _open_selected(self, result: bool):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        r = self.results.get(iid)
        if result and r and r.dst:
            open_in_file_manager(r.dst)
        elif iid in self.items:
            open_in_file_manager(self.items[iid]["path"])

    def start(self):
        if self.running:
            self.cancel.set()
            self.bt_start.configure(text="취소 중…")
            return
        if self.adding:
            return
        jobs = [(iid, it) for iid, it in self.items.items() if not it["err"]]
        if not jobs:
            messagebox.showinfo(APP_NAME, "변환할 이미지나 압축 파일을 먼저 추가하세요.")
            return
        try:
            s = self._settings()
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        if not self._confirm_start(jobs, s):
            return
        self._save_config()

        self.running = True
        self.cancel.clear()
        self.bt_start.configure(text="■ 중지")
        self.results.clear()
        self.expected = {iid: it["units"] for iid, it in jobs}
        self.ticked = {iid: 0 for iid, _ in jobs}
        self.total_units = sum(self.expected.values())
        self.done_units = 0
        self.rows_left = len(jobs)
        self.sum_orig = self.sum_new = 0
        self.errors = 0
        self.cancelled_rows = 0
        self.t_start = time.time()
        self.pb.configure(maximum=self.total_units, value=0)
        for iid, it in jobs:
            self.tree.item(iid, tags=())
            for col in ("new", "saved"):
                self.tree.set(iid, col, "")
            self.tree.set(iid, "status", "대기")
        self.v_status.set(f"변환 중… 0 / {self.total_units:,}장")
        threading.Thread(target=self._run, args=(jobs, s, self.v_arc_folder.get(), dict(self.crops)),
                         daemon=True).start()

    # 백그라운드: 모든 CPU 코어로 처리 (Pillow 인코더는 GIL 을 풀기 때문에 스레드로 충분히 병렬화됨)
    def _run(self, jobs, s, arc_folder, crops):
        post = self.q.put
        workers = core.cpu_workers()
        core.set_big_image_limit(2)
        if s.out_format == "png":
            core.start_png_pool(workers)
        cpu = ThreadPoolExecutor(workers)
        pack = ThreadPoolExecutor(1)   # 압축 풀기·묶기는 디스크 작업이라 하나씩
        slots = threading.Semaphore(2)  # 동시에 풀어둘 압축 파일 수 (디스크 공간 보호)

        def file_job(iid, path):
            if self.cancel.is_set():
                post(("skip", iid, None))
                return
            post(("start", iid, None))
            post(("done", iid, core.convert(path, s, crop=crops.get(iid))))

        def arch_task(iid, job, i, st):
            r = None if self.cancel.is_set() else job.run_task(i)
            post(("tick", iid, None))
            with st["lock"]:
                st["results"][i] = r
                st["left"] -= 1
                last = st["left"] == 0
            if last:
                pack.submit(arch_finish, iid, job, st)

        def arch_finish(iid, job, st):
            try:
                post(("packing", iid, None))
                post(("done", iid, job.finish(st["results"], cancelled=self.cancel.is_set())))
            finally:
                slots.release()

        archives = []
        for iid, it in jobs:
            if it["kind"] == "file":
                cpu.submit(file_job, iid, it["path"])
            else:
                archives.append((iid, it["path"]))

        for iid, path in archives:
            while not slots.acquire(timeout=0.2):
                if self.cancel.is_set():
                    break
            else:
                if self.cancel.is_set():
                    slots.release()
                    post(("skip", iid, None))
                    continue
                post(("extract", iid, None))
                job = core.ArchiveJob(path, s, as_folder=arc_folder)
                try:
                    n = job.prepare(self.cancel)
                except Exception as e:  # noqa: BLE001
                    slots.release()
                    post(("done", iid, core.Result(src=path, error=str(e))))
                    continue
                post(("count", iid, n))
                st = {"lock": threading.Lock(), "results": [None] * n, "left": n}
                if n == 0:
                    pack.submit(arch_finish, iid, job, st)
                for i in range(n):
                    cpu.submit(arch_task, iid, job, i, st)
                continue
            post(("skip", iid, None))  # 취소로 시작하지 못한 압축 파일
        cpu.shutdown(wait=True)
        pack.shutdown(wait=True)

    def _poll(self):
        handled = 0
        try:
            while handled < 400:  # 한 번에 너무 많이 처리하면 화면이 끊기므로 나눠서
                kind, iid, r = self.q.get_nowait()
                handled += 1
                if kind == "add":
                    self._insert_rows(r)
                    continue
                if kind == "notice":
                    self.notice = r
                    continue
                if kind == "add_end":
                    self.adding = False
                    self._update_count()
                    if self.notice:
                        skipped, self.notice = self.notice, None
                        names = ", ".join(p.name for p in skipped[:5])
                        more = f" 외 {len(skipped) - 5}개" if len(skipped) > 5 else ""
                        messagebox.showwarning(
                            APP_NAME,
                            f"지원하지 않는 압축 파일 {len(skipped)}개는 목록에 추가하지 않았습니다.\n\n"
                            f"{names}{more}\n\nRAR/CBR 등은 ZIP으로 바꿔서 넣어주세요.")
                    continue
                if not self.tree.exists(iid):
                    continue
                if kind == "start":
                    self.tree.set(iid, "status", "처리 중…")
                elif kind == "extract":
                    self.tree.set(iid, "status", "압축 푸는 중…")
                elif kind == "count":
                    self._set_expected(iid, max(1, r))
                    self.tree.set(iid, "dim", f"이미지 {r:,}장")
                    self.tree.set(iid, "status", f"변환 중 0 / {r:,}")
                elif kind == "tick":
                    if self.ticked[iid] < self.expected[iid]:
                        self.ticked[iid] += 1
                        self._advance(1)
                    self.tree.set(iid, "status", f"변환 중 {self.ticked[iid]:,} / {self.expected[iid]:,}")
                elif kind == "packing":
                    self.tree.set(iid, "status", "압축 파일로 묶는 중…" if not self.v_arc_folder.get() else "정리 중…")
                elif kind in ("done", "skip"):
                    self._advance(self.expected[iid] - self.ticked[iid])
                    self.ticked[iid] = self.expected[iid]
                    if kind == "skip":
                        self.cancelled_rows += 1
                        self.tree.set(iid, "status", "취소됨")
                    else:
                        self._show_result(iid, r)
                    self.rows_left -= 1
                    if self.rows_left <= 0:
                        self._finish()
        except queue.Empty:
            pass
        if self.running and handled:
            self._update_progress_text()
        self.root.after(30 if handled >= 400 else 80, self._poll)

    def _set_expected(self, iid, n):
        diff = n - self.expected[iid]
        if diff:
            self.expected[iid] = n
            self.total_units += diff
            self.pb.configure(maximum=self.total_units)

    def _advance(self, n):
        self.done_units += n
        self.pb["value"] = self.done_units

    def _update_progress_text(self):
        el = time.time() - self.t_start
        msg = f"변환 중… {self.done_units:,} / {self.total_units:,}장"
        if self.done_units and el > 2:
            rate = self.done_units / el
            left = (self.total_units - self.done_units) / rate
            eta = f"{left / 60:.0f}분" if left >= 90 else f"{left:.0f}초"
            msg += f"  ·  {rate:.1f}장/초  ·  남은 시간 약 {eta}"
        self.v_status.set(msg)

    def _show_result(self, iid, r: core.Result):
        self.results[iid] = r
        if r.error == "취소됨":
            self.cancelled_rows += 1
            self.tree.set(iid, "status", "취소됨")
            return
        if r.error:
            self.errors += 1
            self.tree.set(iid, "status", "오류: " + r.error)
            self.tree.item(iid, tags=("err",))
            return
        self.sum_orig += r.orig_bytes
        self.sum_new += r.new_bytes
        if self.items[iid]["kind"] == "file":
            dim = f"{r.orig_size[0]}×{r.orig_size[1]}"
            if tuple(r.new_size) != tuple(r.orig_size):
                dim += f" → {r.new_size[0]}×{r.new_size[1]}"
            self.tree.set(iid, "dim", dim)
            status = f"완료 · {r.detail}"
        else:
            status = f"완료 · {r.detail} → {r.dst.name}"
        self.tree.set(iid, "new", core.human(r.new_bytes))
        ratio = r.saved_ratio
        self.tree.set(iid, "saved", f"-{ratio:.0%}" if ratio >= 0 else f"+{-ratio:.0%}")
        self.tree.set(iid, "status", status)
        warn = r.saved_ratio < 0 or "실패" in r.detail or core.UNREACHABLE_NOTE in r.detail
        self.tree.item(iid, tags=("warn",) if warn else ("done",))

    def _finish(self):
        self.running = False
        core.release_names()
        self.bt_start.configure(text="▶ 변환 시작")
        ok = sum(1 for r in self.results.values() if not r.error)
        el = time.time() - self.t_start
        if self.cancelled_rows:
            msg = f"중지됨: 완료 {ok:,}개, 취소 {self.cancelled_rows:,}개 ({el:.0f}초)"
        else:
            msg = f"완료: {ok:,}개 ({self.done_units:,}장, {el:.0f}초)"
        if self.sum_orig:
            msg += (f"  ·  {core.human(self.sum_orig)} → {core.human(self.sum_new)}"
                    f"  ({1 - self.sum_new / self.sum_orig:.0%} 절감)")
        grew = sum(1 for r in self.results.values() if not r.error and r.saved_ratio < 0)
        if grew:
            msg += f"  ·  용량이 늘어난 파일 {grew}개(주황색)"
        lowq = sum(1 for r in self.results.values() if not r.error and core.UNREACHABLE_NOTE in r.detail)
        if lowq:
            msg += f"  ·  화질이 떨어질 수 있는 파일 {lowq}개(주황색)"
        if self.errors:
            msg += f"  ·  오류 {self.errors}개"
        self.v_status.set(msg)

    def _on_close(self):
        self._save_config()
        self.cancel.set()
        core.stop_png_pool()
        self.root.destroy()


def main():
    if IS_WIN:
        try:  # 고해상도 모니터에서 흐릿하게 보이지 않도록
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    global DND
    root = None
    if DND:
        try:
            root = TkinterDnD.Tk()
        except Exception:
            # 드래그 앤 드롭 라이브러리를 못 불러오는 환경(예: 경로에 한글이 있고 시스템 언어 설정이 맞지 않을 때)
            # → 그 기능만 끄고 계속 실행. [파일 추가]·[폴더 추가] 버튼은 그대로 동작함.
            DND = False
            # 실패하면서 절반만 만들어진 창이 '기본 창'으로 남아 있으면 이후 설정값이 그 창에 연결돼서
            # 화면이 비어 보이므로 반드시 정리
            stale = getattr(tk, "_default_root", None)
            if stale is not None:
                try:
                    stale.destroy()
                except Exception:
                    pass
                tk._default_root = None
    if root is None:
        root = tk.Tk()
    style = ttk.Style(root)
    if IS_WIN and "vista" in style.theme_names():
        style.theme_use("vista")
    elif not IS_MAC and "clam" in style.theme_names():
        style.theme_use("clam")
    if IS_WIN:
        root.option_add("*Font", ("Malgun Gothic", 9))
        style.configure(".", font=("Malgun Gothic", 9))
    style.configure("Treeview", rowheight=24)
    threading.Thread(target=core.cleanup_stale_temp, daemon=True).start()
    app = App(root)
    args = [a for a in sys.argv[1:] if not a.startswith("-psn")]  # macOS 구버전 인자 제외
    if args:  # 실행 파일 아이콘 위로 파일을 끌어다 놓은 경우
        root.after(200, lambda: app.add_paths(args))
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # 실행 파일(PyInstaller)에서 PNG 병렬 처리용 프로세스가 GUI 를 다시 띄우지 않도록
    main()
