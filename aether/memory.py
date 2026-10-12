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

_WORD = re.compile(r"[\u4e00-\u9fff]|[A-Za-z_][A-Za-z0-9_]*|\d+")

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
           "CHANNEL_WRITABLE"]


@dataclass
class MemNote:
    """统一记忆单元（MemCube 式：带来源、使用史、可迁移）。"""
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
    #  换模型后旧笔记的 embedder 不匹配 ⇒ integrity() 会点名。
    embedder: str = ""

    @property
    def fingerprint(self) -> str:
        """内容指纹：写入侧去重用（**不含** tier/时间，同内容即同一条）。"""
        return content_hash({"content": self.content.strip(),
                             "keywords": list(self.keywords)})[:16]

    @property
    def has_embedding(self) -> bool:
        """字段完整性判据（V5 血泪：入库成功但读不出来 = 没入库）。"""
        return bool(self.embedding) and any(x != 0.0 for x in self.embedding)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "embedding"}
        d["has_embedding"] = self.has_embedding
        d["dim"] = len(self.embedding)
        return d


class MemoryOS:
    """5 层记忆 + 链接演化 + 检索 + 睡眠巩固 + 对抗验证。"""

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
        self.stats = {"written": 0, "deduped": 0, "rejected_channel": 0,
                      "rejected_empty": 0, "promoted": 0, "refuted": 0,
                      "rejected_dim_mismatch": 0, "rejected_embed_error": 0,
                      "search_dim_mismatch": 0}

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
        """写一条记忆。返回 (note, 处置)。处置 ∈ {written, deduped, rejected_*}。

        ⛔ 三条闸（缺一条就会出现"库越来越大但什么也检索不到"）：
          · 通道闸：learner 不许直接写 L2 事实层（V5：写入权由通道定）；
          · 去重闸：同内容指纹只更新 access 与链接，不新增行；
          · 完整性闸：embedding 为空 ⇒ 拒绝入库（不是"降级成功"）。
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
               expand_links: bool = True) -> List[MemNote]:
        try:
            q = self._embed_one(query or "")
        except Exception:  # noqa: BLE001
            # 查询侧嵌入失败 ⇒ 记 error verdict 由调用方处理；这里退化为关键词检索
            self.stats["rejected_embed_error"] += 1
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
            seen = {n.note_id for n in out}
            for n in list(out):
                for lid in n.links:
                    if lid in seen or lid not in self.notes:
                        continue
                    seen.add(lid)
                    out.append(self.notes[lid])
        if update_access:
            ts = time.time()
            for n in out:
                self.notes[n.note_id] = replace(
                    n, access_count=n.access_count + 1, last_access=ts)
        return out

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
                    now: Optional[float] = None) -> List[MemNote]:
        """离线把 L4 情景归档 → L2/L3 稳定知识（"LLM 的 REM 睡眠"）。

        规则（保守，宁可不巩固也不制造幻觉）：
          · 只处理被访问过 >= min_access 次 或 足够老的 L4；
          · 默认 summarizer 是**抽取式**（取高频句子），不编造；
          · 巩固产物走 curated 通道写 L2/L3，并 `supersedes` 指回原 note。
        """
        ts = now if now is not None else time.time()
        pool = [n for n in self.notes.values()
                if n.tier == "L4"
                and (n.access_count >= min_access or (ts - n.created_at) >= max_age_s)]
        if not pool:
            return []
        summ = summarizer or _extractive_summary
        made: List[MemNote] = []
        for n in pool:
            text = summ([n])
            if not text.strip():
                continue
            new, how = self.write("L3", text, source=f"consolidated:{n.note_id}",
                                  channel="curated", tags=("consolidated",))
            if how == "written":
                self.notes[new.note_id] = replace(new, supersedes=n.note_id)
                made.append(self.notes[new.note_id])
            # 原 L4 归档保留（历史不删），只标记已被巩固
            self.notes[n.note_id] = replace(n, tags=tuple(n.tags) + ("consolidated",))
        return made

    # ── 剪枝（产物增殖闸）─────────────────────────────────────────
    def prune(self, *, max_notes: int = 10000,
              min_trust: float = 0.05,
              stale_s: float = 30 * 86400.0,
              now: Optional[float] = None) -> List[str]:
        """剪掉低信任 + 长期未访问的记忆。

        2026 文献点名的失败模式：sub-agent/harness artifact proliferation
        —— 产物只增不减会让系统退化。必须显式剪枝。
        ⛔ L0/L1（元规则与路由索引）**永不剪**。
        """
        ts = now if now is not None else time.time()
        victims = [nid for nid, n in self.notes.items()
                   if n.tier not in ("L0", "L1")
                   and (n.trust < min_trust
                        or (ts - n.last_access) > stale_s and n.access_count == 0)]
        over = len(self.notes) - len(victims) - max_notes
        if over > 0:
            rest = sorted((n for n in self.notes.values()
                           if n.note_id not in set(victims)
                           and n.tier not in ("L0", "L1")),
                          key=lambda n: (n.access_count, n.last_access))[:over]
            victims += [n.note_id for n in rest]
        for nid in victims:
            n = self.notes.pop(nid, None)
            if n:
                self._by_fp.pop(n.fingerprint, None)
        return victims

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
        """
        old_embedder, old_id, old_dim = self.embedder, self.embedder_id, self.dim
        self.embedder = new_embedder
        self.dim = int(getattr(new_embedder, "dim", 0) or self.dim)
        self.embedder_id = str(getattr(
            new_embedder, "embedder_id", getattr(new_embedder, "name", "callable")))
        ok = bad = 0
        try:
            texts = [n.content for n in self.notes.values()]
            vecs = new_embedder.embed(texts) if hasattr(new_embedder, "embed") \
                else [new_embedder(t) for t in texts]
        except Exception as e:  # noqa: BLE001
            # 库**还没被改**（重算发生在替换之前）⇒ 原样回滚，不产生半吊子状态
            self.embedder, self.dim, self.embedder_id = old_embedder, old_dim, old_id
            return {"ok": 0, "failed": len(self.notes), "rolled_back": True,
                    "error": f"{type(e).__name__}: {e}"}
        for n, v in zip(list(self.notes.values()), vecs):
            if not v or len(v) != self.dim or not any(x != 0.0 for x in v):
                bad += 1
                self.notes[n.note_id] = replace(n, embedding=[],
                                                tags=tuple(n.tags) + ("embed_missing",))
                continue
            ok += 1
            self.notes[n.note_id] = replace(
                n, embedding=list(v), embedder=self.embedder_id,
                tags=tuple(t for t in n.tags if t != "embed_missing"))
        #: ⛔ `old_id/old_dim` 在被 `load_for_reindex` 装载后已经是**新**值，
        #    真实来源只能由调用方传进来（否则 "from == to" 是句废话）
        return {"ok": ok, "failed": bad, "rolled_back": False,
                "from": from_label or f"{old_id}:{old_dim}",
                "to": f"{self.embedder_id}:{self.dim}"}

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
        #    而这里 mem 已经指向**新** embedder ⇒ 不记录文件里的真实来源，
        #    报告就会写成 "from hashing:256 → to hashing:256"（同义反复，
        #    看起来像什么都没干，实际全量重算过）。
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
                note.tags = tuple(note.tags) + ("inherited",)
            note.embedder = mem.embedder_id
            mem.notes[note.note_id] = note
            mem._by_fp[note.fingerprint] = note.note_id
            rep["loaded"] += 1
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
