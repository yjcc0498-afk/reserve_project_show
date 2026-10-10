# -*- coding: utf-8 -*-
"""icon_task 任务配置：路径 / 类别 / 采样约定。

数据：`datasets/icon_task/raw/`（242 组）
    每个样本 2 张图：
      <i:04d>_scene_imageData.jpg   320×180，背景色块 + 散布图标（要识别的图）
      <i:04d>_scene_ques.png        288×30，中文提示；**红色高亮**部分是目标「数量+物体」
    外加 manifest.json（采集去重记录）。

题目模板（实测）：`拖动滑块出现完整的{一个|两个|三个}{物体}后就松开`
    → 红色高亮 = 目标物体名 + 数量；图里该物体恰好出现 count 次。

类别（34 个物体）来自对 242 张 ques 的红色区域去重统计，见 `ques_parse.py` /
`docs/icon_task_经验与卡点总结.md`。

注意：**内部类别目录一律用 ASCII（c00..c33）**，中文只用于展示，
否则 Windows 下 `cv2.imwrite` 写中文路径会**静默失败**（见 jisuan / scene 的教训）。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.paths import IMG_EXTS, PRETRAINED_DIR, task_dirs  # noqa: E402,F401

TASK = "icon_task"

# ---------------------------------------------------------------------------
# 类别：48 个物体（顺序即 ASCII 目录 c00..c47 的编号）
# 来源：对全部 ques 的红色高亮文字逐张读取 + 去重（见 ques_parse.py）。
# 注：“梅子”是误读，实为“椅子”（已合并）。
# ---------------------------------------------------------------------------
CLASSES: list[str] = [
    "裙子", "树袋熊", "包", "冰激凌", "杯子", "太阳花", "轮胎", "马", "帐篷",
    "树", "宇航员", "帽子", "鸭子", "鱼", "挂锁", "篮子", "乌龟", "草莓",
    "南瓜", "蘑菇", "熊猫", "苹果", "房子", "伞", "鞋", "兔子", "闹钟",
    "凤梨", "皇冠", "瓶子", "瓢虫", "松鼠", "车", "鸟", "气球",
    "足球", "蛋糕", "耳机", "玫瑰花", "铲土机", "蝴蝶", "吉他", "汉堡包", "枕头",
    "大象", "花盆", "企鹅", "椅子",
]

# 数量词 → 数字
COUNT_WORDS = {"一个": 1, "两个": 2, "三个": 3}

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
_D = task_dirs(TASK)

DATASET_DIR = _D["dataset"]
RAW_DIR = _D["raw"]            # 原始采集图，只增不改不删
LABELS_DIR = _D["labels"]
PROCESSED_DIR = _D["processed"]
RUNS_DIR = _D["runs"]
RELEASE_DIR = _D["release"]
OUTPUTS_DIR = _D["outputs"]

# 样本文件命名
IMAGE_SUFFIX = "_scene_imageData.jpg"   # 背景+图标
QUES_SUFFIX = "_scene_ques.png"         # 提示（红字=目标）

BASE_MODEL = PRETRAINED_DIR / "yolo11n.pt"
SEED = 42


def sample_id(path_or_name) -> str:
    """从文件名取出 4 位样本号，如 `0007_scene_imageData.jpg` -> `0007`。"""
    return Path(path_or_name).name.split("_")[0]


def image_path(sid: str) -> Path:
    return RAW_DIR / f"{sid}{IMAGE_SUFFIX}"


def ques_path(sid: str) -> Path:
    return RAW_DIR / f"{sid}{QUES_SUFFIX}"


def sample_ids() -> list[str]:
    """按编号排序的所有样本号（以 imageData 为准）。"""
    ids = [p.name.split("_")[0] for p in sorted(RAW_DIR.glob(f"*{IMAGE_SUFFIX}"))]
    return ids


# ---------------------------------------------------------------------------
# 自定义类别：标注时遇到不在上面 47 类里的图标，可在 annotate.py 里"加类"，
# 会写到 labels/custom_classes.txt；这里加载并追加到 CLASSES 末尾，
# 这样 cls_dataset / det_build / 训练也都能带上新类别（ASCII 编号 c47..）。
# ---------------------------------------------------------------------------
CUSTOM_CLASSES_FILE = LABELS_DIR / "custom_classes.txt"
if CUSTOM_CLASSES_FILE.exists():
    for _line in CUSTOM_CLASSES_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and _line not in CLASSES:
            CLASSES.append(_line)
