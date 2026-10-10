# -*- coding: utf-8 -*-
"""构建图标分类数据集：`processed/cls/<c00..>/<样本号>__<i>.png`。

来源（优先级）：
  1. 人工标注 `labels/boxes.csv`（用 `annotate.py` 标注）——可信；
  2. 若没有人工标注，退回伪标 `labels/boxes_auto.csv`（`weak_label.py` / 其他）。
划分：**按图片分**（文件名前缀 = 样本号），`cls_train.py` 按样本号切 train/val，避免同图泄漏。

类名用 ASCII 目录（c00..）+ `classes.json` 映射，避免中文路径让 `cv2.imwrite` 静默失败。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\cls_dataset.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CLS_DIR = cfg.PROCESSED_DIR / "cls"
MARGIN = 2


def pick_boxes_file() -> Path | None:
    human = cfg.LABELS_DIR / "boxes.csv"
    if human.exists() and human.stat().st_size > 20:
        return human
    auto = cfg.LABELS_DIR / "boxes_auto.csv"
    if auto.exists() and auto.stat().st_size > 20:
        return auto
    return None


def main() -> int:
    src = pick_boxes_file()
    if src is None:
        print("没有 labels/boxes.csv 或 boxes_auto.csv，请先用 annotate.py 标注")
        return 1
    print(f"标签来源：{src}")

    classes = list(cfg.CLASSES)
    ascii_of = {c: f"c{i:02d}" for i, c in enumerate(classes)}
    unknown = set()

    if CLS_DIR.exists():
        shutil.rmtree(CLS_DIR)
    CLS_DIR.mkdir(parents=True, exist_ok=True)
    (CLS_DIR / "classes.json").write_text(json.dumps(classes, ensure_ascii=False), encoding="utf-8")

    n = 0
    manifest = ["file,image,x1,y1,x2,y2,cls"]
    for r in csv.DictReader(src.open(encoding="utf-8")):
        cls = (r.get("cls") or "").strip()
        if not cls:
            continue
        if cls not in ascii_of:
            unknown.add(cls)
            continue
        img = cv2.imread(str(cfg.RAW_DIR / r["image"]))
        if img is None:
            continue
        ox1, oy1, ox2, oy2 = (int(float(r[k])) for k in ("x1", "y1", "x2", "y2"))
        x1, y1 = max(0, ox1 - MARGIN), max(0, oy1 - MARGIN)
        x2, y2 = min(img.shape[1], ox2 + MARGIN), min(img.shape[0], oy2 + MARGIN)
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        sid = cfg.sample_id(r["image"])
        d = CLS_DIR / ascii_of[cls]
        d.mkdir(parents=True, exist_ok=True)
        # 文件名带“原图号 + 坐标”，可追溯到具体框；审核时可单个删除
        fname = f"{sid}_{x1:03d}_{y1:03d}_{x2:03d}_{y2:03d}.png"
        ok, buf = cv2.imencode(".png", crop)
        if ok:
            (d / fname).write_bytes(buf.tobytes())
            manifest.append(f"{ascii_of[cls]}/{fname},{r['image']},{ox1},{oy1},{ox2},{oy2},{cls}")
            n += 1

    (CLS_DIR / "_manifest.csv").write_text("\n".join(manifest), encoding="utf-8")

    counts = {d.name: len(list(d.glob("*.png"))) for d in sorted(CLS_DIR.iterdir()) if d.is_dir()}
    name_of = {ascii_of[c]: c for c in classes}
    print(f"裁剪 {n} 张 → {CLS_DIR}")
    print("每类：" + "  ".join(f"{k}({name_of[k]}):{v}" for k, v in counts.items()))
    if unknown:
        print("不在 CLASSES 的类别（已跳过）：", sorted(unknown))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
