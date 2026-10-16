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

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

#: ⛔ 老化（跨轮退化）检测与 `runtime` 轨迹记账，此前**从未**被生产链路
#:    import —— 兑现审计实测：它们只活在 demo 脚本里。这里接上主流程。
from . import checkpoint as _cp
from .aging import RoundTrace
from .genome import Capsule, Harness, apply_capsule, blast_radius, diff
from .judge import identity as _judge_identity
from .ledger import Ledger
from .nongression import FrozenTask, GateResult, NonRegressionGate
from .runtime import group_relative
from .store import RunRow, Store
from .verifier import Verdict

#: 判据集身份的**进程内缓存**：`judge.identity()` 要读 5 个模块的源码，
#: 每次裁决都算一遍是纯浪费。⛔ 缓存必须是**进程内**的 —— 跨进程缓存会在
#: 判据代码变更之后继续吐旧身份，而那正是我们要发现的事情。
_JUDGE_CACHE: Dict[str, str] = {}


def _judge_version() -> str:
    """当前判据集身份（进程内缓存一次）。

    ⛔ 它进每条 `eval.verdict`：账本要能回答"这条读数是用哪把尺子量的"。
       缺了它，改一行判据代码就会让历史与未来的读数**不可比**，
       而账本上没有任何字段能发现（见 `aether/judge.py` 的说明）。
    """
    if "v" not in _JUDGE_CACHE:
        _JUDGE_CACHE["v"] = _judge_identity()
    return _JUDGE_CACHE["v"]


Runner = Callable[[Harness, Dict[str, Any]], Verdict]

# 失败信号 → 弱点类别的映射。
#
# ⛔⛔ 纪律（2026-10-15 收口，V5「拦不住的条目不许留在名单里」的同族）：
#    **每一条都必须有真实生产者** —— 即有某段代码会真的产出这个字符串。
#    此前这里是 7 条，实测三条问题（用 `classify_error` 对 649 条真实
#    失败串 + 边界串做全量对拍）：
#      1. `no_skill` / `skill_not_found` / `tool_not_allowed` / `primitive`
#         在整个代码库里**只出现在本词表**（各 1 处）⇒ 永远命中 0 次，
#         是**假仪表**：看着有 4 条分类规则，实际只有 1 条在跑。
#      2. **真实生产者被漏掉**：`agent.require_tools` 缺工具时报的是
#         `PermissionError: 缺工具 ['read_file']`，词表里没有任何 needle
#         能对上 ⇒ L2 层（`gene_tool`）**结构上不可达**（哪怕真的缺工具，
#         也只会被归到缺技能 ⇒ 提议加技能 ⇒ 修不好）。
#      3. **宽泛词误伤**：needle `memory` 会把 `some memory error` 这类
#         任何含该词的文本误判成 `recall_miss`。
#    ⇒ 只保留有生产者的条目。`tests/contract_evolution_test.py` 的 K 组
#      做静态自证：每个 needle 必须在 `aether/` 的**非词表**位置出现过。
#
# ⛔ 关于 `recall_miss`：它**不在**本表里 —— 因为当前没有任何代码会产生
#    "召回失败"这个信号。`agent` 的 L4 通路在**链上无技能**时才走，那时
#    真正的缺口是**技能**（加技能是更根本的修法），不是召回。所以
#    `TemplateProposer` 的 `recall_miss` 分支保留为**显式接口**（供未来的
#    LLM 提议器或人工干预指定 kind），但**没有自动分类路径**。
_ERROR_TO_KIND: Tuple[Tuple[str, str], ...] = (
    # agent.require_tools 闸：缺声明工具 ⇒ 直接失败
    ("缺工具", "missing_tool"),
    # agent 解析 env 失败（配置错，不是能力缺口）⇒ 无可用变异，走 noop
    ("未知 env_id", "bad_config"),
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
    却记为成功，这是最难被发现的一种假成功。而"缺技能"至少会去**试**：
    提议器只提"链上还没有的技能"，都在链上时自然退化为 noop 哨兵
    （由 C7d/C7e 契约守住）—— 所以这个默认**不会**造成重复提议。

    ⛔ 命中顺序即优先级，且**大小写不敏感**：needle 与文本都转小写比较。
    ⛔ 只保留**有真实生产者**的条目，见 `_ERROR_TO_KIND` 上方的纪律说明。
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
    #: ── 减负轴 ──────────────────────────────────────────────
    #: 应用这个候选后**自指负载**掉了多少条（在候选 harness 上**现扫**出来的）。
    #: ⛔ 单位与 reward 不同，不参与加权求和（见 EvolutionConfig 的说明）。
    load_gain: float = 0.0
    #: 是"清理"还是"补能力"。⛔ 用结构化标记（capsule 的 origin），
    #:    不靠名字前缀匹配 —— 名字是给人看的，拿它当判据 ⇒
    #:    改一次命名约定，清理候选就悄悄走回能力轴。
    is_cleanup: bool = False

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
                "load_gain": round(self.load_gain, 4),
                "is_cleanup": self.is_cleanup,
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
    #: step 级信用分配（`runtime.assign_credit` 的 turn-level）。
    #: ⛔ 与 `layer_credit` 是**两个粒度**：层 = 5 层，步 = 轨迹上的每一步
    #:    （链上每个链项各一步）。合并成一个字段会让"哪个链项真的有用"
    #:    这个问题重新变得无从回答。
    step_credit: Optional[Dict[str, Any]] = None
    #: 本轮是否判定为「发展集已到顶」（best 为空的唯一可接受解释之一）
    saturated: bool = False
    #: 本轮是否"无事可做"（无失败证据 / 到顶）。连续发生 ⇒ 停止循环。
    idle: bool = False
    #: 饱和读数：dev 通过数 / 总数，以及 held-out 基线分（None = 未测）
    saturation: Optional[Dict[str, Any]] = None
    #: 本轮末的**全局预算读数**（None = 没装预算 = 无止损）。
    #: ⛔ 每轮都记：只在 run() 末尾记一次 ⇒ 看不到预算是怎么烧掉的。
    budget: Optional[Dict[str, Any]] = None
    #: 本轮的**并行读数**。⛔ 记的是"真的并行跑了多少"，不是"配了几个
    #: worker"：配置存在但一次没走并行分支（`parallel_batches=0`）= 空账，
    #: 与「落点要兑现」同一条纪律。
    parallel: Optional[Dict[str, Any]] = None
    #: 本轮末这一代 harness **知道什么**（陈述性知识的读数）。
    #: ⛔ 它是"学到的洞察"第一次变得可见：此前洞察住在循环的内存缓存里，
    #:    没人能回答"这一轮比上一轮多知道了什么"。
    world_facts: Optional[Dict[str, Any]] = None
    #: 本轮由"连续被拦"**晋升成事实**的条目（含门槛与键）。
    #: ⛔ 与 `world_facts` 分开：那个是"现在知道什么"，这个是"这轮新学到什么"。
    facts_promoted: List[Dict[str, Any]] = field(default_factory=list)
    #: 本轮**常设清理提案器**提了几个候选（与有没有弱点无关）。
    #: ⛔ 单独计数：没有它，"这一轮有没有在收拾自己的产出"在报告里查不到
    #:    —— 而那正是"努力分配"要被看见的那个数。
    cleanup_proposed: int = 0
    #: 本轮基线 harness 的**自指负载**（基因组侧死条目数）。-1 = 没扫出来。
    #: ⛔ 与 `load_gain` 是两件事：这是**存量**，那些是**增量**。
    #:    只看增量 ⇒ 不知道债是在减少还是在增加。
    genome_load_before: int = -1

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
                "layer_credit": self.layer_credit,
                "step_credit": self.step_credit,
                "saturated": self.saturated, "saturation": self.saturation,
                "idle": self.idle, "budget": self.budget,
                "parallel": self.parallel,
                #: 陈述性知识的读数：这一代**知道什么** + 本轮**新学到什么**
                "world_facts": self.world_facts,
                "facts_promoted": self.facts_promoted,
                #: 减负轴的两个读数：本轮提了几个清理候选 + 清理前的存量
                "cleanup_proposed": self.cleanup_proposed,
                "genome_load_before": self.genome_load_before}


@dataclass
class EvolutionConfig:
    """进化超参。**每个都有理由**，不是随手填的。"""
    group_size: int = 3                 # GRPO 的 G：组内相对 baseline 需要 >=2
    min_reward_gain: float = 0.01       # 能力地板：不显著 ⇒ 不提交
    min_advantage: float = 0.0          # 组内优势为正才考虑
    max_size_growth_ratio: float = 0.5  # 产物增殖闸：体积涨 50% 以上需更强增益
    require_human_for_protected: bool = True   # L0 触碰 ⇒ 停等人工
    # ── 减负轴（第二根轴）──────────────────────────────────────
    #: ⛔ 存在理由（努力分配）：进化循环每轮都在**评估候选**，而没有任何东西
    #:    在自己的产出上花一点力。机制根源是**判据只认增益、不认减负** ——
    #:    删掉一个死条目 reward 不变 ⇒ 按单轴判据它永远提不出来 ⇒
    #:    "通过测试但不产生增益"的机制只增不减。
    #: ⛔ **两轴不做加权求和**：reward 的单位是分、load 的单位是条，
    #:    把它们换算成同一个数 ⇒ 换算比例是编的 ⇒ 系统只是去讨好那个比例。
    #:    ⇒ 两轴各自有一道门槛，取"或"（见第 7 步）。
    min_load_gain: float = 1.0           # 减负地板：至少真少 1 条死条目
    #: 常设清理提案器。⛔ **默认开** —— 默认关的开关是假仪表（README 第 83 条）：
    #:    看着有机制，实际这条路从没被走到。
    cleanup_enabled: bool = True
    #: 每轮最多提几个清理候选。⛔ 默认 1：清理同样走**最小编辑原则**，
    #:    一次只清一条 ⇒ 归因清楚、回滚便宜（一次清 5 条 ⇒ 出事不知道是谁）。
    cleanup_group_size: int = 1
    # ── 源码侧清理 pass（第二根轴的**另一半**）────────────────────
    #: ⛔ 与上面的 `cleanup_*` 是**两根不同的轴**，别混为一谈：
    #:    · `cleanup_*`（基因组侧）—— 每轮跑，产出 `Capsule`，改 **harness**
    #:      ⇒ 可回滚（纯数据）、可参与 `best` 选择。
    #:    · `source_*`（源码侧）—— **每次运行**跑一次，产出 `CleanupProposal`
    #:      ⇒ 改的是**源码**，不可逆 ⇒ 只提案、不过 harness 那条选择链。
    #: ⛔ **默认开**（与 `cleanup_enabled` 同理由）：默认关的开关是假仪表。
    source_cleanup_enabled: bool = True
    #: ⛔ **预算 = 每次运行最多对拍几条提案**。默认 **0**（循环只提议、
    #:    记账；真裁决由 CLI 给）。
    #:
    #: ⛔⛔ **放开试过了，结论是「不划算」**（2026-10-16 单变量实验）：
    #:    只改这一个默认值 0 → 1 ⇒ 全量回归由
    #:        `pass=50 fail=0 timeout=1`
    #:    退化为
    #:        `pass=46 fail=1 timeout=4`
    #:    （`contract_evolution_test` ×2 / `contract_ops_test` /
    #:     `contract_streaming_test` 集体超 600s）。
    #:    ⛔ 根因不是"一次 160s 太慢"，而是**每个调 `run()` 的进程都各跑
    #:       一遍对拍** ⇒ 成本 = 单次耗时 × 调用次数。契约里 `run()` 被调
    #:       多次 ⇒ 分钟级被放大成十分钟级。
    #:    ⇒ 于是 **r40 那个「循环 = 提议器 / CLI = 验证器」的切分被实测坐实**：
    #:      循环不该承担分钟级验证；验证是一次**单独、明码标价**的动作。
    #:    ⇒ 想开就开：把默认值改成 1，**但必须同时**把受影响契约的 timeout
    #:      提上去（或让它们少调几次 `run()`）。守卫已就位 ⇒ 不会再失控。
    #:
    #: ⛔ **「看得见的关闭」**（这才是本轮真正修掉的东西）：预算耗尽 /
    #:    被关掉的条目**必须**在 `cleanup.source_pass` 里给出
    #:    `unverified_reasons` 分项计数，让
    #:        「没有可证明的提案」
    #:    与  「有提案，但预算为 0 ⇒ 恒红」
    #:    **可分辨**。此前它只是在事件里永远记 `n_probed=0`，
    #:    外部看起来与"没有问题"完全一样 —— 那才是真正的伤。
    max_source_probes: int = 0
    dev_pass_threshold: float = 0.0     # 发展集 reward 阈值（0=全部计入）
    #: 提交后做一次**反事实层归因**（哪一层带来了这次提升）。
    attribute_layers: bool = True
    #: 归因只用前 N 个发展任务（消融要跑 5 层 × N 次，成本要可控）
    attribution_tasks: int = 3
    #: 死路冷却：同一 (候选, 环境对) **连续**被泛化闸拦下这么多次后，
    #: 本轮不再为它花探测预算（实测：某候选 15 次探测 / 15 次 overfit /
    #: 0 次通过，占掉 55% 预算）。⛔ 不是永久封死 —— 见下。
    deadend_streak: int = 3
    #: 出口：冷却后每逢这个轮次**允许试探一次**（否则环境换了、能力涨了
    #: 也永远不再试 ⇒ 与「难度分层自指死锁」同形态的永久封死）。
    deadend_probation_every: int = 5
    #: 无事可做即停（到顶 / 无失败证据 ⇒ 后续轮次是空转，不是尝试）
    stop_on_saturation: bool = True
    #: 连续这么多轮"无事可做"就停。⛔ 用**连续**而不是累计：偶尔一轮
    #: 没事做（比如刚提交完、新 harness 还没暴露失败）是正常的。
    stop_after_idle_rounds: int = 2
    #: 并发评估的线程数。**0 / 1 = 串行**（默认，与历史行为完全一致）。
    #: ⛔ 为什么默认不开：并行不改变结果（实测逐条一致），但把"结果可复现"
    #:    押在 runner 的线程安全性上 ⇒ 默认串行是**保守且可比**的选择，
    #:    要加速请显式开（`evolve --parallel N`）。
    #: ⛔ 并行**只包住 runner 计算**：run_id 分配与账本写入一律串行
    #:    （2026-10-14 实测：`_run_seq += 1` 在多线程下不保证原子；
    #:    账本 append 有 flock 安全，但**事件顺序**必须保持确定性 ⇒
    #:    顺序漂移会让账本不可复现，而账本是唯一事实源）。
    parallel_workers: int = 0


