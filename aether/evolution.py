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
from .generalization import VERDICTS
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


#: 失败证据 → **粗类别**。⛔ 与 `_ERROR_TO_KIND` 是两件事，别合并：
#:   `_ERROR_TO_KIND` 决定"缺什么能力"（走哪条修法），这张表决定
#:   "**给模型看**的粒度"（只给类别，不给检查名）。合并 ⇒ 粗化会连带
#:   改变弱点归类 ⇒ 那就不只是换考法，而是换判据了。
_FAILURE_MODE_HINTS: Tuple[Tuple[str, str], ...] = (
    ("checks_failed", "behavior_mismatch"),
    #: ⛔ 真实现代 Python 报的是 `ModuleNotFoundError: No module named 'x'`
    #:    —— 里面**没有** "import" 这个词 ⇒ 只写 "import" 会把最常见的
    #:    缺包错误判成 unknown（r62 契约 C3 抓到的正是这一条）。
    ("modulenotfound", "import_failed"),
    ("no module named", "import_failed"),
    ("importerror", "import_failed"),
    ("import", "import_failed"),
    ("timeout", "timeout"),
    ("traceback", "crash"),
    ("raise", "crash"),
)


def _failure_mode(text: str) -> str:
    low = (text or "").lower()
    for needle, mode in _FAILURE_MODE_HINTS:
        if needle in low:
            return mode
    return "unknown"


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

    def coarse_dict(self) -> Dict[str, Any]:
        """给提议器看的**粗化**版本：只给失效类别与数量，**不给检查名**。

        ⛔⛔ 存在理由（r62 实测）：`evidence` 里带的是
            `checks_failed:clamp_low,clamp_high,...` 这种**具体检查名**。
            qwen2.5:7b 拿到这份名单后，直接把 7 个名字逐字写进技能正文
            （`L3.skills.patch:textscore` = "修复 clamp_low,clamp_high, ..."）
            ⇒ dev 0/5 → 5/5 满血，held-out 一点没动 ⇒ 被判 `overfit`。

            检查名是**发展集**的信息。把它当输入，等于让模型抄答案：
            它抄得越好，越不可能给出跨环境的通用修法。

        ⛔ 只粗化**给模型的那一份**：`weakness.mined` 账本事件仍记原文
            （审计要能复查"当时到底看到了什么"），判据一律不粗化。
        """
        modes: Dict[str, int] = {}
        n_checks = 0
        for e in self.evidence:
            m = _failure_mode(e)
            modes[m] = modes.get(m, 0) + 1
            if e.startswith("checks_failed:"):
                n_checks += len([t for t in e[len("checks_failed:"):].split(",")
                                 if t.strip()])
        out: Dict[str, Any] = {
            "kind": self.kind,
            "topic": self.topic,
            "task_ids": self.task_ids,
            "n_failed": self.n_failed,
            "failure_modes": modes,
            "n_failing_checks": n_checks,
            "evidence_granularity": "coarse",
            "note": "判据名已按类别粗化：只给失效类别与数量，不给具体检查名。",
        }
        return out


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
    #: ⛔ 本轮**每个**闯过闸的候选的裁决摘要（r52）。空 = 没跑到这一步。
    #:    没有它，「闸拦了 1 个」与「闸拦了 3 个」在输出里长得一样 ⇒
    #:    「同轮试下一个」到底有没有真的发生过，无从验证（第 4 条）。
    gate_probes: List[Dict[str, Any]] = field(default_factory=list)
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
    #: ⛔ 本轮 held-out **判别力体检 / 自愈**的结果（r53；None = 未启用）。
    #:    "卷子还考得出差别吗"必须每轮可见 —— 它不可见时，连续几轮
    #:    `inconclusive` 会被读成"候选不好"，而真因是卷子已经满了。
    heldout_grow: Optional[Dict[str, Any]] = None

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
                #: ⛔ 「同轮试了几个候选过闸」必须出得来（r52）：不出的话
                #:    「被拦就换下一个」到底是发生了还是没发生，无从验证。
                "gate_probes": self.gate_probes,
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
                "genome_load_before": self.genome_load_before,
                "heldout_grow": self.heldout_grow}


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
    # ── 分层非支配存档（r62，第三根轴）────────────────────────────
    #: ⛔ **默认 0 = 不重采样**，与历史逐字同口径。开了才是新实验口径。
    #:
    #: 存在理由：`_run_round` 每轮只留一个 best，其余**通过准入闸**的
    #: 候选随 `cands` 一起被回收 —— 它们的 reward 与 blast 都已算过（评估
    #: 成本已付），却因为"这一轮不是第一名"被永久丢弃。下一轮基线变了，
    #: 同一个胶囊在新基线上值不值得提交**必须重测才知道**，而旧代码连
    #: 重测的机会都不给 ⇒ 单线爬坡。
    #:
    #: ⛔ 它换的是**候选来源**，不是判据：入档的候选照样过全部闸，重采样
    #: 的候选也要在**当前**基线上重算 reward（存档里的读数是旧基线的）。
    #: 与 `--ho-grow` / `--retry-every` 同族 ⇒ 必须进 run_config。
    archive_resample: int = 0
    #: 每层前沿上限。⛔ 有上限才叫存档：无上限 ⇒ 退化成"按旧 reward 全排"。
    archive_cap: int = 8
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
    #: ⛔⛔ **一轮内最多让几个候选去闯泛化闸**（r52）。默认 `1` = 现状：
    #:    best 被拦 ⇒ 整轮作废、回滚、下一轮**再提同一个**（因为基线没变，
    #:    发展集排序也不变 ⇒ best 恒定）。实测（r51 的 depth2+均衡口径）
    #:    6 轮里有 4 轮在提同一个 `fix_control_flow`、每次都判 partial，
    #:    而同一个发展集上 `+fix_unaryops` 是能过闸的（扫描 31 个修法子集
    #:    里 28 个 generalize）⇒ **代数是被"一次拦截吃掉一整轮"锁死的**。
    #:    ⛔ 这不放松闸：闸一条没少，只是"这个不行 ⇒ 试下一个（按发展集
    #:    排序的下一个）"，held-out 依旧**不参与排序**（H1 不动）。
    #:    ⛔ 默认 1 ⇒ 默认口径零影响、历史基线可比（第 33/34 条）。
    gate_tries: int = 1
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
    #: ⛔⛔ **held-out 判别力自愈**（r53）。默认 **关** ⇒ 与历史逐字同口径。
    #:
    #:    r52 把「层内可提升空间 < 3 ⇒ 不作反证」修好之后，暴露出下一个
    #:    天花板：当 held-out **每层**都没空间时，闸判 `inconclusive` 并
    #:    报「出路：补 held-out 的题数，不是放宽判据」—— 但**系统自己
    #:    没有这个出口**。实测（4 环境 / depth2 / 6 轮）：轮 2 有 +0.4 的
    #:    发展集增益可用，轮 2–4 三轮逐字重复地被同一条理由拦下。
    #:
    #:    ⇒ 开它：每轮用**当前基线**体检 held-out，某层能涨的题少于
    #:      `min_level_headroom` 就补采样（只补"基线也做不对"的题 ⇒
    #:      与候选无关 ⇒ 不给任何候选优待；H1 一条没动）。
    #:    ⛔ 它改的是**考卷**，不是判据 ⇒ 开了就是新口径，不与历史同表比。
    ho_grow: bool = False
    #: 补采样的封顶（合成 max_n 与总题数）。⛔ 必须封顶：合成要真跑变异体
    #:    （执行一次判据）⇒ 不封顶就是把挂钟交给循环自己。
    ho_grow_max_n: int = 64
    ho_grow_max_tasks: int = 96
    #: ⛔⛔ **给提议器的证据粒度**（r62 新口径）。默认 `exact` = 历史逐字同口径。
    #:
    #:    `exact`  —— 弱点里带具体检查名（`checks_failed:clamp_low,...`）。
    #:    `coarse` —— 只带失效**类别**与数量（`behavior_mismatch: 3`、
    #:                `n_failing_checks: 7`），**不出现任何检查名**。
    #:
    #:    存在理由（实测）：`exact` 下 qwen2.5:7b 把 7 个检查名逐字抄进
    #:    技能正文 ⇒ dev 0/5→5/5，held-out 0.0→0.0 ⇒ `overfit` 被拦。
    #:    检查名是**发展集**的信息，等于给模型抄答案；抄得越好越不会写
    #:    跨环境的通用修法。
    #:
    #:    ⛔ 它换的是**给模型的视图**，不是判据：`weakness.mined` 账本事件
    #:       仍记原文，闸一条没少 ⇒ 但模型输入变了 ⇒ 属新口径，必须进
    #:       run_config（与 `--ho-grow` 同族）。
    evidence: str = "exact"
    #: ⛔⛔ **定点修法能不能提交**（r62 新口径）。默认 False = 历史逐字同口径。
    #:
    #:    背景：`patch:<env>` 只对点名的环境生效 ⇒ 它在 held-out 上 0 增益是
    #:    **构造必然**。旧判据拿这个 0 判 `overfit`（"讨好判据"）⇒
    #:    环境专属的定点修法**永远提交不了**，哪怕它在发展集上是真修好了。
    #:    实测（10-20）：dev 0/5→5/5、held-out 0.0→0.0 ⇒ overfit ⇒ 连续
    #:    6 轮空转，`committed=null`。这不是闸严，是**把构造必然当证据**。
    #:
    #:    开了之后：只碰定点技能（`L3.skills.patch:<env>`）、且**没碰**
    #:    held-out 那个环境的候选 ⇒ 判 `scoped`（放行，但不是 `generalize`：
    #:    它没有证明泛化，只是"没资格用这道闸来判"）。非回归闸与能力地板
    #:    照常生效。
    #:
    #:    ⛔ 沾了路由 / 工具 / 通用技能 / 记忆策略的候选**不受影响** ——
    #:    那些层对 held-out 是有影响的，0 增益就真的是没泛化。
    allow_scoped_commit: bool = False


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
    #: ⛔ 类级兜底（同上）：本次运行里「因判据无从判别而拦下」的探测。
    #:    它是**运行级**读数，与跨运行的死路账刻意分开（第 16 条）。
    _no_evidence_hits: Optional[list] = None
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
                 cleanup: Optional[Any] = None,
                 archive: Optional[Any] = None):
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
        #: ⛔ 分层非支配存档（r62）。**默认 None = 不入档、不重采样** ⇒
        #:    与历史逐字同口径（账本里一条 `archive.*` 事件都不会有）。
        #:    ⛔ 刻意**不**在这里自动构造（与 `cleanup` 相反）：存档会
        #:    改变候选来源，默认开就等于悄悄换了实验口径。
        self.archive = archive
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
                    # ⛔ 与 `_runs_from_verdicts` **同源**（r59）：以前两处
                    #    各写一份，后者漏了 fallback ⇒ 弱点恒无证据。
                    "error": self._verdict_error(v),
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
    def _verdict_error(v: "Verdict") -> str:
        """失败原因的**唯一**构造口径（第 13 条：单一实现）。

        ⛔⛔ r59 实测：两处曾经**不同源**，后果是知识层整条链产出空壳 ——

          · `evaluate_dev`：`v.error or "checks_failed:" + 失败检查名`
          · `_runs_from_verdicts`：`v.error or ""`（**没有 fallback**）

        而 `run_round` 的 MINE 步骤**只**走后者（1630 行）⇒ 弱点挖掘拿到
        的 `error` **恒为空** ⇒ `w.evidence` 永不 append ⇒ 知识条目退化成
        `missing_skill / t0：`（**19 字**，`< min_content_len 60`）⇒ 加载
        侧拒载、检索侧无人用。生产实测：**111/173（64%）**的弱点无证据、
        28 次 `knowledge.ingested` 里 **23 次是空壳**；库里 3 条 learner
        条目中，唯一那条空壳的 `access_count=1`，而两条有内容的却是
        **1814 / 1602** —— 空知识在事实上从没被用过。

        ⛔ 取证（r59，同 seed）：30 条 run payload 的 `error` **100% 为空**，
        但 15 条失败裁决的 `checks` 里**全都有**失败项（如
        `["clamp_low","clamp_high","clamp_mid"]`）⇒ fallback 能补 **15/15**。
        另外 15 条 `all_pass` 是 `passed=true` 的 run，不进弱点挖掘。

        ⛔ 为什么不用 `v.error` 就算了：判据层**只在崩溃/异常时**填
        `error`，普通「检查没过」不填 ⇒ 只读 `v.error` 等于只收崩溃信号，
        而进化中最常见的失败恰恰是「跑了、但检查没过」。
        """
        if getattr(v, "error", ""):
            return v.error
        failed = ",".join(c.name for c in (getattr(v, "checks", ()) or ())
                          if not getattr(c, "passed", True))
        return ("checks_failed:" + failed) if failed else ""

    def _evidence_for_prompt(self, w: Weakness) -> Dict[str, Any]:
        """按 `cfg.evidence` 决定喂给**提议器**的弱点视图。

        ⛔ 只影响提议器输入；`weakness.mined` 账本事件始终写 `w.to_dict()`
           的原文（审计要能复查"当时到底看到了什么"），闸与判据一条不动。
        ⛔ 未知取值 ⇒ 按 `exact` 处理并**留痕**：静默回退会让 run_config
           里的口径与真实行为不一致（第 24 条：缺字段被读成 None 那一类）。
        """
        mode = str(getattr(self.cfg, "evidence", "exact") or "exact").lower()
        if mode == "coarse":
            return w.coarse_dict()
        return w.to_dict()

    @staticmethod
    def _runs_from_verdicts(vs: List[Verdict], h: Harness) -> List[RunRow]:
        """把本轮发展集判定包装成 RunRow，供 `mine_weaknesses` 使用。

        ⛔ 为什么不再翻 store：见 `run_round` 第 2 步的说明。这里 Only 用
        本轮判定 ⇒ "弱点"不可能来自上一次运行。
        """
        from .store import RunRow
        out: List[RunRow] = []
        for i, v in enumerate(vs):
            #: ⛔ `topic` 原来只找 `topic` / `env`，而判据层填的是
            #:    **`env_id`**（r59 取证：30 条 evidence 里 `topic`/`env`/
            #:    `task_id` **100% 缺失**，`env_id` 30/30 存在且全为
            #:    `textscore`）⇒ topic 恒为空 ⇒ 退化成 task_id ⇒ 再退化成
            #:    序号 `t0`/`t3` ⇒ 弱点按**任务序号**分桶（每条 n_failed=1），
            #:    而不是按**环境**分桶。生产实测 173 个弱点里 111 个的 topic
            #:    就是 `t0`(77)/`t3`(33)/`t7`(1) 这种序号。
            #:    ⇒ 补 `env_id` 后 topic 恢复成真实环境名（实测 `textscore`），
            #:      与生产那两条**有内容**的化石知识
            #:      （`missing_skill / textscore：checks_failed:...`）吻合 ——
            #:      那两条正是 topic 还没退化时留下的。
            #:    ⛔ 按环境分桶会让 `n_failed` 变大（多条失败聚到一起）⇒
            #:      知识条目的 `evidence[:2]` 才有得拼（顺带缓解过短问题）。
            topic = str(v.evidence.get("topic") or
                        v.evidence.get("env") or
                        v.evidence.get("env_id") or "")
            out.append(RunRow(
                run_id=f"probe:{h.harness_id}:{i}",
                harness_id=h.harness_id,
                task_id=str(v.evidence.get("task_id") or topic or f"t{i}"),
                reward=(1.0 if v.passed else 0.0), passed=bool(v.passed),
                gate_ok=True,
                #: ⛔ `error` 走**同源**的 `_verdict_error`（r59）：这里原来
                #:    写 `v.error or ""` ⇒ 100% 为空 ⇒ 挖出来的弱点没有证据
                #:    ⇒ 知识条目是 19 字空壳（`min_content_len=60`）⇒
                #:    加载侧拒载、检索侧零引用。与 `evaluate_dev` 同一函数。
                payload={"error": EvolutionLoop._verdict_error(v),
                         "topic": topic, "evidence": v.evidence}))
        return out

    # ── 死路账：同一候选在同一环境对上被拦了多少次 ──────────────────
    #: ⛔⛔ **不可判别的裁决**（第 11 条：不可判别 ≠ 有罪）。
    #:
    #:    `inconclusive` 的语义是「这套 held-out 对这道题**无从判别**」——
    #:    它是**机器的**判别力问题，不是**候选的**品行问题。把它计进死路
    #:    streak ⇒ 「因为看不清，所以判它有罪」。
    #:
    #:    ⛔ r59 实测（seed=7 / 4 环境 / depth=1 / rounds=6，同一次运行对照）：
    #:      · 默认口径：`fix_literals` 连得 4 次 `inconclusive`
    #:        （`headroom={'easy':0,'hard':2}` ⇒ `all_thin` ⇒ 每次都拦），
    #:        streak=4 ⇒ 判成死路 ⇒ 冷却 ⇒ **晋升成陈述性知识**；
    #:      · 同一 seed 只开 `--ho-grow`（补 4 条 held-out ⇒ hard 层 2→6）
    #:        ⇒ 它第 2 轮就 `generalize` 并 commit（advantage **1.7321**）。
    #:      ⇒ 那 4 次「被拦」**全是误判**：候选从来不是死路，是尺子太短。
    #:        误判还写进了基因组（`world_facts`）⇒ 跨代继承，换运行也不掉。
    #:
    #:    ⛔ 处置：**不计入也不归零**（它不是成功的证据，不该清零别人的
    #:      账；也不是失败的证据，不该给自己累加）。单独计数 ⇒ 出口可见。
    #:    ⛔ 判据**没放松**：`overfit` / `partial`（有证据的讨好与挑食）
    #:      一个字没改，照旧 +1。
    NO_EVIDENCE_VERDICTS = frozenset({"inconclusive"})

    def _deadend_accum(self, ev: Any, streak: Dict[str, int]) -> None:
        """把**一条**事件累加进死路账（增量与全量共用同一份累加逻辑）。

        ⛔ 只 factored 出"累加"，不 factored 出"读"：读只有 `iter_events`
        一个实现，两条路径（全量 / 增量）都必须走它 —— 多一份读法就会
        在坏行、编码、偏移上漂移，而漂移是静默的。

        ⛔ 累加的是**连续**计数（streak），不是累计总数：
            · `generalize` ⇒ 归零（成功一次就重新给机会）；
            · **有证据**的负面裁决（`overfit` / `partial`）⇒ +1；
            · **不可判别**（`inconclusive`）⇒ **不动**（见 `NO_EVIDENCE_
              VERDICTS` 的实测）。
          `streak` 是"连续"语义的**充分统计量**：正序流式扫一遍就能维护，
          不需要重放历史，也不需要额外记"曾否成功过"的集合 —— 所以增量
          路径与全量路径在数学上严格等价（不存在"增量算出来的值和全量
          不一样"这条漂移路径）。
        """
        if ev.kind != "gen.probe":
            return
        p = ev.payload or {}
        key = f"{p.get('capsule', '')}|{p.get('env_pair', 'unknown')}"
        v = str(p.get("verdict"))
        #: ⛔⛔ **读不懂的裁决单独计数**（2026-10-21 实测，README 134）。
        #:
        #:    坑在 `str()`：字段缺失时 `str(None)` == `"None"` —— 它既不是
        #:    `generalize`，也不在 `NO_EVIDENCE_VERDICTS` ⇒ 原实现把它
        #:    **静默算成「被拦 +1」**（污染冷却 streak，且不报错）。
        #:    判据必须拿**原始值**判：`in 集合` / `is None`，不能先 `str()`。
        #:
        #:    ⛔ 现实里真会缺：同一裁决在 `gen.probe` 里是扁平
        #:      `payload.verdict`，在 `harness.commit` 里是嵌套
        #:      `payload.generalization.verdict` —— 两种形状并存（实测
        #:      140/140 与 0/120 互补），写入方一漂移，这里就会读到 None。
        #:
        #:    ⛔ 处置：**只让异常可见，不改判据**。`streak` 照旧 +1
        #:      （fail-closed，历史决策可复现）；`unknown` 计数器让它
        #:      不再隐形。要改 streak 语义是另一件事，得有自己的交叉表。
        if p.get("verdict") not in VERDICTS:
            self._ensure_deadend_stats()
            self._deadend_stats["unknown"] = (
                self._deadend_stats.get("unknown", 0) + 1)
        if v in self.NO_EVIDENCE_VERDICTS:
            #: ⛔ 单独计数（第 16 条）：「因看不清而没判」与「判了没过」
            #:    是两件事，合并成一个数 ⇒ 出口到底被用了几次永远读不出来。
            self._ensure_deadend_stats()
            self._deadend_stats["no_evidence"] = (
                self._deadend_stats.get("no_evidence", 0) + 1)
            return
        #: ⛔ 严格匹配 `"generalize"`（不大小写折叠、不把 `partial` 算成功）：
        #:    这是改造前就有的口径，改判据属于**另一件事**，要有自己的交叉
        #:    表。当前口径下 `partial`/`GENERALIZE` 都计入"被拦"（+1）。
        if v == "generalize":
            streak[key] = 0
        else:
            streak[key] = streak.get(key, 0) + 1

    def _ensure_no_evidence(self) -> None:
        """惰性初始化「不可判别」名单（与 `_ensure_deadend_stats` 同形态）。

        ⛔ 必须惰性：`__new__` / pickle 构造的对象拿不到 `__init__` 的赋值
            ⇒ 类级兜底是 `None` ⇒ 直接 `append` 会 AttributeError。
        """
        if self._no_evidence_hits is None:
            self._no_evidence_hits = []

    @staticmethod
    def _no_evidence_repeats(hits) -> "Tuple[int, List[str]]":
        """「不冷却」的代价读数：同一候选**第 2 次及以后**再被判无证据 = 重探。

        ⛔ 为什么单列一个函数：这个数要能单测（契约 V1 组），夹在事件构造里
            就只能靠端到端跑一轮才能看到 —— 而端到端 2 分钟起，没人会为了一个
            计数跑它 ⇒ 它就会静默腐烂。

        ⛔ 语义是「重探」不是「总数」：第一次探测在任何口径下都会发生，
            只有第 2 次起才是「因为不冷却才多出来的」。写成总数会把恒定的
            那一次也算成代价 ⇒ 读数虚高、且在只有一个候选时也永远 ≥1
            （第 57 条：看不出「零代价」的恒真读数）。
        """
        seen = set()
        extra = 0
        caps: "List[str]" = []
        for h in hits or []:
            c = h.get("capsule")
            if c in seen:
                extra += 1
                if c not in caps:
                    caps.append(c)
            else:
                seen.add(c)
        return extra, sorted(caps)

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

    @staticmethod
    def _deadend_pair(env_pair: str) -> str:
        """知识侧的「环境对」表示 —— 必须与**账本侧**同源（r59 项 3）。

        ⛔⛔ 旧写法在这里做了 `env_pair_key(*s.split("|", 1))`（当值里含
            `|` 时）—— 那是把「**有向**的发展集→held-out 对」当成
            「**无序**的环境集合」来规范化，语义是错的，而且**会改写 key**：

                env_pair = 'zeta|alpha->ho'      （两个 dev 环境）
                知识侧 → 'alpha->ho|zeta'        ← 被改写了
                账本侧 → 'c1|zeta|alpha->ho'     （`_deadend_accum` 用原值）

            ⇒ 同一个 (候选, 环境对) 在两处是**两个 key** ⇒ 「继承的知识
              优先于本轮派生量」（`_facts_deadend_streak`）会查不到自己刚
              写进去的东西。r58 把这类现象记成「`env_pair` 取值域漂移」，
              真机制是**这个规范化**，不是历史值变了。

        ⛔ 为什么敢直接返回原值：`env_pair` 的唯一生产源是
           `GeneralizationGate.env_pair`（`f"{'|'.join(devs)}->{ho}"`），
           它**已经是规范形式**（dev 侧 sorted、`->` 定方向）⇒ 再规范化
           一次只会引入第二种表示。
        ⛔ 零影响：单 dev 环境（现状，也是生产的全部 15 次运行）时新旧
           两法**逐字相同** ⇒ 已存的 key 一个都不会失配。
        """
        return str(env_pair)

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
        key = W.fact_key(W.SCOPE_DEADEND, capsule, self._deadend_pair(env_pair))
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
        key = W.fact_key(W.SCOPE_DEADEND, capsule, self._deadend_pair(env_pair))
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

    def _archive_offers(self, h: Harness, capsules: List[Capsule],
                        scored: List[Candidate], round_no: int,
                        base_reward: float, env_pair: str) -> None:
        """把本轮**通过准入闸**的解写进分层存档（r62）。

        ⛔ 只入 `scored`（`gate_passed and n_verdicts > 0`）：没被真评估过
           的候选没有 reward，入档就是"零信息条目" —— 它们会在帕累托
           比较里以 0 分挤掉真解（存档的地板被拉平，前沿变噪音）。
        ⛔ 每条都记账（含被支配/重复的）：不记 ⇒ "存档里为什么只有 2 条"
           无从分辨是"只有 2 条够格"还是"40 条都被支配了"。
        """
        from .archive import Entry, capsule_to_dict
        by_name = {c.name: c for c in capsules}
        offered = added = 0
        skips: List[Dict[str, str]] = []
        for c in scored:
            cap = by_name.get(c.capsule_name)
            if cap is None:
                #: ⛔ 找不到 capsule ⇒ 不入档（存空壳等于存噪音），但必须
                #:    留痕：这说明 cands 与 capsules 脱节了，是真缺陷。
                self.ledger.append(
                    "archive.capsule_missing",
                    {"round_no": round_no, "capsule": c.capsule_name},
                    actor="evolution")
                continue
            try:
                after = apply_capsule(h, cap)
            except Exception as e:                    # noqa: BLE001
                self.ledger.append(
                    "archive.apply_failed",
                    {"round_no": round_no, "capsule": c.capsule_name,
                     "error": f"{type(e).__name__}: {e}"}, actor="evolution")
                continue
            layers = tuple(c.blast.get("layers_touched") or ()) \
                or cap.blast_layers()
            entry = Entry(
                capsule=capsule_to_dict(cap), harness_id=c.harness_id,
                harness=after.to_dict(), layers=layers,
                reward=float(c.reward), load_gain=float(c.load_gain),
                size_delta=int(c.blast.get("size_delta") or 0),
                round_no=round_no, baseline_reward=float(base_reward),
                #: ⛔ 记**基线身份**而不只是数值：0.0 这个数值在"还没提交"的
                #:    那些轮里每轮都一样 ⇒ 靠数值判断新旧会把所有条目混成
                #:    同一个基线（r62 实测 n_picked 恒 0 的一半原因）。
                baseline_harness_id=h.harness_id,
                env_pair=env_pair, accepted=bool(c.accepted))
            offered += 1
            try:
                res = self.archive.add(entry)
            except Exception as e:                    # noqa: BLE001
                self.ledger.append(
                    "archive.add_failed",
                    {"round_no": round_no, "capsule": c.capsule_name,
                     "error": f"{type(e).__name__}: {e}"}, actor="evolution")
                continue
            if res.get("added"):
                added += 1
            else:
                skips.append({"capsule": c.capsule_name,
                              "reason": str(res.get("reason") or "")})
        self.ledger.append(
            "archive.offer",
            {"round_no": round_no, "harness_id": h.harness_id,
             "offered": offered, "added": added, "skipped": skips[:8],
             "report": self.archive.to_report()}, actor="evolution")

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

        # 1.5) **held-out 判别力体检**（r53，默认关）
        #
        #      ⛔ 位置有讲究：必须在**基线评估之后、提议之前**。
        #        · 在基线之后 —— 体检问的是"基线还剩几条做不对"，
        #          没有这一步的判定就得再跑一遍（重复成本）；
        #        · 在提议之前 —— 补题用的是**当前基线**的属性，
        #          此时还没有任何候选 ⇒ "给某个候选优待"在结构上不可能。
        #
        #      ⛔ 为什么默认关：它换的是**考卷**（held-out 的题数），
        #        不是判据 ⇒ 开了就与历史基线不可比（第 33/34 条）。
        if bool(getattr(self.cfg, "ho_grow", False)) and self.heldout is not None:
            try:
                gr = self.heldout.ensure_headroom(
                    h, self.runner, round_no=round_no)
            except Exception as e:                             # noqa: BLE001
                #: ⛔ 补采样失败**不许静默**：与"不用补"必须可分辨
                #:    （第 10 条：环境故障被读成"等价"是这一类病）。
                gr = {"enabled": True, "grew": 0,
                      "error": f"{type(e).__name__}: {e}"}
            rep.heldout_grow = gr
            rep.ledger_seqs.append(self.ledger.append(
                "heldout.grow",
                {"round_no": round_no, "harness_id": h.harness_id,
                 "env_pair": self.heldout.env_pair, **gr},
                actor="gate").seq)

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
                self._evidence_for_prompt(w), h, n=self.cfg.group_size))
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

        # 3c) **存档重采样**（r62，默认关）：把历轮"够格但没被选中"的解
        #     重新投进这一轮。⛔ 它们**不豁免任何闸**，且必须在**当前**
        #     基线上重算 reward —— 存档里记的 `reward_at_store` 是旧读数，
        #     只用于"挑哪几条回来"，绝不参与判据（见 archive.py 纪律③）。
        n_res = int(getattr(self.cfg, "archive_resample", 0) or 0)
        if self.archive is not None and n_res > 0:
            have = {c.name for c in capsules}
            try:
                picked = self.archive.resample(
                    n_res, exclude_harness=(h.harness_id,),
                    exclude_capsules=tuple(have),
                    #: ⛔ 与 `exclude_harness` 不是一回事：这个排除的是
                    #:    "读数就是在**当前**基线上量的"（无重测价值）。
                    exclude_baseline=(h.harness_id,))
            except Exception as e:                    # noqa: BLE001
                picked = []
                self.ledger.append(
                    "archive.resample_failed",
                    {"round_no": round_no, "error": f"{type(e).__name__}: {e}"},
                    actor="evolution")
            from .archive import capsule_from_dict as _cap_from_dict
            restored: List[Any] = []
            for g in picked:
                try:
                    cap = _cap_from_dict(g["capsule"])
                except Exception as e:                # noqa: BLE001
                    self.ledger.append(
                        "archive.restore_failed",
                        {"round_no": round_no, "error": f"{type(e).__name__}: {e}"},
                        actor="evolution")
                    continue
                if cap.name in have or not cap.genes:
                    continue
                restored.append(cap)
                have.add(cap.name)
            if restored:
                capsules += restored
            rep.ledger_seqs.append(self.ledger.append(
                "archive.resample",
                {"round_no": round_no, "harness_id": h.harness_id,
                 "n_wanted": n_res, "n_picked": len(picked),
                 "n_restored": len(restored),
                 "capsules": [c.name for c in restored],
                 #: ⛔ 打上 stale：这批评分**来自旧基线**，账本读者要先
                 #:    看到这个标记，才不会把"存档里当初很好"当成"现在很好"。
                 "stale": [{"capsule": g["capsule"].get("name"),
                            "reward_at_store": g.get("reward_at_store"),
                            "baseline_reward": g.get("baseline_reward"),
                            "stored_at_round": g.get("stored_at_round")}
                           for g in picked],
                 "archive": self.archive.to_report()},
                actor="evolution").seq)

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
        #: ⛔ `accepted` 必须在**这里**就打上：7.5 只在装了 held-out 时才跑，
        #:    没装 held-out 的运行（`--heldout off` 与若干契约场景）根本走不
        #:    到那儿 ⇒ 候选永远不会被标记 ⇒ 提交照旧但"谁被接受了"在报告里
        #:    读不出来（实测契约 D2/D7b/D7c 当场转红）。
        if best is not None:
            best.accepted = True
            best.reason = "accepted"

        #: ⛔ 准入通过的**全部**候选（按同一把尺子排序），不再只留一个。
        #:    原实现到这里只留 `best` ⇒ 它被闸拦下就等于"这一轮结束"，
        #:    而下一轮的排序**完全相同**（基线没变、发展集没变）⇒ 又提它
        #:    ⇒ 同一条死路被反复走（r51 实测 6 轮里 4 轮提同一个候选）。
        eligible = sorted(
            (c for c in scored if c.reason in ("", "accepted")),
            key=lambda c: (c.reward - base_reward, c.load_gain), reverse=True)
        #: ⛔ 排序键与上面选 best 的时刻**必须一致**：两把尺子 ⇒ "同轮
        #:    试下一个"试的顺序就不是发展集顺序 ⇒ H1 会被悄悄绕过。
        front = best

        # 7.5) 跨环境泛化闸 —— **在 best 选出之后**才跑。
        #      ⛔ 顺序不能反：held-out 的 reward 一旦参与选 best，
        #      它就变成了第二张发展集，闸自我取消（见 generalization.py H1）。
        #
        #      ⛔⛔ r52：被拦**不等于这一轮结束**。按 `gate_tries` 依次让
        #      排序里的下一个候选去闯闸（held-out 仍然**不参与排序** ——
        #      顺序来自发展集，闸只做准入）。
        #      ⛔ 默认 `gate_tries=1` ⇒ 与改动前**逐字等价**（历史可比）。
        overfit_reason = ""
        if best is not None and self.heldout is not None:
            tries = max(1, int(getattr(self.cfg, "gate_tries", 1) or 1))
            queue = eligible[:tries] or [front]
            probes: List[Dict[str, Any]] = []
            passed: Optional[Candidate] = None
            first_blocked: Optional[Dict[str, Any]] = None
            for cand in queue:
                #: ⛔ 把胶囊对象本身也带上：泛化闸要判它的**能力范围**
                #:    （只看 `patch:<env>` ⇒ 别的环境上 0 增益是构造必然）。
                _cp = next(cp for cp in capsules
                           if cp.name == cand.capsule_name)
                new_h_probe = apply_capsule(h, _cp)
                if cand.is_cleanup:
                    #: ⛔ 清理走泛化闸的**退化面**（`probe_removal`：held-out
                    #:    不许变差）。走 `probe()` 必得 `no_gain` ⇒ blocks
                    #:    ⇒ 清理永远提交不了 —— 那不是闸严，是**用错了闸**：
                    #:    overfit 问的是"涨的是不是真的"，而清理根本不涨。
                    pr = self.heldout.probe_removal(h, new_h_probe,
                                                    self.runner)
                else:
                    pr = self.heldout.probe(
                        h, new_h_probe, self.runner,
                        dev_gain=cand.reward - base_reward,
                        # ⛔ 同层对照：没有它，"能力边界"会被误判成"挑食"，
                        #    通用修法将永远过不了闸 ⇒ 进化循环永久空转。
                        dev_by_difficulty=cand.dev_by_difficulty or None,
                        #: ⛔ 默认 False ⇒ 与历史逐字同口径（env 专属胶囊
                        #:    在 held-out 上 0 增益仍判 overfit）。
                        capsule=_cp,
                        allow_scoped=bool(
                            getattr(self.cfg, "allow_scoped_commit", False)))
                cand.generalization = pr.to_dict()
                #: ⛔⛔ 每个被拦的候选**都要**记 `gen.probe`：死路账的维度
                #:    是 (候选, 环境对)，而此前只有 `best` 会被记 ⇒ 其余
                #:    候选**永远**进不了死路账 ⇒ 冷却只对"每轮第一名"生效
                #:    ⇒ 冷却几乎从不触发（这也是同一个候选能连提 4 轮的
                #:    另一半原因）。
                rep.ledger_seqs.append(self.ledger.append(
                    "gen.probe",
                    {"round_no": round_no, "capsule": cand.capsule_name,
                     "harness_id": cand.harness_id,
                     #: ⛔ 环境对是死路冷却的维度：缺了它，冷却只能
                     #:    按候选全局生效 ⇒ 换环境后被误伤。
                     "env_pair": env_pair,
                     "try_index": len(probes), "queue_size": len(queue),
                     #: ⛔ 纯**读数**：`eligible` 的真实大小。
                     #:    `queue_size = min(tries, len(eligible))` ⇒ 默认
                     #:    `tries=1` 时它恒为 1，**看不出**这一轮到底有几个
                     #:    候选够格 ⇒ 判断「值不值得开 `--gate-tries`」在
                     #:    账本里读不出来。`eligible` 在被 `[:tries]` 截断
                     #:    前已完整算出 ⇒ 任何 `tries` 下都可知。
                     #:    ⛔ 只进账、不参与任何判据（零行为影响）。
                     "n_eligible": len(eligible),
                     **pr.to_dict()}, actor="gate").seq)
                probes.append({"capsule": cand.capsule_name,
                               "verdict": pr.verdict,
                               "blocks": bool(pr.blocks),
                               "dev_gain": round(cand.reward - base_reward, 4),
                               "heldout_gain": round(pr.heldout_gain, 4)})
                #: ⛔ 本次运行的「因看不清而拦」累计（r59）。
                #:    它是**运行级**的，不是死路账级的：死路账要跨运行可比，
                #:    而这条只回答"这一次跑，判别力拖了多少后腿"。
                if str(pr.verdict) in self.NO_EVIDENCE_VERDICTS:
                    self._ensure_no_evidence()
                    self._no_evidence_hits.append({
                        "round_no": round_no, "capsule": cand.capsule_name,
                        "headroom": dict(pr.headroom or {}),
                        "heldout_gain": round(pr.heldout_gain, 4)})
                if not pr.blocks:
                    passed = cand
                    cand.accepted = True
                    cand.reason = "accepted"
                    break
                cand.reason = f"{pr.verdict}: {pr.reason}"
                #: ⛔ 被闸拦下 ⇒ 清掉准入时打上的 accepted（否则"被拦"
                #:    与"被接受"会在同一轮里同时成立）。
                cand.accepted = False
                if first_blocked is None:
                    first_blocked = {"cand": cand, "pr": pr}
            rep.gate_probes = probes
            if passed is not None:
                best = passed
                rep.generalization = best.generalization
            else:
                #: 全被拦 ⇒ 裁决取**排序第一**那个（与改动前语义一致），
                #: 但每个候选的理由都留在自己身上（账本里看得见"试了几个"）。
                best = None
                fb = first_blocked
                if fb is not None:
                    cand0, pr0 = fb["cand"], fb["pr"]
                    overfit_reason = pr0.reason
                    cand0.accepted = False
                    rep.generalization = pr0.to_dict()
                    #: ⛔ 同轮试过但没过的，理由要能区分"我排第二，没轮到"
                    #:    与"我也被拦了" ⇒ 否则观测侧会把它们读成"没探测"。
                    for cand in queue:
                        if cand is not cand0:
                            cand.accepted = False

        rep.candidates = cands

        # 7.7) **入档**（r62，默认关）：把这一轮"通过准入闸"的解留在存档里。
        #      ⛔ 放在这里而不是选 best 之后：`accepted` 要等泛化闸跑完才
        #      最终确定（gate_tries > 1 时会改），早入档 ⇒ 存进去的
        #      accepted 与最终裁决不一致（第 85 条：状态名必须等于事实）。
        #      ⛔ 只入 `scored`（gate_passed 且真跑过 verdict 的）：没被
        #      评估过的候选没有 reward，存进去就是"零信息条目"，会把
        #      前沿的地板拉平（= 用 0 分挤掉真解）。
        if self.archive is not None:
            self._archive_offers(h, capsules, scored, round_no, base_reward,
                                 env_pair)

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

        #: ── r59：判别力不足的**运行级**出口 ──────────────────────────
        #:    `all_thin`（每层可提升空间都 < 门槛）⇒ `inconclusive` ⇒ 拦。
        #:    系统早在 r52 就知道这件事、r53 也修好了出路（`--ho-grow`
        #:    补 held-out 卷子），但出路**默认关** ⇒ 操作者看到的是
        #:    "连续几轮被拦、最后 saturated"，而**看不到**"是尺子太短"。
        #:    ⛔ 这里不自动开它（换考卷 ⇒ 与历史不可比，第 33/34 条），
        #:      只把「有多少增益是被判别力吃掉的」和那条真出路写进账本。
        #:    ⛔ 第 35b 条：reason 里写的出路，系统必须真有 ⇒ `--ho-grow`
        #:      是 CLI 上真实存在的开关（r59 实测：开了 commit 1→2、
        #:      revert 5→0，同一个候选 advantage 1.7321 从"死路"变提交）。
        hits = self._no_evidence_hits or []
        if hits:
            _gains = [h.get("heldout_gain") or 0.0 for h in hits]
            _n_rep, _rep_caps = self._no_evidence_repeats(hits)
            try:
                self.ledger.append(
                    "evolution.no_evidence",
                    {"rounds_done": len(reports),
                     "n_blocked": len(hits),
                     "capsules": sorted({h.get("capsule") for h in hits}),
                     #: ⛔ 代价读数（r59-项4）：把 inconclusive 摘出死路账
                     #:    ⇒ 候选不再进冷却 ⇒ 每轮重探。这个代价**无界**
                     #:    （正比于「看不清的候选 × 轮数」）⇒ 必须进账本。
                     "repeated_probes": _n_rep,
                     "repeated_capsules": _rep_caps,
                     "max_heldout_gain": round(max(_gains), 4),
                     "min_headroom": min(
                         [min(h.get("headroom").values())
                          for h in hits if h.get("headroom")] or [0]),
                     "note": "这些拦截是 held-out **判别力不足**（不可判别），"
                             "不是候选有问题 ⇒ 不计入死路账（第 11 条）。"
                             "代价：不进冷却 ⇒ 每轮重探（repeated_probes 次）"
                             "——ABBA 实测（同 seed，修前/修后各 2 次）"
                             "probe 5→6、eval.verdict 115→120、"
                             "耗时 79.2s→81.7s（+3.0%，组内方差 ±19% ⇒ "
                             "在噪声内）。⛔ 当前**没有封顶**机制：代价随"
                             "「看不清的候选 × 轮数」线性增长，"
                             "多候选场景会比这次实测更大。"
                             "要消除代价得先消除成因 ⇒ --ho-grow",
                     "escape_hatch": "--ho-grow（补 held-out 卷子；"
                                     "默认关，开了与历史基线不可比）"},
                    actor="gate")
            except Exception:   # noqa: BLE001
                pass

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
