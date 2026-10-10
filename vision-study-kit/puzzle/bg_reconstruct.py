# -*- coding: utf-8 -*-
"""背景图**还原**法求缺口（`puzzle_challenge` 的"零训练"路线）。

思路（用户提出，本模块做**算法级验证**）：
  1. 该题型的 `answerArea` 是「同一张原始背景 + 在随机 X 处挖掉一块并压暗」
  2. 若背景池不大且会复用 ⇒ 多次取题后，**同一张原始背景**会被以不同 X 挖块给出
  3. 把多张同源样本**逐像素取"未遮挡版本"**（暗槽处取其它样本的亮值）⇒ **还原出原始图**
  4. 求解时：新样本 vs 还原图做差 ⇒ 差最大的 50×50 区域即**槽位** ⇒ 得 X
  5. **0 训练、确定性、可解释**；比形状匹配更稳（形状匹配在块形状相似/多轮廓时会歧义）

本文件提供：
  · `dhash(img)`        —— 感知哈希（JPEG 噪声下仍能分组）
  · `reconstruct(imgs)` —— 逐像素还原（max / top-k 中位）
  · `locate_by_diff()`  —— 与还原图做差定位槽位
  · `--selftest`        —— **合成夹具**：用真实背景在已知 X 处挖块 → 验证还原与定位

⚠️ 合成夹具（SYNTHETIC）：只验证**算法/坐标/序列化**，不代表目标线上复用率或真实通过率。
   复用率必须用**真实采集**的多样本统计（`--collect` 见 `captcha_fetch.py`）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None

_HERE = pathlib.Path(__file__).resolve().parent
for p in (str(_HERE.parent), str(_HERE.parent / "pure")):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
def dhash(img, size=8):
    """差值哈希：返回 `size*size` 位整数（并排比较相邻像素亮度）。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = g[:, 1:] > g[:, :-1]
    bits = 0
    for v in diff.flatten():
        bits = (bits << 1) | int(v)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def group_by_background(images, max_dist=6):
    """按感知哈希分组（同源背景 → 同组）。返回 `[[idx...], ...]`。"""
    hs = [dhash(im) for im in images]
    groups = []
    for i, h in enumerate(hs):
        for g in groups:
            if hamming(h, hs[g[0]]) <= max_dist:
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def reconstruct(images, mode="top2_median"):
    """多张同源样本 → 还原原始背景。

    `mode`：
      · `max`         —— 逐像素取最大值（槽位被压暗 ⇒ 亮者为未遮挡）
      · `top2_median` —— 每像素取**第二亮**（比 max 更抗单张高光/噪点）
    """
    if not images:
        raise ValueError("需要至少 1 张图")
    stack = np.stack([im.astype(np.int16) for im in images], axis=0)   # (N,H,W,3)
    if mode == "max":
        return np.clip(stack.max(axis=0), 0, 255).astype(np.uint8)
    if mode == "top2_median":
        s = np.sort(stack, axis=0)
        n = s.shape[0]
        return np.clip(s[max(n - 2, 0)], 0, 255).astype(np.uint8)
    raise ValueError(mode)


