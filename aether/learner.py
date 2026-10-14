"""外部知识摄入（learner 通道）：把**外部**知识写进 5 层记忆。

⛔ 这个文件**不实现任何判据、不去重、不做嵌入** —— 那些闸在
`MemoryOS._write_raw` 里，本模块**直接复用**，不在learner 侧另抄一份：

  · 通道闸（`CHANNEL_WRITABLE`）：learner 只许写 L3/L4，写 L0–L2 被拒；
  · 去重闸（内容指纹）：同内容只并 access，不新增行；
  · 完整性闸：embedding 为空 ⇒ 拒（写入成功但检索不到 = 没入库）。

V5 的对应事故正是"learner 自己搞了一套"：web_learner 零去重、auditor 的
删除与 learner 的写入完全解耦 ⇒ 同一内容跨日重复存储 39 次，而报表显示
"入库成功"。⇒ 本模块只做**编排 + 计量 + 记账 + 可达性取证**。

⛔⛔ 摄入的边界：**摄入不产生判据**。reward 只来自 `Verdict.reward`
（RLVR），learner 写进去的知识只是**候选上下文**，它要变成"有效"必须
被 harness 真正用到并在执行里拿到 reward —— 这一条由分层结构保证
（learner 只能写 L3/L4，而 L0–L2 才承载规则与事实）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .memory import CHANNEL_WRITABLE

__all__ = ["KnowledgeItem", "IngestReport", "KnowledgeLearner",
           "CHANNEL_WRITABLE"]

#: 摄入报告的 evidence 窗口上限。
#: ⛔ **计数与 evidence 必须分开**（V5 第 13 条）：把一个有界数组当总数
#:    会把"摄入 500 条"截断成 5 ⇒ 不是省内存，是**改了读数**。
EVIDENCE_WINDOW = 5


@dataclass
class KnowledgeItem:
    """一条待摄入的外部知识。

    ⛔ `tier` 默认 `"L3"`：learner 的可写层只有 L3/L4（见
    `CHANNEL_WRITABLE`）。显式传 L0/L1/L2 ⇒ 被通道闸拒，**不会被静默
    降级**到 L3 —— 降级会让声明者以为自己写进了权威层（假仪表）。
    """
    content: str
    source: str = ""            # 来源（URL / 文件路径 / 仓库名）
    tier: str = "L3"
    keywords: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    trust: float = 0.5          # 外部知识默认**不给满信任**


@dataclass
class IngestReport:
    """一次摄入的处置报告。

    ⛔ 计数齐全 + evidence 有界：`n_*` 是**真实总数**，`evidence` 只是
    抽样展示。二者合一就会把总数截断（V5 同款错误）。
    """
    n_input: int = 0
    n_written: int = 0
    n_deduped: int = 0
    n_rejected: int = 0
    n_truncated: int = 0
    #: ⛔ 写入成功但**过不了加载侧长度闸**的条数（`min_content_len`）。
    #:    这不是假想敌：实测 CLI 摄入 2 条短知识，进程内 `written=2 /
    #:    retrievable=True`，而 `MemoryOS.save → load` 往返后 **2 条全被
    #:    `rejected_unverified`（内容过短）拒载** ⇒ 知识实际没留下来。
    #:    报告里没有这个读数时，"摄入成功"就是假仪表（V5：写入端降级成功
    #:    ≠ 可用 —— 只是这次降级发生在**加载**侧）。
    n_below_min_content: int = 0
    by_disposition: Dict[str, int] = field(default_factory=dict)
    #: 有界窗口（只展示不计数）
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    #: 可达性取证：摄入后**真的能检索到吗**（None = 没写入所以没测）
    retrievable: Optional[bool] = None
    #: ⛔ 探针必须是**完整内容**：用 evidence 里的 `preview`（截断 60 字）
    #:    去比对库里的完整 content ⇒ 恒不相等 ⇒ 取证永远 False（假故障）。
    retrieve_probe: str = ""
    ledger_error: str = ""

    @property
    def ok(self) -> bool:
        """全部写入成功（无拒绝、无截断）。"""
        return self.n_rejected == 0 and self.n_truncated == 0

    def to_dict(self) -> Dict[str, Any]:
        return {"n_input": self.n_input, "n_written": self.n_written,
                "n_deduped": self.n_deduped, "n_rejected": self.n_rejected,
                "n_truncated": self.n_truncated,
                "n_below_min_content": self.n_below_min_content,
                "by_disposition": dict(self.by_disposition),
                "evidence": list(self.evidence),
                "evidence_window": EVIDENCE_WINDOW,
                "retrievable": self.retrievable,
                "retrieve_probe": self.retrieve_probe,
                "ledger_error": self.ledger_error, "ok": self.ok}


class KnowledgeLearner:
    """把外部知识写进记忆（learner 通道）。

    ⛔ `channel` 固定为 `"learner"`：不是参数。让调用方传 `"curated"`
    就等于把"写入权由通道定"这条纪律改成"调用方自己说了算"（V5 教训：
    声明与通道合一 = 没有闸）。要写权威层请走 curated 通道的**另一个**
    入口，而不是给 learner 开后门。

    ⛔ `max_items` 是**配额**：超出 ⇒ 截断**并记账**（`n_truncated`）。
    静默截断会让"摄入了 500 条"实际只进了 100 条，而报告显示成功。
    """

    #: 类级兜底（与 `MemoryOS.ledger` 同形态）：对象可能经 `__new__` 构造
    ledger: Any = None

    def __init__(self, memory: Any, ledger: Any = None,
                 channel: str = "learner", default_tier: str = "L3",
                 max_items: int = 200):
        self.memory = memory
        self.ledger = ledger
        #: ⛔ 见类注释：不接受调用方改写。
        self.channel = channel
        self.default_tier = default_tier
        self.max_items = max_items

    def ingest(self, items: Iterable[KnowledgeItem],
               *, verify_retrievable: bool = True) -> IngestReport:
        """摄入一批知识。返回报告（**每条处置都可见**）。

        ⛔ 逐条处理、逐条记账：一批里一条坏掉不该让整批失败（那会把
        "某条被拒"表现成"摄入没跑"）。但每条都必须有 disposition。
        """
        rep = IngestReport()
        seen = 0
        probe_full = ""     #: 第一条 written 的**完整**内容（取证用）
        for it in items:
            seen += 1
            if seen > self.max_items:
                #: ⛔ 只 +1 不 break：要**数清**被丢了多少条
                rep.n_truncated += 1
                continue
            rep.n_input += 1
            content = (it.content or "").strip()
            note, disp = self.memory.write(
                it.tier or self.default_tier, content,
                source=it.source, channel=self.channel,
                keywords=it.keywords, tags=it.tags, trust=it.trust)
            rep.by_disposition[disp] = rep.by_disposition.get(disp, 0) + 1
            #: ⛔ 写入侧放行 ≠ 加载侧也放行：过短的条目 `save → load` 往返
            #:    会被 `rejected_unverified` 拒掉（实测 2/2 全丢）。这里只做
            #:    **读数**（不替调用方决定要不要写），但必须可见。
            min_len = int(getattr(self.memory, "min_content_len", 0) or 0)
            too_short = bool(min_len and len(content) < min_len)
            if disp == "written":
                rep.n_written += 1
                if not probe_full:
                    probe_full = content
                if too_short:
                    rep.n_below_min_content += 1
            elif disp == "deduped":
                rep.n_deduped += 1
            else:
                rep.n_rejected += 1
            if len(rep.evidence) < EVIDENCE_WINDOW:
                rep.evidence.append({
                    "source": it.source, "tier": it.tier,
                    "disposition": disp,
                    "note_id": getattr(note, "note_id", "") or "",
                    "preview": content[:60],
                    "below_min_content": bool(disp == "written" and too_short)})

        #: ⛔ **可达性取证**：写入成功 ≠ 检索得到（V5：入库成功但读不出来
        #:    = 没入库）。取第一条 written 的内容回查一次，结果进报告。
        #:    没有写入就不测 ⇒ `retrievable=None`（不是 False —— 没测和
        #:    测了不通过是两回事）。
        if verify_retrievable and rep.n_written > 0 and probe_full:
            probe = probe_full
            rep.retrieve_probe = probe
            try:
                hits = self.memory.search(probe, top_k=3)
                rep.retrievable = any(
                    (getattr(n, "content", "") or "").strip() == probe
                    for n in hits)
            except Exception as e:  # noqa: BLE001
                rep.retrievable = False
                rep.ledger_error = f"retrieve_probe_failed:{type(e).__name__}"

        self._record(rep)
        return rep

    def _record(self, rep: IngestReport) -> None:
        """一次摄入 = 账本一条 `knowledge.ingested`（**含被拒的**）。"""
        led = self.ledger if self.ledger is not None else getattr(
            self.memory, "ledger", None)
        if led is None:
            return
        try:
            led.append("knowledge.ingested", {
                "channel": self.channel,
                "n_input": rep.n_input, "n_written": rep.n_written,
                "n_deduped": rep.n_deduped, "n_rejected": rep.n_rejected,
                "n_truncated": rep.n_truncated,
                "by_disposition": rep.by_disposition,
                "retrievable": rep.retrievable,
                #: ⛔ evidence 只是**抽样**，且不参与任何判据：把它当总数
                #:    会把"摄入了几百条"读成几条（V5 第 13 条）。
                "evidence_sample": rep.evidence,
            }, actor="learner")
        except Exception as e:  # noqa: BLE001
            rep.ledger_error = f"{type(e).__name__}: {e}"
