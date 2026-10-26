"""修法基线 + **可执行**的难度分层。

## 难度为什么必须由执行定义

"这道题难不难"如果靠人标，就会变成主观标签，而且随环境变化失效。
这里用**修法**来定义难度：

    easy —— 存在某个**通用修法**能把它修好
    hard —— 所有通用修法都修不好它

"通用修法"指不针对具体环境的**最小编辑**（例如只恢复控制流、不改字面量）。
于是难度有了可判定、可复现、跨环境可比的含义：
easy 题考的是"能不能识别并撤销标准错误"，hard 题考的是"有没有别的能力"。

## 为什么这对泛化闸是必需的

held-out 也可能是**偏的**：如果 held-out 全是 easy 题，一个"只会修 easy 题"
的 harness 会被判成 generalize。分层之后，泛化闸可以要求
**每一层都要涨**，只在简单层涨 ⇒ 判 `partial`（同样拦截）。

⛔ 难度分层不是"评分"，它是把 held-out 的**判别力**也纳入了判据：
一个没有难度跨度的 held-out，其放行结论本身不可信。

## ⛔⛔ 本模块的修法全部是「对齐基线」，不是「生成修复」（2026-10-26 实测）

五个 `revert_*` 的签名都是 `(broken_src, base_src)`：它们遍历两棵 AST，
按位置对齐，把 broken 的算子/常量**换成 base 的**。因此：

    base 里有什么，修法就能产出什么；base 里没有的，它一个字节也造不出来。

2026-10-26 对照实验（`scripts/realbug_experiment.py --baseline`，
真实 bug boltons `c1c25da`）：

    base = 修复后源码 ⇒ 轮 1 `fix_control_flow` reward=1.0 ⇒ 25/25 修好
    base = 缺陷版本身 ⇒ 三轮 0 提交，23/25 依旧失败（模板提议器）
    base = 缺陷版本身 ⇒ 三轮 0 提交，23/25 依旧失败（真模型提议器，
                          已回看 `last_error` 为空 ⇒ 模型确实被调用）

且单测确认：`base == broken` 时五个修法**全部返回输入原文**（恒等操作）。

⇒ **"第 1 轮修好真实 bug"这个成就里，"改对那一行"来自基线（= 人类答案）；
系统自己贡献的是"定位到那一处"**。在自造题上这没有区别（题就是照基线
变异出来的，对齐基线就是正确答案）；在真实 bug 上，它是**照答案抄**。
⛔ 谁要拿"修好了真实 bug"当能力证据，必须同时报这一条。

⇒ 真正的缺口因此是：**没有「判据驱动生成补丁」这一层**。本模块只有
「对齐」，没有「合成」。补它不是加一个 `revert_*` 能解决的。
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .environment import (Environment, numeric_literals, string_literals,
                          verify_source)

Repair = Callable[[str, str], str]      # (broken_source, base_source) -> fixed


# ═══════════════════════════════════════════════════════════════════════════
# 渲染层：把「改过的 AST」变回**源码文本**
#
# ⛔⛔ 为什么不能 `ast.unparse(整棵树)`（2026-10-25 实测取证）
#
#   真实 bug `93185b2`（boltons #302）上，`revert_control_flow` 把逻辑改对了
#   （`start < stop` → `start <= stop`），但产出与人类作者的真实修复**逐字
#   不一致**：581 行 → 468 行，文件头 license 注释 32 行被删、所有空行与
#   行内注释被删、字符串引号与括号被重写。⇒ 这个补丁在真实代码库里
#   **不可提交**。而判据只看行为 ⇒ 7/7 全绿 ⇒ 缺陷完全隐形；更糟的是 V6
#   自己的题源也是 `mutate_source` 用 AST 往返造的 ⇒ 题的源码本身就是
#   unparse 过的 ⇒ 格式一致，噪声无从显现（第 24 / 133 条的形状）。
#
#   ⇒ 渲染必须是**最小编辑**：只替换真正变了的那一小段，其余保留原文本。
#
# ⛔⛔ 自检为什么必须内建
#
#   「找最小改动」写错 ⇒ 静默产出一个**语义不同**的补丁 —— 这比格式噪声
#   坏得多。所以每渲染一次都要**重新解析并与整份 unparse 的 AST 对拍**：
#   不一致 ⇒ 立刻回退到旧行为（`ast.unparse` 整树）。宁可丢格式，不可丢语义。
# ═══════════════════════════════════════════════════════════════════════════

#: 渲染模式：`unchanged`=没改动（原样返回）/ `splice`=最小编辑成功 /
#: `fallback`=自检没过或无法定位，退回整份 unparse。
RenderMode = str


def _sig(n: ast.AST) -> tuple:
    """节点的**浅层**签名：自身类型 + 自身字段值 + **直接子节点的身份**。

    ⛔ 关键性质：后代内部的改动**不会**改变本节点的签名（只记 id）⇒
    签名变了 ⟺ 「这个节点自己变了」（自己字段变了，或它的直接孩子被换了个
    对象）。这正是"最小改动集合"的定义，且只需 O(n) 而不是整棵子树 dump。
    """
    out: List[Any] = [type(n).__name__]
    for f in getattr(n, "_fields", ()):
        v = getattr(n, f, None)
        if isinstance(v, ast.AST):
            out.append(("n", id(v)))
        elif isinstance(v, list):
            out.append(("l", tuple(id(x) if isinstance(x, ast.AST) else repr(x)
                                   for x in v)))
        else:
            out.append(repr(v))
    return tuple(out)


def _segments(src: str, tree: ast.AST) -> Dict[int, Tuple[int, int, int]]:
    """id(node) -> (起始字符偏移, 结束字符偏移, 列号)。只对**原文本**建一次。"""
    offs = [0]
    for ln in src.splitlines(keepends=True):
        offs.append(offs[-1] + len(ln))
    n_lines = len(offs) - 1
    segs: Dict[int, Tuple[int, int, int]] = {}
    for n in ast.walk(tree):
        ln = getattr(n, "lineno", None)
        el = getattr(n, "end_lineno", None)
        co = getattr(n, "col_offset", None)
        ec = getattr(n, "end_col_offset", None)
        if None in (ln, el, co, ec) or ln < 1 or el > n_lines:
            continue
        segs[id(n)] = (offs[ln - 1] + co, offs[el - 1] + ec, co)
    return segs


def _minimal_changed(tree: ast.AST, snap: Dict[int, tuple]) -> List[ast.AST]:
    """返回**最深的**一批"变了的节点"（祖先不再重复列出）。

    ⛔⛔ 只看 `expr` / `stmt`（2026-10-25 实测）：`ast.walk` 会走到 `cmpop` /
    `operator` / `boolop` / `unaryop` 这类**算子包装节点**——它们也是 `ast.AST`，
    但**没有 lineno/col_offset**，且每次改 op 都是**新建**的对象 ⇒ 会被当成
    "新增节点"⇒ 成了"最小改动" ⇒ 定位不到 ⇒ 整份重写（fallback）。
    实测第一版就是这样：真实 bug 上的 5 处改动全部 fallback，等于没修。
    ⇒ 可渲染单元必须是 `expr`/`stmt`：它们 parse 出来**一定有位置**。
    """
    marked = {id(n) for n in ast.walk(tree)
              if isinstance(n, (ast.expr, ast.stmt))
              and (snap.get(id(n)) is None or _sig(n) != snap[id(n)])}

    def rec(n: ast.AST) -> List[ast.AST]:
        got: List[ast.AST] = []
        for c in ast.iter_child_nodes(n):
            got.extend(rec(c))
        if got:
            return got
        return [n] if id(n) in marked else []

    return rec(tree)


def _requote(txt: str, seg_text: str) -> str:
    """字符串常量的**引号风格**要跟着原文本走。

    `ast.unparse` 一律用单引号 ⇒ 原文 `"prod"` 会被写成 `'prod'` ⇒
    补丁仍然正确，但真实代码库里这种改动会被 review 打回（且与周边风格不一致）。
    ⛔ 只在**安全**时改写：原文引号字符出现在内容里（会牵扯转义）就不动，
    宁可留 unparse 的写法 —— 格式是次要的，正确性不是。
    """
    m = re.match(r"^[a-zA-Z]*", seg_text)
    prefix = m.group(0) if m else ""
    if len(seg_text) <= len(prefix) or seg_text[len(prefix)] not in "\"'":
        return txt
    q = seg_text[len(prefix):len(prefix) + 3]
    if len(txt) < 2 or txt[0] not in "\"'" or txt[-1] != txt[0]:
        return txt
    inner = txt[1:-1]
    #: 三引号（docstring 常见）⇒ 保住三引号形态，否则一行 docstring 会被压扁
    if q in ('"""', "'''"):
        return prefix + q + inner + q
    quote = seg_text[len(prefix)]
    if quote in inner:
        return txt
    return prefix + quote + inner + quote


