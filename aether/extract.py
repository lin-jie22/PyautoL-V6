"""⛔⛔ **判据抽取层** —— 把「别人的测试」变成「V6 能吃下的判据」。

═══ 这一层为什么存在（README 162 的取证结论）═══

到 161 为止，"修好真实 bug"的三个案例**没有一个判据是系统自己拿的**：
它们全部由人从作者的测试文件里手工转写（161 第七节白纸黑字写着）。
⇒ 端到端链路断在**第一公里**：抽象回来的东西能不能独立存在。

═══ ⛔⛔ 核心发现：**157 的内联器方向反了** ═══

`run_in_sandbox(source, tests)` 的 source 是**被测模块源码本体**，沙箱命名
空间里只有该模块**内部**的名字 —— **模块对象自己并不在那里**：

    b2h(1024)                   ⇒ NameError: b2h        （原样抽出来）
    strutils.bytes2human(1024)  ⇒ NameError: strutils   （157 内联器的产物）
    bytes2human(1024)           ⇒ fixed '1K' / buggy '1024B'  ← 有鉴别力

157 的 `inline_expr` 做的是"把局部别名展开成**更外层**的作用域名"，而沙箱
里恰恰只有**最内层**的裸名 ⇒ 它不是没做，是**朝着唯一错的方向做**。

本模块的 `_Demote` 走反方向：把指向被测模块的那一段通路**降回模块内裸名**。
两种形态都要照顾到（第一版只做了前者 ⇒ 71 条里 56 条漏判）：

    A 直写前缀  assert strutils.strip_ansi(x) == y     ⇒  strip_ansi(x)
    B 局部别名  b2h = strutils.bytes2human; b2h(1024)  ⇒  bytes2human(1024)

═══ ⛔⛔ 两条自限（不许为了救回率放宽任何一条）═══

  1. **只对被测模块自身降级**。指向别的模块（`d = datetime.date`）一律不动
     ⇒ 否则会在沙箱里凭空变出不存在的名字，等于**制造假判据**。
  2. **时间/随机依赖一律拒收**（`_UNSTABLE_CALLS`）。`date.today()` 这类
     探针跨天跨进程会翻 ⇒ 不可复现的判据 ≡ 随机判据。

═══ ⛔ 金标准在**测试自己写死的 expected** 里，不在"能不能跑"里 ═══

能求值但不等于测试写死的期望 ⇒ 那是**假鉴别力**（它在 fixed 上过、buggy 上
挂，看起来完美）。本层的 `gold_of` / `lit` 就是拿那个 expected 对拍用的。
「救回 370 条能求值」与「其中 20 条真有鉴别力」相差 18 倍 —— 谁把前者当
收益报，谁就是重复 2026-10-26 那次误读（README 157）。

═══ 它与合成层的关系 ═══

    git 仓库 → 本模块（抽判据）→ aether.synthesize（造补丁，不看答案）

`extract_criteria` **只需要测试源码**，不碰被测模块的任何版本 ⇒ 它拿不到
修复后的源码，也没有 `base` 参数（与 `synthesize_patch` 同款边界）。
"""

from __future__ import annotations

import ast
import copy
import json
from typing import Any, Dict, List, Optional, Set, Tuple

#: ⛔ 时间/随机依赖：内联/降级出这种表达式一律拒收（自限 2）。
_UNSTABLE_CALLS = {"today", "now", "random", "randint", "randrange", "uuid",
                   "uuid4", "time", "monotonic", "perf_counter", "choice",
                   "shuffle", "uniform", "getrandbits"}

#: ⛔⛔ unittest 断言映射表 —— **只收「同形」映射**（README 165）。
#:
#: 判据层**只有两种形态**：`expect`（探针 == 一个可 JSON 的值）与 `raises`
#: （探针抛指定异常）。所以能进这张表的 unittest 断言，必须 **不改变这个
#: 形状** —— 只是同一种判断的另一种写法：
#:
#:     assertEqual(a, b)        ≡ assert a == b       ⇒ expect   ✅
#:     assertTupleEqual(a, b)   ≡ assert a == b       ⇒ expect   ✅
#:     assertListEqual(a, b)    ≡ assert a == b       ⇒ expect   ✅
#:     assertRaises(E): ...     ≡ with raises(E)      ⇒ raises   ✅
#:
#: ⛔⛔ 下面这些**一律不许进表**（它们需要新的比较语义 = 扩张判据形态）：
#:
#:     assertNotEqual  ⇒ 需要"≠"判据      assertTrue/False ⇒ 需要 truthy 判据
#:     assertIn        ⇒ 需要"∈"判据      assertIs         ⇒ 需要身份判据
#:     assertAlmostEqual ⇒ 需要容差判据   assertRaisesRegex ⇒ 需要匹配判据
#:
#: 把它们塞进 `expect` 是**造假判据** —— 语义不同却长得一样。本层选择
#: **记账拒收**（`n_unmapped` + 分布），要不要扩张由人拍板（第 126 条）。
_UNITTEST_SAME = {
    "assertEqual": "expect",
    "assertTupleEqual": "expect",
    "assertListEqual": "expect",
}

#: `assertRaises` 也只收**同形**的两种写法（with 上下文 / 直接调用）。
_RAISE_NAMES = ("raises", "assertRaises")

