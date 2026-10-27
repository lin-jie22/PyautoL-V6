#!/usr/bin/env python3
"""⛔ **重测「形态」那把尺子** —— 153 的口径可能偏悲观（第 24 条：先怀疑量具）。

═══ 一、为什么要重测 ═══

153 的形态普查（`realbug_shape_census.py`）判據是：

    整棵 Module 树同构 + 差异只出现在算子/常量  ⇒ operator（revert_* 够得着）
    其余                                        ⇒ structural（够不着）

⇒ 读数：真实修复 **84.9% 结构级，真算子级只有 5.5%**。
⇒ 159 据此写下「合成层的射程上限就在这 5.5% 里」，160 又拿它当分寸。

⛔ 但这个口径**要求整个 Module 都同构**。真实的 bug-fix commit 常常顺带
改了别处（重构另一个函数、加个 import、调整类结构）—— 整棵树立刻不同构
⇒ 被判 structural。**哪怕核心修复就是那个 `>` → `>=`。**

    「这个 commit 纯不纯」 ≠ 「这个 bug 的核心修复在不在射程内」。
    前者让 third-party 的顺手改动决定了 V6 的能力边界判定。

═══ 二、新尺子：按「可独立编辑单元」判，而不是按整棵树 ═══

不再问「整棵树同不像」，改问：

    **是否有一个顶层 def/class，它从 buggy 到 fixed 的变化全部落在
    「已有算子的翻转 / 常量值的改动」上 —— 一次 INSERT/DELETE 都不需要？**

理由：154 之后修法已能产出**最小编辑**，所以 commit 整体形态不重要，
重要的是「从 broken 到 fixed 需要经过什么类型的编辑」。而 INSERT/DELETE
语句是合成层（枚举对**已有节点**的变异）结构上做不到的。

三档（从宽到严，全部报告，**不挑好看的那个**）：

    L1 whole_file  —— 整个文件的差异全是算子/常量级（= 旧尺子）
    L2 some_unit   —— 至少一个顶层单元内部是纯算子/常量级
    L3 synth       —— 至少一个顶层单元内部的变化全落在**合成层实际候选集**里
                      （比较算子翻转 / 数值常量 ±1 / Add↔Sub）
                      ⇒ 其余 UPDATE 形态（字符串常量、and/or、not 增删）
                        是 revert_* 够得着但 synth 够不着的

═══ 三、⛔⛔ 自检（判错就停机，不许带病开工）═══

  S1 `c1c25da`（`val > 2**len_` → `>=`）⇒ 必须 L3（已知被 synth 修好）
  S2 `5eb6cc0`（新增两行 `if k < 0: ...`）⇒ 必须 **unreachable**
     —— 它需要 INSERT 语句，任何只做「翻转已有节点」的系统都够不到
  S3 判档函数本身：人造 INSERT ⇒ unreachable；人造 CMP 翻转 ⇒ L3

═══ 四、读数纪律 ═══

  · 每条 commit 一行，**逐个列出**「旧判死/新判活」的那些，供人工核对；
  · 带上 157 的 `after`（有鉴别力的判据条数）交叉 ⇒ 直接看出漏斗卡在哪；
  · 多点改动会被 L2/L3 判为可达，但 synth 一次只改一处 ⇒ **残留里标注**。

跑法：
    python scripts/probe_shape_ruler.py --repo /tmp/realrepo/boltons
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OP_CMP = {"Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn"}
OP_ARITH = {"Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
            "LShift", "RShift", "BitOr", "BitAnd", "BitXor"}
OP_ADD_SUB = {"Add", "Sub"}

#: ⛔⛔ 合成层（`aether/synthesize.py` 的 `synth_candidates`）实际能枚举的
#:    三类变异，从它的产物描述**逐项对应**到这里 —— 不许写"看起来对"的名字。
#:
#:    谁给合成层加一类候选而不来登记 ⇒ 本脚本的 L3 会**静默低估**射程，
#:    症状是「可达率莫名变低且没人察觉」（第 24 条那类失败）。
#:    契约 `contract_shape_ruler_test.py` 的 C2/C3 两组盯着这条。
_SYNTH_DESC_MAP = {
    "cmp": "cmp",           # 比较算子翻转
    "int+1": "num1",        # 整数常量 ±1（两侧对应同一个 L3 类别）
    "int-1": "num1",
    "binop-flip": "addsub",  # Add ↔ Sub
}
SYNTH_KINDS = frozenset(_SYNTH_DESC_MAP.values())


def _git(repo: Path, *a: str) -> str:
    p = subprocess.run(["git", *a], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


# ─────────────────────────── 差异搜集 ───────────────────────────

#: ⛔ 「可变异的原子」：这些节点的差异由 `_point_kind` 分类，**必须先于**
#:    类型相同与否的检查处理 —— 否则 `Gt` vs `GtE` 会在走到算子分类之前
#:    就被 `type(a) is not type(b)` 判成 struct（本脚本第一版就栽在这里：
#:    连纯 `>` → `>=` 都判不出来，症状是「处处是差异」）。
ATOMS = (ast.operator, ast.cmpop, ast.boolop, ast.unaryop, ast.Constant)


def _point_kind(a: ast.AST, b: ast.AST) -> str | None:
    """两个**原子**节点之间的差异属于哪一类。None ⇒ 无差异。

    ⛔ 必须先判「完全相同」：`Constant(10)` vs `Constant(10)` 若走到
       最后会被落成 `const_num` ⇒ 文件里每一个字面量都变成一处"改动"。
    """
    if type(a) is type(b):
        if isinstance(a, (ast.operator, ast.cmpop, ast.boolop, ast.unaryop)):
            return None
        if isinstance(a, ast.Constant) and a.value == b.value:
            return None
    if isinstance(a, ast.cmpop) or isinstance(b, ast.cmpop):
        return "cmp"
    if isinstance(a, ast.operator) or isinstance(b, ast.operator):
        if type(a).__name__ in OP_ADD_SUB and type(b).__name__ in OP_ADD_SUB:
            return "addsub"
        return "arith"
    if isinstance(a, ast.boolop) or isinstance(b, ast.boolop):
        return "boolop"
    if isinstance(a, ast.unaryop) or isinstance(b, ast.unaryop):
        return "unaryop"
    if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
        va, vb = a.value, b.value
        if isinstance(va, bool) or isinstance(vb, bool):
            return "const_bool"
        if isinstance(va, str) or isinstance(vb, str):
            return "const_str"
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            return "num1" if abs(va - vb) == 1 else "const_num"
        return "const_other"
    return None


def diff_points(a: ast.AST, b: ast.AST, pool: set | None = None):
    """⛔ 返回 [(类别, 说明)]。逐层对齐同类型节点；结构不同 ⇒ 记 struct。

    ⛔ 与 153 的差别就在这里：153 遇到第一个不同就判整个文件 structural，
       本函数把差异**分解到最小单元**再分类 ⇒ 别处的改动不会连坐。
    """
    # ① 原子（算子 / 常量）：先分类，含「完全相同」的快速返回
    if isinstance(a, ATOMS) or isinstance(b, ATOMS):
        #: ⛔ 只有**两边都是原子**才谈得上「翻转/改值」。一边 Constant 一边
        #:    BinOp 是**结构替换**，不是值变了 —— 漏了这条会把
        #:    `return 0` → `return f(x)` 判成「没有差异」（本版第二处 bug）。
        if not (isinstance(a, ATOMS) and isinstance(b, ATOMS)):
            return [("struct", f"{type(a).__name__}→{type(b).__name__}"
                               + _exh_tag(b, pool))]
        k = _point_kind(a, b)
        if k is None:
            return []
        return [(k, (f"Constant {getattr(a, 'value', '?')!r}"
                     f"→{getattr(b, 'value', '?')!r}"
                     if isinstance(a, ast.Constant)
                     else f"{type(a).__name__}→{type(b).__name__}"))]
    # ② 类型变了 ⇒ 结构级
    if type(a) is not type(b):
        return [("struct", f"{type(a).__name__}→{type(b).__name__}"
                           + _exh_tag(b, pool))]
    ca = list(ast.iter_child_nodes(a))
    cb = list(ast.iter_child_nodes(b))
    if len(ca) != len(cb):
        #: ⛔ 子节点数变了 ⇒ INSERT/DELETE。额外记一笔：fixed 侧的这棵子树
        #:    能否在 buggy 文件里找到一模一样的另一处（⇒ 不是新东西，是
        #:    「把别处已有的子树搬过来」）。
        return [("struct", f"{type(a).__name__} 子节点 {len(ca)}→{len(cb)}"
                           + _exh_tag(b, pool))]
    # ③ 叶子（Name / arg / alias …）：没有子节点可比 ⇒ 直接比 dump，
    #    否则「x 改名成 y」会被漏掉 ⇒ 偏乐观
    if not ca:
        if ast.dump(a) == ast.dump(b):
            return []
        return [("struct", f"{type(a).__name__} 叶子值变")]
    out: list = []
    for x, y in zip(ca, cb):
        out.extend(diff_points(x, y, pool))
    return out


def _exh_tag(node: ast.AST, pool: set | None) -> str:
    """: fixed 侧子树是该位置上「新写的」还是「文件里已有的」？"""
    if pool is None:
        return ""
    return "[existing]" if ast.dump(node) in pool else "[novel]"


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """⛔ 剥掉所有 docstring 再比。

    ⛔ 这是本轮最要紧的一处修正。修正前，`const_str`（多半就是 docstring）
       会让「改了文档字符串」的单元被判成「单元内纯 UPDATE」⇒ 一个真正的
       结构性修复，只要顺手改了个 docstring，就被判成**可达**（第一版全量
       跑出来的 8 个「口径分歧」全是这个 —— 逐条核对后无一例外）。
       ⇒ docstring 不影响任何判据，必须从「这算不算一处变化」里剔掉。
    """
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            body = getattr(n, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                if len(body) > 1:
                    n.body = body[1:]
                else:
                    n.body = [ast.Pass()]
    return tree


_UNIT_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def units_of(tree: ast.Module) -> dict:
    return {n.name: n for n in tree.body if isinstance(n, _UNIT_TYPES)}


def analyze(broken_src: str, fixed_src: str) -> dict:
    """返回本次修复在各单元上的差异构成 + 三档判定 + 扩展档位。"""
    ta, tb = ast.parse(broken_src), ast.parse(fixed_src)
    _strip_docstrings(ta)
    _strip_docstrings(tb)
    ua, ub = units_of(ta), units_of(tb)

    #: ⛔ pool = buggy 侧**整个文件**里出现过的所有子树的 dump。
    #:    用来判 fixed 侧那处新东西是「新写的」还是「从别处搬来的」
    #:    ⇒ 后者意味着把候选集扩到「用文件里已有的表达式替换」就够得着。
    pool = {ast.dump(n) for n in ast.walk(ta)}

    units: dict = {}
    for name in ua.keys() & ub.keys():
        pts = diff_points(ua[name], ub[name], pool)
        units[name] = pts

    toplevel = []
    for name in ub.keys() - ua.keys():
        toplevel.append(("struct", f"新增顶层 {name}"))
    for name in ua.keys() - ub.keys():
        toplevel.append(("struct", f"删除顶层 {name}"))

    #: 两边各自的**非单元**语句（import / 全局赋值）差异也算 struct
    others = 0
    sa = [n for n in ta.body if not isinstance(n, _UNIT_TYPES)]
    sb = [n for n in tb.body if not isinstance(n, _UNIT_TYPES)]
    if len(sa) != len(sb) or any(
            ast.dump(x) != ast.dump(y) for x, y in zip(sa, sb)):
        others = 1

    all_pts = [p for pts in units.values() for p in pts] + toplevel
    kinds = [k for k, _ in all_pts]

    wide_UPD = {"cmp", "arith", "addsub", "boolop", "unaryop",
                "const_str", "const_bool", "const_num", "num1",
                "const_other"}

    l1 = bool(all_pts) and set(kinds) <= wide_UPD and not others
    l2 = False
    l3_unit = None
    for name, pts in units.items():
        ks = {k for k, _ in pts}
        if pts and ks <= wide_UPD:
            l2 = True
        if pts and ks <= SYNTH_KINDS:
            l3_unit = name
            break

    #: ── ⛔ 扩展档位：若把候选集从「翻转已有算子」扩到
    #:    「用**文件里已经存在**的子树替换当前子树」，够不够得着？
    #:    （这是 GenProg/ARJA 那类修复的经典候选来源，也不用凭空造代码。）
    #:    只统计**每个单元自己内部的** struct 点 —— 跨单元搬运不算同一处修复。
    extend = "none"
    per_unit_ext = {}
    for name, pts in units.items():
        ss = [d for k, d in pts if k == "struct"]
        if not ss:
            per_unit_ext[name] = ("n/a" if pts else "-")
            continue
        if all("[existing]" in s for s in ss):
            per_unit_ext[name] = "E"          #: 全部是「搬文件里已有的」
        elif any("[existing]" in s for s in ss):
            per_unit_ext[name] = "mix"
        else:
            per_unit_ext[name] = "N"          #: 全是新写的
    vals = [v for v in per_unit_ext.values() if v in ("E", "mix", "N")]
    if "E" in vals:
        extend = "E"        #: 扩候选集即可达（至少一个单元全靠搬运）
    elif vals:
        extend = "N"        #: 连搬运都不够 —— 要凭空造新东西

    return {"units": {n: [k for k, _ in p] for n, p in units.items()},
            "unit_points": {n: p for n, p in units.items()},
            "unit_structs": {n: [d for k, d in p if k == "struct"]
                             for n, p in units.items()},
            "unit_ext": per_unit_ext,
            "extend": extend,
            "toplevel": toplevel, "others": others,
            "n_points": len(all_pts),
            "L1": l1, "L2": l2, "L3": l3_unit is not None,
            "L3_unit": l3_unit,
            "reach": ("L3" if l3_unit else ("L2" if l2 else
                      ("L1" if l1 else "X")))}


# ─────────────────────────── 自检 ───────────────────────────

def selftest_offline() -> list:
    """⛔ **不依赖 git 仓库**的那部分自检 ⇒ 契约可以直接调用它。

    ⛔ 只有一份实现：契约不得自己抄一份，否则迟早漂移成两把尺子
    （157 已经为这个栽过一次）。依赖真实仓库的 S1/S2 留在 `selftest()`。
    """
    res = []

    def ok(name, cond, detail=""):
        res.append((name, bool(cond), detail))

    a = "def f(x):\n    if x > 10:\n        return 1\n    return 2\n"
    bad = "def f(x):\n    if x > 10:\n        return 1\n    return 2\n    \n"
    c = "def f(x):\n    if x >= 10:\n        return 1\n    return 2\n"
    d = "def f(x):\n    if x > 10:\n        return 1\n    z = 0\n    return 2\n"
    ok("S3a 纯 cmp 翻转 ⇒ L3", analyze(a, c)["L3"], analyze(a, c)["reach"])
    ok("S3b ⛔ 多一行语句 ⇒ X", analyze(a, d)["reach"] == "X",
       analyze(a, d)["reach"])
    ok("S3c 多处改动但核心是 cmp ⇒ L3（别处不连坐）",
       analyze("def f(x):\n    if x > 10:\n        return 1\n"
               "def g(y):\n    return y\n",
               "def f(x):\n    if x >= 10:\n        return 1\n"
               "def g(y):\n    z = 1\n    return y\n")["L3"])

    #: ⛔⛔ S4 是本轮的核心自检：只改 docstring 必须**不算**可达。
    #:    第一版全量跑出的 8 个「口径分歧」全是这个假阳性。
    doc_a = 'def f(x):\n    """old doc."""\n    return x\n'
    doc_b = 'def f(x):\n    """brand new doc."""\n    return x\n'
    rd = analyze(doc_a, doc_b)
    ok("S4 ⛔ 只改 docstring ⇒ 不算任何一处变化，不许判可达",
       rd["n_points"] == 0 and rd["reach"] == "X",
       f"n_points={rd['n_points']} reach={rd['reach']}")

    #: S5：「新语句用的是文件里已有的子树」⇒ 当前 X，但 extend=E
    mv_a = "def f(x):\n    y = x + 1\n    return 0\n"
    mv_b = "def f(x):\n    y = x + 1\n    return x + 1\n"   #: return 换成已有的子树
    rm = analyze(mv_a, mv_b)
    ok("S5 搬运已有子树 ⇒ 当前仍 X，但 extend=E",
       rm["reach"] == "X" and rm["extend"] == "E",
       f"reach={rm['reach']} extend={rm['extend']}"
       f" structs={rm['unit_structs'].get('f')}")
    #: S6：凭空新写的子树 ⇒ extend=N
    rn = analyze("def f(x):\n    return 0\n",
                 "def f(x):\n    return brand_new_call(x)\n")
    ok("S6 凭空造新子树 ⇒ extend=N",
       rn["reach"] == "X" and rn["extend"] == "N",
       f"extend={rn['extend']} structs={rn['unit_structs'].get('f')}")
    return res


def selftest(repo: Path) -> list:
    """离线自检 + 依赖真实 boltons 仓库的两条（S1/S2）。"""
    res = selftest_offline()

    def ok(name, cond, detail=""):
        res.append((name, bool(cond), detail))

    def src(sha, path):
        return _git(repo, "show", f"{sha}^:{path}"), _git(repo, "show", f"{sha}:{path}")

    b, f = src("c1c25da", "boltons/mathutils.py")
    r = analyze(b, f) if b and f else None
    ok("S1 c1c25da（已知 synth 修好）⇒ L3",
       r is not None and r["L3"], f"reach={r and r['reach']}"
       f" 单元差异={r and {n: [k for k, _ in p] for n, p in r['unit_points'].items() if p}}")

    b5, f5 = src("5eb6cc0", "boltons/mathutils.py")
    r5 = analyze(b5, f5) if b5 and f5 else None
    ok("S2 ⛔ 5eb6cc0（新增两行 if）⇒ X，不许判可达",
       r5 is not None and r5["reach"] == "X", f"reach={r5 and r5['reach']}")
    return res


# ─────────────────────────── 主流程 ───────────────────────────

def pick_commits(repo: Path, limit: int, require_test: bool = True):
    """与 157 同口径：**恰好一个** boltons/*.py 源文件改动。

    ⛔ `require_test`（默认开）再要求该 commit 改动了**测试文件** ——
      没改测试的 commit 就抽不出新判据，进漏斗会虚增分母
      （第 57 条：空集合当分母）。被这一步筛掉多少必须报出来。
    """
    out, seen = [], 0
    for line in _git(repo, "log", "--format=%H").split():
        files = [l.strip() for l in
                 _git(repo, "show", "--name-only", "--format=", line).splitlines()
                 if l.strip()]
        srcs = [f for f in files
                if f.startswith("boltons/") and f.endswith(".py")
                and "__init__" not in f]
        seen += 1
        if len(srcs) != 1:
            continue
        tests = [f for f in files
                 if ("test" in f.lower()) and f.endswith(".py")]
        if require_test and not tests:
            continue
        out.append((line[:7], srcs[0]))
        if limit and len(out) >= limit:
            break
    return out, seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/tmp/realrepo/boltons")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ctx", default="/tmp/ctxgate40c.json",
                    help="157 的 commit 级判据读数，用于交叉漏斗")
    ap.add_argument("--out", default="")
    ap.add_argument("--all-commits", action="store_true",
                    help="不要求改动测试文件（会虚增漏斗分母，仅供对比）")
    args = ap.parse_args()
    repo = Path(args.repo)

    print("=" * 78)
    print("量具自检（⛔ 判错就停机）")
    print("=" * 78)
    st = selftest(repo)
    for n, c, d in st:
        print(f"  {'✅' if c else '❌'} {n}" + (f"   {d}" if d else ""))
    if not all(c for _, c, _ in st):
        print("\n  ⇒ 尺子不可信，停机（第 24 条）")
        return 1

    ctx = {}
    if args.ctx and Path(args.ctx).exists():
        ctx = json.loads(Path(args.ctx).read_text())["per_commit"]

    commits, scanned = pick_commits(repo, args.limit,
                                    require_test=not args.all_commits)
    print(f"\n扫描 {scanned} 个 commit ⇒ 候选 {len(commits)} 个"
          f"（筛掉 {scanned - len(commits)} 个：非单源文件 / 无测试改动）"
          f"，其中 {len(set(s for s, _ in commits) & set(ctx))} 个有 157 的判据读数")

    rows, cnt = [], Counter()
    for sha, mod in commits:
        b, f = _git(repo, "show", f"{sha}^:{mod}"), _git(repo, "show", f"{sha}:{mod}")
        if not b or not f:
            rows.append({"sha": sha, "module": mod, "note": "取不到源码"})
            continue
        try:
            r = analyze(b, f)
        except Exception as e:
            rows.append({"sha": sha, "module": mod,
                         "note": f"解析失败 {type(e).__name__}"})
            continue
        cnt[r["reach"]] += 1
        old = "operator" if r["L1"] else "structural"
        after = ctx.get(sha, {}).get("after")
        before = ctx.get(sha, {}).get("before")
        rows.append({"sha": sha, "module": mod, "reach": r["reach"],
                     "old": old, "n_points": r["n_points"],
                     "units": r["units"], "others": r["others"],
                     "unit_structs": r["unit_structs"],
                     "unit_ext": r["unit_ext"], "extend": r["extend"],
                     "toplevel": [t[1] for t in r["toplevel"]],
                     "L3_unit": r["L3_unit"],
                     "after": after, "before": before})

    n = len([r for r in rows if "reach" in r])
    print("\n" + "=" * 78)
    print(f"形态分布（新尺子 vs 旧口径，n={n}）")
    print("=" * 78)
    for k in ("L3", "L2", "L1", "X"):
        v = cnt.get(k, 0)
        print(f"  {k:<3} {v:>4}  ({v / max(n, 1):.1%})"
              + {"L3": "   整个文件纯 UPDATE 且落在 synth 候选集",
                 "L2": "   某单元纯 UPDATE（含 revert_* 够得着、synth 够不着的）",
                 "L1": "   某些 indirect 形态",
                 "X": "   需要 INSERT/DELETE ⇒ 都够不到"}[k])
    oldop = sum(1 for r in rows if r.get("old") == "operator")
    print(f"\n  旧口径（整棵树同构）：operator {oldop}/{n}"
          f" = {oldop / max(n, 1):.1%}")
    newr = cnt.get("L3", 0) + cnt.get("L2", 0) + cnt.get("L1", 0)
    print(f"  新口径（至少一个单元可达）：{newr}/{n} = {newr / max(n, 1):.1%}")
    print(f"  ⛔ 其中真正落在 synth 候选集内的（L3）：{cnt.get('L3', 0)}")

    #: ⛔⛔ 「若扩候选集」能多覆盖多少 —— 这是**下一步值不值**的唯一依据
    xs = [r for r in rows if r.get("reach") == "X"]
    ec = Counter(r.get("extend", "none") for r in xs)
    print(f"\n{'=' * 78}\n形态墙的硬度：{len(xs)} 个当前不可达的，墙有多厚？\n{'=' * 78}")
    print(f"  E  结构差异**全部**是「搬文件里已有的子树」"
          f" ⇒ 候选集扩到『用已有表达式替换』即够得着：{ec.get('E', 0)}")
    print(f"  mix 部分靠搬运、部分要新写：{ec.get('mix', 0)}")
    print(f"  N  ⛔ 必须凭空造新东西（新名字/新调用/新常量）"
          f" ⇒ 扩候选集也没用：{ec.get('N', 0)}")
    print(f"  none 顶层层面的增删，或单元层面没有结构差异"
          f"（新增/删除顶层定义）：{ec.get('none', 0)}")
    tot = sum(ec.get(k, 0) for k in ("E", "mix", "N"))
    if tot:
        print(f"\n  ⇒ 扩候选集的天花板（E+mix）："
              f"{ec.get('E', 0) + ec.get('mix', 0)}/{tot}"
              f" = {(ec.get('E', 0) + ec.get('mix', 0)) / tot:.1%}"
              f" 的不可达修复会被纳入射程")

    #: X 里 E 档的逐条列出 —— 这是未来若要动手的候选名单
    ex_only = [r for r in xs if r.get("extend") == "E"]
    if ex_only:
        print(f"\n  E 档明细（扩候选集后可达的 {len(ex_only)} 个）：")
        for r in ex_only:
            print(f"    {r['sha']} {r['module']:<26}"
                  f"{ {n: s for n, s in r['unit_structs'].items() if s} }")

    #: ⛔ 逐个列出「旧判死 / 新判活」——这些是口径分歧的全部来源，供人工核对
    flip = [r for r in rows if r.get("old") == "structural"
            and r.get("reach") in ("L3", "L2", "L1")]
    print(f"\n{'=' * 78}\n口径分歧：旧判 structural、新判可达的 {len(flip)} 个"
          f"（⛔ 必须逐条能说出理由，否则就是放宽放过了头）\n{'=' * 78}")
    for r in flip:
        ch = {nn: k for nn, k in r["units"].items() if k}
        print(f"  {r['sha']} {r['reach']}  {r['module']:<28} 改动单元={ch}")
        if r["toplevel"]:
            print(f"          顶层结构变化：{r['toplevel']}")
        if r["others"]:
            print("          另有模块级语句差异（不计入分档）")

    #: 漏斗交叉
    if ctx:
        print(f"\n{'=' * 78}\n端到端漏斗（形态 × 判据，157 读数 n={len(ctx)}）\n{'=' * 78}")
        inter = [r for r in rows if r["sha"] in ctx and "reach" in r]
        #: ⛔ 口径校准：160 的「k≥3」是**给合成层的判据总数**（含回归保护），
        #:    而 ctx 的 after = **有鉴别力的判据数**（F2P）。两者不是一回事：
        #:    159 那次成功是「给全 25 条（23 条 P2P + 2 条 F2P）」，after=2
        #:    ⇒ 若拿 ≥3 当 F2P 门槛，连唯一的成功案例都会被算掉。
        #:    ⇒ 判据关的最低线是 **≥1 条 F2P**；≥3 只作为参考列。
        steps = [
            ("有明确改动的 commit", len(inter)),
            ("≥1 条 F2P（判据关最低线）", sum(1 for r in inter if (r["after"] or 0) > 0)),
            ("≥3 条 F2P（更严，参考用）", sum(1 for r in inter if (r["after"] or 0) >= 3)),
            ("且形态≥L2（含 revert 够得着）", sum(1 for r in inter if (r["after"] or 0) > 0
                                          and r["reach"] in ("L2", "L1", "L3"))),
            ("且形态=L3（synth 射程）", sum(1 for r in inter if (r["after"] or 0) > 0
                                      and r["reach"] == "L3")),
        ]
        for i, (nm, v) in enumerate(steps):
            prev = steps[i - 1][1] if i else v
            bar = "█" * max(1, round(v * 40 / max(steps[0][1], 1)))
            print(f"  {nm:<26} {v:>3}  {bar}"
                  + (f"   ← 掉 {prev - v}" if i else ""))
        #: ⛔⛔ 反向看：形态关过了、但判据没过的 —— 这些是**提高物化率能解锁
        #:    的全部候选**，也就是「下一步最高能拿到多少」的上界。
        lost_by_ctx = [r for r in inter if (r["after"] or 0) == 0
                       and r["reach"] == "L3"]
        if lost_by_ctx:
            print(f"\n  ⛔ 形态=L3 但 F2P=0（**提高物化率能解锁的全部候选**）："
                  f"{len(lost_by_ctx)} 个")
            for r in lost_by_ctx:
                ch = {nn: k for nn, k in r["units"].items() if k}
                print(f"    {r['sha']} {r['module']:<26}{ch}")
        print("\n  明细（after≥1 的，按 F2P 条数排序）：")
        print(f"  {'sha':<9}{'reach':<6}{'F2P':<6}{'改动了哪些单元':<44}")
        for r in sorted(inter, key=lambda x: -(x["after"] or 0)):
            if (r["after"] or 0) <= 0:
                continue
            ch = {nn: k for nn, k in r["units"].items() if k}
            print(f"  {r['sha']:<9}{r['reach']:<6}{str(r['after']):<6}{str(ch)[:42]:<44}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"n": n, "counts": dict(cnt), "old_operator": oldop, "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
