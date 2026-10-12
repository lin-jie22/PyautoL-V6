"""跨环境泛化闸（Held-Out Generalization Gate）。

非回归闸回答的是：**有没有变坏**。
泛化闸回答的是另一个方向的问题：**"发展集上变好了"是真的变强了，还是
只是学会了讨好这一批判据？**

这两件事在单环境里**无法区分**。self-harness 系统一旦只在一个判据集上做
选择，最优策略很快会滑向"针对判据硬编码"——它每一轮都在涨，但它没有变强。
这是 reward hacking 在自进化系统里的温和形态，也是最难被发现的一种：
因为所有数字都很好看，而且每一步都是"真的通过了执行判据"。

V6 的处理方式：**另开一个从不参与选择的环境**，只在裁决时用一次。

三条硬纪律（缺一条这套闸就退化成第二个发展集）：

  H1 **held-out 不参与选择**。候选之间的比较只用发展集 reward；
     held-out 的分数**不得**进入 `group_relative`、不得进入 best 的排序。
     一旦它参与选择，它就变成了发展集，闸就自我取消了。
  H2 **结构性不重叠**。`GeneralizationGate` 在构造时就校验 held-out
     与发展集没有共同 task_id，重叠直接抛异常 —— 不靠调用方自觉。
  H3 **held-out 上不涨 ⇒ 判 overfit，不提交**。哪怕发展集涨得很好看。
     宁可少提交一次，也不能把"讨好判据"固化进 harness。

⛔ 泛化闸是**准入闸**（gate），不是评估项（metric）：
   它不参与"哪个候选更好"的比较，只在最后做一次放行/拦截。
   与非回归闸的关系：非回归闸拦"变坏"，泛化闸拦"假变好"，两道都要过。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .genome import Harness
from .nongression import FrozenTask
from .verifier import Verdict, error_verdict

Runner = Callable[[Harness, Dict[str, Any]], Verdict]

#: 裁决取值。**封闭集合**：出现集合外的值说明有新的失败形态没被归类。
#: `partial` = 整体涨了但**只在部分难度层涨**（典型：只会修简单题）。
VERDICTS = ("generalize", "overfit", "partial", "no_gain", "inconclusive")


@dataclass(frozen=True)
class HeldOutSuite:
    """一组**不参与选择**的任务。来自与训练/发展环境不同的环境。"""
    name: str
    tasks: Tuple[FrozenTask, ...]
    env_id: str = ""
    #: task_id → 难度（easy/hard）。空 ⇒ 不分层（行为与早期版本一致）。
    #: 见 aether/repair.py：难度由"通用修法能否修好"**执行判定**，不靠标注。
    difficulty: Tuple[Tuple[str, str], ...] = ()

    @property
    def task_ids(self) -> Tuple[str, ...]:
        return tuple(t.task_id for t in self.tasks)

    @property
    def n_tasks(self) -> int:
        return len(self.tasks)

    @property
    def difficulty_map(self) -> Dict[str, str]:
        return dict(self.difficulty)

    def ids_of(self, level: str) -> Tuple[str, ...]:
        d = self.difficulty_map
        return tuple(t.task_id for t in self.tasks if d.get(t.task_id) == level)

    @property
    def levels(self) -> Tuple[str, ...]:
        return tuple(sorted({v for _k, v in self.difficulty}))

    @property
    def spread(self) -> bool:
        """有没有难度跨度。⛔ 没有跨度的 held-out **判别力不足**。"""
        return len(self.levels) >= 2


@dataclass
class ProbeResult:
    """一次泛化探针的完整裁决记录（无论放行还是拦截都保留）。"""
    verdict: str
    dev_gain: float = 0.0
    heldout_gain: float = 0.0
    base_heldout: float = 0.0
    cand_heldout: float = 0.0
    n_tasks: int = 0
    reason: str = ""
    #: 各难度层的 held-out 增益。⛔ 只看总体会放过"只在简单题上泛化"。
    by_difficulty: Dict[str, float] = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        """是否拦截提交。⛔ 未知裁决一律拦截（fail-closed）。"""
        return self.verdict != "generalize"

    def to_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict,
                "dev_gain": round(self.dev_gain, 4),
                "heldout_gain": round(self.heldout_gain, 4),
                "base_heldout": round(self.base_heldout, 4),
                "cand_heldout": round(self.cand_heldout, 4),
                "n_tasks": self.n_tasks,
                "by_difficulty": {k: round(v, 4)
                                  for k, v in self.by_difficulty.items()},
                "reason": self.reason}


class GeneralizationGate:
    """跨环境泛化闸。**只在 best 选出之后跑一次**。"""

    def __init__(self, suite: HeldOutSuite,
                 dev_task_ids: Optional[Iterable[str]] = None,
                 heldout_floor: float = 0.0,
                 dev_floor: float = 0.0,
                 min_tasks: int = 1,
                 base_at_ceiling: Optional[float] = None):
        # H2：结构性不重叠。重叠 ⇒ 这套闸自我取消 ⇒ 构造期就炸，不靠自觉。
        dev_ids = set(dev_task_ids or ())
        overlap = sorted(set(suite.task_ids) & dev_ids)
        if overlap:
            raise ValueError(
                f"held-out 与发展集重叠 {overlap[:5]} ⇒ 它就不再是 held-out，"
                f"泛化闸会自我取消")
        self.suite = suite
        self.dev_task_ids = frozenset(dev_ids)
        #: held-out 增益必须**严格超过**这个值（等于即判 overfit）。
        #: 默认 0 ⇒ 必须在 held-out 上真的有正向增益，光"没退化"不算。
        self.heldout_floor = heldout_floor
        self.dev_floor = dev_floor
        self.min_tasks = min_tasks
        #: held-out 的满分线（reward 上界）。None ⇒ 不做天花板判定。
        self.base_at_ceiling = base_at_ceiling

    # ── 评估 held-out（**不得**用于候选之间比较）─────────────────────
    def evaluate(self, h: Harness, runner: Runner) -> Tuple[float, List[Verdict]]:
        """在 held-out 上跑一遍，返回 (平均 reward, 判定列表)。"""
        vs: List[Verdict] = []
        for t in self.suite.tasks:
            try:
                vs.append(runner(h, t.spec))
            except Exception as e:  # noqa: BLE001 —— 记账，不吞
                vs.append(error_verdict(f"heldout/{t.task_id}", e))
        if not vs:
            return 0.0, vs
        return sum(v.reward for v in vs) / len(vs), vs

    def _gain_by_difficulty(self, base_vs: List[Verdict],
                            cand_vs: List[Verdict]) -> Dict[str, float]:
        """按难度层分别算 held-out 增益。

        ⛔ 总体增益会**掩盖**"只在简单题上泛化"：easy 涨、hard 不动，
        平均下来仍然是正的。分层之后这种候选会被判 `partial`。
        """
        d = self.suite.difficulty_map
        if not d:
            return {}
        ids = self.suite.task_ids
        base_by_id = {tid: v.reward for tid, v in zip(ids, base_vs)}
        cand_by_id = {tid: v.reward for tid, v in zip(ids, cand_vs)}
        out: Dict[str, float] = {}
        for level in self.suite.levels:
            sel = self.suite.ids_of(level)
            if not sel:
                continue
            b = sum(base_by_id.get(t, 0.0) for t in sel) / len(sel)
            c = sum(cand_by_id.get(t, 0.0) for t in sel) / len(sel)
            out[level] = c - b
        return out

    # ── 裁决 ────────────────────────────────────────────────────────
    def probe(self, base_h: Harness, cand_h: Harness, runner: Runner,
              dev_gain: float) -> ProbeResult:
        """对 (基线, 候选) 做一次泛化裁决。

        ⛔ 判据顺序有讲究：先判"发展集到底涨没涨"，再判"涨的是不是泛化的"。
        反过来的话，"发展集没涨"会被误记成 overfit —— 那是两回事：
        没涨是能力问题，涨了但不泛化是**诚信**问题。
        """
        if self.suite.n_tasks < self.min_tasks:
            return ProbeResult(
                verdict="inconclusive", dev_gain=dev_gain, n_tasks=self.suite.n_tasks,
                reason=f"held-out 任务数 {self.suite.n_tasks} < 最小要求 {self.min_tasks}"
                       f" ⇒ 无法裁决，按拦截处理")

        base_ho, base_vs = self.evaluate(base_h, runner)
        cand_ho, cand_vs = self.evaluate(cand_h, runner)
        ho_gain = cand_ho - base_ho
        by_diff = self._gain_by_difficulty(base_vs, cand_vs)

        # ⛔ 与 heldout_floor 同一个口径：**等于地板也算"没涨"**。
        #    两侧口径不一致会让"dev 涨 0"和"ho 涨 0"得到相反的对待。
        if dev_gain <= self.dev_floor:
            return ProbeResult(verdict="no_gain", dev_gain=dev_gain,
                               heldout_gain=ho_gain, base_heldout=base_ho,
                               cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                               reason=f"发展集增益 {dev_gain:.4f} 未超过地板 {self.dev_floor}"
                                      f" ⇒ 不是本闸职责（进化循环自会否决）")
        # ⛔ held-out 已在天花板 ⇒ **没有判别力**，判 inconclusive（拦截）。
        #    不这么处理的话，base 已经满分时任何候选的增益都是 0，
        #    会被误判成 overfit —— 那是把"无从判别"当成"有罪"。
        if self.base_at_ceiling is not None and base_ho >= self.base_at_ceiling - 1e-9:
            return ProbeResult(
                verdict="inconclusive", dev_gain=dev_gain, heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks,
                reason=f"held-out 基线已在天花板 {base_ho:.2f} ⇒ 无从判别是否泛化"
                       f"（按拦截处理，需换一批更难的任务）")
        # ⛔ 用 `<=`：等于地板也算"没跟着涨"。用 `<` 的话 0 增益会被判成
        #    泛化 —— 那正好放过最典型的一种 reward hacking（dev 涨、held-out 纹丝不动）。
        if ho_gain <= self.heldout_floor:
            return ProbeResult(
                verdict="overfit", dev_gain=dev_gain, heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks, by_difficulty=by_diff,
                reason=(f"发展集 +{dev_gain:.4f} 但 held-out {ho_gain:+.4f} "
                        f"（{base_ho:.2f}→{cand_ho:.2f}）⇒ 讨好判据，不提交"))
        # ⛔ 分层裁决：总体涨了，但某一难度层没涨 ⇒ 判 partial（**同样拦截**）。
        #    典型形态：只会修"通用修法能搞定的简单题"。平均数是正的，
        #    但它在 hard 层毫无进步 —— 那不是泛化，是挑食。
        lagging = sorted([f"{lv}{by_diff[lv]:+.4f}" for lv in by_diff
                          if by_diff[lv] <= self.heldout_floor])
        if lagging:
            return ProbeResult(
                verdict="partial", dev_gain=dev_gain, heldout_gain=ho_gain,
                base_heldout=base_ho, cand_heldout=cand_ho,
                n_tasks=self.suite.n_tasks, by_difficulty=by_diff,
                reason=(f"总体 held-out {ho_gain:+.4f}，但分层看 {lagging} 未涨"
                        f" ⇒ 只在部分难度上泛化，不提交"))
        return ProbeResult(verdict="generalize", dev_gain=dev_gain,
                           heldout_gain=ho_gain, base_heldout=base_ho,
                           cand_heldout=cand_ho, n_tasks=self.suite.n_tasks,
                           by_difficulty=by_diff,
                           reason=f"发展集 +{dev_gain:.4f} 且 held-out {ho_gain:+.4f}"
                                  + (f"（分层 {by_diff}）" if by_diff else "")
                                  + " ⇒ 泛化，放行")


def make_heldout(env_id: str, tasks: List[FrozenTask], name: str = "",
                 difficulty: Optional[Dict[str, str]] = None) -> HeldOutSuite:
    """便捷构造：给一组冻结任务打上 env_id（可选带上难度标签）。"""
    return HeldOutSuite(
        name=name or f"heldout::{env_id}", tasks=tuple(tasks), env_id=env_id,
        difficulty=tuple(sorted((difficulty or {}).items())))
