"""工作区公共路径：所有任务共用，任务自己的路径在 tasks/<任务名>/config.py 里拼。

目录约定（每个任务都按这个规律放东西）：
    datasets/<task>/raw/          原始采集图（只增不改）
    datasets/<task>/labels/       标注文件（人工/传播/伪标签）
    datasets/<task>/processed/    由脚本生成的训练格式数据集（可随时删掉重建）
    runs/<task>/<method>/<exp>/   训练输出（草稿，可删）
    models/pretrained/            官方预训练权重（只读）
    models/release/<task>/        挑选出来的正式模型（推理/实战只读这里）
    outputs/<task>/               推理结果、可视化图等产物
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATASETS_DIR = PROJECT_ROOT / "datasets"
RUNS_DIR = PROJECT_ROOT / "runs"
MODELS_DIR = PROJECT_ROOT / "models"
PRETRAINED_DIR = MODELS_DIR / "pretrained"
RELEASE_DIR = MODELS_DIR / "release"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")


def task_dirs(task: str) -> dict[str, Path]:
    """返回某个任务的标准目录集合，新任务直接调用即可。"""
    ds = DATASETS_DIR / task
    return {
        "dataset": ds,
        "raw": ds / "raw",
        "labels": ds / "labels",
        "processed": ds / "processed",
        "runs": RUNS_DIR / task,
        "release": RELEASE_DIR / task,
        "outputs": OUTPUTS_DIR / task,
    }