#: 算子 token 文本表。⛔ 只列**二元/比较/布尔**与**一元**两类：
#: 五个变异维度改的东西全在这里面；表外的一律退回 `ast.unparse`。
_BINOP_TEXT = {
    "Add": "+", "Sub": "-", "Mult": "*", "Div": "/", "FloorDiv": "//",
    "Mod": "%", "Pow": "**", "LShift": "<<", "RShift": ">>",
    "BitOr": "|", "BitXor": "^", "BitAnd": "&", "MatMult": "@",
    "Eq": "==", "NotEq": "!=", "Lt": "<", "LtE": "<=", "Gt": ">",
    "GtE": ">=", "Is": "is", "IsNot": "is not", "In": "in",
    "NotIn": "not in", "And": "and", "Or": "or",
}
_UNARY_TEXT = {"Not": "not ", "USub": "-", "UAdd": "+", "Invert": "~"}


def _render_node(n: ast.AST, segs: Dict[int, Tuple[int, int, int]],
                 snap: Dict[int, tuple], src: str) -> Optional[str]:
    """渲染**一个变了的节点**：不变的子节点一律用**原文本**，只重写算子 token。

    ⛔ 为什么不能一律 `ast.unparse(n)`：实测换 `and`→`or` 时，它会把整段
    `start == stop and MODE == "prod"` 重新打印 ⇒ 内层字符串的双引号被改成
    单引号。算子只改一个 token，补丁就该只有一个 token 的差别。
    ⛔ 返回 `None` = 认不出来 ⇒ 调用方退回 `ast.unparse`（丢一点格式，不丢语义）。
    ⛔ 拼错的风险由 `render_patch` 末尾的 AST 自检兜住：拼错 ⇒ 回退。
    """
    #: 没变的节点 ⇒ **原文本**（这一步是保住引号/注释/换行的关键）
    if id(n) in snap and _sig(n) == snap[id(n)] and id(n) in segs:
        s, e, _ = segs[id(n)]
        return src[s:e]

    def sub(x: ast.AST) -> Optional[str]:
        """渲染子节点。⛔ 子节点**自带的分组括号**要一起带上：

        节点的 `col_offset` 指向括号**里面**（`(not x)` 的 UnaryOp 从 `not` 起）
        ⇒ 只按节点区间取原文本 ⇒ `A and (not x)` 会变成 `A and not x`，
        括号被吃掉。这里能丢、那里就可能丢出语义差别（`A and (B or C)` ⇒
        `A and B or C`）。⛔ 只会发生在**中缀/前缀算子的子节点**上：那种位置
        紧邻的括号一定是分组括号，不会是父节点的语法括号（不像 `f(x)`）。
        """
        if id(x) in segs and id(x) in snap and _sig(x) == snap[id(x)]:
            s, e, _ = segs[id(x)]
            while s - 1 >= 0 and e < len(src) \
                    and src[s - 1] == "(" and src[e] == ")":
                s -= 1
                e += 1
            return src[s:e]
        return _render_node(x, segs, snap, src)

    if isinstance(n, ast.Compare):
        left = sub(n.left)
        if left is None:
            return None
        out = [left]
        for op, comp in zip(n.ops, n.comparators):
            t = _BINOP_TEXT.get(type(op).__name__)
            right = sub(comp) if t else None
            if t is None or right is None:
                return None
            out.append(" " + t + " " + right)
        return "".join(out)
    if isinstance(n, ast.BinOp):
        t = _BINOP_TEXT.get(type(n.op).__name__)
        a, b = sub(n.left), sub(n.right)
        if t is None or a is None or b is None:
            return None
        return f"{a} {t} {b}"
    if isinstance(n, ast.BoolOp):
        t = _BINOP_TEXT.get(type(n.op).__name__)
        vals = [sub(v) for v in n.values]
        if t is None or any(v is None for v in vals):
            return None
        return (" " + t + " ").join(vals)
    if isinstance(n, ast.UnaryOp):
        t = _UNARY_TEXT.get(type(n.op).__name__)
        v = sub(n.operand)
        if t is None or v is None:
            return None
        return t + v
    if isinstance(n, ast.Constant):
        try:
            txt = ast.unparse(n)
        except Exception:                                     # noqa: BLE001
            return None
        seg = segs.get(id(n))
        return _requote(txt, src[seg[0]:seg[1]]) if seg else txt
    return None