#: ⛔⛔ **门标签**（README 166 的普查口径）：测试文件里**每一条**探针必须
#: 落到**恰好一格**。⛔ 这是诊断层：它只给已有拒收**逐条命名**，不改变任何
#: 一条判据能不能进来（扩张判据形态仍需人拍板，第 126 条）。
#:
#:   criterion      产出为候选判据（**还没跑过**，仍可能倒在下面的沙箱三验）
#:   form           判据形态不支持（assertTrue / assertIn / assert a != b …）
#:   unserializable 期望值不可 JSON（沙箱走 JSON，结构性限制）
#:   tuple          期望是 tuple（JSON 会把它降级成 list ⇒ 恒失败，README 165）
#:   unstable       时间/随机依赖 ⇒ 不可复现
#:
#: ⛔ `criterion` 之后还有**沙箱一侧**的三道（README 166 加在脚本里，不在
#:    本层）：`ctx`（参照实现上就跑不过/跑不稳/与写死期望不符）、`P2P`
#:    （buggy 上仍通过 ⇒ 无鉴别力）、`F2P`（buggy 上失败 ⇒ 真有鉴别力）。
GATE_CRITERION = "criterion"
GATE_FORM = "form"
GATE_UNSERIALIZABLE = "unserializable"
GATE_TUPLE = "tuple"
GATE_UNSTABLE = "unstable"
#: 顺序 = 报告里的展示顺序（先"成了"，再按拦的量降序的常见形态）。
GATE_ORDER = (GATE_CRITERION, GATE_FORM, GATE_UNSERIALIZABLE,
              GATE_TUPLE, GATE_UNSTABLE)


# ─────────────────────────── 断言抽取（带作用域）─────────────────────────
def _bare_assert_parts(test: ast.AST):
    """⛔ **单一来源**：裸 `assert` 的**同形**部分 ⇒ `(实际, 期望)`。

    只认 `a == b` 这一种比较。`!=` / `in` / `not` / `is` / 链式比较 ⇒ `None`
    （形态不支持，拒收并记账）。⛔ 判据形态只在这里定义一次 —— 裸 assert
    的扫描器与门归因层都调它 ⇒ 不会出现"扫描说能抽、归因说形态不支持"。
    """
    if (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        return ast.unparse(test.left), ast.unparse(test.comparators[0])
    return None


def _lambda_args(node: ast.Lambda):
    """该 lambda 有没有参数（有 ⇒ 探针无从求值，不许当同形写法收）。"""
    a = node.args
    return (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
            + ([a.vararg] if a.vararg else [])
            + ([a.kwarg] if a.kwarg else []))


def _iter_all_probes(test_src: str) -> List[Dict[str, Any]]:
    """⛔⛔ **单一来源**：测试文件里**每一条**探针 —— **含被拒收的**。

    返回 `[{stmt, expr, gold, fn, kind, attr, family, mapped}]`：

      · `mapped=False` ⇒ 判据形态不支持（README 165 第二节），**不进**流水线
        但必须记账 —— 它是"要不要扩张判据形态"的唯一依据（第 149 条）。
      · `stmt` = 所在**整条语句**（用于与 PR 新增断言对拍，README 166）。
      · `family` ∈ bare / pytest / unittest，`attr` = 断言写法名
        （`assert` / `chk` / `raises` / `assertEqual` / `assertTrue` / …）。

    ⛔⛔ 三个 `scoped_*` 与 `unmapped_unittest_calls` **全是本函数的投影**：
    判据形态只在 `_UNITTEST_SAME`、`_RAISE_NAMES`、`_bare_assert_parts` 里
    定义一次 ⇒ 改形态只改一处，不会出现两套规则各自漂移（README 166 第八节
    就是要抓这个：165 里"三验说能跑、执行说跑不过"正是两套口径的产物）。
    """
    tree = ast.parse(test_src)
    out: List[Dict[str, Any]] = []

    def rec(stmt, expr, gold, fn, kind, attr, family, mapped=True):
        return {"stmt": ast.unparse(stmt), "expr": expr, "gold": gold,
                "fn": fn, "kind": kind, "attr": attr, "family": family,
                "mapped": mapped}

    def callee_name(call: ast.AST):
        f = getattr(call, "func", None)
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return getattr(f, "attr", None)
        return None

    #: ⛔ `with self.assertRaises(E):` 的 **context_expr 本身也是一个 Call**，
    #:    而 `walk` 在 With 分支之后仍会递归进它的子节点 ⇒ 如果不记下来，
    #:    它会被下面的 Call 分支**再记一次**（单参 ⇒ 判为"形态不支持"）
    #:    ⇒ 一条 with 语句变成两条记录，其中一条是凭空造的拒收。
    #:    （README 166：正是这类双计数让 `n_unmapped` 从 128 跳到 270。）
    seen_ctx: Set[int] = set()

    def walk(node, fn: str):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, ch.name)
                continue
            if isinstance(ch, ast.Assert):
                parts = _bare_assert_parts(ch.test)
                if parts is None:
                    out.append(rec(ch, ast.unparse(ch.test), None, fn,
                                   "expect", "assert", "bare", False))
                else:
                    out.append(rec(ch, parts[0], parts[1], fn,
                                   "expect", "assert", "bare"))
            elif isinstance(ch, ast.Call):
                nm = callee_name(ch)
                if id(ch) in seen_ctx:
                    pass                      #: 已由 With 分支记过，不重复
                elif nm == "chk" and len(ch.args) == 2:
                    out.append(rec(ch, ast.unparse(ch.args[0]),
                                   ast.unparse(ch.args[1]), fn,
                                   "expect", "chk", "bare"))
                elif nm in _UNITTEST_SAME:
                    if len(ch.args) == 2:
                        out.append(rec(ch, ast.unparse(ch.args[0]),
                                       ast.unparse(ch.args[1]), fn,
                                       "expect", nm, "unittest"))
                    else:
                        out.append(rec(ch, ast.unparse(ch), None, fn,
                                       "expect", nm, "unittest", False))
                elif nm == "assertRaises":
                    a0 = ch.args[0] if ch.args else None
                    exc = (getattr(a0, "id", None)
                           or getattr(a0, "attr", None)) if a0 else None
                    callee = ch.args[1] if len(ch.args) >= 2 else None
                    #: ⛔⛔ README 166 第三节：**无参 lambda 是同形写法**。
                    #:    `assertRaises(E, lambda: X)` ≡ `with raises(E): X`
                    #:    —— 都是"执行 X 应抛 E"，不引入任何新比较语义。
                    #:    165 的实现只认 Name/Attribute ⇒ 普查实测它正是
                    #:    more-itertools **form 门最大的一格**（10/20）。
                    #:    ⛔ 但**带参** lambda 一概不收：参数无从求值 ⇒ 那
                    #:    是扩张，不是补漏。
                    if (exc and isinstance(callee, ast.Lambda)
                            and not _lambda_args(callee)):
                        out.append(rec(ch, ast.unparse(callee.body), exc, fn,
                                       "raises", nm, "unittest"))
                    elif exc and isinstance(callee, (ast.Name, ast.Attribute)):
                        probe = ast.Call(func=callee, args=ch.args[2:],
                                         keywords=[])
                        ast.fix_missing_locations(probe)
                        out.append(rec(ch, ast.unparse(probe), exc, fn,
                                       "raises", nm, "unittest"))
                    else:
                        out.append(rec(ch, ast.unparse(ch), None, fn,
                                       "raises", nm, "unittest", False))
                elif nm and nm.startswith("assert"):
                    out.append(rec(ch, ast.unparse(ch), None, fn,
                                   "expect", nm, "unittest", False))
            elif isinstance(ch, ast.With):
                for it in ch.items:
                    cx = it.context_expr
                    if isinstance(cx, ast.Call):
                        seen_ctx.add(id(cx))
                    if not isinstance(cx, ast.Call):
                        continue
                    if callee_name(cx) not in _RAISE_NAMES or not cx.args:
                        continue
                    a0 = cx.args[0]
                    exc = getattr(a0, "id", None) or getattr(a0, "attr", None)
                    if not exc:
                        continue
                    for st in ch.body:
                        if isinstance(st, ast.Expr):
                            out.append(rec(ch, ast.unparse(st.value), exc, fn,
                                           "raises", "raises", "pytest"))
                        else:
                            out.append(rec(ch, ast.unparse(st), None, fn,
                                           "raises", "raises", "pytest", False))
            walk(ch, fn)

    walk(tree, "<module>")
    return out


