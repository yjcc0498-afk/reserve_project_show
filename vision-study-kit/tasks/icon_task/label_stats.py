# -*- coding: utf-8 -*-
"""标注统计：每个类别已标多少框 / 覆盖情况，并列出「样本不足」类别的目标图，便于补标。

- 框按类别统计（来自 `labels/boxes.csv`，人工标注）；
- 「目标图」来自 `labels/questions.json`（该图题目要找这个类，必然含该类）；
- 不足阈值默认 8，可用 --min 调整。

用法（项目根目录）：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\label_stats.py [--min 8]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def image_status(name: str, boxes: dict, questions: dict) -> str:
    """none=未标 / part=部分（有框没类别，或目标数量对不上）/ done=已标。"""
    b = boxes.get(name, [])
    if not b:
        return "none"
    if any(not x[4] for x in b):
        return "part"
    sid = cfg.sample_id(name)
    q = questions.get(sid)
    if q:
        got = sum(1 for x in b if x[4] == q["object"])
        if got != q["count"]:
            return "part"
    return "done"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=8, help="每类期望的最少框数（低于则提示补标）")
    args = ap.parse_args()

    rows = list(csv.DictReader((cfg.LABELS_DIR / "boxes.csv").open(encoding="utf-8")))
    boxes: dict[str, list] = defaultdict(list)
    for r in rows:
        boxes[r["image"]].append([r["x1"], r["y1"], r["x2"], r["y2"], r.get("cls", "")])
    questions = json.loads((cfg.LABELS_DIR / "questions.json").read_text(encoding="utf-8"))

    cnt = Counter(r["cls"] for r in rows if r["cls"])
    bycls = defaultdict(list)
    for sid, v in questions.items():
        bycls[v["object"]].append(sid)

    print(f"已标注 {sum(cnt.values())} 框 / {len(boxes)} 图；共 {len(cfg.CLASSES)} 类")
    low = [c for c in cfg.CLASSES if cnt.get(c, 0) < args.min]
    print(f"低于 {args.min} 框的类别 {len(low)} 个；覆盖 {len([c for c in cfg.CLASSES if cnt.get(c, 0) > 0])}/{len(cfg.CLASSES)}")

    print("\n各类框数（升序，方便先补少的）：")
    for c in sorted(cfg.CLASSES, key=lambda c: cnt.get(c, 0)):
        mark = "  <== 需补" if cnt.get(c, 0) < args.min else ""
        print(f"  {c:<6} {cnt.get(c, 0):>4}{mark}")

    print(f"\n需补标的类别的「目标图」（题目要找它，必然含；去标这些图）：")
    for c in low:
        ids = sorted(bycls.get(c, []))
        print(f"\n【{c}】目标图 {len(ids)} 张，需再补约 {max(0, args.min - cnt.get(c, 0))} 框：")
        for i in range(0, len(ids), 14):
            print("   " + " ".join(ids[i:i + 14]))

    st = Counter(image_status(n.name, boxes, questions) for n in cfg.RAW_DIR.glob(f"*{cfg.IMAGE_SUFFIX}"))
    print(f"\n图片状态：未标 {st.get('none',0)} / 部分 {st.get('part',0)} / 已标 {st.get('done',0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