def _full_unparse(tree: ast.AST, original: str) -> str:
    """旧行为（整份重写）。只在自检不过 / 无法定位时兜底。"""
    try:
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    except Exception:                                          # noqa: BLE001
        return original


def render_patch(broken_src: str, base_src: str,
                 mutate: Callable[[ast.AST, ast.AST], Optional[ast.AST]],
                 ) -> Tuple[str, RenderMode]:
    """跑一次变异并把它渲染回源码 —— **最小编辑**，改不动的格式一个字节都不动。

    `mutate(b, g)` 就地修改 `b`（或返回新树）；返回 `None` ⟹ 该修法**拒绝**
    动手（fail-closed，与各个 `revert_*` 里"长度不等就不对齐"同一条防线）。

    ⛔⛔ 返回前必做自检：`ast.dump(parse(结果))` 必须等于 `ast.dump(m)`。
    不等 ⇒ 最小编辑弄错了 ⇒ **退回整份 unparse**（宁可丢格式，不可丢语义）。
    """
    try:
        b = ast.parse(broken_src)
        g = ast.parse(base_src)
    except SyntaxError:                                        # noqa: BLE001
        return broken_src, "unchanged"
    #: ⛔ 快照必须在**变异之前**取：之后节点被就地改掉，就没有"原来是什么"了。
    snap = {id(n): _sig(n) for n in ast.walk(b)}
    segs = _segments(broken_src, b)
    try:
        m = mutate(b, g)
    except Exception:                                         # noqa: BLE001
        return broken_src, "unchanged"
    if m is None:
        return broken_src, "unchanged"
    mins = _minimal_changed(m, snap)
    if not mins:
        #: ⛔ 没定位到改动，不等于没改动：`expr`/`stmt` 之外的地方若真变了
        #:   （例如新增一个 `arg`），原样返回就是**静默丢改动**。
        #:   ⇒ 先对拍整棵树，真变了就走整份 unparse（丢格式，不丢语义）。
        try:
            if ast.dump(m) != ast.dump(ast.parse(broken_src)):
                return _full_unparse(m, broken_src), "fallback"
        except Exception:                                     # noqa: BLE001
            pass
        return broken_src, "unchanged"

    edits: List[Tuple[int, int, str, int]] = []
    for n in mins:
        seg = segs.get(id(n))
        if seg is None:
            #: 新增节点（例如 `not x` 包回来）没有自己的位置 ⇒ 用最近的、
            #: **在原文本里存在**的后代的位置（替换 `x` → `not x`）。
            for d in ast.walk(n):
                if id(d) in segs:
                    seg = segs[id(d)]
                    break
        if seg is None:
            return _full_unparse(m, broken_src), "fallback"
        txt = _render_node(n, segs, snap, broken_src)
        if txt is None:
            #: 认不出来的节点类型 ⇒ 退回 unparse 这一个节点（不再整份重写）
            try:
                txt = ast.unparse(n)
            except Exception:                                 # noqa: BLE001
                return _full_unparse(m, broken_src), "fallback"
            txt = _requote(txt, broken_src[seg[0]:seg[1]])
        edits.append((seg[0], seg[1], txt, seg[2]))

    out = broken_src
    #: ⛔ 从后往前替换，前面的偏移才不会被自己改出来的长度带偏。
    for s, e, txt, col in sorted(edits, key=lambda x: x[0], reverse=True):
        if "\n" in txt:
            #: 多行替换（替换到的是语句块）⇒ 续行补上原本的缩进
            txt = txt.replace("\n", "\n" + " " * col)
        out = out[:s] + txt + out[e:]

    try:
        if ast.dump(ast.parse(out)) != ast.dump(m):
            return _full_unparse(m, broken_src), "fallback"
    except Exception:                                         # noqa: BLE001
        return _full_unparse(m, broken_src), "fallback"
    return out, "splice"


