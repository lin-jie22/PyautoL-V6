#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：`requires-python = ">=3.9"` 是**声明的下限**，必须真能跑。

═══ 为什么需要这份 ═══

`pyproject.toml` 写了 `requires-python = ">=3.9"`，CI 也真的在 3.9 上跑全套。
但"在 3.9 上跑"不等于"每条代码路径都能在 3.9 上执行"——实测抓到的两个：

  · `tests/contract_commit_verdict_test.py` 的 f-string 表达式里带 `\\"`
    ⇒ 3.11- **SyntaxError**（PEP 701 到 3.12 才放开）；
  · `scripts/ext_upper.py` 的 `spec: dict | None` ⇒ 3.9 **导入即崩**
    （`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`）。
    而这个模块是 `contract_serve_test.py` 热装载的**产品文件**，于是 3.9 上
    它的 I 组 6 条全红，报的却是"清单没装载上任何能力"—— 真因藏在三个下游。

⛔ `ast.parse(src, feature_version=(3, 9))` **抓不到**这两类：
   `feature_version` 只管"这个版本有没有这个语法特性"，覆盖不了 f-string
   解析器的差异，也完全不看注解的**运行时求值**。
   ⇒ 唯一可靠的判据是**用真实的最低版本解释器去执行**。

本契约分两层：
  A 组（静态、任何解释器都能跑）：注解位置用了 `X | Y` 却没写
     `from __future__ import annotations` ⇒ 在 3.9 上是运行期 TypeError。
     （只扫"注解位置"，不是全仓 grep `|` —— 那会误伤 `a | b` 的位运算。）
  B 组（真实解释器）：找到 3.9 就逐个 `import aether.*`；找不到就**显式跳过
     并记账**，不假装验过（第 145 条）。
