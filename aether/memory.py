"""5 层记忆操作系统（Memory OS）+ 链接演化 + 睡眠期巩固 + 对抗验证。

2026 的记忆共识（Mem0 报告 / A-MEM / MemoryOS / MemOS 汇总）：
  1. 分层：L0 元规则 / L1 洞察索引 / L2 稳定事实 / L3 技能 / L4 会话归档。
     L1 作**路由层**，只取"恰好需要的知识"，这是把上下文压到 30K 的关键。
  2. 记忆不是"存起来"就完了 —— 要**演化**：A-MEM 用 Zettelkasten 式链接
     生成（新记忆自动与旧记忆互链），检索时沿链接扩散。
  3. **睡眠期巩固**（sleep-time consolidation，2026 被普遍接受）：
     离线把情景轨迹（L4）转成稳定语义（L2/L3）——"LLM 的 REM 睡眠"。
  4. **对抗验证**（本模块的核心增强）：没有任何主流框架做这件事。
     Verifier 先找**反证**，找不到才 promote。慢速记忆污染是长跑 agent
     的真实故障，而现有基准根本不测时间尺度效应。

V5 的对应教训（直接写进实现）：
  · 写入端"降级成功 ≠ 可用"：入库条数 > 0 但 embedding 为空 ⇒ 永远检索不到
    ⇒ 本模块所有写入完成后**必须**校验 `has_embedding`。
  · 去重做在检索侧而非写入侧会导致 39 次重复存储 ⇒ 本模块写入侧
    按内容指纹去重（同内容不重复入库，只增加 access_count 与合并链接）。
  · 知识分层（source_tier）与通道（ingest_channel）分离：写入权由通道定。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .embeddings import (HashingEmbedder, cosine, cosine_or_none,
                         hash_embedding)
from .ledger import content_hash

TIERS = ("L0", "L1", "L2", "L3", "L4")
#: 只有 curated 通道能写 L0/L2（authoritative 层）；learner 只能写 L3/L4
CHANNEL_WRITABLE: Dict[str, Tuple[str, ...]] = {
    "curated": ("L0", "L1", "L2", "L3", "L4"),
    "learner": ("L3", "L4"),
    "system": ("L4",),
}

#: 寻址指纹前缀长度（hex 字符数）。
#: ⛔ 与 `genome._ID_PREFIX` / `nongression._ID_PREFIX` / `environment._ID_PREFIX`
#:    / `runtime._ID_PREFIX` 保持同值。`MemNote.fingerprint` 是**去重键**
#:    （`_by_fp` 的 key）；`[:16]`（64 位）在长期累积的记忆库上会碰撞 ⇒
#:    两条不同内容被当成同一条合并。
_ID_PREFIX = 32

_WORD = re.compile(r"[\u4e00-\u9fff]|[A-Za-z_][A-Za-z0-9_]*|\d+")

#: `_WORD` 切出来的"单字符 CJK token"识别（查询侧兜底用）。
#: ⛔ 范围必须与 `_WORD` 的正则一致 —— 两处各写一份范围就会漂移
#:    （同族教训见 `runtime._CJK_RANGES` 的注释）。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_cjk_token(t: str) -> bool:
    return bool(t) and _CJK_RE.fullmatch(t) is not None

#: ⛔ **唯一事实源**：记忆内容的"可信度"词表。
#: 跨运行持久化之后，上一轮的**自述 / 占位 / 未验证**条目会跟着库一起
#: 复活并在下一轮被检索到 —— 那等于让脏记忆进入因果链。
#: 这个词表同时供「写入后提升(promote)」与「装载时污染闸」使用，
#: 分成两份就会漂移（V5 血泪：改了一处忘了另一处 ⇒ 闸形同虚设）。
UNVERIFIED_MARKS: Tuple[str, ...] = ("TODO", "待确认", "可能", "大概是",
                                     "FIXME", "应该是", "估计")
PLACEHOLDER_BODIES = frozenset({"pass", "ok", "done", "todo", "n/a", "-", ""})


def memory_purity(note: "MemNote", min_len: int = 40) -> Tuple[bool, str]:
    """记忆内容的**可信度**判定：职责是证伪，不是确认。

    ⛔ 方向不能反：默认"通过"的验证等于没有验证。这里主动找反证。
    """
    body = (getattr(note, "content", "") or "").strip()
    if len(body) < min_len:
        return False, f"内容过短（{len(body)} < {min_len}），不足以作为稳定事实"
    if body.lower() in PLACEHOLDER_BODIES:
        return False, "占位内容"
    hit = [m for m in UNVERIFIED_MARKS if m in body]
    if hit:
        return False, f"含未验证标记 {hit}"
    return True, "未找到反证"


# ──────────────────────────────────────────────────────────────────
# 嵌入：见 aether/embeddings.py（此处 re-export 保持旧引用可用）
# ──────────────────────────────────────────────────────────────────
__all__ = ["hash_embedding", "cosine", "MemoryOS", "MemNote", "TIERS",
           "CHANNEL_WRITABLE", "memory_purity"]


@dataclass(frozen=True)
class MemNote:
    """统一记忆单元（MemCube 式：带来源、使用史、可迁移）。

    ⛔ **冻结** —— 修改一律走 `dataclasses.replace()`。原版未 frozen ⇒
        `note.content = "..."` 是合法的，而它**不会**同步 `_by_fp` 去重表
        ⇒ 去重键与内容脱钩 ⇒ 去重**静默失效**（同样的新内容被写两次）。
        `frozen=True` 让这类赋值抛 `FrozenInstanceError`。
        ⛔ 已知边界：`embedding` 是 list，浅冻结不改其内部元素可改性
        （`note.embedding.append(...)` 仍合法）。要完全冻结需 tuple，
        但那会破坏 `cosine_or_none` 的接口契约（期望 list）。
    """
    note_id: str = ""
    tier: str = "L3"
    content: str = ""
    keywords: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    links: Tuple[str, ...] = ()
    embedding: List[float] = field(default_factory=list)
    source: str = ""
    channel: str = "learner"
    trust: float = 1.0
    access_count: int = 0
    created_at: float = 0.0
    last_access: float = 0.0
    supersedes: str = ""
    #: 建这条记忆时用的嵌入器（名字+dim）。⛔ 它是**可查的事实**：
    #: 换模型后旧笔记的 embedder 不匹配 ⇒ integrity() 会点名。
    embedder: str = ""

    @property
    def fingerprint(self) -> str:
        """内容指纹：写入侧去重用（**不含** tier/时间，同内容即同一条）。

        ⛔ 用 `[:32]`（128 位）—— 与 `_ID_PREFIX` 保持一致。
        """
        return content_hash({"content": self.content.strip(),
                             "keywords": list(self.keywords)})[:_ID_PREFIX]

    @property
    def has_embedding(self) -> bool:
        """字段完整性判据（V5 血泪：入库成功但读不出来 = 没入库）。"""
        return bool(self.embedding) and any(x != 0.0 for x in self.embedding)

    def to_dict(self) -> Dict[str, Any]:
        #: ⛔ 依赖 `__dict__`（frozen=True 仍保留 `__dict__`；加 `slots=True`
        #:    会破坏这里）。将来若加 slots，改成显式字段列表。
        d = {k: v for k, v in self.__dict__.items() if k != "embedding"}
        d["has_embedding"] = self.has_embedding
        d["dim"] = len(self.embedding)
        return d


class MemoryOS:
    """5 层记忆 + 链接演化 + 检索 + 睡眠巩固 + 对抗验证。"""

    #: ⛔ 类级兜底：记忆**必须能进账本** —— 它跨运行继承、直接喂给提议器，
    #:    是因果链的一环。不进账本 ⇒ 记忆被谁写的、什么时候写的、被拒了
    #:    多少，全都不可溯源，也无从发现被外部改动。默认 None ⇒ 未挂载时
    #:    行为完全不变（鸭子类型挂载，不强制依赖）。
    ledger: Any = None
    #: 记账失败的原因（类级兜底，未挂载时恒为空串）
    _ledger_error: str = ""
    #: ⛔ 类级兜底（V5 教训 M7）：对象可能经 `__new__` 构造而拿不到 `__init__`
    #:    里赋的实例属性 ⇒ `consolidate()` 首行赋值前读它会 AttributeError。
    #:    这里给 None，`consolidation_report()` 对 None 返回全零表。
    last_consolidation: Optional[Dict[str, Any]] = None
    #: 同上（类级兜底）：上一次真剪枝的分账
    last_prune: Optional[Dict[str, Any]] = None
    #: 同款类级兜底：最近一次 `search` 的查询侧嵌入失败原因
    #: （空串 = 上一次查询侧嵌入正常）。
    _last_search_embed_error: str = ""

    def __init__(self, embedder: Any = None, dim: int = 0, link_topk: int = 3,
                 min_content_len: int = 0):
        """embedder 可以是 `Embedder` 对象（推荐，带 name/dim），
        也可以是 `Callable[[str], List[float]]`（旧接口，dim 需显式给）。

        ⛔ 库与 embedder **强绑定**：`self.dim` 与 `self.embedder_id` 一旦确定，
        所有入库存量必须是同一个 dim。换 embedder 只能走 `reindex()` 全量重算，
        ⛔⛔ 绝不静默降级混入（V5 教训：两种格式混在一个库里，写入端显示成功、
        检索端全废，而且没有任何报错）。
        """
        if embedder is None:
            embedder = HashingEmbedder(dim=dim or 256)
        self.embedder = embedder
        self.dim = int(getattr(embedder, "dim", 0) or dim or 256)
        self.embedder_id = str(getattr(embedder, "embedder_id",
                                       getattr(embedder, "name", "callable")))
        self.link_topk = link_topk
        #: ⛔ 检索下限（V5 r108a：极短条目余弦虚高，会霸占 top-k）
        self.min_content_len = min_content_len
        self.notes: Dict[str, MemNote] = {}
        self._by_fp: Dict[str, str] = {}
        #: ⛔ 巩固分账也放进初始 stats —— 原版在 `consolidate()` 里动态加键，
        #:    导致"跑过巩固"与"没跑过"的 stats 键集**不同** ⇒ 跨运行对比
        #:    需要先判断"这个键是否存在"。初始全零，键集固定。
        self.stats = {"written": 0, "deduped": 0, "rejected_channel": 0,
                      "rejected_empty": 0, "promoted": 0, "refuted": 0,
                      "rejected_dim_mismatch": 0, "rejected_embed_error": 0,
                      "search_embed_error": 0,
                      "search_dim_mismatch": 0, "ledger_failed": 0,
                      "consolidated_made": 0, "consolidated_deduped": 0}
        #: 实例级 shadow 掉类级默认值
        self._last_search_embed_error = ""

    # ── 账本挂载（事实源收敛）────────────────────────────────────
    def attach_ledger(self, ledger: Any) -> "MemoryOS":
        """把记忆写入接进账本。**返回 self** 便于链式构造。

        ⛔ 用挂载而不是构造参数：`MemoryOS` 在测试/脚本里有几十处构造点，
        改签名会让所有调用点跟着动；挂载是**可选增强**，未挂载行为不变。
        """
        self.ledger = ledger
        return self

    # ── 嵌入调用（含维度契约）────────────────────────────────────
    def _embed_one(self, text: str) -> List[float]:
        """⛔ 真嵌入失败必须**向上抛**，由调用方记账并拒写。

        绝不能 `except: return hash_embedding(text)` —— 那会把 256 维混进
        768 维的库，余弦在维度不符时静默返回 0.0，表现为"知识越存越多、
        检索越来越差"，而没有任何一条错误日志。
        """
        emb = self.embedder
        if hasattr(emb, "embed"):
            return list(emb.embed([text])[0])
        return list(emb(text))      # 旧接口：Callable

    # ── 写入（含通道闸 + 去重 + 字段完整性）──────────────────────
    def write(self, tier: str, content: str, *, source: str = "",
              channel: str = "learner", keywords: Optional[Iterable[str]] = None,
              tags: Optional[Iterable[str]] = None, trust: float = 1.0,
              now: Optional[float] = None) -> Tuple[MemNote, str]:
        """写一条记忆（**并记账**）。返回 (note, 处置)。

        处置 ∈ {written, deduped, rejected_*}。⛔ 记账失败**不得**改变写入
        的结果，但**必须**计数 —— 静默丢账本比丢写入更危险：库里有什么
        将不再有据可查（`stats["ledger_failed"]` 就是这条裂缝的宽度）。
        """
        note, disp = self._write_raw(
            tier, content, source=source, channel=channel, keywords=keywords,
            tags=tags, trust=trust, now=now)
        self._record_write(tier, content, note, disp, channel, source)
        return note, disp

    def _record_write(self, tier: str, content: str, note: MemNote,
                      disposition: str, channel: str, source: str) -> None:
        """一次写入尝试 = 账本一条 `memory.write`（**含被拒的**）。"""
        led = getattr(self, "ledger", None)
        if led is None:
            return                       # 未挂载 ⇒ 行为与以前完全一致
        try:
            led.append("memory.write", {
                "tier": tier, "channel": channel, "source": source,
                "disposition": disposition,
                "note_id": getattr(note, "note_id", "") or "",
                "content_hash": content_hash((content or "").strip())[:_ID_PREFIX],
                "inherited": "inherited" in tuple(getattr(note, "tags", ()) or ()),
            }, actor="memory")
        except Exception as e:  # noqa: BLE001
            self.stats["ledger_failed"] += 1
            self._ledger_error = f"{type(e).__name__}: {e}"

    def _write_raw(self, tier: str, content: str, *, source: str = "",
                   channel: str = "learner",
                   keywords: Optional[Iterable[str]] = None,
                   tags: Optional[Iterable[str]] = None, trust: float = 1.0,
                   now: Optional[float] = None) -> Tuple[MemNote, str]:
        """真正的写入（不含记账）—— `write()` 是它 + 记账的包装。

        拆开是为了让**巩固/装载**等内部路径也走同一套闸，而不是各抄一份。

        返回 (note, 处置)。处置 ∈ {written, deduped, rejected_*}。

        ⛔ 三条闸（缺一条就会出现"库越来越大但什么也检索不到"）：
          · 通道闸：learner 不许直接写 L2 事实层（V5：写入权由通道定）；
          · 去重闸：同内容指纹只更新 access 与链接，不新增行；
          · 完整性闸：embedding 为空 ⇒ 拒绝入库（不是"降级成功"）。

        ⛔⛔ 每一次尝试（**含被拒的**）都进账本（由 `write()` 的
        `_record_write()` 负责）：只记成功写入的话，"被拒了多少、为什么
        被拒"在事实源里就不存在，等于把闸的另一半藏起来。
        """
        if tier not in TIERS:
            raise ValueError(f"未知 tier {tier}（可选 {TIERS}）")
        if tier not in CHANNEL_WRITABLE.get(channel, ()):
            self.stats["rejected_channel"] += 1
            return MemNote(), f"rejected_channel:{channel}→{tier}"
        body = (content or "").strip()
        if not body:
            self.stats["rejected_empty"] += 1
            return MemNote(), "rejected_empty"

        ts = now if now is not None else time.time()
        kw = tuple(keywords or _auto_keywords(body))
        try:
            emb = self._embed_one(body)
        except Exception as e:  # noqa: BLE001
            self.stats["rejected_embed_error"] += 1
            return MemNote(), f"rejected_embed_error:{type(e).__name__}"

        probe = MemNote(content=body, keywords=kw, embedding=emb)
        fp = probe.fingerprint
        if fp in self._by_fp:
            old_id = self._by_fp[fp]
            old = self.notes[old_id]
            self.notes[old_id] = replace(
                old, access_count=old.access_count + 1, last_access=ts,
                tier=_higher_tier(old.tier, tier), trust=max(old.trust, trust))
            self.stats["deduped"] += 1
            return self.notes[old_id], "deduped"

        if not emb or not any(x != 0.0 for x in emb):
            self.stats["rejected_empty"] += 1
            return MemNote(), "rejected_no_embedding"
        if len(emb) != self.dim:
            # ⛔ 这是"库被污染"的最后一道闸：宁可拒写，也不让异维向量入库
            self.stats["rejected_dim_mismatch"] += 1
            return MemNote(), (f"rejected_dim_mismatch:{len(emb)}!={self.dim}"
                               f"（换 embedder 必须 reindex，禁止混库）")

        nid = f"{tier}:{fp}"
        note = MemNote(note_id=nid, tier=tier, content=body, keywords=kw,
                       tags=tuple(tags or ()), embedding=emb, source=source,
                       channel=channel, trust=trust, created_at=ts,
                       last_access=ts, embedder=self.embedder_id)
        self.notes[nid] = note
        self._by_fp[fp] = nid
        self._link(note)
        self.stats["written"] += 1
        return note, "written"

    def backfill_ledger(self) -> int:
        """为**存量**记忆补记账本证据（账本化之前写入的那些）。

        ⛔ 为什么必须有这个出口：没有它，"库里有 N 条、账本里 0 条"会变成
        一条**永远修不好**的告警 —— 而修不好的告警会被忽略，等于没有告警
        （和"投影不一致却只报一句"是同一个坑）。

        ⛔ 但它**不是**实时写入的等价物：`disposition="backfill"` 只能证明
        "此刻库里有这条"，不能证明"它当初是这么被写进来的"（中途被外部
        改过也看不出来）。所以读数里必须把它**单独计数**，不能混进 written。
        """
        led = getattr(self, "ledger", None)
        if led is None:
            return 0
        n = 0
        for note in list(self.notes.values()):
            try:
                led.append("memory.write", {
                    "tier": note.tier, "channel": note.channel,
                    "source": note.source or "",
                    "disposition": "backfill",
                    "note_id": note.note_id,
                    "content_hash": content_hash(
                        note.content.strip())[:_ID_PREFIX],
                    "inherited": "inherited" in tuple(note.tags or ()),
                }, actor="memory")
                n += 1
            except Exception as e:  # noqa: BLE001
                self.stats["ledger_failed"] += 1
                self._ledger_error = f"{type(e).__name__}: {e}"
        return n

    # ── 链接演化（A-MEM / Zettelkasten）───────────────────────────
    def _link(self, note: MemNote) -> None:
        """新记忆与最相近的旧记忆互链。**双向**且去重。"""
        cands = self.search(note.content, top_k=self.link_topk + 1,
                            tiers=None, update_access=False)
        ids = [n.note_id for n in cands if n.note_id != note.note_id][:self.link_topk]
        if not ids:
            return
        merged = tuple(dict.fromkeys(tuple(note.links) + tuple(ids)))
        self.notes[note.note_id] = replace(note, links=merged)
        for oid in ids:
            o = self.notes.get(oid)
            if o is None or note.note_id in o.links:
                continue
            self.notes[oid] = replace(o, links=tuple(o.links) + (note.note_id,))

    # ── 检索（语义 + 关键词 + 链接扩散 + 长度下限）─────────────────
    def search(self, query: str, top_k: int = 5,
               tiers: Optional[Iterable[str]] = None,
               update_access: bool = True,
               expand_links: bool = True,
               *,
               max_links_added: Optional[int] = None,
               ledger_actor: str = "") -> List[MemNote]:
        """按 `query` 检索记忆。

        🔴 修复一：查询侧嵌入失败 ⇒ **显式退化为关键词-only 路径**。
            原版 `q = []` 之后，所有 note 都被 `cosine_or_none([], ...)
            is None` 挡掉并 `continue` ⇒ 关键词加权那行**永远到不了**
            ⇒ 实际返回**空列表**，而注释却说"退化为关键词检索"（撒谎）。
            同时 `search_dim_mismatch` 被掺进了"查询侧嵌入失败"这个
            完全不同的事件 ⇒ 排障方向指向"库被污染"，而真相是"这次
            嵌入没跑成"。改用**独立计数器 + 独立分支**。
            关键词-only 路径的分数量级与余弦无关 ⇒ 仍能按"比大小"选
            top_k（用**词覆盖率** = 命中关键词 / 该 note 关键词总数）。

        🟡 修复十：`expand_links=True`（默认）时返回条数**可能超过
            `top_k`** —— 这是链接扩散语义的一部分（原行为）。调用方若
            需要**严格上界**，传 `max_links_added=0` 或 `expand_links=False`。
            不改默认行为（保持兼容），但**接口上承认**这个语义。

        ⛔ consume 段入账（2026-10-15 收口）：`ledger_actor` 非空时往账本
           记一条 `memory.recall`。此前 `search` **完全无账** ⇒ 账本只能
           回答"记忆被写过几次"，回答不了"被消费过几次、有没有召回" ⇒
           learn → consume → cleanup 三段里 consume 是黑的。
           ⛔ 默认**关**（空字符串）：search 是热点（agent 每轮 + 提议器
           每轮），内部调用都记会把账本淹掉；只让**真实消费点**显式打开。
        """
        q: List[float] = []
        query_embed_failed = False
        try:
            q = self._embed_one(query or "")
        except Exception as e:  # noqa: BLE001
            #: ⛔ 只记**这个**事件，不混进 `search_dim_mismatch`（那是库脏了）。
            #: ⛔⛔ 也不能复用写侧的 `rejected_embed_error`：写侧那个是
            #:     "**入库被拒**"，查询侧这个是"**这次检索降级**" —— 同一
            #:     个键记两件事，读数就无法回答"到底是写入坏了还是检索坏了"。
            #:     （原注释声称"改用独立计数器"，但代码仍在写共享键 —— 注释
            #:     与实现不一致本身就是缺陷。现在补上真正的独立计数器。）
            self.stats["search_embed_error"] += 1
            self._last_search_embed_error = f"{type(e).__name__}: {e}"
            query_embed_failed = True
            q = []

        allowed = set(tiers) if tiers else None
        scored: List[Tuple[float, MemNote]] = []
        for n in self.notes.values():
            if allowed and n.tier not in allowed:
                continue
            if self.min_content_len and len(n.content) < self.min_content_len:
                continue
            if not n.has_embedding:
                continue
            if query_embed_failed:
                #: ⛔ 关键词-only 路径：原版无此分支 ⇒ "退化为关键词检索"
                #:    是**假的**（所有 note 都被 `cosine_or_none([], ...)`
                #:    挡掉，返回空）。
                #: ⛔⛔ r33 实测三版都错：① `k in query`（拿 note 的关键词
                #:    去子串匹配整条 query）⇒ 中文单字关键词永远命中不了；
                #:    ② 按 `len(t) > 1` 取查询词 ⇒ 中文没有无歧义的分词器，
                #:    切不出多字词 ⇒ 空表；③ 只认 ASCII 词 ⇒ 中文查询全落空
                #:    （而本系统的实际查询就是中文）⇒ 空表。
                #:    口径：查询侧取（长度 ≥2 的词）∪（CJK 单字），
                #:    与 note 的正文/关键词做**字符包含**匹配。
                #:    ⛔ 已知边界：字符级匹配会把"非"这种高频字算成相关
                #:    ⇒ 这是**降级兜底**（嵌入挂了才有这条路，且
                #:    `search_embed_error` 与 `_last_search_embed_error`
                #:    都留了痕）。它保证"有候选"而不是"精确"。
                qwords = set()
                for t in _WORD.findall(query or ""):
                    if len(t) > 1:
                        qwords.add(t.lower())
                    elif _is_cjk_token(t):
                        qwords.add(t)
                if not qwords:
                    continue
                hay = (" ".join(n.keywords) + "\n" + n.content).lower()
                hits_n = sum(1 for w in qwords if w in hay)
                if not hits_n:
                    continue
                #: 用**查询侧命中率**，量级与余弦无关 ⇒ 与余弦结果不混谈
                s = hits_n / len(qwords)
            else:
                s = cosine_or_none(q, n.embedding)
                if s is None:
                    # ⛔ 维度不符 = 库脏了。跳过并**计数**，让它可被看见，
                    #    而不是当成"不相关"（那是不可判别与不相关被混为一谈）。
                    self.stats["search_dim_mismatch"] += 1
                    continue
                # 关键词命中做小幅加权（纯向量对短查询不稳）
                if any(k and k in (query or "") for k in n.keywords):
                    s += 0.05
            scored.append((s, n))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = [n for _, n in scored[:top_k]]

        if expand_links and out:
            #: 🟡 修复十：`max_links_added` 给调用方**严格上界**（默认
            #:    `None` = 无上限 = 原行为）。改**默认行为**会破坏兼容；
            #:    但**接口**上承认"可能超过 top_k"。
            budget = (max_links_added if max_links_added is not None
                      else len(self.notes))
            seen = {n.note_id for n in out}
            added = 0
            for n in list(out):
                if added >= budget:
                    break
                for lid in n.links:
                    if added >= budget:
                        break
                    if lid in seen or lid not in self.notes:
                        continue
                    seen.add(lid)
                    out.append(self.notes[lid])
                    added += 1
        if update_access:
            ts = time.time()
            for n in out:
                self.notes[n.note_id] = replace(
                    n, access_count=n.access_count + 1, last_access=ts)
        #: ⛔ consume 段入账（只有显式指定 actor 才记，见 docstring）。
        if ledger_actor:
            self._record_recall(query, out, ledger_actor, query_embed_failed)
        return out

    def _record_recall(self, query: str, out: List["MemNote"],
                       actor: str, degraded: bool) -> None:
        """记一条**有界的**召回账。

        ⛔ 只记 `query_hash` 不记原文：① search 是热点，原文进账本会让账本
           按查询内容膨胀；② 记忆正文/查询属于内容，账本是长期留存的事实源。
           ⛔ 记账失败**不吞**：留痕到 `_ledger_error`（与 `prune` 同形态），
           否则"consume 无账"会以"看起来有账但其实是空的"形式复现。
        """
        try:
            self.ledger.append("memory.recall", {
                "query_hash": content_hash(query or "")[:16],
                "n_hits": len(out),
                "degraded": bool(degraded),
                "tiers": sorted({n.tier for n in out}),
                "top_note": (out[0].note_id if out else ""),
            }, actor=actor)
        except Exception as e:  # noqa: BLE001
            self._ledger_error = f"{type(e).__name__}: {e}"

    # ── 提升（L4/L3 → L2）＋ 对抗验证 ─────────────────────────────
    def promote(self, note_id: str, *,
                verifier: Optional[Callable[[MemNote], Tuple[bool, str]]] = None,
                channel: str = "curated") -> Tuple[bool, str]:
        """把一条记忆提升到 L2（稳定事实层）—— **先找反证再 promote**。

        对抗验证（2026 主流框架都没做的事）：
        verifier 的职责是**尽力证伪**这条记忆；找不到反证才允许提升。
        ⛔ verifier=None ⇒ 拒绝提升（fail-closed），而不是默认通过。
           "没人反对"不等于"通过验证"。
        """
        n = self.notes.get(note_id)
        if n is None:
            return False, "note_not_found"
        if verifier is None:
            self.stats["refuted"] += 1
            return False, "refuted:no_verifier（无验证器 ⇒ fail-closed，不提升）"
        try:
            ok, why = verifier(n)
        except Exception as e:  # noqa: BLE001
            self.stats["refuted"] += 1
            return False, f"refuted:verifier_error:{type(e).__name__}"
        if not ok:
            self.stats["refuted"] += 1
            return False, f"refuted:{why}"
        self.notes[note_id] = replace(n, tier="L2", channel=channel,
                                      trust=min(1.0, n.trust + 0.1))
        self.stats["promoted"] += 1
        return True, "promoted"

    # ── 睡眠期巩固（sleep-time consolidation）─────────────────────
    def consolidate(self, *, max_age_s: float = 86400.0,
                    min_access: int = 2,
                    summarizer: Optional[Callable[[List[MemNote]], str]] = None,
                    min_group: int = 2,
                    now: Optional[float] = None) -> List[MemNote]:
        """离线把 L4 情景归档 → L3 稳定知识（"LLM 的 REM 睡眠"）。

        ⛔⛔ 巩固是**跨条聚合**，不是逐条复制（2026-10-14 修）：
        逐条摘要的产物恒等于输入，写回时必然被自己的去重闸吃掉 ⇒ 产出恒 0。
        现在按"模板骨架"分组，只有同构的一组（>= min_group 条）才归纳。

        规则（保守，宁可不巩固也不制造幻觉）：
          · 只处理被访问过 >= min_access 次 或 足够老的 L4；
          · 默认 summarizer 是**抽取式归纳**（骨架 + 槽位分布），不编造；
          · 巩固产物走 curated 通道写 L3，并 `supersedes` 指回组内首条。

        ⛔ 记账必须分开设：只报一个数字的话，"没有新信息可归纳"与
        "机制坏了"在输出里长得一模一样 —— 典型静默失败。分账见
        `consolidation_report()`：made / deduped / skipped_* 各自独立。

        🟡 修复八：`skipped_empty` 与 `skipped_not_isomorphic` 分家 ——
            原版从不递增 `skipped_empty`，"归纳为空"与"不同构"混在一起
            （处置不同：前者是"素材不足/无数字槽位"，后者是"骨架不一致"）。
        """
        ts = now if now is not None else time.time()
        pool = [n for n in self.notes.values()
                if n.tier == "L4"
                and (n.access_count >= min_access or (ts - n.created_at) >= max_age_s)]
        rep = {"pool": len(pool), "groups": 0, "made": 0, "deduped": 0,
               "skipped_below_min_group": 0, "skipped_not_isomorphic": 0,
               "skipped_empty": 0, "rejected": 0}
        self.last_consolidation = rep
        if not pool:
            return []

        #: ⛔ 分组键用**归一化 source**（把数字抹平），不用内容骨架：
        #:    内容模板一旦改版（比如换掉一个词），骨架分组就会把所有记录
        #:    拆成互不相同的单条组 ⇒ 全部 skip ⇒ 又回到"产出恒 0 但看不出
        #:    为什么"。source 是"同一个生产者 + 同一套模板"的可靠代理。
        #:    内容是否同构由归纳器自己校验（槽位数不一致 ⇒ 明确记 skipped）。
        #: ⛔ 第二维是槽位数：同一个 source 里可能混着**不同代的模板**
        #:    （旧格式把 8 位 hash 写进内容 ⇒ 数字个数随 hash 浮动，3~7 槽
        #:    都有）。只按 source 分组会让新旧混在一组 ⇒ 整组不同构 ⇒
        #:    连同新格式一起被 skip，历史脏数据把新机制也拖死（实测
        #:    skipped_not_isomorphic=30）。按 (source, 槽位数) 分则可各自归纳。
        groups: Dict[Tuple[str, int], List[MemNote]] = {}
        for n in pool:
            key = (_skeleton(n.source) or "#nosource", len(_slots(n.content)))
            groups.setdefault(key, []).append(n)

        made: List[MemNote] = []
        for _skel, grp in groups.items():
            if len(grp) < min_group:
                rep["skipped_below_min_group"] += len(grp)
                continue
            rep["groups"] += 1
            #: 🟡 修复八：区分"空"与"不同构" —— 用扩展归纳器拿**原因**。
            if summarizer is not None:
                text = summarizer(grp)
                empty_reason = "" if text.strip() else "custom_summarizer_empty"
            else:
                text, empty_reason = _inductive_summary_ex(grp)
            if not text.strip():
                if empty_reason in ("insufficient_notes", "no_numeric_slots",
                                    "custom_summarizer_empty"):
                    rep["skipped_empty"] += len(grp)
                else:
                    rep["skipped_not_isomorphic"] += len(grp)
                continue
            new, how = self.write("L3", text,
                                  source=f"consolidated:{grp[0].note_id}",
                                  channel="curated", tags=("consolidated",))
            if how == "written":
                self.notes[new.note_id] = replace(new, supersedes=grp[0].note_id)
                made.append(self.notes[new.note_id])
                rep["made"] += 1
            elif how == "deduped":
                #: 同样的归纳已经存在 ⇒ 没有新信息。这是**正确**结果，不是故障
                rep["deduped"] += 1
            else:
                rep["rejected"] += 1
            # 原 L4 归档保留（历史不删），只标记已被巩固
            # ⛔ 标签必须去重：无脑追加会让 consolidated 重复 11~19 次（存量污染）
            for n in grp:
                if "consolidated" not in n.tags:
                    self.notes[n.note_id] = replace(
                        n, tags=tuple(n.tags) + ("consolidated",))
        #: 🟡 修复九：stats 键已在 `__init__` 初始化 ⇒ 直接 += 即可
        self.stats["consolidated_made"] += rep["made"]
        self.stats["consolidated_deduped"] += rep["deduped"]
        return made

    def consolidation_report(self) -> Dict[str, Any]:
        """上一次巩固的**分账**。没跑过也返回全零表，绝不给 None。

        ⛔ 调用方看到 `deduped > 0 / made == 0` 与 `pool == 0` 的处置完全
        不同：前者是"没有新信息"，后者是"根本没有素材"。合并成一个数字
        就无法区分（V5 老教训：静默失败最爱藏在聚合读数里）。
        """
        r = self.last_consolidation
        if r:
            return dict(r)
        return {"pool": 0, "groups": 0, "made": 0, "deduped": 0,
                "skipped_below_min_group": 0, "skipped_not_isomorphic": 0,
                "skipped_empty": 0, "rejected": 0, "note": "not_run"}

    def dedup_tags(self) -> int:
        """把所有 note 的重复标签压成唯一（幂等）。返回被修改的条数。

        存量污染来源：旧的 consolidate 无脑 `tags + ("consolidated",)` 且
        不去重 ⇒ 生产库里同一条 note 的 tags 出现 consolidated 重复 11~19
        次。既空转又污染数据，而读数完全看不出来。
        """
        fixed = 0
        for nid, n in list(self.notes.items()):
            seen: List[str] = []
            for t in n.tags:
                if t not in seen:
                    seen.append(t)
            if len(seen) != len(n.tags):
                self.notes[nid] = replace(n, tags=tuple(seen))
                fixed += 1
        return fixed

    # ── 剪枝（产物增殖闸）─────────────────────────────────────────
    def prune(self, *, max_notes: int = 10000,
              min_trust: float = 0.05,
              stale_s: float = 30 * 86400.0,
              now: Optional[float] = None,
              actor: str = "memory") -> List[str]:
        """剪掉低信任 + 长期未访问的记忆（**真删，会留痕**）。

        2026 文献点名的失败模式：sub-agent/harness artifact proliferation
        —— 产物只增不减会让系统退化。必须显式剪枝。
        ⛔ L0/L1（元规则与路由索引）**永不剪**。
        ⛔⛔ 它是不可逆的 ⇒ 默认路径请走 `prune_dryrun()`（只报数不删），
        真删由人显式触发（CLI `memory prune --apply`）。

        🟠 修复二：**只删已留痕的**。原版先记账（失败就 `break`）但随后
            仍**删除全部** `victims` ⇒ "删了 5 条但只有 2 条有账" 直接
            违反"事实只有一份"。改成逐条记账、逐条删除；一旦记账失败
            立即停手（余下的不删）。
        """
        ts = now if now is not None else time.time()
        victims, by_reason = self._select_prune_victims(
            max_notes=max_notes, min_trust=min_trust, stale_s=stale_s, ts=ts)
        #: ⛔⛔ 剪枝是**不可逆**操作 ⇒ 删了什么必须留痕。不留的话，"这条
        #:    记忆去哪了"在事实源里根本不存在 —— 与 V5 "except 吞异常"同族：
        #:    静默丢失，且事后无从追究。
        #:    🟠 修复二：**没记账的不删**。宁可少删几条，不可删了没账。
        recorded: List[str] = []
        if self.ledger is None and victims:
            self._ledger_error = "ledger_not_attached（剪枝未留痕）"
        else:
            for nid in victims:
                n = self.notes.get(nid)
                if n is None:
                    continue
                try:
                    self.ledger.append(
                        "memory.pruned",
                        {"note_id": nid, "tier": n.tier,
                         "trust": round(float(n.trust), 4),
                         "access_count": int(n.access_count),
                         "age_s": round(ts - n.last_access, 1),
                         "content_hash": content_hash(
                             n.content)[:_ID_PREFIX]},
                        actor=actor)
                    recorded.append(nid)
                except Exception as e:  # noqa: BLE001
                    self._ledger_error = f"{type(e).__name__}: {e}"
                    break
        for nid in recorded:
            n = self.notes.pop(nid, None)
            if n:
                self._by_fp.pop(n.fingerprint, None)
        self.last_prune = {"n": len(recorded), "by_reason": by_reason,
                           "remaining": len(self.notes),
                           #: 🟠 修复二（附）：让观测侧能区分"留痕完整"与
                           #:    "留痕只覆盖部分"。缺口大小 = len(victims)
                           #:    - len(recorded)。
                           "intended": len(victims),
                           "ledger_failed": bool(self._ledger_error)}
        return recorded

    def _select_prune_victims(self, *, max_notes: int, min_trust: float,
                              stale_s: float,
                              ts: float) -> Tuple[List[str], Dict[str, int]]:
        """剪枝判据的**唯一事实源** —— dry-run 与真剪共用同一份。

        ⛔ 两条判据各写一份是灾难：`memory prune` 报"要剪 5 条"，真剪却剪了
        7 条，而没人能说清差在哪（与 V5 "分数定义必须唯一来源"同源）。
        """
        by_reason = {"low_trust": 0, "stale_unused": 0, "over_capacity": 0}
        victims: List[str] = []
        for nid, n in self.notes.items():
            if n.tier in ("L0", "L1"):
                continue        # ⛔ 元规则与路由索引永不剪
            if n.trust < min_trust:
                victims.append(nid)
                by_reason["low_trust"] += 1
            elif (ts - n.last_access) > stale_s and n.access_count == 0:
                victims.append(nid)
                by_reason["stale_unused"] += 1
        over = len(self.notes) - len(victims) - max_notes
        if over > 0:
            vset = set(victims)
            rest = sorted((n for n in self.notes.values()
                           if n.note_id not in vset
                           and n.tier not in ("L0", "L1")),
                          key=lambda n: (n.access_count, n.last_access))[:over]
            victims += [n.note_id for n in rest]
            by_reason["over_capacity"] += len(rest)
        return victims, by_reason

    def prune_dryrun(self, *, max_notes: int = 10000,
                     min_trust: float = 0.05,
                     stale_s: float = 30 * 86400.0,
                     now: Optional[float] = None) -> Dict[str, Any]:
        """只算不删（**默认路径**）。判据与 `prune()` 同源。

        ⛔ 为什么自动路径只报数不删：剪枝不可逆，而"产物增殖闸"要的是
        **看得见**，不是自动删。 evolve 结束时跑这个 ⇒ 该不该剪由人决定。

        🟠 修复三：dry-run 必须把 `prune()` 的**留痕前置条件**一并建模，
        否则"报的数"不是"真剪会做的数"：
          · `n`           = 判据命中的条数（判据同源的部分）
          · `will_delete` = **真跑 `prune()` 会删掉的条数**
          · `blocked_by`  = 拦住删除的原因（没挂账本 ⇒ `ledger_not_attached`）
        ⛔ 实测（r33）：没挂账本时原版 `n=2` 而真剪 0 条 —— 报的数字比
           真删的多，读数看起来"闸在动"，实际一条都没动。两个数字必须
           分开给，且 `will_delete` 才是诚实的那个。
        """
        ts = now if now is not None else time.time()
        victims, by_reason = self._select_prune_victims(
            max_notes=max_notes, min_trust=min_trust, stale_s=stale_s, ts=ts)
        #: ⛔ 与 `prune()` 的 `if self.ledger is None and victims` **同源**：
        #:    没账本 ⇒ 一条都不删（宁可少删，不可删了没账）。
        blocked_by: List[str] = []
        if self.ledger is None and victims:
            blocked_by.append("ledger_not_attached")
        will_delete = 0 if blocked_by else len(victims)
        note = ""
        if blocked_by:
            note = ("未挂账本 ⇒ 真剪一条都不会删（留痕前置条件未满足）；"
                    "`n` 是判据命中的条数，`will_delete` 才是真剪会做的数")
        return {"n": len(victims), "will_delete": will_delete,
                "by_reason": by_reason,
                "remaining": len(self.notes) - will_delete,
                "total": len(self.notes), "sample": victims[:10],
                "victims": victims,
                "ledger_attached": self.ledger is not None,
                "blocked_by": blocked_by, "note": note, "applied": False}

    def prune_report(self) -> Dict[str, Any]:
        """上一次**真**剪枝的分账。没跑过返回全零表（不给 None）。"""
        r = self.last_prune
        if r:
            return dict(r)
        return {"n": 0, "by_reason": {}, "remaining": len(self.notes),
                "note": "not_run"}

    # ── 重索引：换 embedder 的**唯一**合法路径 ──────────────────────
    def reindex(self, new_embedder: Any, *,
                from_label: str = "") -> Dict[str, Any]:
        """全量重算所有记忆的嵌入。

        ⛔ 这是换嵌入器的**唯一**入口。直接改 `self.embedder` 会让库里同时
        存在两种 dim 的向量 —— 检索时维度不符被静默跳过，表现为"知识越存
        越多、越搜越差"，且没有任何报错（V5 的 169 条就是这个形态）。

        ⛔ 逐条失败 ⇒ 该条标记 `embed_missing`（不删内容），
        **绝不**用零向量占位：零向量能通过 `has_embedding` 之外的检查，
        但它在余弦里恒为 0，等于一条永远召不回的死记录。

        🟠 修复三：`zip(notes, vecs)` 会**静默按短的截断** —— 若
            embedder 返回的向量数少于 note 数（batch bug / 中途超时），
            后面 N 条保持**旧向量**，而 `reindex` 报告说"ok=N, bad=0"。
            库里混进两种 dim ⇒ 检索时维度不符被静默跳过（V5 那 169 条
            形态原样复现）。这里显式校验长度，不符即整批回滚。
        """
        old_embedder, old_id, old_dim = self.embedder, self.embedder_id, self.dim
        self.embedder = new_embedder
        self.dim = int(getattr(new_embedder, "dim", 0) or self.dim)
        self.embedder_id = str(getattr(
            new_embedder, "embedder_id", getattr(new_embedder, "name", "callable")))

        notes_list = list(self.notes.values())
        texts = [n.content for n in notes_list]
        try:
            vecs = new_embedder.embed(texts) if hasattr(new_embedder, "embed") \
                else [new_embedder(t) for t in texts]
            #: 🟠 修复三：长度必须严格匹配。`zip` 会按短的截断 ⇒ 后 N 条
            #:    静默保留旧向量 ⇒ 库里混进两种 dim ⇒ 检索时维度不符被
            #:    静默跳过（V5 那 169 条形态）。
            vecs = list(vecs)
            if len(vecs) != len(texts):
                self.embedder, self.dim, self.embedder_id = \
                    old_embedder, old_dim, old_id
                return {"ok": 0, "failed": len(notes_list),
                        "rolled_back": True,
                        "error": (f"embedder 返回 {len(vecs)} 个向量，"
                                  f"与 {len(texts)} 条记忆不符 ⇒ "
                                  f"拒绝重算（长度不符会混库）")}
        except Exception as e:  # noqa: BLE001
            # 库**还没被改**（重算发生在替换之前）⇒ 原样回滚，不产生半吊子状态
            self.embedder, self.dim, self.embedder_id = old_embedder, old_dim, old_id
            return {"ok": 0, "failed": len(notes_list), "rolled_back": True,
                    "error": f"{type(e).__name__}: {e}"}
        ok = bad = 0
        for n, v in zip(notes_list, vecs):
            if not v or len(v) != self.dim or not any(x != 0.0 for x in v):
                bad += 1
                self.notes[n.note_id] = replace(
                    n, embedding=[],
                    tags=tuple(n.tags) + ("embed_missing",))
                continue
            ok += 1
            self.notes[n.note_id] = replace(
                n, embedding=list(v), embedder=self.embedder_id,
                tags=tuple(t for t in n.tags if t != "embed_missing"))
        #: ⛔ `old_id/old_dim` 在被 `load_for_reindex` 装载后已经是**新**值，
        #:    真实来源只能由调用方传进来（否则 "from == to" 是句废话）
        out = {"ok": ok, "failed": bad, "rolled_back": False,
               "from": from_label or f"{old_id}:{old_dim}",
               "to": f"{self.embedder_id}:{self.dim}"}
        if not notes_list:
            out["note"] = "empty_library"
        return out

    # ── 视图 ──────────────────────────────────────────────────────
    def layer_sizes(self) -> Dict[str, int]:
        out = {t: 0 for t in TIERS}
        for n in self.notes.values():
            out[n.tier] = out.get(n.tier, 0) + 1
        return out

    def integrity(self) -> Dict[str, Any]:
        """完整性自检：多少条能真正被检索到（V5：写入成功 ≠ 可用）。"""
        tot = len(self.notes)
        ok = sum(1 for n in self.notes.values() if n.has_embedding)
        no_link = sum(1 for n in self.notes.values() if not n.links)
        dims: Dict[int, int] = {}
        embs: Dict[str, int] = {}
        for n in self.notes.values():
            dims[len(n.embedding)] = dims.get(len(n.embedding), 0) + 1
            embs[n.embedder or "unknown"] = embs.get(n.embedder or "unknown", 0) + 1
        return {"total": tot, "retrievable": ok, "unretrievable": tot - ok,
                "no_links": no_link, "layer_sizes": self.layer_sizes(),
                "embedder_id": self.embedder_id, "dim": self.dim,
                "dim_histogram": dims, "embedder_histogram": embs,
                #: ⛔ 库里出现非当前 embedder 的笔记 = 混库事故，必须点名
                "foreign_embedder": sum(c for e, c in embs.items()
                                        if e != self.embedder_id),
                "stats": dict(self.stats)}

    # ── 持久化（跨运行复用）──────────────────────────────────────
    # ⛔ 2026-10-13 实测：两次 CLI 运行的记忆**完全相同**（L0:1 L2:2 L4:1）⇒
    #    记忆是纯进程内的 ⇒ "经验跨运行积累"是假的，每次都从零开始。
    # ⛔⛔ 但持久化会把**上一轮的脏记忆**也一起复活（自述 / 占位 / 未验证），
    #    于是装载必须过污染闸，否则跨运行累积 = 跨运行累积噪声。
    def snapshot(self) -> Dict[str, Any]:
        """序列化。**带 embedder_id 与 dim** —— 这是装载时的对齐契约。"""
        return {"schema": 1, "embedder_id": self.embedder_id,
                "dim": self.dim, "min_content_len": self.min_content_len,
                "notes": [{**n.__dict__, "embedding": list(n.embedding)}
                          for n in self.notes.values()]}

    def save(self, path: str | Path) -> int:
        """原子写（⛔ 半截 JSON 会让下一轮**整个库**读不出来）。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
        return len(self.notes)

    @classmethod
    def load_for_reindex(cls, path: str | Path, new_embedder: Any = None, *,
                         min_content_len: Optional[int] = None
                         ) -> Tuple["MemoryOS", Dict[str, Any]]:
        """**只装载内容、丢弃旧向量** —— `reindex()` 的唯一合法前置。

        ⛔⛔ 这是拒载之后的**恢复路径**，不是绕过闸的后门。区别在语义：
        拒载时装出来的库是空的（"我读不了"）；这里装出来的库是**无向量的**
        （"我读得了，但每条都必须重算才能用"）。后者必须紧接着调用
        `reindex()`，任何一条漏算都会以 `embed_missing` 显式留痕，
        而 `integrity()` 会把它们点名 —— 不存在"看起来能用"的中间态。

        ⛔ 旧向量一律丢弃：异维向量混在库里就是 V5 那 169 条的形态
        （写入成功、检索永远搜不到、且没有任何报错）。

        🟡 修复十一：装载后调 `dedup_tags()` —— 与 `load()` 一致。
            原版漏了这步，`load_for_reindex + reindex + save` 的路径
            不会清理重复标签（老 `consolidated` × 19 会保留下来）。
        """
        rep: Dict[str, Any] = {"path": str(path), "loaded": 0,
                               "dropped_vectors": 0, "rejected_unverified": 0,
                               "reasons": []}
        mem = cls(embedder=new_embedder)
        p = Path(path)
        if min_content_len is not None:
            mem.min_content_len = int(min_content_len)
        if not p.exists():
            rep["note"] = "no_snapshot（没有可重算的记忆库）"
            return mem, rep
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            rep["note"] = f"unreadable（{type(e).__name__}: {e}）"
            return mem, rep
        #: ⛔ 记账必须说真话：`reindex()` 会拿 `self.embedder_id` 当"来源"，
        #:    而这里 mem 已经指向**新** embedder ⇒ 不记录文件里的真实来源，
        #:    报告就会写成 "from hashing:256 → to hashing:256"（同义反复，
        #:    看起来像什么都没干，实际全量重算过）。
        rep["from_embedder"] = str(payload.get("embedder_id") or "unknown")
        rep["from_dim"] = int(payload.get("dim") or 0)
        for raw in (payload.get("notes") or []):
            try:
                note = MemNote(**{k: v for k, v in raw.items()
                                  if k in MemNote.__dataclass_fields__})
            except Exception:  # noqa: BLE001
                continue
            if len(note.embedding):
                rep["dropped_vectors"] += 1
            ok, why = memory_purity(note)
            if not ok:
                rep["rejected_unverified"] += 1
                if len(rep["reasons"]) < 5:
                    rep["reasons"].append(why)
                continue
            #: 向量清空 + 打标 ⇒ 没重算的条目一眼可见，不会被当成可用记忆
            note = replace(note, embedding=[],
                           tags=tuple(note.tags) + ("embed_missing",))
            mem.notes[note.note_id] = note
            mem._by_fp[note.fingerprint] = note.note_id
            rep["loaded"] += 1
        #: 🟡 修复十一：与 `load()` 一致 —— 压平重复标签（存量自愈）。
        rep["tags_deduped"] = mem.dedup_tags()
        rep["note"] = (f"装载 {rep['loaded']} 条内容（丢弃 {rep['dropped_vectors']} "
                       f"条旧向量）⇒ **必须立刻 reindex()**")
        return mem, rep

    @classmethod
    def load(cls, path: str | Path, embedder: Any = None, *,
             dim: int = 0, link_topk: int = 3,
             min_content_len: Optional[int] = None,
             purity: bool = True) -> Tuple["MemoryOS", Dict[str, Any]]:
        """从磁盘装载记忆。返回 (MemoryOS, 装载报告)。

        ⛔⛔ **embedder 不匹配 ⇒ 整库拒载**，而不是"能读几条读几条"：
        换嵌入后绝对余弦跨 embedder 不可比，混库的表现是"库越来越大、
        检索越来越差"，且没有任何报错（V5 教训：float32 与 json 混在一张表里，
        169 条全部解析失败却写着"写入成功"）。要换嵌入必须显式 `reindex()`。

        ⛔⛔ 继承来的记忆一律打 `inherited` 标记，**不得进入 L4 通路**
        （2026-10-13 实测）：跨运行累积会让同一个种子 harness 的基线漂移 ——
        空库时 base=0.0、装载后 base=0.6，同一个 h0 的能力取决于历史状态
        ⇒ 进化结果不可复现 ⇒ 回归测试失去意义。而且那 0.6 不是 harness 变强了，
        是记忆在**冒充能力**：真实弱点（没有技能）被抬高后的基线掩盖住。
        于是继承的记忆只做一件事 —— 让提议器少走弯路（不重复提被闸拒的变异），
        绝不参与 reward 判据。本轮执行产生的记忆才进 L4。

        ⛔ 逐条还要过三道闸：维度一致 / 字段完整 / 内容可信（污染闸）。
        """
        rep: Dict[str, Any] = {"path": str(path), "loaded": 0,
                               "rejected_embedder_mismatch": 0,
                               "rejected_dim_mismatch": 0,
                               "rejected_no_embedding": 0,
                               "rejected_unverified": 0, "reasons": []}
        mem = cls(embedder=embedder, dim=dim, link_topk=link_topk)
        p = Path(path)
        if not p.exists():
            rep["note"] = "no_snapshot（首次运行，从空记忆开始）"
            return mem, rep
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            # ⛔ 读不出来 ⇒ **空库 + 显式记账**，绝不带着半个库继续跑
            rep["note"] = f"unreadable（{type(e).__name__}: {e}）⇒ 从空记忆开始"
            return mem, rep
        if min_content_len is not None:
            mem.min_content_len = int(min_content_len)

        file_emb = str(payload.get("embedder_id") or "")
        file_dim = int(payload.get("dim") or 0)
        if file_emb and file_emb != mem.embedder_id:
            rep["rejected_embedder_mismatch"] = len(payload.get("notes") or [])
            rep["note"] = (f"整库拒载：文件 embedder={file_emb} ≠ 当前 "
                           f"{mem.embedder_id} ⇒ 需显式 reindex() 全量重算")
            return mem, rep
        if file_dim and file_dim != mem.dim:
            rep["rejected_dim_mismatch"] = len(payload.get("notes") or [])
            rep["note"] = f"整库拒载：文件 dim={file_dim} ≠ 当前 {mem.dim}"
            return mem, rep

        for raw in (payload.get("notes") or []):
            try:
                note = MemNote(**{k: v for k, v in raw.items()
                                  if k in MemNote.__dataclass_fields__})
            except Exception:  # noqa: BLE001
                rep["rejected_no_embedding"] += 1
                continue
            if len(note.embedding) != mem.dim:
                rep["rejected_dim_mismatch"] += 1
                continue
            if not note.has_embedding:
                rep["rejected_no_embedding"] += 1
                continue
            if purity:
                ok, why = memory_purity(note)
                if not ok:
                    rep["rejected_unverified"] += 1
                    if len(rep["reasons"]) < 5:
                        rep["reasons"].append(why)
                    continue
            # ⛔ `inherited` 标记：这条是**跨运行继承**的，不是本轮执行产生的。
            #    见 `load` 的 docstring —— 继承的记忆只供提议器参考，
            #    不得进入 L4 通路，否则基线会随历史漂移。
            if "inherited" not in note.tags:
                note = replace(note, tags=tuple(note.tags) + ("inherited",))
            note = replace(note, embedder=mem.embedder_id)
            mem.notes[note.note_id] = note
            mem._by_fp[note.fingerprint] = note.note_id
            rep["loaded"] += 1
        #: 存量自愈（幂等）：旧的 consolidate 无脑追加 consolidated 标签，
        #: 生产库里同一条 note 最多重复 19 次。装载即压平，不必等下次巩固。
        rep["tags_deduped"] = mem.dedup_tags()
        return mem, rep