def revert_control_flow(broken_src: str, base_src: str) -> str:
    """**保守通用修法**：只把比较/运算恢复成基线的，**不动字面量**。

    ⛔ 为什么刻意"不动字面量"：数值常量常常是有意的配置（`window=60`、
    `gap=30` 就是配置），一个**通用**修法没有立场去改它。保守是它的美德，
    也是它的代价 —— 它在发展集上会**输给**那个"整个文件换回基线"的定点修法。

    这不是为了演示而造的缺陷，而是 reward hacking 之所以发生的真实土壤：
    **讨好判据的修法在发展集上确实更好**，所以它会赢，所以必须靠 held-out 拦。
    """
    def ops(tree):
        """按同一遍历序收集控制流节点（**存节点本身**，不是它的 op）。"""
        out: List[Tuple[str, Any]] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare):
                out.append(("cmp", n))
            elif isinstance(n, ast.BinOp):
                out.append(("bin", n))
        return out

    def mutate(b: ast.AST, g: ast.AST) -> Optional[ast.AST]:
        bo, go = ops(b), ops(g)
        if len(bo) != len(go):
            #: ⛔ 与 `revert_literals` 同一条防线：树形不同 ⇒ 遍历序不可信
            #:    ⇒ 宁可不动。这里没有"部分对齐"这种折中：错位一次就污染一片。
            return None
        for i in range(min(len(bo), len(go))):
            kind, node = bo[i]
            ref = go[i][1]
            # 只有两棵树**同一位置是同类节点**才恢复：结构对不上就跳过，
            # 不硬套（硬套会静默改坏没被变异的地方）。
            if kind == "cmp" and isinstance(ref, ast.Compare):
                node.ops = list(ref.ops)
            elif kind == "bin" and isinstance(ref, ast.BinOp):
                node.op = ref.op
        return b

    return render_patch(broken_src, base_src, mutate)[0]