def scoped_assertions(test_src: str):
    """抽 `(实际表达式, 期望文本, 所在函数名)`。

    ⛔ 多返回**所属函数**是必需的：`x` 在 A 函数里是 3、在 B 函数里是 'a'，
    没有作用域就会被当成同一个 x ⇒ 内联出语义错误的探针。

    ⛔ 实现上**只是** `_iter_all_probes` 的投影（裸 assert / chk 的同形部分）。
    """
    return [(p["expr"], p["gold"], p["fn"]) for p in _iter_all_probes(test_src)
            if p["mapped"] and p["family"] == "bare" and p["kind"] == "expect"]


def scoped_raise_probes(test_src: str):
    """⛔ **期望异常**探针也带作用域 —— 与 `scoped_assertions` 同一个理由。

    第一版普查只抽返回值断言 ⇒ `c1c25da`（靠 2 条 raises 判据修好的那个 bug）
    在普查里显示「0 条鉴别力」⇒ 差点据此写下「内联增益全在形态门外」的假结论。
    ⛔ 一个「0」有两种含义：**真的没有** vs **抽漏了**（第 25 条）。

    ⛔ 实现上**只是** `_iter_all_probes` 的投影（`with raises(...)` 里的
    每条表达式）。`assertRaises`（unittest）与 `raises`（pytest）同形，
    第一版只认后者 ⇒ more-itertools 的 134 条 with 形态整个漏掉
    —— 那个"0 条判据"里有一半是这个，不是没鉴别力。
    """
    return [(p["expr"], p["gold"], p["fn"]) for p in _iter_all_probes(test_src)
            if p["mapped"] and p["family"] == "pytest" and p["kind"] == "raises"]


def scoped_unittest_probes(test_src: str):
    """⛔ README 165：**unittest 风格**的断言（`self.assertEqual(...)`）。

    只产出**同形**映射（见 `_UNITTEST_SAME` 上的长注释）。返回
    `[(探针, 期望文本|异常名, 函数名, kind)]`，与 `scoped_assertions`
    的元组形状一致，可直接拼进同一条流水线。

    ⛔ 覆盖 `assertRaises` 的**两种**写法：

        with self.assertRaises(ValueError):      ⇒ (探针, 'ValueError')
            mi.take(3, x)
        self.assertRaises(ValueError, mi.take, 3, x)   ⇒ 同上（直接调用式）

    第二版只做了 with 形态 ⇒ more-itertools 的 50 条直接调用式全漏。
    直接调用式映射成 `callee(args...)`，只接受 Name/Attribute 作 callee
    （lambda / 下标等 ⇒ 拒收，不做文本拼接，免得造出语法正确的假探针）。

    ⛔ 实现上**只是** `_iter_all_probes` 的投影（unittest 家族的同形部分）。
    """
    return [(p["expr"], p["gold"], p["fn"], p["kind"])
            for p in _iter_all_probes(test_src)
            if p["mapped"] and p["family"] == "unittest"]


def unmapped_unittest_calls(test_src: str) -> Dict[str, int]:
    """⛔ **被拒收**的 unittest 断言分布（`{名字: 条数}`）。

    记账而非静默丢弃（第 149 条：跳过 ≠ 通过）。⛔ 这张表是"要不要扩张
    判据形态"的**唯一依据** —— 如果 `assertTrue` 有 800 条而 `assertEqual`
    只有 20 条，那么"覆盖率低"的原因就不是抽取没做，而是形态太窄。

    ⛔ 实现上**只是** `_iter_all_probes` 的投影（`family == unittest` 且
    `mapped=False` 的那些）⇒ 与抽取层共用同一张形态表，不会各说一套。
    """
    out: Dict[str, int] = {}
    for p in _iter_all_probes(test_src):
        if p["family"] == "unittest" and not p["mapped"]:
            out[p["attr"]] = out.get(p["attr"], 0) + 1
    return out


