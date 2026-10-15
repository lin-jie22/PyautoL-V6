#!/usr/bin/env python3
"""断言接线自证：`ck()` 调用的参数不许错位。

为什么单独一个文件
------------------
`tests/` 下 30+ 个 `contract_*_test.py`，**每个文件都在顶部自己定义了一个
断言辅助函数 `ck(...)`**，而且签名**不统一**：

  · 33 个是三参 `ck(name, cond, detail="")`
  · `contract_boundary_test.py` 是四参 `ck(group, name, ok, note="")`

这埋了一个**静默失效**的坑，而且它不会报错、不会警告：

    在四参文件里照三参风格写 ⇒ `ok` 位上落进来的是一个**非空 f-string**
    （truthy）⇒ 断言**恒真** ⇒ 全绿，但一条都没测。

这是本项目最常被自己违反、又最难靠人眼发现的一类错误 —— 它长得和
正常调用一模一样，只有逐条数实参个数才看得出。2026-10-15 r37 收口时
我（在重写 boundary C 组时）正好犯了一次，四条断言全中。

⛔ 所以这条闸只做一件事：**机器**扫一遍所有契约文件，确认每个 `ck()`
   调用的"条件位"上站的不是字符串。人眼 review 抓不住，交给 AST。

⛔⛔ 同源纪律：定位条件位**不许**用"倒数第二个实参"。
   四参文件里漏传 group 的调用只剩 3 个实参，倒数第二位**恰好**还是
   条件位 ⇒ 抓不到（我就是这么漏掉的）。必须按 **形参名** `ok` / `cond`
   定位，且要求实参个数 ≥ 签名的**必填**参数个数。
"""
from __future__ import annotations

import ast
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))

FAILS: List[str] = []
N = [0]


