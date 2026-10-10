# -*- coding: utf-8 -*-
"""切图审核工具：按类别查看 `processed/cls/<cXX>/` 里切出来的小图，删除标错/串类的，
再把删除同步回 `labels/boxes.csv`（这样重训时就不会再生成它们）。

流程：
    1) 先跑 `cls_dataset.py` 生成切图（文件名 = 原图号_坐标，可追溯）；
    2) 本工具里左边选类别、右边看缩略图；左键点选 = 标记删除（变红）；
    3) 点「删除标记的图并同步 boxes.csv」→ 删文件 + 从 boxes.csv 移除对应行；
    4) 重新跑 `cls_dataset.py` + `cls_train.py` 即可（不会再出现被删的图）。

用法（项目根目录）：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\review_crops.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import cv2
from PIL import Image, ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

try:
    from annotate import PINYIN
except Exception:  # noqa: BLE001
    PINYIN = {}

CLS = cfg.PROCESSED_DIR / "cls"
MANIFEST = CLS / "_manifest.csv"
BOXES_CSV = cfg.LABELS_DIR / "boxes.csv"
DEL_LOG = cfg.LABELS_DIR / "_crop_deletions.csv"    # 删除记录（image,x1,y1,x2,y2,cls）
THUMB = 76


class ReviewApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("切图审核（点选删除 → 同步 boxes.csv）")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        W, H = min(1200, sw - 60), min(900, sh - 110)
        root.geometry(f"{W}x{H}+{max(0,(sw-W)//2)}+{max(0,(sh-H)//3)}")
        root.minsize(900, 560)

        if not (CLS / "classes.json").exists():
            raise SystemExit("没找到 processed/cls，请先运行 cls_dataset.py")
        self.cj = json.loads((CLS / "classes.json").read_text(encoding="utf-8"))
        self.dirs = sorted([d for d in CLS.iterdir() if d.is_dir()])
        if not self.dirs:
            raise SystemExit("processed/cls 下没有类别文件夹，请先运行 cls_dataset.py")
        self.name_of = {d.name: self.cj[int(d.name[1:])] for d in self.dirs}
        self.dirs.sort(key=lambda d: PINYIN.get(self.name_of[d.name], self.name_of[d.name]))

        self.marked: set[Path] = set()
        self.i = 0
        self._thumbs = []

        self._style()
        self._build_ui()
        self.load_class()

    def _style(self) -> None:
        self.root.configure(bg="#f5f6f8")
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=("Microsoft YaHei", 10), background="#f5f6f8")
        st.configure("TFrame", background="#f5f6f8")
        st.configure("TLabel", background="#f5f6f8")
        st.configure("TLabelframe", background="#f5f6f8")
        st.configure("TLabelframe.Label", background="#f5f6f8", foreground="#3b5bdb",
                     font=("Microsoft YaHei", 10, "bold"))
        st.configure("TButton", padding=(6, 4))

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(8, 2))
        self.title = ttk.Label(top, font=("Microsoft YaHei", 12, "bold"))
        self.title.pack(side="left")
        self.cnt = ttk.Label(top, foreground="#1864ab")
        self.cnt.pack(side="left", padx=12)
        self.mark_lbl = ttk.Label(top, foreground="#c92a2a", font=("Microsoft YaHei", 10, "bold"))
        self.mark_lbl.pack(side="right")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=10, pady=4)

        left = ttk.Frame(body, width=200)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="类别（拼音序）").pack(anchor="w")
        self.lb = tk.Listbox(left, width=22, font=("Consolas", 10), exportselection=False)
        self.lb.pack(fill="both", expand=True)
        for d in self.dirs:
            n = len(list(d.glob("*.png")))
            self.lb.insert("end", f"{self.name_of[d.name]}  ({n})")
        self.lb.bind("<<ListboxSelect>>", self.on_pick)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.canvas = tk.Canvas(right, bg="#ffffff", highlightthickness=1, highlightbackground="#ddd")
        vsb = ttk.Scrollbar(right, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Button(bar, text="🗑 删除标记的图并同步 boxes.csv", command=self.apply_delete).pack(side="left")
        ttk.Button(bar, text="清空标记", command=self.clear_marks).pack(side="left", padx=6)
        ttk.Button(bar, text="↻ 重载切图", command=self.reload).pack(side="left")
        ttk.Button(bar, text="只同步(不删文件)", command=self.sync_only).pack(side="left", padx=6)
        self.status = ttk.Label(self.root, foreground="#1864ab")
        self.status.pack(fill="x", padx=12)
        ttk.Label(self.root, foreground="#868e96", font=("Microsoft YaHei", 9),
                  text="左键单击缩略图 = 标记删除（红框）；再点一次取消。确认无误后点「删除标记的图并同步 boxes.csv」。").pack(
            fill="x", padx=12, pady=(0, 6))

    # ---------------- 渲染 ----------------
    def cur_dir(self) -> Path:
        return self.dirs[self.i]

    def load_class(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._thumbs.clear()
        d = self.cur_dir()
        files = sorted(d.glob("*.png"))
        name = self.name_of[d.name]
        self.title.config(text=f"{d.name}  {name}")
        self.cnt.config(text=f"该类 {len(files)} 张（共 {len(self.dirs)} 类）")
        self.mark_lbl.config(text=f"已标记 {len(self.marked)} 张")
        cols = max(4, (self.canvas.winfo_width() - 20) // (THUMB + 10))
        for k, p in enumerate(files):
            img = cv2.imread(str(p))
            if img is None:
                continue
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            ph = ImageTk.PhotoImage(Image.fromarray(rgb).resize((THUMB, THUMB), Image.NEAREST))
            self._thumbs.append(ph)
            border = 3 if p in self.marked else 1
            color = "#e03131" if p in self.marked else "#cccccc"
            lab = tk.Label(self.inner, image=ph, bd=0, highlightthickness=border,
                           highlightbackground=color, highlightcolor=color, bg="white")
            lab.grid(row=k // cols, column=k % cols, padx=4, pady=4)
            lab.bind("<Button-1>", lambda e, pp=p: self.toggle(pp))
            lab.bind("<Enter>", lambda e, l=lab: l.config(highlightbackground="#3b5bdb"))
            lab.bind("<Leave>", lambda e, l=lab, pp=p: l.config(
                highlightbackground=("#e03131" if pp in self.marked else "#cccccc")))

    def reload(self) -> None:
        self.load_class()

    def on_pick(self, _e=None) -> None:
        sel = self.lb.curselection()
        if sel:
            self.i = sel[0]
            self.load_class()

    def toggle(self, p: Path) -> None:
        if p in self.marked:
            self.marked.discard(p)
        else:
            self.marked.add(p)
        self.load_class()

    def clear_marks(self) -> None:
        self.marked.clear()
        self.load_class()
        self.status.config(text="已清空标记")

    # ---------------- 删除 + 同步 ----------------
    def apply_delete(self) -> None:
        if not self.marked:
            self.status.config(text="没有标记要删除的图")
            return
        self._log_deletions(list(self.marked))
        n = 0
        for p in list(self.marked):
            try:
                p.unlink()
                n += 1
            except Exception:  # noqa: BLE001
                pass
        self.marked.clear()
        removed = self._sync_boxes()
        self.status.config(text=f"已删除 {n} 个切图；从 boxes.csv 移除 {removed} 个框"
                                f"（已记录到 _crop_deletions.csv，boxes 已备份 .bak）")
        self._recount_list()
        self.load_class()

    def _log_deletions(self, files: list[Path]) -> None:
        """把被删的切图对应的原始框追加到删除日志（方便回溯“我删了哪些”）。"""
        if not MANIFEST.exists():
            return
        man = {r["file"]: r for r in csv.DictReader(MANIFEST.open(encoding="utf-8"))}
        is_new = not DEL_LOG.exists()
        with DEL_LOG.open("a", encoding="utf-8") as f:
            if is_new:
                f.write("image,x1,y1,x2,y2,cls\n")
            for p in files:
                r = man.get(f"{p.parent.name}/{p.name}")
                if r:
                    f.write(f"{r['image']},{r['x1']},{r['y1']},{r['x2']},{r['y2']},{r['cls']}\n")

    def sync_only(self) -> None:
        removed = self._sync_boxes()
        self.status.config(text=f"已同步：从 boxes.csv 移除 {removed} 个（其切图已不存在）")
        self._recount_list()
        self.load_class()

    def _sync_boxes(self) -> int:
        """把“切图文件已不存在”的框从 boxes.csv 移除。"""
        if not MANIFEST.exists():
            return 0
        missing = set()
        for r in csv.DictReader(MANIFEST.open(encoding="utf-8")):
            f = CLS / r["file"]
            if not f.exists():
                missing.add((r["image"], int(r["x1"]), int(r["y1"]), int(r["x2"]), int(r["y2"])))
        if not missing:
            return 0
        rows = list(csv.DictReader(BOXES_CSV.open(encoding="utf-8")))
        keep = ["image,x1,y1,x2,y2,cls"]
        removed = 0
        for r in rows:
            key = (r["image"], int(float(r["x1"])), int(float(r["y1"])),
                   int(float(r["x2"])), int(float(r["y2"])))
            if key in missing:
                removed += 1
                continue
            keep.append(f"{r['image']},{r['x1']},{r['y1']},{r['x2']},{r['y2']},{r.get('cls','')}")
        if removed:
            shutil.copy2(BOXES_CSV, BOXES_CSV.with_suffix(".csv.bak"))
            tmp = BOXES_CSV.with_suffix(".csv.tmp")
            tmp.write_text("\n".join(keep), encoding="utf-8")
            tmp.replace(BOXES_CSV)
        return removed

    def _recount_list(self) -> None:
        sel = self.lb.curselection()
        self.lb.delete(0, "end")
        for d in self.dirs:
            self.lb.insert("end", f"{self.name_of[d.name]}  ({len(list(d.glob('*.png')))})")
        if sel:
            self.lb.selection_set(sel[0])


def main() -> int:
    root = tk.Tk()
    ReviewApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
