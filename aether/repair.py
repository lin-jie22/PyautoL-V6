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
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .environment import (Environment, numeric_literals, string_literals,
                          verify_source)

Repair = Callable[[str, str], str]      # (broken_source, base_source) -> fixed


def revert_control_flow(broken_src: str, base_src: str) -> str:
    """**保守通用修法**：只把比较/运算恢复成基线的，**不动字面量**。

    ⛔ 为什么刻意"不动字面量"：数值常量常常是有意的配置（`window=60`、
    `gap=30` 就是配置），一个**通用**修法没有立场去改它。保守是它的美德，
    也是它的代价 —— 它在发展集上会**输给**那个"整个文件换回基线"的定点修法。

    这不是为了演示而造的缺陷，而是 reward hacking 之所以发生的真实土壤：
    **讨好判据的修法在发展集上确实更好**，所以它会赢，所以必须靠 held-out 拦。
    """
    b = ast.parse(broken_src)
    g = ast.parse(base_src)

    def ops(tree):
        """按同一遍历序收集控制流节点（**存节点本身**，不是它的 op）。"""
        out: List[Tuple[str, Any]] = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare):
                out.append(("cmp", n))
            elif isinstance(n, ast.BinOp):
                out.append(("bin", n))
        return out

    bo, go = ops(b), ops(g)
    if len(bo) != len(go):
        #: ⛔ 与 `revert_literals` 同一条防线：树形不同 ⇒ 遍历序不可信
        #:    ⇒ 宁可不动。这里没有"部分对齐"这种折中：错位一次就污染一片。
        return broken_src
    for i in range(min(len(bo), len(go))):
        kind, node = bo[i]
        ref = go[i][1]
        # 只有两棵树**同一位置是同类节点**才恢复：结构对不上就跳过，
        # 不硬套（硬套会静默改坏没被变异的地方）。
        if kind == "cmp" and isinstance(ref, ast.Compare):
            node.ops = list(ref.ops)
        elif kind == "bin" and isinstance(ref, ast.BinOp):
            node.op = ref.op
    ast.fix_missing_locations(b)
    return ast.unparse(b)


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
    b = ast.parse(broken_src)
    g = ast.parse(base_src)
    #: ⛔⛔ 口径必须与变异器同源（`environment.numeric_literals`）。
    #:    以前这里**排除 bool** 而变异器不排除 ⇒ 两边序列长度不同
    #:    ⇒ 下面的对齐会整体错位 ⇒ 把没坏的常量改坏（实测 4 条残差）。
    #: ⛔ 传**树**不是字符串：必须拿到与 `b`/`g` 同一批节点对象。
    bo, go = numeric_literals(b), numeric_literals(g)
    if len(bo) != len(go):
        #: ⛔ fail-closed：长度不等说明树形已不同，遍历序无法保证对应
        #:    ⇒ 宁可一条都不改，也不许"错位污染"。
        return broken_src
    for i in range(len(bo)):
        bo[i].value = go[i].value
    ast.fix_missing_locations(b)
    return ast.unparse(b)


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
    b = ast.parse(broken_src)
    g = ast.parse(base_src)

    def boolops(tree):
        return [n for n in ast.walk(tree) if isinstance(n, ast.BoolOp)]

    bo, go = boolops(b), boolops(g)
    if len(bo) != len(go):
        #: ⛔ 与另两个修法同一条防线：树形不同 ⇒ 遍历序不可信 ⇒ 不动。
        return broken_src
    for i in range(len(bo)):
        bo[i].op = type(go[i].op)()
    ast.fix_missing_locations(b)
    return ast.unparse(b)


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
    b = ast.parse(broken_src)
    g = ast.parse(base_src)
    bo, go = string_literals(b), string_literals(g)
    if len(bo) != len(go):
        return broken_src        #: fail-closed：树形已变 ⇒ 不许错位对齐
    for i in range(len(bo)):
        bo[i].value = go[i].value
    ast.fix_missing_locations(b)
    return ast.unparse(b)


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
    b = ast.parse(broken_src)
    g = ast.parse(base_src)

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

    new_root = align(b, g)
    ast.fix_missing_locations(b)
    try:
        return ast.unparse(new_root)
    except Exception:                                          # noqa: BLE001
        return broken_src


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
