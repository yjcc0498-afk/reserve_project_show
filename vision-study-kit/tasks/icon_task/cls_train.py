# -*- coding: utf-8 -*-
"""icon_task 图标分类器训练（CNN）。

数据：`datasets/icon_task/processed/cls/<c00..>/*.png`（`cls_dataset.py` 生成）
划分：**按图片切**（默认每 5 张留 1 张做 val，图级不泄漏）。
模型：小 CNN（默认 RGB 64×64）。
增强（本任务图标有灰度/换色/旋转/镜像变体，增强很重要）：
    旋转/缩放/平移、水平+垂直翻转、灰度化、HSV 换色、亮度对比度、模糊、遮挡、mixup。

推理提供 **TTA**（原图+水平翻转+垂直翻转，概率平均），供标注工具预标 / det_build 伪标使用。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\cls_train.py --epochs 150 [--size 64]
输出：runs/icon_task/cls/<实验名>/best.pt（含 classes/names/size）
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
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CLS_DIR = cfg.PROCESSED_DIR / "cls"
SIZE = 64            # 默认输入边长（可在 checkpoint 里带 size）


def stem_of(path: Path) -> str:
    """取所属原图号（用于图级切 train/val）。

    兼容两种切图命名：`0000__0001.png`（旧）与 `0000_221_044_284_109.png`（新）。
    """
    return cfg.sample_id(path.name)


class IconDataset(Dataset):
    def __init__(self, items, train: bool, size: int = SIZE):
        self.items = items
        self.train = train
        self.size = size

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, y = self.items[i]
        S = self.size
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            img = np.zeros((S, S, 3), np.uint8)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = cv2.resize(img, (S, S))
        if self.train:
            s = random.uniform(0.85, 1.15)
            M = cv2.getRotationMatrix2D((S / 2, S / 2), random.uniform(-20, 20), s)
            M[0, 2] += random.uniform(-4, 4)
            M[1, 2] += random.uniform(-4, 4)
            img = cv2.warpAffine(img, M, (S, S), borderMode=cv2.BORDER_REPLICATE)
            if random.random() < 0.35:
                img = img[:, ::-1]
            if random.random() < 0.15:
                img = img[::-1]
            u8 = (img * 255).astype(np.uint8)
            if random.random() < 0.3:                     # 灰度变体
                u8 = cv2.cvtColor(cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
                img = u8.astype(np.float32) / 255
            if random.random() < 0.6:                     # 换色变体
                hsv = cv2.cvtColor(u8, cv2.COLOR_RGB2HSV).astype(np.float32)
                hsv[..., 0] = (hsv[..., 0] + random.uniform(-14, 14)) % 180
                hsv[..., 1] = np.clip(hsv[..., 1] * random.uniform(0.5, 1.5), 0, 255)
                hsv[..., 2] = np.clip(hsv[..., 2] * random.uniform(0.8, 1.2), 0, 255)
                img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32) / 255
            img = np.clip(img * random.uniform(0.8, 1.25) + random.uniform(-0.1, 0.1), 0, 1)
            if random.random() < 0.5:
                img = cv2.GaussianBlur(img, (3, 3), random.uniform(0.3, 1.2))
            if random.random() < 0.3:
                x0, y0 = random.randint(0, S - 16), random.randint(0, S - 16)
                img[y0:y0 + 16, x0:x0 + 16] = random.random()
        x = torch.from_numpy(np.ascontiguousarray(img.transpose(2, 0, 1)))
        return x, y


class IconNet(nn.Module):
    def __init__(self, nclass: int):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.Conv2d(32, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
            nn.Conv2d(128, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.head = nn.Linear(128, nclass)

    def forward(self, x):
        return self.head(self.features(x).flatten(1))


# --------------------------- 推理（单模型 / 集成） ---------------------------
ENSEMBLE_JSON = cfg.RUNS_DIR / "cls" / "ensemble.json"


def load_model(run_dir: Path):
    """加载单个模型的 best.pt，返回 (net, names, size)。"""
    ck = torch.load(str(Path(run_dir) / "best.pt"), map_location="cpu")
    net = IconNet(len(ck["classes"]))
    net.load_state_dict(ck["model"])
    net.eval()
    return net, ck["names"], int(ck.get("size", SIZE))


def load_model_set(path: Path | None = None):
    """加载集成（多个模型平均）。返回 (nets, names, size)。

    path 为 ensemble.json 文件时读其中的 models 列表；否则当作单个 run 目录。
    默认：有 ensemble.json 就用集成，否则用 icon48_cnn。
    """
    if path is None:
        rel = cfg.RELEASE_DIR / "icon64_cnn_best.pt"
        if rel.exists():                       # 优先读正式模型（models/release）
            path = rel
        else:
            path = ENSEMBLE_JSON if ENSEMBLE_JSON.exists() else (cfg.RUNS_DIR / "cls" / "icon48_cnn")
    path = Path(path)
    if path.suffix == ".json" and path.exists():
        cfgj = json.loads(path.read_text(encoding="utf-8"))
        model_files = [cfg.RUNS_DIR / "cls" / n / "best.pt" for n in cfgj["models"]]
    elif path.is_file():
        model_files = [path]
    else:
        model_files = [path / "best.pt"]
    nets, names, size = [], None, SIZE
    for f in model_files:
        ck = torch.load(str(f), map_location="cpu")
        net = IconNet(len(ck["classes"]))
        net.load_state_dict(ck["model"]); net.eval()
        nets.append(net); names = ck["names"]; size = int(ck.get("size", SIZE))
    return nets, names, size


@torch.no_grad()
def predict_probs_multi(nets, crop_bgr: np.ndarray, size: int) -> np.ndarray:
    """多模型（或单模型）平均的类别概率。"""
    im = cv2.resize(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB), (size, size)).astype(np.float32) / 255
    t = torch.from_numpy(np.ascontiguousarray(im.transpose(2, 0, 1)))[None]
    ps = [torch.softmax(n(t), 1)[0].numpy() for n in nets]
    return np.mean(ps, axis=0)


@torch.no_grad()
def predict_probs(net, crop_bgr: np.ndarray, size: int, device: str = "cpu", tta: bool = True) -> np.ndarray:
    """对单个 BGR 裁剪给出类别概率（TTA：原图/水平翻转/垂直翻转/0.9 缩放 平均）。"""
    rgb0 = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    variants = [rgb0, rgb0[:, ::-1], rgb0[::-1]]
    if tta:
        h, w = rgb0.shape[:2]
        c = rgb0[int(h * 0.07):int(h * 0.93), int(w * 0.07):int(w * 0.93)]
        variants.append(c)
    batch = []
    for v in variants:
        f = cv2.resize(v, (size, size)).astype(np.float32) / 255
        batch.append(f.transpose(2, 0, 1))
    t = torch.from_numpy(np.ascontiguousarray(np.stack(batch))).to(device)
    p = torch.softmax(net(t), 1).mean(0).cpu().numpy()
    return p


def tta_label(net, names, crop_bgr, size, device="cpu"):
    p = predict_probs(net, crop_bgr, size, device)
    i = int(p.argmax())
    return names[i], float(p[i])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--size", type=int, default=SIZE)
    ap.add_argument("--mixup", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=cfg.SEED)
    ap.add_argument("--exp", default="icon48_cnn")
    ap.add_argument("--val-stems", default="")
    ap.add_argument("--holdout", type=int, default=0,
                    help=">0 时每 N 张图完全抽出做 test（不参与训练/选型），用于真实评估")
    args = ap.parse_args()

    random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"

    cls_names = json.loads((CLS_DIR / "classes.json").read_text(encoding="utf-8"))
    dirs = sorted([d for d in CLS_DIR.iterdir() if d.is_dir()])
    classes = [d.name for d in dirs]
    label_of = {c: i for i, c in enumerate(classes)}

    all_stems = sorted({stem_of(p) for d in dirs for p in d.glob("*.png")})
    test_stems: set[str] = set()
    if args.holdout and args.holdout > 1:
        test_stems = set(all_stems[::args.holdout])          # 完全留出，做独立测试集
    pool = [s for s in all_stems if s not in test_stems]
    val_stems = {s for s in args.val_stems.split(",") if s}
    if not val_stems:
        val_stems = set(pool[::5]) if len(pool) >= 5 else set(pool[:1])
    print(f"输入 {args.size}px，val 图 {len(val_stems)}，独立 test 图 {len(test_stems)}")

    tr, va, te, counts = [], [], [], {}
    for d in dirs:
        ps = sorted(d.glob("*.png"))
        counts[d.name] = len(ps)
        for p in ps:
            s = stem_of(p)
            (te if s in test_stems else (va if s in val_stems else tr)).append((p, label_of[d.name]))
    print(f"总样本 {len(tr) + len(va) + len(te)}  train {len(tr)}  val {len(va)}  test {len(te)}")

    ds_tr, ds_va = IconDataset(tr, True, args.size), IconDataset(va, False, args.size)
    cnt = np.zeros(len(classes))
    for _, y in tr:
        cnt[y] += 1
    w = 1.0 / np.maximum(cnt, 1)
    sampler = WeightedRandomSampler([w[y] for _, y in tr], len(tr), replacement=True) if len(tr) else None
    dl_tr = DataLoader(ds_tr, batch_size=args.bs, sampler=sampler, shuffle=(sampler is None), num_workers=0)
    dl_va = DataLoader(ds_va, batch_size=128, shuffle=False)

    model = IconNet(len(classes)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)

    out = cfg.RUNS_DIR / "cls" / args.exp
    out.mkdir(parents=True, exist_ok=True)
    (out / "classes.json").write_text(json.dumps(cls_names, ensure_ascii=False), encoding="utf-8")
    (out / "dirs.json").write_text(json.dumps(classes), encoding="utf-8")

    best = 0.0
    for ep in range(1, args.epochs + 1):
        model.train()
        tot = cor = 0
        for x, y in dl_tr:
            x, y = x.to(device), y.to(device)
            if args.mixup > 0 and random.random() < 0.5:
                lam = np.random.beta(args.mixup, args.mixup)
                idx = torch.randperm(x.size(0), device=device)
                x = lam * x + (1 - lam) * x[idx]
                out_ = model(x)
                loss = lam * F.cross_entropy(out_, y, label_smoothing=0.05) + \
                       (1 - lam) * F.cross_entropy(out_, y[idx], label_smoothing=0.05)
            else:
                out_ = model(x)
                loss = F.cross_entropy(out_, y, label_smoothing=0.05)
            opt.zero_grad(); loss.backward(); opt.step()
            tot += y.numel(); cor += (out_.argmax(1) == y).sum().item()
        sched.step()
        model.eval()
        vt = vc = 0
        with torch.no_grad():
            for x, y in dl_va:
                x, y = x.to(device), y.to(device)
                vt += y.numel(); vc += (model(x).argmax(1) == y).sum().item()
        acc = vc / max(vt, 1)
        if acc >= best:
            best = acc
            torch.save({"model": model.state_dict(), "classes": classes,
                        "names": cls_names, "size": args.size}, out / "best.pt")
        if ep % 10 == 0 or ep == 1:
            print(f"ep{ep:>3}  train={cor / max(tot,1):.3f}  val={acc:.3f}  best={best:.3f}")
    print(f"\n最佳 val 准确率：{best:.3f}")
    # 独立 test 集评估（真正的泛化估计）
    if te:
        model.eval()
        tt = tc = 0
        with torch.no_grad():
            for x, y in DataLoader(IconDataset(te, False, args.size), batch_size=128):
                x, y = x.to(device), y.to(device)
                tt += y.numel(); tc += (model(x).argmax(1) == y).sum().item()
        print(f"独立 test 准确率：{tc / max(tt,1):.3f}（{tt} 个裁剪，来自 {len(test_stems)} 张没训过的图）")
    print(f"模型：{out / 'best.pt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
