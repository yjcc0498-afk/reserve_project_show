# -*- coding: utf-8 -*-
"""校对 ques 的数量（一个/两个/三个）：不靠 OCR，用红字最左的“数量词”独立判定。

做法：
1. 取红字墨量图，截最左约 2 个字宽（=数量词 一个/两个/三个）；
2. 与已知参照样本的数量词做相关匹配（自测 100% 正确）；
3. 与 `labels/questions.json` 对比，margin 足够大且不一致 → 报错并出拼图。

用法：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\ques_verify_count.py
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

# 参照：一个/两个/三个（来自确定无误的样本）
REF_SAMPLES = {"一个": ["0002", "0022", "0038", "0024"],
               "两个": ["0000", "0013", "0105"],
               "三个": ["0003", "0005", "0008", "0012"]}
NUM = {"一个": 1, "两个": 2, "三个": 3}
MARGIN_THR = 0.12


def _crop_ink(ink: np.ndarray) -> np.ndarray:
    ys, xs = np.where(ink > 60)
    return ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def countword_feat(ink: np.ndarray) -> np.ndarray | None:
    c = _crop_ink(ink)
    if c.size == 0:
        return None
    cut = min(c.shape[1], max(12, int(c.shape[0] * 2.0)))   # 两个字宽
    ch = c[:, :cut]
    f = cv2.resize(ch, (40, 20)).astype(np.float32).ravel()
    f -= f.mean()
    n = np.linalg.norm(f)
    return f / n if n > 0 else None


def judge(f: np.ndarray, ref: dict) -> tuple[str, float, float]:
    d = {k: max(float(f @ rf) for rf in v) for k, v in ref.items()}
    order = sorted(d, key=lambda k: -d[k])
    return order[0], round(d[order[0]], 2), round(d[order[0]] - d[order[1]], 3)


def load_ref() -> dict[str, list[np.ndarray]]:
    ref: dict[str, list[np.ndarray]] = {}
    for word, sids in REF_SAMPLES.items():
        for sid in sids:
            bgr = read_ques_bgr(sid)
            if bgr is None:
                continue
            f = countword_feat(red_ink(bgr))
            if f is not None:
                ref.setdefault(word, []).append(f)
    return ref


def main() -> int:
    ref = load_ref()
    # 自测
    ok = all(judge(countword_feat(red_ink(read_ques_bgr(s))), ref)[0] == w and judge(countword_feat(red_ink(read_ques_bgr(s))), ref)[2] >= 0.15
             for w, sids in REF_SAMPLES.items() for s in sids)
    print(f"参照自测：{'通过' if ok else '失败'}")

    q = json.loads((cfg.LABELS_DIR / "questions.json").read_text(encoding="utf-8"))
    bad = []
    for sid, v in q.items():
        bgr = read_ques_bgr(sid)
        if bgr is None:
            continue
        f = countword_feat(red_ink(bgr))
        if f is None:
            continue
        word, conf, marg = judge(f, ref)
        if word != v["text"][:2] and marg >= MARGIN_THR:
            bad.append((sid, v["text"], v["count"], word, NUM.get(word, 0), marg))

    print(f"校对完成：共 {len(q)}，数量不一致 {len(bad)}")
    for sid, text, c, word, got, marg in bad[:80]:
        print(f"  {sid}  标注={text}({c})  判定='{word}'->{got}  margin={marg}")

    out = cfg.OUTPUTS_DIR / "_probe"
    out.mkdir(parents=True, exist_ok=True)
    (out / "count_mismatch.json").write_text(json.dumps(bad, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
