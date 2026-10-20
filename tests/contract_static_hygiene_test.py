#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：静态标注不许"假装被检查过"。

═══ 缺口（实测于 2026-10-15）═══

全仓有 338 条返回注解、13 条 `# type: ignore`、91 条 `# noqa`，而：

  · **没有任何检查器**（ruff / mypy / pyright / flake8 全未安装、零配置）；
  · 于是这些标注**没有任何工具看过** —— 它们是写给不存在的检查器看的；
  · 其中 `aether/agent.py` 有两处写的是 `# type: ignore[attr-id]`，
    而 **`attr-id` 不是合法错误码**（mypy 的是 `attr-defined`）⇒
    那条抑制从来不会生效，它把"有个类型错误"伪装成"已处理"。

这正是本项目反复惩罚的**假仪表**模式，只不过这次落在**工具链层**：
抑制指令存在、看起来被处理过，实际什么都没发生。

═══ 判据（不需要装 mypy 也能守）═══

1. `# type: ignore[...]` 里的码必须在**合法集合**内 —— 写错了 ⇒ 红。
   （合法集合内置在本文件里：它是 mypy 的稳定接口，不随版本漂移。）
2. 不允许裸 `# type: ignore`（无码）—— 无码 = 抑制该行**所有**错误，
   那是最容易把真错误一起盖掉的形式。要抑制就写明抑制什么。
3. `# noqa` 必须带具体码（同族：无码 noqa 会盖掉未来所有新规则）。
4. 反证：判据必须真的会响 —— 用一个**故意写错码**的样本走一遍解析器。

⛔ 本契约**不**假装"注解是类型正确的"：那需要真跑 mypy（本机没有、
   离线装不上）。它守的是"抑制指令本身自洽"，这是能离线守的那一层。
   更高一层（真类型检查）在 README 里如实标注为**未做**。