"""
import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []
SKIPPED = []
N = [0]

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "build",
              "dist", "data", ".mypy_cache", ".pytest_cache"}


def ck(name, cond, detail=""):
    N[0] += 1
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def sk(name, why):
    N[0] += 1
    print(f"  SKIP {name} —— {why}")
    SKIPPED.append(f"{name}：{why}")


# ══ A 组：注解位置的 PEP 604 `X | Y` 必须有 future import ══════════

def _has_future_annotations(tree) -> bool:
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module == "__future__":
            if any(a.name == "annotations" for a in n.names):
                return True
    return False


def _annotation_nodes(tree):
    """**只取注解位置**的表达式节点。

    ⛔ 全仓 grep `|` 会把 `a | b`（位运算）、`"|"`（字符串）全算进来；
       判据必须落在"这段代码会不会被当类型求值"上。
    """
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = n.args
            for arg in (list(a.posonlyargs) + list(a.args)
                        + list(a.kwonlyargs)):
                if arg.annotation is not None:
                    yield arg.annotation
            for v in (a.vararg, a.kwarg):
                if v is not None and v.annotation is not None:
                    yield v.annotation
            if n.returns is not None:
                yield n.returns
        elif isinstance(n, ast.AnnAssign) and n.annotation is not None:
            yield n.annotation


def _pep604_in(ann) -> bool:
    return any(isinstance(x, ast.BinOp) and isinstance(x.op, ast.BitOr)
               for x in ast.walk(ann))


def scan_pep604(path: Path):
    """返回 [(行号, 文本)]；文件本身语法错 ⇒ 返回 None（由别的契约管）。"""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return None
    if _has_future_annotations(tree):
        return []
    out = []
    for ann in _annotation_nodes(tree):
        if _pep604_in(ann):
            out.append((int(getattr(ann, "lineno", 0)),
                        ast.get_source_segment(src, ann) or ""))
    return out


def _py_files():
    for p in sorted(ROOT.rglob("*.py")):
        if set(p.relative_to(ROOT).parts) & _SKIP_DIRS:
            continue
        yield p


# ══ B 组：真实最低版本解释器 ═══════════════════════════════════════

def find_py39() -> str:
    """找一个**真实是 3.9** 的解释器；没找到返回空串。

    ⛔ 不许拿 `python3` 猜版本：本机 `python3` 可能就是 3.13。
       每个候选都要跑一次 `-c print(version)` 核对，对齐"发现不等于存在"。
    """
    cands = [os.environ.get("V6_PY39", "").strip(), "python3.9",
             "/usr/bin/python3.9", "/usr/local/bin/python3.9",
             "/opt/homebrew/bin/python3.9",
             #: macOS 自带的 `/usr/bin/python3` 常常正是 3.9（本机就是这样）
             "/usr/bin/python3"]
    for c in cands:
        if not c:
            continue
        p = shutil.which(c) or (c if os.path.isfile(c) else "")
        if not p:
            continue
        try:
            r = subprocess.run(
                [p, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if (r.stdout or "").strip() == "3.9":
            return p
    return ""


_IMPORT_PROBE = (
    "import importlib, json, sys\n"
    "mods = json.loads(sys.argv[1])\n"
    "bad = []\n"
    "for m in mods:\n"
    "    try:\n"
    "        importlib.import_module(m)\n"
    "    except BaseException as e:\n"
    "        bad.append(m + ': ' + type(e).__name__ + ': ' + str(e)[:90])\n"
    "print(json.dumps(bad))\n"
)


def import_under(py: str, mods, cwd: Path):
    """用指定解释器逐个 import；返回 (bad 列表, 原始 stderr)。"""
    r = subprocess.run([py, "-c", _IMPORT_PROBE, json.dumps(list(mods))],
                       cwd=str(cwd), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=600)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    err = (r.stderr or b"").decode("utf-8", "replace")
    try:
        return json.loads(out.splitlines()[-1] if out else "[]"), err
    except ValueError:
        return ["<探针输出不是 JSON>：%r" % out[:200]], err


def main() -> int:
    print("=" * 68)
    print("Python 兼容契约：声明的下限必须真能跑（不是'CI 上跑过全套'）")
    print("=" * 68)

    # ── A 组：静态 ────────────────────────────────────────────────
    print("\n【A】注解位置用了 `X | Y` ⇒ 必须有 `from __future__ import annotations`")
    files = list(_py_files())
    ck("A0 扫到了足够多的源文件（否则本组空转）", len(files) >= 100,
       f"{len(files)} 个 .py")

    hits = []
    unparsable = []
    for p in files:
        got = scan_pep604(p)
        if got is None:
            unparsable.append(str(p.relative_to(ROOT)))
            continue
        for ln, txt in got:
            hits.append(f"{p.relative_to(ROOT)}:{ln} `{txt[:50]}`")
    ck("A1 ⛔ 没有'注解里用 X|Y 却没写 future import'的文件"
       "（3.9/3.10 上会 TypeError: unsupported operand |）",
       not hits, "; ".join(hits[:4]) or f"{len(files)} 个文件全部干净"
       + (f"（{len(unparsable)} 个无法解析，见 A1b）" if unparsable else ""))
    #: ⛔ 无法解析的文件不能静默算作"干净"：`scan_pep604` 返回 None 是
    #:    "**没验**"，不是"没问题"（第 142 条）。这里如实披露，不设成红 ——
    #:    语法错误本身由 `contract_assert_wiring_test.py` 的 A0 负责报。
    ck("A1b 无法解析的文件数=0（有的话 A1 的覆盖就有缺口，也不许当成绿）",
       not unparsable, f"{unparsable[:4]}")

    #: ⛔ 反证：扫描器必须能命中一个**真的会被拒**的样本。
    #:    这里不造字符串样本，直接拿 `ext_upper.py` 的**修复前形态**（去掉
    #:    future import 与那段注释）—— 那是这次真实的标本。
    ext = ROOT / "scripts" / "ext_upper.py"
    orig = ext.read_text(encoding="utf-8")
    stripped = "\n".join(
        ln for ln in orig.splitlines()
        if not ln.strip().startswith("from __future__ import annotations"))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "ext_upper_no_future.py"
        fake.write_text(stripped, encoding="utf-8")
        got_fake = scan_pep604(fake)
        ck("A2 ⛔⛔ 反证：把 `from __future__ import annotations` 去掉"
           "（= 本次真实标本）⇒ 扫描器必须命中",
           bool(got_fake), f"命中={got_fake}")
        #: 对照：原文件（带 future import）必须**不**命中，否则 A1 只是
        #: "任何文件都被判犯规"，等于恒真。
        ck("A3 对照：带 future import 的原文件 ⇒ 不命中（A2 因此不是恒真）",
           scan_pep604(ext) == [], f"命中={scan_pep604(ext)}")

    # ── B 组：真实 3.9 解释器 ─────────────────────────────────────
    print("\n【B】真实 3.9 解释器：能 import 整个包（不是'语法能过'）")
    py39 = find_py39()
    mods = sorted("aether." + p.stem
                  for p in (ROOT / "aether").glob("*.py") if p.stem != "__init__")
    ck("B0 包里有足够多的模块可验（否则本组空转）", len(mods) >= 10,
       f"{len(mods)} 个")

    if not py39:
        sk("B1–B3 真实 3.9 下整个包可导入",
           "本机找不到 3.9 解释器（可设 V6_PY39 指定）；"
           "⛔ 这不是通过，是没验")
    else:
        print(f"  （用 {py39}）")
        bad, err = import_under(py39, mods, ROOT)
        ck(f"B1 ⛔ {py39} 下 {len(mods)} 个模块全部导入成功"
           f"（TypeError/ImportError 都会在这里现形）",
           not bad, "; ".join(bad[:3]) or f"{len(mods)} 个全部 OK")
        #: ⛔ 反证：探针本身必须能报出失败。否则 B1 的"全绿"可能只是
        #:    探针把异常吞了（`import_under` 返回的是空列表）。
        bad2, _e2 = import_under(py39, ["aether.__no_such_module__"], ROOT)
        ck("B2 ⛔⛔ 反证：故意导入一个不存在的模块 ⇒ 探针必须报出来"
           "（否则 B1 是恒真）",
           len(bad2) == 1 and "no_such_module" in bad2[0], str(bad2)[:160])
        #: ⛔ 第二层反证：探针必须能抓 `TypeError`（不是只抓 ImportError）。
        #:    造一个真用了 `dict | None` 且没写 future import 的模块，在 3.9
        #:    下 import 它 ⇒ 必须出现 `TypeError` 且提到 `unsupported operand`。
        with tempfile.TemporaryDirectory() as td:
            bad3, _e3 = import_under(py39, ["probe604"], Path(td))
            #: 先把夹具写进那个临时目录再跑一次（上一步用作"目录里没东西"的
            #: 对照：必须是 ImportError 而不是 TypeError，两者不许混）。
            (Path(td) / "probe604.py").write_text(
                "def f(spec: dict | None = None) -> dict:\n"
                "    return {}\n", encoding="utf-8")
            bad4, _e4 = import_under(py39, ["probe604"], Path(td))
        ck("B3 ⛔⛔ 反证：`dict | None` 且无 future import ⇒ 3.9 下必须 "
           "TypeError（不是 ImportError，两者不许混）",
           len(bad4) == 1 and "TypeError" in bad4[0]
           and "unsupported operand" in bad4[0], str(bad4)[:200])
        ck("B4 对照：同样 import 一个**不存在**的模块 ⇒ 报的是 ImportError"
           "（B3 因此不是'什么都报 TypeError'）",
           len(bad3) == 1 and "ImportError" in bad3[0]
           or "ModuleNotFoundError" in (bad3[0] if bad3 else ""),
           str(bad3)[:160])

    print("\n" + "=" * 68)
    if SKIPPED:
        print(f"⏭ {len(SKIPPED)} 项因环境不适用而跳过（不算通过）：")
        for s in SKIPPED:
            print(f"   - {s}")
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过（{N[0]} 项）：注解层与真实 3.9 都过得去")
    return 0


if __name__ == "__main__":
    sys.exit(main())
