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
from typing import Any, Callable, Dict, List, Optional, Tuple

from .environment import Environment, verify_source

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
        if isinstance(spec, dict):
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
