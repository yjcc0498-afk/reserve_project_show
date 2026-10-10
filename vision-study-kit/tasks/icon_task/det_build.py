# -*- coding: utf-8 -*-
"""构建 YOLO 检测数据集 `processed/det/`（人工框 + CNN 伪标）。

来源：
  - 人工标注（`labels/boxes.csv`）→ 真值框；
  - 其余图片 → `cls_build.detect_icons` 出框 + 训练好的 CNN 分类（伪标）。
类名用 ASCII（c00..），映射写进 `names.json`（避免中文 yaml/路径坑）。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\det_build.py [--val-stems 0000,0001]
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402
from cls_build import detect_icons  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

OUT = cfg.PROCESSED_DIR / "det"


class CnnLabeler:
    """用 CNN（支持集成）给裁剪判类（BGR crop -> 中文类名）。"""

    def __init__(self, exp: str = ""):
        self.ok = False
        try:
            from cls_train import load_model_set, predict_probs_multi
            path = (cfg.RUNS_DIR / "cls" / exp) if exp else None
            self.nets, self.names, self.size = load_model_set(path)
            self._fn = predict_probs_multi
            self.ok = True
        except Exception:  # noqa: BLE001
            self.ok = False

    def __call__(self, crop: np.ndarray) -> str:
        p = self._fn(self.nets, crop, self.size)
        return self.names[int(p.argmax())]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val-stems", default="", help="逗号分隔的 val 样本号")
    ap.add_argument("--cls-exp", default="icon48_cnn")
    args = ap.parse_args()

    human: dict[str, list] = defaultdict(list)
    hpath = cfg.LABELS_DIR / "boxes.csv"
    if hpath.exists():
        for r in csv.DictReader(hpath.open(encoding="utf-8")):
            human[cfg.sample_id(r["image"])].append(
                (int(float(r["x1"])), int(float(r["y1"])), int(float(r["x2"])), int(float(r["y2"])), r["cls"]))
    print(f"人工标注图 {len(human)} 张")

    classes = list(cfg.CLASSES)
    idx_of = {c: i for i, c in enumerate(classes)}
    ascii_names = [f"c{i:02d}" for i in range(len(classes))]
    clf = CnnLabeler(args.cls_exp)
    print("CNN 伪标可用：" if clf.ok else "CNN 伪标不可用（只用人工框）", clf.ok)

    val_stems = {s for s in args.val_stems.split(",") if s}
    ids = cfg.sample_ids()
    if not val_stems:
        val_stems = set(ids[::12])   # 每 12 张取 1 张作 val

    if OUT.exists():
        shutil.rmtree(OUT)
    for s in ("train", "val"):
        (OUT / "images" / s).mkdir(parents=True, exist_ok=True)
        (OUT / "labels" / s).mkdir(parents=True, exist_ok=True)

    n_box = {"train": 0, "val": 0}
    n_img = {"train": 0, "val": 0}
    for sid in ids:
        img = cv2.imread(str(cfg.image_path(sid)))
        if img is None:
            continue
        H, W = img.shape[:2]
        if sid in human:
            boxes = human[sid]
        elif clf.ok:
            boxes = []
            for x, y, w, h in detect_icons(img):
                c = clf(img[y:y + h, x:x + w])
                if c in idx_of:
                    boxes.append((x, y, x + w, y + h, c))
        else:
            continue
        boxes = [b for b in boxes if b[4] in idx_of]
        if not boxes:
            continue
        s = "val" if sid in val_stems else "train"
        lines = []
        for x1, y1, x2, y2, c in boxes:
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(W, x2), min(H, y2)
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            lines.append(f"{idx_of[c]} {(x1 + x2) / 2 / W:.6f} {(y1 + y2) / 2 / H:.6f} "
                         f"{(x2 - x1) / W:.6f} {(y2 - y1) / H:.6f}")
        if not lines:
            continue
        fname = f"{sid}{cfg.IMAGE_SUFFIX}"
        (OUT / "labels" / s / (Path(fname).stem + ".txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")
        shutil.copy(cfg.image_path(sid), OUT / "images" / s / fname)
        n_box[s] += len(lines)
        n_img[s] += 1

    (OUT / "data.yaml").write_text(
        f"path: {OUT.resolve().as_posix()}\ntrain: images/train\nval: images/val\n\n"
        "nameLOCAL_WORKSPACE" + "\n".join(f"  {i}: \"{n}\"" for i, n in enumerate(ascii_names)) + "\n",
        encoding="utf-8")
    (OUT / "names.json").write_text(json.dumps({f"c{i:02d}": c for i, c in enumerate(classes)},
                                               ensure_ascii=False), encoding="utf-8")
    print(f"数据集 {OUT}")
    print(f"train: {n_img['train']} 图 / {n_box['train']} 框")
    print(f"val  : {n_img['val']} 图 / {n_box['val']} 框")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
