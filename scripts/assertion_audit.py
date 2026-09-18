"""断言有效性审计（G3 的计数口径）：四类可数的嫌疑断言，脚本列候选、人工逐条定性。

为什么要这件：goal 的 G3 判据是「永真断言 = 0、断内部 tag/样式类/常量透传的用例归零、
mock 面 ≤ 真实面」。这三条只有全仓扫过才数得清，人肉抽查必漏。首版只用「字面串在源码里找不
到」判永真，一跑就 70+ 条——绝大多数是 ``策略${i} · ${s.id}`` 这类拼接出来的标签，字面串本
来就不存在、断言却货真价实，误报到没法用。所以比对改成先抽骨架（挖掉数字与模板插值），并加了
对抗性自检：往 frontend/src 塞一个故意写坏目标名的 canary 文件，脚本必须点出来；点不出来说明
脚本自己失效了（第一版就是这么被 canary 抓到两处口径 bug）。

四类口径：
- 缺席断言的目标造不出来：``queryBy*`` + 「不在文档里」这一类，断言的文字在非测试源码骨架里
  根本产不出来——「它不出现」就不是这段代码保证的，纯永真。正向查询（``getBy*``）不判：查不
  到就直接抛，套件全绿已经证明目标出现过。
- 形状恒真的断言：``expect(true).toBe(true)``、查完又断 ``toBeDefined()`` 这类怎么写都真。
- 实现细节耦合断言：断样式类、标签名、内部 data-* 状态位——这些是「怎么实现」不是「用户看到
  什么」，重构一碰就红、且红得没有信息量。data-testid 与 aria-* 属于对外契约，不算。
- mock 面大于真实面：``vi.mock("...api")`` 工厂里挂了真实 ``api.ts`` 没有的方法，测试就在跟
  一个不存在的接口对话。

用法：
    python scripts/assertion_audit.py            # 只给计数 + 前 20 条
    python scripts/assertion_audit.py --detail   # 展开全部候选明细
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRONTEND_SRC = REPO / "frontend" / "src"
QUOTE = chr(34)

QUERY_HEAD_RE = re.compile(
    "(get|query)(?:All)?By(?:Role|Text|LabelText|PlaceholderText|TestId)\\s*\\("
)
NAME_RE = re.compile("name:" + QUOTE + "([^" + QUOTE + "]+)" + QUOTE)
STR_RE = re.compile(QUOTE + "([^" + QUOTE + "]{2,})" + QUOTE)
TEMPLATE_HOLE_RE = re.compile(r"\$\{[^{}]*}")
DIGITS_RE = re.compile(r"\d+")
TOKEN_SPLIT_RE = re.compile(
    "[^0-9A-Za-z\\u4e00-\\u9fff]+"
)  # 词边界：非字母数字汉字都算分隔
MOCK_API_RE = re.compile(
    "vi\\.mock\\(\\s*"
    + QUOTE
    + "[^"
    + QUOTE
    + "]*api[^"
    + QUOTE
    + "]*"
    + QUOTE
    + "\\s*,"
)
MOCKED_KEY_RE = re.compile(r"^\s*(\w+)\s*:\s*(?:vi\.fn|async)", re.MULTILINE)
API_METHOD_RE = re.compile(r"^  (?:async )?(\w+)\(", re.MULTILINE)

COUPLED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("断样式类 toHaveClass", re.compile(r"\.toHaveClass\(")),
    ("断 className 字符串", re.compile(r"\.className")),
    ("断 classList", re.compile(r"classList\.")),
    (
        "断内部 data-* 状态位",
        re.compile('getAttribute\\(\\s*"' + "data-(?!testid)" + r'[^"]*"'),
    ),
    ("断标签名 tagName", re.compile(r"\.tagName")),
)

TAUTOLOGY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "断字面量恒真",
        re.compile(r"expect\(true\)\.toBe\(true\)"),
    ),
    (
        "正向查询后断 toBeDefined/toBeTruthy（查询查不到就抛，断言恒真）",
        re.compile(
            r"By(?:Role|Text|LabelText|PlaceholderText|TestId)\(.*\)\)\.(?:toBeDefined|toBeTruthy)\(\)"
        ),
    ),
)

ROLES = {
    "button",
    "checkbox",
    "columnheader",
    "combobox",
    "dialog",
    "grid",
    "gridcell",
    "group",
    "heading",
    "label",
    "legend",
    "link",
    "list",
    "listbox",
    "listitem",
    "menu",
    "menuitem",
    "menubar",
    "meter",
    "navigation",
    "option",
    "progressbar",
    "radio",
    "radiogroup",
    "region",
    "row",
    "rowgroup",
    "rowheader",
    "scrollbar",
    "separator",
    "slider",
    "spinbutton",
    "status",
    "switch",
    "tab",
    "table",
    "tablist",
    "term",
    "textbox",
    "timer",
    "toolbar",
    "tooltip",
    "tree",
    "treegrid",
    "treeitem",
    "menuitemcheckbox",
    "menuitemradio",
    "searchbox",
    "banner",
    "contentinfo",
    "form",
    "complementary",
    "main",
    "article",
    "document",
}


def skeleton(text: str) -> str:
    """挖掉数字与模板插值后的骨架，让拼接标签和它的模板能对上。"""
    return DIGITS_RE.sub("", TEMPLATE_HOLE_RE.sub("", text))


def explainable(target: str, haystack: str) -> bool:
    """判断断言文字能不能由「源码模板 + 假数据」拼出来。

    整段骨架比对对 ``${name} · ${id}`` 这类跨源拼接无能为力（源码里只有 `` · ``，名字来自假
    数据），所以再按分隔符切成词逐一看：每个有实义的词都各有出处，就算能产出。代价是漏报优于
    误报——只把「整词哪儿都没有」的留给人工看。
    """
    frame = skeleton(target)
    if len(frame) < 2:
        return True
    if frame in haystack:
        return True
    tokens = [token for token in TOKEN_SPLIT_RE.split(frame) if len(token) >= 2]
    return bool(tokens) and all(token in haystack for token in tokens)


def test_files() -> list[Path]:
    """全部前端测试文件。"""
    return sorted(FRONTEND_SRC.rglob("*.test.ts*"))


def prod_text() -> str:
    """非测试源码拼一份，用来判「这个界面文字到底造不造得出来」。"""
    parts: list[str] = []
    for path in FRONTEND_SRC.rglob("*.ts*"):
        if ".test." in path.name or path.name.endswith(".gen.ts"):
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def targets_of(blob: str) -> list[str]:
    """取一次查询里真正要匹配的界面文字。"""
    stripped = blob.strip()
    if not stripped:
        return []
    first = stripped.splitlines()[0].strip().strip(QUOTE)
    if first in ROLES and "name:" not in stripped:
        return []
    named = NAME_RE.findall(stripped)
    if named:
        return named
    return [item for item in STR_RE.findall(stripped) if item not in ROLES]


def matching_close(text: str, start: int) -> int:
    """从 ``start`` 处的括号出发，返回与之配平的右括号下标；不配平返回 -1。"""
    opener = text[start]
    closer = {"(": ")", "{": "}"}.get(opener, "")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opener:
            depth += 1
        elif closer and text[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def query_calls(text: str) -> list[tuple[int, str, int, int, str]]:
    """列出全部按名查询，返回 ``(行号, 参数正文, 起始偏移, 结束偏移, 动词)``。

    动词是 ``get`` / ``query``：两类断言的有效性完全不同，得分开判。
    ``getBy*`` 查不到就直接抛，套件全绿本身就证明「目标确实在 DOM 里出现过」，再用「目标
    文字在源码里找不到」去怀疑它必然全误报（首版就是这么报了 32 条动态拼接的可访问名）。
    真正会永真的是 ``queryBy*`` + 「不在文档里」这一类缺席断言。

    参数正文靠括号配平取，而不是「取到行尾最后一个右括号」：
    ``expect(screen.getByLabelText("名称")).toHaveValue("新指令")`` 里的「新指令」是断言的期望
    值、不是查询目标，按行尾贪婪取会把整行的字符串都当成查询目标，误报一大片。

    Args:
        text: 测试文件正文。

    Returns:
        按出现顺序排列的查询五元组。
    """
    calls: list[tuple[int, str, int, int, str]] = []
    for head in QUERY_HEAD_RE.finditer(text):
        open_at = head.end() - 1
        close_at = matching_close(text, open_at)
        if close_at < 0:
            continue
        line = text.count("\n", 0, head.start()) + 1
        calls.append(
            (line, text[open_at + 1 : close_at], head.start(), close_at, head.group(1))
        )
    return calls


def audit_absent(prod: str) -> list[str]:
    """缺席断言里「目标文字哪儿都造不出来」的——断一个永远不会有的东西不存在，纯永真。

    正向查询不判（理由见 ``query_calls``）。对照面 = 非测试源码骨架 + 本文件挖掉查询调用后的
    假数据骨架：缺席断言的目标常常是 api 假数据里的文件名（删掉后要它不再出现），这类由数据
    供给侧保证，属合法回归护栏；两边骨架里都找不到的才是嫌疑。
    """
    findings: list[str] = []
    prod_frame = skeleton(prod)
    for path in test_files():
        text = path.read_text(encoding="utf-8")
        calls = query_calls(text)
        pieces: list[str] = []
        cursor = 0
        for _, _, start, end, _ in calls:
            pieces.append(text[cursor:start])
            cursor = end + 1
        pieces.append(text[cursor:])
        haystack = prod_frame + skeleton("".join(pieces))
        rel = path.relative_to(REPO).as_posix()
        for number, args, _, _, verb in calls:
            if verb != "query":
                continue
            for target in targets_of(args):
                if not explainable(target, haystack):
                    findings.append(
                        f"{rel}:{number} 断言 {target!r} 不出现，但没有任何东西能产出它"
                    )
    return findings


def audit_tautology() -> list[str]:
    """形状上就恒真的断言（断 ``expect(true)``、断查询对象 ``toBeDefined`` 等）。"""
    findings: list[str] = []
    for path in test_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in TAUTOLOGY_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{number} {label}")
    return findings


def mock_factory_bodies(text: str) -> list[str]:
    """抠出 ``vi.mock("...api", () => ({...}))`` 工厂里的对象字面量正文。"""
    bodies: list[str] = []
    for head in MOCK_API_RE.finditer(text):
        open_at = text.find("{", head.end())
        close_at = matching_close(text, open_at) if open_at >= 0 else -1
        if close_at > 0:
            bodies.append(text[open_at + 1 : close_at])
    return bodies


def audit_mock_surface() -> list[str]:
    """api mock 工厂里挂了真实 api.ts 没有的方法名（mock 面大于真实面就是假保险）。"""
    api_text = (FRONTEND_SRC / "api.ts").read_text(encoding="utf-8")
    surface = set(API_METHOD_RE.findall(api_text))
    surface.update(re.findall(r"^  (\w+): ", api_text, re.MULTILINE))
    if not surface:
        return ["解析 api.ts 方法面失败，口径需要复核"]
    findings: list[str] = []
    for path in test_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        for body in mock_factory_bodies(text):
            for name in sorted(set(MOCKED_KEY_RE.findall(body))):
                if name not in surface:
                    findings.append(f"{rel} mock 了 {name}()，api.ts 无此方法")
    return sorted(findings)


def audit_coupled() -> list[str]:
    """断样式类 / 标签名 / 内部状态位的实现细节耦合断言。"""
    findings: list[str] = []
    for path in test_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in COUPLED_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{number} {label}")
    return findings


def stats() -> tuple[int, int]:
    """自证口径：扫了几个测试文件、抓到几处按名查询（候选为 0 时先确认分母不是 0）。"""
    files = test_files()
    queries = sum(len(query_calls(path.read_text(encoding="utf-8"))) for path in files)
    return len(files), queries


def report(title: str, items: list[str], detail: bool) -> None:
    """先给计数（台账要的是数），--detail 时才展开明细。"""
    print(f"## {title}：{len(items)} 处")
    shown = items if detail else items[:20]
    for item in shown:
        print("- " + item)
    if not detail and len(items) > len(shown):
        print(f"- …另有 {len(items) - len(shown)} 条，跑 --detail 看全")
    print()


def main() -> int:
    """按四类口径各扫一遍并输出计数与候选。"""
    detail = "--detail" in sys.argv
    prod = prod_text()
    scanned, queries = stats()
    print(f"## 口径自证：扫描 {scanned} 个测试文件，抓到 {queries} 处按名查询")
    print()
    report("永真嫌疑：缺席断言的目标源码造不出来", audit_absent(prod), detail)
    report("永真嫌疑：形状恒真的断言", audit_tautology(), detail)
    report("实现细节耦合断言", audit_coupled(), detail)
    report("mock 面大于真实 api 面", audit_mock_surface(), detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
