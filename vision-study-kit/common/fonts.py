"""跨任务的 PIL 中文字体加载。

背景：PIL 默认字体（`ImageFont.load_default()`）**不支持中文**，
用它 `ImageDraw.text("原图")` 会画成一堆方块/乱码。
所有需要往图上写中文的脚本都应该用这里的 `load_cjk_font()`。
"""

from __future__ import annotations

import os
from functools import lru_cache

from PIL import ImageFont

# 按优先级尝试；Windows 在前，Linux 兜底
_FONT_CANDIDATES = [
    r"LOCAL_WORKSPACE",      # 微软雅黑（Win 7+ 一般都有）
    r"LOCAL_WORKSPACE",
    r"LOCAL_WORKSPACE",    # 黑体
    r"LOCAL_WORKSPACE",    # 宋体
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


@lru_cache(maxsize=32)
def load_cjk_font(size: int = 14) -> ImageFont.FreeTypeFont:
    """返回一个能画中文的 PIL 字体；找不到就用默认字体兜底。"""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()