@dataclass
class RunBudget:
    """跨轮**全局**预算：进化循环的止损线。

    ⛔ 为什么需要它：`interop.Budget` 只管**单次** UCI 调用（超时 / 输出
    字节），而进化循环此前**没有任何**总量约束 —— 轮数、挂钟、评估次数
    全靠调用方传参，跑多久花多少完全不可控。真接上 LLM proposer 后，
    一次 `propose` 就是一次付费调用，没有止损等于把预算交给循环自己。

    ⛔ **默认 `None` = 不限**：全 None 时行为与加预算之前**完全一致**
    （历史账本可比）。这是一条刻意的选择：改默认口径会让旧基线不可比
    （V5 纪律：改口径必须单独跑一轮，不可与修复混在一张表里）。
    ⇒ 生产接真 LLM 时**必须显式设**，见 `budget_limited`。

    ⛔ 四个维度，任一耗尽即停：
      · `max_rounds`        —— 轮数
      · `max_seconds`       —— 挂钟（**轮末**检查）
      · `max_propose_calls` —— 提议器调用次数（真 LLM 时 = 付费调用数）
      · `max_eval_runs`     —— 发展集评估任务次数（= runner 跑了几遍）

    ⛔ 耗尽 ⇒ **停并记账**，不是"静默截断"：静默少跑几轮会把"预算用完"
    伪装成"跑完了但没提升"，与「空转记成努力过」同形态。
    """

    max_rounds: Optional[int] = None
    max_seconds: Optional[float] = None
    max_propose_calls: Optional[int] = None
    max_eval_runs: Optional[int] = None

    #: ── 计量（只读，由 `spend` 累加）────────────────────────────
    rounds: int = 0
    seconds: float = 0.0
    propose_calls: int = 0
    eval_runs: int = 0

    def spend(self, *, rounds: int = 0, propose_calls: int = 0,
              eval_runs: int = 0, seconds: Optional[float] = None) -> None:
        self.rounds += rounds
        self.propose_calls += propose_calls
        self.eval_runs += eval_runs
        if seconds is not None:
            self.seconds = seconds

    def exhausted(self) -> Optional[str]:
        """返回耗尽原因（如 `rounds>=3`），未耗尽返回 None。"""
        for name, cap, used in (
                ("rounds", self.max_rounds, self.rounds),
                ("seconds", self.max_seconds, self.seconds),
                ("propose_calls", self.max_propose_calls, self.propose_calls),
                ("eval_runs", self.max_eval_runs, self.eval_runs)):
            if cap is not None and used >= cap:
                return f"{name}:{used}>={cap}"
        return None

    @property
    def limited(self) -> bool:
        """是否真的装了预算（False = 无止损，调用方应知情）。"""
        return any(x is not None for x in (self.max_rounds, self.max_seconds,
                                           self.max_propose_calls,
                                           self.max_eval_runs))

    def to_dict(self) -> Dict[str, Any]:
        return {"limited": self.limited,
                "max": {"rounds": self.max_rounds, "seconds": self.max_seconds,
                        "propose_calls": self.max_propose_calls,
                        "eval_runs": self.max_eval_runs},
                "used": {"rounds": self.rounds,
                         "seconds": round(self.seconds, 3),
                         "propose_calls": self.propose_calls,
                         "eval_runs": self.eval_runs},
                "exhausted": self.exhausted()}


#: ⛔ **可重入守卫的环境变量**（跨进程）。
#:    对拍（`srcmut.probe`）是**起子进程**跑冻结契约的；而契约里有若干份
#:    自己会 `EvolutionLoop.run(...)` ⇒ run() → 清理 pass → probe → 子进程
#:    → run() → … ⇒ **无限递归**。类旗子挡不住子进程，只能靠环境变量
#:    （`contract_runner` 复制 `os.environ` ⇒ 会被继承）。
_GUARD_ENV = "V6_SOURCE_CLEANUP_ACTIVE"


