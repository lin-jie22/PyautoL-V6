"""r48 · 用**真实代码**自动扩张任务世界（worldgen）。

为什么需要它
------------
r47 的系统级判决找到一个一票否决级事实：这台 20793 行的进化引擎，
**历史上从未走到第 3 代**血缘。拆完两个独立根因（resume 死锁 / 能力饱和）
后剩下的唯一真问题是第二个 ——

    tasks/ 里只有 4 个手工 toy（每个 task.py 3–10 条判据），
    两个胶囊就把 dev 集打满 ⇒ 轮 3 起 `no_weakness_evidence` 主动停机。
    **20793 行引擎的能力上限，被 4 个小函数锁死。**

本脚本不是"再手写几个 toy"，而是铺一条管道：把**真实 Python 源码**
（默认：V6 自己的 `aether/` 包）变成标准的 `task.py + tests.json` 环境。

四条不可省的纪律
----------------
1. **判据必须来自执行，不来自猜测**：每条 `(call, args, expect)` 都是真的
   在沙箱里跑出来的（`_probe` subprocess），expect 必须 JSON round-trip
   无损（ `json.loads(json.dumps(x)) == x` ）⇒ 判据可读、可比对、可存账本。
2. **同源**：`_probe` 用 ``exec(src, {"__name__": "task"})`` 加载源码，
   与被测侧 `environment._RUNNER` **完全同一种加载方式**。
   ⛔ 若两边加载方式不同（一边 import 包、一边 exec 文件），就会出现
   "生成时判据成立、评测时 import 失败"的幽灵任务（第 1 条纪律）。
3. **F2P 是硬门槛**：光有判据不够 —— 必须证明**变异能把它打穿**
   （复用 `environment.synthesize_tasks` 的 Fail-to-Pass 筛选）。
   变异打不穿的判据会被剔除 ⇒ 留下的才是"能被破坏、也能被修好"的真任务源。
   ⛔ 没有这一步，新环境就是注水：判据全绿但 Δ 恒 0 ⇒ 和 234 条恒绿闸同族。
4. **切片必须自包含**：被测源码经 **stdin** 交给 runner ⇒ 相对导入会断。
   这里只收"零跨模块依赖"的符号，并保留模块头部所有 **stdlib** import。

诚实边界（必须一起说）
----------------------
· expect 是 **characterization**（以生成时刻的实现行为为真），不是规格。
  当前实现若本身有 bug，判据会把 bug 固化成"正确答案"。这是有意的取舍：
  它换来的是"任意真实代码都能秒级变成可持续变强的题源"。
· 所以每个环境的 `tests.json` 里都写明 ` oracle: characterization `。
· 纯化是**启发式**（静态黑名单），不保证零副作用。

用法
----
    python3 scripts/worldgen.py --src aether --out tasks_gen --max-envs 12
    python3 scripts/worldgen.py --check            # 只审计、不写盘
"""
from __future__ import annotations

import argparse
import ast
import builtins
import itertools
import json
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

PY = sys.executable or "python3"

#: 副作用黑名单：函数体里出现这些根名 ⇒ 判为"不纯"，不作为任务源。
#: ⛔ 这是**启发式**，不是证明 —— 见模块 docstring 的"诚实边界"。
IO_ROOTS = frozenset({
    "os", "sys", "subprocess", "socket", "sqlite3", "requests", "urllib",
    "http", "time", "random", "threading", "asyncio", "tempfile", "shutil",
    "pickle", "signal", "resource", "ctypes", "multiprocessing", "ssl",
    "selectors", "atexit", "gc", "logging", "socketserver", "fcntl",
})
_BAD_CALLS = frozenset({"open", "exec", "eval", "input", "compile",
                        "__import__", "exit", "quit"})
_BAD_ATTRS = frozenset({"read", "write", "execute", "commit", "connect",
                        "sleep", "dump", "load", "spawn", "kill", "system"})

#: ── 参数取值池（JUnit 风格的边界值：空/单/多/负/极值）──────────────
_POOL_STR = ["", "a", "abc def", "Hello World", "x,y,z", "  pad  ",
             # ⛔ 这个多字节串是**刻意**留的边界值：swapcase / 去空格 / 切片
             #    三类变异在 ASCII 上很容易"看不出差别"，在多字节上才暴露；
             #    同时它验证 JSON 往返不丢非 ASCII（沙箱只传 JSON）。
             #    ⛔ 它是取值池里的**测试数据**，不是文档乱码（第 20 条）。
             "日本語テキスト", "a-b_c1"]
_POOL_INT = [0, 1, 2, 3, 7, -1, 42]
_POOL_FLOAT = [0.0, 1.0, 0.5, 2.5, -1.0]
_POOL_BOOL = [True, False]
_POOL_LIST = [[], [1, 2, 3], ["a", "b"], [3, 1, 2], ["x"]]
_POOL_DICT = [{}, {"a": 1}, {"a": 1, "b": 2}, {"x": "y"}]
_POOL_TUPLE = [(), (1, 2), ("a", "b")]
_POOL_SET = [set(), {1, 2}]            #: 只在注解明确要 set 时用（JSON 化会丢）

_NAME_HINTS: Sequence[Tuple[Tuple[str, ...], str]] = (
    (("text", "s", "name", "key", "word", "line", "content", "msg",
      "id", "path", "prefix", "suffix", "topic", "kind", "label"), "str"),
    (("n", "i", "j", "k", "count", "size", "limit", "idx", "index",
      "depth", "width", "total", "offset", "level", "cap"), "int"),
    (("x", "lo", "hi", "alpha", "score", "ratio", "weight", "frac",
      "threshold", "floor", "budget"), "float"),
    (("items", "seq", "rows", "values", "nums", "lst", "list", "parts",
      "tokens", "elems", "keys", "names", "args"), "list"),
    (("d", "map", "mapping", "dict", "cfg", "config", "data", "payload",
      "spec", "state", "params", "kwargs", "obj", "meta"), "dict"),
    (("flag", "enable", "strict", "ok", "verbose", "force", "dry",
      "recursive", "reverse", "ascending"), "bool"),
    (("pair", "tup", "range2", "bounds"), "tuple"),
    (("seen", "allowed", "chosen", "unique"), "set"),
)


