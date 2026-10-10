# -*- coding: utf-8 -*-
"""icon_task 图标「框选 + 类别」标注工具（Tkinter，无需额外安装）。

参考 `common/annotate_gui.py` 的风格：自适应缩放 / 原子写入 + .bak / 快捷键。

标注对象：`datasets/icon_task/raw/*_scene_imageData.jpg` 里的**所有图标**。
顶部显示本题（来自 `labels/questions.json`，如「两个裙子」= 目标 2 个），方便核对。
结果写到 `datasets/icon_task/labels/boxes.csv`：`image,x1,y1,x2,y2,cls`。

主要功能
--------
- **按标注状态筛选**：全部 / 未标 / 部分（有框没类别或目标数量对不上）/ 已标；
- **按题目搜索跳转**：输入中文或拼音（如 `pch`/瓢虫）筛选出"题目要找它"的图片，←/→ 逐张标；
- 类别表**按拼音排序，右侧显示已标数量**；搜索框可中文/拼音筛选类别；
- 打开图**默认不出框**；按 `A` 才出框（带 CNN 类别提示，且不会叠重复框）；
- 单击框 = 应用当前类别；拖动 = 移动；拖角/边 = 缩放；右键 = 删框；
- 滚轮缩放 / 中键平移 / `R` 复位；`PgUp/PgDn` 也可翻页。

用法（项目根目录）：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\annotate.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

UI = "Microsoft YaHei"
PALETTE = ["#e03131", "#2f9e44", "#1971c2", "#f08c00", "#9c36b5", "#0c8599",
           "#e8590c", "#5c940d", "#c2255c", "#1098ad", "#7048e8", "#2b8a3e",
           "#d9480f", "#1864ab", "#a61e4d", "#495057"]

# 类别拼音（无空格，用于“英文字母”排序 + 拼音模糊搜索）
PINYIN: dict[str, str] = {
    "裙子": "qunzi", "树袋熊": "shudaixiong", "包": "bao", "冰激凌": "bingjiling",
    "杯子": "beizi", "太阳花": "taiyanghua", "轮胎": "luntai", "马": "ma",
    "帐篷": "zhangpeng", "树": "shu", "宇航员": "yuhangyuan", "帽子": "maozi",
    "鸭子": "yazi", "鱼": "yu", "挂锁": "guasuo", "篮子": "lanzi",
    "乌龟": "wugui", "草莓": "caomei", "南瓜": "nangua", "蘑菇": "mogu",
    "熊猫": "xiongmao", "苹果": "pingguo", "房子": "fangzi", "伞": "san",
    "鞋": "xie", "兔子": "tuzi", "闹钟": "naozhong", "凤梨": "fengli",
    "皇冠": "huangguan", "瓶子": "pingzi", "瓢虫": "piaochong",
    "松鼠": "songshu", "车": "che", "鸟": "niao", "气球": "qiqiu",
    "足球": "zuqiu", "蛋糕": "dangao", "耳机": "erji", "玫瑰花": "meiguihua",
    "铲土机": "chantuji", "蝴蝶": "hudie", "吉他": "jita", "汉堡包": "hanbaobao",
    "枕头": "zhentou", "大象": "daxiang", "花盆": "huapen",
    "企鹅": "qie", "椅子": "yizi",
}

BOXES_CSV = cfg.LABELS_DIR / "boxes.csv"
QUESTIONS_JSON = cfg.LABELS_DIR / "questions.json"
CUSTOM_FILE = cfg.LABELS_DIR / "custom_classes.txt"
PREFILL_FILE = cfg.LABELS_DIR / "_prefilled.txt"    # 自动预标过、还没标记已核的图

MIN_SIDE = 10          # 原图坐标下最小边长
HIT_HANDLE = 8         # 把手命中半径（显示像素）
MOVE_MARGIN = 7        # 选中框外的移动容错（显示像素）
DUP_IOU = 0.4          # 新框与已有框重叠超过此 IoU → 判为误操作
PREVIEW = 110          # 选中框放大预览边长


def load_boxes(path: Path) -> dict[str, list[list]]:
    out: dict[str, list[list]] = {}
    if not path.exists():
        return out
    for r in csv.DictReader(path.open(encoding="utf-8")):
        try:
            out.setdefault(r["image"], []).append(
                [int(float(r["x1"])), int(float(r["y1"])),
                 int(float(r["x2"])), int(float(r["y2"])), r.get("cls", "") or ""])
        except Exception:  # noqa: BLE001
            continue
    return out


class Annotator:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("icon_task 图标标注")
        # 按屏幕自适应：不超出屏幕，底部导航始终可见
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        W, H = min(1320, sw - 60), min(940, sh - 110)
        root.geometry(f"{W}x{H}+{max(0, (sw - W) // 2)}+{max(0, (sh - H) // 3)}")
        root.minsize(1020, 600)

        self.files_all = sorted(cfg.RAW_DIR.glob(f"*{cfg.IMAGE_SUFFIX}"))
        if not self.files_all:
            raise SystemExit(f"{cfg.RAW_DIR} 下没有 *{cfg.IMAGE_SUFFIX}")
        self.files = list(self.files_all)

        self.questions: dict[str, dict] = {}
        if QUESTIONS_JSON.exists():
            self.questions = json.loads(QUESTIONS_JSON.read_text(encoding="utf-8"))

        self.boxes = load_boxes(BOXES_CSV)
        self.prefilled = self._load_prefilled()
        self._customs = self._load_customs()
        self.classes = self._sorted_names()
        self._filtered = list(range(len(self.classes)))
        self._last_counts: Counter = Counter()
        self.cur_class = 0
        self.sel = None
        self.i = 0

        # 视图
        self.fit_scale = 1.0
        self.zoom = 1.0
        self.off = [0.0, 0.0]
        self.tkimg = None
        self._img_cache: dict[str, np.ndarray] = {}
        self._panning = False
        self._pan_last = (0, 0)

        # 交互
        self.mode = None
        self._op_pushed = False
        self.anchor = None
        self.resize_handle = None
        self._press_xy = None
        self.undo_stack: list = []
        self._mute_list = False
        self._cnn = "unset"

        self._style()
        self._build_ui()
        self.update_model_label()
        self.apply_filters(keep=True)
        root.bind("<Configure>", lambda e: self._on_resize(e))
        root.protocol("WM_DELETE_WINDOW", self.quit)

    # ==================== 样式 ====================
    def _style(self) -> None:
        self.root.configure(bg="#f5f6f8")
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=(UI, 10), background="#f5f6f8")
        st.configure("TFrame", background="#f5f6f8")
        st.configure("TLabel", background="#f5f6f8")
        st.configure("TLabelframe", background="#f5f6f8")
        st.configure("TLabelframe.Label", background="#f5f6f8", foreground="#3b5bdb",
                     font=(UI, 10, "bold"))
        st.configure("TButton", font=(UI, 10), padding=(6, 4))
        st.configure("Accent.TButton", font=(UI, 10, "bold"))
        st.configure("Mode.TRadiobutton", font=(UI, 10))
        st.configure("Q.TLabel", font=(UI, 15, "bold"), foreground="#c92a2a")
        st.configure("T.TLabel", font=(UI, 11, "bold"))
        st.configure("Hint.TLabel", foreground="#868e96", font=(UI, 9))

    # ==================== UI ====================
    def _build_ui(self) -> None:
        # 顶部信息
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(8, 2))
        self.title = ttk.Label(top, style="T.TLabel")
        self.title.pack(side="left")
        self.q_lbl = ttk.Label(top, style="Q.TLabel")
        self.q_lbl.pack(side="left", padx=14)
        self.cnt_lbl = ttk.Label(top, style="T.TLabel", foreground="#1864ab")
        self.cnt_lbl.pack(side="left", padx=8)
        self.prog = ttk.Progressbar(top, length=180, mode="determinate")
        self.prog.pack(side="right")

        body = ttk.Frame(self.root)   # 先不 pack，等底部导航占好位再 pack（保证导航不被挤出去）
        self.canvas = tk.Canvas(body, bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Button-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self.on_wheel)

        # 侧栏放进可滚动 Canvas（窗口再矮也能滚到所有按钮）
        side_outer = ttk.Frame(body, width=290)
        side_outer.pack(side="right", fill="y", padx=(8, 0))
        side_outer.pack_propagate(False)
        sc = tk.Canvas(side_outer, highlightthickness=0, bg="#f5f6f8", width=272)
        vsb = ttk.Scrollbar(side_outer, orient="vertical", command=sc.yview)
        sc.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        sc.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(sc)
        _win = sc.create_window((0, 0), window=side, anchor="nw")
        side.bind("<Configure>", lambda e: sc.configure(scrollregion=sc.bbox("all")))
        sc.bind("<Configure>", lambda e: sc.itemconfigure(_win, width=e.width))

        def _side_wheel(ev):
            sc.yview_scroll(int(-ev.delta / 120), "units")
            return "break"
        sc.bind("<MouseWheel>", _side_wheel)
        side.bind("<MouseWheel>", _side_wheel)

        # ---- ① 筛选 ----
        flt = ttk.Labelframe(side, text=" ① 筛选图片 ")
        flt.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(flt)
        row.pack(fill="x", padx=6, pady=(4, 2))
        self.mode_var = tk.StringVar(value="all")
        for txt, val in (("全部", "all"), ("未标", "none"), ("部分", "part"), ("已标", "done"), ("预标待核", "prefilled")):
            ttk.Radiobutton(row, text=txt, value=val, variable=self.mode_var,
                            style="Mode.TRadiobutton", command=self.apply_filters
                            ).pack(side="left", padx=1)
        self.mode_cnt = ttk.Label(flt, foreground="#1864ab", font=(UI, 9), wraplength=250,
                                  justify="left")
        self.mode_cnt.pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(flt, text="题目搜索（中文/拼音，跳转到含该目标的图）", font=(UI, 9)).pack(anchor="w", padx=8)
        self.qsearch_var = tk.StringVar()
        qs = ttk.Entry(flt, textvariable=self.qsearch_var)
        self.q_entry = qs
        qs.pack(fill="x", padx=8, pady=(0, 6))
        qs.bind("<KeyRelease>", lambda e: self.apply_filters())
        qs.bind("<Escape>", lambda e: (self.qsearch_var.set(""), self.apply_filters(), "break")[2])

        # ---- ② 类别 ----
        cbox = ttk.Labelframe(side, text=" ② 类别（拼音序，含已标数量） ")
        cbox.pack(fill="x")
        self.csearch_var = tk.StringVar()
        cs = ttk.Entry(cbox, textvariable=self.csearch_var)
        self.c_entry = cs
        cs.pack(fill="x", padx=8, pady=(6, 2))
        cs.bind("<KeyRelease>", lambda e: self._refresh_class_list(self.csearch_var.get()))
        cs.bind("<Return>", lambda e: (self._search_enter(), "break")[1])
        cs.bind("<Escape>", lambda e: (self.csearch_var.set(""), self._refresh_class_list(""), "break")[2])

        lrow = ttk.Frame(cbox)
        lrow.pack(fill="x", padx=8, pady=2)
        self.lb = tk.Listbox(lrow, height=11, font=("Consolas", 10), exportselection=False,
                             activestyle="dotbox", highlightthickness=1)
        self.lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lrow, orient="vertical", command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", self.on_class_pick)

        num = ttk.Frame(cbox)
        num.pack(fill="x", padx=8, pady=4)
        ttk.Label(num, text="编号").pack(side="left")
        self.num_var = tk.StringVar()
        self.num_entry = ttk.Entry(num, textvariable=self.num_var, width=5, justify="center",
                                   font=("Consolas", 12, "bold"))
        self.num_entry.pack(side="left", padx=4)
        self.num_entry.bind("<Return>", self._apply_num)
        self.num_entry.bind("<Key-space>", self._apply_num)
        ttk.Button(num, text="＋加类", width=6, command=self._add_from_search).pack(side="left", padx=1)
        ttk.Button(num, text="删自定义", width=7, command=self.remove_custom).pack(side="left")

        # ---- ③ 操作 ----
        act = ttk.Labelframe(side, text=" ③ 自动预标 / 操作 ")
        act.pack(fill="x", pady=6)
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(act, text="打开空图时自动出框+CNN预标", variable=self.auto_var).pack(anchor="w", padx=8, pady=(4, 2))
        g = ttk.Frame(act)
        g.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(g, text="⚡ 出框+题目纠正预标 (A)", command=self.autofill).grid(row=0, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(g, text="只出框(不填类)", command=lambda: self.autofill(with_class=False)).grid(row=1, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(g, text="撤销 (Ctrl+Z)", command=self.undo).grid(row=2, column=0, sticky="ew", padx=1, pady=2)
        ttk.Button(g, text="删框 (Del)", command=self.del_selected).grid(row=2, column=1, sticky="ew", padx=1, pady=2)
        ttk.Button(g, text="清空本图 (C)", command=self.clear_cur).grid(row=3, column=0, sticky="ew", padx=1, pady=2)
        ttk.Button(g, text="复位视图 (R)", command=self.reset_view).grid(row=3, column=1, sticky="ew", padx=1, pady=2)
        ttk.Button(g, text="↻ 重载 CNN 模型", command=self.reload_cnn).grid(row=4, column=0, sticky="ew", padx=1, pady=2)
        ttk.Button(g, text="✅ 标记已核 (V)", command=self.mark_reviewed).grid(row=4, column=1, sticky="ew", padx=1, pady=2)
        g.columnconfigure(0, weight=1); g.columnconfigure(1, weight=1)

        # ---- ④ 预览 / 模型信息 ----
        pv = ttk.Labelframe(side, text=" ④ 选中框预览 / 模型 ")
        pv.pack(fill="x", pady=(0, 6))
        self.preview = ttk.Label(pv)
        self.preview.pack(pady=4)
        self.model_lbl = ttk.Label(pv, text="", foreground="#2b8a3e", font=(UI, 9), wraplength=250,
                                   justify="left")
        self.model_lbl.pack(anchor="w", padx=8, pady=(0, 6))


        # ---- 底部导航（先占位，保证任何屏幕都看得到）----
        bottom = ttk.Frame(self.root)
        nav = ttk.Frame(bottom)
        nav.pack(fill="x", padx=10, pady=(2, 4))
        ttk.Button(nav, text="◀ 上一张", command=lambda: self.go(-1)).pack(side="left")
        ttk.Button(nav, text="保存并下一张 (Enter)", style="Accent.TButton",
                   command=lambda: self.save_and(1)).pack(side="left", padx=6)
        ttk.Button(nav, text="下一张 ▶", command=lambda: self.go(1)).pack(side="left")
        ttk.Label(nav, text="   跳到第").pack(side="left")
        self.jump_var = tk.StringVar()
        j = ttk.Entry(nav, textvariable=self.jump_var, width=6, justify="center")
        j.pack(side="left", padx=3)
        j.bind("<Return>", lambda ev: (self.do_jump(), "break")[1])
        self.pos_lbl = ttk.Label(nav, foreground="#1864ab")
        self.pos_lbl.pack(side="left", padx=10)
        self.status = ttk.Label(bottom, foreground="#1864ab")
        self.status.pack(fill="x", padx=12)
        ttk.Label(bottom, style="Hint.TLabel",
                  text="选类别→单击框=套用 ｜ 拖=移动 ｜ 拖角/边=缩放 ｜ 方向键(Shift)=微移1(10)px ｜ "
                       "数字键=编号,回车应用 ｜ 右键=删框 ｜ 滚轮缩放/中键平移/R复位 ｜ A 出框 ｜ "
                       "C 清空 ｜ Ctrl+Z 撤销 ｜ Enter 保存并下一张 ｜ ←/→ PgUp/PgDn 翻页 ｜ / 题目搜索").pack(
            fill="x", padx=12, pady=(0, 6))
        bottom.pack(side="bottom", fill="x")
        body.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        # ---- 键位 ----
        def g_(fn):
            return lambda e: (None if self._typing() else fn(e))

        self.root.bind("<Left>", g_(lambda e: self.go(-1)))
        self.root.bind("<Right>", g_(lambda e: self.go(1)))
        self.root.bind("<Prior>", g_(lambda e: self.go(-1)))
        self.root.bind("<Next>", g_(lambda e: self.go(1)))
        self.root.bind("<Up>", g_(lambda e: self.nudge(0, -1, e)))
        self.root.bind("<Down>", g_(lambda e: self.nudge(0, 1, e)))
        self.root.bind("<Return>", lambda e: self.save_and(1))
        self.root.bind("<Control-z>", lambda e: self.undo())
        self.root.bind("<Control-Z>", lambda e: self.undo())
        self.root.bind("<KeyPress-s>", g_(lambda e: self.save_and(1)))
        self.root.bind("<KeyPress-S>", g_(lambda e: self.save_and(1)))
        self.root.bind("<KeyPress-c>", g_(lambda e: self.clear_cur()))
        self.root.bind("<KeyPress-C>", g_(lambda e: self.clear_cur()))
        self.root.bind("<KeyPress-a>", g_(lambda e: self.autofill()))
        self.root.bind("<KeyPress-A>", g_(lambda e: self.autofill()))
        self.root.bind("<KeyPress-r>", g_(lambda e: self.reset_view()))
        self.root.bind("<KeyPress-R>", g_(lambda e: self.reset_view()))
        self.root.bind("<KeyPress-v>", g_(lambda e: self.mark_reviewed()))
        self.root.bind("<KeyPress-V>", g_(lambda e: self.mark_reviewed()))
        self.root.bind("<Delete>", g_(lambda e: self.del_selected()))
        self.root.bind("<BackSpace>", g_(lambda e: self.del_selected()))
        self.root.bind("<slash>", lambda e: (self.q_entry.focus_set(), "break")[1])
        self.root.bind("<Control-f>", lambda e: (self.c_entry.focus_set(), "break")[1])
        for k in "0123456789":
            self.root.bind(f"<KeyPress-{k}>", self.on_digit)
        self.root.bind("<Escape>", lambda e: self.quit())

    def _typing(self) -> bool:
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, ttk.Entry))

    # ==================== 类别 / 统计 ====================
    def _load_prefilled(self) -> set[str]:
        if PREFILL_FILE.exists():
            return {l.strip() for l in PREFILL_FILE.read_text(encoding="utf-8").splitlines() if l.strip()}
        return set()

    def _save_prefilled(self) -> None:
        PREFILL_FILE.write_text("\n".join(sorted(self.prefilled)), encoding="utf-8")

    def mark_reviewed(self) -> None:
        """把当前图标为“已人工核对”（从预标待核里移除）。"""
        if not self.files:
            return
        self.prefilled.discard(self.key()); self._save_prefilled()
        self.status.config(text=f"已标记已核：{self.key()}")
        if self.mode_var.get() == "prefilled":
            self.apply_filters()
        else:
            self.save()

    def _load_customs(self) -> set[str]:
        if CUSTOM_FILE.exists():
            return {l.strip() for l in CUSTOM_FILE.read_text(encoding="utf-8").splitlines() if l.strip()}
        return set()

    def _sorted_names(self) -> list[str]:
        base = [c for c in cfg.CLASSES if c not in self._customs]
        return sorted(base, key=lambda c: PINYIN.get(c, c)) + sorted(self._customs)

    def _save_customs(self) -> None:
        CUSTOM_FILE.parent.mkdir(parents=True, exist_ok=True)
        CUSTOM_FILE.write_text("\n".join(sorted(self._customs)), encoding="utf-8")

    def _counts(self) -> Counter:
        c: Counter = Counter()
        for boxes in self.boxes.values():
            for b in boxes:
                if b[4]:
                    c[b[4]] += 1
        return c

    def _match(self, name: str, q: str) -> bool:
        py = PINYIN.get(name, "")
        if q in name or q in py:
            return True
        it = iter(py)
        return all(ch in it for ch in q)

    def _refresh_class_list(self, query: str = "") -> None:
        yv = self.lb.yview()
        cnt = self._counts()
        self._last_counts = cnt
        q = (query or "").strip().lower()
        self._filtered = [i for i, name in enumerate(self.classes) if not q or self._match(name, q)]
        self.lb.delete(0, "end")
        for i in self._filtered:
            n = cnt.get(self.classes[i], 0)
            self.lb.insert("end", f"{i:>2}  {self.classes[i]}  ({n})")
        self._highlight_current()
        if yv:
            self.lb.yview_moveto(yv[0])

    def _highlight_current(self) -> None:
        pos = self._filtered.index(self.cur_class) if self.cur_class in self._filtered else None
        self._mute_list = True
        try:
            self.lb.selection_clear(0, "end")
            if pos is not None:
                self.lb.selection_set(pos); self.lb.see(pos)
        finally:
            self._mute_list = False

    def _search_enter(self) -> None:
        if self._filtered:
            self.set_class(self._filtered[0])
        else:
            self.add_class(self.csearch_var.get())

    def on_class_pick(self, _e=None) -> None:
        if self._mute_list:
            return
        sel = self.lb.curselection()
        if sel and self._filtered:
            self.set_class(self._filtered[sel[0]])

    def set_class(self, idx: int) -> None:
        if not (0 <= idx < len(self.classes)):
            return
        self.cur_class = idx
        self._highlight_current()
        if self.sel is not None and self.sel < len(self.cur_boxes()):
            self.push_undo()
            self.cur_boxes()[self.sel][4] = self.classes[idx]
            self.status.config(text=f"选中框 → {self.classes[idx]}")
            self.save()
        else:
            self.status.config(text=f"当前类别 = {self.classes[idx]}")
        self.redraw()

    def _apply_num(self, _e=None) -> str:
        s = self.num_var.get().strip(); self.num_var.set("")
        if s.isdigit():
            idx = int(s)
            if 0 <= idx < len(self.classes):
                self.set_class(idx)
            else:
                self.status.config(text=f"编号超范围 0~{len(self.classes) - 1}")
        self.root.focus_set()
        return "break"

    def on_digit(self, e) -> None:
        if self._typing():
            return
        self.num_var.set(self.num_var.get() + e.char)
        self.num_entry.focus_set(); self.num_entry.icursor("end")

    def _add_from_search(self) -> None:
        self.add_class(self.csearch_var.get())

    def add_class(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            self.status.config(text="先在搜索框输入新类别名再点加类")
            return
        if name in self.classes:
            self.set_class(self.classes.index(name)); return
        self._customs.add(name); self._save_customs()
        self.classes = self._sorted_names()
        self.csearch_var.set(""); self._refresh_class_list("")
        self.set_class(self.classes.index(name))
        self.status.config(text=f"已新增类别「{name}」→ custom_classes.txt")

    def remove_custom(self) -> None:
        if not (0 <= self.cur_class < len(self.classes)):
            return
        name = self.classes[self.cur_class]
        if name not in self._customs:
            self.status.config(text=f"「{name}」不是自定义类，不能删"); return
        self._customs.discard(name); self._save_customs()
        self.classes = self._sorted_names()
        n = 0
        for boxes in self.boxes.values():
            for b in boxes:
                if b[4] == name:
                    b[4] = ""; n += 1
        self.cur_class = 0
        self._refresh_class_list(self.csearch_var.get()); self.save()
        self.status.config(text=f"已删自定义类「{name}」（{n} 框类别清空）")

    # ==================== 图片筛选 ====================
    def _status(self, name: str) -> str:
        b = self.boxes.get(name, [])
        if not b:
            return "none"
        if any(not x[4] for x in b):
            return "part"
        q = self.questions.get(cfg.sample_id(name))
        if q:
            got = sum(1 for x in b if x[4] == q["object"])
            if got != q["count"]:
                return "part"
        return "done"

    def apply_filters(self, keep: bool = False) -> None:
        cur_name = self.files[self.i].name if self.files and 0 <= self.i < len(self.files) else None
        m = self.mode_var.get()
        qf = self.qsearch_var.get().strip().lower()
        out = []
        for f in self.files_all:
            if m == "prefilled":
                if f.name not in self.prefilled:
                    continue
            elif m != "all" and self._status(f.name) != m:
                continue
            if qf:
                q = self.questions.get(cfg.sample_id(f.name), {})
                hay = f"{q.get('text','')} {q.get('object','')}".lower()
                py = PINYIN.get(q.get("object", ""), "")
                it = iter(py)
                ok = qf in hay or qf in py or all(ch in it for ch in qf)
                if not ok:
                    continue
            out.append(f)
        self.files = out
        sc = Counter(self._status(f.name) for f in self.files_all)
        self.mode_cnt.config(text=f"全部 {len(self.files_all)} · 未标 {sc.get('none',0)} · "
                                  f"部分 {sc.get('part',0)} · 已标 {sc.get('done',0)} ｜ "
                                  f"预标待核 {len(self.prefilled)}  → 当前 {len(out)} 张")
        if keep and cur_name:
            for k, f in enumerate(self.files):
                if f.name == cur_name:
                    self.i = k; break
            else:
                self.i = 0
        else:
            self.i = 0
        self.load_current()

    # ==================== 数据存取 ====================
    def key(self) -> str:
        return self.files[self.i].name

    def sid(self) -> str:
        return cfg.sample_id(self.key())

    def cur_boxes(self) -> list:
        return self.boxes.setdefault(self.key(), [])

    def push_undo(self) -> None:
        self.undo_stack.append((self.key(), [b[:] for b in self.cur_boxes()]))
        self.undo_stack = self.undo_stack[-50:]

    def undo(self) -> None:
        if not self.undo_stack:
            self.status.config(text="没有可撤销的操作"); return
        name, boxes = self.undo_stack.pop()
        self.boxes[name] = boxes
        if name != self.key():
            for k, f in enumerate(self.files):
                if f.name == name:
                    self.i = k; break
        self.sel = None
        self.load_current(); self.status.config(text="已撤销")

    def load_current(self) -> None:
        self.sel = None; self.mode = None
        if not self.files:
            self.title.config(text="（该筛选下没有图片）")
            self.q_lbl.config(text=""); self.pos_lbl.config(text=""); self.prog["value"] = 0
            self.canvas.delete("all"); self.preview.config(image="")
            return
        self.title.config(text=f"{self.i + 1}/{len(self.files)}  {self.key()}")
        q = self.questions.get(self.sid(), {})
        self.q_lbl.config(text=f"要找：{q.get('text', '?')}（{q.get('object','?')} × {q.get('count','?')}）")
        self._update_cnt()
        self.pos_lbl.config(text=f"[{self.i + 1}/{len(self.files)}]  状态：{self._st_cn(self._status(self.key()))}")
        self.prog["value"] = (self.i + 1) / len(self.files) * 100
        self._prefill_if_empty(); self._update_cnt()
        self.reset_view()

    @staticmethod
    def _st_cn(s: str) -> str:
        return {"none": "未标", "part": "部分", "done": "已标"}.get(s, s)

    def _update_cnt(self) -> None:
        if self.files:
            self.cnt_lbl.config(text=f"本图 {len(self.cur_boxes())} 个框")

    def save(self) -> None:
        rows = ["image,x1,y1,x2,y2,cls"]
        for name in sorted(self.boxes):
            for x1, y1, x2, y2, c in self.boxes[name]:
                rows.append(f"{name},{x1},{y1},{x2},{y2},{c}")
        BOXES_CSV.parent.mkdir(parents=True, exist_ok=True)
        if BOXES_CSV.exists():
            shutil.copy2(BOXES_CSV, BOXES_CSV.with_suffix(".csv.bak"))
        tmp = BOXES_CSV.with_suffix(".csv.tmp")
        tmp.write_text("\n".join(rows), encoding="utf-8")
        tmp.replace(BOXES_CSV)
        # 数量变了才刷新类别列表（保留滚动位置）
        if self._counts() != self._last_counts:
            self._refresh_class_list(self.csearch_var.get())
        if self.files:
            self.pos_lbl.config(text=f"[{self.i + 1}/{len(self.files)}]  状态：{self._st_cn(self._status(self.key()))}")

    def save_and(self, d: int) -> None:
        self.save(); self.go(d)

    def _prefill_if_empty(self) -> None:
        if not self.auto_var.get() or self.cur_boxes():
            return
        try:
            self.autofill(silent=True, with_class=True)   # 出框 + CNN 预标类别
        except Exception:  # noqa: BLE001
            pass

    # ==================== 视图 ====================
    def _on_resize(self, event) -> None:
        if event.widget is self.root:
            self.reset_view()

    def img_bgr(self, name: str) -> np.ndarray:
        if name not in self._img_cache:
            self._img_cache[name] = cv2.imread(str(cfg.RAW_DIR / name))
        return self._img_cache[name]

    def reset_view(self) -> None:
        self.zoom = 1.0; self.redraw()

    def on_wheel(self, e) -> None:
        if not self.files:
            return
        bgr = self.img_bgr(self.key())
        if bgr is None:
            return
        nz = max(1.0, min(6.0, self.zoom * (1.15 if e.delta > 0 else 1 / 1.15)))
        if abs(nz - self.zoom) < 1e-6:
            return
        sc = self.fit_scale
        ix = (e.x - self.off[0]) / (sc * self.zoom); iy = (e.y - self.off[1]) / (sc * self.zoom)
        self.zoom = nz
        self.off[0] = e.x - ix * sc * self.zoom; self.off[1] = e.y - iy * sc * self.zoom
        self.redraw()

    def _pan_start(self, e) -> None:
        self._panning = True; self._pan_last = (e.x, e.y)

    def _pan_move(self, e) -> None:
        if not self._panning:
            return
        self.off[0] += e.x - self._pan_last[0]; self.off[1] += e.y - self._pan_last[1]
        self._pan_last = (e.x, e.y); self.redraw()

    def to_disp(self, x, y):
        s = self.fit_scale * self.zoom
        return self.off[0] + x * s, self.off[1] + y * s

    def to_orig(self, cx, cy):
        s = self.fit_scale * self.zoom
        return (cx - self.off[0]) / s, (cy - self.off[1]) / s

    def redraw(self) -> None:
        if not self.files:
            return
        w = max(200, self.canvas.winfo_width()); h = max(200, self.canvas.winfo_height())
        bgr = self.img_bgr(self.key())
        if bgr is None:
            return
        pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        iw, ih = pil.size
        self.fit_scale = min(w / iw, h / ih)
        s = self.fit_scale * self.zoom
        dw, dh = int(iw * s), int(ih * s)
        if self.zoom == 1.0:
            self.off = [(w - dw) / 2, (h - dh) / 2]
        self.tkimg = ImageTk.PhotoImage(pil.resize((dw, dh)))
        self.canvas.delete("all")
        self.canvas.create_image(self.off[0], self.off[1], anchor="nw", image=self.tkimg)
        for idx, (x1, y1, x2, y2, c) in enumerate(self.cur_boxes()):
            self._draw_box(idx, x1, y1, x2, y2, c, selected=(idx == self.sel))
        self._update_preview()

    def _class_color(self, c: str) -> str:
        return PALETTE[self.classes.index(c) % len(PALETTE)] if c in self.classes else "#ffd43b"

    def _draw_box(self, idx, x1, y1, x2, y2, c, selected=False) -> None:
        dx1, dy1 = self.to_disp(x1, y1); dx2, dy2 = self.to_disp(x2, y2)
        col = self._class_color(c)
        self.canvas.create_rectangle(dx1, dy1, dx2, dy2, outline=col, width=3 if selected else 2)
        self.canvas.create_text(dx1 + 3, dy1 + 9, anchor="w", text=(c or "?"), fill=col, font=(UI, 10, "bold"))
        if selected:
            for hx, hy in self._handles(dx1, dy1, dx2, dy2).values():
                self.canvas.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4, fill=col, outline="white")

    @staticmethod
    def _handles(dx1, dy1, dx2, dy2) -> dict:
        mx, my = (dx1 + dx2) / 2, (dy1 + dy2) / 2
        return {"nw": (dx1, dy1), "n": (mx, dy1), "ne": (dx2, dy1), "e": (dx2, my),
                "se": (dx2, dy2), "s": (mx, dy2), "sw": (dx1, dy2), "w": (dx1, my)}

    def _update_preview(self) -> None:
        if self.sel is None or self.sel >= len(self.cur_boxes()):
            self.preview.config(image=""); self.preview.image = None; return
        x1, y1, x2, y2, _ = self.cur_boxes()[self.sel]
        img = self.img_bgr(self.key())
        crop = img[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        if crop.size == 0:
            return
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        ph = ImageTk.PhotoImage(Image.fromarray(rgb).resize((PREVIEW, PREVIEW), Image.NEAREST))
        self.preview.config(image=ph); self.preview.image = ph

    # ==================== 命中 / 鼠标 ====================
    def hit(self, ex, ey):
        boxes = self.cur_boxes(); n = len(boxes)
        sel = self.sel if (self.sel is not None and 0 <= self.sel < n) else None
        order = ([sel] if sel is not None else []) + [i for i in range(n - 1, -1, -1) if i != sel]
        for idx in order:
            dx1, dy1 = self.to_disp(boxes[idx][0], boxes[idx][1]); dx2, dy2 = self.to_disp(boxes[idx][2], boxes[idx][3])
            r = HIT_HANDLE if idx == sel else 6
            for h, (hx, hy) in self._handles(dx1, dy1, dx2, dy2).items():
                if abs(ex - hx) <= r and abs(ey - hy) <= r:
                    return "resize", idx, h
        for idx in order:
            dx1, dy1 = self.to_disp(boxes[idx][0], boxes[idx][1]); dx2, dy2 = self.to_disp(boxes[idx][2], boxes[idx][3])
            m = MOVE_MARGIN if idx == sel else 3
            if dx1 - m <= ex <= dx2 + m and dy1 - m <= ey <= dy2 + m:
                return "move", idx, None
        return "new", None, None

    @staticmethod
    def _iou(a, b) -> float:
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        if inter <= 0:
            return 0.0
        aa = (a[2] - a[0]) * (a[3] - a[1]); bb = (b[2] - b[0]) * (b[3] - b[1])
        return inter / max(1, aa + bb - inter)

    def on_hover(self, e) -> None:
        if not self.cur_boxes():
            self.canvas.config(cursor="crosshair"); return
        kind, _, h = self.hit(e.x, e.y)
        if kind == "resize":
            cur = {"nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
                   "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
                   "e": "sb_h_double_arrow", "w": "sb_h_double_arrow"}.get(h, "crosshair")
        elif kind == "move":
            cur = "fleur"
        else:
            cur = "crosshair"
        self.canvas.config(cursor=cur)

    def on_press(self, e) -> None:
        kind, idx, h = self.hit(e.x, e.y)
        self.mode, self.sel, self.resize_handle = kind, idx, h
        self.anchor = self.to_orig(e.x, e.y); self._press_xy = (e.x, e.y); self._op_pushed = False
        if kind != "new":
            self.redraw()

    def on_right_click(self, e) -> None:
        kind, idx, _ = self.hit(e.x, e.y)
        if idx is not None and 0 <= idx < len(self.cur_boxes()):
            self.push_undo(); self.cur_boxes().pop(idx); self.sel = None
            self._update_cnt(); self.save(); self.redraw()
            self.status.config(text="已右键删除该框")

    def _ensure_undo(self) -> None:
        if not self._op_pushed:
            self.push_undo(); self._op_pushed = True

    def on_motion(self, e) -> None:
        if self.mode is None:
            return
        x, y = self.to_orig(e.x, e.y); boxes = self.cur_boxes()
        if self.mode == "new" and self.anchor:
            x0, y0 = self.anchor
            dx1, dy1 = self.to_disp(min(x0, x), min(y0, y)); dx2, dy2 = self.to_disp(max(x0, x), max(y0, y))
            self.canvas.delete("tmp")
            self.canvas.create_rectangle(dx1, dy1, dx2, dy2, outline="#ffd43b", width=2, tags="tmp")
        elif self.mode == "move" and self.sel is not None and self.anchor:
            self._ensure_undo()
            x0, y0 = self.anchor; dx, dy = int(x - x0), int(y - y0); b = boxes[self.sel]
            b[0], b[1], b[2], b[3] = b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy
            self.anchor = (x, y); self.redraw()
        elif self.mode == "resize" and self.sel is not None:
            self._ensure_undo()
            b = boxes[self.sel]; h = self.resize_handle
            if "n" in h: b[1] = int(y)
            if "s" in h: b[3] = int(y)
            if "w" in h: b[0] = int(x)
            if "e" in h: b[2] = int(x)
            self.redraw()

    def on_release(self, e) -> None:
        if self.mode is None:
            return
        mode = self.mode; self.mode = None; boxes = self.cur_boxes()
        moved = max(abs(e.x - self._press_xy[0]), abs(e.y - self._press_xy[1])) if self._press_xy else 99
        if mode == "new" and self.anchor:
            x0, y0 = self.anchor; x1, y1 = self.to_orig(e.x, e.y); self.anchor = None
            x1, x2 = sorted((int(x0), int(x1))); y1, y2 = sorted((int(y0), int(y1)))
            if x2 - x1 < MIN_SIDE or y2 - y1 < MIN_SIDE:
                self.redraw(); return
            self.push_undo()
            new = [x1, y1, x2, y2, self.classes[self.cur_class]]
            dup = next((k for k, b in enumerate(boxes) if self._iou(new, b) > DUP_IOU), None)
            if dup is not None:
                self.undo_stack.pop(); self.sel = dup
                self.status.config(text="与已有框重叠，已改为选中该框（不新建）"); self.redraw(); return
            boxes.append(new); self.sel = len(boxes) - 1
        elif mode in ("move", "resize") and self.sel is not None:
            b = boxes[self.sel]
            b[0], b[2] = sorted((b[0], b[2])); b[1], b[3] = sorted((b[1], b[3]))
            if b[2] - b[0] < MIN_SIDE or b[3] - b[1] < MIN_SIDE:
                boxes.pop(self.sel); self.sel = None
            elif mode == "move" and moved <= 4:
                cur = self.classes[self.cur_class]
                if boxes[self.sel][4] != cur:
                    self.push_undo(); boxes[self.sel][4] = cur
                    self.status.config(text=f"该框 → {cur}")
        self.anchor = None; self._press_xy = None
        self._update_cnt(); self.save(); self.redraw()

    def nudge(self, dx: int, dy: int, e=None) -> None:
        if self._typing() or self.sel is None or self.sel >= len(self.cur_boxes()):
            return
        step = 10 if (e is not None and (e.state & 0x0001)) else 1
        self.push_undo(); b = self.cur_boxes()[self.sel]
        b[0] += dx * step; b[2] += dx * step; b[1] += dy * step; b[3] += dy * step
        self.save(); self.redraw()

    # ==================== 删除/清空 ====================
    def del_selected(self) -> None:
        boxes = self.cur_boxes()
        if self.sel is not None and 0 <= self.sel < len(boxes):
            self.push_undo(); boxes.pop(self.sel); self.sel = None
        elif boxes:
            self.push_undo(); boxes.pop()
        self._update_cnt(); self.save(); self.redraw()

    def clear_cur(self) -> None:
        if self.cur_boxes():
            self.push_undo()
        self.boxes[self.key()] = []
        self.sel = None; self._update_cnt(); self.save(); self.redraw()

    # ==================== 自动出框 ====================
    def reload_cnn(self) -> None:
        """丢弃缓存，重新从 best.pt 加载模型（避免一直用旧模型）。"""
        self._cnn = "unset"
        fn = self._load_cnn()
        self.update_model_label(auto=fn is not None)
        self.status.config(text="已重载 CNN 模型" if fn else "没找到模型文件")

    def update_model_label(self, auto: bool = False) -> None:
        if not hasattr(self, "model_lbl"):
            return
        try:
            import time
            rel = cfg.RELEASE_DIR / "icon64_cnn_best.pt"
            p = rel if rel.exists() else (cfg.RUNS_DIR / "cls" / "icon48_cnn" / "best.pt")
            if not p.exists():
                self.model_lbl.config(text="未找到 CNN（预标只出框）", foreground="#c92a2a"); return
            import torch
            ck = torch.load(str(p), map_location="cpu")
            where = "release" if p == rel else "runs"
            self.model_lbl.config(text=f"CNN 已加载：{len(ck['classes'])}类 {ck.get('size', 64)}px（{where}，"
                                       f"{time.strftime('%m-%d %H:%M', time.localtime(p.stat().st_mtime))}）",
                                  foreground="#2b8a3e")
        except Exception:  # noqa: BLE001
            self.model_lbl.config(text="CNN 加载失败", foreground="#c92a2a")

    def _load_cnn(self):
        """返回 crop(BGR) -> 概率向量 np.ndarray（支持多模型集成）；类名存 self._cnn_names。"""
        if self._cnn != "unset":
            return self._cnn
        self._cnn = None
        self._cnn_names: list[str] = []
        try:
            from cls_train import load_model_set, predict_probs_multi
            nets, names, size = load_model_set()
            self._cnn_names = list(names)
            self._cnn_n = len(nets)

            def fn(crop):
                return predict_probs_multi(nets, crop, size)
            self._cnn = fn
        except Exception:  # noqa: BLE001
            self._cnn = None
        return self._cnn

    def autofill(self, silent: bool = False, with_class: bool = True) -> None:
        """出框；with_class=True 时用 CNN 预填，并**用题目强制纠正目标类与数量**。"""
        try:
            from cls_build import detect_icons
        except Exception as e:  # noqa: BLE001
            self.status.config(text=f"自动出框不可用：{e}"); return
        img = self.img_bgr(self.key())
        if img is None:
            return
        dets = detect_icons(img)
        cnn = self._load_cnn() if with_class else None
        labels = [""] * len(dets)
        probs: list = [None] * len(dets)
        if cnn:
            names = self._cnn_names
            for j, (x, y, w, h) in enumerate(dets):
                crop = img[max(0, y):y + h, max(0, x):x + w]
                if crop.size == 0:
                    continue
                pv = cnn(crop)
                probs[j] = pv
                i = int(pv.argmax())
                if pv[i] >= 0.20:                       # 低置信留空
                    labels[j] = names[i]
            # 用题目（目标类 + 数量）强制纠正：先清掉目标类，再取概率最高的 count 个框填上
            q = self.questions.get(self.sid(), {})
            tgt, k = q.get("object"), int(q.get("count", 0) or 0)
            if tgt in names and k > 0:
                for j in range(len(dets)):
                    if labels[j] == tgt:
                        labels[j] = ""
                ti = names.index(tgt)
                idxs = [j for j in range(len(dets)) if probs[j] is not None]
                idxs.sort(key=lambda j: -float(probs[j][ti]))
                for j in idxs[:k]:
                    labels[j] = tgt
        # 与已有框去重后加入
        boxes = self.cur_boxes(); existing = list(boxes); new_items = []
        for (x, y, w, h), cls in zip(dets, labels):
            nb = [int(x), int(y), int(x + w), int(y + h), cls]
            if any(self._iou(nb, b) > 0.3 for b in existing):
                continue
            existing.append(nb); new_items.append(nb)
        if new_items:
            self.push_undo(); boxes.extend(new_items)
            if cnn:                              # 记入“预标待核”
                self.prefilled.add(self.key()); self._save_prefilled()
        self._update_cnt()
        if not silent:
            self.status.config(text=f"自动预标：+{len(new_items)} 个框" +
                                     ("（已用题目纠正目标类/数量）" if cnn else "（只出框）"))
        self.save(); self.redraw()

    # ==================== 导航 ====================
    def go(self, d: int) -> None:
        if not self.files:
            return
        self.save()
        self.i = (self.i + d) % len(self.files)
        self.load_current()

    def do_jump(self) -> None:
        try:
            k = int(self.jump_var.get()) - 1
        except Exception:  # noqa: BLE001
            return
        if 0 <= k < len(self.files):
            self.save(); self.i = k; self.load_current()

    def quit(self) -> None:
        self.save(); self.root.destroy()


def main() -> int:
    root = tk.Tk()
    Annotator(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