def ck(name: str, cond: bool, detail: str = "") -> None:
    N[0] += 1
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── 扫描器 ─────────────────────────────────────────────────────────
def _ck_signature(tree: ast.Module) -> Tuple[List[str], int]:
    """取本文件 `ck` 的形参名列表与行号。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "ck":
            return [a.arg for a in node.args.args], int(node.lineno)
    return [], 0


def _cond_index(params: List[str]) -> int:
    """条件位的形参下标：三参文件叫 `cond`，四参文件叫 `ok`。

    ⛔ 按名字定位，不按位置 —— 见文件头那条纪律。
    """
    for want in ("ok", "cond"):
        if want in params:
            return params.index(want)
    return -1


def _is_stringy(node: ast.AST) -> bool:
    """条件位上是字符串 ⇒ 恒真断言。f-string 也算。"""
    if isinstance(node, ast.JoinedStr):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _find_ck(tree: ast.Module) -> Any:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "ck":
            return node
    return None


def _fail_var(tree: ast.Module) -> str:
    """失败累积列表的变量名 —— **让被测对象自己告诉我**。

    ⛔⛔ 别写死 `FAILS`：34 个契约里 31 个叫 `FAILS`、**3 个叫 `FAILED`**
        （`genome_source` / `logging` / `repro`）。
        第一版判据就栽在这：全文本匹配 `"FAILS.append" not in t` ⇒
        那 3 个被误判成"失败不记账"，而它们其实记得好好的。
    """
    fn = _find_ck(tree)
    if fn is None:
        return ""
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)):
            return node.func.value.id
    return ""


def _is_exit_call(func: ast.AST) -> bool:
    return getattr(func, "id", "") == "exit" \
        or getattr(func, "attr", "") == "exit" \
        or getattr(func, "id", "") == "SystemExit" \
        or getattr(func, "attr", "") == "SystemExit"


def _rc_honest(tree: ast.Module, var: str) -> bool:
    """存在一条把失败列表传到**退出码**的路径吗？

    实测到的合法形态有四种，都得认（按 AST 判，不按字符串宽窄判）：
      ① `return 1 if FAILS else 0`      —— Return 值引用失败列表
      ② `sys.exit(1 if FAILS)`          —— exit 参数引用失败列表
      ③ `if FAILED: print(…); sys.exit(1)`  —— 以失败列表为条件的分支里有 exit
      ④ `sys.exit(main())`              —— 转交 main ⇒ 由 ① 覆盖
    """
    if not var:
        return False

    def refs(n: ast.AST) -> bool:
        return any(isinstance(x, ast.Name) and x.id == var
                   for x in ast.walk(n))

    for n in ast.walk(tree):
        if isinstance(n, ast.Return) and n.value is not None and refs(n.value):
            return True
        if isinstance(n, ast.Call) and _is_exit_call(n.func) \
                and n.args and refs(n.args[0]):
            return True
        #: ③ `if <失败列表>: …` 分支里真的停下来了（许诺许PTHexit 或 return）
        if isinstance(n, ast.If) and refs(n.test):
            for x in ast.walk(n):
                if isinstance(x, ast.Call) and _is_exit_call(x.func):
                    return True
                if isinstance(x, ast.Return) and x.value is not None:
                    return True
    return False


def scan_file(path: Path) -> Dict[str, Any]:
    """扫一个契约文件。

    返回 {'bad': [{'line','kind','text'}], 'params': [...],
           'n_calls': int, 'fail_var': str, 'rc_honest': bool}
    kind ∈ {"too_few"（实参个数不够 ⇒ 后面的位整体前移）,
            "stringy"（条件位上是字符串 ⇒ 恒真）}
    """
    try:
        src = path.read_text(encoding="utf-8")
    except Exception as e:                              # noqa: BLE001
        return {"bad": [{"line": 0, "kind": "stringy",
                         "text": f"读不到：{type(e).__name__}: {e}"}],
                "params": [], "n_calls": 0}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"bad": [{"line": 0, "kind": "stringy",
                         "text": f"语法错误（判据无意义）：{e}"}],
                "params": [], "n_calls": 0}

    params, _ = _ck_signature(tree)
    if not params:
        return {"bad": [{"line": 0, "kind": "stringy",
                         "text": "找不到 def ck(...) ⇒ 无法核对条件位"}],
                "params": [], "n_calls": 0}
    ci = _cond_index(params)
    if ci < 0:
        return {"bad": [{"line": 0, "kind": "stringy",
                         "text": f"ck 形参里没有 ok/cond：{params}"}],
                "params": params, "n_calls": 0}

    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "ck")
    need = len(fn.args.args) - len(fn.args.defaults)

    bad: List[Dict[str, Any]] = []
    n_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "ck":
            continue
        if node.keywords:
            continue            # 带关键字参数 ⇒ 不会静默错位，跳过
        n_calls += 1
        if len(node.args) < need:
            bad.append({"line": int(node.lineno), "kind": "too_few",
                        "text": f"实参 {len(node.args)} 个 < 必填 {need} 个"})
            continue
        if _is_stringy(node.args[ci]):
            bad.append({"line": int(node.lineno), "kind": "stringy",
                        "text": f"条件位({ci})是字符串："
                                f"{ast.unparse(node.args[ci])[:60]}"})

    fv = _fail_var(tree)
    return {"bad": bad, "params": params, "n_calls": n_calls,
            "fail_var": fv, "rc_honest": _rc_honest(tree, fv)}


def _pick(bad: List[Dict[str, Any]], kind: str) -> List[str]:
    return [f"{b['text']} @L{b['line']}" for b in bad if b["kind"] == kind]


# ── A 组：所有契约文件的 ck 调用都不错位 ───────────────────────────
def group_a() -> None:
    print("\n【A】所有契约文件：ck() 调用的参数没错位")

    files = sorted(TESTS.glob("contract_*_test.py"))
    ck("A1 目录里确实有契约文件（空目录 ⇒ 下面全是空真）",
       len(files) >= 30, f"{len(files)} 个")

    no_sig: List[str] = []
    all_bad: Dict[str, List[Dict[str, Any]]] = {}
    total = 0
    for f in files:
        r = scan_file(f)
        total += int(r["n_calls"])
        if not r["params"]:
            no_sig.append(f.name)
        if r["bad"]:
            all_bad[f.name] = list(r["bad"])

    ck("A2 每个文件都能定位到 ck 的条件位（形参含 ok 或 cond）",
       not no_sig, f"定位不了={no_sig}")

    flat = [b for v in all_bad.values() for b in v]
    too_few = _pick(flat, "too_few")
    stringy = _pick(flat, "stringy")

    ck(f"A3 {total} 条 ck() 调用的实参个数都够（不许整体前移）",
       not too_few, "; ".join(too_few[:3]) or f"{total} 条全部够数")
    ck("A4 条件位上站的不是字符串（否则是恒真断言）",
       not stringy, "; ".join(stringy[:3]) or "无条件位是字符串")


# ── B 组：失败记账与退出码必须诚实 ─────────────────────────────────
def group_b() -> None:
    print("\n【B】契约的失败必须能被看见（记账 + 退出码）")

    files = sorted(TESTS.glob("contract_*_test.py"))
    no_append: Dict[str, str] = {}
    no_rc: Dict[str, str] = {}
    used: Dict[str, int] = {}
    for f in files:
        r = scan_file(f)
        var = str(r.get("fail_var") or "")
        used[var or "<无>"] = used.get(var or "<无>", 0) + 1
        if not var:
            no_append[f.name] = "ck 里没有任何 `.append(...)` ⇒ 失败不落地"
        elif not bool(r.get("rc_honest")):
            no_rc[f.name] = (f"失败列表叫 {var!r}，但退出码没有引用它"
                             f" ⇒ 跑红了也会报 0")

    #:    ⛔ 命名分布只作 detail 披露（分诊用），不单独设一条断言 ——
    #:       它跟 B1 判的是同一件事，写成独立断言只会多一条重复项。
    ck("B1 每个契约的 ck 都把失败**记下来**（不是只 print 一眼）",
       not no_append,
       "; ".join(f"{k}: {v}" for k, v in list(no_append.items())[:3])
       or f"{len(files)} 个全部记账")

    ck("B2 失败列表会传到**退出码**（红了 run_contracts 看得见）",
       not no_rc,
       "; ".join(f"{k}: {v}" for k, v in list(no_rc.items())[:3])
       or f"{len(files)} 个全部诚实")


# ── X 组：反证（扫描器自己必须能被翻转）─────────────────────────────
def group_x() -> None:
    print("\n【X】反证：扫描器不是恒真的")

    d = Path(tempfile.mkdtemp(prefix="v6_wiring_"))

    #: 事故版 1：四参签名 + 三参调用（我犯过的那个错）
    accident = d / "accident.py"
    accident.write_text(textwrap.dedent('''
        def ck(group: str, name: str, ok: bool, note: str = "") -> bool:
            return bool(ok)

        def demo(kinds) -> None:
            ck("C1 这里漏传了 group 参数", "harness.commit" in kinds,
               f"kinds={kinds}")
    ''').lstrip(), encoding="utf-8")
    got = scan_file(accident)
    hit = _pick(got["bad"], "stringy")     # ⛔ got 是 dict，不是 bad 列表
    ck("X1 事故版（四参签名 + 三参调用）⇒ 扫描器必须抓到",
       bool(hit), (hit[0] if hit else "没抓到 ⇒ 扫描器恒真"))

    #: 干净版：同四参签名，但调用是四参 ⇒ 不许报（否则 X1 是恒真）
    clean = d / "clean.py"
    clean.write_text(textwrap.dedent('''
        def ck(group: str, name: str, ok: bool, note: str = "") -> bool:
            return bool(ok)

        def demo(kinds) -> None:
            ck("C", "C1 正常传了 group", "harness.commit" in kinds,
               f"kinds={kinds}")
    ''').lstrip(), encoding="utf-8")
    got2 = scan_file(clean)
    ck("X2 干净版（四参签名 + 四参调用）⇒ 扫描器不报",
       not got2["bad"], f"误报={got2['bad'][:1]}")

    #: 事故版 2：三参签名 + 只传 1 个实参 ⇒ 条件位缺失
    short = d / "short.py"
    short.write_text(textwrap.dedent('''
        def ck(name, cond, detail=""):
            return bool(cond)

        def demo() -> None:
            ck("D1 只有名字，没有条件")
    ''').lstrip(), encoding="utf-8")
    got3 = scan_file(short)
    hit3 = _pick(got3["bad"], "too_few")
    ck("X3 事故版（三参签名 + 1 个实参）⇒ 扫描器必须抓到",
       bool(hit3), (hit3[0] if hit3 else "没抓到"))

    #: ⛔⛔ B 组也要有反证，否则"B1/B2 全绿"可能只是因为它们的判据太宽
    #:     （我第一版 B2 就是写死 `return 1 if FAILS`，宽到把 17 个诚实
    #:      的契约判成不诚实 —— 那是**假红**，方向相反但同样是坏判据）。
    nofail = d / "nofail.py"
    nofail.write_text(textwrap.dedent('''
        def ck(name, cond, detail=""):
            print(name)          # ⛔ 只 print，失败不落地

        def demo() -> None:
            ck("X 失败了但没人知道", False)
    ''').lstrip(), encoding="utf-8")
    ck("X4 事故版（ck 只 print 不记账）⇒ B1 的 fail_var 取不到 ⇒ 必红",
       not str(scan_file(nofail).get("fail_var") or ""),
       f"fail_var={scan_file(nofail).get('fail_var')!r}")

    noreturn = d / "noreturn.py"
    noreturn.write_text(textwrap.dedent('''
        FAILS = []

        def ck(name, cond, detail=""):
            if not cond:
                FAILS.append(name)

        def demo() -> None:
            ck("X", False)

        def main() -> int:
            demo()
            return 0             # ⛔ 无条件返回 0 ⇒ 跑红了也报绿
    ''').lstrip(), encoding="utf-8")
    r5 = scan_file(noreturn)
    ck("X5 事故版（main 无条件 return 0）⇒ B2 必须判它不诚实",
       str(r5.get("fail_var") or "") == "FAILS"
       and r5.get("rc_honest") is False,
       f"fail_var={r5.get('fail_var')!r} rc_honest={r5.get('rc_honest')}")

    #: 干净版对照：`return 1 if FAILS` ⇒ 不许报（否则 X5 是恒真）
    okret = d / "okret.py"
    okret.write_text(textwrap.dedent('''
        FAILS = []

        def ck(name, cond, detail=""):
            if not cond:
                FAILS.append(name)

        def main() -> int:
            ck("X", False)
            return 1 if FAILS else 0
    ''').lstrip(), encoding="utf-8")
    r6 = scan_file(okret)
    ck("X6 干净版（return 1 if FAILS）⇒ B2 判它诚实（X5 因此不是恒真）",
       r6.get("rc_honest") is True,
       f"fail_var={r6.get('fail_var')!r} rc_honest={r6.get('rc_honest')}")


def main() -> int:
    print("=" * 66)
    print("断言接线契约：ck() 调用不许参数错位（恒真断言的静态闸）")
    print("=" * 66)
    for fn in (group_a, group_b, group_x):
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            print(f"  ❌ {fn.__name__} 抛异常: {type(e).__name__}: {e}")
            FAILS.append(f"{fn.__name__} raised")
    print("\n" + "=" * 66)
    print(f"总计 {N[0]} 项，通过 {N[0] - len(FAILS)}，失败 {len(FAILS)}")
    for f in FAILS:
        print(f"  - {f}")
    print("=" * 66)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