# ═══════════════════════════════════════════════════════════════════
# 1) 静态筛选
# ═══════════════════════════════════════════════════════════════════
def _is_pure(node: ast.AST) -> bool:
    """启发式判纯：不含 IO 根名、不含危险调用、不是生成器/协程。"""
    for n in ast.walk(node):
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            return False
        if isinstance(n, (ast.Yield, ast.YieldFrom, ast.Await)):
            return False
        if isinstance(n, ast.Name) and n.id in IO_ROOTS:
            return False
        if isinstance(n, ast.Attribute):
            v = n.value
            while isinstance(v, ast.Attribute):
                v = v.value
            if isinstance(v, ast.Name) and v.id in IO_ROOTS:
                return False
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id in _BAD_CALLS:
                return False
            if isinstance(f, ast.Attribute) and f.attr in _BAD_ATTRS:
                return False
    return True


def _refs_of(node: ast.AST) -> Set[str]:
    """节点里引用到的**外部**根名（含 `a.b.c` 的 `a`）。

    ⛔⛔ **必须先减掉函数内部的局部绑定**（`_local_bound`）。这里出过一次
        一票否决级误杀：局部变量 `text` / `line` / `indents` 被当成"外部
        符号" ⇒ 每个候选都判"闭包不自包含" ⇒ `--src <stdlib>` 时 **11 个
        模块全军覆没**（verbose 才暴露）。少量：静态依赖分析不看赋值目标，
        只看出现过的名字，是**必然错**的。
    """
    out: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            v = n.value
            while isinstance(v, ast.Attribute):
                v = v.value
            if isinstance(v, ast.Name):
                out.add(v.id)
    return out - _local_bound(node)


def _local_bound(node: ast.AST) -> Set[str]:
    """节点内部**自己绑定**的名字：参数、赋值目标、for/with/comprehension
    目标、嵌套 def/class 名、`except ... as`。"""
    out: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.Global):
            out |= set(n.names)
    return out


def _annotation_type(a: ast.arg) -> Optional[str]:
    """从注解猜类型名；识别 Optional[...] / Sequence[...] 等常见写法。"""
    ann = a.annotation
    if ann is None:
        return None
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        return ann.value.strip().strip("\"'")
    head = ann
    while True:
        if isinstance(head, ast.Subscript):
            head = head.value
        elif isinstance(head, ast.BinOp):
            head = head.left
        else:
            break
    if isinstance(head, ast.Name):
        return head.id
    if isinstance(head, ast.Attribute):
        return head.attr
    if isinstance(head, ast.Constant):
        return str(head.value)
    return None


#: 注解名 → 取值池
_ANN_TO_POOL: Dict[str, List[Any]] = {
    "str": _POOL_STR, "int": _POOL_INT, "float": _POOL_FLOAT,
    "bool": _POOL_BOOL, "list": _POOL_LIST, "dict": _POOL_DICT,
    "tuple": _POOL_TUPLE, "set": _POOL_SET,
    "Sequence": _POOL_LIST, "List": _POOL_LIST, "Iterable": _POOL_LIST,
    "Mapping": _POOL_DICT, "Dict": _POOL_DICT, "Tuple": _POOL_TUPLE,
    "Set": _POOL_SET, "Optional": [None], "Any": [], "None": [None],
}


def _values_for(a: ast.arg, default: Optional[ast.AST] = None,
                limit: int = 6) -> List[Any]:
    """给一个参数挑候选值：**默认值最优先**，其次注解，再次参数名启发式。

    ⛔ 默认值排第一是有道理的：它是**作者自己写下来的合法调用形态**
       （作者调这个函数时真的用了它），比我们按名字猜的更像真实入参。
       ⛔ `ast.arg` 在 3.13 没有 `.default` —— 默认值在 `arguments.defaults`
       里**尾部对齐**，需由调用方算好传进来（这里曾 AttributeError 过）。
    """
    vals: List[Any] = []
    if default is not None:
        try:
            vals.append(ast.literal_eval(default))
        except Exception:                                    # noqa: BLE001
            pass
    t = _annotation_type(a)
    if t and t in _ANN_TO_POOL and _ANN_TO_POOL[t]:
        vals += _ANN_TO_POOL[t]
    low = a.arg.lower()
    for names, kind in _NAME_HINTS:
        if any(h == low or low.endswith("_" + h) or low.startswith(h + "_")
               for h in names):
            vals += {"str": _POOL_STR, "int": _POOL_INT, "float": _POOL_FLOAT,
                     "list": _POOL_LIST, "dict": _POOL_DICT,
                     "bool": _POOL_BOOL, "tuple": _POOL_TUPLE,
                     "set": _POOL_SET}[kind]
            break
    #: 去重保序
    seen: List[Any] = []
    for v in vals:
        if v not in seen:
            seen.append(v)
    return seen[:limit]


def _jsonable(x: Any) -> bool:
    try:
        return json.loads(json.dumps(x)) == x
    except Exception:                                        # noqa: BLE001
        return False


