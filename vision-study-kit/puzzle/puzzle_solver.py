# -*- coding: utf-8 -*-
"""几何拼图滑块**缺口检测**（`puzzle_get` / `puzzle_challenge`）。

依据（实测 `docs/api.md` §J.3 + 本目录真实样本）：
  · 取题响应：`answerArea` = 背景 JPEG **320×180**（含缺口）
              `puzzle`     = 拼图块 PNG **50×50**（**带 alpha**，形状随主题变化）
              `puzzleY`    = 拼图块在背景中的 **Y 坐标（已给出）**
              `tag`        = 主题（`theme_yellow` / `plant_green` / `clothes_red` / …）
  · ⇒ 只需解 **X**；`per = x / width`（提交字段，见 `docs/api.md` §K.1）

方法（按可靠性排序）：
  1. **带掩模模板匹配**（首选）：直接用 `puzzle` 的 alpha 作 mask 在背景上匹配
     —— 本题型**同时给出块图与背景**，掩模法比纯边缘法稳健得多
  2. **边缘 + 模板匹配**（兜底）：Canny 后匹配（skill `references/recognition-and-trajectories.md`）

依赖边界：需要 `opencv-python` + `numpy`。识别结果**不自动提交**；坐标契约
（缩放/偏移/舍入）须用同版本真实提交样本另行校准（skill 明确要求）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

try:
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None

_HERE = pathlib.Path(__file__).resolve().parent
_SRC = _HERE.parent
for p in (str(_SRC), str(_SRC / "pure")):
    if p not in sys.path:
        sys.path.insert(0, p)


def decode_image(data_url_or_bytes):
    """`data:image/...;base64,...` 或原始 bytes → BGR ndarray。"""
    if cv2 is None:
        raise RuntimeError("需要 opencv-python")
    if isinstance(data_url_or_bytes, str):
        m = re.match(r"data:image/\w+;base64,(.*)$", data_url_or_bytes, re.S)
        if not m:
            raise ValueError("不是 data URL")
        import base64
        buf = np.frombuffer(base64.b64decode(m.group(1)), np.uint8)
    else:
        buf = np.frombuffer(data_url_or_bytes, np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("图片解码失败")
    return img


def split_alpha(piece):
    """拼图块 → `(bgr, alpha或None)`。"""
    if piece.ndim == 3 and piece.shape[2] == 4:
        return piece[:, :, :3], piece[:, :, 3]
    return piece, None


def _score_map(bg_bgr, tpl_bgr, mask=None):
    """返回 `(scores, method_name)`。"""
    if mask is not None:
        r = cv2.matchTemplate(bg_bgr, tpl_bgr, cv2.TM_CCORR_NORMED, mask=mask)
        # 掩模版可能含 inf/nan（全 0 掩模区），清理
        r = np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)
        return r, "TM_CCORR_NORMED+mask"
    bg_g = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2GRAY)
    tpl_g = cv2.cvtColor(tpl_bgr, cv2.COLOR_BGR2GRAY)
    bg_e = cv2.Canny(bg_g, 100, 200)
    tpl_e = cv2.Canny(tpl_g, 100, 200)
    if not np.any(bg_e) or not np.any(tpl_e):
        r = cv2.matchTemplate(bg_g, tpl_g, cv2.TM_CCOEFF_NORMED)
        return r, "TM_CCOEFF_NORMED"
    r = cv2.matchTemplate(bg_e, tpl_e, cv2.TM_CCOEFF_NORMED)
    return r, "canny+TM_CCOEFF_NORMED"


def find_gap_x(answer_area, puzzle, *, y_hint=None, y_tol=24, min_score=0.30,
               restrict_y=True):
    """求缺口 X。

    返回 dict：`{x, y, score, method, width, per, ok, reason}`
    · `x` = 拼图块左上角在背景中的像素 X（**未做任何缩放/偏移换算**）
    · `per` = `x / width`（提交候选；**契约需另行校准**）
    · `y_hint` 给定时（= 响应里的 `puzzleY`）只在 `y_hint ± y_tol` 行带内搜索
    """
    if cv2 is None:
        raise RuntimeError("需要 opencv-python")
    bg = decode_image(answer_area) if not isinstance(answer_area, np.ndarray) else answer_area
    pc = decode_image(puzzle) if not isinstance(puzzle, np.ndarray) else puzzle
    tpl, alpha = split_alpha(pc)
    H, W = bg.shape[:2]
    th, tw = tpl.shape[:2]
    if th > H or tw > W:
        return {"ok": False, "reason": "模板大于背景"}

    mask = None
    if alpha is not None:
        mask = cv2.cvtColor(alpha, cv2.COLOR_GRAY2BGR)

    results = []
    # 全局搜索
    scores, method = _score_map(bg, tpl, mask=mask)
    _, best, _, loc = cv2.minMaxLoc(scores)
    results.append((best, loc, method, None))
    # 行带搜索（若给了 y_hint）
    if restrict_y and y_hint is not None:
        y0 = max(0, int(y_hint) - y_tol)
        y1 = min(H - th, int(y_hint) + y_tol)
        if y1 >= y0:
            sub = bg[y0:y0 + th + (y1 - y0), :]
            s2, m2 = _score_map(sub, tpl, mask=mask)
            _, b2, _, l2 = cv2.minMaxLoc(s2)
            results.append((b2, (l2[0], l2[1] + y0), m2, y0))

    best_s, best_loc, best_m, band = max(results, key=lambda t: t[0])
    x, y = int(best_loc[0]), int(best_loc[1])
    out = {"x": x, "y": y, "score": float(best_s), "method": best_m,
           "width": W, "height": H, "tpl": [tw, th],
           "per": round(x / W, 6), "y_band": band,
           "ok": bool(best_s >= min_score)}
    if not out["ok"]:
        out["reason"] = f"置信度 {best_s:.4f} < 阈值 {min_score}"
    if y_hint is not None:
        out["y_vs_hint"] = y - int(y_hint)
    return out



# ---------------------------------------------------------------------------
# `puzzle_challenge` 专用：目标槽 = 「按块形状挖空并涂黑」的区域
#   ⇒ 用 **alpha 掩模 × 暗度图** 求相关，而非纹理匹配（实测本轮对比图证实）
# ---------------------------------------------------------------------------
def find_slot_x(answer_area, puzzle, *, min_score=0.12, y_hint=None, y_tol=3):
    """按「形状 × 暗度」求目标槽 X（`puzzle_challenge`）。

    原理（实测 3 样本 + 人工核对）：槽位是把拼图块的**形状挖空并压暗**，
    因此最大化 `mean(darkness inside alpha-mask)` 的位置即槽位。

    返回 `{x, y, score, per, ok, ...}`；`score` = 掩模内平均暗度（0–1，越大越像槽）。
    """
    if cv2 is None:
        raise RuntimeError("需要 opencv-python")
    bg = decode_image(answer_area)
    pc = decode_image(puzzle)
    tpl, alpha = split_alpha(pc)
    H, W = bg.shape[:2]
    th, tw = tpl.shape[:2]
    if alpha is None:
        # 无 alpha：用非白像素当掩模（兜底）
        g = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        alpha = (g < 245).astype(np.uint8) * 255
    m = (alpha > 127).astype(np.float32)
    msum = float(m.sum())
    if msum <= 0:
        return {"ok": False, "reason": "掩模为空"}
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    darkness = (255.0 - gray) / 255.0                    # 0–1
    # 用掩模对暗度做归一化相关（= 掩模内平均暗度）
    k = m / msum
    score = cv2.filter2D(darkness, -1, k[k.shape[0]::-1, k.shape[1]::-1],
                         anchor=(0, 0), borderType=cv2.BORDER_CONSTANT)
    score = score[:H - th + 1, :W - tw + 1]
    # ★ 形状相关性：把「暗区二值图」与「块掩模」做 NCC
    #   —— 背景可能有【多个】深色轮廓（草丛/四叶草/松树…），只有形状吻合的才是目标槽
    # ★ 暗区提取：优先用**黑帽形态学**（提取"比邻域更暗"的局部结构）
    #   —— 对深色底图（深红树林等）比全局阈值/ Otsu 稳健；再与全局阈值取并集兜底
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    g8 = gray.astype(np.uint8)
    blackhat = cv2.morphologyEx(g8, cv2.MORPH_BLACKHAT, k).astype(np.float32)
    thr = float(np.median(gray)) - 25.0
    global_dark = (gray < thr).astype(np.float32)
    dark = np.clip(blackhat / 40.0, 0, 1) * 0.7 + global_dark * 0.3
    mask_img = (m * 255.0).astype(np.float32)
    shape = cv2.matchTemplate(dark, mask_img, cv2.TM_CCOEFF_NORMED)
    shape = np.nan_to_num(shape, nan=-1.0, posinf=-1.0, neginf=-1.0)
    shape = shape[:H - th + 1, :W - tw + 1]
    # ★ `puzzleY` 实测就是槽位 Y（3/3 样本成立）⇒ 给定时只在 y_hint±y_tol 行带内取最优
    if y_hint is not None:
        y0 = max(0, int(y_hint) - y_tol)
        y1 = min(shape.shape[0] - 1, int(y_hint) + y_tol)
        if y1 >= y0:
            band = np.full_like(shape, -1.0)
            band[y0:y1 + 1, :] = shape[y0:y1 + 1, :]
            shape = band
    # 综合分：形状相关性为主，掩模内暗度为辅
    comb = shape * 0.8 + score * 0.2
    _, _, _, loc = cv2.minMaxLoc(comb)
    x, y = int(loc[0]), int(loc[1])
    out = {"x": x, "y": y, "score": float(score[y, x]), "shape": float(shape[y, x]),
           "comb": float(comb[y, x]),
           "method": "shape-NCC(dark x alpha)", "width": W, "height": H,
           "tpl": [tw, th], "per": round(x / W, 6),
           "ok": bool(shape[y, x] >= min_score)}
    if not out["ok"]:
        out["reason"] = f"形状相关 {out['shape']:.4f} < 阈值 {min_score}"
    return out


def annotate(answer_area, puzzle, res, out_path):
    """把检测结果画到背景上，便于人工核对。"""
    bg = decode_image(answer_area) if not isinstance(answer_area, np.ndarray) else answer_area.copy()
    pc = decode_image(puzzle) if not isinstance(puzzle, np.ndarray) else puzzle
    tpl, alpha = split_alpha(pc)
    th, tw = tpl.shape[:2]
    x, y = res["x"], res["y"]
    cv2.rectangle(bg, (x, y), (x + tw, y + th), (0, 0, 255), 2)
    cv2.putText(bg, f"x={x} per={res['per']} s={res['score']:.3f}",
                (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), bg)
    return out_path


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="几何拼图滑块缺口检测")
    ap.add_argument("--bg", help="背景图（jpg/png 或 data URL 文件）")
    ap.add_argument("--piece", help="拼图块（png，带 alpha）")
    ap.add_argument("--y-hint", type=int, help="响应里的 puzzleY")
    ap.add_argument("--annotate", help="把结果画到背景并写出该路径")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not (args.bg and args.piece):
        ap.print_help()
        return 2
    bg = pathlib.Path(args.bg).read_bytes()
    pc = pathlib.Path(args.piece).read_bytes()
    res = find_gap_x(bg, pc, y_hint=args.y_hint)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if args.annotate:
        annotate(bg, pc, res, args.annotate)
        print("已标注 ->", args.annotate)
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

    print("== puzzle_solver 自检（真实样本，离线）==")
    check("opencv 可用", cv2 is not None)
    if cv2 is None:
        return 0
    root = pathlib.Path(__file__).resolve().parents[3]      # sites/
    samp = root / "_artifacts/local_capture/puzzle_samples"
    if not samp.is_dir():
        print("  [SKIP] 无 puzzle_samples")
        return 0
    files = sorted(samp.glob("*_answerArea_*.jpg"))
    check(f"真实样本 {len(files)} 组", len(files) >= 3, str(len(files)))
    rows = []
    for bgp in files:
        y = int(bgp.stem.rsplit("_", 1)[1])
        tag = bgp.stem.split("_", 1)[1].rsplit("_answerArea", 1)[0]
        pcp = list(samp.glob(f"*_{tag}_puzzle_{y}.png"))
        if not pcp:
            continue
        res = find_slot_x(bgp.read_bytes(), pcp[0].read_bytes(), y_hint=y)
        rows.append((tag, y, res))
        check(f"{tag}: 检出缺口 X={res.get('x')} 置信度 {res.get('score'):.3f}",
              res.get("ok") is True, f"method={res.get('method')}")
        check(f"{tag}: X 落在 0..{res.get('width', 0) - res.get('tpl', [0])[0]}",
              0 <= res.get("x", -1) <= res.get("width", 0) - res.get("tpl", [0])[0])
        check(f"{tag}: 形状相关 > 0.05", res.get("shape", 0) > 0.05, f"shape={res.get('shape'):.3f}")
        check(f"{tag}: 检出 Y ≈ puzzleY（|Δy|<=2）", abs(res.get("y", 999) - y) <= 2,
              f"y={res.get('y')} vs puzzleY={y}")
    if rows:
        # 标注输出（人工核对用）
        outp = samp / "_annotated"
        for tag, y, res in rows:
            bgp = list(samp.glob(f"*_{tag}_answerArea_{y}.jpg"))[0]
            pcp = list(samp.glob(f"*_{tag}_puzzle_{y}.png"))[0]
            annotate(bgp.read_bytes(), pcp.read_bytes(), res, outp / f"{tag}_{y}.jpg")
        check("已输出标注图供人工核对", outp.is_dir(), str(outp))
    print(f"结果: PASS={ok} FAIL={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