def collect_bindings(test_src: str) -> Dict[str, Dict[str, Any]]:
    """收集 **分作用域** 的 `name = <rhs>` 绑定。

    ⛔ 同一作用域内同名有两个不同 rhs ⇒ 整条剔除（循环里重复赋值的变量，
    语义不确定）。返回 `{作用域: {name: rhs_ast}}`。
    """
    tree = ast.parse(test_src)
    envs: Dict[str, Dict[str, Any]] = {"<module>": {}}

    def scan(body, scope: str):
        bucket: Dict[str, List[Any]] = {}
        for st in body:
            if isinstance(st, (ast.Assign, ast.AnnAssign)):
                tgts = st.targets if isinstance(st, ast.Assign) else [st.target]
                val = st.value
                if val is None or len(tgts) != 1:
                    continue
                t = tgts[0]
                if isinstance(t, ast.Name):
                    bucket.setdefault(t.id, []).append(val)
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan(st.body, st.name)
            elif isinstance(st, ast.ClassDef):
                scan(st.body, scope)
            elif hasattr(st, "body"):
                scan(st.body, scope)
        envs[scope] = envs.get(scope, {})
        envs[scope].update(bucket)

    scan(tree.body, "<module>")
    for sc in list(envs):
        envs[sc] = {k: v[0] for k, v in envs[sc].items()
                    if len(set(ast.dump(x) for x in v)) == 1}
    return envs


class _Inliner(ast.NodeTransformer):
    def __init__(self, binds):
        self.binds = binds
        self.changed = False

    def visit_Name(self, node):  # noqa: N802
        if (isinstance(node.ctx, ast.Load) and node.id in self.binds
                and node.id not in ("self", "cls")):
            self.changed = True
            return copy.deepcopy(self.binds[node.id])
        return node


_UNSTABLE_CACHE: Dict[str, bool] = {}


def has_unstable_call(text: str) -> bool:
    """⛔ 自限 2：含时间/随机调用 ⇒ 拒收（不可复现的判据 ≡ 随机判据）。"""
    if text in _UNSTABLE_CACHE:
        return _UNSTABLE_CACHE[text]
    try:
        t = ast.parse(text, mode="eval")
    except SyntaxError:
        return False
    bad = False
    for n in ast.walk(t):
        if isinstance(n, ast.Call):
            f = n.func
            nm = getattr(f, "id", None) or getattr(f, "attr", None)
            if nm in _UNSTABLE_CALLS:
                bad = True
                break
    _UNSTABLE_CACHE[text] = bad
    return bad


