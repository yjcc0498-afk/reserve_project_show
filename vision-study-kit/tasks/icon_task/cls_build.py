# -*- coding: utf-8 -*-
"""图标检测 + 素材聚类（CNN / YOLO 方案的第 1 步）。

背景
----
`*_imageData.jpg`（320×180）＝ 平滑色块背景 + 若干**贴图素材图标**。
背景（含点状纹理）局部细节少，图标细节密 → 用「局部细节密度」找图标。

流程
----
1. `detect_icons(img)`：局部细节图 → 密度阈值 → 连通域 → 合并重叠 → bbox；
2. 对所有图的所有候选裁剪做 48×48 特征贪心聚类（同一素材图标几乎相同）；
3. 输出编号拼图 `outputs/_probe/icon_clusters.png`（人工命名用）；
4. 人工读名后写入 `labels/icon_clusters.json`，供 `cls_dataset.py` / `det_build.py` 用。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\cls_build.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CLUSTER_THR = 22.0     # 两个 48×48 裁剪的平均像素差阈值（越小越严格）
FEAT = 48
DENS_K = 21            # 局部细节密度的窗口
DENS_THR = 0.42        # 密度阈值（图标内部细节密）
DETAIL_THR = 30        # 单像素“有细节”的阈值（|原图-中值| 之和）
# 多组参数取并集（召回优先）
DET_SETTINGS = [(0.42, 30, 21), (0.34, 22, 15), (0.30, 18, 13)]
# 在“去竖条纹”后的图上再跑一组（专门对付白底/灰底细竖线背景）
DESTRIPE_SETTINGS = [(0.42, 30, 21), (0.30, 18, 13)]


def _destripe(bgr: np.ndarray) -> np.ndarray:
    """去掉细竖线背景（形态学开运算提取竖直结构再减去），保留图标。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    vert = cv2.morphologyEx(gray, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, 21)))
    clean = cv2.subtract(gray, vert // 2)
    return cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR)


def detail_map(bgr: np.ndarray, k: int = 9) -> np.ndarray:
    """局部细节强度：与原图中值滤波的差（背景平滑 → 低；图标/纹理 → 高）。"""
    med = cv2.medianBlur(bgr, k)
    d = np.abs(bgr.astype(np.int32) - med.astype(np.int32)).sum(2)
    return np.clip(d, 0, 255).astype(np.uint8)


def detect_icons(bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
    """返回图标 bbox 列表 (x, y, w, h)。

    用**多组参数取并集**（召回优先，允许误检）：单组参数召回仅 ~73%，
    三组并集后可达 ~90%（多出的框大多是未标过的真图标，可让标注者顺手标）。
    """
    boxes: list[list[int]] = []
    for dens_thr, det_thr, k in DET_SETTINGS:
        boxes += _detect_once(bgr, dens_thr, det_thr, k)
    dst = _destripe(bgr)                               # 去竖条纹后再跑一遍
    for dens_thr, det_thr, k in DESTRIPE_SETTINGS:
        boxes += _detect_once(dst, dens_thr, det_thr, k)
    return _nms(boxes, 0.4)


def _detect_once(bgr: np.ndarray, dens_thr: float, det_thr: int, k: int) -> list[list[int]]:
    det = (detail_map(bgr) > det_thr).astype(np.float32)
    dens = cv2.blur(det, (k, k))
    blobs = (dens > dens_thr).astype(np.uint8)
    blobs = cv2.morphologyEx(blobs, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    n, _, st, _ = cv2.connectedComponentsWithStats(blobs, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = st[i]
        if area < 90 or bw < 16 or bh < 16 or bw > 220 or bh > 200:
            continue
        if not (0.28 <= bw / bh <= 3.6):
            continue
        out.append([int(x), int(y), int(x + bw), int(y + bh)])
    return out


def _iou_xyxy(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1, aa + bb - inter)


def _nms(boxes: list[list[int]], thr: float) -> list[tuple[int, int, int, int]]:
    boxes = sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
    keep: list[list[int]] = []
    for b in boxes:
        if all(_iou_xyxy(b, k) <= thr for k in keep):
            keep.append(b)
    keep.sort()
    return [(x1, y1, x2 - x1, y2 - y1) for x1, y1, x2, y2 in keep]


def main() -> int:
    ids = cfg.sample_ids()
    crops_dir = cfg.PROCESSED_DIR / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    feats, metas = [], []
    for sid in ids:
        img = cv2.imread(str(cfg.image_path(sid)))
        if img is None:
            continue
        for i, (x, y, w, h) in enumerate(detect_icons(img)):
            crop = img[y:y + h, x:x + w]
            f = cv2.resize(crop, (FEAT, FEAT)).astype(np.float32)
            feats.append(f)
            metas.append({"sid": sid, "i": i, "xywh": [x, y, w, h]})

    # 贪心聚类
    centers, members = [], []
    for idx, fi in enumerate(feats):
        best, bestd = -1, 1e9
        for ci, cen in enumerate(centers):
            d = float(np.abs(fi - cen).mean())
            if d < bestd:
                best, bestd = ci, d
        if best >= 0 and bestd < CLUSTER_THR:
            members[best].append(idx)
            centers[best] = centers[best] * 0.9 + fi * 0.1
        else:
            centers.append(fi.copy())
            members.append([idx])

    # 保存裁剪 + 簇
    json.dump([{"sid": m["sid"], "i": m["i"], "xywh": m["xywh"]} for m in metas],
              (cfg.PROCESSED_DIR / "crops.json").open("w", encoding="utf-8"),
              ensure_ascii=False)
    order = sorted(range(len(members)), key=lambda c: -len(members[c]))
    lines = []
    for pos, cid in enumerate(order):
        lines.append(f"[{pos:>2}] cid={cid:>3} 成员={len(members[cid]):>4}  "
                     f"样例={metas[members[cid][0]]['sid']}")

    # 编号拼图（前 80 簇，每簇给代表 + 2 个其它成员）
    top = order[:80]
    cell = 96
    cols = 8
    rows = (len(top) + cols - 1) // cols
    grid = np.full((rows * (cell + 16), cols * cell, 3), 255, np.uint8)
    for pos, cid in enumerate(top):
        mem = members[cid]
        r, c = pos // cols, pos % cols
        reps = mem[:3] if len(mem) >= 3 else mem
        for j, idx in enumerate(reps):
            m = metas[idx]
            img = cv2.imread(str(cfg.image_path(m["sid"])))
            x, y, w, h = m["xywh"]
            crop = cv2.resize(img[y:y + h, x:x + w], (46, 46))
            gx = c * cell + 2 + (j % 2) * 46
            gy = r * (cell + 16) + 16 + (j // 2) * 46
            grid[gy:gy + 46, gx:gx + 46] = crop
        cv2.putText(grid, f"{pos}", (c * cell + 4, r * (cell + 16) + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 200), 1)
        cv2.putText(grid, f"n{len(mem)}", (c * cell + 34, r * (cell + 16) + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 120, 0), 1)
    out = cfg.OUTPUTS_DIR / "_probe"
    out.mkdir(parents=True, exist_ok=True)
    grid = cv2.resize(grid, (grid.shape[1] * 2, grid.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out / "icon_clusters.png"), grid)
    (out / "icon_clusters.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"候选裁剪 {len(feats)} 张 → {len(members)} 个簇（阈值 {CLUSTER_THR}）")
    print(f"编号拼图：{out / 'icon_clusters.png'}")
    print("\n".join(lines[:25]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
