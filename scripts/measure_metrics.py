"""重构度量脚本：把「重复实现份数 / 死代码面 / 规模 / 产物体积」变成可复跑的计数。

为什么要这件脚本：本轮重构的判据全是「改前 X 份 → 改后 Y 份」这类可数指标，靠 grep 现算
既容易每次口径不同、也没法证明「改后真的少了」。脚本固定口径，跑两次即得对照表。

用法（任意 Python 3.9+ 均可，只用标准库）：
    python scripts/measure_metrics.py                # 打 markdown 表到 stdout
    python scripts/measure_metrics.py --json out.json  # 另存一份机读结果（供 diff）

口径说明（判「死」与判「重复」都只认这里的规则，别在别处另立一套）：
- 行统计一律排除空行与纯注释行吗？不——为了与业界 LOC 工具（tokei/cloc 的 code 档）对齐成本高、
  且本项目要的是「同一口径改前改后可比」，故统一按物理行计数（含空行与注释）。
- 匹配一律用正则扫源码文本，不依赖语法树：语法树要装第三方解析器，违背「度量脚本零依赖」。
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO / "backend" / "src" / "dataset_factory"
BACKEND_TESTS = REPO / "backend" / "tests"
FRONTEND_SRC = REPO / "frontend" / "src"
DIST = REPO / "frontend" / "dist"

#: (分组, 指标名, 扫描根, 正则, 说明[, 排除 *.test.*]) —— 每行都是一个「越少越好」的可数指标。
PATTERNS: tuple[
    tuple[str, str, Path, str, str] | tuple[str, str, Path, str, str, bool], ...
] = (
    (
        "G2 重复实现",
        "api 手写异常处理器闭包",
        BACKEND_SRC / "api",
        r"^\s*def \w+\(request: Request, exc: ",
        "app.py 里逐个手写的异常处理闭包（含带 extras 的），目标收敛为表 + 工厂",
    ),
    (
        "G2 重复实现",
        "problem+json 响应脚手架",
        BACKEND_SRC / "api",
        r"application/problem\+json",
        "路由装饰器 responses= 里手写的错误形声明，两处调用点写法不一致",
    ),
    (
        "G2 重复实现",
        "wid→路径解析函数定义",
        BACKEND_SRC,
        r"def _workdir_path\(",
        "同一段「按 wid 查出工作目录路径」的定义份数",
    ),
    (
        "G2 重复实现",
        "wid→路径内联展开",
        BACKEND_SRC / "api",
        r"\.path\)",
        "在调用点内联写「注册表查出来再取 .path」的处数",
    ),
    (
        "G2 重复实现",
        "手写分块 sha256",
        BACKEND_SRC,
        r"hashlib\.sha256\(\)",
        "手写分块哈希（while/for 喂 update）的处数（同仓已有 hashlib.file_digest）",
    ),
    (
        "G2 重复实现",
        "规范 JSON 内容哈希实现",
        BACKEND_SRC,
        r"def (_sha256_canonical|strategy_content_hash)\(",
        "同一套「规范化 JSON 再哈希」的独立实现份数",
    ),
    (
        "G2 重复实现",
        "时间戳助手定义",
        BACKEND_SRC,
        r"def (_now_iso|_utc_now_iso|_utc_compact|now_iso|now_log_stamp)\(",
        "取当前 UTC 时间并格式化的助手份数（收敛后两种格式各一个，且都在 _clock 一个模块里）",
    ),
    (
        "G2 重复实现",
        "内联取当前 UTC 时间",
        BACKEND_SRC,
        r"datetime\.now\(UTC\)\.isoformat\(|datetime\.now\(timezone\.utc\)",
        "绕开 _clock 手工格式化 ISO 时间戳的处数（id 目录名的 strftime 定宽戳不在此列）",
    ),
    (
        "G2 重复实现",
        "安全路径段校验实现",
        BACKEND_SRC,
        r"Path\(\s*\w+\s*\)\.name|isidentifier\(",
        "「这个字符串必须是单个安全路径段」的各自实现——按判定形状数（比对 Path(x).name 或 isidentifier）",
    ),
    (
        "G2 重复实现",
        "安全路径段候选行（粗筛对照）",
        BACKEND_SRC,
        r"\.name != |isidentifier\(|\"/\" in |os\.sep",
        (
            "上一口径的粗筛：任何含 `.name !=` / `os.sep` 的行都算，会把「同名比对」「目录前缀包含」"
            "这类不是路径段校验的行也算进来（留作对照，不拿它当终态判据）"
        ),
    ),
    (
        "G2 重复实现",
        "CLI 绕过公共输出助手",
        BACKEND_SRC / "cli",
        r"typer\.echo\(|print\(",
        "绕开 operations.print_result / confirm_action 直接输出的处数（含合法的首行 usage，改后仍>0）",
    ),
    (
        "G2 重复实现",
        "api key 解析实现",
        BACKEND_SRC,
        r"def resolve_api_key\(|DSF_API_KEY",
        "密钥双通道解析的落点（自称单一来源却仍有分支）",
    ),
    (
        "G2 重复实现",
        "前端长任务轮询循环",
        FRONTEND_SRC,
        r"setInterval\(|setTimeout\(\s*(?:async )?\(\)\s*=>",
        "前端自己起定时器轮询任务/进度的处数（目标收敛到一个 hook）",
    ),
    (
        "G2 重复实现",
        "「上次任务已丢失」文案份数",
        FRONTEND_SRC,
        r"丢失",
        "同一句提示语在各页面手写 copies（含注释与测试，改后应显著下降）",
    ),
    (
        "G2 重复实现",
        "api 层逐字段搬运响应模型",
        BACKEND_SRC / "api",
        r"^\s+[a-z][a-z0-9_]*=\w+\.\w+,?$",
        (
            "形如「字段=域对象.属性,」的搬运行：响应契约在模型里已声明一遍，这里再抄一遍就是"
            "两处事实。同名纯搬运已改由 pydantic 按属性取值，剩下的行都是从多处现拼/请求体组装"
        ),
    ),
    (
        "G2 重复实现",
        "前端 view model mapper 函数",
        FRONTEND_SRC,
        r"^\s*(?:export )?(?:async )?function \w+\([^)]*\): (?:\w+(?:View|Model|State)|Promise<)",
        "手写「接口对象 → 视图模型」的复制字段函数份数",
    ),
    (
        "G2 重复实现",
        "裸 role=alert 行内错误",
        FRONTEND_SRC,
        r'role="alert"',
        "未走统一 <FormError> 的行内错误提示处数",
    ),
    (
        "G2 重复实现",
        "Feedback 类型副本",
        FRONTEND_SRC,
        r"(?:interface|type) Feedback\b",
        "同一个「页面反馈」类型的重复声明份数",
    ),
    (
        "G2 重复实现",
        "dialog 宽度写法",
        FRONTEND_SRC,
        r"max-w-\[?\w*\]?[^\"'`]*[^\"'`]*\"",
        "对话框宽度类名出现处数（本轮归一属像素风险，只做观测不立项）",
    ),
    (
        "G1 死代码",
        "前端 useExhaustiveDependencies 豁免",
        FRONTEND_SRC,
        r"useExhaustiveDependencies",
        "Biome 反应式依赖豁免注释处数（B4 门槛：23 → ≤5）",
    ),
    (
        "G1 死代码",
        "前端 biome-ignore 总数",
        FRONTEND_SRC,
        r"biome-ignore",
        "全部 Biome 抑制注释处数（不得新增）",
    ),
    (
        "G1 死代码",
        "后端 pyright 抑制",
        BACKEND_SRC,
        r"pyright:\s*ignore",
        "类型检查定点豁免处数（不得新增）",
    ),
    (
        "G1 死代码",
        "后端 noqa",
        BACKEND_SRC,
        r"# noqa",
        "ruff 定点豁免处数（不得新增）",
    ),
    (
        "G3 测试质量",
        "后端测试 import 私名",
        BACKEND_TESTS,
        r"from [^\s]+ import [^\n]*\b_[a-z]\w*",
        "测试伸手拿模块私名的位点（改动模块上要求归零）",
    ),
    (
        "G2 重复实现",
        "前端手工字节格式化",
        FRONTEND_SRC,
        r"/ ?1024",
        (
            "页面里现写「除以 1024 再 toFixed」的显示换算处数（收成 lib/format.ts 后应为 0）；"
            "测试里为对照而保留的旧式抄本不算份数"
        ),
        True,
    ),
    (
        "G3 测试质量",
        "后端 patch 打在模块全局名",
        BACKEND_TESTS,
        r"(?:monkeypatch\.setattr|patch\.setattr|mock\.patch|patch)\(",
        "全部 patch/setattr 注入位点（观测口径，用于看改造前后总量变化）",
    ),
    (
        "G3 测试质量",
        "后端私名 patch 位点",
        BACKEND_TESTS,
        r'setattr\([^,]+, *"_|setattr\("[^"]*\._[a-z]|patch\("[^"]*\._[a-z]',
        "patch/monkeypatch 打在 `_` 前缀私名上的位点（钉实现，重构时假红）",
    ),
    (
        "G3 测试质量",
        "前端 vi.mock api 重抄",
        FRONTEND_SRC,
        r"vi\.mock\(['\"].*api['\"]",
        "各测试文件自己重抄的 api mock（目标收敛到 test-utils）",
    ),
    (
        "G3 测试质量",
        "前端 resetAllMocks",
        FRONTEND_SRC,
        r"resetAllMocks",
        "逐文件重复的 mock 复位调用处数",
    ),
    (
        "G3 测试质量",
        "前端实现耦合断言",
        FRONTEND_SRC,
        r"querySelector\(|querySelectorAll\(|getElementsByTagName\(",
        "伸手拿 DOM 内部结构的断言处数（可访问性语义断言不算）",
    ),
    (
        "G3 测试质量",
        "后端 pytest skip 标记",
        BACKEND_TESTS,
        r"pytest\.param?sid\(|pytest\.mark\.skip|skipif|pytest\.skip",
        "跳过/条件跳过标记处数（skip 数不得增加，每条要有书面原因）",
    ),
)


def iter_files(
    root: Path, suffixes: tuple[str, ...], *, skip_tests: bool = False
) -> list[Path]:
    """按后缀收集源码文件，跳过缓存与虚拟环境目录。

    Args:
        root: 扫描根。
        suffixes: 认的后缀。
        skip_tests: 是否排除 `*.test.*`。有的指标要连测试一起看（例如「测试里重抄的 mock」），
            有的只看产码（例如「页面里现写的显示换算」——测试为对照而保留的抄本不该算份数）。
    """
    skip = {
        "__pycache__",
        ".venv",
        "node_modules",
        "dist",
        ".pytest_cache",
        ".ruff_cache",
    }
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in suffixes or not path.is_file():
            continue
        if skip & set(path.parts):
            continue
        if skip_tests and ".test." in path.name:
            continue
        out.append(path)
    return out


def count_matches(
    root: Path, pattern: str, suffixes: tuple[str, ...], *, skip_tests: bool = False
) -> tuple[int, list[str]]:
    """统计正则在一片源码里的命中行数，并回传命中位置（file:line）便于复核口径。"""
    rx = re.compile(pattern)
    total = 0
    hits: list[str] = []
    for path in iter_files(root, suffixes, skip_tests=skip_tests):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for no, line in enumerate(lines, 1):
            found = rx.findall(line)
            if found:
                total += len(found)
                hits.append(f"{rel}:{no}")
    return total, hits


def loc(files: list[Path]) -> int:
    """物理行总数（含空行与注释，口径见模块 docstring）。"""
    total = 0
    for path in files:
        try:
            total += len(path.read_text(encoding="utf-8").splitlines())
        except (UnicodeDecodeError, OSError):
            continue
    return total


def frontend_metrics() -> dict[str, object]:
    """前端规模：手写源码 / 生成类型 / 测试文件分列，避免生成代码掩盖真实变化。"""
    all_files = iter_files(FRONTEND_SRC, (".ts", ".tsx"))
    tests = [p for p in all_files if ".test." in p.name]
    generated = [p for p in all_files if p.name.endswith(".gen.ts")]
    source = [p for p in all_files if p not in tests and p not in generated]
    test_calls = 0
    for path in tests:
        text = path.read_text(encoding="utf-8")
        test_calls += len(re.findall(r"^\s*(?:it|test)\(", text, re.MULTILINE))
    return {
        "前端源文件数": len(source),
        "前端测试文件数": len(tests),
        "前端手写源码行": loc(source),
        "前端生成类型行": loc(generated),
        "前端测试用例数": test_calls,
    }


def backend_metrics() -> dict[str, object]:
    """后端规模与最大文件榜（G6 单文件 ≤700 行门槛的观测口径）。"""
    src = iter_files(BACKEND_SRC, (".py",))
    tests = iter_files(BACKEND_TESTS, (".py",))
    funcs = 0
    for path in tests:
        funcs += len(
            re.findall(
                r"^\s*(?:async )?def test_",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
    largest = sorted(
        (
            (
                len(p.read_text(encoding="utf-8").splitlines()),
                p.relative_to(REPO).as_posix(),
            )
            for p in src
        ),
        reverse=True,
    )[:8]
    return {
        "后端源文件数": len(src),
        "后端源码行": loc(src),
        "后端测试文件数": len(tests),
        "后端测试函数数": funcs,
        "后端超 700 行文件数": sum(1 for n, _ in largest if n > 700),
        "后端最大文件": [f"{name} ({lines} 行)" for lines, name in largest[:5]],
    }


def dist_metrics() -> dict[str, object]:
    """构建产物体积：原始字节 + gzip 后字节（首屏关键路径按 gzip 口径评估）。"""
    if not DIST.is_dir():
        return {"dist 状态": "未构建（先跑 npm run build 再度量）"}
    files = sorted(p for p in DIST.rglob("*") if p.is_file())
    total = 0
    gz_js = 0
    rows: list[str] = []
    for path in files:
        raw = path.read_bytes()
        total += len(raw)
        g = (
            len(gzip.compress(raw, 9))
            if path.suffix in {".js", ".css", ".html"}
            else len(raw)
        )
        if path.suffix == ".js":
            gz_js += g
        rows.append(f"{path.relative_to(DIST).as_posix()} {len(raw)} B / gz {g} B")
    return {
        "dist 文件数": len(files),
        "dist 总字节": total,
        "dist JS gzip 字节": gz_js,
        "dist 明细": rows,
    }


def ui_export_surface() -> dict[str, object]:
    """components/ui 的公共导出面（G1 门槛：19 → ≤8）与逐文件入站引用数。"""
    ui_dir = FRONTEND_SRC / "components" / "ui"
    if not ui_dir.is_dir():
        return {}
    names: list[str] = []
    for path in sorted(ui_dir.glob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"export (?:const|function|interface|type) (\w+)", text
        ):
            names.append(match.group(1))
        for match in re.finditer(r"export \{([^}]*)\}", text):
            for part in match.group(1).split(","):
                token = part.strip().split(" as ")[-1].strip()
                if token:
                    names.append(token)
    return {
        "ui 目录": sorted(ui_dir.glob("*.tsx")) and len(list(ui_dir.glob("*.tsx"))),
        "ui 导出名": sorted(set(names)),
    }


def collect() -> dict[str, object]:
    """跑全部度量，返回机读结果。"""
    metrics: dict[str, object] = {}
    metrics.update(backend_metrics())
    metrics.update(frontend_metrics())
    metrics["dist"] = dist_metrics()
    metrics["ui_surface"] = ui_export_surface()
    dup: dict[str, object] = {}
    for row in PATTERNS:
        _group, name, root, pattern, _note = row[:5]
        skip_tests = bool(row[5]) if len(row) > 5 else False
        if not root.exists():
            continue
        suffixes = (
            (".py",)
            if root.suffix == ".py" or "backend" in root.parts
            else (
                ".ts",
                ".tsx",
            )
        )
        count, hits = count_matches(root, pattern, suffixes, skip_tests=skip_tests)
        dup[name] = {"数量": count, "位置": hits[:40]}
    metrics["重复与死代码计数"] = dup
    return metrics


def as_markdown(metrics: dict[str, object]) -> str:
    """渲染成 markdown（直接贴进 review 件，避免手抄出错）。"""
    lines: list[str] = ["## 规模", ""]
    for key, val in metrics.items():
        if key in {"重复与死代码计数", "dist", "ui_surface"}:
            continue
        lines.append(f"- **{key}**：{val}")
    dist = metrics.get("dist") or {}
    if isinstance(dist, dict):
        lines += ["", "## 产物体积", ""]
        for key, val in dist.items():
            if isinstance(val, list):
                lines += [f"  - {item}" for item in val]
            else:
                lines.append(f"- **{key}**：{val}")
    surface = metrics.get("ui_surface") or {}
    if isinstance(surface, dict):
        lines += [
            "",
            "## ui 公共导出面",
            "",
            f"- 文件数：{surface.get('ui 目录')}",
            f"- 导出名：{surface.get('ui 导出名')}",
        ]
    lines += ["", "## 重复实现与死代码计数", "", "| 指标 | 数量 |", "| --- | --- |"]
    dup = metrics.get("重复与死代码计数") or {}
    if isinstance(dup, dict):
        for name, entry in dup.items():
            count = entry["数量"] if isinstance(entry, dict) else entry
            lines.append(f"| {name} | {count} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """入口：可选 --json 落盘，默认打 markdown 到 stdout。"""
    parser = argparse.ArgumentParser(description="重构度量脚本")
    parser.add_argument(
        "--json", type=Path, default=None, help="把机读结果另存到该路径"
    )
    args = parser.parse_args(argv)
    metrics = collect()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
    print(as_markdown(metrics))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
