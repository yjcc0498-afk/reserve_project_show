# -*- coding: utf-8 -*-
"""拼图滑块**正式求解器**（还原法）：`样本 → 归组 → 与还原图比对 → x → per`。

流程（`docs/api.md` §AC）：
```
① 建库：采集到的多样本 → dhash 归组 → 每组 reconstruct(逐像素取未遮挡) → 还原图 ref
② 质检：ref 的**残留暗区占比** —— 过高说明该组样本不足以覆盖槽位 ⇒ 该组不可用（需补采样）
③ 求解：新样本 → dhash 找最近组 → locate_by_diff(样本, ref) → x
④ 输出：x（原图像素）；per 为**候选**（契约待校准，见 §AD）
```
只依赖 `opencv-python`；不发任何网络请求。
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

_HERE = pathlib.Path(__file__).resolve().parent
for p in (str(_HERE.parent), str(_HERE.parent / "pure")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bg_reconstruct as BGR   # noqa: E402
import puzzle_solver as PS     # noqa: E402


# ---------------------------------------------------------------------------
def residual_dark_ratio(ref, kernel=31, thresh=60.0):
    """还原图的**残留暗区占比**（越小越好）。

    原理：槽位在还原图里应已被补上 ⇒ 强"黑帽"响应（比邻域明显暗的局部结构）很少。
    残留多 = 该组样本未覆盖到那些位置 ⇒ 还原不完整。
    """
    g = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k).astype(np.float32)
    return float((bh > thresh).mean())


def build_references(items, *, group_key="bg_group", max_residual=0.002, verbose=False):
    """`items` = [{path, group, ...}] → `{group: {ref, residual, n, ok}}`。"""
    groups = {}
    for it in items:
        g = it.get(group_key)
        if g is None:
            continue
        groups.setdefault(g, []).append(it)
    out = {}
    for g, its in groups.items():
        imgs = []
        for it in its:
            im = cv2.imread(str(it["path"]))
            if im is not None:
                imgs.append(im)
        if not imgs:
            continue
        ref = BGR.reconstruct(imgs, mode="top2_median")
        res = residual_dark_ratio(ref)
        # ★ 相对指标：还原图的暗特征应【少于】样本中位数（消除"背景本身暗"的干扰）
        samp = float(np.median([residual_dark_ratio(im) for im in imgs]))
        gain = samp - res
        out[g] = {"ref": ref, "residual": res, "samp_median": samp, "gain": gain,
                  "n": len(imgs), "ok": bool(gain >= max_residual), "dhash": BGR.dhash(ref)}
        if verbose:
            print(f"  组{g:3d}: n={len(imgs):3d} ref暗区={res:.4f} 样本中位={samp:.4f} 增益={gain:+.4f} "
                  f"{'✅' if out[g]['ok'] else '❌'}")
    return out


def solve_sample(img, refs, *, tpl=(50, 50), y_hint=None, max_hamming=12):
    """新样本 → 归组（dhash 最近）→ ref-diff 求 x。"""
    best_g, best_d = None, 10 ** 9
    h = BGR.dhash(img)
    for g, r in refs.items():
        d = BGR.hamming(h, r["dhash"])
        if d < best_d:
            best_g, best_d = g, d
    if best_g is None or best_d > max_hamming:
        return {"ok": False, "reason": f"未找到同源还原图（最近组 {best_g} 距离 {best_d}）",
                "group": best_g, "hamming": best_d}
    r = refs[best_g]
    if not r["ok"]:
        return {"ok": False, "reason": f"组 {best_g} 还原质量不达标（增益 {r['gain']:+.4f}）",
                "group": best_g, "hamming": best_d, "gain": r["gain"]}
    loc = BGR.locate_by_diff(img, r["ref"], tpl, y_hint=y_hint)
    loc.update({"group": best_g, "hamming": best_d, "residual": r["residual"],
                "n_ref_samples": r["n"]})
    # 候选坐标契约（**未校准**，仅打印供对照）
    W = loc.get("width", img.shape[1])
    loc["per_candidates"] = {"x/320": round(loc["x"] / 320, 4),
                             "x/272": round(loc["x"] / 272, 4),
                             "(x+25)/320": round((loc["x"] + 25) / 320, 4)}
    return loc


def load_library(sample_dir, manifest_name="manifest.json"):
    """从采集目录载入 → `(items, manifest)`；`items[i]['path']` 为 answerArea 路径。"""
    d = pathlib.Path(sample_dir)
    man = json.loads((d / manifest_name).read_text(encoding="utf-8"))
    items = []
    for r in man["manifest"]:
        f = r.get("files", {}).get("answerArea") or r.get("files", {}).get("imageData")
        if not f:
            continue
        p = d / f
        if p.is_file():
            items.append({"path": p, "bg_group": r.get("bg_group"), "group": r.get("bg_group"), "i": r.get("i"),
                          "puzzleY": r.get("puzzleY"), "tag": r.get("tag"),
                          "puzzle": (d / r["files"]["puzzle"]) if r.get("files", {}).get("puzzle") else None})
    return items, man


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="拼图滑块正式求解器（还原法，离线）")
    ap.add_argument("--library", help="采集目录（含 manifest.json）")
    ap.add_argument("--sample", help="待解样本 answerArea 图片")
    ap.add_argument("--y-hint", type=int)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest(args.library or "sites/_artifacts/local_capture/collect_puzzle")
    if not args.library:
        ap.print_help()
        return 2
    items, _ = load_library(args.library)
    refs = build_references(items, verbose=True)
    if args.sample:
        img = cv2.imread(args.sample)
        print(json.dumps(solve_sample(img, refs, y_hint=args.y_hint), ensure_ascii=False, indent=1))
    return 0


def _selftest(library) -> int:
    ok = fail = 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  [PASS] {name} {detail}")
        else:
            fail += 1
            print(f"  [FAIL] {name} {detail}")

    print("== puzzle_solver_v2 自检（离线，用真实采集库）==")
    check("opencv 可用", cv2 is not None)
    if cv2 is None:
        return 0
    if not pathlib.Path(library).is_dir():
        print("  [SKIP] 无采集库", library)
        return 0
    items, man = load_library(library)
    check(f"载入样本 {len(items)}", len(items) >= 50, str(len(items)))
    refs = build_references(items, max_residual=0.02)
    good = [g for g, r in refs.items() if r["ok"]]
    check(f"还原库 {len(refs)} 组，其中合格 {len(good)} 组", len(refs) >= 20,
          f"合格={sorted(good)[:12]}")

    # 质量与样本数的关系（应正相关）
    small = [r for r in refs.values() if r["n"] <= 2]
    big = [r for r in refs.values() if r["n"] >= 9]
    if small and big:
        check(f"大组还原增益({np.mean([r['gain'] for r in big]):+.4f}) > 小组({np.mean([r['gain'] for r in small]):+.4f})",
              np.mean([r["gain"] for r in big]) > np.mean([r["gain"] for r in small]))

    # 抽样求解：对合格组内的样本，求解且与形状法对比
    solved = agree = 0
    tested = 0
    for it in items:
        g = it.get("group")
        if g not in good or it.get("puzzle") is None:
            continue
        tested += 1
        if tested > 40:
            break
        img = cv2.imread(str(it["path"]))
        r = solve_sample(img, refs, y_hint=it.get("puzzleY"))
        if r.get("ok"):
            solved += 1
            sh = PS.find_slot_x(it["path"].read_bytes(), it["puzzle"].read_bytes(), y_hint=it.get("puzzleY"))
            if abs(r["x"] - sh["x"]) <= 3:
                agree += 1
    check(f"合格组内抽样求解成功 {solved}/{min(tested, 25)}", solved >= 15, f"{solved}/{tested}")
    check(f"与形状法一致 {agree}/{max(solved,1)}", agree >= 8, f"{agree}/{solved}")

    # 输出若干还原图供人工核对
    outdir = pathlib.Path(library) / "_refs"
    outdir.mkdir(exist_ok=True)
    for g in sorted(refs, key=lambda g: -refs[g]["n"])[:5]:
        cv2.imwrite(str(outdir / f"ref_g{g}_n{refs[g]['n']}_res{refs[g]['residual']:.3f}.jpg"),
                    refs[g]["ref"])
    check("已输出前 5 组还原图供人工核对", outdir.is_dir(), str(outdir))
    print(f"结果: PASS={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