def _higher_tier(a: str, b: str) -> str:
    """去重合并时取"更稳定"的层（L0 > L2 > L3 > L4）。"""
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
    return a if order.get(a, 9) <= order.get(b, 9) else b


def _auto_keywords(text: str, k: int = 6) -> List[str]:
    toks = _WORD.findall(text or "")
    freq: Dict[str, int] = {}
    for t in toks:
        if len(t) == 1 and not ("\u4e00" <= t <= "\u9fff"):
            continue
        freq[t] = freq.get(t, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:k]]


def _extractive_summary(notes: List[MemNote]) -> str:
    """抽取式摘要（**不编造**）：取原句中最像结论的一句。

    ⛔ 默认绝不用生成式摘要：离线巩固里一次幻觉会永久污染 L2 事实层，
    而 L2 是会被 promote 去影响 harness 的。要生成式就自己传 summarizer。

    ⛔ 保留此函数是**接口兼容**考虑（外部调用方可能仍直接使用它作为
        `summarizer=`）。`consolidate` 的内部默认走 `_inductive_summary_ex`
        —— 那是跨条归纳，语义不同于"逐条抽首句"。
    """
    if not notes:
        return ""
    parts = []
    for n in notes:
        sents = re.split(r"[。！？\n]", n.content)
        sents = [s.strip() for s in sents if len(s.strip()) >= 12]
        if not sents:
            continue
        parts.append(sents[0])
    return " ".join(parts)[:400]


