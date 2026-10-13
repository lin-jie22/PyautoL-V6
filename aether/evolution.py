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

  4. **跨环境泛化闸**（`generalization.py`）：非回归闸只防"变坏"，
     防不了"假变好"——在单一判据集上做选择，最优策略会滑向讨好判据。
     所以另开一个**从不参与选择**的 held-out 环境，只在最后裁决一次：
     dev 涨而 held-out 不涨 ⇒ 判 overfit，不提交。

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
    #: 泛化闸裁决（只有被选为 best 的候选才会有值）
    generalization: Dict[str, Any] = field(default_factory=dict)
    #: 发展集**按难度层**的增益。⛔ 泛化闸拿它做同层对照 —— 缺了它，
    #: "能力边界"会被误判成"挑食"（见 generalization.probe 的自指死锁说明）。
    dev_by_difficulty: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"capsule": self.capsule_name, "harness_id": self.harness_id,
                "gate_passed": self.gate_passed,
                "gate_regressions": self.gate_regressions,
                "reward": round(self.reward, 4),
                "advantage": round(self.advantage, 4),
                "accepted": self.accepted, "reason": self.reason,
                "blast": self.blast, "n_verdicts": self.n_verdicts,
                "dev_by_difficulty": {k: round(v, 4)
                                      for k, v in self.dev_by_difficulty.items()},
                "generalization": self.generalization}


@dataclass
class RoundReport:
    round_no: int
    weakness: Optional[Weakness] = None
    baseline_reward: float = 0.0
    candidates: List[Candidate] = field(default_factory=list)
    committed: Optional[str] = None
    revert_reason: str = ""
    ledger_seqs: List[int] = field(default_factory=list)
    #: 本轮 best 候选的泛化裁决（None = 没跑到这一步）
    generalization: Optional[Dict[str, Any]] = None
    #: 提交后的**反事实层归因**（None = 没提交 / 未启用）
    layer_credit: Optional[Dict[str, Any]] = None

    @property
    def improved(self) -> bool:
        return self.committed is not None

    def to_dict(self) -> Dict[str, Any]:
        return {"round_no": self.round_no,
                "weakness": self.weakness.to_dict() if self.weakness else None,
                "baseline_reward": round(self.baseline_reward, 4),
                "candidates": [c.to_dict() for c in self.candidates],
                "committed": self.committed, "revert_reason": self.revert_reason,
                "ledger_seqs": self.ledger_seqs,
                "generalization": self.generalization,
                "layer_credit": self.layer_credit}


@dataclass
class EvolutionConfig:
    """进化超参。**每个都有理由**，不是随手填的。"""
    group_size: int = 3                 # GRPO 的 G：组内相对 baseline 需要 >=2
    min_reward_gain: float = 0.01       # 能力地板：不显著 ⇒ 不提交
    min_advantage: float = 0.0          # 组内优势为正才考虑
    max_size_growth_ratio: float = 0.5  # 产物增殖闸：体积涨 50% 以上需更强增益
    require_human_for_protected: bool = True   # L0 触碰 ⇒ 停等人工
    dev_pass_threshold: float = 0.0     # 发展集 reward 阈值（0=全部计入）
    #: 提交后做一次**反事实层归因**（哪一层带来了这次提升）。
    attribute_layers: bool = True
    #: 归因只用前 N 个发展任务（消融要跑 5 层 × N 次，成本要可控）
    attribution_tasks: int = 3


