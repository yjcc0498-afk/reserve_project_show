# -*- coding: utf-8 -*-
"""训练 YOLO11 检测图标（与 CNN 两阶段对比）。

离线要点：amp=False、本地 .pt 权重（models/pretrained/yolo11n.pt）。

用法（项目根目录）：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\det_train.py --epochs 100 --imgsz 640
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402
from ultralytics import YOLO  # noqa: E402

DATA = str(cfg.PROCESSED_DIR / "det" / "data.yaml")
PROJECT = str((cfg.RUNS_DIR / "det").as_posix())


def on_epoch_end(trainer) -> None:
    m = trainer.metrics or {}
    s = f"[进度] epoch {int(trainer.epoch) + 1}/{int(trainer.epochs)}"
    for k in ("train/box_loss", "metrics/mAP50(B)", "metrics/mAP50-95(B)"):
        if k in m:
            s += f"  {k.split('/')[-1]}={m[k]:.3f}"
    print(s, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(cfg.PRETRAINED_DIR / "yolo11n.pt"))
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0")
    ap.add_argument("--patience", type=int, default=40)
    ap.add_argument("--name", default="yolo11n_icons")
    args = ap.parse_args()

    model = YOLO(args.model)
    model.add_callback("on_train_epoch_end", on_epoch_end)
    model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                device=args.device, patience=args.patience, amp=False, seed=0, workers=0,
                hsv_h=0.02, hsv_s=0.7, hsv_v=0.4,          # 颜色抖动（换色/灰度变体）
                degrees=15.0, translate=0.1, scale=0.4,     # 旋转/平移/缩放
                fliplr=0.5, flipud=0.1,                     # 镜像
                mosaic=1.0, erasing=0.2,                    # 拼接/遮挡
                project=PROJECT, name=args.name, exist_ok=True)
    print(f"完成，权重: {PROJECT}/{args.name}/weights/best.pt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
