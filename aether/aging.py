"""跨轮退化检测（agent lifespan / aging）。

## 前沿依据

ACE / ALE（Agent Lifespan Engineering, 2026）研究的是**长期退化**：
压缩（compression）、干扰（interference）、修订（revision）、维护
（maintenance）这四种机制，会让一个"每一轮单看都没亏"的系统，在长跑
之后悄悄变弱 —— **行为测试看起来正常，准确率却已经衰减**。
它的读法不是看 aggregate reward，而是**时间依赖图 + 反事实探针**。

## V6 为什么需要它

已有的 `nongression.NonRegressionGate` 是**横截面**的：它回答
"这个变体相对冻结基线有没有变坏"，一次只看一轮。它是**准入闸**，
不是**寿命计**。

退化是**纵向**的：第 1 轮到第 N 轮之间，同一批冻结探针上的 reward
是不是在自己往下掉。单轮闸绿灯 ⇏ 长跑不退化 —— 本项目就实测过这个形态：
装一个新技能把旧的顶掉 ⇒ easy 层从 +1.0 掉到 −1.0，而那一轮的非回归闸
照样是绿的（回归是"相对冻结基线"，不是"相对上一轮的自己"）。

## 四条纪律

1. ⛔ **探针集必须跨轮完全一致**。换了探针，reward 的差值是"换尺子"
   不是"变弱" ⇒ 整体标注 `comparable=False`（fail-closed），不报健康。
2. ⛔ **只看下降，不看上升**。上升是进步，不是退化的反面证据。
   把上升也算进"healthy"会造出一个涨了就永远绿的闸。
3. ⛔ **事件没发生过 ⇒ 该类信号不可判别**，标注 `available=False`。
   ⛔⛔ 它不是 healthy —— "没做过巩固"不等于"巩固没退化"。
   这是全项目最贵的一条：不可判别 ≠ 通过。
4. ⛔ **证据是逐条任务的 Δ**，不是聚合分。聚合分会把
   "两道题一升一降"读成"没变化"，而那正是干扰的典型指纹。

## 为什么不把它做进非回归闸

两者正交，且**必须正交**：非回归闸是"能不能进"，老化检测是"进来之后
还在不在变弱"。合一之后，一个"放行但正在退化"的系统会同时骗过两件事。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 四类退化机制。⛔ 与上面的纪律一一对应，**不加第五类**：
#: 多一类就多一个"没发生 ⇒ 假装通过"的出口。
AGING_KINDS: Tuple[str, ...] = (
    "compression",     # 巩固/压缩：L4 → L3 归纳后丢了细节
    "interference",    # 干扰：装新技能之后，旧技能的任务掉了
    "revision",        # 修订：口径变更（supersede）后读数不可比
    "maintenance",     # 维护：剪枝（prune）之后召回命中下降
)

#: ⛔ 判定为"下降"的阈值。**显式**且默认极小：宁可报"疑似"，
#:    也不要让一个 ε 悄悄决定系统算不算健康。
DEFAULT_EPS: float = 1e-9


@dataclass(frozen=True)
class ProbeResult:
    """冻结探针上一道题的读数。"""
    task_id: str
    reward: float

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "reward": round(self.reward, 6)}


@dataclass(frozen=True)
class RoundTrace:
    """一轮进化之后的**纵向**读数。

    ⛔ `probes` 必须是**同一批**冻结探针（跨轮不可变）。
    ⛔ `consolidated` / `pruned` / `skill_count` 是"这一轮发生了什么事件"，
       —— 没有事件发生 ⇒ 对应信号无从判别（见 `detect_aging`）。
    """
    round_no: int
    harness_id: str
    probes: Tuple[ProbeResult, ...] = ()
    skill_count: int = 0
    memory_pool: int = 0
    consolidated: int = 0
    pruned: int = 0
    #: ⛔ 增殖闸的**候选数**（`prune_dryrun()` 报的"将剪 N 条"）。
    #:    自动路径**只 dry-run 不删** ⇒ `pruned` 恒 0，单看它无法区分
    #:    "库很干净"和"闸没接线"。`prune_candidates > 0` 才是"该剪了"的
    #:    信号。此前 CLI 写了 `aging_events["prune_candidates"]` 却**没有人
    #:    消费**（死字段）⇒ 增殖在账本里依然不可见（V5：有机制 ≠ 这条路
    #:    被走到）。
    prune_candidates: int = 0
    #: 口径指纹（seed / 判据版本等）。跨轮不同 ⇒ 读数不可比。
    caliber: Dict[str, Any] = field(default_factory=dict)

    @property
    def probe_ids(self) -> Tuple[str, ...]:
        return tuple(p.task_id for p in self.probes)

    @property
    def mean_reward(self) -> float:
        if not self.probes:
            return 0.0
        return sum(p.reward for p in self.probes) / len(self.probes)

    def to_dict(self) -> Dict[str, Any]:
        return {"round_no": self.round_no, "harness_id": self.harness_id,
                "probes": [p.to_dict() for p in self.probes],
                "skill_count": self.skill_count,
                "memory_pool": self.memory_pool,
                "consolidated": self.consolidated, "pruned": self.pruned,
                "prune_candidates": self.prune_candidates,
                "caliber": dict(self.caliber),
                "mean_reward": round(self.mean_reward, 6)}


@dataclass(frozen=True)
class AgingSignal:
    """一类退化机制的裁决。"""
    kind: str
    #: 该类事件是否在这串轮次里发生过 ⇒ 可判别
    available: bool
    detected: bool
    #: 事件轮次相对上一轮的 reward **均值**变化（负 = 下降）
    delta: float
    #: ⛔ 逐条下降的探针（纪律 4：证据是逐条 Δ，不是聚合分）
    degraded: Tuple[str, ...] = ()
    #: 同时上升的探针。⛔ 它与 degraded 并存 = **此消彼长**，正是 ALE 点名
    #:   的干扰指纹：均值一平均就把它抹平成"没变化"，而系统其实在换能力。
    improved: Tuple[str, ...] = ()
    #: uniform_decline（只降）| interference（有升有降）| none
    pattern: str = "none"
    evidence: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "available": self.available,
                "detected": self.detected, "delta": round(self.delta, 6),
                "degraded": list(self.degraded),
                "improved": list(self.improved),
                "pattern": self.pattern,
                "evidence": list(self.evidence)}


@dataclass(frozen=True)
class AgingReport:
    """整条寿命线的裁决。"""
    rounds: int
    probe_ids: Tuple[str, ...] = ()
    #: 探针集跨轮是否一致（False ⇒ 读数不可比 ⇒ 不判健康）
    comparable: bool = True
    signals: Tuple[AgingSignal, ...] = ()
    #: 首轮 → 末轮的 reward 均值变化（寿命含义：整体在涨还是在掉）
    overall_delta: float = 0.0
    #: 单轮最大下降（"最差的一次回退"）
    worst_regression: float = 0.0
    reason: str = ""

    @property
    def healthy(self) -> bool:
        """只有**可比**且**没有任何已判别信号报警**才算健康。

        ⛔ `available=False` 的信号**不**参与放行：它既不是健康证据，
        也不是退化证据，它是"这一项这次没看到" —— 必须单独看。
        """
        if not self.comparable:
            return False
        return not any(s.detected for s in self.signals if s.available)

    @property
    def undetermined(self) -> Tuple[str, ...]:
        """哪些信号这次**无从判别**（不是通过）。"""
        return tuple(s.kind for s in self.signals if not s.available)

    def to_dict(self) -> Dict[str, Any]:
        return {"rounds": self.rounds, "probe_ids": list(self.probe_ids),
                "comparable": self.comparable,
                "signals": [s.to_dict() for s in self.signals],
                "overall_delta": round(self.overall_delta, 6),
                "worst_regression": round(self.worst_regression, 6),
                "healthy": self.healthy,
                "undetermined": list(self.undetermined),
                "reason": self.reason}


# ──────────────────────────────────────────────────────────────────
# 判据
# ──────────────────────────────────────────────────────────────────
def _events(kind: str, prev: RoundTrace, cur: RoundTrace) -> bool:
    """这一轮是否发生了该类事件。⛔ 单一定义，别处不许重写。"""
    if kind == "compression":
        return cur.consolidated > 0
    if kind == "interference":
        return cur.skill_count > prev.skill_count
    if kind == "maintenance":
        return cur.pruned > 0
    if kind == "revision":
        return dict(cur.caliber or {}) != dict(prev.caliber or {})
    return False


def _probe_deltas(prev: RoundTrace, cur: RoundTrace, eps: float
                  ) -> Tuple[Dict[str, float], Tuple[str, ...], Tuple[str, ...]]:
    """逐条探针的 Δ（纪律 4 的载体）。

    ⛔ 返回**逐条**而不是均值：干扰的指纹是"一升一降"，均值一平均就
      把它抹成"没变化"（ALE 点名的头号误读）。
    """
    pm = {q.task_id: q.reward for q in prev.probes}
    cm = {p.task_id: p.reward for p in cur.probes}
    delta = {tid: cm[tid] - pm[tid] for tid in cm if tid in pm}
    degraded = tuple(sorted(t for t, d in delta.items() if d < -eps))
    improved = tuple(sorted(t for t, d in delta.items() if d > eps))
    return delta, degraded, improved


def detect_aging(traces: Sequence[RoundTrace],
                 eps: float = DEFAULT_EPS) -> AgingReport:
    """在**同一批冻结探针**上，检测跨轮退化。

    ⛔ 只看相邻两轮之间的**下降**（纵向），不是相对冻结基线（那是闸的活）。
    ⛔ 探针集为空 / 跨轮不一致 ⇒ `comparable=False` ⇒ healthy=False（fail-closed）。
    ⛔ 事件未发生 ⇒ 该类 `available=False` ⇒ 既不放行也不报警，单列 `undetermined`。
    ⛔⛔ 判定看**逐条** degraded，不看均值：有升有降 = 干扰指纹（`interference`
       pattern）也报警 —— 那意味着系统正在用新能力换掉旧能力，不是没变化。
    """
    if not traces:
        return AgingReport(rounds=0, comparable=False,
                           reason="没有轮次 ⇒ 无从判别（fail-closed）")

    first_ids = traces[0].probe_ids
    if not first_ids:
        return AgingReport(rounds=len(traces), comparable=False,
                           reason="冻结探针集为空 ⇒ 测不出退化（fail-closed）")

    for t in traces[1:]:
        if t.probe_ids != first_ids:
            return AgingReport(
                rounds=len(traces), comparable=False,
                reason=("探针集跨轮发生变化 ⇒ 读数是换尺子不是变弱"
                        "（fail-closed：不可比就不判健康）"))

    deltas: List[float] = []
    for i in range(1, len(traces)):
        deltas.append(traces[i].mean_reward - traces[i - 1].mean_reward)

    signals: List[AgingSignal] = []
    for kind in AGING_KINDS:
        ev_rounds = [i for i in range(1, len(traces))
                     if _events(kind, traces[i - 1], traces[i])]
        if not ev_rounds:
            signals.append(AgingSignal(
                kind=kind, available=False, detected=False, delta=0.0,
                pattern="none",
                evidence=(f"这 {len(traces)} 轮里没有发生过 {kind} 事件"
                          " ⇒ 该类退化本次无从判别（不是通过）",)))
            continue

        #: ⛔ revision 是**唯一的例外**：口径一变，纵向 Δ 本身就不可比了
        #:    （尺子换了）。这时不该等"下降"才报警 —— 读数已经不可信。
        #:    它报的不是"变弱了"，而是"从这一轮起没法判断变没变弱"。
        if kind == "revision":
            signals.append(AgingSignal(
                kind=kind, available=True, detected=True, delta=0.0,
                pattern="revision",
                evidence=tuple(
                    f"第 {i + 1} 轮口径指纹变更（{sorted((traces[i - 1].caliber or {}).items())}"
                    f" → {sorted((traces[i].caliber or {}).items())}）"
                    " ⇒ 此后纵向 Δ 不可比"
                    for i in ev_rounds)))
            continue

        #: 取**下降最严重**的一轮作为该类信号的代表读数
        worst = None
        for i in ev_rounds:
            prev, cur = traces[i - 1], traces[i]
            _, deg, imp = _probe_deltas(prev, cur, eps)
            cand = (cur.mean_reward - prev.mean_reward, deg, imp, cur.probes,
                    prev.probes)
            if worst is None or cand[0] < worst[0]:
                worst = cand
        delta, deg, imp, cprobes, pprobes = worst
        pm = {q.task_id: q.reward for q in pprobes}
        cm = {p.task_id: p.reward for p in cprobes}
        ev = tuple(f"{tid}: {pm[tid]:.4f} → {cm[tid]:.4f}"
                   f" (Δ{cm[tid] - pm[tid]:+.4f})" for tid in deg)
        if deg and imp:
            pattern = "interference"    # ⛔ 此消彼长：均值抹平，但确实在退化
        elif deg:
            pattern = "uniform_decline"
        else:
            pattern = "none"
        signals.append(AgingSignal(kind=kind, available=True,
                                   detected=bool(deg), delta=delta,
                                   degraded=deg, improved=imp,
                   pattern=pattern,
                   evidence=ev or ("事件发生过，但没有探针下降",)))

    overall = traces[-1].mean_reward - traces[0].mean_reward
    worst_d = min(deltas) if deltas else 0.0
    undet = [s.kind for s in signals if not s.available]
    if undet:
        reason = (f"已判别 {len(AGING_KINDS) - len(undet)}/{len(AGING_KINDS)} 类；"
                  f"未判别（非通过）：{', '.join(undet)}"
                  + (f"；整体 Δ={overall:+.4f}, 最差单轮回退={worst_d:+.4f}"
                     if len(undet) < len(AGING_KINDS) else ""))
    else:
        reason = (f"四类均已判别；整体 Δ={overall:+.4f}，"
                  f"最差单轮回退={worst_d:+.4f}")

    return AgingReport(rounds=len(traces), probe_ids=first_ids,
                       comparable=True, signals=tuple(signals),
                       overall_delta=overall, worst_regression=worst_d,
                       reason=reason)
