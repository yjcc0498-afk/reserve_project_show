# -*- coding: utf-8 -*-
"""从 `*_ques.png` 解析「目标物体 + 数量」，统计去重类别。

背景
----
每张 ques.png 是一行中文提示：
    拖动滑块出现完整的{一个|两个|三个}{物体}后就松开
其中**红色高亮**的正是「数量+物体」（一个杯子 / 三个太阳花 / 两个树袋熊 …）。
红色文字的字体是艺术字，`ddddocr` 会读错个别字（裙→?, 熊→篪, 鼠→风…），
所以这里把 **242 张的读数人工定稿**固化在 `MANUAL_LABELS` 里作为权威标签，
脚本只负责：拆分数量/物体 → 校验类别 → 写 `labels/questions.json` / `labels/classes.txt` / 统计。

辅助函数
--------
- `read_red_crop(sid)`：取出 ques 的红色文字区域（给可视化/复核用）；
- `ocr_red(sid)`：ddddocr beta 读数（新样本可用它得到候选，再据 `config.CLASSES` 纠正）。

用法（项目根目录）：
    .\\.venv\\Scripts\\python.exe tasks\\icon_task\\ques_parse.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

QJSON = cfg.LABELS_DIR / "questions.json"
CLASSES_TXT = cfg.LABELS_DIR / "classes.txt"

# 242 张 ques 的权威读数（人工逐张核对；顺序 = 样本号）
MANUAL_LABELS: dict[str, str] = {
    "0000": "两个裙子", "0001": "两个树袋熊", "0002": "一个包", "0003": "三个冰激凌",
    "0004": "两个杯子", "0005": "三个太阳花", "0006": "一个轮胎", "0007": "三个冰激凌",
    "0008": "三个马", "0009": "两个帐篷", "0010": "两个树", "0011": "两个宇航员",
    "0012": "三个帽子", "0013": "两个鸭子", "0014": "一个冰激凌", "0015": "三个鱼",
    "0016": "一个挂锁", "0017": "两个轮胎", "0018": "两个鸭子", "0019": "三个鱼",
    "0020": "一个太阳花", "0021": "一个太阳花", "0022": "一个篮子", "0023": "一个乌龟",
    "0024": "一个草莓", "0025": "一个太阳花", "0026": "两个太阳花", "0027": "两个南瓜",
    "0028": "两个蘑菇", "0029": "两个熊猫", "0030": "一个苹果", "0031": "一个房子",
    "0032": "三个房子", "0033": "一个伞", "0034": "两个鞋", "0035": "两个挂锁",
    "0036": "一个兔子", "0037": "三个苹果", "0038": "一个闹钟", "0039": "三个杯子",
    "0040": "两个树袋熊", "0041": "两个凤梨", "0042": "两个皇冠", "0043": "两个轮胎",
    "0044": "一个蘑菇", "0045": "两个瓶子", "0046": "一个太阳花", "0047": "三个树",
    "0048": "两个宇航员", "0049": "两个冰激凌", "0050": "两个挂锁", "0051": "一个闹钟",
    "0052": "两个梅子", "0053": "两个蘑菇", "0054": "一个帽子", "0055": "一个苹果",
    "0056": "一个草莓", "0057": "两个南瓜", "0058": "一个草莓", "0059": "两个南瓜",
    "0060": "一个马", "0061": "三个瓢虫", "0062": "一个树袋熊", "0063": "两个轮胎",
    "0064": "三个松鼠", "0065": "一个挂锁", "0066": "三个车", "0067": "一个鸟",
    "0068": "两个气球", "0069": "两个冰激凌", "0070": "一个蘑菇", "0071": "两个南瓜",
    "0072": "一个帽子", "0073": "一个树袋熊", "0074": "一个蘑菇", "0075": "一个鱼",
    "0076": "一个凤梨", "0077": "两个兔子", "0078": "一个足球", "0079": "一个杯子",
    "0080": "一个车", "0081": "一个冰激凌", "0082": "两个足球", "0083": "三个轮胎",
    "0084": "两个鸭子", "0085": "三个鸭子", "0086": "两个闹钟", "0087": "三个蛋糕",
    "0088": "一个耳机", "0089": "三个马", "0090": "三个鸟", "0091": "两个乌龟",
    "0092": "三个玫瑰花", "0093": "一个篮子", "0094": "一个凤梨", "0095": "一个房子",
    "0096": "三个铲土机", "0097": "一个苹果", "0098": "三个蛋糕", "0099": "一个凤梨",
    "0100": "一个气球", "0101": "两个挂锁", "0102": "一个树袋熊", "0103": "三个太阳花",
    "0104": "一个耳机", "0105": "两个汉堡包", "0106": "一个苹果", "0107": "两个轮胎",
    "0108": "一个铲土机", "0109": "一个房子", "0110": "一个鞋", "0111": "一个南瓜",
    "0112": "两个兔子", "0113": "一个耳机", "0114": "三个裙子", "0115": "两个乌龟",
    "0116": "一个篮子", "0117": "一个南瓜", "0118": "一个耳机", "0119": "三个蝴蝶",
    "0120": "一个皇冠", "0121": "一个苹果", "0122": "一个瓢虫", "0123": "三个篮子",
    "0124": "一个鸟", "0125": "一个篮子", "0126": "一个鞋", "0127": "三个房子",
    "0128": "一个包", "0129": "两个枕头", "0130": "三个伞", "0131": "一个轮胎",
    "0132": "两个玫瑰花", "0133": "两个熊猫", "0134": "一个篮子", "0135": "一个篮子",
    "0136": "一个蝴蝶", "0137": "一个枕头", "0138": "三个草莓", "0139": "两个宇航员",
    "0140": "两个苹果", "0141": "一个包", "0142": "一个鱼", "0143": "两个房子",
    "0144": "一个包", "0145": "三个耳机", "0146": "一个蘑菇", "0147": "三个南瓜",
    "0148": "两个挂锁", "0149": "三个树袋熊", "0150": "三个轮胎", "0151": "一个太阳花",
    "0152": "一个挂锁", "0153": "一个吉他", "0154": "一个鞋", "0155": "两个包",
    "0156": "三个宇航员", "0157": "一个瓢虫", "0158": "一个枕头", "0159": "一个花盆",
    "0160": "两个树", "0161": "三个铲土机", "0162": "三个玫瑰花", "0163": "一个帐篷",
    "0164": "两个鱼", "0165": "一个凤梨", "0166": "三个皇冠", "0167": "一个铲土机",
    "0168": "两个闹钟", "0169": "一个瓶子", "0170": "三个挂锁", "0171": "一个熊猫",
    "0172": "一个瓶子", "0173": "两个汉堡包", "0174": "两个树袋熊", "0175": "一个梅子",
    "0176": "两个铲土机", "0177": "一个枕头", "0178": "两个伞", "0179": "一个气球",
    "0180": "两个吉他", "0181": "三个树袋熊", "0182": "三个鸭子", "0183": "三个太阳花",
    "0184": "三个轮胎", "0185": "三个鸭子", "0186": "一个包", "0187": "三个凤梨",
    "0188": "一个蝴蝶", "0189": "两个松鼠", "0190": "三个伞", "0191": "两个耳机",
    "0192": "一个包", "0193": "两个帐篷", "0194": "三个帽子", "0195": "一个鸟",
    "0196": "两个车", "0197": "一个凤梨", "0198": "一个篮子", "0199": "两个鱼",
    "0200": "三个南瓜", "0201": "两个蛋糕", "0202": "三个鞋", "0203": "两个闹钟",
    "0204": "一个蘑菇", "0205": "三个足球", "0206": "一个凤梨", "0207": "一个枕头",
    "0208": "一个闹钟", "0209": "三个瓶子", "0210": "两个帽子", "0211": "两个南瓜",
    "0212": "一个篮子", "0213": "三个蝴蝶", "0214": "三个足球", "0215": "两个包",
    "0216": "两个鸟", "0217": "三个足球", "0218": "一个吉他", "0219": "一个瓢虫",
    "0220": "一个闹钟", "0221": "两个蛋糕", "0222": "一个伞", "0223": "两个宇航员",
    "0224": "两个鱼", "0225": "两个闹钟", "0226": "三个苹果", "0227": "两个树",
    "0228": "三个篮子", "0229": "两个帐篷", "0230": "两个大象", "0231": "两个鸟",
    "0232": "两个吉他", "0233": "两个凤梨", "0234": "两个篮子", "0235": "三个苹果",
    "0236": "两个耳机", "0237": "三个草莓", "0238": "一个花盆", "0239": "三个乌龟",
    "0240": "一个花盆", "0241": "三个篮子",
}


def red_ink(bgr: np.ndarray) -> np.ndarray:
    """红色文字墨量图（只在红色色相区域保留 r-max(g,b)）。"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hh, s, v = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    reddish = ((hh < 12) | (hh > 168)) & (s > 100) & (v > 110)
    b, g, r = bgr[:, :, 0].astype(np.int32), bgr[:, :, 1].astype(np.int32), bgr[:, :, 2].astype(np.int32)
    ink = np.clip(r - np.maximum(g, b), 0, 255).astype(np.uint8)
    ink[~reddish] = 0
    return ink