def inline_expr(expr: str, binds: Dict[str, Any], maxdepth: int = 6):
    """把 expr 里的自由变量按 binds 展开。没得可展 ⇒ 返回 None。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    total = False
    for _ in range(maxdepth):
        tr = _Inliner(binds)
        new = tr.visit(tree)
        if not tr.changed:
            break
        total = True
        ast.fix_missing_locations(new)
        tree = new
    return ast.unparse(tree) if total else None


# ─────────────────────── ② 自指模块别名降级 ──────────────────────────────
def target_module(src_path: str) -> str:
    """`boltons/strutils.py` ⇒ `boltons.strutils`。"""
    p = src_path[:-3] if src_path.endswith(".py") else src_path
    return p.replace("/", ".")


def module_aliases(tree: ast.Module) -> Dict[str, str]:
    """`{测试文件里的绑定名: 模块限定名}`。

    覆盖四种真实写法：
        from boltons import strutils      ⇒ {'strutils': 'boltons.strutils'}
        import boltons.strutils           ⇒ {'boltons': 'boltons'}
        import boltons.strutils as bs     ⇒ {'bs': 'boltons.strutils'}
        from boltons.strutils import f    ⇒ {'f': 'boltons.strutils.f'}
    """
    out: Dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for al in n.names:
                if al.asname:
                    out[al.asname] = al.name
                else:
                    out[al.name.split(".")[0]] = al.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module:
            for al in n.names:
                nm = al.asname or al.name
                out[nm] = f"{n.module}.{al.name}"
    return out


def _qual_of(node: ast.AST, aliases: Dict[str, str]):
    """把 `x.y.z` 解析成限定名。根不在 alias 表里 ⇒ 不可解。"""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    if parts[0] not in aliases:
        return None
    return ".".join([aliases[parts[0]]] + parts[1:])


class _Demote(ast.NodeTransformer):
    """把「指向被测模块的 alias.名字」替换成**模块内裸名**（方向不可逆）。

    ⛔⛔ `pkg` —— **父包**的完全限定名（README 165）。开了它才会把
    `mi.first(x)`（`import more_itertools as mi`）降成 `first(x)`：

        被测模块是 more_itertools/more.py，而测试用的是**包的公开 API**
        （`__init__` 里 re-export 的那批名字）。没有这条 ⇒ more-itertools
        的 628 条判据**一条都跑不起来**（沙箱只装 more.py 本体，里面没有
        `mi` 这个名字）⇒ 又是一个"0 条"的假读数（第 25 条）。

    ⛔ 它是**试探性**的，与 `target` 那条**不是**一个可信度级别：
       · `target` 降级：限定名**精确等于**被测模块 ⇒ 静态可证。
       · `pkg` 降级：只证明"这名字是从包上取的"，**不证明它来自被测
         模块**（包里 re-export 的可能来自别的子模块）。
      ⇒ 它**只能**在三验（能求值 + 两次一致 + 与写死期望一致）之后成立：
       名字在被测模块里不存在 ⇒ NameError ⇒ 被三验剔除（fail-closed）。
      ⛔ 因此调用方必须**记账**它救了多少条，不许当成白捡的收益。
    """

    def __init__(self, target: str, aliases: Dict[str, str],
                 pkg: Optional[str] = None):
        self.target = target
        self.aliases = aliases
        self.pkg = pkg
        self.changed = False
        #: ⛔ **链内标记**：`mi.recipes.f` 里的 `mi.recipes` 是**链中段**，
        #:    不许它自己"降一下"。第一版没这个标记 ⇒ 自底向上的
        #:    `generic_visit` 先把内层 `mi.recipes` 降成 `recipes`，整条链
        #:    变成 `recipes.f(1)` —— 一个被测模块里根本不存在的名字。
        self._in_chain = False

    def _try(self, node, prefix: str):
        q = _qual_of(node, self.aliases)
        if q and q.startswith(prefix + "."):
            tail = q[len(prefix) + 1:]
            #: ⛔ 只降**单段**标识符：`mi.recipes.foo` 不许降成 `recipes.foo`
            #:    （那会凭空造出"被测模块里有 recipes"的假象）。
            if "." not in tail and tail.isidentifier():
                self.changed = True
                return ast.copy_location(ast.Name(id=tail, ctx=node.ctx), node)
        return None

    def visit_Attribute(self, node):  # noqa: N802
        if self._in_chain:
            #: 我是链中段：继续往下（保持链内），但自己**不降**
            self._descend(node)
            return node
        new = self._try(node, self.target)
        if new is None and self.pkg:
            new = self._try(node, self.pkg)
        if new is not None:
            return new
        self._descend(node)
        return node

    def _descend(self, node):
        """往下走：属性链内部保持 `_in_chain`，**非链**子节点正常处理。

        ⛔ 为什么不直接 `generic_visit`：那会把 `mi.recipes`（`mi.recipes.f`
        的内层）当成**独立**访问再判一次 ⇒ 它的 tail 恰好是单段 ⇒ 被降。
        """
        if isinstance(node.value, ast.Attribute):
            saved, self._in_chain = self._in_chain, True
            self.visit(node.value)
            self._in_chain = saved
        else:
            #: ⛔ 进这里时 `_in_chain` **必须**复位：下面可能藏着该降的
            #:    `mi.first(x)`（例如 `mi.first(x).attr` 的 value 是 Call）。
            saved, self._in_chain = self._in_chain, False
            self.generic_visit(node)
            self._in_chain = saved


def demote_binds(binds: Dict[str, Any], target: str,
                 aliases: Dict[str, str], maxdepth: int = 4,
                 pkg: Optional[str] = None):
    """把一批绑定的 rhs 做自指模块降级。

    ⛔ **链式**（`h = b2h`）不在这里做 —— 那是 `_Inliner` 的递归展开负责的。
    第一版把两者混在一起 ⇒ 自检挂了才发现的职责边界。
    """
    cur = {k: v for k, v in binds.items()}
    for _ in range(maxdepth):
        changed = False
        nxt: Dict[str, Any] = {}
        for k, v in cur.items():
            tr = _Demote(target, aliases, pkg)
            newv = tr.visit(copy.deepcopy(v))
            ast.fix_missing_locations(newv)
            if tr.changed:
                changed = True
            nxt[k] = newv
        cur = nxt
        if not changed:
            break
    return cur


def demote_expr(expr: str, target: str, aliases: Dict[str, str],
                pkg: Optional[str] = None):
    """⛔ **A 形态**：断言里**直接**写了模块名前缀（`strutils.f(x)` ⇒ `f(x)`）。

    它比 B 形态（局部别名）普遍得多：`test_strutils.py` 里绝大多数断言是这种
    直写形态，用别名的只有 `test_bytes2human` 一个函数。第一版只做了
    `demote_binds` ⇒ 71 条里 56 条落进「other」——**一个维度全压在一档，
    先怀疑量具**（第 24 条）。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    tr = _Demote(target, aliases, pkg)
    new = tr.visit(tree)
    if not tr.changed:
        return None
    ast.fix_missing_locations(new)
    return ast.unparse(new)


def resolve_probe(expr: str, binds: Dict[str, Any], target: str,
                  aliases: Dict[str, str], rounds: int = 3,
                  pkg: Optional[str] = None):
    """把一条探针的上下文依赖降到沙箱里有解为止。

    ⛔ 两种形态**交替**下刀，缺一个就漏一大片；且 A 做完可能暴露出新的 B
    （反之亦然）⇒ 必须迭代到不动点。

    `pkg`：父包降级（README 165），默认**不开** —— 它是试探性的，只有
    `extract_criteria` 那条路径开，并记账。
    """
    dbinds = demote_binds(binds, target, aliases, pkg=pkg)
    cur = expr
    moved = False
    for _ in range(rounds):
        before = cur
        d = demote_expr(cur, target, aliases, pkg=pkg)
        if d:
            cur, moved = d, True
        i = inline_expr(cur, dbinds)
        if i and i != cur:
            cur, moved = i, True
        if cur == before:
            break
    return cur if (moved and cur != expr) else None


