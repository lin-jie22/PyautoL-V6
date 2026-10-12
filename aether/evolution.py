"""Self-Harness 进化循环：弱点挖掘 → 最小编辑 → 非回归闸 → 组内选择 → 提交/回滚。

2026 主线的实现（Self-Harness / AutoHarness / A-Evolve / HarnessX 的共同骨架）：

    MINE      从失败轨迹里挖**弱点**（不是随机变异）
    PROPOSE   产出**最小编辑**（一个 capsule = 一组协同的原子编辑）
    VALIDATE  先过**冻结非回归闸**，再在发展集上测（两个方向分两张表）
    SELECT    GRPO 式**组内相对优势**决定留谁（不是绝对分）
    COMMIT    通过才提交；否则 revert，且**两种情况都进账本**

V6 相对这些工作多做的三件事（来自 V5 的硬教训 + 2026 文献点名的失败模式）：

  1. **非回归闸是准入条件，不是评估项**。文献说 catastrophic forgetting
     是自进化头号失败模式、缓解手段是 deterministic non-regression gates；
     V5 恰好有这套资产（冻结套件 + 零新红）。这里把它前置为硬闸：
     闸不过 ⇒ **根本不评估它有没有变好**。

  2. **奖励只来自执行判据**（RLVR），且 Verdict 必须带 evidence。
     没有任何一步用"模型觉得更好"来做决策。

  3. **产物增殖闸 + 能力地板**：
     · capability floor —— 变体必须显著好于当前，否则不提交
       （文献：弱模型可能根本激活不了进化出来的 harness）；
     · proliferation gate —— harness 体积膨胀要有增益支撑，
       否则拒绝（文献：artifact proliferation 会让系统退化）。

⛔ 两种"成功"都要记账：提交成功、以及**正确地拒绝**。
   后者同样是有价值的信息（V5：只记成功 ⇒ 无法区分"没机会"和"没能力"）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .genome import Capsule, Harness, apply_capsule, blast_radius, diff
from .ledger import Ledger
from .nongression import FrozenTask, GateResult, NonRegressionGate
from .runtime import group_relative
from .store import RunRow, Store
from .verifier import Verdict

Runner = Callable[[Harness, Dict[str, Any]], Verdict]

# 弱点类别 → 提议器 kind 的映射（V5 纪律：词表是判据源，动它要做全量对拍）
_ERROR_TO_KIND: Tuple[Tuple[str, str], ...] = (
    ("no_skill", "missing_skill"),
    ("skill_not_found", "missing_skill"),
    ("unknown", "missing_skill"),
    ("tool_not_allowed", "missing_tool"),
    ("primitive", "missing_tool"),
    ("recall", "recall_miss"),
    ("memory", "recall_miss"),
)


@dataclass
class Weakness:
    """一个被挖出来的弱点：**必须由失败证据支撑**，不能凭感觉。"""
    kind: str
    topic: str
    task_ids: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    n_failed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "topic": self.topic,
                "task_ids": self.task_ids, "evidence": self.evidence[:5],
                "n_failed": self.n_failed}


def classify_error(text: str) -> str:
    """把失败信号归到弱点类别。⛔ 未知 ⇒ `missing_skill`（保守默认）。

    为什么保守默认是"缺技能"：默认"unknown/noop"会让进化循环**什么都不做**
    却记为成功，这是最难被发现的一种假成功。
    """
    low = (text or "").lower()
    for needle, kind in _ERROR_TO_KIND:
        if needle in low:
            return kind
    return "missing_skill"


def mine_weaknesses(runs: List[RunRow], min_failures: int = 1) -> List[Weakness]:
    """从失败 run 里挖弱点，按 (kind, topic) 聚合，按失败数排序。

    ⛔ 没有失败 ⇒ 返回空列表（不是"挖到一个通用弱点"）：
    没证据就不提议，让这一轮自然结束。
    """
    buckets: Dict[Tuple[str, str], Weakness] = {}
    for r in runs:
        if r.reward > 0:
            continue
        payload = r.payload or {}
        err = str(payload.get("error") or payload.get("reason") or "")
        kind = classify_error(err)
        topic = str(payload.get("topic") or r.task_id.split("::")[0])
        key = (kind, topic)
        w = buckets.setdefault(key, Weakness(kind=kind, topic=topic))
        w.task_ids.append(r.task_id)
        w.n_failed += 1
        if err:
            w.evidence.append(err[:120])
    out = [w for w in buckets.values() if w.n_failed >= min_failures]
    out.sort(key=lambda w: -w.n_failed)
    return out


@dataclass
class Candidate:
    """一个候选变体的完整裁决记录（无论成败都保留）。"""
    capsule_name: str
    harness_id: str
    gate_passed: bool = False
    gate_regressions: List[str] = field(default_factory=list)
    reward: float = 0.0
    advantage: float = 0.0
    accepted: bool = False
    reason: str = ""
    blast: Dict[str, Any] = field(default_factory=dict)
    n_verdicts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"capsule": self.capsule_name, "harness_id": self.harness_id,
                "gate_passed": self.gate_passed,
                "gate_regressions": self.gate_regressions,
                "reward": round(self.reward, 4),
                "advantage": round(self.advantage, 4),
                "accepted": self.accepted, "reason": self.reason,
                "blast": self.blast, "n_verdicts": self.n_verdicts}


@dataclass
class RoundReport:
    round_no: int
    weakness: Optional[Weakness] = None
    baseline_reward: float = 0.0
    candidates: List[Candidate] = field(default_factory=list)
    committed: Optional[str] = None
    revert_reason: str = ""
    ledger_seqs: List[int] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return self.committed is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"round_no": self.round_no,
                "weakness": self.weakness.to_dict() if self.weakness else None,
                "baseline_reward": round(self.baseline_reward, 4),
                "candidates": [c.to_dict() for c in self.candidates],
                "committed": self.committed, "revert_reason": self.revert_reason,
                "ledger_seqs": self.ledger_seqs}


@dataclass
class EvolutionConfig:
    """进化超参。**每个都有理由**，不是随手填的。"""
    group_size: int = 3                 # GRPO 的 G：组内相对 baseline 需要 >=2
    min_reward_gain: float = 0.01       # 能力地板：不显著 ⇒ 不提交
    min_advantage: float = 0.0          # 组内优势为正才考虑
    max_size_growth_ratio: float = 0.5  # 产物增殖闸：体积涨 50% 以上需更强增益
    require_human_for_protected: bool = True   # L0 触碰 ⇒ 停等人工
    dev_pass_threshold: float = 0.0     # 发展集 reward 阈值（0=全部计入）


class EvolutionLoop:
    """Self-Harness 进化循环。

    ⛔ 依赖注入（runner / proposer / gate / store）：本类不做 I/O 决策，
    于是**机制正确性**可以被确定性测试（V5 纪律：先证明机制跑得通）。
    """

    def __init__(self, ledger: Ledger, gate: NonRegressionGate,
                 dev_suite: List[FrozenTask], runner: Runner,
                 proposer: Any, store: Optional[Store] = None,
                 caliber: Optional[Dict[str, Any]] = None,
                 config: Optional[EvolutionConfig] = None):
        self.ledger = ledger
        self.gate = gate
        self.dev_suite = dev_suite
        self.runner = runner
        self.proposer = proposer
        self.store = store
        self.caliber = caliber or {"seed": 0}
        self.cfg = config or EvolutionConfig()
        #: 内存态 run 账（**不依赖 store**）：机制正确性不该依赖外部存储存在。
        #: store 只是它的持久化投影。
        self._runs: List[RunRow] = []

    # ── 评估 ──────────────────────────────────────────────────────
    def evaluate_dev(self, h: Harness, tag: str = "") -> Tuple[float, List[Verdict]]:
        """在**发展集**上评估（与非回归闸用的冻结套件**不是**同一批任务）。

        ⛔ 两个方向必须分开：冻结套件回答"有没有变坏"，发展集回答
        "有没有变好"。混在一张表里 ⇒ 用新任务的收益掩盖旧任务的回归。
        """
        vs: List[Verdict] = []
        for t in self.dev_suite:
            try:
                v = self.runner(h, t.spec)
            except Exception as e:  # noqa: BLE001 —— 记账，不吞
                from .verifier import error_verdict
                v = error_verdict(f"dev/{t.task_id}", e)
            vs.append(v)
            row = RunRow(
                    run_id=f"{tag}{h.harness_id[:8]}:{t.task_id}",
                    harness_id=h.harness_id, task_id=t.task_id,
                    reward=v.reward, passed=v.passed, gate_ok=False,
                    payload={
                    "verdict": v.to_dict(), "split": "dev",
                    "topic": t.spec.get("topic", ""),
                    # ⛔ 失败**原因**必须进 payload：弱点挖掘靠它归类，
                    # 只有 reward=0 而没有原因 ⇒ 只能落到"保守默认"类别。
                    "error": v.error or ("checks_failed:"
                                         + ",".join(c.name for c in v.checks
                                                    if not c.passed)),
                })
            self._runs.append(row)
            if self.store is not None:
                self.store.save_run(row, caliber=self.caliber)
            self.ledger.append("eval.verdict", {
                "harness_id": h.harness_id, "task_id": t.task_id,
                "reward": v.reward, "passed": v.passed,
                "split": "dev", "tag": tag,
                "verdict": v.to_dict(), "caliber": self.caliber,
            }, actor="evolution")
        if not vs:
            return 0.0, vs
        return sum(v.reward for v in vs) / len(vs), vs

    # ── 单轮 ──────────────────────────────────────────────────────
    def run_round(self, h: Harness, round_no: int = 0) -> RoundReport:
        rep = RoundReport(round_no=round_no)

        # 1) 基线探测 —— **必须放在挖掘之前**。
        #    第一轮时库里没有任何 run ⇒ 挖不到弱点 ⇒ 循环"什么都不做"
        #    却记为成功，这是自进化系统最难发现的假成功形态。
        #    先跑一次发展集，把失败证据写进库，再谈挖掘。
        base_reward, _ = self.evaluate_dev(h, tag="base:")
        rep.baseline_reward = base_reward

        # 2) MINE —— 弱点必须由**当前 harness 的**失败证据支撑。
        #    ⛔ 按 harness_id 过滤：拿旧 harness 的失败当证据 ⇒ 修好后
        #    还会反复"挖到"同一个弱点，循环永远停不下来。
        if self.store is not None:
            runs = self.store.runs(harness_id=h.harness_id, limit=200)
        else:
            runs = [r for r in self._runs if r.harness_id == h.harness_id]
        weaknesses = mine_weaknesses(runs)
        if not weaknesses:
            rep.revert_reason = "no_weakness_evidence（无失败证据 ⇒ 不提议）"
            rep.ledger_seqs.append(self.ledger.append(
                "evolve.noop", {"round_no": round_no, "harness_id": h.harness_id,
                                "reason": rep.revert_reason},
                actor="evolution").seq)
            return rep
        w = weaknesses[0]
        rep.weakness = w
        rep.ledger_seqs.append(self.ledger.append(
            "weakness.mined", {"round_no": round_no, **w.to_dict()},
            actor="evolution").seq)

        # 3) PROPOSE + 构造候选
        capsules = self.proposer.propose(w.to_dict(), h, n=self.cfg.group_size)
        cands: List[Candidate] = []
        for cap in capsules:
            try:
                cand_h = apply_capsule(h, cap)
            except Exception as e:  # noqa: BLE001
                cands.append(Candidate(capsule_name=cap.name, harness_id="",
                                       reason=f"apply_failed:{type(e).__name__}: {e}"))
                continue

            br = blast_radius(h, cand_h)
            c = Candidate(capsule_name=cap.name, harness_id=cand_h.harness_id,
                          blast=br)

            # 受保护层（L0）⇒ 停等人工，自动循环不得提交
            if br.get("touches_protected") and self.cfg.require_human_for_protected:
                c.reason = "blocked:protected_layer（L0 需人工审批）"
                self.ledger.append("gate.blocked", {
                    "round_no": round_no, "capsule": cap.name,
                    "harness_id": cand_h.harness_id, "blast": br}, actor="evolution")
                cands.append(c)
                continue

            # 4) 非回归闸（**准入**，不是评估项）
            g: GateResult = self.gate.evaluate(cand_h, self.runner, self.caliber)
            c.gate_passed = g.passed
            c.gate_regressions = g.regressions
            rep.ledger_seqs.append(self.ledger.append(
                "gate.result", {"round_no": round_no, "capsule": cap.name,
                                "harness_id": cand_h.harness_id,
                                **g.to_dict()}, actor="gate").seq)
            if not g.passed:
                c.reason = "gate_reject:" + (";".join(g.regressions)[:120]
                                             or g.caliber_reason)
                cands.append(c)
                continue

            # 5) 发展集评估（reward 只来自 verifier 执行）
            reward, vs = self.evaluate_dev(cand_h, tag="cand:")
            c.reward = reward
            c.n_verdicts = len(vs)
            cands.append(c)

        # 6) 组内相对优势（只对过了闸且有评估结果的候选算）
        scored = [c for c in cands if c.gate_passed and c.n_verdicts > 0]
        # ⛔ **基线必须进组**。否则当只有一个候选过闸时，组内 std=0 ⇒
        #    优势恒为 0 ⇒ 唯一有效候选永远无法提交（G=1 的退化）。
        #    GRPO 的"组"本就是同一任务下的一组采样，当前 harness 是其中之一。
        if scored:
            advs = group_relative([base_reward] + [c.reward for c in scored])
            for c, a in zip(scored, advs[1:]):
                c.advantage = a

        # 7) 选择：优势为正 + 超过能力地板 + 过了增殖闸
        best: Optional[Candidate] = None
        for c in scored:
            if c.advantage <= self.cfg.min_advantage:
                c.reason = c.reason or f"no_advantage({c.advantage:.3f})"
                continue
            gain = c.reward - base_reward
            if gain < self.cfg.min_reward_gain:
                c.reason = f"below_capability_floor(gain={gain:.4f})"
                continue
            growth = c.blast.get("size_delta", 0) / max(1, h.size())
            if growth > self.cfg.max_size_growth_ratio and gain < 0.1:
                c.reason = f"proliferation_gate(growth={growth:.2f}, gain={gain:.4f})"
                continue
            if best is None or c.reward > best.reward:
                best = c
        if best is not None:
            best.accepted = True
            best.reason = "accepted"

        rep.candidates = cands

        # 8) 提交 / 回滚 —— 两种结果都进账本
        if best is None:
            rep.revert_reason = "no_candidate_passed（闸/地板/增殖 至少一项未过）"
            rep.ledger_seqs.append(self.ledger.append(
                "harness.revert", {"round_no": round_no, "harness_id": h.harness_id,
                                   "reason": rep.revert_reason,
                                   "candidates": [c.to_dict() for c in cands]},
                actor="evolution").seq)
            return rep

        new_h = apply_capsule(h, next(cp for cp in capsules
                                      if cp.name == best.capsule_name))
        rep.committed = new_h.harness_id
        rep.ledger_seqs.append(self.ledger.append(
            "harness.commit", {
                "round_no": round_no, "harness_id": new_h.harness_id,
                "parent_id": h.harness_id, "capsule": best.capsule_name,
                "payload": new_h.to_dict(), "diff": diff(h, new_h),
                "blast": best.blast, "reward": best.reward,
                "baseline_reward": base_reward,
                "gain": round(best.reward - base_reward, 4),
                "advantage": round(best.advantage, 4),
            }, actor="evolution").seq)
        if self.store is not None:
            self.store.save_snapshot(new_h.harness_id, new_h.to_dict(),
                                     parent_id=h.harness_id, accepted=True,
                                     round_no=round_no)
        return rep

    # ── 多轮 ──────────────────────────────────────────────────────
    def run(self, h: Harness, rounds: int = 3) -> Tuple[Harness, List[RoundReport]]:
        """跑多轮进化。返回 (最终 harness, 每轮报告)。

        ⛔ 回滚语义：某轮没选出候选 ⇒ harness 保持不变（指针不前进），
        而不是"部分应用"。
        """
        reports: List[RoundReport] = []
        cur = h
        for i in range(rounds):
            rep = self.run_round(cur, round_no=i + 1)
            reports.append(rep)
            if rep.committed and self.store is not None:
                snap = [s for s in self.store.snapshots()
                        if s["harness_id"] == rep.committed]
                if snap:
                    from .genome import Harness as _H
                    cur = _H.from_dict(snap[-1]["payload"])
        return cur, reports