# ── 跨条归纳（巩固的正确形态）──────────────────────────────────────
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _skeleton(text: str) -> str:
    """把数字替换成占位符，得到该条记录的"模板骨架"。

    纯机械正则替换，**不做任何语义归纳** ⇒ 不可能编造。
    骨架相同 = 两条记录由同一个模板产出，才谈得上"归纳出模式"。
    """
    return _NUM.sub("#", text or "").strip()


def _slots(text: str) -> Tuple[str, ...]:
    """取出该条记录里所有数字（= 骨架上的"槽位取值"）。"""
    return tuple(_NUM.findall(text or ""))


def _inductive_summary_ex(notes: List[MemNote], max_chars: int = 400
                          ) -> Tuple[str, str]:
    """跨 note 的抽取式归纳：返回 `(text, empty_reason)`。

    `empty_reason` 在 text 非空时恒为空串；为空时它的取值用来让
    `consolidate()` **分账**（skipped_empty vs skipped_not_isomorphic）。

    ⛔⛔ 巩固的语义是**聚合**，不是逐条复制（2026-10-14 修）：
    旧实现 `for n in pool: summ([n])` 逐条调 `_extractive_summary`，
    而抽取式摘要取的是首句 ⇒ 对单条 note 而言**摘要恒等于原文**；
    写回 L3 时内容指纹必然命中它自己 ⇒ 一律 `deduped` ⇒ 产出恒 0。
    （实测：生产库 33 条 L4，consolidate 返回 0 条。）

    归纳输出在文本上**必然不同于任何单条原文**（多了骨架与槽位分布），
    所以不会自我去重；而"同样一组再巩固一次"仍会 dedup —— 那是对的：
    没有新信息就不该产出新知识。

    ⛔ 全程仍是抽取：只搬运原文里**出现过**的数字与句子，不生成新句子
    ⇒ 不会把幻觉带进会被 promote 的 L2/L3。

    ⛔ `empty_reason` 的四个取值：
        · "insufficient_notes"  —— 少于 2 条（不该发生，`consolidate`
          会先按 min_group 拦）；
        · "no_numeric_slots"    —— 没有数字槽位（无法归纳"模式"）；
        · "slot_count_mismatch" —— 槽位数不一致；
        · "skeleton_mismatch"   —— 槽位数相同但文字骨架不同（不同代模板）。
    前两个归 `skipped_empty`，后两个归 `skipped_not_isomorphic`
    —— 处置完全不同（"素材不足"vs"不同构"）。
    """
    if len(notes) < 2:
        return "", "insufficient_notes"
    skel = _skeleton(notes[0].content)
    slots = [_slots(n.content) for n in notes]
    nslot = len(slots[0])
    if nslot == 0:
        return "", "no_numeric_slots"
    if any(len(s) != nslot for s in slots):
        return "", "slot_count_mismatch"
    if any(_skeleton(n.content) != skel for n in notes):
        #: ⛔ 槽位数**相同但文字骨架不同**（如 "基线 reward=1，候选 2 个"
        #:    vs "上限 reward=3，阶段 4 个"）：数字个数一样但根本不是同一个
        #:    模板 ⇒ 拿第一条的骨架去归纳会编造出一个两条都不成立的"模式"
        #:    ⇒ 必须拒绝。这是"抽取式"的最后一道闸：宁可不产出，不误导。
        return "", "skeleton_mismatch"
    cols: List[Tuple[str, ...]] = []
    for j in range(nslot):
        vals: List[str] = []
        for s in slots:
            if s[j] not in vals:
                vals.append(s[j])
        cols.append(tuple(vals))
    bits: List[str] = []
    for j, vals in enumerate(cols):
        if len(vals) == 1:
            bits.append(f"槽位{j}恒为 {vals[0]}")
        else:
            shown = "/".join(vals[:8]) + ("..." if len(vals) > 8 else "")
            bits.append(f"槽位{j}取 {len(vals)} 种：{shown}")
    head = f"归纳 {len(notes)} 条同类归档，模板：{skel}"
    return (head + " | " + "；".join(bits))[:max_chars], ""


def _inductive_summary(notes: List[MemNote], max_chars: int = 400) -> str:
    """`_inductive_summary_ex` 的**向后兼容**薄包装（返回纯字符串）。

    ⛔ 保留它是因为 `summarizer=` 的签名是 `Callable[[List[MemNote]], str]`
        —— 破坏它会让所有外部 summarizer 全不兼容。
    """
    return _inductive_summary_ex(notes, max_chars)[0]