def locate_by_diff(img, ref, tpl_size, *, y_hint=None, y_tol=3, min_gain=8.0):
    """样本 `img` 与还原图 `ref` 做差 → 槽位 X。

    `min_gain` = 槽区域内平均"提亮增益"阈值（还原图比样本亮多少）。
    返回 `{x, y, gain, per, ok, ...}`
    """
    if img.shape != ref.shape:
        raise ValueError("尺寸不一致")
    th, tw = tpl_size
    H, W = img.shape[:2]
    gi = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gr = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gain = np.clip(gr - gi, 0, None)                     # 还原图更亮的地方 = 被遮挡过
    box_k = np.ones((th, tw), np.float32) / float(th * tw)
    score = cv2.filter2D(gain, -1, box_k, anchor=(th // 2, tw // 2),
                         borderType=cv2.BORDER_CONSTANT)
    if y_hint is not None:
        y0, y1 = max(0, int(y_hint) - y_tol), min(H - 1, int(y_hint) + y_tol)
        band = np.full_like(score, -1.0)
        band[y0:y1 + 1, :] = score[y0:y1 + 1, :]
        score = band
    _, best, _, loc = cv2.minMaxLoc(score)
    x = int(loc[0] - tw // 2)
    y = int(loc[1] - th // 2)
    x = max(0, min(x, W - tw))
    y = max(0, min(y, H - th))
    out = {"x": x, "y": y, "gain": float(best), "per": round(x / W, 6),
           "width": W, "height": H, "tpl": [tw, th],
           "method": "ref-diff(brightness gain)", "ok": bool(best >= min_gain)}
    if not out["ok"]:
        out["reason"] = f"提亮增益 {best:.2f} < 阈值 {min_gain}"
    return out


def simulate_sample(bg, x, y, tpl_size, darken=0.45, seed=0):
    """**SYNTHETIC**：在 `bg` 的 (x,y) 处挖 `tpl_size` 并压暗（模拟真实槽位）。"""
    th, tw = tpl_size
    im = bg.copy()
    patch = im[y:y + th, x:x + tw].astype(np.float32)
    im[y:y + th, x:x + tw] = np.clip(patch * darken, 0, 255).astype(np.uint8)
    # 轻微 JPEG 噪声（模拟真实样本重编码）
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1.2, im.shape).astype(np.float32)
    return np.clip(im.astype(np.float32) + noise, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="背景图还原法求缺口（含 SYNTHETIC 自检）")
    ap.add_argument("--ref", help="还原图（jpg/png）")
    ap.add_argument("--img", help="待解样本（jpg/png）")
    ap.add_argument("--tpl", default="50x50", help="拼图块尺寸，如 50x50")
    ap.add_argument("--y-hint", type=int)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not (args.ref and args.img):
        ap.print_help()
        return 2
    tw, th = (int(v) for v in args.tpl.lower().split("x"))
    ref = cv2.imread(args.ref)
    img = cv2.imread(args.img)
    res = locate_by_diff(img, ref, (th, tw), y_hint=args.y_hint)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 1


def _selftest() -> int:
    ok = fail = 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  [PASS] {name} {detail}")
        else:
            fail += 1
            print(f"  [FAIL] {name} {detail}")

    print("== bg_reconstruct 自检【SYNTHETIC 夹具】==")
    check("opencv 可用", cv2 is not None)
    if cv2 is None:
        return 0
    root = pathlib.Path(__file__).resolve().parents[3]
    samp = root / "_artifacts/local_capture/puzzle_samples"
    if not samp.is_dir():
        print("  [SKIP] 无样本")
        return 0
    bgs = sorted(samp.glob("*_answerArea_*.jpg"))
    check(f"用真实背景作 SYNTHETIC 底图：{len(bgs)} 张", len(bgs) >= 1)
    if not bgs:
        return 0
    base = cv2.imread(str(bgs[0]))
    H, W = base.shape[:2]
    TPL = (50, 50)

    # 1) 感知哈希：同源（挖块位置不同）应同组
    xs = [30, 96, 162, 228]
    ys = [40, 40, 40, 40]
    synth = [simulate_sample(base, x, y, TPL, seed=i) for i, (x, y) in enumerate(zip(xs, ys))]
    groups = group_by_background(synth, max_dist=12)
    check("dhash 把同源样本分到同一组", len(groups) == 1 and len(groups[0]) == 4,
          f"组数={len(groups)} 大小={[len(g) for g in groups]}")

    # 2) 还原
    for mode in ("max", "top2_median"):
        ref = reconstruct([synth[i] for i in groups[0]], mode=mode)
        # 挖块区域（4 个 X）应被其它样本补上 ⇒ 与原始 base 的差异显著变小
        diff_raw = float(np.abs(synth[0].astype(np.int16) - base.astype(np.int16)).mean())
        diff_ref = float(np.abs(ref.astype(np.int16) - base.astype(np.int16)).mean())
        check(f"{mode}: 还原图比单张样本更接近原始（{diff_ref:.2f} < {diff_raw:.2f}）",
              diff_ref < diff_raw, f"{diff_ref:.3f} vs {diff_raw:.3f}")

    ref = reconstruct(synth, mode="top2_median")
    # 3) 定位：对每张样本用还原图求 X，应与 ground truth 一致
    for i, (x, y) in enumerate(zip(xs, ys)):
        res = locate_by_diff(synth[i], ref, TPL, y_hint=y)
        check(f"SYNTHETIC#{i}: 定位 X={res['x']}（真值 {x}）",
              abs(res["x"] - x) <= 2, f"gain={res['gain']:.2f} Δx={res['x'] - x}")
    # 4) 新样本（未参与还原）也应定位正确
    xn, yn = 190, 90
    news = simulate_sample(base, xn, yn, TPL, seed=99)
    res = locate_by_diff(news, ref, TPL)
    check(f"SYNTHETIC 新样本: 定位 X={res['x']}（真值 {xn}）", abs(res["x"] - xn) <= 3,
          f"Δx={res['x'] - xn}")

    # 5) 输出可视化（人工核对）
    vis = np.hstack([synth[0], np.full((H, 6, 3), 255, np.uint8), ref,
                     np.full((H, 6, 3), 255, np.uint8), base])
    outp = samp / "_annotated" / "reconstruct_synthetic.png"
    outp.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(outp), vis)
    check("已输出[样本|还原图|原始]对比图", outp.is_file(), str(outp))
    print("  [NOTE] 本夹具为 SYNTHETIC：只验证算法/坐标，不代表线上复用率")
    print(f"结果: PASS={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