# ═══════════════════════════════════════════════════════════════════
# 2) 切片（自包含：stdlib import + 依赖闭包）
# ═══════════════════════════════════════════════════════════════════
def _module_symbols(tree: ast.Module) -> Dict[str, ast.stmt]:
    out: Dict[str, ast.stmt] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            out[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out[n.target.id] = n
    return out


def _import_bindings(n: ast.AST) -> Set[str]:
    """一条 import 语句向命名空间**绑定了哪些名字**。

    `import os.path` 绑定的是 `os`（不是 `os.path`）；`import x as y`
    绑定的是 `y` ⛔ 不把这点做对，按需裁剪就会把还在用的 import 剪掉。
    """
    out: Set[str] = set()
    if isinstance(n, ast.Import):
        for a in n.names:
            out.add(a.asname or a.name.split(".")[0])
    elif isinstance(n, ast.ImportFrom):
        for a in n.names:
            out.add(a.asname or a.name)
    return out


def _head_imports(tree: ast.Module, used: Set[str]) -> List[str]:
    """模块头部、**切片真用得到**的 stdlib import。

    ⛔ 两件事在这里同时发生，缺一不可：

    1. **剔除相对导入与 `aether.*`**：被测源码是经 stdin `exec` 的，没有包
       上下文 ⇒ 留着必然 `ImportError`（且是"评测侧才暴露"的那种幽灵失败）。
    2. **按需裁剪**（r48 实测补上的）：原版无条件保留**全部**头部 import，
       于是 `ledger` 的切片带上了 `import fcntl`，而 `fcntl` 正好在沙箱的
       `_AUDIT_DENY_MODULES` 里 ⇒ 沙箱 exec 时 `PermissionError` ⇒
       **整份判据基线全红**（`probe` 侧却 8/8 全绿 —— 两侧不同 witness，
       差点被误读成"判据不可用"）。成品文件因此被白白丢掉。
       ⇒ 只保留绑定名与 `used` 有交集的那些；`__future__` 恒保留（它改语义）。
    """
    lines: List[str] = []
    for n in tree.body:
        if not isinstance(n, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level or mod.startswith("aether"):
                continue
            if mod == "__future__":
                lines.append(ast.unparse(n))
                continue
        elif isinstance(n, ast.Import):
            names = [a.name for a in n.names]
            if any(x.startswith("aether") for x in names):
                continue          #: 混合那种极少见，保守跳过
        if _import_bindings(n) & used:
            lines.append(ast.unparse(n))
    return lines


def build_slice(tree: ast.Module, syms: Dict[str, ast.stmt],
                want: Sequence[str]) -> str:
    """把 `want` 这些符号及其**同模块内的依赖闭包**拼成一个自包含源码。"""
    needed: Set[str] = set()
    queue: List[str] = list(want)
    while queue:
        nm = queue.pop()
        if nm in needed or nm not in syms:
            continue
        needed.add(nm)
        node = syms[nm]
        for r in _refs_of(node):
            if r in syms and r not in needed:
                queue.append(r)
    used: Set[str] = set()
    for nm in needed:
        used |= _refs_of(syms[nm])
    #: ⛔ 按需裁剪 import：见 `_head_imports` 注释里的 `fcntl` 事故。
    head = _head_imports(tree, used)
    body: List[str] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)) and n.name in needed:
            body.append(ast.unparse(n))
        elif isinstance(n, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id in needed
                   for t in n.targets):
                body.append(ast.unparse(n))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) \
                and n.target.id in needed:
            body.append(ast.unparse(n))
    return "\n".join(head + ["", ""] + body) + "\n"


# ═══════════════════════════════════════════════════════════════════
# 3) 执行（判据发现）—— 与被测侧同源：subprocess + exec-to-namespace
# ═══════════════════════════════════════════════════════════════════
_PROBE = r'''
import json, sys, traceback
src = sys.stdin.read()
cases = json.loads(open(sys.argv[1], encoding="utf-8").read())["cases"]
ns = {"__name__": "task"}
err = None
try:
    exec(compile(src, "task.py", "exec"), ns)
except Exception:
    err = traceback.format_exc()[-400:]
out = []
for c in cases:
    if err:
        out.append({"name": c["name"], "ok": False,
                    "reason": "import-failed: " + (err or "")[-200:]})
        continue
    try:
        fn = ns.get(c["call"])
        if not callable(fn):
            out.append({"name": c["name"], "ok": False,
                        "reason": "symbol-not-callable"})
            continue
        got = fn(*c["args"], **c.get("kwargs", {}))
    except Exception as e:
        out.append({"name": c["name"], "ok": False,
                    "reason": "raised:" + type(e).__name__ + str(e)[:80]})
        continue
    try:
        if json.loads(json.dumps(got)) != got:
            out.append({"name": c["name"], "ok": False,
                        "reason": "json-lossy"})
            continue
    except Exception:
        out.append({"name": c["name"], "ok": False,
                    "reason": "json-unsafe"})
        continue
    out.append({"name": c["name"], "ok": True, "expect": got})
print(json.dumps({"results": out, "import_error": (err or "")[-300:]}))
'''


def probe_source(source: str, cases: List[Dict[str, Any]],
                 timeout: float = 15.0) -> Tuple[List[Dict[str, Any]], str]:
    """在子进程里跑一遍，收集每条 `@worldgen` 的 case 的真实返回值。"""
    with tempfile.TemporaryDirectory(prefix="wg_") as td:
        cases_f = Path(td) / "cases.json"
        runner_f = Path(td) / "probe.py"
        cases_f.write_text(json.dumps({"cases": cases}), encoding="utf-8")
        runner_f.write_text(_PROBE, encoding="utf-8")
        try:
            p = subprocess.run([PY, str(runner_f), str(cases_f)],
                               input=source.encode("utf-8"),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               timeout=timeout, cwd=td)
        except subprocess.TimeoutExpired:
            return [], "timeout"
        if p.returncode != 0:
            return [], f"rc={p.returncode}:{p.stderr.decode()[-200:]}"
        try:
            data = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
        except Exception as e:                                # noqa: BLE001
            return [], f"badjson:{e}"
        return data.get("results") or [], data.get("import_error") or ""


# ═══════════════════════════════════════════════════════════════════
# 4) 主流程
# ═══════════════════════════════════════════════════════════════════
def candidate_functions(tree: ast.Module,
                        syms: Dict[str, ast.stmt],
                        mod: str) -> List[Tuple[str, ast.FunctionDef]]:
    """挑可作为判据目标的公开函数：纯 + 有 return + 参数可 JSON 化。"""
    out: List[Tuple[str, ast.FunctionDef]] = []
    for name, node in syms.items():
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if name.startswith("_") or name.startswith("__"):
            continue
        if not node.body or not isinstance(node.body[-1], ast.Return) \
                or node.body[-1].value is None:
            continue
        if not node.args.args:
            continue                      #: 无参函数 ⇒ 变异打不穿的概率高
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
            continue
        if len(node.args.args) > 4:
            continue                      #: 组合爆炸
        if not _is_pure(node):
            continue
        out.append((name, node))
    out.sort(key=lambda kv: kv[0])
    return out


def _has_dataclass_deco(node: ast.ClassDef) -> bool:
    for d in node.decorator_list:
        nm = getattr(d, "id", None) or getattr(d, "attr", None)
        if nm in ("dataclass", "dataclass_transform", "dataclass_json"):
            return True
        if isinstance(d, ast.Call):
            f = d.func
            nm2 = getattr(f, "id", None) or getattr(f, "attr", None)
            if nm2 in ("dataclass", "dataclass_transform"):
                return True
    return False