# ─────────────────────── ③ 失败归因 ─────────────────────────────────────
def free_names(expr: str) -> Set[str]:
    """表达式里未绑定的 Load 名字 + 属性访问的根。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    bound: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            bound.add(n.id)
        elif isinstance(n, ast.Lambda):
            bound.update(a.arg for a in n.args.args)
        elif isinstance(n, ast.comprehension):
            for nm in ast.walk(n.target):
                if isinstance(nm, ast.Name):
                    bound.add(nm.id)
    out: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in bound:
                out.add(n.id)
        elif isinstance(n, ast.Attribute):
            cur = n
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id not in bound:
                out.add(cur.id)
    return out


def func_params(tree: ast.Module) -> Dict[str, Set[str]]:
    """`{函数名: {形参}}` —— 判定 fixture 的依据（`self` 不算 fixture）。"""
    out: Dict[str, Set[str]] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in list(n.args.args) + list(n.args.kwonlyargs)]
            out[n.name] = {a for a in args if a != "self"}
    return out


def rebound_names(tree: ast.Module) -> Dict[str, Set[str]]:
    """`{函数名: {被赋了多个不同值的名字}}` —— 多步序列的标志之一。"""
    out: Dict[str, Set[str]] = {}

    def scan(body, fn):
        seen: Dict[str, Set[str]] = {}
        for st in body:
            if isinstance(st, ast.Assign):
                for t in st.targets:
                    if isinstance(t, ast.Name):
                        seen.setdefault(t.id, set()).add(ast.dump(st.value))
            elif isinstance(st, (ast.AugAssign, ast.AnnAssign)):
                tg = getattr(st, "target", None)
                if isinstance(tg, ast.Name) and st.value is not None:
                    seen.setdefault(tg.id, set()).add(ast.dump(st.value))
            if hasattr(st, "body"):
                scan(st.body, fn)
            for f in ("orelse", "finalbody"):
                if hasattr(st, f):
                    scan(getattr(st, f), fn)
        out[fn] = out.get(fn, set()) | {k for k, v in seen.items()
                                        if len(v) > 1}

    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scan(n.body, n.name)
    return out


def attribute(names: Set[str], probe_fn: str,
              params: Dict[str, Set[str]],
              rebound: Dict[str, Set[str]]) -> str:
    """给一条仍然失败的探针归因。⛔ 优先级 fixture > rebound > other。"""
    if not names:
        return "other"
    hits = set()
    for nm in names:
        if nm in params.get(probe_fn, set()):
            hits.add("fixture")
        elif nm in rebound.get(probe_fn, set()):
            hits.add("rebound")
        else:
            hits.add("other")
    for k in ("fixture", "rebound", "other"):
        if k in hits:
            return k
    return "other"


# ─────────────────────── ④ 判据 / 金标准 ────────────────────────────────
def gold_of(exp_text: str):
    """测试自己写死的期望值。只能 literal 的才算金标准。"""
    try:
        return ast.literal_eval(exp_text)
    except (ValueError, SyntaxError):
        return None


def lit(text):
    if text is None:
        return None, False
    try:
        return ast.literal_eval(text), True
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return text, False


# ─────────────────────── ⑤ 顶层入口 ─────────────────────────────────────
def _jsonable(v) -> bool:
    """判据必须能进沙箱 —— 沙箱用 JSON 传参，`bytes` / `bytearray` /
    `datetime` 类的期望值序列化不了 ⇒ 这类判据**结构上进不了 V6**。

    ⛔ 第一版没管这件事：调用方各自踩一次 TypeError（`probe_auto_unlock`
    先崩、契约 H 组再崩，同一个坑踩两遍）⇒ 提到这一层，并**记账**（丢了多少）
    而不是静默丢弃（跳过 ≠ 通过，第 149 条）。
    """
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _has_tuple(v) -> bool:
    """⛔⛔ **tuple 期望是假判据**（README 165 第五节）。

    沙箱用 JSON 传 `expect` ⇒ **tuple 会被降级成 list**，而 `got` 是真实的
    tuple ⇒ `got == expect` **恒为 False** ⇒ 这类判据在任何版本上都"失败"。

    ⛔ 最坏的地方在于它**看起来像 F2P**：在缺陷版上失败 ⇒ 被当成有鉴别力
    ⇒ 合成层拿它当目标去搜 ⇒ 永远搜不到（改哪儿都不可能让 tuple == list）
    ⇒ 白白烧掉整个候选预算（实测 more-itertools 的 69 条"F2P"里，
    **绝大多数**是这一条造成的）。

    ⛔ 为什么三验没拦住：三验比对的是 `got` 的 `literal_eval` 与 gold
    （那里两边都是 tuple，相等），而判据真正执行时比的是 **JSON 往返后**
    的值（一边 tuple 一边 list）⇒ 两套比较口径不一致。修法是**两处都动**：
    这里显式丢弃并记账，调用方再加一道 `passed` 验（见 `probe_auto_unlock`）。
    """
    if isinstance(v, tuple):
        return True
    if isinstance(v, (list, set, frozenset)):
        return any(_has_tuple(x) for x in v)
    if isinstance(v, dict):
        return any(_has_tuple(k) or _has_tuple(x) for k, x in v.items())
    return False


def _drop(dropped: Optional[List[Dict[str, Any]]], gate: str,
          probe: Dict[str, Any], call: Optional[str]):
    """记账一条被拦下的探针（README 166）。`dropped=None` ⇒ 纯 no-op。"""
    if dropped is None:
        return
    dropped.append({"gate": gate, "attr": probe["attr"],
                    "stmt": probe["stmt"], "fn": probe["fn"],
                    "kind": probe["kind"], "call": call})


def attribute_statements(test_src: str, target: str,
                         jsonable_only: bool = True,
                         stats: Optional[Dict[str, int]] = None,
                         ) -> List[Dict[str, Any]]:
    """⛔⛔ README 166：测试文件里**每一条**探针 ⇒ **恰好一格门**。

    返回 `out + dropped`，每条带 `gate`：

        criterion      成为候选判据（⛔ 尚未跑沙箱，仍可能倒在下面的三验）
        form           判据形态不支持
        unserializable 期望值不可 JSON
        tuple          期望是 tuple（JSON 往返会降级成 list ⇒ 恒失败）
        unstable       时间/随机依赖

    ⛔⛔ **为什么必须和 `extract_criteria` 同源**：165 的教训就是"两套口径"
    —— 三验说能跑、判据执行说跑不过，于是 69 条假 F2P 被当成真鉴别力。
    本函数**只调** `extract_criteria`（不自带第二份规则）⇒ 门标签与实际
    抽取行为**不可能**漂移。契约 E 组盯着这条恒等式：

        #{gate == criterion}  ==  len(extract_criteria(...))

    ⛔ 只覆盖**抽取层**的门。沙箱一侧（参照实现上跑不过 / P2P / F2P）需要
    真跑 ⇒ 由 `scripts/probe_gate_attribution.py` 补，不在这里伪造。
    """
    dropped: List[Dict[str, Any]] = []
    out = extract_criteria(test_src, target, jsonable_only=jsonable_only,
                           stats=stats, dropped=dropped)
    return [dict(r, gate=GATE_CRITERION) for r in out] + dropped


def extract_criteria(test_src: str, target: str,
                     jsonable_only: bool = True,
                     stats: Optional[Dict[str, int]] = None,
                     dropped: Optional[List[Dict[str, Any]]] = None,
                     ) -> List[Dict[str, Any]]:
    """⛔ **纯 AST**：只要测试源码，产出候选判据（**尚未跑过，未验语义**）。

    `target` = 被测模块的完全限定名（`target_module("boltons/x.py")`）。
    ⛔ 这里**不碰**被测模块的任何版本 ⇒ 结构上拿不到"修复后源码"。

    返回 `[{call, expect|raises, gold, fn, kind}]`（`kind` ∈ expect/raises）。
    `stats` 若传入，会被填上 `n_raw / n_unstable / n_unjsonable / n_out`
    —— 每一步丢了多少都记账，调用方不能只看最终条数。

    `dropped`（README 166）若传入，会把**每条被丢掉的**探针 `{gate, attr,
    stmt, fn, kind}` 追加进去 ⇒ 调用方可以回答"这条断言被哪道门拦了"，
    而不是只看到总数变少。⛔ 不传 ⇒ 行为与 165 完全一致（向后兼容）。
    """
    st = {"n_raw": 0, "n_unstable": 0, "n_unjsonable": 0, "n_out": 0,
          "n_gold_resolved": 0}
    tree = ast.parse(test_src)
    aliases = module_aliases(tree)
    binds_by_scope = collect_bindings(test_src)
    unmapped: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    #: ⛔ 父包降级（README 165）只在**测试确实导入了这个包**时才开：
    #:    否则 `mi` 这种别名根本不存在，开了也白开；而开了却没别名 ⇒
    #:    不会误伤任何东西（记账为 0，读数自证）。
    pkg = target.rsplit(".", 1)[0] if "." in target else None
    if pkg and pkg not in set(aliases.values()):
        pkg = None
    probes = _iter_all_probes(test_src)
    #: ⛔ 形态不支持的**根本进不了流水线**（README 165 第二节）—— 它们不是
    #:    "被抽出来又丢掉"，而是压根没被映射。归因时必须**单独记账**。
    if dropped is not None:
        for p in probes:
            if not p["mapped"]:
                dropped.append({"gate": GATE_FORM, "attr": p["attr"],
                                "stmt": p["stmt"], "fn": p["fn"],
                                "kind": p["kind"], "call": None})
    #: ⛔ **稳定排序**保证产出顺序与 165 之前**一字不变**（bare → pytest
    #:    → unittest 三段拼接）；否则只是换了顺序，就会让"条数没变"掩盖
    #:    "索引变了"（判据名 `auto{j}` 跟着变 ⇒ 跨轮读数不可比）。
    _fam = {"bare": 0, "pytest": 1, "unittest": 2}
    raw = [(p["expr"], p["gold"], p["fn"], p["kind"], p)
           for p in sorted((q for q in probes if q["mapped"]),
                           key=lambda q: _fam[q["family"]])]
    for expr, gold, fn, kind, p in raw:
        st["n_raw"] += 1
        binds = dict(binds_by_scope.get("<module>", {}))
        binds.update(binds_by_scope.get(fn, {}))
        #: ⛔ **两条都跑**，不是为了兜底而是为了**记账**：`pkg` 降级是
        #:    试探性的（README 165），必须单独知道它救了多少条。
        #:    ⛔ 第一版写成"strict 失败才试 pkg" ⇒ 全漏：`resolve_probe`
        #:    只需**动过**就返回非 None（内联 `iterable` 也算动过）⇒
        #:    `mi.last(iterable)` 早就在 strict 里"成功"了，pkg 分支永远
        #:    不进 ⇒ 628 条里只救回 159 条，剩下的全带着 `mi.` 前缀
        #:    原样进沙箱（那里没有 `mi`）⇒ 又是一个"0 条"（第 24 条）。
        strict = resolve_probe(expr, binds, target, aliases)
        new = (resolve_probe(expr, binds, target, aliases, pkg=pkg)
               if pkg else strict)
        if pkg and new is not None and new != (strict or expr):
            st["n_pkg_demote"] = st.get("n_pkg_demote", 0) + 1
        call = new or expr
        if has_unstable_call(call):
            st["n_unstable"] += 1
            _drop(dropped, GATE_UNSTABLE, p, call)
            continue                      #: 自限 2：不可复现 ⇒ 不产出
        row = {"call": call, "fn": fn, "kind": kind, "orig": expr,
               "stmt": p["stmt"], "attr": p["attr"]}
        if kind == "raises":
            row["raises"] = gold
        else:
            g = gold_of(gold)
            if g is None:
                #: ⛔ **期望侧也要降级+内联**（README 165）。`assertEqual(a,
                #:    expected)` 里 `expected` 是个局部变量 ⇒ `literal_eval`
                #:    拿不到值 ⇒ 第一版整条丢弃。more-itertools 的 921 条
                #:    assertEqual 里 **293 条**的期望侧就长这样。
                #:    ⛔ 只有内联成 **literal** 才收：若展开后仍不是常量
                #:    （`expected = f(x)`），说明期望值本身依赖被测模块
                #:    ⇒ 那是循环论证，不收。
                gi = resolve_probe(gold, binds, target, aliases)
                if gi:
                    g2 = gold_of(gi)
                    if g2 is not None:
                        g, st["n_gold_resolved"] = g2, \
                            st["n_gold_resolved"] + 1
            if jsonable_only and (g is None or not _jsonable(g)):
                st["n_unjsonable"] += 1
                _drop(dropped, GATE_UNSERIALIZABLE, p, call)
                continue
            if jsonable_only and _has_tuple(g):
                st["n_tuple_gold"] = st.get("n_tuple_gold", 0) + 1
                _drop(dropped, GATE_TUPLE, p, call)
                continue
            #: ⛔ `gold` 保留**原文**（契约按原文对拍），`gold_value` 是
            #:    解出来的**值**（可能来自内联）。调用方必须用后者 ——
            #:    只用前者 ⇒ 期望侧是变量的判据会静默变成"0 条"
            #:    （第一版就是这样，293 条里一条没进）。
            row["gold"] = gold
            row["gold_value"] = g
        out.append(row)
    for k, v in unmapped_unittest_calls(test_src).items():
        unmapped[k] = unmapped.get(k, 0) + v
    st["n_out"] = len(out)
    st["n_unmapped"] = sum(unmapped.values())
    if stats is not None:
        stats.update(st)
        stats.setdefault("unmapped_kinds", {})
        for k, v in unmapped.items():
            stats["unmapped_kinds"][k] = \
                stats["unmapped_kinds"].get(k, 0) + v
    return out


# ─────────────────────── ④ 装载上下文（README 164）───────────────────────
def pkg_deps(src: str, pkgname: str) -> Set[str]:
    """抽源码里指向**同包**的模块依赖（相对导入 + 绝对包内导入）。

    ⛔ 只认**第一段**：`from .a.b import c` ⇒ 依赖 `a`；`import boltons.x.y`
    ⇒ 依赖 `x`。同包依赖的粒度就是模块，更细的没有意义。

    ⛔ 语法错误的源码返回空集（不 raise）：调用方拿它做裁剪，一个装不起来的
    模块不该让整个装载崩在这里 —— 它的失败会在沙箱里**如实**报出来。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level and n.module:
                out.add(n.module.split(".")[0])
            elif n.level:
                #: ⛔ `from . import a, b` —— **module 是 None，依赖藏在
                #:    `names` 里**。第一版只在 `n.module` 存在时才加依赖
                #:    ⇒ 这一形态整个漏掉 ⇒ attrs 25 个里 23 个装不起来
                #:    （症状 `cannot import name '_config' from 'attr'`）。
                #:    实测 attrs 的 `_make.py` 就写 `from . import _compat,
                #:    _config, setters`。
                for a in n.names:
                    out.add(a.name.split(".")[0])
            elif n.module:
                parts = n.module.split(".")
                if parts[0] == pkgname:
                    if len(parts) > 1:
                        out.add(parts[1])
                    else:
                        #: `from <pkg> import a`：a 可能是子模块也可能是属性
                        for a in n.names:
                            out.add(a.name.split(".")[0])
        elif isinstance(n, ast.Import):
            for a in n.names:
                parts = a.name.split(".")
                if len(parts) > 1 and parts[0] == pkgname:
                    out.add(parts[1])
    return out


