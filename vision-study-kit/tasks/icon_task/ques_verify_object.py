# -*- coding: utf-8 -*-
"""校验 ques 的“物体”是否标错：用红字自洽性（不靠 OCR）。

原理：同一串（如“两个裙子”）的多张红字渲染几乎一致。对某样本，若它与
**其它同类样本**的最大相关，反而低于与**别的类**的最大相关，就说明它的
标注对象很可能错了 —— 会挑出来 + 出拼图。

背景：raw/ 里的图会被替换，替换后同一编号的题目可能变了，旧标签需要复核。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\ques_verify_object.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402
from ques_parse import read_ques_bgr, red_ink  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

Hn, Wn = 22, 96
MARGIN_THR = 0.05


def feat(sid: str):
    b = read_ques_bgr(sid)
    if b is None:
        return None
    ink = red_ink(b)
    ys, xs = np.where(ink > 60)
    if len(xs) == 0:
        return None
    c = ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    f = cv2.resize(c, (Wn, Hn)).astype(np.float32).ravel()
    f -= f.mean()
    n = np.linalg.norm(f)
    return f / n if n > 0 else None


def main() -> int:
    ref = json.loads((cfg.LABELS_DIR / "ques_labels.json").read_text(encoding="utf-8"))
    ids = sorted(ref)
    F = np.array([feat(s) if feat(s) is not None else np.zeros(Wn * Hn, np.float32) for s in ids])
    ok = np.array([feat(s) is not None for s in ids])
    texts = sorted(set(ref.values()))
    tidx = {t: i for i, t in enumerate(texts)}
    lab = np.array([tidx[ref[s]] for s in ids])

    bad = []
    for k, s in enumerate(ids):
        if not ok[k]:
            continue
        sc = F @ F[k]
        per_first, per_second = {}, {}
        for ti in range(len(texts)):
            vals = sorted((float(sc[j]) for j in range(len(ids))
                           if lab[j] == ti and ids[j] != s), reverse=True)
            if vals:
                per_first[ti] = vals[0]
                per_second[ti] = vals[1] if len(vals) > 1 else -9
        if not per_first:
            continue
        order = sorted(per_first, key=lambda ti: -per_first[ti])
        top = order[0]
        second = order[1] if len(order) > 1 else None
        if texts[top] != ref[s] and (second is None or per_first[top] - per_first[second] >= MARGIN_THR):
            bad.append((s, ref[s], texts[top], round(per_first[top] - (per_first[second] if second else -1), 3)))

    print(f"物体自洽性检查：共 {len(ids)}，疑似标错 {len(bad)}")
    for s, a, b, m in bad:
        print(f"  {s}  标注={a}  实际像是={b}  margin={m}")
    out = cfg.OUTPUTS_DIR / "_probe"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ques_object_inconsistent.json").write_text(
        json.dumps(bad, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