def _closure_ok(nm: str, syms: Dict[str, ast.stmt],
                avail: Set[str]) -> bool:
    """`nm` 的递归依赖闭包是否**完全可满足**（切片自包含的判据）。

    ⛔ 这个判据改过三次，每一版都来自实测，**别退化回更早的版本**：

      ① `r in foreign and foreign[r] != mod`
         —— 在 stdlib 规模（1034 个文件）下**必然误杀**：`re` / `reduce` /
            `words` 这类通用名到处都定义了 ⇒ 函数明明用的是本模块或内置的
            那个，却被判成跨模块依赖（实测 16 个模块全军覆没）。
      ② `本模块优先、否则 foreign`
         —— 少一半误杀，但对"通过 stdlib import 绑定进来的名字"（`import re`
            的 `re`）仍然误判。
      ③ **现在的判据：`avail` 集合** —— 可用名字 = 本模块符号 ∪ builtins
         ∪ 会被保留的 stdlib import 绑定名。
         ⇒ `re` 可用（head import 保留了它）；
         ⇒ `content_hash` 这类由**相对导入**进来的符号不可用（相对导入被
            `_head_imports` 剔除）⇒ 正确判为"切片会 NameError"。
    """
    closure: Set[str] = set()
    queue: List[str] = [nm]
    while queue:
        x = queue.pop()
        if x in closure:
            continue
        closure.add(x)
        if x not in syms:
            return False                   #: 依赖了本模块没有的符号
        node = syms[x]
        #: ⛔ `@dataclass` 类在 exec-namespace 模式下**跑不起来**
        #:    （`dataclasses._is_type` 要 `cls.__module__` 在 `sys.modules`
        #:    里查得到，而 `ns = {"__name__": "task"}` 没有它 ⇒
        #:    `AttributeError: 'NoneType' object has no attribute '__dict__'`）。
        #:    ⛔ 这不是 worldgen 独有的限制：被测侧 `_RUNNER` 用的是**同一种**
        #:    加载方式 ⇒ 两侧都会炸。⇒ 这里主动跳过依赖它的候选，
        #:    别把"注定 import 失败"的切片写出去（V5 同款：降级成功 ≠ 可用）。
        if isinstance(node, ast.ClassDef) and _has_dataclass_deco(node):
            return False
        for r in _refs_of(node):
            if r in avail:
                if r in syms and r not in closure:
                    queue.append(r)         #: 本模块符号 ⇒ 继续递归闭包
            else:
                #: 既不是本模块符号，也不是内置/可用 import ⇒ 切片必然缺符号
                return False
    return True