def read_ques_bgr(sid: str) -> np.ndarray | None:
    im = cv2.imread(str(cfg.ques_path(sid)), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    return cv2.cvtColor(im, cv2.COLOR_BGRA2BGR) if im.shape[2] == 4 else im


def read_red_crop(sid: str, pad: int = 3) -> np.ndarray | None:
    """取出红色文字区域（带 pad），给可视化/复核用。"""
    bgr = read_ques_bgr(sid)
    if bgr is None:
        return None
    ink = red_ink(bgr)
    ys, xs = np.where(ink > 60)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    return bgr[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]


def ocr_red(sid: str) -> str:
    """ddddocr beta 读红字（新样本可用；结果可能有错字，需据 CLASSES 纠正）。"""
    import ddddocr
    if not hasattr(ocr_red, "_o"):
        ocr_red._o = ddddocr.DdddOcr(show_ad=False, beta=True)
    crop = read_red_crop(sid)
    return "" if crop is None else ocr_red._o.classification(crop)


def split_text(text: str) -> tuple[str, int] | None:
    """`两个树袋熊` -> ('树袋熊', 2)；数量词不在表里返回 None。"""
    for w, n in cfg.COUNT_WORDS.items():
        if text.startswith(w):
            return text[len(w):], n
    return None


QLABELS_JSON = cfg.LABELS_DIR / "ques_labels.json"


def load_questions() -> dict[str, dict]:
    """优先用 `labels/ques_labels.json`（全量人工读数）；没有就退回内置的 242 张 MANUAL_LABELS。"""
    if QLABELS_JSON.exists():
        raw = json.loads(QLABELS_JSON.read_text(encoding="utf-8"))
    else:
        raw = dict(MANUAL_LABELS)
    out = {}
    for sid, text in raw.items():
        sp = split_text(text)
        if sp is None:
            continue
        obj, cnt = sp
        out[sid] = {"text": text, "object": obj, "count": cnt}
    return out


def main() -> int:
    ids = cfg.sample_ids()
    questions = load_questions()

    missing = [s for s in ids if s not in questions]
    unknown = sorted({v["object"] for v in questions.values()} - set(cfg.CLASSES))
    print(f"样本 {len(ids)}，已标注 {len(questions)}，缺 {len(missing)}")
    if missing:
        print("缺：", missing)
    if unknown:
        print("类别不在 config.CLASSES：", unknown)

    obj_count = Counter(v["object"] for v in questions.values())
    text_count = Counter(v["text"] for v in questions.values())
    print(f"唯一红字串 {len(text_count)} 个；唯一物体类别 {len(obj_count)} 个"
          f"（config.CLASSES = {len(cfg.CLASSES)}）")

    QJSON.parent.mkdir(parents=True, exist_ok=True)
    QJSON.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    # classes.txt：按出现次数降序（也可直接用 config.CLASSES 的顺序）
    classes = sorted(obj_count, key=lambda o: (-obj_count[o], o))
    CLASSES_TXT.write_text("\n".join(classes), encoding="utf-8")

    print("\n类别分布（作为目标的样本数）：")
    for o in classes:
        print(f"  {o:<6} {obj_count[o]:>3}")
    print(f"\n→ {QJSON}\n→ {CLASSES_TXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
