"""死代码扫描（入站引用口径）：把「零入站引用」证明出来，而不是 grep 出来。

为什么需要这件脚本：knip / vulture 这类工具各有口径盲区（vulture 看不见「只在 `__all__`
里转发、外部没人 import」的名字，knip 不知道「OpenAPI 里可见的模型即便只在文件内引用也活着」）。
本脚本补的是本项目特有的一类证明——**模块顶层名与包转发名到底有没有外部使用者**：把 src 里每个
模块的顶层定义扫出来，再在「除定义处以外的全部源码与测试」里找引用，零引用才算死。

四类「看起来像死的」不算死、脚本会显式跳过（口径与 goal G1 判定纪律一致）：
① 装饰器注册的（`@router.*` / `@app.command`）；② OpenAPI 里可见的 pydantic 模型；
③ 被 `__all__` 声明为公共面且确有外部使用者的；④ 模板字符串 / 动态 key 注入的前端类名。

knip / vulture / deptry 的调用不在这里——它们各自的报告与退出码由 verify 步骤清单直接跑，
本脚本只用标准库做上面这一类证明。

用法：
    python scripts/deadcode_report.py
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO / "backend" / "src" / "dataset_factory"
BACKEND_TESTS = REPO / "backend" / "tests"
OPENAPI = REPO / "backend" / "openapi.json"
SEP = "\n"


def _source_files(root: Path) -> list[Path]:
    """收集某目录下的 Python 源码文件（跳过缓存目录）。"""
    return [
        path for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts
    ]


def _openapi_model_names() -> set[str]:
    """读出契约里出现过的模型名——它们在 spec 上可见，属「活着的公共面」。"""
    if not OPENAPI.is_file():
        return set()
    spec: dict[str, object] = json.loads(OPENAPI.read_text(encoding="utf-8"))
    components = spec.get("components")
    if not isinstance(components, dict):
        return set()
    schemas = components.get("schemas")
    return set(schemas) if isinstance(schemas, dict) else set()


def _top_level_names(tree: ast.Module) -> dict[str, ast.stmt]:
    """取模块顶层定义的名字（下划线私名除外，它们本就不算公共面）。"""
    out: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                out[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    out[target.id] = node
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and not node.target.id.startswith("_")
        ):
            out[node.target.id] = node
    return out


def _decorated_public(node: ast.stmt) -> bool:
    """判断某顶层定义是否由装饰器注册进来（路由 / CLI 命令），这类不是死代码。"""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    decorators = [ast.unparse(item) for item in node.decorator_list]
    return any(
        key in name
        for name in decorators
        for key in ("router.", "app.command", "app.callback", "contextmanager")
    )


def _referenced(name: str, home: Path, corpus: dict[Path, str]) -> bool:
    """这个名字除了「自己的定义」以外还有没有人用。

    本模块内还有第二处出现（同文件其他函数在读这个常量）算有使用者；其他文件出现也算。
    只看「跨文件引用」会把「模块内自用常量」误判成死代码，这里连本文件的其余出现一起算。

    Args:
        name: 待查的顶层名。
        home: 该名字的定义文件。
        corpus: 全部待扫文件与内容。

    Returns:
        除定义本身外还有任何一处使用则返回 True。
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    for path, text in corpus.items():
        hits = len(pattern.findall(text))
        if path == home:
            # 定义处自己占一次，多出来的算本文件内的使用。
            if hits > 1:
                return True
        elif hits:
            return True
    return False


def backend_report() -> list[str]:
    """后端死代码报告：零外部引用的顶层名，与各包转发面的实际使用情况。"""
    lines: list[str] = ["## 后端：模块顶层名与包公共面的入站引用"]
    spec_models = _openapi_model_names()
    source_files = _source_files(BACKEND_SRC)
    corpus: dict[Path, str] = {
        path: path.read_text(encoding="utf-8") for path in source_files
    }
    corpus.update(
        {
            path: path.read_text(encoding="utf-8")
            for path in _source_files(BACKEND_TESTS)
        }
    )
    dead: list[str] = []
    for path in source_files:
        rel = path.relative_to(REPO).as_posix()
        for name, node in _top_level_names(ast.parse(corpus[path])).items():
            if name in spec_models or _decorated_public(node):
                continue
            if not _referenced(name, path, corpus):
                dead.append(f"{rel}: {name}")
    lines.append(f"- 顶层名零入站引用（不含装饰器注册与契约模型）：{len(dead)} 处")
    lines += [f"  - {item}" for item in dead[:80]]

    for barrel in (path for path in source_files if path.name == "__init__.py"):
        names = re.findall(r'"(\w+)"', barrel.read_text(encoding="utf-8"))
        # barrel 的 import 行与 __all__ 各占一次，故只认「本文件之外有没有人用」。
        unused = [
            name
            for name in names
            if not any(
                path != barrel and re.search(rf"\b{re.escape(name)}\b", text)
                for path, text in corpus.items()
            )
        ]
        if unused:
            rel = barrel.relative_to(REPO).as_posix()
            lines.append(
                f"- `{rel}` 转发名 {len(names)} 个，零外部引用 {len(unused)} 个：{unused}"
            )
    return lines


def main(argv: list[str]) -> int:
    """入口：打印入站引用口径的死代码报告。"""
    parser = argparse.ArgumentParser(description="死代码扫描报告（入站引用口径）")
    parser.add_argument(
        "--backend",
        action="store_true",
        help="兼容 verify 清单的参数（本脚本只看后端）",
    )
    parser.parse_args(argv)
    print(SEP.join(backend_report()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