def _avail_names(tree: ast.Module, syms: Dict[str, ast.stmt]) -> Set[str]:
    """切片里**哪些名字是拿得到的**：本模块符号 ∪ builtins ∪ 会保留的 import。

    ⛔ 星号导入（`from x import *`）**故意不算**可用：静态推不出它绑定了什么，
       冒充可用会让切片写出"看起来齐整、跑起来 NameError"的东西 ——
       宁可少几个环境，也不要产物口径失真（第 5 条：写入端"降级成功 ≠ 可用"）。
    """
    out: Set[str] = set(syms) | set(dir(builtins))
    for n in tree.body:
        if not isinstance(n, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if n.level or mod.startswith("aether"):
                continue            #: 相对/包内 import 会被 `_head_imports` 剔除
            if any(a.name == "*" for a in n.names):
                continue
        elif isinstance(n, ast.Import):
            if any(a.name.startswith("aether") for a in n.names):
                continue
        out |= _import_bindings(n)
    return out


def gen_cases(fn_name: str, node: ast.FunctionDef,
              cap: int = 48) -> List[Dict[str, Any]]:
    """给一个函数生成候选调用（含边界值），返回 `[{name, call, args}]`。
    """
    n_args = len(node.args.args)
    defaults = list(node.args.defaults)
    first_default = n_args - len(defaults)      #: 默认值尾部对齐
    per_arg = [_values_for(a,
                           defaults[i - first_default]
                           if i >= first_default and defaults else None)
               for i, a in enumerate(node.args.args)]
    if any(not v for v in per_arg):
        return []                          #: 有参数猜不出取值 ⇒ 放弃
    cases: List[Dict[str, Any]] = []
    combos = itertools.product(*per_arg)
    for i, combo in enumerate(combos):
        if i >= 4000:                      #: 防御：组合数上限
            break
        args = list(combo)
        if not all(_jsonable(a) for a in args):
            continue
        cases.append({"name": f"{fn_name}#{len(cases)}", "call": fn_name,
                      "args": args})
        if len(cases) >= cap:
            break
    return cases


def mutation_kill_matrix(source: str, cases: List[Dict[str, Any]],
                         ok_map: Dict[str, Dict[str, Any]],
                         kinds: Tuple[str, ...], n_mut: int,
                         timeout: float = 15.0,
                         ) -> Tuple[Dict[str, Set[int]], int]:
    """**每条候选判据能杀死哪几个变异体** —— 判据选择的事实依据。

    ⛔⛔ 为什么必须有这一步（2026-10-17 实测取证）：老逻辑是「每个函数取
        itertools.product 的**前 6 条**，再按**当前返回值**去重」。结果：

            doctest（637 行）只剩 1 条判据，6 次变异里 5 次**没让它变红**
            editor / encoder 同样 6 次变异只击穿 1 次

        ⇒ 造出来的环境**对变异免疫**。它们在 `--envs auto` 里把世界从 4
        撑到 10，但每个只贡献 1 条题 ⇒ 世界是「虚胖」的：账面上环境多了，
        题量没涨（这正是 r48 里「只加算子反而倒退到 1 代」的底噪）。

        ⛔ 根因不是"算子不匹配"，是**判据没被按杀伤力选过**：
        「当前返回值相同」的两条 case，在变异体下**可能不同** ——
        按当前返回值去重，等于把最有区分力的那几条提前扔掉。

    成本：每个变异体只需要**一次**批量 probe（probe_source 一次跑完所有
    case）⇒ n_mut 次子进程，与 case 数无关。
    """
    from aether.environment import mutate_source

    kills: Dict[str, Set[int]] = {}
    n_seen = 0
    for i in range(int(n_mut)):
        try:
            mut_src, _desc = mutate_source(source, which=i, kinds=kinds)
        except Exception:                                     # noqa: BLE001
            continue                       #: 没有可变异点 ⇒ 这个 which 跳过
        n_seen += 1
        res, err = probe_source(mut_src, cases, timeout=timeout)
        if err:
            continue
        rmap = {r["name"]: r for r in res}
        for c in cases:
            base = ok_map.get(c["name"])
            if base is None:
                continue
            r = rmap.get(c["name"])
            if r is None or not r.get("ok"):
                #: 变异后这条 case 直接抛错 ⇒ 行为变了 ⇒ 算杀死
                kills.setdefault(c["name"], set()).add(i)
                continue
            if json.dumps(r.get("expect"), sort_keys=True, default=str) \
                    != json.dumps(base.get("expect"), sort_keys=True, default=str):
                kills.setdefault(c["name"], set()).add(i)
    return kills, n_seen


def lethal_mutants(env: "Any", n_mut: int = 32,
                   kinds: Tuple[str, ...] = ("compare", "binop", "constant",
                                             "boolop", "unaryop", "string"),
                   timeout: float = 10.0, balanced: bool = False) -> int:
    """**题源强度**的唯一读数：有多少个变异体能真把判据打红。

    ⛔⛔ 为什么单独抽这个函数（2026-10-17 实测）：一个环境"在不在世界里"
        和"能不能贡献题"是两件事。r48 把世界从 4 撑到 10，但逐个量下来：

            doctest  23 次变异只击穿 1 次      editor  30 次只击穿 1 次

        ⇒ 这些环境让 `--envs auto` 的数字变好看了，题量一点没涨。
        「世界大小」是会撒谎的读数，**题源强度**不会：它就是
        `synthesize_tasks` 的 `valid`（真产生 F2P 任务的变异体数）。

    ⛔ 它与 `rep.attempted` 的区别（别拿尝试数冒充）：
        变异成功 ≠ 打红了判据。变异落在没人执行的分支上，attempted 照样 +1。
    """
    from aether.environment import synthesize_tasks
    rep = synthesize_tasks(env, max_n=int(n_mut), timeout=timeout,
                           kinds=kinds, balanced=balanced)
    return int(rep.valid)


def pick_tests(cases: List[Dict[str, Any]],
               ok_map: Dict[str, Dict[str, Any]],
               kills: Dict[str, Set[int]],
               keep_names: Sequence[str],
               mode: str = "kill", per_call: int = 6,
               ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """从候选里挑判据。`kill` = 变异杀伤导向（默认）；`first` = 老逻辑（对照）。

    ⛔ `first` 必须保留：它是**反证组**的对照组。没有它就无法证明
        「题量涨了」是选判据方式的功劳，还是素材本身的差异。
    """
    by_call: Dict[str, List[str]] = {}
    for c in cases:
        if c["name"] in ok_map:
            by_call.setdefault(c["call"], []).append(c["name"])
    cmap = {c["name"]: c for c in cases}

    def _mk(cn: str, idx: int) -> Dict[str, Any]:
        return {"name": f"{cmap[cn]['call']}_{idx}", "call": cmap[cn]["call"],
                "args": cmap[cn]["args"], "expect": ok_map[cn]["expect"]}

    tests: List[Dict[str, Any]] = []
    if mode == "first":
        seen_sig: Set[str] = set()
        picked: List[str] = []
        for fn in keep_names:
            per, seen_ret = 0, set()
            for cn in by_call.get(fn, []):
                if per >= per_call:
                    break
                rsig = json.dumps(ok_map[cn]["expect"], sort_keys=True,
                                  default=str)
                sig = json.dumps([cmap[cn]["call"], cmap[cn]["args"]],
                                 sort_keys=True, default=str)
                if sig in seen_sig or rsig in seen_ret:
                    continue
                seen_sig.add(sig)
                seen_ret.add(rsig)
                picked.append(cn)
                tests.append(_mk(cn, per))
                per += 1
        #: ⛔ `covered` 两种模式**都要给**：契约靠它做同条数对照，
        #:    只给 kill 的话 first 没有读数 ⇒ 对照退化成恒等断言。
        cov: Set[int] = set()
        for cn in picked:
            cov |= kills.get(cn, set())
        return tests, {"mode": "first", "covered": sorted(cov),
                       "n_covered": len(cov)}

    #: ── kill：贪心覆盖 ─────────────────────────────────────────
    #: 每轮挑「能杀死最多个『还没被杀过』的变异体」的 case；
    #: 平手时挑参数更"小"的（更可读、更不容易超时）。
    all_mut: Set[int] = set()
    for s in kills.values():
        all_mut |= s
    covered: Set[int] = set()
    chosen: List[str] = []
    per_count: Dict[str, int] = {}
    while True:
        best, best_gain = None, 0
        for cn in cmap:
            if cn not in ok_map or cn in chosen:
                continue
            fn = cmap[cn]["call"]
            if per_count.get(fn, 0) >= per_call:
                continue
            gain = len(kills.get(cn, set()) - covered)
            if gain > best_gain or (gain == best_gain and gain > 0
                                    and best is not None
                                    and len(json.dumps(cmap[cn]["args"]))
                                    < len(json.dumps(cmap[best]["args"]))):
                best, best_gain = cn, gain
        if best is None or best_gain == 0:
            break
        chosen.append(best)
        covered |= kills.get(best, set())
        per_count[cmap[best]["call"]] = per_count.get(cmap[best]["call"], 0) + 1

    #: ⛔ 覆盖完变异体就停会**凑不够条数**（实测 doctest 只选到 1 条，
    #:    被 min_tests 直接淘汰）。剩下按"杀伤力降序"补齐 —— 这样
    #:    kill 与 first 的**条数相同**，唯一变量是杀伤力 ⇒ 对照才成立。
    target = per_call * max(1, len([k for k in by_call if k in set(
        cmap[c]["call"] for c in ok_map)]))
    rest = sorted((cn for cn in cmap if cn in ok_map and cn not in chosen),
                  key=lambda cn: (-len(kills.get(cn, set())),
                                  len(json.dumps(cmap[cn]["args"])),
                                  cn))
    for cn in rest:
        if len(chosen) >= target:
            break
        fn = cmap[cn]["call"]
        if per_count.get(fn, 0) >= per_call:
            continue
        # ⛔ **不按返回值去重**：当前返回值相同的两条 case，在变异体下
        #    可能不同 —— 去重等于把有区分力的判据提前扔掉（实测取证）。
        chosen.append(cn)
        per_count[fn] = per_count.get(fn, 0) + 1
    for cn in chosen:
        fn = cmap[cn]["call"]
        per_count.setdefault(fn, 0)
        tests.append(_mk(cn, per_count[fn]))
        per_count[fn] += 1
    return tests, {"mode": "kill", "n_mutants": len(all_mut),
                   "n_killed": len(covered), "covered": sorted(covered),
                   "n_covered": len(covered),
                   "kill_rate": (len(covered) / len(all_mut)) if all_mut else 0.0}


def origin_label(p: Path, root: Path, stdlib: Path) -> str:
    """把**真实源文件路径**变成一个可读、不含用户名的溯源标签。

    ⛔⛔ 这个函数原来只收 `mod`（stem），用 `src_root/f"{mod}.py"` **重建**一个
       路径再套前缀。重建把子目录全丢了，于是 2026-10-22 实测 10 条生成环境里
       **7 条标签指向不存在的文件**：

           chaos   → stdlib/chaos.py     实际 stdlib/turtledemo/chaos.py
           core    → stdlib/core.py      实际 pip/_vendor/idna/core.py
           encoder → stdlib/encoder.py   实际 stdlib/json/encoder.py
           utils   → stdlib/utils.py     实际 pip/_vendor/requests/utils.py
           _inspect / _writer / _parser  同样是 vendor 包里的实现

       ⛔ 更坏的是 `stdlib/` 这个前缀：上面 4 条里有 3 条是**第三方**代码
       （pip 的 vendor 目录），却被标成标准库 ⇒ 标签不只是丢信息，是在撒谎。
       ⇒ 一律按**真实路径**取：子路径不许被压平，vendor 不许被冒充成 stdlib。

    ⛔ 分支顺序不能调：`site-packages` 在 stdlib **里面**，先判 stdlib 就会
       产出 `stdlib/site-packages/pip/_vendor/...`（字段虽真，但一眼看过去
       还是"标准库"，与本次要修的错法同形）。

    四支（都不含用户名，都能反推）：
      仓库内        ⇒ 仓库相对路径
      site-packages ⇒ `<site-packages 之后的那一段>`（`idna/core.py`…）
      其余 stdlib   ⇒ `stdlib/<相对>`（`stdlib/json/encoder.py`…）
      其它          ⇒ `<目录名>/<文件名>`
    """
    try:
        rp = Path(p).resolve()
    except OSError:                                   # 路径都取不到 ⇒ 原样给
        return str(p)

    try:
        return str(rp.relative_to(Path(root).resolve()))
    except ValueError:
        pass

    parts = rp.parts
    if "site-packages" in parts:
        #: 取**最后一个** `site-packages` 之后的整段（嵌套 venv 只认内层）
        idx = len(parts) - 1 - parts[::-1].index("site-packages")
        rest = Path(*parts[idx + 1:])
        return str(rest) if rest.parts else rp.name

    try:
        return "stdlib/" + str(rp.relative_to(Path(stdlib).resolve()))
    except (ValueError, KeyError, OSError):
        return f"{rp.parent.name}/{rp.name}"


def make_env(name: str, source: str, tests: List[Dict[str, Any]],
             out_dir: Path, meta: Dict[str, Any]) -> bool:
    d = out_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.py").write_text(source, encoding="utf-8")
    (d / "tests.json").write_text(json.dumps(
        {"env_id": name, "note": meta.get("note", ""),
         "oracle": "characterization",
         "generated_by": "scripts/worldgen.py",
         "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "source_origin": meta.get("origin", ""),
         "tests": tests}, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="用真实代码扩张 V6 任务世界")
    ap.add_argument("--src", default="aether", help="源码根目录")
    ap.add_argument("--out", default="tasks_gen", help="环境输出根目录")
    ap.add_argument("--max-envs", default="12", help="最多产出几个环境")
    ap.add_argument("--min-tests", default="6",
                    help="一个环境的有效判据少于此 ⇒ 丢弃")
    ap.add_argument("--max-combos", default="48", help="每函数最多试探多少组入参")
    ap.add_argument("--per-call", default="6",
                    help="每个函数最多留几条判据")
    ap.add_argument("--pick", default="kill", choices=["kill", "first"],
                    help="判据选择方式：kill=按**变异杀伤率**贪心选（默认）；"
                         "first=取前 N 条并按当前返回值去重（老逻辑，作对照）")
    ap.add_argument("--mutations", default="32",
                    help="F2P 验证时尝试多少个变异点（= mutate_source 的 which 上限）。"
                         "⛔⛔ 这是**题源强度的主杠杆**（2026-10-17 实测）："
                         "默认 8 只取 which 0..7 ⇒ doctest/encoder/shlex 各自只有"
                         " 0–1 个能击穿判据的变异体 ⇒ 环境「虚胖」（世界从 4 涨到"
                         " 10，题量没涨）。扩到 64 实测有效变异体 0→6 / 1→5 / 1→3。")
    ap.add_argument("--min-lethal", default="2",
                    help="有效变异体（真能打红判据的）少于此 ⇒ 弃。"
                         "⛔ 只要求 ≥1 太松：editor 实测 39 个变异点、0 个有杀伤力，"
                         "却靠一次「变异后跑不完」混过了门槛")
    ap.add_argument("--min-behavior", default="0",
                    help="有效题里**真行为变化**（非崩溃）的条数少于此 ⇒ 弃。"
                         "⛔ 默认 0：不淘汰任何既有环境 ⇒ 历史读数可比。"
                         "⛔ 显开启例：eventflow 实测 6 条有效题里只有 1 条是"
                         "行为变化（5 条是崩溃）⇒ 含金量 17%；崩溃题任何"
                         "「把源码改回接近基线」的修法都能顺手修好 ⇒ 注水")
    ap.add_argument("--kinds", default="compare,binop,constant,boolop,unaryop,string",
                    help="F2P 验证用的变异算子。⛔ 默认含 `string`：真实代码里"
                         "字符串处理是常态，而默认三种算子打不穿纯串处理函数"
                         "（实测 textwrap 12 条判据、0 次击穿）")
    ap.add_argument("--balanced", action="store_true",
                    help="F2P 验证时**按维度轮流**取变异点（默认按遍历序取前 N 个）。"
                         "⛔ 默认关：它换的是一批题 ⇒ 与既有题源强度读数不可比。"
                         "⛔ 开启后 `--min-lethal` 的门槛含义会变（同样的环境，"
                         "实测有效变异体更多）⇒ 那是新口径的读数，别与旧表并排看")
    ap.add_argument("--timeout", default="10.0")
    ap.add_argument("--only", default="", help="只处理这些模块（逗号分隔）")
    ap.add_argument("--check", action="store_true",
                    help="只审计不写盘（预算勘察）")
    ap.add_argument("--verbose", action="store_true",
                    help="打印**每个**模块的淘汰阶段（排产率时必开）")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from aether.environment import (Environment, synthesize_tasks,  # noqa: E402
                                    verify_source)

    src_root = (root / args.src) if not Path(args.src).is_absolute() \
        else Path(args.src)

    #: ⛔ `source_origin` **不许写绝对路径**（r67）：它是溯源标签，不是运行
    #:    依赖。写成绝对路径的后果是仓库里躺着 10 行"某人的家目录"，别人
    #:    clone 下来第一眼以为这库带着私人路径。
    #: ⛔⛔ 而"不许绝对路径"这条修完之后，标签又变成了**按 stem 重建**的假标签
    #:    （2026-10-22 审计：10 条里 7 条指向不存在的文件，3 条把 pip 的 vendor
    #:    代码标成 `stdlib/`）⇒ 取值一律走模块级 `origin_label(真实路径)`，
    #:    `note` 与 `source_origin` 用**同一个** label（两个字段各渲染一遍就会漂，
    #:    实测修前 10/10 全都不同源）。
    try:
        _stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    except (KeyError, OSError):
        _stdlib = Path(sys.prefix)
    out_dir = Path(args.out) if Path(args.out).is_absolute() \
        else root / args.out
    max_envs = int(args.max_envs)
    min_tests = int(args.min_tests)
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    files = sorted(src_root.rglob("*.py"))
    all_syms: Dict[str, Dict[str, ast.stmt]] = {}
    trees: Dict[str, ast.Module] = {}
    if only:
        #: 只扫 `only` 里的模块 —— 全 stdlib 有 1034 个文件，逐个 parse
        #: 会让每次迭代都等几十秒。
        files = [p for p in files if p.stem in only]
    trees: Dict[str, ast.Module] = {}
    paths_by_stem: Dict[str, Path] = {}
    for p in files:
        if p.stem == "__init__":
            continue
        if p.stem in trees:            #: 同名 stem ⇒ 后者覆盖，必须看见
            print(f"  ⚠️ 同名模块覆盖：{p}")
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except Exception as e:                               # noqa: BLE001
            if args.verbose:
                print(f"  · {p.stem}: parse 失败 {type(e).__name__}")
            continue
        trees[p.stem] = tree
        all_syms[p.stem] = _module_symbols(tree)
        #: ⛔ 真实路径必须**当场记下来**：后面所有循环都只按 stem 索引
        #:    （`for mod, tree in trees.items()`），路径一丢就只剩"重建"这条路，
        #:    而重建会把子目录压平（本次缺陷的根因）。
        paths_by_stem[p.stem] = p

    #: 「哪些名字属于别的模块」—— 用于排除跨模块依赖（切片会断）
    foreign: Dict[str, str] = {}
    for mod, syms in all_syms.items():
        for nm in syms:
            foreign.setdefault(nm, mod)

    print(f"扫描 {len(trees)} 个模块于 {src_root}")
    t0 = time.time()
    made = 0
    report: List[Dict[str, Any]] = []

    for mod, tree in trees.items():
        if made >= max_envs:
            break
        if only and mod not in only:
            continue
        syms = all_syms[mod]
        cands = candidate_functions(tree, syms, mod)
        if len(cands) < 2:
            if args.verbose:
                print(f"  · {mod}: 候选不足（{len(cands)} 个可选函数）")
            continue

        #: ⛔ 排除依赖**切片给不了**的符号的函数。判据见 `_closure_ok`：
        #:    这里靠 `avail`（本模块符号 ∪ builtins ∪ 会被保留的 import），
        #:    而不是"别处有没有同名" —— 后者在 stdlib 规模下必然误杀。
        avail = _avail_names(tree, syms)
        keep: List[Tuple[str, ast.FunctionDef]] = []
        for nm, node in cands:
            if _closure_ok(nm, syms, avail):
                keep.append((nm, node))
        if not keep:
            if args.verbose:
                print(f"  · {mod}: {len(cands)} 个候选的依赖闭包都不自包含")
            continue

        source = build_slice(tree, syms, [nm for nm, _ in keep])

        #: ── 判据发现 ────────────────────────────────────────────
        cases: List[Dict[str, Any]] = []
        for fn, node in keep:
            cases += gen_cases(fn, node, cap=int(args.max_combos))
        if not cases:
            if args.verbose:
                print(f"  · {mod}: {len(keep)} 个函数的入参都猜不出取值")
            continue
        results, imp_err = probe_source(source, cases,
                                        timeout=float(args.timeout) + 8.0)
        if imp_err:
            print(f"  · {mod}: 切片不可用（{imp_err[:70]}）")
            continue
        ok_map = {r["name"]: r for r in results if r.get("ok")}
        by_call: Dict[str, List[Dict[str, Any]]] = {}
        for c in cases:
            r = ok_map.get(c["name"])
            if r is None:
                continue
            by_call.setdefault(c["call"], []).append(c["name"])
        #: ── 判据选择：`kill`（变异杀伤导向）或 `first`（老逻辑，作对照）──
        _kinds_t = tuple(x.strip() for x in args.kinds.split(",") if x.strip())
        pick_mode = str(getattr(args, "pick", "kill") or "kill")
        if pick_mode == "kill":
            kills, n_mut_seen = mutation_kill_matrix(
                source, cases, ok_map, _kinds_t, int(args.mutations),
                timeout=float(args.timeout) + 8.0)
        else:
            kills, n_mut_seen = {}, 0
        tests, pick_info = pick_tests(cases, ok_map, kills,
                                      [nm for nm, _ in keep],
                                      mode=pick_mode, per_call=int(args.per_call))
        if args.verbose:
            print(f"    {mod}: 判据选择 mode={pick_mode} "
                  f"{pick_info} ⇒ {len(tests)} 条")
        if len(tests) < min_tests:
            print(f"  · {mod}: 判据 {len(tests)} 条 < {min_tests} ⇒ 弃")
            continue

        #: ── F2P 硬门槛：变异必须能把它打穿 ──────────────────────
        tmp = Path(tempfile.mkdtemp(prefix="wg_env_"))
        make_env(mod, source, tests, tmp,
                 {"origin": origin_label(paths_by_stem[mod], root, _stdlib)})
        try:
            env = Environment.from_dir(tmp / mod)
            base = verify_source(env, timeout=float(args.timeout))
            n_pass = sum(1 for c in base.checks if c.passed)
            if n_pass != len(tests):
                print(f"  · {mod}: 基线就有 {len(tests) - n_pass} 条判据不绿 ⇒ 弃")
                continue
            _kinds = tuple(x.strip() for x in args.kinds.split(",") if x.strip())
            rep = synthesize_tasks(env, max_n=int(args.mutations),
                                   timeout=float(args.timeout),
                                   kinds=_kinds,
                                   balanced=bool(args.balanced))
        except Exception as e:                               # noqa: BLE001
            print(f"  · {mod}: 验证异常 {type(e).__name__}: {e} ⇒ 弃")
            if args.verbose:
                import traceback
                traceback.print_exc()
            continue
        #: ⛔ **题源强度闸**：只要求 valid ≥ 1 会让「虚胖环境」混进来 ——
        #:    实测 editor 有 39 个变异点、却只有 0 个能真打红判据（它那
        #:    1/8 的"产出"来自变异后跑不完，不是行为变了）⇒ 进套件只会
        #:    贡献 Δ=0 的题，把世界撑大的同时把判别力稀释掉。
        if rep.valid < int(args.min_lethal):
            print(f"  · {mod}: {len(tests)} 条判据，{rep.attempted} 次变异只有"
                  f" {rep.valid} 次击穿 < {args.min_lethal} ⇒ 弃（题源太弱）")
            continue
        #: ⛔ **含金量闸**：`valid` 会撒谎 —— 它只数"打红了几条"，不区分
        #:    是**行为变了**还是**崩了**。实测 eventflow 6 条有效题里 5 条
        #:    是崩溃类（含金量 17%）：崩溃题任何"把源码改回接近基线"的
        #:    修法都能顺手修好 ⇒ 账面多了题，reward 里看不见 ⇒ 与 editor
        #:    那个虚胖环境同一个病，只是这次量具自己看得见。
        #:    ⛔ 默认 0（不淘汰任何既有环境，历史可比）；显式开启才是新口径。
        if rep.n_behavior < int(args.min_behavior):
            print(f"  · {mod}: {rep.valid} 次击穿里只有 {rep.n_behavior} 次是"
                  f"**行为变化**（其余 {rep.n_crash} 崩溃 / {rep.n_unknown} 未知）"
                  f" < {args.min_behavior} ⇒ 弃（含金量不足）")
            continue
        #: ⛔ 只留**被证明可被击穿**的判据（出现过在 fail_to_pass 里的）
        broken: Set[str] = set()
        for t in rep.tasks:
            broken |= set(t.fail_to_pass)
        strong = [t for t in tests if t["name"] in broken]
        if len(strong) < min_tests:
            print(f"  · {mod}: 可被击穿的判据只有 {len(strong)} 条 ⇒ 弃")
            continue
        weak = len(tests) - len(strong)
        #: ⛔⛔ 两个字段用**同一个** label：`note` 原先写死 `aether/{mod}.py`，
        #:    于是 2026-10-22 实测 10/10 的 note 与 source_origin **互不一致**
        #:    （note 说 aether/utils.py，source_origin 说 stdlib/utils.py，
        #:     两个文件都不存在）—— 同一件事渲染两遍，就会漂，而且两边
        #:    都不知道自己错（第 46 条的老形状，换了个字段）。
        origin = origin_label(paths_by_stem[mod], root, _stdlib)
        note = (f"由 {origin} 切片自动生成：{len(strong)} 条经 F2P 验证"
                f"的判据（另有 {weak} 条对变异免疫，已剔除）。"
                f"expect 为生成时刻的实现行为（characterization），非规格。")
        print(f"  ✓ {mod}:  {len(strong):3d} 条有效判据 / {len(keep)} 个函数 /"
              f"  {len(source.splitlines()):4d} 行源码 / 变异产出率"
              f" {rep.valid}/{rep.attempted}")
        report.append({"env": mod, "n_tests": len(strong),
                       "n_functions": len(keep),
                       "n_source_lines": len(source.splitlines()),
                       "n_synth_valid": rep.valid, "n_synth_attempted": rep.attempted,
                       #: ⛔ 打红原因三态分账：题量必须伴生"含金量"，
                       #:    否则世界又会被崩溃题撑胖（第 37 条的直接延伸）。
                       "n_synth_behavior": rep.n_behavior,
                       "n_synth_crash": rep.n_crash,
                       "n_synth_unknown": rep.n_unknown,
                       "behavior_rate": round(rep.behavior_rate, 4),
                       "n_weak_dropped": weak, "fail_to_pass": sorted(broken),
                       #: ⛔ 判据选择方式必须进报告：题量涨了要能归因到它，
                       #:    不能靠"这次素材好"糊过去。
                       "pick": pick_info})
        if not args.check:
            make_env(mod, source, strong, out_dir,
                     {"origin": origin, "note": note})
        made += 1

    print(f"\n产出 {made} 个环境 / 用时 {time.time() - t0:.1f}s")
    if args.check:
        print("（--check 模式：未写盘）")
    else:
        print(f"落盘：{out_dir}")
        (out_dir / "_worldgen_report.json").write_text(json.dumps(
            report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