def revert_literals(broken_src: str, base_src: str) -> str:
    """**通用**字面量修法：数值常量按同一遍历序对齐到基线，其余一律不动。

    与 `revert_control_flow` 的关系：那一维修的是"比较/运算"，这一维修的是
    "数值字面量"。两者都只把自己那一类对齐到基线、都不碰别的东西 ⇒
    它们是**可叠加的两个能力维度**，不是同一个修法的两种写法。

    ⛔⛔ 它**刻意不进** `GENERIC_REPAIRS`（2026-10-13 实测取证
        `scripts/probe_repair_literals.py`：4/4 环境能修常量题，控制流题
        0/10 被它修好）。难度由"通用修法能否修好"定义（见本文件顶部），
        把它加进那个集合 ⇒ 常量题从 hard 变 easy ⇒ 发展集与 held-out 的
        **难度跨度消失** ⇒ 泛化闸退化。它只作为**技能**进入 proposer 池：
        泛不泛化由 held-out 闸在执行中判，不由"是不是通用修法"预先判。

    ⛔ 与 `restore_baseline` 的区别：它**只动数值常量**，字符串、结构、
       比较符、运算符一律保留 broken 的形态。
    """
    def mutate(b: ast.AST, g: ast.AST) -> Optional[ast.AST]:
        #: ⛔⛔ 口径必须与变异器同源（`environment.numeric_literals`）。
        #:    以前这里**排除 bool** 而变异器不排除 ⇒ 两边序列长度不同
        #:    ⇒ 下面的对齐会整体错位 ⇒ 把没坏的常量改坏（实测 4 条残差）。
        #: ⛔ 传**树**不是字符串：必须拿到与 `b`/`g` 同一批节点对象。
        bo, go = numeric_literals(b), numeric_literals(g)
        if len(bo) != len(go):
            #: ⛔ fail-closed：长度不等说明树形已不同，遍历序无法保证对应
            #:    ⇒ 宁可一条都不改，也不许"错位污染"。
            return None
        for i in range(len(bo)):
            bo[i].value = go[i].value
        return b

    return render_patch(broken_src, base_src, mutate)[0]


def revert_boolops(broken_src: str, base_src: str) -> str:
    """**通用**布尔运算修法：只把 `and` / `or` 对齐到基线，其余一律不动。

    ⛔⛔ 与另外两个通用修法构成**三个正交维度**（2026-10-14 实测）：

            revert_control_flow ⇒ 比较符 / 运算符
            revert_literals     ⇒ 数值常量
            revert_boolops      ⇒ and / or

        "正交"在这里是可判定的：每一维只动自己那一类节点，
        任取一维修另一维造出来的题 ⇒ reward 必须仍为 0；
        三者叠加 ⇒ 三类题都能修好。判据来自执行，不来自声明。

    ⛔ 与 `revert_literals` 同理，**刻意不进** `GENERIC_REPAIRS`：
        难度由"通用修法（= 保守控制流修法）能否修好"定义，
        把它加进那个集合 ⇒ boolop 题从 hard 变 easy ⇒ 难度跨度消失。
        它只作为**技能**进 proposer 池，泛不泛化交给 held-out 闸判。
    """
    def boolops(tree):
        return [n for n in ast.walk(tree) if isinstance(n, ast.BoolOp)]

    def mutate(b: ast.AST, g: ast.AST) -> Optional[ast.AST]:
        bo, go = boolops(b), boolops(g)
        if len(bo) != len(go):
            #: ⛔ 与另两个修法同一条防线：树形不同 ⇒ 遍历序不可信 ⇒ 不动。
            return None
        for i in range(len(bo)):
            bo[i].op = type(go[i].op)()
        return b

    return render_patch(broken_src, base_src, mutate)[0]


