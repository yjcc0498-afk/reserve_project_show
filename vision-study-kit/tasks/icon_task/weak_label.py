# -*- coding: utf-8 -*-
"""弱监督自动打标：只用「每张图的目标类 + 数量」训练分类器，再给所有图标打伪标签。

原理（重要）
------------
本任务的图标**不是复用素材**（实测每个裁剪都近乎唯一，最近邻像素差最小 ~9），
所以「聚类 → 命名」那条路走不通。但每张图有一个免费监督：
    图中恰好有 count 个【目标类】的图标，其余是别的类。
把它写成「基数损失」：
    p_j = softmax(model(crop_j))[目标类]      # 第 j 个候选是目标类的概率
    L_card = (sum_j p_j - count)^2            # 总数对上
    L_conf = 熵(p)                            # 逼 p 变 0/1（只 count 个是 1）
训练后 model 就能给每个裁剪判类别。**无需任何人工框**。

产物：
    labels/boxes_auto.csv        每个检测框 + 预测类别（伪标，供 cls_dataset/det_build 用）
    outputs/.../_probe/weak_label_check.png   几张抽查图（供人工看一眼）

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\weak_label.py --epochs 60
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402
from cls_build import detect_icons  # noqa: E402
from cls_train import IconNet, SIZE  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

OUT_CSV = cfg.LABELS_DIR / "boxes_auto.csv"


def load_boxes(sid: str):
    img = cv2.imread(str(cfg.image_path(sid)))
    boxes = detect_icons(img)
    crops = []
    for x, y, w, h in boxes:
        c = img[max(0, y):y + h, max(0, x):x + w]
        c = cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2RGB), (SIZE, SIZE)).astype(np.float32) / 255
        crops.append(c)
    return img, boxes, crops


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lam", type=float, default=0.1, help="熵项权重")
    ap.add_argument("--seed", type=int, default=cfg.SEED)
    ap.add_argument("--exp", default="weak_icon48")
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"

    questions = json.loads((cfg.LABELS_DIR / "questions.json").read_text(encoding="utf-8"))
    classes = list(cfg.CLASSES)
    cidx = {c: i for i, c in enumerate(classes)}

    # 预计算
    data = []
    for sid in cfg.sample_ids():
        if sid not in questions:
            continue
        img, boxes, crops = load_boxes(sid)
        if not crops:
            continue
        q = questions[sid]
        data.append({
            "sid": sid, "boxes": boxes,
            "X": torch.from_numpy(np.ascontiguousarray(np.stack(crops).transpose(0, 3, 1, 2))),
            "c": cidx[q["object"]], "k": q["count"],
        })
    print(f"可用图片 {len(data)}（过滤掉 0 检测的）")

    model = IconNet(len(classes)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    out = cfg.RUNS_DIR / "cls" / args.exp
    out.mkdir(parents=True, exist_ok=True)

    for ep in range(1, args.epochs + 1):
        random.shuffle(data)
        model.train()
        tot_card = tot_ent = 0.0
        for rec in data:
            X = rec["X"].to(device)
            logits = model(X)
            p = torch.softmax(logits, 1)[:, rec["c"]]
            card = (p.sum() - rec["k"]) ** 2 / max(len(p), 1)
            ent = -(p * torch.log(p + 1e-6)).mean()
            loss = card + args.lam * ent
            opt.zero_grad(); loss.backward(); opt.step()
            tot_card += float(card); tot_ent += float(ent)
        sched.step()
        if ep % 5 == 0 or ep == 1:
            print(f"ep{ep:>3}  card={tot_card/len(data):.4f}  ent={tot_ent/len(data):.4f}")
    torch.save({"model": model.state_dict(), "classes": classes,
                "names": classes}, out / "best.pt")

    # 打伪标签
    model.eval()
    rows = ["image,x1,y1,x2,y2,cls"]
    n_box = 0
    for rec in data:
        with torch.no_grad():
            pr = torch.softmax(model(rec["X"].to(device)), 1).cpu().numpy()
        for (x, y, w, h), pv in zip(rec["boxes"], pr):
            rows.append(f"{rec['sid']}{cfg.IMAGE_SUFFIX},{x},{y},{x+w},{y+h},{classes[int(pv.argmax())]}")
            n_box += 1
    OUT_CSV.write_text("\n".join(rows), encoding="utf-8")
    print(f"伪标签 {n_box} 框 → {OUT_CSV}")

    # 抽查图：画框 + 预测类
    vis = []
    for rec in data[:6]:
        img = cv2.imread(str(cfg.image_path(rec["sid"])))
        with torch.no_grad():
            pr = torch.softmax(model(rec["X"].to(device)), 1).cpu().numpy()
        for (x, y, w, h), pv in zip(rec["boxes"], pr):
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 1)
        vis.append(cv2.resize(img, (480, 270)))
    grid = np.concatenate([np.concatenate(vis[:3], 1), np.concatenate(vis[3:6], 1)], 0)
    probe = cfg.OUTPUTS_DIR / "_probe"
    probe.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(probe / "weak_label_check.png"), grid)
    print(f"抽查图：{probe / 'weak_label_check.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
