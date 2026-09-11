"""导出 OpenAPI 契约快照：把 FastAPI 自动生成的 spec 固化成仓库里的 openapi.json。

用法（在 backend/ 目录下）::

    uv run python scripts/export_openapi.py

为什么要快照：spec 是「前后端之间的契约」的单一事实来源——前端从它生成类型（T16）、
CI 用它做破坏性变更检测（oasdiff）。代码即事实：改了路由就必须重新导出并提交快照，
CI 的漂移检查会在快照与代码不一致时挡下提交。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本无论从哪个工作目录运行都能找到包（scripts/ 在 backend/ 下）。
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))


def export(output: Path) -> None:
    """生成当前 spec 并以稳定格式写出（键排序 + 缩进 + 结尾换行，diff 才干净）。

    写文件时强制 LF 行尾（不用平台默认换行）：快照要与 CI（Linux）逐字节比对，
    Windows 上若用默认换行会造成虚假的「漂移」。
    """
    from dataset_factory.api import app

    spec = app.openapi()
    output.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    paths = len(spec.get("paths", {}))
    print(f"已导出 OpenAPI 契约：{output}（{paths} 个路径）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出 OpenAPI 契约快照")
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_DIR / "openapi.json",
        help="输出路径（默认 backend/openapi.json）",
    )
    args = parser.parse_args()
    export(args.output)