def revert_strings(broken_src: str, base_src: str) -> str:
    """**第四个正交维度**：只把**字符串常量**按遍历序对齐到基线。

    ⛔ 它存在的理由是可逆性，不是"多一种修法"（2026-10-17 实测）：
        `string` 变异算子刚加上时，配套的修法并没有跟着加 ⇒ 变异能造出
        真失败的任务（F2P 通过），但**没有任何技能修得回来** ⇒ 这类题在
        dev 与 held-out 上 delta 恒 0 ⇒ 造出来的是**注水的题源**，比没有
        更坏 —— 它让"世界变大了"这件事在账面上成立、在 reward 里完全看
        不见。⇒ **加一个变异维度，必须同时加它的对称修法。**

    ⛔ 与另外三个维度正交，四者各管一类节点：

        revert_control_flow ⇒ 比较符 / 运算符
        revert_literals     ⇒ 数值常量
        revert_boolops      ⇒ and / or
        revert_strings      ⇒ 字符串常量

        正交在这里是可判定的：任取一维修另一维造的题 ⇒ reward 必须为 0。

    ⛔ 与 `revert_literals` 同理，**刻意不进** `GENERIC_REPAIRS`（已实测）：
        它进那个集合 ⇒ 字符串题从 hard 变 easy ⇒ 发展集与 held-out 的
        难度跨度消失 ⇒ 泛化闸退化。它只作为**技能**进 proposer 池。
    """
    def mutate(b: ast.AST, g: ast.AST) -> Optional[ast.AST]:
        bo, go = string_literals(b), string_literals(g)
        if len(bo) != len(go):
            return None          #: fail-closed：树形已变 ⇒ 不许错位对齐
        for i in range(len(bo)):
            bo[i].value = go[i].value
        return b

    return render_patch(broken_src, base_src, mutate)[0]