def needed_modules(main_src: str, pkgname: str, pool: Dict[str, str],
                   include_init: bool = False) -> List[Tuple[str, str]]:
    """按**依赖闭包**裁剪 `pool`，返回 `[(相对名, 源码)]`（依赖在前）。

    ⛔⛔ 为什么要裁剪：第一版把**整个包**都塞进沙箱 ⇒ boltons 从 100% 掉到
    0%。原因是无关模块连坐 —— `ecoutils.py` 会 `import getpass → termios`，
    而 `termios` 在沙箱的 deny-modules 里 ⇒ 一个八竿子打不着的模块让整批
    装载失败。**被测模块只依赖它真正 import 的那几个**。

    ⛔ 循环依赖不追求严格拓扑：runner 侧是**先注册到 `sys.modules` 再 exec**，
    所以 A↔B 互相 import 不会死递归（只是属性可能未定义 —— 那是源码自己的
    性质，不该由装载层掩盖）。这里只保证「依赖尽量在前」，并用预算上限兜底
    防止迭代不终止。

    ⛔ `__init__` 默认**不带**：它常写 `from .<被测> import *`，而实测证明
    runner 已能处理；但带它会引入整包的副作用（同上，连坐风险）。
    """
    have = dict(pool)
    init = have.pop("", None)
    order: List[str] = []
    done: Set[str] = set()
    #: ⛔⛔ `onpath` 是**当前 DFS 路径**，不是"还在栈里的节点"。
    #:    第一版用 `pend = set(stack)` 判循环 ⇒ **误伤**：同层兄弟节点
    #:    （主干也直接依赖 `_config`）也被当成"已满足" ⇒ 依赖被排到
    #:    被依赖者**后面**。实测 attrs `validators.py` 因此得到顺序
    #:    `[_compat, setters, _make, _config, ...]` —— `setters` 排第 2，
    #:    而它依赖的 `_config`/`exceptions` 排在后面 ⇒ 装载直接炸。
    #:    循环检测必须按**路径**判（`onpath`），不能按"在不在栈里"。
    onpath: Set[str] = set()

    def visit(rel: str) -> None:
        if rel in done or rel not in have:
            return
        if rel in onpath:            #: 真循环 ⇒ 就地放下，不等待
            return
        onpath.add(rel)
        for d in sorted(pkg_deps(have[rel], pkgname)):
            visit(d)
        onpath.discard(rel)
        done.add(rel)
        order.append(rel)            #: 后序 ⇒ 依赖一定排在被依赖者前面

    for d in sorted(pkg_deps(main_src, pkgname)):
        visit(d)
    out = [(r, have[r]) for r in order]
    if include_init and init is not None:
        out.append(("", init))
    return out