class EvolutionLoop:
    """Self-Harness 进化循环。

    ⛔ 依赖注入（runner / proposer / gate / store）：本类不做 I/O 决策，
    于是**机制正确性**可以被确定性测试（V5 纪律：先证明机制跑得通）。
    """

    def __init__(self, ledger: Ledger, gate: NonRegressionGate,
                 dev_suite: List[FrozenTask], runner: Runner,
                 proposer: Any, store: Optional[Store] = None,
                 caliber: Optional[Dict[str, Any]] = None,
                 config: Optional[EvolutionConfig] = None,
                 heldout: Optional[Any] = None,
                 dev_difficulty: Optional[Dict[str, str]] = None,
                 on_round_end: Optional[Callable[[Any], None]] = None):
        self.ledger = ledger
        self.gate = gate
        self.dev_suite = dev_suite
        self.runner = runner
        self.proposer = proposer
        self.store = store
        self.caliber = caliber or {"seed": 0}
        self.cfg = config or EvolutionConfig()
        #: 跨环境泛化闸（可选）。None ⇒ 不启用（保持旧行为）。
        #: ⛔ 它**只**在 best 选出之后跑，绝不参与候选之间的比较。
        self.heldout = heldout
        #: 发展集的难度标签（task_id → easy/hard）。空 ⇒ 不做同层对照
        #: （泛化闸届时 fail-closed，见 generalization.probe）。
        self.dev_difficulty: Dict[str, str] = dict(dev_difficulty or {})
        #: 每轮结束的回调（供调用方把**本轮经验**写进记忆等外部系统）。
        #: ⛔ 必须在**循环内**触发：放在 loop.run() 之后写，这一轮的下一轮
        #:    拿不到它 ⇒ 经验对因果链的影响恒为 0（V5：有机制 ≠ 被走到）。
        self.on_round_end = on_round_end
        #: 内存态 run 账（**不依赖 store**）：机制正确性不该依赖外部存储存在。
        #: store 只是它的持久化投影。
        self._runs: List[RunRow] = []
        #: ⛔ run_id 的**单调锚**：起点取账本当前 seq，之后每次执行 +1。
        #:    2026-10-13 实测事故：run_id 曾由 (harness, task) 拼出 ⇒ 同一
        #:    组合的第二次执行会 `INSERT OR REPLACE` 掉第一次 ⇒ 账本 495 条
        #:    执行、投影只剩 51 条。锚在账本 seq 上 ⇒ 跨运行也不重置。
        self._run_seq: int = int(getattr(self.ledger, "seq", 0) or 0)

    def next_run_id(self, h, task_id: str, tag: str = "") -> str:
        """生成**全局唯一**的 run_id（唯一事实源：只有这里产它）。"""
        self._run_seq += 1
        return f"{tag}{h.harness_id[:8]}:{task_id}#{self._run_seq}"

    def register_root(self, h) -> None:
        """把**初始 harness** 记进账本，再落投影。

        ⛔ 2026-10-13 实测：CLI 原先直接 `store.save_snapshot(h0)`，绕过账本
        ⇒ 初始 harness 只存在于库里 ⇒ 从账本重建会**丢掉它**（实测 7 vs 6）。
        宪法第 1 条"任何想成为事实的东西必须先 append"被自己的代码违反了。
        """
        self.ledger.append("harness.init", {
            "harness_id": h.harness_id, "round_no": 0,
            "payload": h.to_dict(), "parent_id": "",
        }, actor="evolution")
        if self.store is not None:
            self.store.save_snapshot(h.harness_id, h.to_dict(),
                                     accepted=True, round_no=0)

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
            #: ⛔ run_id 必须唯一（见 `next_run_id`），且**同一份**写进账本
            #:    —— 两处不同一个 id ⇒ 投影无法从账本重建 ⇒ "库只是视图"
            #:    就成了无法验证的声明（2026-10-13 实测）。
            run_id = self.next_run_id(h, t.task_id, tag)
            row = RunRow(
                    run_id=run_id,
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
                "run_id": run_id,
                "harness_id": h.harness_id, "task_id": t.task_id,
                "reward": v.reward, "passed": v.passed,
                "split": "dev", "tag": tag,
                "verdict": v.to_dict(), "caliber": self.caliber,
            }, actor="evolution")
        if not vs:
            return 0.0, vs
        return sum(v.reward for v in vs) / len(vs), vs

    # ── 难度分层（发展集侧）────────────────────────────────────────
    def reward_by_difficulty(self, vs: List[Verdict]) -> Dict[str, float]:
        """把发展集的判定结果按难度层聚合（**与 held-out 同一口径**）。

        ⛔ 两个集合的难度标签都由「通用修法能否修好」执行判定
        （`repair.classify_difficulty`），所以**同层可比**。
        没有难度标签 ⇒ 返回空（泛化闸届时 fail-closed）。
        """
        if not self.dev_difficulty or len(vs) != len(self.dev_suite):
            return {}
        buckets: Dict[str, List[float]] = {}
        for t, v in zip(self.dev_suite, vs):
            lv = self.dev_difficulty.get(t.task_id)
            if not lv:
                continue
            buckets.setdefault(lv, []).append(v.reward)
        return {lv: sum(xs) / len(xs) for lv, xs in buckets.items()}

    @staticmethod
    def _level_gain(base: Dict[str, float], cand: Dict[str, float]
                    ) -> Dict[str, float]:
        return {lv: cand.get(lv, 0.0) - base.get(lv, 0.0)
                for lv in set(base) | set(cand)}

    def _pick_attribution_specs(self, h: Harness) -> List[Dict[str, Any]]:
        """挑"这次提升**真的发生了**"的任务来做归因。

        ⛔⛔ 2026-10-13 实测：归因原本固定取发展集**前 N 条** ⇒
        scheduler/stateflow 组 dev 是 3 easy / 8 hard，前 3 条落在 hard 上
        ⇒ 提交后的 harness 在这些题上 reward 恒 0 ⇒ 消融任何层 Δ 都是 0
        ⇒ 输出"L0–L4 贡献全为 0"。那不是"这些层没贡献"，是**仪器盲区**：
        在一把永远读 0 的尺子上量不出任何差值。

        ⛔ 判据不能有循环论证：不能"挑消融后会变的任务"（那等于先知道答案）。
        这里用的是**提升是否发生** —— 归因回答"这次提升来自哪一层"，
        就必须在提升真的发生的那批任务上测；全都没提升 ⇒ 退回前 N 条，
        由 `attribute_layers` 自己报"这组任务对 5 层没有判别力"。
        """
        n = max(1, int(self.cfg.attribution_tasks))
        all_specs = [t.spec for t in self.dev_suite]
        if len(all_specs) <= n:
            return all_specs
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for s in all_specs:
            try:
                r = self.runner(h, s).reward
            except Exception:  # noqa: BLE001
                r = 0.0
            scored.append((r, s))
        # ⛔ 优先取 reward > 0 的（提升真的发生了）；全 0 ⇒ 取分数最高的前 n 条
        picked = [s for r, s in scored if r > 0.0][:n]
        if not picked:
            scored.sort(key=lambda x: -x[0])
            picked = [s for _, s in scored[:n]]
        return picked

    # ── 单轮 ──────────────────────────────────────────────────────
    def run_round(self, h: Harness, round_no: int = 0) -> RoundReport:
        rep = RoundReport(round_no=round_no)

        # 1) 基线探测 —— **必须放在挖掘之前**。
        #    第一轮时库里没有任何 run ⇒ 挖不到弱点 ⇒ 循环"什么都不做"
        #    却记为成功，这是自进化系统最难发现的假成功形态。
        #    先跑一次发展集，把失败证据写进库，再谈挖掘。
        base_reward, base_vs = self.evaluate_dev(h, tag="base:")
        rep.baseline_reward = base_reward
        base_by_level = self.reward_by_difficulty(base_vs)

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
            c.dev_by_difficulty = self._level_gain(
                base_by_level, self.reward_by_difficulty(vs))
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

        # 7.5) 跨环境泛化闸 —— **在 best 选出之后**才跑，且只跑一次。
        #      ⛔ 顺序不能反：held-out 的 reward 一旦参与选 best，
        #      它就变成了第二张发展集，闸自我取消（见 generalization.py H1）。
        overfit_reason = ""
        if best is not None and self.heldout is not None:
            new_h_probe = apply_capsule(
                h, next(cp for cp in capsules if cp.name == best.capsule_name))
            pr = self.heldout.probe(
                h, new_h_probe, self.runner,
                dev_gain=best.reward - base_reward,
                # ⛔ 同层对照：没有它，"能力边界"会被误判成"挑食"，
                #    通用修法将永远过不了闸 ⇒ 进化循环永久空转。
                dev_by_difficulty=best.dev_by_difficulty or None)
            best.generalization = pr.to_dict()
            rep.generalization = pr.to_dict()
            rep.ledger_seqs.append(self.ledger.append(
                "gen.probe", {"round_no": round_no, "capsule": best.capsule_name,
                              "harness_id": best.harness_id,
                              **pr.to_dict()}, actor="gate").seq)
            if pr.blocks:
                overfit_reason = pr.reason
                best.reason = f"{pr.verdict}: {pr.reason}"
                best.accepted = False
                best = None

        rep.candidates = cands

        # 7.8) 跨轮经验记忆：把**这一轮被拒的变异**交给提议器记住。
        #      ⛔ 必须在**循环内部**调用，不能等 loop.run() 结束再补：
        #      补在结束后的话，这次运行的下一轮拿不到它；而 MemoryOS 是
        #      进程内存储 ⇒ 下次运行也拿不到 ⇒ 记忆对因果链的影响恒为 0。
        #      ⛔ 鸭子类型（`hasattr`）：提议器**同协议**可替换，没有
        #      remember 的提议器行为完全不变（不是强制依赖）。
        remember = getattr(self.proposer, "remember", None)
        if callable(remember) and rep.weakness is not None:
            for c in cands:
                remember(rep.weakness.to_dict(), c.capsule_name, c.reason)

        # 8) 提交 / 回滚 —— 两种结果都进账本
        if best is None:
            rep.revert_reason = (
                f"generalization_gate（{overfit_reason}）" if overfit_reason
                else "no_candidate_passed（闸/地板/增殖 至少一项未过）")
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
                "generalization": best.generalization,
            }, actor="evolution").seq)
        if self.store is not None:
            self.store.save_snapshot(new_h.harness_id, new_h.to_dict(),
                                     parent_id=h.harness_id, accepted=True,
                                     round_no=round_no)

        # 9) 反事实层归因 —— **提交之后**才算，且**绝不参与选择**。
        #    ⛔ 顺序与 held-out 同理：归因一旦进入 group_relative 或 best
        #    排序，它就变成了第二张发展集，进化会去讨好归因本身。
        if self.cfg.attribute_layers:
            from .attribution import attribute_layers
            try:
                specs = self._pick_attribution_specs(new_h)
                lc = attribute_layers(self.runner, new_h, specs)
                rep.layer_credit = lc.to_dict()
                self.ledger.append("attribution.layer", {
                    "round_no": round_no, "harness_id": new_h.harness_id,
                    **lc.to_dict()}, actor="evolution")
            except Exception as e:  # noqa: BLE001 —— 归因失败不得影响提交
                rep.layer_credit = {"available": False,
                                    "reason": f"{type(e).__name__}: {e}"}
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
            if self.on_round_end is not None:
                try:
                    self.on_round_end(rep)
                except Exception:  # noqa: BLE001 —— 回调失败不得中断进化
                    pass
            if rep.committed and self.store is not None:
                snap = [s for s in self.store.snapshots()
                        if s["harness_id"] == rep.committed]
                if snap:
                    from .genome import Harness as _H
                    cur = _H.from_dict(snap[-1]["payload"])
        return cur, reports