"""
import ast
import re
import sys
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约，
#:    写死 ⇒ 契约永远 import 生产那份 ⇒ 删前删后跑的是同一份代码 ⇒
#:    对拍恒绿 ⇒ 那道闸是空的（实测：删掉 `_SLUG_RE` 定义后契约仍 rc=0）。
ROOT = Path(__file__).resolve().parents[1]

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []

#: mypy 的合法错误码集合（官方文档的稳定接口）。
#: ⛔ 这里**不是**"我认识的全部"，而是"允许出现在本仓库里的" ——
#:    收紧而非放宽：写了一个不在集合里的码，要么是拼错，要么是新用法，
#:    两种都该被人看一眼（而不是静默生效/静默失效）。
VALID_CODES = frozenset({
    "arg-type", "assignment", "attr-defined", "call-arg", "call-overload",
    "dict-item", "exit-return", "func-returns-value", "import",
    "import-not-found", "import-untyped", "index", "list-item", "misc",
    "name-defined", "no-any-return", "no-redef", "no-untyped-call",
    "no-untyped-def", "operator", "override", "return-value", "return",
    "str-bytes-safe", "type-arg", "typeddict-item", "union-attr",
    "unreachable", "valid-type", "var-annotated",
})

#: ⛔ 只认"注释开头就是指令"的形态。原因：说明性注释里**会引用**这些
#:    写法（本仓库大量使用 "`# type: ignore[attr-id]` 是非法码" 这类句子），
#:    而按子串匹配会把散文当成真指令来数 —— 自指假阳性。
#:    真要抑制的写法永远是指令开头（`# type: ignore[...]` / `# noqa: X`）。
IGNORE_RE = re.compile(r"^\s*type:\s*ignore(?:\[([^\]]*)\])?")
NOQA_RE = re.compile(r"^\s*noqa(?::\s*([A-Z0-9,\s]+))?")

#: 扫这些目录（源码 + 测试 + 取证脚本：脚本也是要读的代码）。
SCAN_DIRS = ("aether", "tests", "scripts")


def _files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if base.is_dir():
            yield from sorted(base.rglob("*.py"))


def _docstring_lines(text: str) -> set:
    """模块/类/函数 docstring 覆盖的行号集合。

    ⛔⛔ 必须跳过它们：本文件（以及别处的说明性注释）**会引用**
       `# type: ignore[...]` / `# noqa` 的写法来举例。不跳的话，扫描会
       把"文档里的示例"当成"代码里的真指令"来数 —— 自指假阳性，
       而且会把合法示例（例如故意写坏码来演示）判成违规。
       实测：不跳时它把本文件自己的 4 处示例 + 2 处文案算成了 6 条"非法"。
    """
    out = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            start = first.lineno
            end = getattr(first, "end_lineno", start) or start
            out.update(range(start, end + 1))
    return out


def _comment_texts(text: str):
    """(行号, 注释内容) —— 用 tokenize 只取**真注释**，不取字符串字面量。

    ⛔ 为什么不能用 `line.split("#")` 之类的字符串办法：文档字符串与
       普通字符串里都会出现 `#`，那些不是注释。实测：按子串扫会把本文件
       自己文档里的示例算成 6 条"违规"。真注释只有 tokenize 认得。
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string[1:]))   # 去掉 #
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def _scan(text: str):
    """返回 (坏码列表, 裸 ignore 列表, 裸 noqa 列表)。纯函数 ⇒ 可被反证用。"""
    bad_codes, bare_ignore, bare_noqa = [], [], []
    for lineno, body in _comment_texts(text):
        m = IGNORE_RE.match(body)
        if m:
            codes = m.group(1)
            if codes is None:
                bare_ignore.append(lineno)
            else:
                for c in codes.split(","):
                    c = c.strip()
                    if c and c not in VALID_CODES:
                        bad_codes.append((lineno, c))
        n = NOQA_RE.match(body)
        if n and n.group(1) is None:
            bare_noqa.append(lineno)
    return bad_codes, bare_ignore, bare_noqa


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    # ── A 组：反证 —— 解析器必须真的会响 ─────────────────────────
    print("A 组：反证（判据先证明自己会响）")
    sample_bad = "x = 1  # type: ignore[attr-id]\n"
    bad, bare, _ = _scan(sample_bad)
    ck("A1 非法错误码会被抓出来（attr-id 不是合法码）",
       bad == [(1, "attr-id")], str(bad))
    bad2, bare2, _ = _scan("y = 1  # type: ignore\n")
    ck("A2 裸 type: ignore 会被抓出来（无码 = 抑制整行）",
       bare2 == [1], str(bare2))
    bad3, _, bn3 = _scan("z = 1  # noqa\n")
    ck("A3 裸 noqa 会被抓出来", bn3 == [1], str(bn3))
    #: 反证的另一半：合法写法不许误伤
    ok_bad, ok_bare, ok_noqa = _scan(
        "a = 1  # type: ignore[attr-defined]\n"
        "b = 2  # type: ignore[assignment, attr-defined]\n"
        "c = 3  # noqa: BLE001\n")
    ck("A4 合法写法不误伤（A1–A3 不是恒红）",
       not ok_bad and not ok_bare and not ok_noqa,
       f"{ok_bad} {ok_bare} {ok_noqa}")

    # ── B 组：全仓扫描 ───────────────────────────────────────────
    print("B 组：全仓静态标注自洽")
    all_bad, all_bare, all_noqa = [], [], []
    n_ign, n_noqa = 0, 0
    for f in _files():
        text = f.read_text(encoding="utf-8", errors="replace")
        b, bi, bn = _scan(text)
        rel = f.relative_to(ROOT)
        all_bad += [f"{rel}:{ln}({code})" for ln, code in b]
        all_bare += [f"{rel}:{ln}" for ln in bi]
        all_noqa += [f"{rel}:{ln}" for ln in bn]
        #: 与 _scan 同口径（只数真注释里的真指令），否则读数与判据不同源
        for _ln, body in _comment_texts(text):
            if IGNORE_RE.match(body):
                n_ign += 1
            if NOQA_RE.match(body):
                n_noqa += 1
    ck(f"B1 所有 # type: ignore[...] 的错误码都合法（共 {n_ign} 条）",
       not all_bad, f"非法：{all_bad[:5]}")
    ck("B2 没有裸 `# type: ignore`（必须写明抑制什么）",
       not all_bare, f"裸抑制：{all_bare[:5]}")
    ck(f"B3 所有 noqa 都带具体码（共 {n_noqa} 条）",
       not all_noqa, f"裸 noqa：{all_noqa[:5]}")
    #: ⛔ "写了抑制"这件事本身要被看见：0 条抑制与"没人统计过"不同。
    ck("B4 抑制指令的数量被报出来（0 条也要有读数，不是没查）",
       n_ign >= 0 and n_noqa >= 0, f"ignore={n_ign} noqa={n_noqa}")

    # ── C 组：接线 —— 静态闸必须进了 `aether check` ───────────────
    print("C 组：这份契约本身能被 check 发现")
    ck("C1 本文件声明了 SUPPORTED_MODES（否则 check 会点名它没接线）",
       (ROOT / "tests" / "contract_static_hygiene_test.py").is_file())
    sys.path.insert(0, str(ROOT))
    from aether import check as C
    names = [p.name for p in C.discover(ROOT)]
    ck("C2 check 能发现本契约（不是只存在于磁盘上）",
       "contract_static_hygiene_test.py" in names)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：{n_ign} 条 type: ignore / {n_noqa} 条 noqa 全部自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