def revert_unaryops(broken_src: str, base_src: str) -> str:
    """**第五个正交维度**：把 `not` 的增删按**树形位置**对齐回基线。

    ⛔⛔ 为什么这里**不能**沿用前四个维度的"按遍历序对齐常量"写法：
        `unaryop` 变异是**删除节点**（`not x` → `x`），不是改值 ⇒
        broken 树里的 UnaryOp 节点**比 baseline 少一个** ⇒ 按序号对齐
        会**整体错位**（前面每删一个 not，后面全部前移一位）⇒ 修法会把
        本来正确的节点改坏。这与 `numeric_literals` 记过的那个自洽性
        bug 同源，只是触发条件从"bool 是否算入""表达式是否嵌套"
        变成了"**变异是否增删节点**"。

        ⇒ 本修法改用**同位置递归**：沿两棵树同一条路径往下走，
          baseline 在该位置是 UnaryOp 而 broken 不是 ⇒ **包回**一层。
          删除 not 不改变父节点的字段索引（只是那个位置的节点类型变了）
          ⇒ 路径仍然对齐。

    ⛔ fail-closed：两棵树结构对不上（字段是 list 且长度不同 / 节点类型
        不同且不是 unary 差异）⇒ **原样返回**，绝不猜着改。

    ⛔ 与另外四个维度正交，五者各管一类节点：

        revert_control_flow ⇒ 比较符 / 运算符
        revert_literals     ⇒ 数值常量
        revert_boolops      ⇒ and / or
        revert_strings      ⇒ 字符串常量
        revert_unaryops     ⇒ not 的增删

    ⛔ 与 `revert_strings` 同理，**刻意不进** `GENERIC_REPAIRS`：
        进那个集合 ⇒ unaryop 题从 hard 变 easy ⇒ 难度跨度消失
        ⇒ 泛化闸退化。它只作为**技能**进 proposer 池。
    """
    def align(bn: ast.AST, gn: ast.AST) -> ast.AST:
        #: ⛔⛔ **None 守卫**（2026-10-17 回归实测）：AST 的**列表字段**里
        #:    合法地含 `None` —— 实测 `ast.arguments.kw_defaults == [None]`
        #:    （有 kw-only 参数但没给默认值）。原版直接 `gn._fields`
        #:    ⇒ `AttributeError: 'NoneType' object has no attribute
        #:    '_fields'` ⇒ 修法**抛异常** ⇒ 冻结任务从通过变不通过
        #:    （监督器 `REGRESSION_DETECTED` 当场抓到：frozen::_writer 1→0）。
        #:
        #:    ⛔ 这是「递归对齐」这类写法的通病：它假定"列表里每个元素都是
        #:    节点"，而 AST 的字段域是 **可选节点**（Optional[AST]）。
        #:    ⇒ fail-closed：任一边缺节点 ⇒ **原样返回**，绝不猜着改。
        if bn is None or gn is None:
            return bn
        #: baseline 这里有 unary、broken 没有 ⇒ 包回一层（变异删掉了它）
        if isinstance(gn, ast.UnaryOp):
            if isinstance(bn, ast.UnaryOp):
                bn.op = type(gn.op)()
                bn.operand = align(bn.operand, gn.operand)
                return bn
            return ast.UnaryOp(op=type(gn.op)(),
                               operand=align(bn, gn.operand))
        if isinstance(bn, ast.UnaryOp) and not isinstance(gn, ast.UnaryOp):
            #: broken 多了一层 unary ⇒ 拆掉它（对称面：变异也可能加）
            return align(bn.operand, gn)
        if type(bn) is not type(gn):
            return bn                       #: 结构不同 ⇒ 不碰（fail-closed）
        for field in gn._fields:
            gv = getattr(gn, field, None)
            bv = getattr(bn, field, None)
            if isinstance(gv, ast.AST):
                if isinstance(bv, ast.AST):
                    setattr(bn, field, align(bv, gv))
            elif isinstance(gv, list):
                if not isinstance(bv, list) or len(bv) != len(gv):
                    return bn               #: 长度不同 ⇒ 不碰
                setattr(bn, field, [align(x, y) for x, y in zip(bv, gv)])
        return bn

    def mutate(b: ast.AST, g: ast.AST) -> Optional[ast.AST]:
        return align(b, g)

    return render_patch(broken_src, base_src, mutate)[0]


def restore_baseline(broken_src: str, base_src: str) -> str:
    """**完整恢复**：整个文件换回基线。

    ⛔ 它**不是**通用修法 —— 它要求"知道这个环境的基线长什么样"。
    放在这里只作对照（它是"针对性修法"的上界）。
    """
    return base_src


#: 通用修法集合。**默认只有保守修法**：再加一条"整个文件换回基线"的话，
#: 所有任务都会变成 easy，分层就失去意义了。
GENERIC_REPAIRS: Dict[str, Repair] = {
    "revert_control_flow": revert_control_flow,
}


def repair_rewards(env: Environment, broken_source: str,
                   repairs: Optional[Dict[str, Repair]] = None,
                   timeout: float = 10.0) -> Dict[str, float]:
    """用每个通用修法试着修，返回各自的实际 reward（执行判据，不是估计）。"""
    repairs = repairs or GENERIC_REPAIRS
    out: Dict[str, float] = {}
    for name, fn in repairs.items():
        try:
            fixed = fn(broken_source, env.source)
        except Exception:  # noqa: BLE001
            out[name] = 0.0
            continue
        try:
            out[name] = verify_source(env, source=fixed, timeout=timeout).reward
        except Exception:  # noqa: BLE001
            out[name] = 0.0
    return out


def classify_difficulty(env: Environment, broken_source: str,
                        repairs: Optional[Dict[str, Repair]] = None,
                        timeout: float = 10.0) -> str:
    """easy = 有通用修法能修好；hard = 都不行。"""
    rs = repair_rewards(env, broken_source, repairs, timeout=timeout)
    return "easy" if rs and max(rs.values()) >= 1.0 else "hard"


