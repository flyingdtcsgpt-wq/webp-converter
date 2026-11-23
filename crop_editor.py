"""개별 자르기 창.

목록에서 사진을 더블클릭(또는 [✂ 개별 자르기])하면 열린다.
- 박스 안을 끌면 이동, 모서리를 끌면 크기 조절, 박스 밖을 끌면 새로 그리기 (선택한 비율 고정)
- ←/→ 또는 [이전]/[다음]으로 사진을 넘긴다. 넘길 때 바꾼 내용은 자동으로 저장된다.
- 자르기 영역은 '회전을 반영한 원본' 기준 비율 좌표 (x0, y0, x1, y1) 로 저장된다 (webp_core 와 같은 방식).

사진별 상태: None = 일괄 설정대로, "none" = 이 사진은 자르지 않음, (x0, y0, x1, y1) = 직접 지정
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from PIL import Image, ImageEnhance, ImageOps, ImageTk

import webp_core as core

RATIOS = [
    ("자유", None), ("원본 비율", "orig"), ("1:1", (1, 1)), ("4:3", (4, 3)), ("3:4", (3, 4)),
    ("3:2", (3, 2)), ("2:3", (2, 3)), ("16:9", (16, 9)), ("9:16", (9, 16)), ("4:5", (4, 5)), ("5:4", (5, 4)),
]
HANDLE = 10  # 모서리 잡는 범위 (px)
MIN_BOX = 8


def _ratio_label(r) -> str:
    for label, v in RATIOS:
        if v == r:
            return label
    return f"{r[0]}:{r[1]}" if isinstance(r, tuple) else "자유"


class CropEditor(tk.Toplevel):
    def __init__(self, master, entries: list, index: int, get_state: Callable, set_state: Callable,
                 settings: Optional[core.Settings]):
        super().__init__(master)
        self.entries, self.index = entries, index
        self.get_state, self.set_state = get_state, set_state
        self.settings = settings or core.Settings()
        self.title("개별 자르기")
        self.transient(master)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.cw, self.ch = max(480, min(1000, sw - 160)), max(320, min(660, sh - 280))

        g = self.settings.crop_ratio
        self.v_ratio = tk.StringVar(value=_ratio_label(g) if g else "자유")
        self.ratio_flip_auto = bool(g) and self.settings.crop_flip_portrait
        self.v_info = tk.StringVar()
        self.v_state = tk.StringVar()

        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="비율:").pack(side="left")
        cb = ttk.Combobox(top, values=[l for l, _ in RATIOS], textvariable=self.v_ratio,
                          state="readonly", width=10)
        cb.pack(side="left", padx=(4, 4))
        cb.bind("<<ComboboxSelected>>", lambda e: self._ratio_changed())
        ttk.Button(top, text="↔ 가로/세로 바꾸기", command=self._swap_ratio).pack(side="left")
        ttk.Label(top, textvariable=self.v_state, foreground="#1d4ed8").pack(side="right")

        self.cv = tk.Canvas(self, width=self.cw, height=self.ch, background="#2b2b2b",
                            highlightthickness=0, cursor="crosshair")
        self.cv.pack(padx=10, pady=4)
        ttk.Label(self, textvariable=self.v_info, foreground="#444").pack(fill="x", padx=12)
        ttk.Label(self, foreground="#888",
                  text="박스 안을 끌면 이동 · 모서리를 끌면 크기 조절 · 박스 밖을 끌면 새로 그리기 · "
                       "←/→ 다음 사진 (바꾼 내용은 자동 저장)").pack(fill="x", padx=12)

        bot = ttk.Frame(self, padding=(10, 6, 10, 10))
        bot.pack(fill="x")
        ttk.Button(bot, text="◀ 이전", command=lambda: self._go(-1)).pack(side="left")
        ttk.Button(bot, text="다음 ▶", command=lambda: self._go(1)).pack(side="left", padx=(4, 16))
        ttk.Button(bot, text="자르지 않기", command=self._set_none).pack(side="left")
        ttk.Button(bot, text="일괄 설정대로", command=self._set_auto).pack(side="left", padx=4)
        ttk.Button(bot, text="적용하고 닫기", command=self._apply_close).pack(side="right")
        ttk.Button(bot, text="취소", command=self.destroy).pack(side="right", padx=4)

        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Motion>", self._hover)
        self.bind("<Left>", lambda e: self._go(-1))
        self.bind("<Right>", lambda e: self._go(1))
        self.bind("<Return>", lambda e: self._apply_close())
        self.bind("<Escape>", lambda e: self.destroy())

        self._load()
        self.focus_set()

    # ------------------------------------------------------------ 이미지 불러오기
    def _load(self):
        iid, path = self.entries[self.index]
        self.dirty = False
        try:
            im = Image.open(path)
            self.full_size = im.size
            try:
                if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                    self.full_size = (im.size[1], im.size[0])
            except Exception:
                pass
            if im.format in ("JPEG", "MPO"):
                im.draft("RGB", (self.cw * 2, self.ch * 2))  # 미리보기는 작게 디코딩 (빠름)
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (200, 200, 200, 255))
                im = Image.alpha_composite(bg, im)
            im = im.convert("RGB")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("개별 자르기", f"이미지를 열 수 없습니다.\n{path.name}\n{e}", parent=self)
            self.destroy()
            return
        im.thumbnail((self.cw, self.ch), Image.LANCZOS)
        self.img = im
        self.tw, self.th = im.size
        self.ox, self.oy = (self.cw - self.tw) // 2, (self.ch - self.th) // 2
        self.photo = ImageTk.PhotoImage(ImageEnhance.Brightness(im).enhance(0.35))  # 잘려 나갈 부분(어둡게)
        self.cv.delete("all")
        self.cv.create_image(self.ox, self.oy, image=self.photo, anchor="nw")
        self.bright_item = self.cv.create_image(0, 0, anchor="nw")
        self.bright = None
        self._apply_flip_for_image()
        st = self.get_state(iid)
        if isinstance(st, tuple):
            box = st
        elif st == "none":
            box = (0, 0, 1, 1)
        else:
            box = core.auto_crop_box(im, self.settings) or (0, 0, 1, 1)
        self.box = (self.ox + box[0] * self.tw, self.oy + box[1] * self.th,
                    self.ox + box[2] * self.tw, self.oy + box[3] * self.th)
        self.title(f"개별 자르기 - {path.name}  ({self.index + 1}/{len(self.entries)})")
        self._redraw()

    def _apply_flip_for_image(self):
        """일괄 설정에서 '사진 방향에 맞춰 비율 뒤집기'를 켰다면, 사진을 넘길 때 비율도 맞춰 준다."""
        r = self._ratio()
        if not (self.ratio_flip_auto and isinstance(r, tuple)):
            return
        w, h = self.full_size
        if (r[0] - r[1]) * (w - h) < 0:
            self.v_ratio.set(_ratio_label((r[1], r[0])))

    # ------------------------------------------------------------ 비율
    def _ratio(self):
        return dict(RATIOS).get(self.v_ratio.get())

    def _ratio_value(self) -> Optional[float]:
        r = self._ratio()
        if r == "orig":
            return self.full_size[0] / self.full_size[1]
        return r[0] / r[1] if isinstance(r, tuple) else None

    def _ratio_changed(self):
        self.ratio_flip_auto = False  # 직접 고르면 그 비율을 그대로 유지
        R = self._ratio_value()
        if R is None:
            return
        x0, y0, x1, y1 = self.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        w = min(self.tw, self.th * R)  # 그 비율로 들어가는 가장 큰 박스를, 지금 박스 중심에
        h = w / R
        x0 = min(max(cx - w / 2, self.ox), self.ox + self.tw - w)
        y0 = min(max(cy - h / 2, self.oy), self.oy + self.th - h)
        self.box = (x0, y0, x0 + w, y0 + h)
        self.dirty = True
        self._redraw()

    def _swap_ratio(self):
        r = self._ratio()
        if isinstance(r, tuple) and r[0] != r[1]:
            self.v_ratio.set(_ratio_label((r[1], r[0])))
            self._ratio_changed()

    # ------------------------------------------------------------ 그리기
    def _redraw(self):
        x0, y0, x1, y1 = self.box
        cv = self.cv
        cv.delete("ov")
        ix0, iy0 = int(round(x0 - self.ox)), int(round(y0 - self.oy))
        ix1, iy1 = int(round(x1 - self.ox)), int(round(y1 - self.oy))
        if ix1 > ix0 and iy1 > iy0:
            self.bright = ImageTk.PhotoImage(self.img.crop((ix0, iy0, ix1, iy1)))
            cv.itemconfigure(self.bright_item, image=self.bright)
            cv.coords(self.bright_item, self.ox + ix0, self.oy + iy0)
        for i in (1, 2):  # 3분할선
            xs, ys = x0 + (x1 - x0) * i / 3, y0 + (y1 - y0) * i / 3
            cv.create_line(xs, y0, xs, y1, fill="#ffffff", width=1, dash=(3, 4), tags="ov")
            cv.create_line(x0, ys, x1, ys, fill="#ffffff", width=1, dash=(3, 4), tags="ov")
        cv.create_rectangle(x0, y0, x1, y1, outline="#000000", width=3, tags="ov")
        cv.create_rectangle(x0, y0, x1, y1, outline="#ffffff", width=1, tags="ov")
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            cv.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="#ffffff", outline="#1d4ed8", tags="ov")
        self._update_info()

    def _norm_box(self):
        x0, y0, x1, y1 = self.box
        b = ((x0 - self.ox) / self.tw, (y0 - self.oy) / self.th, (x1 - self.ox) / self.tw, (y1 - self.oy) / self.th)
        return tuple(min(1.0, max(0.0, v)) for v in b)

    def _update_info(self):
        iid, _ = self.entries[self.index]
        b = self._norm_box()
        W, H = self.full_size
        cw, ch = round(W * (b[2] - b[0])), round(H * (b[3] - b[1]))
        whole = cw >= W - 1 and ch >= H - 1
        self.v_info.set(f"원본 {W}×{H}px  →  " + ("자르지 않음" if whole else f"자를 영역 {cw}×{ch}px"))
        st = self.get_state(iid)
        if self.dirty:
            self.v_state.set("● 바뀜 (저장 안 됨)")
        elif isinstance(st, tuple):
            self.v_state.set("✂ 직접 지정됨")
        elif st == "none":
            self.v_state.set("이 사진은 자르지 않음")
        else:
            self.v_state.set("일괄 설정대로")

    # ------------------------------------------------------------ 마우스
    def _clamp(self, x, y):
        return (min(max(x, self.ox), self.ox + self.tw), min(max(y, self.oy), self.oy + self.th))

    def _corner_at(self, x, y):
        x0, y0, x1, y1 = self.box
        for cx, cy, ax, ay in ((x0, y0, x1, y1), (x1, y0, x0, y1), (x0, y1, x1, y0), (x1, y1, x0, y0)):
            if abs(x - cx) <= HANDLE and abs(y - cy) <= HANDLE:
                return ax, ay  # 반대쪽 모서리를 고정점으로
        return None

    def _hover(self, e):
        x0, y0, x1, y1 = self.box
        if self._corner_at(e.x, e.y):
            self.cv.configure(cursor="sizing")
        elif x0 < e.x < x1 and y0 < e.y < y1:
            self.cv.configure(cursor="fleur")
        else:
            self.cv.configure(cursor="crosshair")

    def _press(self, e):
        anchor = self._corner_at(e.x, e.y)
        x0, y0, x1, y1 = self.box
        if anchor:
            self.mode, self.anchor = "resize", anchor
        elif x0 < e.x < x1 and y0 < e.y < y1:
            self.mode, self.grab = "move", (e.x - x0, e.y - y0)
        else:
            self.mode, self.anchor = "resize", self._clamp(e.x, e.y)

    def _drag(self, e):
        if getattr(self, "mode", None) == "move":
            x0, y0, x1, y1 = self.box
            w, h = x1 - x0, y1 - y0
            nx = min(max(e.x - self.grab[0], self.ox), self.ox + self.tw - w)
            ny = min(max(e.y - self.grab[1], self.oy), self.oy + self.th - h)
            self.box = (nx, ny, nx + w, ny + h)
        elif getattr(self, "mode", None) == "resize":
            ax, ay = self.anchor
            px, py = self._clamp(e.x, e.y)
            dx, dy = px - ax, py - ay
            sx, sy = (1 if dx >= 0 else -1), (1 if dy >= 0 else -1)
            maxw = (self.ox + self.tw - ax) if sx > 0 else (ax - self.ox)
            maxh = (self.oy + self.th - ay) if sy > 0 else (ay - self.oy)
            w, h = abs(dx), abs(dy)
            R = self._ratio_value()
            if R:
                w = max(w, h * R)
                h = w / R
                if w > maxw:
                    w, h = maxw, maxw / R
                if h > maxh:
                    h, w = maxh, maxh * R
            w, h = max(w, MIN_BOX), max(h, MIN_BOX)
            bx0, bx1 = sorted((ax, ax + sx * w))
            by0, by1 = sorted((ay, ay + sy * h))
            self.box = (bx0, by0, bx1, by1)
        else:
            return
        self.dirty = True
        self._redraw()

    def _release(self, e):
        self.mode = None

    # ------------------------------------------------------------ 저장·이동
    def _save_current(self):
        if not self.dirty:
            return
        iid, _ = self.entries[self.index]
        b = self._norm_box()
        whole = b[0] <= 0.002 and b[1] <= 0.002 and b[2] >= 0.998 and b[3] >= 0.998
        self.set_state(iid, "none" if whole else b)
        self.dirty = False

    def _go(self, step):
        n = self.index + step
        if not 0 <= n < len(self.entries):
            return
        self._save_current()
        self.index = n
        self._load()

    def _set_none(self):
        iid, _ = self.entries[self.index]
        self.set_state(iid, "none")
        self._load()

    def _set_auto(self):
        iid, _ = self.entries[self.index]
        self.set_state(iid, None)
        self._load()

    def _apply_close(self):
        self._save_current()
        self.destroy()
