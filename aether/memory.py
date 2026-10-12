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
import math
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .ledger import content_hash

TIERS = ("L0", "L1", "L2", "L3", "L4")
#: 只有 curated 通道能写 L0/L2（authoritative 层）；learner 只能写 L3/L4
CHANNEL_WRITABLE: Dict[str, Tuple[str, ...]] = {
    "curated": ("L0", "L1", "L2", "L3", "L4"),
    "learner": ("L3", "L4"),
    "system": ("L4",),
}

_WORD = re.compile(r"[\u4e00-\u9fff]|[A-Za-z_][A-Za-z0-9_]*|\d+")


# ──────────────────────────────────────────────────────────────────
# 嵌入：可插拔，默认 hashing trick（确定性、离线、零依赖）
# ──────────────────────────────────────────────────────────────────
def hash_embedding(text: str, dim: int = 256) -> List[float]:
    """词袋 hashing trick。同文本 ⇒ 同向量（跨进程确定性）。

    ⛔ 为什么默认不用真模型：写入端不能因为"模型没加载"就写空向量
    （V5 的 169 条事故）。确定性哈希保证**任何环境**写进去都可读。
    真模型通过 `embedder=` 注入，签名一致。
    """
    vec = [0.0] * dim
    toks = _WORD.findall(text or "")
    if not toks:
        return vec
    for t in toks:
        h = int(hashlib.blake2b(t.encode("utf-8"), digest_size=8).hexdigest(), 16)
        idx = h % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    va, vb = list(a), list(b)
    if not va or not vb or len(va) != len(vb):
        return 0.0
    return sum(x * y for x, y in zip(va, vb))


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

    def __init__(self, embedder: Optional[Callable[[str], List[float]]] = None,
                 dim: int = 256, link_topk: int = 3,
                 min_content_len: int = 0):
        self.dim = dim
        self._embed = embedder or (lambda t: hash_embedding(t, dim))
        self.link_topk = link_topk
        #: ⛔ 检索下限（V5 r108a：极短条目余弦虚高，会霸占 top-k）
        self.min_content_len = min_content_len
        self.notes: Dict[str, MemNote] = {}
        self._by_fp: Dict[str, str] = {}
        self.stats = {"written": 0, "deduped": 0, "rejected_channel": 0,
                      "rejected_empty": 0, "promoted": 0, "refuted": 0}

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
        emb = self._embed(body)

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

        nid = f"{tier}:{fp}"
        note = MemNote(note_id=nid, tier=tier, content=body, keywords=kw,
                       tags=tuple(tags or ()), embedding=emb, source=source,
                       channel=channel, trust=trust, created_at=ts, last_access=ts)
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
        q = self._embed(query or "")
        allowed = set(tiers) if tiers else None
        scored: List[Tuple[float, MemNote]] = []
        for n in self.notes.values():
            if allowed and n.tier not in allowed:
                continue
            if self.min_content_len and len(n.content) < self.min_content_len:
                continue
            if not n.has_embedding:
                continue
            s = cosine(q, n.embedding)
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
        return {"total": tot, "retrievable": ok, "unretrievable": tot - ok,
                "no_links": no_link, "layer_sizes": self.layer_sizes(),
                "stats": dict(self.stats)}


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