class EvolutionLoop:
    """Self-Harness 进化循环。

    ⛔ 依赖注入（runner / proposer / gate / store）：本类不做 I/O 决策，
    于是**机制正确性**可以被确定性测试（V5 纪律：先证明机制跑得通）。

    ⛔⛔ **这两个类属性不是装饰，是兜底**：对象可能经 `__new__` / pickle
        构造，**拿不到 `__init__` 里的赋值** ⇒ 那时访问 `self._deadend_cache`
        会直接 AttributeError（2026-10-13 实测：J 组正是用 `__new__`
        手塞属性来测 `_deadend_map` 的，加实例属性后它当场炸了）。
        与 V5 的 `_exp_store = None` 完全同形态：新属性一律给类级兜底。
    """
    _deadend_cache: Optional[Dict[str, Any]] = None
    _deadend_stats: Optional[Dict[str, Any]] = None
    #: ⛔ 类级兜底（与 `_deadend_cache` 同形态）：对象可能经 `__new__` /
    #:    pickle 构造，拿不到 `__init__` 的赋值 ⇒ 访问 `self.budget` 会
    #:    AttributeError。默认 None = **不限**（保持旧行为与旧基线可比）。
    budget: Optional[RunBudget] = None
    #: ⛔ 类级兜底（与 `budget` 同形态）：对象可能经 `__new__` / pickle
    #:    构造，拿不到 `__init__` 的赋值 ⇒ 访问 `self.checkpoint_path`
    #:    会 AttributeError。默认 None = 不写 checkpoint（保持旧行为）。
    checkpoint_path: Optional[str] = None
    last_checkpoint: Optional[str] = None
    #: checkpoint 里保留的**最近轮次摘要条数**。⛔ 有界窗口：不能把每轮
    #:    报告全塞进去（轮数越多文件越大，而它是每轮都重写的文件）。
    #:    ⛔ 计数与展示分离：`round_no` 是**真实总数**，这个窗口只影响
    #:    `done_rounds` 的展示条数，绝不能反过来用它推算完成到第几轮。
    _CP_KEEP_ROUNDS: int = 10
    #: ⛔ **对拍 runner 工厂的注入点**（默认 None ⇒ 用 `srcmut.contract_runner`）。
    #:
    #: ⛔⛔ **为什么不放在 `EvolutionConfig` 里**：配置对象会被
    #:    `asdict()` → `fingerprint()` JSON 化（见 `_cfg_fingerprint`）。
    #:    塞一个函数进去 ⇒ JSON 化抛 TypeError ⇒ 被 `except` 兜住
    #:    ⇒ **指纹静默退回空串** ⇒ checkpoint 续跑的那道一致性校验
    #:    从此恒过（假仪表，第 5 条纪律的又一个落点）。
    #:    ⇒ 机制（可注入）与事实源（可序列化）必须分开存放。
    #:
    #: ⛔ 存在的唯一理由是**可测**：不注入的话，契约要么跑 160 秒的
    #:    真实对拍，要么只能做静态检查 —— 而静态检查证明不了
    #:    「预算 > 0 时**真的**对拍了」（第 5 条：有机制 ≠ 这条路被走到）。
    _probe_runner_factory: Optional[Callable[..., Callable[[Any],
                                                           Dict[str, Any]]]] = None
    #: ⛔ **可重入守卫**（类级，与 `_deadend_cache` 同形态）。
    #:    `_source_cleanup_pass` 由 `run()` 调用，而它对拍跑的契约里又有调
    #:    `run()` 的 ⇒ 没有这面旗子就是无限递归。详见该方法 docstring。
    _in_source_cleanup: bool = False
    #: ⛔ **进程级一次性闩锁**：对拍的前提是"源码未变"，而同一进程内源码
    #:    不会变 ⇒ **第二次及以后重跑对拍零信息量**，只是把分钟级成本乘上
    #:    调用次数（实测：契约里 run() 被调多次 ⇒ 4 份契约集体超时）。
    #:    ⛔ 与守卫是**两件事**：守卫挡"嵌套"，闩锁挡"重复"。
    _probed_once: bool = False

    def __init__(self, ledger: Ledger, gate: NonRegressionGate,
                 dev_suite: List[FrozenTask], runner: Runner,
                 proposer: Any, store: Optional[Store] = None,
                 caliber: Optional[Dict[str, Any]] = None,
                 config: Optional[EvolutionConfig] = None,
                 heldout: Optional[Any] = None,
                 dev_difficulty: Optional[Dict[str, str]] = None,
                 on_round_end: Optional[Callable[[Any], None]] = None,
                 aging_probes: Optional[List[FrozenTask]] = None,
                 budget: Optional[RunBudget] = None,
                 checkpoint_path: Optional[str] = None,
                 cleanup: Optional[Any] = None):
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
        #: 老化检测的**冻结探针集**。⛔ 默认就是发展集，但**必须能显式指定**：
        #:    老化看的是"同一批题跨轮怎么变"，探针集一换，Δ 就是换尺子。
        self.aging_probes: List[FrozenTask] = list(
            aging_probes) if aging_probes else list(dev_suite)
        #: 本轮发生的"记忆事件"读数（巩固 / 剪枝条数），由外部在轮末写入。
        #: ⛔ 不在这里猜 —— 记忆不在 loop 手里，猜出来的事件会让老化
        #:    信号指向一个不存在的因（V5：记账方式变了 ⇒ Δ 要拆账）。
        self.aging_events: Dict[str, Any] = {}
        #: 全局预算（None = 不限，行为与加预算前一致）。
        self.budget = budget
        #: 断点续跑：checkpoint 落盘路径（None = 不写 ⇒ 无法续跑）。
        #: ⛔ 默认 None 而非"默认写到 data/"：写文件是**副作用**，不该由
        #:    库层替调用方决定往哪儿写（CLI 侧显式开 `--checkpoint`）。
        self.checkpoint_path = checkpoint_path
        #: 常设清理提案器（**不看 weakness**，每轮都提"收拾自己产出"的变异）。
        #: ⛔⛔ 没有注入且配置允许时**自己造一个**：不构造 ⇒ 这条通路一个候选
        #:    都提不出来 ⇒ `min_load_gain` 那道闸永远不触发 ⇒ 看着有判据，
        #:    实际从不裁决（与"默认关的记账开关"同形态的假仪表）。
        #:    ⇒ 想关掉请显式传 `cleanup=None` + `cfg.cleanup_enabled=False`，
        #:      那是一眼看得见的关闭，不是"没接线所以当它不存在"。
        self.cleanup = cleanup
        if self.cleanup is None and self.cfg.cleanup_enabled:
            #: ⛔ 延迟 import：`providers → cleanup → agent → memory/environment`
        #:    这条链不轻，而它只在**真的要建清理提案器**时才需要。
            from .providers import CleanupProposer
            self.cleanup = CleanupProposer()
        #: 最近一次成功落盘的 checkpoint 路径（读数，供 CLI/观测侧显示）。
        #: ⛔ 类级兜底见上方 `checkpoint_path` 的声明处。
        self.last_checkpoint: Optional[str] = None
        #: 跨轮纵向读数（老化检测的事实源，可投影到账本）。
        self.aging_traces: List[RoundTrace] = []
        #: 内存态 run 账（**不依赖 store**）：机制正确性不该依赖外部存储存在。
        #: store 只是它的持久化投影。
        self._runs: List[RunRow] = []
        #: ⛔ run_id 的**单调锚**：起点取账本当前 seq，之后每次执行 +1。
        #:    2026-10-13 实测事故：run_id 曾由 (harness, task) 拼出 ⇒ 同一
        #:    组合的第二次执行会 `INSERT OR REPLACE` 掉第一次 ⇒ 账本 495 条
        #:    执行、投影只剩 51 条。锚在账本 seq 上 ⇒ 跨运行也不重置。
        self._run_seq: int = int(getattr(self.ledger, "seq", 0) or 0)
        #: ⛔ 并行**可达性**计数：不记这两个数，"配了 parallel_workers=4"和
        #:    "真的并行跑了"在外部完全等价 —— 落点要兑现，不只是存在
        #:    （`grep` 到参数只证明写过，不证明被调用过）。
        self.parallel_batches: int = 0     # 走了并行分支的批次次数
        self.parallel_runs: int = 0        # 并行分支里跑掉的 runner 次数
        #: 死路账的**增量缓存**。⛔ 它是"能从账本重建出来的派生量"，不是事实
        #: 源 —— 任何时候丢了都要能靠全量重扫拿回同一个值（有测试盯这点）。
        self._deadend_cache: Optional[Dict[str, Any]] = None
        #: ⛔ 增量**必须可观测**：不记这两个数，"真增量"与"每轮全扫"在外部
        #:    完全等价（假仪表 = V5 同款病）。`invalidated` 高说明缓存
        #:    一直在失效（比如有人每轮 rebuild 账本）⇒ 增量形同虚设。
        self._deadend_stats: Dict[str, Any] = {
            "full": 0, "incremental": 0, "invalidated": 0, "scanned": 0,
            "reasons": {"shrunk": 0, "chain_broken": 0,
                        "no_new_but_touched": 0},
        }

    # ── 老化（跨轮退化）读数 ─────────────────────────────────────
    def _record_aging(self, rep: RoundReport, h: Harness) -> Optional[Dict[str, Any]]:
        """在**冻结探针集**上跑一遍，把本轮纵向读数写进账本。

        ⛔ 为什么另跑一遍而不是复用 `evaluate_dev` 的结果：
           老化要的是"同一批题、同一个 harness、跨轮可比的**逐条**读数"。
           复用会掺进候选评估的中间态 ⇒ 尺子不统一。
        """
        from .aging import ProbeResult, RoundTrace
        #: ⛔ 与 `evaluate_dev` 走**同一个**并行点（`_eval_many`）：两处各写
        #:    一份并行 ⇒ 一边串行一边并行 ⇒ 老化读数和评估读数不可比。
        #:    ⛔ 探针必须**按输入序**归位：老化要的是"同一批题跨轮逐条可比"，
        #:    顺序错位 ⇒ 跨轮 Δ 变成噪声。
        vs = self._eval_many(h, self.aging_probes, prefix="aging/")
        probes = [ProbeResult(task_id=t.task_id, reward=float(v.reward))
                  for t, v in zip(self.aging_probes, vs)]

        trace = RoundTrace(
            round_no=rep.round_no, harness_id=h.harness_id,
            probes=tuple(probes),
            skill_count=len(getattr(h, "skills", {}) or {}),
            consolidated=int(self.aging_events.get("consolidated", 0) or 0),
            pruned=int(self.aging_events.get("pruned", 0) or 0),
            #: ⛔ 消费 CLI 写下的 `prune_candidates`：不接这一步，CLI 那边
            #:    算了也白算（写进 dict 没人读 = 死字段）。
            prune_candidates=int(
                self.aging_events.get("prune_candidates", 0) or 0),
            caliber=dict(self.caliber or {}))
        self.aging_traces.append(trace)
        try:
            self.ledger.append("aging.trace", trace.to_dict(),
                               actor="evolution")
        except Exception:                                    # noqa: BLE001
            pass
        #: 每轮读一次事件后清空 ⇒ 事件是**这一轮**的，不跨轮累积
        self.aging_events = {}
        return trace.to_dict()

    def aging_report(self) -> Dict[str, Any]:
        """整条寿命线的裁决（跨轮退化检测）。"""
        from .aging import detect_aging
        return detect_aging(self.aging_traces).to_dict()

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
    def _eval_many(self, h: Harness, tasks: List[Any],
                   prefix: str = "dev/") -> List[Verdict]:
        """跑一批 runner 调用。**唯一**的并行点。

        ⛔⛔ 并行**只包住 runner 计算**，`run_id` 分配 / 落库 / 账本 append
        一律留在外面串行做。三条理由，每条都是实测：
          · `_run_seq += 1` 不是原子操作 ⇒ 并发下 run_id 可能重复 ⇒ 唯一
            run_id 这条不变量当场失效（2026-10-13 已因 run_id 冲突丢过
            444 条执行记录，同形态的坑不踩第二次）；
          · 账本 append 有 `flock`（安全），但**事件顺序**会随完成顺序漂移
            ⇒ 账本不再逐条可复现，而"账本是唯一事实源"正是靠可复现撑着；
          · `budget.spend` / `_runs.append` 都不是线程安全的累加。

        ⛔ 结果**按输入序归位**（`ThreadPoolExecutor.map` 保序）：顺序变了
        ⇒ `zip(dev_suite, vs)` 错位 ⇒ reward 记到别人头上 —— 这比慢更糟。
        """
        from .verifier import error_verdict

        def _one(t: Any) -> Verdict:
            try:
                return self.runner(h, t.spec)
            except Exception as e:  # noqa: BLE001 —— 记账，不吞
                return error_verdict(f"{prefix}{t.task_id}", e)

        workers = int(getattr(self.cfg, "parallel_workers", 0) or 0)
        if workers > 1 and len(tasks) > 1:
            from concurrent.futures import ThreadPoolExecutor
            self.parallel_batches += 1
            self.parallel_runs += len(tasks)
            with ThreadPoolExecutor(max_workers=min(workers, len(tasks))) as ex:
                return list(ex.map(_one, tasks))
        return [_one(t) for t in tasks]

    def evaluate_dev(self, h: Harness, tag: str = "") -> Tuple[float, List[Verdict]]:
        """在**发展集**上评估（与非回归闸用的冻结套件**不是**同一批任务）。

        ⛔ 两个方向必须分开：冻结套件回答"有没有变坏"，发展集回答
        "有没有变好"。混在一张表里 ⇒ 用新任务的收益掩盖旧任务的回归。
        """
        #: ⛔ 计算可以并行，下面的 run_id / 落库 / 账本**必须**串行。
        vs: List[Verdict] = self._eval_many(h, self.dev_suite, prefix="dev/")
        for t, v in zip(self.dev_suite, vs):
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
            #: ⛔⛔ 判据集身份**必须进裁决**。此前账本记下了"被判的东西"
            #:    （环境指纹 `env_fingerprint`、harness 内容寻址 id），
            #:    却没记下"判它的那段代码" ⇒ 改一行 `verify_source` 的判据，
            #:    历史读数与未来读数就**不可比**，而账本上没有任何字段能发现。
            #:    而"零新红""非回归""发展集涨了"全部建立在这把尺子上。
            #:    ⇒ 每条裁决带上 `judge_version`；跨版本比较由
            #:      `judge.incomparable` 判"不可比"并**拒绝**。
            self.ledger.append("eval.verdict", {
                "run_id": run_id,
                "harness_id": h.harness_id, "task_id": t.task_id,
                "reward": v.reward, "passed": v.passed,
                "split": "dev", "tag": tag,
                "verdict": v.to_dict(), "caliber": self.caliber,
                "judge_version": _judge_version(),
            }, actor="evolution")
        #: ⛔ 预算计量挂在**唯一评估入口**上：`evaluate_dev` 是所有发展集
        #:    评估的咽喉。逐个调用点各记一次 ⇒ 迟早漏一处 ⇒ 预算少算
        #:    ⇒ 止损形同虚设（V5："有机制 ≠ 这条路被走到"）。
        if self.budget is not None:
            self.budget.spend(eval_runs=len(vs))
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

    @staticmethod
    def _runs_from_verdicts(vs: List[Verdict], h: Harness) -> List[RunRow]:
        """把本轮发展集判定包装成 RunRow，供 `mine_weaknesses` 使用。

        ⛔ 为什么不再翻 store：见 `run_round` 第 2 步的说明。这里 Only 用
        本轮判定 ⇒ "弱点"不可能来自上一次运行。
        """
        from .store import RunRow
        out: List[RunRow] = []
        for i, v in enumerate(vs):
            topic = str(v.evidence.get("topic") or
                        v.evidence.get("env") or "")
            out.append(RunRow(
                run_id=f"probe:{h.harness_id}:{i}",
                harness_id=h.harness_id,
                task_id=str(v.evidence.get("task_id") or topic or f"t{i}"),
                reward=(1.0 if v.passed else 0.0), passed=bool(v.passed),
                gate_ok=True,
                payload={"error": v.error or "", "topic": topic,
                         "evidence": v.evidence}))
        return out

    # ── 死路账：同一候选在同一环境对上被拦了多少次 ──────────────────
    def _deadend_accum(self, ev: Any, streak: Dict[str, int]) -> None:
        """把**一条**事件累加进死路账（增量与全量共用同一份累加逻辑）。

        ⛔ 只 factored 出"累加"，不 factored 出"读"：读只有 `iter_events`
        一个实现，两条路径（全量 / 增量）都必须走它 —— 多一份读法就会
        在坏行、编码、偏移上漂移，而漂移是静默的。

        ⛔ 累加的是**连续**计数（streak），不是累计总数：
            · `generalize` ⇒ 归零（成功一次就重新给机会）；
            · 其它 verdict ⇒ +1。
          `streak` 是"连续"语义的**充分统计量**：正序流式扫一遍就能维护，
          不需要重放历史，也不需要额外记"曾否成功过"的集合 —— 所以增量
          路径与全量路径在数学上严格等价（不存在"增量算出来的值和全量
          不一样"这条漂移路径）。
        """
        if ev.kind != "gen.probe":
            return
        p = ev.payload or {}
        key = f"{p.get('capsule', '')}|{p.get('env_pair', 'unknown')}"
        #: ⛔ 严格匹配 `"generalize"`（不大小写折叠、不把 `partial` 算成功）：
        #:    这是改造前就有的口径，改判据属于**另一件事**，要有自己的交叉
        #:    表。当前口径下 `partial`/`GENERALIZE` 都计入"被拦"（+1）。
        if str(p.get("verdict")) == "generalize":
            streak[key] = 0
        else:
            streak[key] = streak.get(key, 0) + 1

    def _ensure_deadend_stats(self) -> None:
        """惰性初始化统计。**幂等**（重复调用不重置计数）。

        ⛔ 必须有：`__new__` / pickle 构造的对象拿不到 `__init__` 的赋值，
            类级兜底是 `None`。不加这一步，`_deadend_map` 会在那条路径上
            抛 AttributeError（实测：J 组就是这么构造的）。
        """
        if self._deadend_stats is None:
            self._deadend_stats = {
                "full": 0, "incremental": 0, "invalidated": 0, "scanned": 0,
                "reasons": {"shrunk": 0, "chain_broken": 0,
                            "no_new_but_touched": 0},
            }

    def _deadend_full(self) -> Dict[str, Any]:
        """全量重扫（增量失效时的兜底，也是第一次调用的路径）。

        ⛔ `head_hash` 取**最后一条任意 kind** 的事件，不是最后一条
        `gen.probe`：增量校验要比的是"新增部分的第一条"的 `prev_hash`，
        而新增的第一条可以是任何 kind。记成 gen.probe 的会让校验在
        「探测之后又写了别的事件」时误判成"账本被改过" ⇒ 每轮全扫。
        """
        streak: Dict[str, int] = {}
        self._ensure_deadend_stats()
        prog: Dict[str, Any] = {"offset": 0}
        head = self._genesis_hash()
        n = 0
        for ev in self.ledger.iter_events(progress=prog):
            self._deadend_accum(ev, streak)
            head = ev.hash
            n += 1
        self._deadend_stats["full"] += 1
        self._deadend_stats["scanned"] += n
        return {"offset": int(prog["offset"]), "head_hash": head,
                "streak": streak, "fp": self._deadend_fp()}

    @staticmethod
    def _genesis_hash() -> str:
        #: ⛔ 用 `Ledger.GENESIS_HASH`（类属性），不抄一份 `"0"*64`：
        #:    抄了就是第二个事实源，哪天创世值变了这里静默失配。
        return Ledger.GENESIS_HASH

    def _deadend_fp(self) -> Tuple[int, int]:
        """账本文件指纹 (大小, mtime_ns) —— 用来发现"被写过但没新增"。"""
        try:
            st = self.ledger.path.stat()
            return (int(st.st_size), int(st.st_mtime_ns))
        except OSError:
            return (0, 0)

    # ── 陈述性知识（world_facts）：可继承、可选、可回滚 ───────────
    def inherited_facts(self, h: Harness) -> Dict[str, Any]:
        """这一代 harness **继承到的**世界事实（读数，不是判据）。

        ⛔ 与 `_deadend_map()` 职责**不同**，所以不合并：
           · `_deadend_map()` 是**从账本重建的派生量**，管"本轮少跑几次"；
           · `world_facts` 是**基因组里的陈述性知识**，管"跨代继承什么"。
           合并会让"读到旧账"与"学到新知识"混成一件事。
        """
        from . import world_facts as W
        return W.summarize(h) if h is not None else {"n": 0, "by_scope": {},
                                                     "keys": {}}

    def _facts_deadend_streak(self, h: Harness, capsule: str,
                              env_pair: str) -> int:
        """基因组里是否已记着"这个候选在这套环境对上是死路" ⇒ 返回观测数。

        ⛔ 继承的知识也要**能被撤销**：这里的返回值只影响"本轮要不要花预算
           探测"，不影响任何裁决；事实本身可被 `world_facts.forget` 或
           `apply_gene(remove)` 回滚（因此可被账本记录、可被审计）。
        """
        if h is None:
            return 0
        from . import world_facts as W
        key = W.fact_key(W.SCOPE_DEADEND, capsule,
                         W.env_pair_key(*str(env_pair).split("|", 1))
                         if "|" in str(env_pair) else str(env_pair))
        v = W.get(h, key)
        try:
            return int((v or {}).get("observations") or 0)
        except (TypeError, ValueError):
            return 0

    def _observe_deadend(self, h: Harness, capsule: str, env_pair: str,
                         round_no: int, event_kind: str,
                         streak: int) -> Dict[str, Any]:
        """把一次"被拦"记成**观察**；达到门槛才**晋升成事实**写进基因组。

        ⛔ 门槛不能是 1（见 `world_facts.PROMOTE_MIN_OBS`）：一次偶发被写成
           事实会被**继承**下去、影响后续所有轮的提议 ⇒ 把噪声固化成制度。
        ⛔ 晋升是**一次真实的基因编辑**（走 `apply_gene`）⇒ 它会改变
           `harness_id`、能被 `harness.commit` 记进账本、能被回滚。
           这正是本模块存在的理由：洞察不再是过程性的。
        """
        from . import world_facts as W
        pair = (W.env_pair_key(*str(env_pair).split("|", 1))
                if "|" in str(env_pair) else str(env_pair))
        key = W.fact_key(W.SCOPE_DEADEND, capsule, pair)
        obs = [{"key": key, "kind": "deadend", "round_no": round_no}
               for _ in range(max(0, int(streak)))]
        cands = W.promote(obs)
        out: Dict[str, Any] = {"key": key, "observations": int(streak),
                               "promoted": False}
        if key in cands and h is not None:
            merged = dict(cands[key])
            merged.update({"streak": int(streak), "event": event_kind})
            new_h = W.remember(h, key, **merged)
            out["promoted"] = True
            out["harness_id"] = new_h.harness_id
            out["world_facts"] = new_h.world_facts
        return out

    def _deadend_map(self) -> Dict[str, int]:
        """从账本统计每个 (候选, 环境对) 的**连续**被拦次数。

        ⛔ 连续（遇到一次 `generalize` 就归零）而不是累计：累计会把「早期
        失败过、后来成了」的候选也算成死路 ⇒ 永久封死（与难度分层那个
        自指死锁同形态）。
        ⛔ 数据源是**账本**不是内存：换一次运行就忘掉死路 ⇒ 冷却白做。

        ⛔⛔ 2026-10-13：原写法是 `reversed(self.ledger.events())` —— 为了
        算 **7 个** key 的计数而**物化整份账本**（实测峰值 **6.49 MB**），
        而这个函数在 `run_round` 里**每轮调一次** ⇒ 每轮峰值与耗时随账本
        线性增长（自演化跑久了必然变慢）。改：正序一次遍历，流式。

        ⛔⛔ **语义必须逐字对齐旧实现，不许"顺手修"**：旧实现倒序遍历、
        遇 generalize 就 `out[key]=0` 并 `done.add(key)` —— 由于 `done` 之后
        不再累加，而 `out[key]=0` 又**覆盖**了此前累加的值，它的真实语义是
        **二元的**：「这个 key 曾经 generalize 过 ⇒ 0；否则 ⇒ 全部条数」。

        这与上面 docstring 说的"连续"**不是一回事**：序列
        `[G, 拦, 拦]`（成功过、之后又连续被拦两次）旧实现给 **0**，
        "连续"语义应给 **2** ⇒ **只要一个候选曾经成功过，它就永远不会进
        冷却**。这是判据层的 bug，不是性能问题；改它会改变冷却的触发面，
        必须先做「成败 × 是否命中冷却」交叉表再拍板。

        ⇒ 第 14 轮**只换实现、不动语义**，保持二元值，并用旧实现当 oracle
        做随机对拍（含 `[G, 拦, 拦]` 这个边界）把语义钉死 —— 就是为了让
        修它的人**必须先看到交叉表**。

        ⛔⛔ **2026-10-13 第 17 轮：已按"连续"语义修好**。拍板依据是实测
        交叉表（不是"看起来 better"）：

          · 生产账本 `data/ledger.jsonl`：39 条 `gen.probe` / 7 个 key，
            **0 个** key 处在「曾 generalize 过、但末尾连续被拦」的形态
            （所有成功过的 key 序列都是纯 G 或以 G 结尾）
            ⇒ 两种语义在当前账本上**逐 key 同值，零决策翻转**（零回归面）。
          · 冷却阈值本身的误伤率（历史上"连续失败 ≥k 次后下一次真
            generalize"）：k=3 时 12 次命中 / 0 次误伤。样本小，但方向上
            支持"阈值 3 不激进"。
          · ⛔ 反面也记清楚：样本只有 39 条，**证明不了未来零影响**，只证明
            当前零回归。真正保证不永久封死的是 `deadend_probation_every`
            出口（每 5 轮强制试探一次），不是这份统计。

        ⛔⛔ 修的不是"值算错了"，而是**充分统计量选错了**：旧实现记的是
        `total` + `ever_gen`（曾否成功），由此只能算出二元值；连续语义的
        充分统计量是 `streak`（当前连续失败数），正序扫一遍即可维护，
        **并且让增量路径与全量路径严格等价**（不需要重放历史）。
        ⇒ 副作用：`ever_gen` 集合消失，state 更小。

        ⛔⛔ 2026-10-13 第 16 轮：上面那版已经流式了，但它**每轮仍全扫一遍
        账本**（只是不再物化）。账本 10 万条时每轮仍要解析 10 万行 ⇒ 轮数
        一多就成了 O(轮 × 账本)。改：**增量** —— 记住上次读到的文件偏移与
        链头，只解析新增部分。

        ⛔ 增量**不是**"相信文件只会追加"，而是**能证明前面的部分没变**：
          ① 偏移只停在行边界（见 `iter_events` 注释：半行不推进）；
          ② 新增第一条的 `prev_hash` 必须等于缓存的链头 ⇒ 中间被插入或
             改写（rebuild）会立刻发现 ⇒ 回退全量；
          ③ 一条新增都没有、但文件指纹变了 ⇒ **可疑**（`rebuild_from_ledger`
             重写整份、或 truncate 后长度恰好没变）⇒ 宁可全扫一次，
             不可抱着一份可能过时的缓存（fail-closed）。
        ⛔ 缓存失效必须**可观测**：每次全量都记进 `self._deadend_stats`，
           否则"每轮全扫"和"真增量"在外部看起来一模一样（假仪表）。
        """
        state = self._deadend_state()
        #: ⛔ 返回副本：调用方拿到的是快照，改它不会污染增量缓存
        #:    （缓存里的 dict 是要被后续事件继续累加的）。
        return dict(state["streak"])

    def _deadend_state(self) -> Dict[str, Any]:
        """取（必要时更新）死路账的**增量状态**。"""
        self._ensure_deadend_stats()
        c = self._deadend_cache
        if c is None:
            self._deadend_cache = self._deadend_full()
            return self._deadend_cache

        fp = self._deadend_fp()
        # ① 文件变小（truncate / 被重写成更短）⇒ 偏移已经越界 ⇒ 全量
        if fp[0] < c["offset"]:
            self._deadend_stats["invalidated"] += 1
            self._deadend_stats["reasons"]["shrunk"] += 1
            self._deadend_cache = self._deadend_full()
            return self._deadend_cache

        prog: Dict[str, Any] = {"offset": c["offset"]}
        new: List[Any] = list(self.ledger.iter_events(since_offset=c["offset"],
                                                      progress=prog))
        if not new:
            # ② 一条新增都没有，但文件被写过 ⇒ 无法证明缓存仍然成立
            if fp != c["fp"]:
                self._deadend_stats["invalidated"] += 1
                self._deadend_stats["reasons"]["no_new_but_touched"] += 1
                self._deadend_cache = self._deadend_full()
                return self._deadend_cache
            return c

        # ③ 链必须接得上：接不上说明中间的 history 不是我上次看到的那段
        if new[0].prev_hash != c["head_hash"]:
            self._deadend_stats["invalidated"] += 1
            self._deadend_stats["reasons"]["chain_broken"] += 1
            self._deadend_cache = self._deadend_full()
            return self._deadend_cache

        for ev in new:
            self._deadend_accum(ev, c["streak"])
        c["head_hash"] = new[-1].hash
        c["offset"] = int(prog["offset"])
        c["fp"] = fp
        self._deadend_stats["incremental"] += 1
        self._deadend_stats["scanned"] += len(new)
        return c

    def _in_cooldown(self, cap_name: str, env_pair: str,
                     deadend: Dict[str, int], round_no: int
                     ) -> Tuple[bool, int]:
        """(是否冷却, 连续被拦次数)。"""
        key = f"{cap_name}|{env_pair}"
        streak = deadend.get(key, 0)
        if streak < self.cfg.deadend_streak:
            return False, streak
        #: 出口：冷却不是永久封死 —— 每逢 probation 轮允许试探一次。
        if (self.cfg.deadend_probation_every > 0
                and round_no % self.cfg.deadend_probation_every == 0):
            return False, streak
        return True, streak

    def _saturation(self, base_vs: List[Verdict], scored: List[Candidate],
                    overfit_reason: str, h: Harness,
                    base_reward_of: Optional[float] = None
                    ) -> Optional[Dict[str, Any]]:
        """判定「发展集已到顶」（None = 不是饱和，是其它失败）。

        判据（三条同时成立）：
          ① 所有过了闸的候选 advantage 都不为正（没人能超过基线）；
          ② 基线 reward **不低于**任何候选（不是"分数高"，是"没人赢"）；
          ③ 不是被泛化闸拦下的（那是失败，值得重试，不是到顶）。

        ⛔ 不要求"发展集全绿"：全绿时弱点挖掘根本不会产出弱点，轮次在
        更早的 `no_weakness_evidence` 就结束了 ⇒ 要求全绿会让这条判定
        永远不可达（第一版就是这么写的，测试直接把它证伪了）。
        真正要抓的是**停滞**：有弱点、有候选、但没人能超过基线。

        ⛔ 为什么要在饱和时跑一次 held-out：只剩"没提升"这一个模糊结论时，
           dev 全绿而 held-out 不绿是**真信号**（任务分布不够 / 已过拟合），
           必须能读出来。此时 `best` 已经是 None ⇒ 没有候选在拿 held-out
           互相比较 ⇒ 不存在"拿考试题选答案"的污染。
        ⛔ 但它记的是 `saturation.probe` 而不是 `gen.probe`：后者是监督器
           规则读的判定流，混进去会污染"泛化闸被绕过"等判据的样本。
        """
        if overfit_reason or not base_vs or not scored:
            return None
        n_passed = sum(1 for v in base_vs if v.passed)
        if any(c.advantage > self.cfg.min_advantage for c in scored):
            return None
        if base_reward_of is None or any(c.reward > base_reward_of
                                         for c in scored):
            return None

        ho_score: Optional[float] = None
        ho_passed: Optional[int] = None
        if self.heldout is not None:
            try:
                sc, vs = self.heldout.evaluate(h, self.runner)
                ho_score = round(float(sc), 4)
                ho_passed = sum(1 for v in vs if v.passed)
                self.ledger.append(
                    "saturation.probe",
                    {"harness_id": h.harness_id, "heldout_score": ho_score,
                     "heldout_passed": ho_passed, "heldout_total": len(vs),
                     "note": "只读读数（best 已为空 ⇒ 不参与任何选择）"},
                    actor="gate")
            except Exception as e:  # noqa: BLE001 —— 读数失败不许影响判定
                self.ledger.append(
                    "saturation.probe",
                    {"harness_id": h.harness_id,
                     "error": f"{type(e).__name__}: {e}"}, actor="gate")

        clean = (n_passed == len(base_vs))
        head = ("saturated:dev_clean" if clean else "saturated:stagnated")
        if ho_score is None:
            reason = (f"{head}（发展集 {n_passed}/{len(base_vs)} 通过，"
                      f"无候选能超过基线 ⇒ 再跑也是空转；held-out 未配置）")
        elif ho_passed is not None and ho_passed < (
                self.heldout.suite.n_tasks if self.heldout else 0):
            reason = (f"{head}（发展集 {n_passed}/{len(base_vs)} 通过、"
                      f"无候选能超过它，但 held-out 只过 {ho_passed} ⇒ "
                      f"任务分布或合成器不够，不是能力到顶）")
        else:
            reason = (f"{head}（发展集 {n_passed}/{len(base_vs)} 通过、"
                      f"无候选能超过它；held-out {ho_score}）")
        return {"dev_passed": n_passed, "dev_total": len(base_vs),
                "heldout_score": ho_score, "heldout_passed": ho_passed,
                "reason": reason}

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
        """`_run_round` 的薄包装：只负责**并行读数**。

        ⛔ 为什么不写在 `_run_round` 末尾：它有 **6 处提前 return**（无弱点 /
        到顶 / 预算耗尽 / 受保护层…），写在末尾 ⇒ 那些轮次 `parallel=None`
        ⇒ "这一轮并行了多少次"在最需要它的轮次（提前停）反而读不到。
        读数必须在**所有**返回路径上都有值 —— 否则它就是一个会选择性
        失明的仪表。
        """
        _p0 = (self.parallel_batches, self.parallel_runs)
        rep = self._run_round(h, round_no)
        rep.parallel = {
            "configured": int(getattr(self.cfg, "parallel_workers", 0) or 0),
            "batches": self.parallel_batches - _p0[0],
            "runs": self.parallel_runs - _p0[1],
        }
        #: 陈述性知识读数：填在**所有**返回路径都经过的地方（与 parallel 同址）。
        #: ⛔ `try/except`：读数失败不该拖垮一轮进化；失败时留 `None`
        #:    （"没读到"与"读到 0 条"必须能分辨）。
        try:
            rep.world_facts = self.inherited_facts(h)
        except Exception:                                  # noqa: BLE001
            rep.world_facts = None
        return rep

    def _append_safe(self, kind: str, payload: Dict[str, Any],
                     actor: str = "evolution") -> None:
        """账本写入兜底：账本挂了不该让整轮进化陪葬。

        ⛔ 为什么需要它：这个 pass 跑在**每次运行末尾**，而账本写不进
           （磁盘满 / 权限）就让整轮进化失败 ⇒ 因果完全不成比例。
        ⛔ 但 `except: pass` 是最危险的形态 —— 它让"记了"与"没记"无从分辨。
           ⇒ 失败路径**另有事件**承担可见性（见 `_source_cleanup_pass`
           的 `cleanup.source_failed`），不是把失败吞掉。
        """
        try:
            self.ledger.append(kind, payload, actor=actor)
        except Exception:                                  # noqa: BLE001
            pass

    def _source_cleanup_pass(self, reports: List[RoundReport]) -> None:
        """**每次运行**一次：扫源码侧自指负载 → 产出清理提案 → 过闸 → 记账。

        ═══ 它回答的是"让系统能提议改自己的源码"这件事的**另一半** ═══

        基因组侧（r39 的 `CleanupProposer`）已经在**每轮**提议了，但源码侧
        此前只有一条**人肉 CLI**（`aether caps cleanup propose`）⇒
        "系统的债是在涨还是在消"这个问题**没有任何时间序列**能回答 ——
        每次看都是一个人肉快照。那正是"只能靠人回头发现"的机制形态。

        ⛔ 为什么不每轮做：三次全仓 AST 扫描很贵，每轮扫 ⇒ 进化循环会在
           编码工作上花掉比评估还多的时间 ⇒ 把"努力分配"砍向了**另一端**。
           我们要的是"分一点力给它"，不是"全搬过去"。

        ⛔⛔ **源码清理不参与 `best` 选择**（与基因组侧的关键区别）：
            删源码**不改变 harness** ⇒ reward 不变 ⇒ 拿它跟能力候选比，
            是把两种量纲塞进同一把尺子（与"两轴不加权求和"同一个理由）。
            ⇒ 它走**并行的提案流水线**：提议 → 过闸 → 记账 → 等人执行。
            ⛔ 后果：它**不会**出现在任何一轮的 `committed` 里 ——
               那不是"没记账"，是账记在 `cleanup.source_pass` 事件上。

        ⛔ 扫描挂了 ⇒ **记失败事件**，不静默跳过：
           "没扫"与"扫了没有"必须能分辨（否则它又是一个假仪表）。

        ⛔⛔ **可重入守卫**（2026-10-16 实测踩到，代价是 108 个失控进程）：
           本 pass 由 `run()` 在末尾调用；而对拍跑的**冻结契约里有若干份自己
           会调用 `run()`**（`contract_evolution_test` / `contract_checkpoint_test`
           都在进程内构造 `EvolutionLoop` 并 `run(...)`）。
               ⇒ run() → 本 pass → probe → 子进程跑契约 → 契约里 run()
                 → 本 pass → probe → … ⇒ **无限递归**（不是慢，是不终止）。
           ⛔ 这也是当初 `max_source_probes=0` **承重的真正原因**：它不是
              "太贵所以关掉"，而是**关掉它恰好把递归压住了**。⇒ 光"因为
              实测只要 160s 就放开默认值"会把递归一起放出来（已实测复现）。
           ⇒ 正解是**可重入守卫**：嵌套调用直接跳过并**显式记账**
              （`cleanup.source_skipped` / reason=`reentrant`），
              而**不是**靠预算默认值去兜底 —— 兜底这件事不该由一个
              成本参数承担，它与安全性正交。
        """
        if not self.cfg.source_cleanup_enabled:
            return
        #: 双重守卫：**进程内**用类旗子，**跨进程**用环境变量。
        #: ⛔⛔ 为什么环境变量不可省：对拍是**起子进程**跑契约的，类旗子
        #:    活在父进程的内存里 ⇒ 子进程看到的是初始值 False。
        #:    只加类旗子 ⇒ 递归照旧（已实测：108 个进程失控）。
        #:    `contract_runner` 用 `env = dict(os.environ)` 生成子进程环境
        #:    ⇒ 这里设的环境变量**会**被子进程继承 ⇒ 能挡住跨进程递归。
        if type(self)._in_source_cleanup or os.environ.get(_GUARD_ENV) == "1":
            #: ⛔ 跳过的**必须**记账：否则"嵌套里没扫"和"扫了没有问题"
            #:    在外部完全等价（第 11 条：跳过是最会撒谎的那种通过）。
            self._append_safe("cleanup.source_skipped", {
                "rounds": len(reports),
                "reason": "reentrant",
                "note": ("嵌套的 run() 里不再重复扫源码（对拍跑的契约自己会调 run()"
                         "⇒ 不守卫就是无限递归）。**这不是通过，是没有读数**。")})
            return
        _prev = os.environ.get(_GUARD_ENV)
        os.environ[_GUARD_ENV] = "1"
        type(self)._in_source_cleanup = True
        try:
            return self._source_cleanup_pass_inner(reports)
        finally:
            type(self)._in_source_cleanup = False
            if _prev is None:
                os.environ.pop(_GUARD_ENV, None)
            else:
                os.environ[_GUARD_ENV] = _prev

    def _source_cleanup_pass_inner(self, reports: List[RoundReport]) -> None:
        """真正的实现体。⛔ **只能**被 `_source_cleanup_pass` 调用。

        拆成两层是**可重入守卫**的实现需要：外层负责"要不要做 + 记账"，
        内层只管"怎么做"。把守卫和业务逻辑写在一个函数里 ⇒ `return`
        散落多处，迟早漏掉 `finally` 复位（与「退化分支要整段进守卫」
        同族事故）。
        """
        try:
            from . import cleanup as _cl
            from . import srcmut as _sm
            from .paths import ROOT as _root
        except Exception as e:                             # noqa: BLE001
            self._append_safe("cleanup.source_failed", {
                "rounds": len(reports),
                "error": f"{type(e).__name__}: {e}",
                "note": "源码清理 pass 连模块都没导入 ⇒ 这一项**没有读数**"})
            return
        try:
            rd = _cl.source_load()
            props = _cl.build(limit=self.cfg.max_source_probes * 4 + 8)
        except Exception as e:                             # noqa: BLE001
            self._append_safe("cleanup.source_failed", {
                "rounds": len(reports),
                "error": f"{type(e).__name__}: {e}",
                "note": "源码清理 pass 扫描失败 ⇒ 这一项**没有读数**"})
            return

        #: ① 读数事件（沿用旧的 `cleanup.scan` 形态 + 三个键，向后兼容）
        self._append_safe("cleanup.scan", {
            "rounds": len(reports),
            "source": rd,
            "genome": (reports[-1].genome_load_before if reports else -1),
            "note": ("源码侧自指负载（**下界**）。每次运行扫一次、不每轮扫"
                     "（太贵）。要回答『债是在涨还是在消』，就把这个事件的"
                     "时间序列连起来读 —— 单看一次只能看到一个快照。"),
        })

        #: ② 过闸 + 逐条记账
        probed = 0
        n_pass = 0
        by_kind: Dict[str, int] = {}
        #: ⛔ 原因码：**必须**能分辨「没东西可证明」与「有东西但没给预算」。
        #:    二者在外部都表现为 n_passed=0，但修法完全相反（补契约 vs 给预算）。
        #:    与「三态必须分开」同族：把环境故障读成"等价"是同一类事故。
        reasons: Dict[str, int] = {}
        for p in props:
            by_kind[p.kind] = by_kind.get(p.kind, 0) + 1
            verdict = None
            code = ""
            if p.edits:
                if probed >= self.cfg.max_source_probes:
                    code = "over_budget"
                    p.evidence["nonregression"] = {
                        "ok": False, "unverified": True,
                        "reason": (f"超出本次对拍预算（max_source_probes="
                                   f"{self.cfg.max_source_probes}）⇒ 未验证")}
                elif type(self)._probed_once:
                    #: ⛔ **进程级一次性闩锁**：对拍的前提是"源码未变"，
        #:    而**同一进程内源码不会变** ⇒ 第二次及以后重跑对拍
        #:    **零信息量**，只是把分钟级成本乘上调用次数
        #:    （实测：契约里 run() 被调多次 ⇒ 4 份契约集体超时）。
        #:    ⛔ 与可重入守卫是**两件事**：守卫挡"嵌套"，闩锁挡"重复"。
                    code = "already_probed"
                    p.evidence["nonregression"] = {
                        "ok": False, "unverified": True,
                        "reason": ("本进程已对拍过一次 ⇒ 源码未变，"
                                   "重跑无信息量（想强制重跑请换进程）")}
                else:
                    probed += 1
                    type(self)._probed_once = True
                    eds = [_sm.Edit.from_dict(x) for x in p.edits]
                    sel, why = _sm.select_contracts(
                        _root, [e.file for e in eds])
                    pr: Dict[str, Any]
                    if sel:
                        factory = (type(self)._probe_runner_factory
                                   or _sm.contract_runner)
                        pr = _sm.probe(_root, eds, runner=factory(sel))
                    else:
                        pr = {"ok": False, "unverified": True, "reason": why}
                    p.evidence["nonregression"] = pr
                    verdict = bool(pr.get("ok")) and not pr.get("unverified")
                    code = "verified_ok" if verdict else (
                        "verified_fail" if not pr.get("unverified")
                        else "probe_unverified")
                    if not sel:
                        code = "no_contracts"
            else:
                #: ⛔ 前两类是**启发式** ⇒ 拿不出可应用的编辑 ⇒ 无从对拍。
                #:    它们本来就该红 —— 这不是缺陷，是判据的边界：
                #:    启发式只撑得住"提案"，撑不住"判定"。
                code = "heuristic_no_edits"
                p.evidence["nonregression"] = {
                    "ok": False, "unverified": True,
                    "reason": ("启发式判定 ⇒ 无可执行编辑 ⇒ 无法对拍；"
                               "这一类只能提案，不能过闸")}
            p.evidence["nonregression"]["reason_code"] = code
            reasons[code] = reasons.get(code, 0) + 1
            p = _cl.validate(p, non_regression_ok=verdict)
            _cl.record(self.ledger, p)
            n_pass += 1 if p.gate_passed else 0

        #: ③ 汇总事件（这次运行的源码清理读数）
        #:    ⛔ `unverified_reasons` 是**给外部看的判据**：n_passed=0 时
        #:       靠它区分「无提案」/「预算为 0」/「契约缺失」/「确实删坏了」。
        n_provable = sum(1 for p in props if p.edits)
        self._append_safe("cleanup.source_pass", {
            "rounds": len(reports),
            "n_proposed": len(props), "n_by_kind": by_kind,
            "n_provable": n_provable,
            "probe_budget": int(self.cfg.max_source_probes),
            "n_probed": probed, "n_passed": n_pass,
            "unverified_reasons": dict(sorted(reasons.items())),
            "load": rd,
            "note": ("源码侧清理 pass：提议 → 过闸 → 记账。**不进 best "
                     "选择**（删源码不改 harness ⇒ reward 不变 ⇒ 两种量纲"
                     "不能比）。过闸的提案等人执行："
                     "`aether caps cleanup approve`。"
                     + ("" if self.cfg.max_source_probes > 0 else
                        " ⚠️ 本轮预算为 0 ⇒ 可证明提案**一律未验证**，"
                        "这不是『没有债』而是『闸被关着』。")),
        })

    def _genome_load(self, h: Harness) -> int:
        """基因组侧的自指负载（死条目数）。**扫不出来 ⇒ 返回 -1**。

        ⛔ 为什么必须能表示"没扫出来"：返回 0 会得到一个"没有死条目"的
           假象 ⇒ 减负轴从此**永远为零** ⇒ 清理候选全部因"减负不可测"
           被拦 ⇒ 看起来像"清理没用"，实际是表坏了。
           -1 与 0 必须能分辨（与"skipped ≠ pass"同一条纪律）。

        ⛔ 延迟 import：`cleanup → agent → memory/environment` 这条链不轻，
           而本方法只在**真的要量减负**时才需要；且它依赖 `agent` 的
           策略词汇表，那本身就是"判据同源"要求的代价。
        """
        try:
            from .cleanup import genome_load
            return int(genome_load(h))
        except Exception:                                  # noqa: BLE001
            return -1

    # ── 基线随 best 升级 ────────────────────────────────────────
    def _rebaseline(self, h: Harness, round_no: int, reason: str) -> None:
        """把非回归基线 supersede 到**当前 best** harness。

        ⛔⛔ 为什么必须有这一步（2026-10-17 实测）：基线停在**种子**时，
            它守的永远是"种子能力"。裸种子（只有工具、没有修法）的通过集
            只含"本来就不需要修的题"（= 原始源码那 4 条）⇒ 提案器能产生
            的变异（装技能 / 改路由 / 改元规则）**全部**不改变这类题的
            判定 ⇒ 闸在结构上恒绿：账本 234 条 `gate.result` 全 True、
            `gate.blocked` 0 次、`deltas` 全 0。
            ⛔ 闸不是没牙 —— 砍工具 / 砍 python_exec / 口径漂移都会红
            （`scripts/probe_gate_discrimination.py` 反证组 4/5 红）。
            缺的是**判据集与变异空间相交**：只有基线跟着 best 走，
            "best 已经具备的修法被顶掉 / 链被清空"才会变成可观测的回归。

        ⛔ 反向对照（同一轮实测，别走回头路）：想靠"给种子预置一条修法
            链"取得判别力，代价是进化活动直接减半（3 轮：事件 155→70、
            闸门裁决 10→3、提交 2→1）—— 与"记忆预置修法 ⇒ 候选增益 0"
            同一条纪律：外部预置的能力会**掩盖**真实弱点。

        ⛔ 被 supersede 的旧基线会**拒绝一切对比**（`check_caliber` 首行）
            ⇒ 必须同步替换 `gate.baseline`，不能只标记旧的。
        """
        try:
            from .nongression import Baseline
            old = self.gate.baseline
            vs = {t.task_id: self.runner(h, dict(t.spec))
                  for t in self.gate.suite}
            new_b = Baseline(caliber=dict(self.caliber), verdicts=vs,
                             harness_id=h.harness_id)
            n_old = sum(1 for v in old.verdicts.values() if v.reward > 0)
            n_new = sum(1 for v in vs.values() if v.reward > 0)
            self.gate.baseline = new_b
            self.ledger.append("baseline.superseded", {
                "round_no": round_no, "reason": reason,
                "from_harness": old.harness_id, "to_harness": h.harness_id,
                "n_passing_before": n_old, "n_passing_after": n_new,
                "delta": n_new - n_old,
                "shrank": n_new < n_old,
                "note": ("基线随 best 升级 ⇒ 闸守的是『当前已具备的能力』"
                         + ("⚠️ 通过集缩小 ⇒ 有题从『必须守住』里掉出去了，"
                            "这是一次能力倒退被放行（fail-open 边缘）"
                            if n_new < n_old else ""))},
                actor="evolution")
        except Exception as e:                             # noqa: BLE001
            #: ⛔ 升级失败不得静默，也不得拖垮已经完成的提交
            self.ledger.append("baseline.supersede_failed", {
                "round_no": round_no, "reason": reason,
                "harness_id": h.harness_id,
                "error": f"{type(e).__name__}: {e}"},
                actor="evolution")

    def _run_round(self, h: Harness, round_no: int = 0) -> RoundReport:
        rep = RoundReport(round_no=round_no)

        # 1) 基线探测 —— **必须放在挖掘之前**。
        #    第一轮时库里没有任何 run ⇒ 挖不到弱点 ⇒ 循环"什么都不做"
        #    却记为成功，这是自进化系统最难发现的假成功形态。
        #    先跑一次发展集，把失败证据写进库，再谈挖掘。
        base_reward, base_vs = self.evaluate_dev(h, tag="base:")
        rep.baseline_reward = base_reward
        base_by_level = self.reward_by_difficulty(base_vs)
        #: 减负轴的**存量**基准（在基线 harness 上现扫）。
        #: ⛔ 与 `load_gain`（增量）分开记：只看增量 ⇒ 不知道债是在减少
        #:    还是在增加（背着债还在涨，与没背债在涨，是两件不同的事）。
        base_load = self._genome_load(h)
        rep.genome_load_before = base_load

        # 2) MINE —— 弱点必须由**这一轮、这个 harness 的**失败证据支撑。
        #
        #    ⛔ 2026-10-13 实测（10 轮压力跑）：原先从 store 按 harness_id
        #    取历史 run。harness_id 由内容决定 ⇒ 新一次运行的 seed harness
        #    与上次**同 id**，于是上次运行遗留的失败 run 被当成"当前弱点"
        #    ⇒ r2–r10 每轮挖出同一个过期弱点、提同一个候选、拿到同一个
        #    advantage=0.000，空转 9 轮。与监督器那个误报同形态：**缺会话
        #    归属**。而投影里的 run 恰恰没有 sid（RunRow 无此字段）⇒
        #    按 sid 过滤在当前 schema 下做不到。
        #
        #    ⇒ 直接拿本轮 base 评估的判定当证据：它们天然属于"这一轮 +
        #      这个 harness + 当前这套发展集"，串味在结构上不可能发生。
        #      （历史 run 仍然写进 store，供投影/归因/审计使用。）
        runs = self._runs_from_verdicts(base_vs, h)
        weaknesses = mine_weaknesses(runs)
        #: ⛔⛔ 无弱点**不再直接 return** —— 那正是"每轮都在评估候选、没有
        #:    任何东西在自己的产出上花一点力"的机制根源：发展集一全绿，
        #:    这一轮就什么也不做（实测：10 轮压力跑里 r2–r10 全是这个，
        #:    空转 9 轮）。改成：能力侧无事可做 ⇒ 把这一轮花在清理上。
        w = weaknesses[0] if weaknesses else None
        capsules: List[Capsule] = []
        if w is not None:
            rep.weakness = w
            rep.ledger_seqs.append(self.ledger.append(
                "weakness.mined", {"round_no": round_no, **w.to_dict()},
                actor="evolution").seq)
            # 3) PROPOSE + 构造候选
            #: ⛔ 提议预算**在调用之前**查：真 LLM 时一次 propose = 一次付费
            #:    调用，调用完再查等于钱已经花出去了（止损必须发生在花钱前）。
            if self.budget is not None and self.budget.exhausted():
                rep.revert_reason = (f"budget_exhausted（{self.budget.exhausted()}"
                                     f" ⇒ 不再提议）")
                rep.ledger_seqs.append(self.ledger.append(
                    "evolution.budget_stop", {
                        "round_no": round_no, "harness_id": h.harness_id,
                        "reason": rep.revert_reason,
                        "budget": self.budget.to_dict()},
                    actor="evolution").seq)
                return rep
            capsules += list(self.proposer.propose(
                w.to_dict(), h, n=self.cfg.group_size))
            if self.budget is not None:
                self.budget.spend(propose_calls=1)

        # 3b) **常设清理提案器**：与"这一轮有没有弱点"无关，每轮都跑。
        #:     ⛔ 它不看 weakness —— 码健康不是失败信号，是**维护轴**，
        #:        发展集全绿它也不会消失。放在弱点分支**之后**且不受它
        #:        `return` 影响，是这一轮"总在产出上花力"的保证。
        if self.cleanup is not None:
            try:
                got = self.cleanup.propose(
                    {}, h, n=self.cfg.cleanup_group_size)
            except Exception as e:                         # noqa: BLE001
                #: ⛔ 编码层故障不该让循环炸掉，但**不许静默**：提不出来
                #:    必须能在账本上看见（否则"没提"与"没扫"无法区分）。
                got = []
                self.ledger.append(
                    "cleanup.propose_failed",
                    {"round_no": round_no, "harness_id": h.harness_id,
                     "error": f"{type(e).__name__}: {e}"}, actor="evolution")
            capsules += list(got)
            rep.cleanup_proposed = len(got)

        if not capsules:
            #: 两边都没得提 ⇒ 才是真的"这一轮无事可做"（与"到顶"同族）。
            #: ⛔ 措辞沿用原分支：契约依赖它，且它必须与"提了但被闸拦下"
            #:    区分开 —— 后者值得重试，前者再跑也是空转。
            rep.idle = True
            rep.revert_reason = (
                "no_weakness_evidence（无失败证据 ⇒ 不提议）" if w is None
                else "no_candidates（能力侧与清理侧都没产出候选）")
            rep.ledger_seqs.append(self.ledger.append(
                "evolve.noop", {"round_no": round_no, "harness_id": h.harness_id,
                                "reason": rep.revert_reason},
                actor="evolution").seq)
            return rep
        #: 死路账：**从账本读**（事实源），不是进程内状态 —— 否则换一次
        #:    运行就"忘了"这条死路，冷却形同虚设。
        deadend = self._deadend_map()
        env_pair = self.heldout.env_pair if self.heldout is not None else "unknown"
        cands: List[Candidate] = []
        for cap in capsules:
            #: 死路冷却：已知在这套环境对上会被拦的候选，不再为它花预算。
            #: ⛔ 冷却只跳过**探测**，不许静默 —— 每条都进账本。
            cooling, streak = self._in_cooldown(cap.name, env_pair, deadend,
                                                round_no)
            #: ⛔⛔ **继承的知识优先于本轮派生量**：如果基因组里已经记着
            #:    "这个候选在这套环境对上是死路"，就没有理由再花一次探索。
            #:    这不是新增一层闸 —— 它是让**已经学到的**东西真的起作用
            #:    （此前洞察烂在内存缓存里，换一次运行就丢光）。
            inherited = self._facts_deadend_streak(h, cap.name, env_pair)
            if inherited >= 1 and not cooling:
                cooling = True
                streak = max(streak, inherited)
                self.ledger.append(
                    "deadend.inherited",
                    {"round_no": round_no, "capsule": cap.name,
                     "env_pair": env_pair, "observations": inherited,
                     "note": "基因组里已记着这条死路（陈述性知识）⇒ "
                             "本轮不花探测预算；要撤销请 remove 那条 fact"},
                    actor="evolution")
            if cooling:
                cands.append(Candidate(
                    capsule_name=cap.name, harness_id="",
                    reason=f"deadend_cooldown(连续 {streak} 次被拦 "
                           f"@ {env_pair})"))
                self.ledger.append(
                    "deadend.cooldown",
                    {"round_no": round_no, "capsule": cap.name,
                     "env_pair": env_pair, "streak": streak,
                     "note": "已知死路 ⇒ 本轮不花探测预算（每 "
                             f"{self.cfg.deadend_probation_every} 轮试探一次）"},
                    actor="evolution")
                #: ⛔⛔ **观察 → 事实**：连续被拦达门槛，就把这条洞察
                #:    **写进基因组**（而不是只留在本轮的派生量里）。
                #:    这是本模块存在的理由：洞察要能被**继承**。
                #:    ⛔ 晋升只做一次（已有事实就不重复写，避免每轮改
                #:      `harness_id` 造成无意义的产物增殖）。
                if streak >= int(self.cfg.deadend_streak):
                    try:
                        pair0 = env_pair
                        k0 = __import__(
                            "aether.world_facts", fromlist=["x"]).fact_key(
                            "deadend", cap.name,
                            __import__("aether.world_facts",
                                       fromlist=["x"]).env_pair_key(
                                *str(env_pair).split("|", 1))
                            if "|" in str(env_pair) else str(env_pair))
                        already = self._facts_deadend_streak(h, cap.name,
                                                             pair0) >= 1
                        if not already:
                            prom = self._observe_deadend(
                                h, cap.name, pair0, round_no,
                                "gen.probe", streak)
                            if prom.get("promoted"):
                                rep.facts_promoted.append(prom)
                                self.ledger.append(
                                    "world_fact.promoted",
                                    {"round_no": round_no,
                                     "capsule": cap.name, "env_pair": env_pair,
                                     "observations": streak,
                                     "fact_key": k0,
                                     "harness_id": prom.get("harness_id"),
                                     "note": "连续被拦达标 ⇒ 晋升为"
                                             "**陈述性知识**（可继承、"
                                             "可被选择、可被回滚）"},
                                    actor="evolution")
                    except Exception as e:                 # noqa: BLE001
                        #: 晋升失败不该拖垮一轮：留痕并继续（读数里能看见）。
                        self.ledger.append(
                            "world_fact.promote_failed",
                            {"round_no": round_no, "capsule": cap.name,
                             "error": f"{type(e).__name__}: {e}"},
                            actor="evolution")
                continue
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
            #: 5b) 减负轴：**在候选 harness 上现扫**，不许用提案时算好的数。
            #:    ⛔ 减负读数必须可复核：提案说"清理后少 1 条"，真正的验证
            #:      是拿应用后的产物再扫一遍 —— 对不上就是提案在撒谎。
            #:    ⛔ 扫不出来（-1）⇒ load_gain 记 0 而不是记一个看起来
            #:      很大的数 ⇒ 坏了的表**不放行**（fail-closed）。
            cand_load = self._genome_load(cand_h)
            c.load_gain = (float(base_load - cand_load)
                           if base_load >= 0 and cand_load >= 0 else 0.0)
            c.is_cleanup = (cap.origin == "cleanup")
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

        # 7) 选择：**两轴准入**（能力轴 ∪ 减负轴，取「或」）
        #    ⛔ 两轴**不加权求和**：reward 的单位是分、load 的单位是条，
        #      把它们换算成同一个数 ⇒ 换算比例是编出来的 ⇒ 系统只是去
        #      讨好那个比例（与"held-out 参与选择 ⇒ 闸自我取消"同形态）。
        best: Optional[Candidate] = None
        best_gain = -1e9
        best_load = -1e9
        for c in scored:
            gain = c.reward - base_reward
            if c.is_cleanup:
                # ── 减负轴 ─────────────────────────────────────
                #: ⛔ **不判"组内优势"**：组内相对优势是为**有噪声的 reward
                #:    采样**设计的（同一任务多次采样才需要归一化）。死条目数
                #:    是**确定性计数**，没有采样噪声要归一化 ⇒ 拿它去跟
                #:    能力候选比 z-score，是把两种量纲塞进同一把尺子。
                if c.load_gain < self.cfg.min_load_gain:
                    c.reason = (f"cleanup_no_load_gain(load_gain="
                                f"{c.load_gain:.0f} < "
                                f"{self.cfg.min_load_gain:.0f})"
                                f" ⇒ 这次清理不可测")
                    continue
                if gain < 0:
                    c.reason = (f"cleanup_regresses(gain={gain:.4f}) ⇒ "
                                f"清理不得伴随能力下降（减负不是降质的借口）")
                    continue
                #: ⛔ 增殖闸对清理**结构性不适用**：它防的是体积膨胀，
                #:    而清理的产物必然变小（size_delta < 0）。不写这条
                #:    判断，是因为写了它永远不触发（看着有闸，实际是空闸）。
            else:
                # ── 能力轴（原判据，一个字都不改）────────────────
                if c.advantage <= self.cfg.min_advantage:
                    c.reason = c.reason or f"no_advantage({c.advantage:.3f})"
                    continue
                if gain < self.cfg.min_reward_gain:
                    c.reason = f"below_capability_floor(gain={gain:.4f})"
                    continue
                growth = c.blast.get("size_delta", 0) / max(1, h.size())
                if growth > self.cfg.max_size_growth_ratio and gain < 0.1:
                    c.reason = (f"proliferation_gate(growth={growth:.2f}, "
                                f"gain={gain:.4f})")
                    continue
            #: ⛔ 排序键 (gain, load_gain) ⇒ **能力优先**。
            #:    一个真让 reward 涨了的候选永远排在"只是少一台机器"前面
            #:    ⇒ 清理只在**没有能力可涨**的那些轮才上位。这正是要的
            #:    努力分配：有得涨就涨，没得涨就收拾。
            if best is None or (gain, c.load_gain) > (best_gain, best_load):
                best = c
                best_gain = gain
                best_load = c.load_gain
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
            if best.is_cleanup:
                #: ⛔ 清理走泛化闸的**退化面**（`probe_removal`：held-out
                #:    不许变差）。走 `probe()` 必得 `no_gain` ⇒ blocks
                #:    ⇒ 清理永远提交不了 —— 那不是闸严，是**用错了闸**：
                #:    overfit 问的是"涨的是不是真的"，而清理根本不涨。
                pr = self.heldout.probe_removal(h, new_h_probe, self.runner)
            else:
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
                              #: ⛔ 环境对是死路冷却的维度：缺了它，冷却只能
                              #:    按候选全局生效 ⇒ 换环境后被误伤。
                              "env_pair": env_pair,
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
            #: ⛔ 「没提交」有两种截然不同的含义，不能合并成一句
            #:    `no_candidate_passed`：被闸拦下（失败，值得重试）与发展集
            #:    已到顶（正常终止，再跑也是空转）。混在一起 ⇒ 10 轮压力跑
            #:    里 r2–r10 全部长成同一个样子，看不出系统其实早就没有
            #:    可提升空间了（实测：base 恒 1.0 / advantage 恒 0.000）。
            sat = self._saturation(base_vs, scored, overfit_reason, h,
                                   base_reward)
            if sat is not None:
                rep.saturated = True
                rep.idle = True
                rep.saturation = sat
                rep.revert_reason = sat["reason"]
                self.ledger.append("evolution.saturated",
                                   {"round_no": round_no,
                                    "harness_id": h.harness_id, **sat},
                                   actor="evolution")
            else:
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

        # 8b) ⛔ 基线必须跟着 best 升级 —— 否则非回归闸永远只守"种子
        #     能力"（裸种子只修得好"不用修的题"）⇒ 判据集与变异空间
        #     不相交 ⇒ 闸结构上恒绿（详见 `_rebaseline` 的实测注释）。
        self._rebaseline(new_h, round_no,
                         reason=f"commit:{best.capsule_name}")

        # 9) 反事实层归因 —— **提交之后**才算，且**绝不参与选择**。
        #    ⛔ 顺序与 held-out 同理：归因一旦进入 group_relative 或 best
        #    排序，它就变成了第二张发展集，进化会去讨好归因本身。
        _attr_specs: List[Dict[str, Any]] = []
        if self.cfg.attribute_layers:
            from .attribution import attribute_layers
            try:
                specs = self._pick_attribution_specs(new_h)
                _attr_specs = list(specs)
                lc = attribute_layers(self.runner, new_h, specs)
                rep.layer_credit = lc.to_dict()
                self.ledger.append("attribution.layer", {
                    "round_no": round_no, "harness_id": new_h.harness_id,
                    **lc.to_dict()}, actor="evolution")
            except Exception as e:  # noqa: BLE001 —— 归因失败不得影响提交
                rep.layer_credit = {"available": False,
                                    "reason": f"{type(e).__name__}: {e}"}
                #: ⛔ 但**不得静默**：归因是"哪一层真正起作用"的唯一证据，
                #:    它挂了却只在内存报告里写一句 ⇒ 账本/控制台上都看不到
                #:    ⇒ 下一次仍然不知道自己是在盲改（实测：18 次提交只有
                #:    4 条归因事件，其余去哪了以前无人知晓）。
                try:
                    self.ledger.append("attribution.unavailable", {
                        "round_no": round_no,
                        "harness_id": new_h.harness_id,
                        "error": f"{type(e).__name__}: {e}",
                    }, actor="evolution")
                except Exception:  # noqa: BLE001 —— 记账本身也不拖垮提交
                    pass

        # 9b) step 级信用分配（`assign_credit` 的 turn-level）。
        #     ⛔⛔ 2026-10-14 兑现审计实测：它在端到端 evolve 里被调 **0 次**
        #     —— 不是"算错了"，是**从来没被接线**（生产没有 replay_fn）。
        #     现在由 `HarnessAgent.replay_fn` 提供**真**反事实重放。
        #     ⛔ 拿不到可重放的 agent ⇒ 记 unavailable，**不给近似值**：
        #        编出来的归因比不归因更有害（V5）。
        #     ⛔ 与 9) 一样在提交之后、一样不参与选择。
        if self.cfg.attribute_layers and _attr_specs:
            try:
                from .runtime import assign_credit
                ag = getattr(self.runner, "agent", None)
                if ag is None or not hasattr(ag, "replay_fn"):
                    rep.step_credit = {
                        "available": False,
                        "reason": ("runner 不是可重放的 HarnessAgent ⇒ "
                                   "turn-level 归因不可用（不给近似值）")}
                    self.ledger.append("attribution.step_unavailable", {
                        "round_no": round_no,
                        "harness_id": new_h.harness_id,
                        "reason": rep.step_credit["reason"]},
                        actor="evolution")
                else:
                    spec0 = _attr_specs[0]
                    rf = ag.replay_fn(new_h, spec0)
                    if rf is None:
                        rep.step_credit = {
                            "available": False,
                            "reason": ("环境不可重放（同一 harness 两次 "
                                       "reward 不一致）⇒ 不给归因数字")}
                        self.ledger.append("attribution.step_unavailable", {
                            "round_no": round_no,
                            "harness_id": new_h.harness_id,
                            "reason": rep.step_credit["reason"]},
                            actor="evolution")
                    else:
                        res = ag.run(new_h, spec0)
                        cr = assign_credit(res.trajectory, replay_fn=rf)
                        rep.step_credit = cr.to_dict()
                        self.ledger.append("attribution.step", {
                            "round_no": round_no,
                            "harness_id": new_h.harness_id,
                            **cr.to_dict()}, actor="evolution")
            except Exception as e:  # noqa: BLE001 —— 归因失败不得影响提交
                rep.step_credit = {"available": False,
                                   "reason": f"{type(e).__name__}: {e}"}
                try:
                    self.ledger.append("attribution.step_unavailable", {
                        "round_no": round_no,
                        "harness_id": new_h.harness_id,
                        "error": f"{type(e).__name__}: {e}"},
                        actor="evolution")
                except Exception:  # noqa: BLE001
                    pass
                    pass
        return rep

    # ── 多轮 ──────────────────────────────────────────────────────
    # ── 断点续跑（checkpoint）─────────────────────────────────────
    def _cfg_fingerprint(self) -> str:
        """进化配置的指纹。**只**取 `EvolutionConfig` 自己的字段。"""
        try:
            return _cp.fingerprint(asdict(self.cfg))
        except Exception:      # noqa: BLE001 —— 指纹算不出 ⇒ 退回空串
            return ""

    def write_checkpoint(self, h: Harness, reports: List[RoundReport],
                         rounds_total: int) -> Optional[str]:
        """把"已完成到第几轮 + 当前 harness"落盘。**只在轮末调用**。

        ⛔ 为什么只在轮末：轮中写的 checkpoint 会与"写到一半的账本"配对
        ⇒ 恢复后这一轮被跑第二遍（与 `RunBudget` 只在轮末查挂钟同源）。

        ⛔ 写失败**不得**中断进化：checkpoint 是**派生量**，不是事实源
        （事实源是账本）。但必须记账 —— 否则"我明明开了 --checkpoint"
        却续跑不了，是没有任何读数的静默失败（假仪表）。
        """
        if not self.checkpoint_path:
            return None
        try:
            cp = _cp.Checkpoint(
                round_no=reports[-1].round_no if reports else 0,
                rounds_total=rounds_total,
                harness=h.to_dict(),
                harness_id=str(getattr(h, "harness_id", "") or ""),
                ledger_seq=int(getattr(self.ledger, "seq", 0) or 0),
                run_seq=int(self._run_seq),
                caliber_fp=_cp.fingerprint(self.caliber),
                cfg_fp=self._cfg_fingerprint(),
                done_rounds=tuple(r.to_dict()
                                  for r in reports[-self._CP_KEEP_ROUNDS:]),
            )
            path = _cp.write(self.checkpoint_path, cp)
        except Exception as e:  # noqa: BLE001 —— 记账，不吞
            try:
                self.ledger.append(
                    "evolution.checkpoint_failed",
                    {"path": self.checkpoint_path, "error": f"{type(e).__name__}: {e}",
                     "note": "checkpoint 是派生量 ⇒ 不中断进化，但本次进度"
                             "不可续跑（没有读数就等于没有这个功能）"},
                    actor="evolution")
            except Exception:   # noqa: BLE001 —— 账本也写不进 ⇒ 只能放弃
                pass
            return None
        self.last_checkpoint = path
        return path

    def load_checkpoint(self, path: Optional[str] = None
                        ) -> Tuple[Any, Harness]:
        """读并**校验** checkpoint，返回 `(Checkpoint, harness)`。

        ⛔ fail-closed：任何不一致都抛 `CheckpointError`（带可行动原因）。
        ⛔ 校验用的 caliber / cfg / ledger_seq 全部取自**当前实例**：
           拿"当时的环境"去校验是无意义的，resume 要回答的是
           "现在这套环境能不能接着那次跑"。
        """
        p = path or self.checkpoint_path
        if not p:
            raise _cp.CheckpointError(
                "未指定 checkpoint 路径（用 --checkpoint 指定，或先跑一次"
                "带 --checkpoint 的 evolve）")
        cp = _cp.read(p)
        _cp.verify_or_raise(cp,
                            ledger_seq=int(getattr(self.ledger, "seq", 0) or 0),
                            caliber=self.caliber, cfg=asdict(self.cfg))
        return cp, Harness.from_dict(cp.harness)

    def run(self, h: Harness, rounds: int = 3,
            start_round: int = 0) -> Tuple[Harness, List[RoundReport]]:
        """跑多轮进化。返回 (最终 harness, 每轮报告)。

        ⛔ 回滚语义：某轮没选出候选 ⇒ harness 保持不变（指针不前进），
        而不是"部分应用"。

        ⛔ `start_round`：续跑时轮号必须**接着上次的编号**，否则账本里
        会出现两个"第 3 轮"，而"跑了几轮"这个读数随之失真（与 run_id
        锚同族的事故）。
        """
        reports: List[RoundReport] = []
        cur = h
        idle_streak = 0
        t0 = time.time()
        rounds_total = start_round + rounds
        for i in range(rounds):
            rep = self.run_round(cur, round_no=start_round + i + 1)
            reports.append(rep)
            if self.budget is not None:
                self.budget.spend(rounds=1, seconds=time.time() - t0)
                rep.budget = self.budget.to_dict()
            if self.on_round_end is not None:
                try:
                    self.on_round_end(rep)
                except Exception:  # noqa: BLE001 —— 回调失败不得中断进化
                    pass
            if rep.committed and self.store is not None:
                #: ⛔ 走 SQL WHERE，**不要** "全量拉回再过滤" —— 后者为取
                #:    一条快照而把整张表的 payload 全 `json.loads` 一遍
                #:    （每轮提交都来一次 ⇒ 轮数越多越慢）。
                snap = self.store.snapshots(harness_id=rep.committed, limit=1)
                if snap:
                    from .genome import Harness as _H
                    cur = _H.from_dict(snap[-1]["payload"])
            #: ⛔ 老化读数必须在**循环内**取，且取的是"本轮末"的 harness。
            #:    放在 run() 之后 ⇒ 只能看到最后一轮 ⇒ 退化曲线退化成一个点
            #:    （有机制 ≠ 被走到，V5 第 2 条）。
            self._record_aging(rep, cur)

            #: ⛔ checkpoint 必须在 **`cur` 前进之后**写：写在前面会把
            #:    "本轮提交前的 harness"当成进度 ⇒ 续跑后本轮的收益被丢掉。
            #:    也必须写在**提前终止判断之前**：否则"无事可做/预算耗尽"
            #:    这两种正常结束反而没有 checkpoint（可它们同样是进度）。
            self.write_checkpoint(cur, reports, rounds_total)

            #: ⛔ 无事可做就停：不是失败，是"再跑也是空转"。实测 10 轮里
            #:    r2–r10 全在重复同一轮（base 恒 1.0 / advantage 恒 0），
            #:    继续跑只会把"空转"记成"努力过但没成功"。
            #:    ⛔ 必须留事件 —— 否则"为什么只跑了 3 轮"无从解释。
            idle_streak = idle_streak + 1 if rep.idle else 0
            if (self.cfg.stop_on_saturation and rep.idle
                    and idle_streak >= self.cfg.stop_after_idle_rounds):
                self.ledger.append(
                    "evolution.stopped",
                    {"round_no": rep.round_no, "rounds_requested": rounds,
                     "idle_streak": idle_streak, "reason": rep.revert_reason,
                     "note": "连续无事可做 ⇒ 提前终止（不是失败）"},
                    actor="evolution")
                break

            #: ⛔ 预算耗尽 ⇒ 停并**记账**。与"无事可做"是**两种原因**，
            #:    混在一起会把"钱花完了"记成"没有可做的"（后者是能力问题，
            #:    前者是预算问题，修法完全不同）。
            #: ⛔ 只在**轮末**检查挂钟：中途打断会留下半截账本（一轮写到
            #:    一半的账本读不出因果）。这是刻意取舍 —— 单轮耗时上限
            #:    由 UCI 的 `timeout_s` 管，不由这里管。
            why = self.budget.exhausted() if self.budget is not None else None
            if why:
                self.ledger.append(
                    "evolution.budget_stop",
                    {"round_no": rep.round_no, "rounds_requested": rounds,
                     "rounds_done": len(reports), "reason": why,
                     "budget": self.budget.to_dict(),
                     "note": "预算耗尽 ⇒ 提前终止（不是失败，也不是空转）"},
                    actor="evolution")
                break

        #: ── 源码侧清理 pass：每**次运行**一次（很贵，不许每轮扫）────
        #:    ⛔ 这是"12 个零消费方接口 / 10 个只写不读属性"那两个读数的家。
        #:      此前它们只活在 `caps cleanup propose` 这条**手工命令**里
        #:      ⇒ "系统的债是在涨还是在消"这个问题**没有任何时间序列**
        #:      能回答 —— 每次看都是一个人肉快照。这也正是"只能靠人回头
        #:      发现"的机制形态。
        #:    ⇒ 现在它自己产出提案、自己过闸、自己记账：人只剩"执行"这一步。
        self._source_cleanup_pass(reports)

        #: ⛔ 落点要兑现：写了 checkpoint 却没有任何读数 ⇒ "我开了续跑"
        #:    与"续跑真的可用"在外部无法区分（假仪表）。这里记一条事件，
        #:    让 `status` / 账本能回答"最近一次进度存到哪、存到第几轮"。
        if self.last_checkpoint:
            try:
                self.ledger.append(
                    "evolution.checkpoint",
                    {"path": self.last_checkpoint,
                     "round_no": reports[-1].round_no if reports else 0,
                     "rounds_total": rounds_total,
                     "rounds_done": len(reports),
                     "harness_id": getattr(cur, "harness_id", "") or "",
                     "resumable": bool(reports) and reports[-1].round_no
                     < rounds_total},
                    actor="evolution")
            except Exception:   # noqa: BLE001 —— 账本写不进 ⇒ 不影响返回
                pass
        return cur, reports