def grow_for_difficulty(env: Environment,
                        synth: Callable[[int], List[Any]],
                        want_levels: Iterable[str],
                        *, max_n: int = 8, ceiling: int = 32,
                        timeout: float = 10.0) -> Dict[str, Any]:
    """扩大合成采样，直到发展集覆盖 `want_levels` 里的每个难度层。

    ⛔ 为什么必须补（2026-10-13 实测）：难度层由"通用修法能否修好"定义，
       是**候选无关**的属性。发展集没采到 hard 题 ⇒ 那一层无从对照 ⇒
       泛化闸只能判 `inconclusive`（拦截）。而"没采到"是**采样配置**问题：

           max_tasks=8   ⇒ dev easy/hard = 3/0 ⇒ 三轮全部 inconclusive
           max_tasks=12  ⇒ dev easy/hard = 3/2 ⇒ generalize 并提交

       让候选为采样不足背锅，和"永远不提交"长得一模一样 ——
       把罪名从 partial 改成 inconclusive 只是改了说法，没改结局。

    ⛔ 补采样**只加题不加判据**：合成任务来自 AST 变异（`mutate_source`），
       与候选无关 ⇒ 扩大采样不会给任何候选优待，它只解决"无从对照"。
       这一步的代价是真实的执行时间（实测 8→24 约 1–3s），所以用
       `ceiling` 封顶；到顶仍缺层 ⇒ 明确报出 missing，不假装补齐。
    """
    want = {str(x) for x in want_levels}
    steps: List[Dict[str, Any]] = []
    n = max(1, int(max_n))
    tasks = list(synth(n))
    labels = difficulty_report(env, tasks, timeout=timeout)["labels"]
    have = {str(v) for v in labels.values()}

    def _snap() -> Dict[str, Any]:
        dist: Dict[str, int] = {}
        for v in labels.values():
            dist[str(v)] = dist.get(str(v), 0) + 1
        return {"max_n": n, "n_tasks": len(tasks), "dist": dist}

    steps.append(_snap())
    while (want - have) and n < ceiling:
        n = min(int(ceiling), n * 2)
        tasks = list(synth(n))
        labels = difficulty_report(env, tasks, timeout=timeout)["labels"]
        have = {str(v) for v in labels.values()}
        steps.append(_snap())

    missing = sorted(want - have)
    return {"tasks": tasks, "labels": labels, "max_n_used": n,
            "steps": steps, "missing": missing, "covered": not missing,
            "note": ("补采样到上限仍缺难度层 " + "/".join(missing)
                     + " ⇒ 该环境配对与 held-out 难度分布不可比，"
                       "请提高 --max-tasks 或换一组环境配对"
                     if missing else
                     (f"采样自愈：{max_n}→{n} 补齐了缺失的难度层"
                      if n != max_n else ""))}


def difficulty_report(env: Environment, tasks: List[Any],
                      repairs: Optional[Dict[str, Repair]] = None,
                      timeout: float = 10.0) -> Dict[str, Any]:
    """给一批任务打难度标签，并给汇总（供 held-out 分层用）。

    ⛔ 汇总里的 `spread` 很重要：一个**只有 easy** 的 held-out 没有判别力，
    它放行"只会修简单题"的候选。这时应该去补 hard 题，而不是继续跑。
    """
    labels: Dict[str, str] = {}
    for t in tasks:
        src = None
        spec = getattr(t, "spec", None)
        #: ⛔⛔ 必须是 `Mapping` 而不是 `dict`：发展集/held-out 的任务都是
        #:    `FrozenTask`，而 `FrozenTask.__post_init__` 会把 spec 包成
        #:    `MappingProxyType`（冻结基线承诺）—— 它**不是** dict 子类。
        #:    ⇒ 写 `isinstance(spec, dict)` 时 labels **恒为空** ⇒ 难度
        #:    分层静默失效 ⇒ 泛化闸拿不到同层对照 ⇒ "放行"变成无条件。
        #:    与 `nongression.FrozenTask.fingerprint` 里那个坑**完全同形**
        #:    （上轮只修了那一处，这是同一个错误的第二次出现）。
        if isinstance(spec, Mapping):
            src = spec.get("broken_source")
        if src is None:
            src = getattr(t, "broken_source", None)
        if not src:
            continue
        labels[t.task_id] = classify_difficulty(env, src, repairs, timeout=timeout)
    n_easy = sum(1 for v in labels.values() if v == "easy")
    n_hard = sum(1 for v in labels.values() if v == "hard")
    return {"env_id": env.env_id, "labels": labels,
            "n_easy": n_easy, "n_hard": n_hard,
            "spread": bool(n_easy and n_hard),
            "note": ("held-out 只有单一难度 ⇒ 判别力不足，它会放行"
                     "「只会修这类题」的候选" if not (n_easy and n_hard) else "")}
