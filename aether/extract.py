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


# ─────────────────────────── 断言抽取（带作用域）─────────────────────────
def scoped_assertions(test_src: str):
    """抽 `(实际表达式, 期望文本, 所在函数名)`。

    ⛔ 多返回**所属函数**是必需的：`x` 在 A 函数里是 3、在 B 函数里是 'a'，
    没有作用域就会被当成同一个 x ⇒ 内联出语义错误的探针。
    """
    tree = ast.parse(test_src)
    out: List[Tuple[str, str, str]] = []

    def walk(node, fn: str):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, ch.name)
                continue
            if isinstance(ch, ast.Call):
                f = ch.func
                nm = getattr(f, "id", None) or getattr(f, "attr", None)
                if nm == "chk" and len(ch.args) == 2:
                    out.append((ast.unparse(ch.args[0]),
                                ast.unparse(ch.args[1]), fn))
            if isinstance(ch, ast.Assert):
                t = ch.test
                if (isinstance(t, ast.Compare) and len(t.ops) == 1
                        and isinstance(t.ops[0], ast.Eq)):
                    out.append((ast.unparse(t.left),
                                ast.unparse(t.comparators[0]), fn))
            walk(ch, fn)

    walk(tree, "<module>")
    return out


def scoped_raise_probes(test_src: str):
    """⛔ **期望异常**探针也带作用域 —— 与 `scoped_assertions` 同一个理由。

    第一版普查只抽返回值断言 ⇒ `c1c25da`（靠 2 条 raises 判据修好的那个 bug）
    在普查里显示「0 条鉴别力」⇒ 差点据此写下「内联增益全在形态门外」的假结论。
    ⛔ 一个「0」有两种含义：**真的没有** vs **抽漏了**（第 25 条）。
    """
    tree = ast.parse(test_src)
    out: List[Tuple[str, str, str]] = []

    def walk(node, fn: str):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, ch.name)
                continue
            if isinstance(ch, ast.With):
                for it in ch.items:
                    ctx = it.context_expr
                    if not isinstance(ctx, ast.Call) or not ctx.args:
                        continue
                    f = ctx.func
                    nm = getattr(f, "id", None) or getattr(f, "attr", None)
                    if nm != "raises":
                        continue
                    a0 = ctx.args[0]
                    exc = getattr(a0, "id", None) or getattr(a0, "attr", None)
                    if not exc:
                        continue
                    for st in ch.body:
                        if isinstance(st, ast.Expr):
                            out.append((ast.unparse(st.value), exc, fn))
            walk(ch, fn)

    walk(tree, "<module>")
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
    """把「指向被测模块的 alias.名字」替换成**模块内裸名**（方向不可逆）。"""

    def __init__(self, target: str, aliases: Dict[str, str]):
        self.target = target
        self.aliases = aliases
        self.changed = False

    def visit_Attribute(self, node):  # noqa: N802
        self.generic_visit(node)
        q = _qual_of(node, self.aliases)
        if q and q.startswith(self.target + "."):
            tail = q[len(self.target) + 1:]
            if "." not in tail and tail.isidentifier():
                self.changed = True
                return ast.copy_location(ast.Name(id=tail, ctx=node.ctx), node)
        return node


def demote_binds(binds: Dict[str, Any], target: str,
                 aliases: Dict[str, str], maxdepth: int = 4):
    """把一批绑定的 rhs 做自指模块降级。

    ⛔ **链式**（`h = b2h`）不在这里做 —— 那是 `_Inliner` 的递归展开负责的。
    第一版把两者混在一起 ⇒ 自检挂了才发现的职责边界。
    """
    cur = {k: v for k, v in binds.items()}
    for _ in range(maxdepth):
        changed = False
        nxt: Dict[str, Any] = {}
        for k, v in cur.items():
            tr = _Demote(target, aliases)
            newv = tr.visit(copy.deepcopy(v))
            ast.fix_missing_locations(newv)
            if tr.changed:
                changed = True
            nxt[k] = newv
        cur = nxt
        if not changed:
            break
    return cur


def demote_expr(expr: str, target: str, aliases: Dict[str, str]):
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
    tr = _Demote(target, aliases)
    new = tr.visit(tree)
    if not tr.changed:
        return None
    ast.fix_missing_locations(new)
    return ast.unparse(new)


def resolve_probe(expr: str, binds: Dict[str, Any], target: str,
                  aliases: Dict[str, str], rounds: int = 3):
    """把一条探针的上下文依赖降到沙箱里有解为止。

    ⛔ 两种形态**交替**下刀，缺一个就漏一大片；且 A 做完可能暴露出新的 B
    （反之亦然）⇒ 必须迭代到不动点。
    """
    dbinds = demote_binds(binds, target, aliases)
    cur = expr
    moved = False
    for _ in range(rounds):
        before = cur
        d = demote_expr(cur, target, aliases)
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


def extract_criteria(test_src: str, target: str,
                     jsonable_only: bool = True,
                     stats: Optional[Dict[str, int]] = None,
                     ) -> List[Dict[str, Any]]:
    """⛔ **纯 AST**：只要测试源码，产出候选判据（**尚未跑过，未验语义**）。

    `target` = 被测模块的完全限定名（`target_module("boltons/x.py")`）。
    ⛔ 这里**不碰**被测模块的任何版本 ⇒ 结构上拿不到"修复后源码"。

    返回 `[{call, expect|raises, gold, fn, kind}]`（`kind` ∈ expect/raises）。
    `stats` 若传入，会被填上 `n_raw / n_unstable / n_unjsonable / n_out`
    —— 每一步丢了多少都记账，调用方不能只看最终条数。
    """
    st = {"n_raw": 0, "n_unstable": 0, "n_unjsonable": 0, "n_out": 0}
    tree = ast.parse(test_src)
    aliases = module_aliases(tree)
    binds_by_scope = collect_bindings(test_src)
    out: List[Dict[str, Any]] = []
    raw = ([(a, e, f, "expect") for a, e, f in scoped_assertions(test_src)]
           + [(a, e, f, "raises") for a, e, f in scoped_raise_probes(test_src)])
    for expr, gold, fn, kind in raw:
        st["n_raw"] += 1
        binds = dict(binds_by_scope.get("<module>", {}))
        binds.update(binds_by_scope.get(fn, {}))
        new = resolve_probe(expr, binds, target, aliases)
        call = new or expr
        if has_unstable_call(call):
            st["n_unstable"] += 1
            continue                      #: 自限 2：不可复现 ⇒ 不产出
        row = {"call": call, "fn": fn, "kind": kind, "orig": expr}
        if kind == "raises":
            row["raises"] = gold
        else:
            g = gold_of(gold)
            if jsonable_only and (g is None or not _jsonable(g)):
                st["n_unjsonable"] += 1
                continue
            row["gold"] = gold
        out.append(row)
    st["n_out"] = len(out)
    if stats is not None:
        stats.update(st)
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
