# -*- coding: utf-8 -*-
"""标注质检：找出可能标错/漏标的框。

检查项：
  1. 题目数量对不上（目标类的框数 != 题目 count）；
  2. 空类别框；
  3. CNN 与标签不一致（模型很自信却和标签不同 → 疑似标错）；
  4. 可能漏框（OpenCV 检测到图标处没有人工框）；
  5. 重复框（同图同类高度重叠）。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\label_qa.py [--top 200]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
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


def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1, aa + bb - inter)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=160, help="每类展示的疑似错误数上限")
    args = ap.parse_args()

    rows = list(csv.DictReader((cfg.LABELS_DIR / "boxes.csv").open(encoding="utf-8")))
    by_img: dict[str, list] = defaultdict(list)
    for r in rows:
        by_img[r["image"]].append([int(float(r["x1"])), int(float(r["y1"])),
                                   int(float(r["x2"])), int(float(r["y2"])), r.get("cls", "") or ""])
    Q = json.loads((cfg.LABELS_DIR / "questions.json").read_text(encoding="utf-8"))

    import torch
    from cls_train import IconNet, SIZE
    ck = torch.load(str(cfg.RUNS_DIR / "cls" / "icon48_cnn" / "best.pt"), map_location="cpu")
    net = IconNet(len(ck["classes"])); net.load_state_dict(ck["model"]); net.eval()
    names = ck["names"]

    def predict(crop):
        im = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), (SIZE, SIZE)).astype(np.float32) / 255
        t = torch.from_numpy(np.ascontiguousarray(im.transpose(2, 0, 1)))[None]
        with torch.no_grad():
            p = torch.softmax(net(t), 1)[0]
        i = int(p.argmax())
        return names[i], float(p[i])

    report: list[str] = []
    cnt_bad, empty_bad, disagree, missed, dup = [], [], [], [], []
    for name, bs in by_img.items():
        sid = cfg.sample_id(name)
        q = Q.get(sid, {})
        img = cv2.imread(str(cfg.RAW_DIR / name))
        if img is None:
            continue
        # 1/2
        empt = [b for b in bs if not b[4]]
        if empt:
            empty_bad.append((sid, len(empt)))
        if q:
            got = sum(1 for b in bs if b[4] == q["object"])
            if got != q["count"]:
                cnt_bad.append((sid, q["object"], q["count"], got))
        # 3 CNN 不一致
        for b in bs:
            if not b[4]:
                continue
            crop = img[max(0, b[1]):max(0, b[3]), max(0, b[0]):max(0, b[2])]
            if crop.size == 0:
                continue
            pred, conf = predict(crop)
            if pred != b[4] and conf >= 0.7:
                disagree.append((sid, b, b[4], pred, conf, crop.copy()))
        # 4 漏框
        for x, y, w, h in detect_icons(img):
            db = [x, y, x + w, y + h]
            if all(iou(db, b) < 0.15 for b in bs):
                crop = img[y:y + h, x:x + w]
                if crop.size:
                    missed.append((sid, db, crop.copy()))
        # 5 重复框
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                if bs[i][4] and bs[i][4] == bs[j][4] and iou(bs[i], bs[j]) > 0.7:
                    dup.append((sid, bs[i][4]))

    report.append(f"已标 {len(rows)} 框 / {len(by_img)} 图")
    report.append(f"\n[1] 题目数量对不上：{len(cnt_bad)} 张")
    for sid, obj, need, got in sorted(cnt_bad):
        report.append(f"    {sid}  目标={obj}×{need}  实际标了 {got}")
    report.append(f"\n[2] 空类别框：{len(empty_bad)} 张 -> " + " ".join(f"{s}({n})" for s, n in empty_bad[:40]))
    report.append(f"\n[3] CNN 与标签不一致（conf>=0.7）：{len(disagree)} 处")
    by_lp = Counter(f"{lb}→{pr}" for _, _, lb, pr, _, _ in disagree)
    for k, v in by_lp.most_common(25):
        report.append(f"    {k}  x{v}")
    report.append(f"\n[4] 可能漏框：{len(missed)} 处（检测到图标但没人工框）")
    report.append("    " + " ".join(f"{sid}" for sid, _, _ in missed[:60]))
    report.append(f"\n[5] 重复框（同类 IoU>0.7）：{len(dup)} 处")
    (cfg.LABELS_DIR / "qa_report.txt").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))

    # 拼图：CNN 不一致 + 漏框
    from PIL import Image, ImageDraw
    from common.fonts import load_cjk_font
    font = load_cjk_font(14)

    def montage(items, path, tag):
        tiles = []
        for it in items[:args.top]:
            if tag == "dis":
                sid, b, lb, pr, conf, crop = it
                label = f"{sid} 标={lb}→判={pr} {conf:.2f}"
            else:
                sid, db, crop = it
                label = f"{sid} 未标 @{db[0]},{db[1]}"
            c = cv2.resize(crop, (72, 72))
            canv = Image.new("RGB", (72 + 200, 76), "white")
            canv.paste(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)), (2, 2))
            ImageDraw.Draw(canv).text((78, 30), label, fill=(200, 0, 0), font=font)
            tiles.append(np.array(canv))
        if not tiles:
            return
        cols = 3
        rows_ = (len(tiles) + cols - 1) // cols
        W, H = cols * 276, rows_ * 78
        out = np.full((H, W, 3), 255, np.uint8)
        for k, t in enumerate(tiles):
            r, c = divmod(k, cols)
            out[r * 78:r * 78 + 76, c * 276:c * 276 + t.shape[1]] = t
        cv2.imwrite(path, out)
        print(f"拼图 {path} ({len(tiles)} 项)")

    out = cfg.OUTPUTS_DIR / "_probe"
    out.mkdir(parents=True, exist_ok=True)
    montage(sorted(disagree, key=lambda x: -x[4]), str(out / "qa_disagree.png"), "dis")
    montage(missed, str(out / "qa_missed.png"), "miss")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
