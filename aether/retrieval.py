"""检索判别力基准 + 嵌入选型闸。

## 为什么必须有它

接真嵌入（nomic / OpenAI text-embedding）时，最自然的想法是"真模型一定比
hashing trick 强"。**实测不是这样**：在本机 nomic-embed-text 上，

    去重~删除重复数据（同义）     hashing 0.585   nomic 0.686
    限流~排序稳定性  （非同义）    hashing 0.000   nomic 0.776

nomic 把**非同义对也判成 0.776**，绝对相似度全面虚高。如果只看"相似度变高了"
就切换，等于把判别力换成了一团糊。绝对余弦值**跨 embedder 不可比** ——
这与 V5 那条"分数不可跨口径比较"是同一条纪律。

所以切换的依据只能是**排序判别力**，不能是相似度高低：

    recall@1  —— top1 是不是正例
    MRR       —— 正例排第几
    margin    —— sim(正例) − max(sim(干扰))   ← 判别力的本体

## 闸的规则

新 embedder 只有在 **recall@1 严格更高**、且 **margin 不下降** 时才允许切换；
否则拒绝并给出实测理由。⛔ 它不是"评估指标"，是**准入条件** ——
和非回归闸同构：先证没变坏（margin 不降），再证变好（recall 更高）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .embeddings import cosine, cosine_or_none


# ──────────────────────────────────────────────────────────────────
# 标注集：query 是**同义改写**，与正例文档**字面几乎不重叠**。
# ⛔ 这是刻意的：字面重叠的 case 词面嵌入也能做对，测不出差别。
# ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Case:
    doc_id: str
    doc: str
    query: str


BENCH: Tuple[Case, ...] = (
    Case("dedup", "写入侧去重：同内容指纹只更新 access_count 与链接，不新增行。",
         "相同的知识被反复存进去，库越撑越大，该怎么处理"),
    Case("nongress", "非回归闸：冻结套件是准入条件，闸不过就不评估发展集。",
         "怎么确保这次改动没有把原本能做的事弄坏"),
    Case("overfit", "泛化闸：held-out 不参与选择，dev 涨而 held-out 不涨判过拟合。",
         "分数一直在涨，可真实能力并没有变强，问题出在哪"),
    Case("adversarial", "对抗验证：先尽力找反证，找不到才提升；验证器为空则拒绝。",
         "一条经验要变成可信的事实之前，要不要先试着推翻它"),
    Case("consolidate", "睡眠期巩固：离线把 L4 会话归档沉淀为 L2/L3 稳定知识。",
         "聊天记录攒了很多，怎么变成以后能用的长期知识"),
    Case("envfirst", "环境优先：先建共享执行环境，再合成任务，用失败转通过来筛选。",
         "可以验证的练习题不够用，怎么批量造出来"),
    Case("ledger", "内容寻址账本：每条带前序哈希，改任意一条链就断。",
         "历史记录被人悄悄改过了，有什么办法能立刻发现"),
    Case("rlvr", "奖励只来自可执行判据，模型自己的评价一律不计入。",
         "能不能让模型给自己打分来判断任务是否完成"),
    Case("grpo", "组内相对优势：用同组候选的均值与标准差归一化，不训练价值网络。",
         "多个候选方案之间怎么比较高低，又不想再训一个打分模型"),
    Case("proliferate", "产物增殖：只增不减会让系统退化，必须显式剪枝。",
         "自动生成的东西越来越多，需不需要定期清理掉一批"),
)


#: 英文对照集。⛔ 它的存在是为了**排除实现错误**：
#:   如果某个 embedder 在中英两组上都差 ⇒ 大概率是我的评测写错了；
#:   如果在英文上明显强、中文上明显弱 ⇒ 那是模型本身的语言能力差异，
#:   结论才是可信的（nomic-embed-text 主要训英文）。
BENCH_EN: Tuple[Case, ...] = (
    Case("dedup", "Write-side dedup: same content fingerprint only bumps "
                  "access_count and links; no new row is inserted.",
         "the same knowledge keeps getting stored again and again, the store "
         "keeps growing, what should I do"),
    Case("nongress", "Non-regression gate: the frozen suite is an admission "
                     "condition; if it fails, the dev set is never evaluated.",
         "how do I make sure this change did not break what already worked"),
    Case("overfit", "Generalization gate: held-out never participates in "
                    "selection; dev up but held-out flat means overfit.",
         "scores keep going up but real ability did not improve, what is wrong"),
    Case("adversarial", "Adversarial verification: try hard to refute first, "
                        "promote only if no counter-evidence; no verifier means "
                        "fail closed.",
         "before an experience becomes a trusted fact, should I try to "
         "disprove it first"),
    Case("consolidate", "Sleep-time consolidation: offline distillation of L4 "
                        "episodic archives into stable L2/L3 knowledge.",
         "I have a lot of transcripts, how do I turn them into long-term "
         "knowledge I can reuse"),
    Case("envfirst", "Environment first: build one shared execution "
                     "environment, then synthesize tasks and keep only those "
                     "that flip a passing test to failing.",
         "I do not have enough verifiable exercises, how do I generate them in "
         "bulk"),
    Case("ledger", "Content-addressed ledger: every event carries the previous "
                   "hash, so editing any entry breaks the chain.",
         "someone quietly edited the history, how can I detect that at once"),
    Case("rlvr", "Reward comes only from executable checks; the model's own "
                 "self-assessment never counts.",
         "can I let the model grade itself to decide whether a task was done"),
    Case("grpo", "Group-relative advantage: normalize with the group mean and "
                 "std instead of training a value network.",
         "how do I rank several candidate solutions without training a scorer"),
    Case("proliferate", "Artifact proliferation: growth without pruning "
                        "degrades the system, so pruning must be explicit.",
         "generated artifacts keep piling up, do I need to clean some up "
         "periodically"),
)


@dataclass
class CaseResult:
    doc_id: str
    rank: int
    sim_pos: float
    sim_best_neg: float
    margin: float


@dataclass
class BenchReport:
    embedder_id: str
    dim: int
    n_cases: int = 0
    recall_at_1: float = 0.0
    mrr: float = 0.0
    margin_mean: float = 0.0
    margin_min: float = 0.0
    cases: List[CaseResult] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"embedder_id": self.embedder_id, "dim": self.dim,
                "n_cases": self.n_cases,
                "recall_at_1": round(self.recall_at_1, 4),
                "mrr": round(self.mrr, 4),
                "margin_mean": round(self.margin_mean, 4),
                "margin_min": round(self.margin_min, 4),
                "error": self.error}


def evaluate(embedder: Any, cases: Sequence[Case] = BENCH) -> BenchReport:
    """测一个 embedder 的检索判别力。

    ⛔ 候选池 = 全部正例文档（query 的正例 + 其余全是干扰）。
    这样"干扰"是**同类但不同条**的文档 —— 最难区分的那种，
    能暴露"语义一团糊"的嵌入（nomic 在本机就是栽在这里）。
    """
    eid = str(getattr(embedder, "embedder_id",
                      getattr(embedder, "name", "callable")))
    dim = int(getattr(embedder, "dim", 0) or 0)
    rep = BenchReport(embedder_id=eid, dim=dim)
    docs = [c.doc for c in cases]
    queries = [c.query for c in cases]
    try:
        dv = embedder.embed(docs) if hasattr(embedder, "embed") \
            else [embedder(d) for d in docs]
        qv = embedder.embed(queries) if hasattr(embedder, "embed") \
            else [embedder(q) for q in queries]
    except Exception as e:  # noqa: BLE001
        rep.error = f"{type(e).__name__}: {e}"
        return rep

    ranks, margins = [], []
    for i, (q, c) in enumerate(zip(qv, cases)):
        scored: List[Tuple[float, int]] = []
        for j, d in enumerate(dv):
            s = cosine_or_none(list(q), list(d))
            if s is None:
                continue
            scored.append((s, j))
        if not scored:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        pos_sim = next((s for s, j in scored if j == i), 0.0)
        neg_best = next((s for s, j in scored if j != i), 0.0)
        rank = next(k for k, (_s, j) in enumerate(scored, 1) if j == i)
        ranks.append(rank)
        margins.append(pos_sim - neg_best)
        rep.cases.append(CaseResult(c.doc_id, rank, round(pos_sim, 4),
                                    round(neg_best, 4),
                                    round(pos_sim - neg_best, 4)))
    if not ranks:
        rep.error = "no_scorable_case（维度不符或嵌入全空）"
        return rep
    rep.n_cases = len(ranks)
    rep.recall_at_1 = sum(1 for r in ranks if r == 1) / len(ranks)
    rep.mrr = sum(1.0 / r for r in ranks) / len(ranks)
    rep.margin_mean = sum(margins) / len(margins)
    rep.margin_min = min(margins)
    return rep


@dataclass
class SwitchVerdict:
    allow: bool
    reason: str
    current: Dict[str, Any]
    candidate: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"allow": self.allow, "reason": self.reason,
                "current": self.current, "candidate": self.candidate}


class EmbeddingSwitchGate:
    """嵌入选型闸：**先证没变坏（margin 不降），再证变好（recall 更高）**。

    ⛔ 与所有其它闸同源：不允许"用判别力换一点召回"。
    ⛔ `min_margin_gain` 默认 0 表示"不下降即可"，不是"有提升即可" ——
       顺序很重要：margin 是**准入**，recall 是**收益**。
    """

    def __init__(self, min_recall_gain: float = 0.0,
                 min_margin_gain: float = 0.0):
        self.min_recall_gain = min_recall_gain
        self.min_margin_gain = min_margin_gain

    def decide(self, current: BenchReport, candidate: BenchReport) -> SwitchVerdict:
        if candidate.error:
            return SwitchVerdict(False, f"候选不可用：{candidate.error}",
                                 current.to_dict(), candidate.to_dict())
        if current.error:
            return SwitchVerdict(True, f"当前嵌入不可用（{current.error}）"
                                       f" ⇒ 允许切换（修故障优先于比优劣）",
                                 current.to_dict(), candidate.to_dict())
        d_recall = candidate.recall_at_1 - current.recall_at_1
        d_margin = candidate.margin_mean - current.margin_mean
        if d_margin < self.min_margin_gain:
            return SwitchVerdict(
                False, f"判别力下降：margin {current.margin_mean:.4f} → "
                       f"{candidate.margin_mean:.4f}（Δ{d_margin:+.4f}）"
                       f" ⇒ 不准入（相似度变高不等于更能区分）",
                current.to_dict(), candidate.to_dict())
        if d_recall <= self.min_recall_gain:
            return SwitchVerdict(
                False, f"召回没有变好：recall@1 {current.recall_at_1:.3f} → "
                       f"{candidate.recall_at_1:.3f}（Δ{d_recall:+.3f}）"
                       f" ⇒ 无收益，不切换（换嵌入要 reindex，代价真实）",
                current.to_dict(), candidate.to_dict())
        return SwitchVerdict(
            True, f"recall@1 {current.recall_at_1:.3f}→{candidate.recall_at_1:.3f}"
                  f"（Δ{d_recall:+.3f}），margin {d_margin:+.4f} ⇒ 允许切换",
            current.to_dict(), candidate.to_dict())


def compare(embedders: Sequence[Any],
            cases: Sequence[Case] = BENCH) -> List[BenchReport]:
    return [evaluate(e, cases) for e in embedders]
