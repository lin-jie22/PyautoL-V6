"""冻结非回归闸（Deterministic Non-Regression Gate）。

2026 的 self-harness 综述把**灾难性遗忘 / 回归**列为自进化系统的头号
失败模式，而论文给出的缓解手段正是 "deterministic non-regression gates"
——确定性的、不受模型波动影响的非回归闸门。

这一条恰好是 V5 最硬的资产（红契约 + 冻结基线 + 三轮对照 + 零新红），
V6 把它从"工程师的纪律"升级成"系统的准入条件"：

    **任何 harness 变体，必须先过冻结闸，才允许被评估"有没有变好"。**

闸门语义（三条，缺一不可）：
  G1 冻结套件全绿：候选在冻结任务集上不得让任何一条**原本通过**的任务变红；
  G2 口径一致：评估口径（caliber）没变才能比；口径变了必须 supersede 留痕，
     并**禁止**与旧口径同表对比（V5：`wait_terminal` ON/OFF 两口径不可比）；
  G3 判定同源：闸用的是 verifier 自己的 Verdict.reward，不是这里另写一套
     （V5 实测：重写判据差 3.8pt，与效应同量级）。

⛔ 闸门只对"冻结套件"负责，不对"目标集"负责：
   先证明没变坏（闸），再证明变好了（进化选择）。两个方向不能混在一张表里。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from .genome import Harness
from .ledger import content_hash
from .verifier import Verdict, error_verdict

Runner = Callable[[Harness, Dict[str, Any]], Verdict]


@dataclass(frozen=True)
class FrozenTask:
    """冻结任务：spec 是可执行判据所需的输入，**不含期望答案**。

    ⛔ spec 里不许塞 expected_reward —— 那会让闸变成"比对数字"，
    而数字是被测对象自己报的 ⇒ 立刻退化成自述即奖励。
    """
    task_id: str
    spec: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def fingerprint(self) -> str:
        return content_hash({"task_id": self.task_id, "spec": self.spec})[:16]


@dataclass(frozen=True)
class Baseline:
    """冻结基线：某口径下、某 harness 在冻结套件上的判定集合。"""
    caliber: Dict[str, Any]                  # 口径签名（seed/模型/超时/…）
    verdicts: Dict[str, Verdict]             # task_id → Verdict
    harness_id: str = ""
    superseded: bool = False
    supersede_reason: str = ""

    @property
    def baseline_id(self) -> str:
        return content_hash({
            "caliber": self.caliber,
            "verdicts": {k: v.to_dict() for k, v in sorted(self.verdicts.items())},
        })[:16]

    def passed_ids(self) -> List[str]:
        return sorted(k for k, v in self.verdicts.items() if v.reward > 0)


@dataclass
class GateResult:
    """闸门裁决。**结构化**：不只是 bool，还要能说清哪条退了、怎么退的。"""
    passed: bool
    regressions: List[str] = field(default_factory=list)
    caliber_ok: bool = True
    caliber_reason: str = ""
    deltas: Dict[str, float] = field(default_factory=dict)
    verdicts: Dict[str, Verdict] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "regressions": self.regressions,
            "caliber_ok": self.caliber_ok,
            "caliber_reason": self.caliber_reason,
            "deltas": self.deltas,
            "n_tasks": len(self.verdicts),
            "errors": self.errors,
        }


class NonRegressionGate:
    """非回归闸。

    ⛔ 设计取舍：**不做**"允许少量回归换取大幅增益"的权衡开关。
    V5 的交叉表经验表明，一旦允许 trade-off，回归会被系统性低估
    （失败项会静默累积，直到某天"零新红"这个判据本身失效）。
    想要 trade-off ⇒ 显式 supersede 基线并留痕，走人工闸门。
    """

    def __init__(self, suite: List[FrozenTask], baseline: Baseline):
        self.suite = suite
        self.baseline = baseline

    # ── 口径闸（G2）───────────────────────────────────────────────
    def check_caliber(self, caliber: Dict[str, Any]) -> Tuple[bool, str]:
        """口径必须与基线一致，否则禁止对比。

        ⛔ 这是 fail-closed：口径对不上就判"不可比"，不是"大致可比"。
        """
        if self.baseline.superseded:
            return False, (f"基线已被 supersede（{self.baseline.supersede_reason}），"
                           f"禁止再作为非回归基准")
        missing = [k for k in self.baseline.caliber if k not in caliber]
        if missing:
            return False, f"口径缺字段 {sorted(missing)}"
        diff = {k: (self.baseline.caliber[k], caliber[k])
                for k in self.baseline.caliber if caliber[k] != self.baseline.caliber[k]}
        if diff:
            return False, f"口径不一致 {diff} ⇒ 两口径不可比，需先 supersede"
        return True, ""

    # ── 主闸 ──────────────────────────────────────────────────────
    def evaluate(self, harness: Harness, runner: Runner,
                 caliber: Dict[str, Any]) -> GateResult:
        """跑冻结套件并裁决。

        ⛔ 单条任务崩了 ⇒ 整闸失败（fail-closed）。
        "跑挂了"不等于"没回归"，把它当通过是自进化系统最危险的默认。
        """
        ok, why = self.check_caliber(caliber)
        if not ok:
            return GateResult(passed=False, caliber_ok=False, caliber_reason=why)

        verdicts: Dict[str, Verdict] = {}
        errors: List[str] = []
        for t in self.suite:
            try:
                verdicts[t.task_id] = runner(harness, t.spec)
            except Exception as e:  # noqa: BLE001 —— 记账，不吞
                v = error_verdict(f"gate/{t.task_id}", e)
                verdicts[t.task_id] = v
                errors.append(f"{t.task_id}: {v.error}")

        regressions: List[str] = []
        deltas: Dict[str, float] = {}
        for tid, base in self.baseline.verdicts.items():
            if base.reward <= 0:
                continue                       # 本来就没过 ⇒ 不参与回归判定
            cur = verdicts.get(tid)
            if cur is None:
                regressions.append(f"{tid}: 缺失判定")
                continue
            deltas[tid] = cur.reward - base.reward
            if cur.reward <= 0:
                regressions.append(
                    f"{tid}: {base.reward:.0f}→{cur.reward:.0f}"
                    + (f" ({cur.error})" if cur.error else ""))

        return GateResult(
            passed=(not regressions) and (not errors),
            regressions=regressions,
            caliber_ok=True,
            deltas=deltas,
            verdicts=verdicts,
            errors=errors,
        )


def supersede(baseline: Baseline, new_caliber: Dict[str, Any],
              reason: str) -> Tuple[Baseline, Baseline]:
    """口径变更：返回 **(新基线, 旧基线标记 superseded 后的副本)**。

    ⛔ 留痕是强制的：V5 的 `baseline_superseded_old_caliber.json` 就是这个
    形态，它让"为什么分数突然变了"可以被事后查到。
    ⛔ 返回二元组而不是"就地替换"：调用方必须显式处理旧基线，
    不能假装它不存在。
    """
    new = Baseline(caliber=new_caliber, verdicts={}, harness_id="",
                   superseded=False, supersede_reason=reason)
    old = replace(baseline, superseded=True, supersede_reason=reason)
    return new, old


def pass_at_k(scores: List[float], k: Optional[int] = None) -> float:
    """pass@k：k 次里至少一次通过的概率（无偏估计）。

    用于"冻结套件"之外的发展集衡量：单次通过率方差太大，V5 三轮合并
    就是这个道理（score 0.7184 / pass@3 0.7931）。
    """
    n = len(scores)
    if n == 0:
        return 0.0
    k = k or n
    k = min(k, n)
    c = sum(1 for s in scores if s > 0)
    if c == 0:
        return 0.0
    # 无偏估计：1 - C(n-c, k)/C(n, k)
    from math import comb
    return 1.0 - comb(n - c, k) / comb(n, k)
