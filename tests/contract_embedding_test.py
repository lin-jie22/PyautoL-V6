#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：真嵌入供给 + 维度契约 + 嵌入选型闸。

## 这一轮实测出来的反直觉事实（本文件把它固化成判据）

本机 nomic-embed-text（768 维真嵌入）在**英文**上把 recall@1 从 0.20 提到
0.50、margin +0.098 ⇒ 闸**允许**切换；在**中文**上反而把它从 0.40 压到 0.10、
margin 下降 ⇒ 闸**拒绝**切换。同一个模型、同一个闸，两侧裁决相反 ——
这正好证明闸有判别力，不是恒放行也不是恒拦截。

结论：**"真嵌入一定更好"是信仰，不是事实**。绝对余弦值跨 embedder 不可比
（这与 V5 那条"分数不可跨口径比较"是同一条纪律），切换的依据只能是
排序判别力（recall@1 / MRR / margin）。

## 守的纪律

  E1 写入必须过三维契约：非空 + 非全零 + **维度 == 库绑定 dim**
  E2 真嵌入失败 ⇒ **拒写并记账**，⛔ 绝不静默降级到 hashing
     （256 维混进 768 维的库 ⇒ 余弦静默返回 0 ⇒ 库越写越废且无报错）
  E3 换 embedder 的唯一合法路径是 `reindex()` 全量重算；失败 ⇒ 原样回滚
  E4 选型闸：先证没变坏（margin 不降），再证变好（recall@1 更高）
  E5 探测必须**实测 dim**，不允许拿猜测的 dim 继续跑

跑法（离线，秒级；不依赖任何模型）：
    python tests/contract_embedding_test.py
"""
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

from aether.embeddings import (HashingEmbedder, SynonymEmbedder,  # noqa: E402
                               resolve_embedder)
from aether.memory import MemoryOS  # noqa: E402
from aether.retrieval import BenchReport, EmbeddingSwitchGate  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


class _Broken:
    """真嵌入的**失败**形态：embed 抛异常（模型没起来 / 网络断了）。"""

    name = "broken:768"
    embedder_id = "broken:768"
    dim = 768

    def embed(self, texts):
        raise RuntimeError("connection refused")

    def probe(self):
        return False, "connection refused"


class _FlakyQuery:
    """**只在查询侧**失败：写入正常、检索时嵌入挂了。

    ⛔ 为什么必须区分《写侧失败》与《查询侧失败》：前者是"库没收下"，
       后者是"库没问题，只是这次搜索降级了"。共用一个计数键时排障方向
       会被指向错误的一侧（r33：注释声称已分家，代码仍写共享键）。
    ⛔ 实现细节：写入阶段会调 embed 两次（指纹/维度自检各一次），所以用
       "第 1 次成功、之后失败"来表达"写的时候好好的，之后模型挂了"。
       `probe()` 必须始终 True —— 它只回答"这个嵌入器能不能用"，
       不是"这次调用成功了没"。
    """

    name = "flakyquery:256"
    embedder_id = "flakyquery:256"
    dim = 256

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("query-side embed down")
        return [HashingEmbedder(256).embed([t])[0] for t in texts]

    def probe(self):
        return True, "dim=256"


class _SilentlyDegraded:
    """⛔ **反例实现**：真嵌入失败时静默回退 hashing。

    它看起来"很健壮"（永远不抛异常），实际是 V5 那 169 条事故的复刻：
    256 维向量混进 768 维的库，检索时维度不符被跳过，表现为
    "知识越存越多、越搜越差"，而没有任何一条错误日志。
    """

    name = "degraded"
    embedder_id = "degraded:768"
    dim = 768
    calls = 0
    fallbacks = 0

    def embed(self, texts):
        self.calls += 1
        if self.calls == 1:
            return [[0.5] * 768 for _ in texts]      # 第一次：正常 768 维
        self.fallbacks += 1
        return [HashingEmbedder(256).embed([t])[0] for t in texts]  # 之后：256 维

    def probe(self):
        return True, "dim=768"


DOCS = [
    "写入侧去重：同内容指纹只更新 access_count，不新增行。",
    "非回归闸：冻结套件是准入条件，闸不过就不评估发展集。",
    "对抗验证：先找反证，找不到才提升；验证器为空则拒绝。",
]


def _os(embedder=None):
    return MemoryOS(embedder=embedder or HashingEmbedder(256))


print("== A 组：供给层契约（探测必须实测 dim）==")
e, rep = resolve_embedder("hashing")
ck("A1 resolve 永远返回一个可用 embedder（hashing 兜底）", e is not None)
ck("A2 探测报告记下 chosen/embedder_id/dim/degraded",
   all(k in rep for k in ("chosen", "embedder_id", "dim", "degraded")))
ok, why = HashingEmbedder(256).probe()
ck("A3 hashing 自报可用且 dim 与配置一致", ok and HashingEmbedder(256).dim == 256)
bk = _Broken()
bok, _ = bk.probe()
ck("A4 不可用的真嵌入必须自报不可用（不允许拿猜测的 dim 继续跑）", not bok)
try:
    bk.embed(["x"])
    ck("A5 真嵌入失败必须**抛异常**，绝不返回空/零向量", False, "没抛")
except RuntimeError:
    ck("A5 真嵌入失败必须**抛异常**，绝不返回空/零向量", True)

print()
print("== B 组：写入端的维度契约（E1/E2）==")
m = _os()
for d in DOCS:
    m.write("L3", d, channel="curated")
ck("B1 正常写入 3 条全部入库", m.stats["written"] == 3, str(m.stats))
ck("B2 所有入库条目的 dim 与库绑定一致",
   all(len(n.embedding) == m.dim for n in m.notes.values()))
ck("B3 integrity 报出 embedder_id 与 dim",
   m.integrity()["embedder_id"].startswith("hashing")
   and m.integrity()["dim"] == 256)
ck("B4 检索能召回（同一 query 命中自身）",
   m.search("写入侧去重：同内容指纹", top_k=1)[0].content == DOCS[0])

m2 = MemoryOS(embedder=_Broken())
_note, how = m2.write("L3", DOCS[0], channel="curated")
ck("B5 真嵌入失败 ⇒ 拒写并记账 rejected_embed_error",
   how.startswith("rejected_embed_error"), how)
ck("B6 ⛔ 且**没有**静默降级成 hashing（库里不该出现任何条目）",
   len(m2.notes) == 0 and m2.stats["rejected_embed_error"] == 1, str(m2.stats))

# ── B7：**查询侧**嵌入失败与写入侧失败必须分开记账 ────────────────
#    ⛔ 两者是完全不同的事件：写侧 = "入库被拒"（库没收下），
#       查询侧 = "这次检索降级为关键词-only"（库没问题）。
#       共用一个计数键 ⇒ 排障时无法回答"是写入坏了还是检索坏了"
#       （r33 实测：原注释声称"改用独立计数器"，代码却仍写共享键）。
m2b = MemoryOS(embedder=_FlakyQuery(), min_content_len=0)
m2b.write("L3", DOCS[1], channel="curated")
#: ⛔ 写入路径自己也会嵌入两次（一次建笔记、一次 `_link` 找邻居），而
#:    `_FlakyQuery` 从第 2 次起就挂 ⇒ 写阶段已经记过一次。
#:    判据必须看**增量**（查询这一趟只该记 1 次），而不是累计值 ——
#:    累计值会把"写侧的嵌入失败"算到查询侧头上，正是本条要防的那件事。
_before = m2b.stats["search_embed_error"]
hits = m2b.search("非回归闸 冻结套件", top_k=3)
_after = m2b.stats["search_embed_error"]
ck("B7 查询侧嵌入失败 ⇒ 只记 1 次 search_embed_error（增量口径）",
   (_after - _before) == 1
   and m2b.stats["rejected_embed_error"] == 0
   and bool(getattr(m2b, "_last_search_embed_error", "")),
   f"delta={_after - _before} stats={m2b.stats} "
   f"/ {getattr(m2b, '_last_search_embed_error', '')!r}")
ck("B7b ⛔ 写侧计数不被查询失败污染（两个事件分家）",
   m2b.stats["rejected_embed_error"] == 0, str(m2b.stats))
ck("B8 且真的走了关键词-only 路径（不是静默返回空表）",
   any(n.content == DOCS[1] for n in hits), f"hits={len(hits)}")

print()
print("== C 组：⛔ 静默降级为什么必须被闸挡住（事故版复刻）==")
m3 = MemoryOS(embedder=_SilentlyDegraded())
m3.write("L3", DOCS[0], channel="curated")     # 第一次：正常 768 维
_n3, how3 = m3.write("L3", DOCS[1], channel="curated")   # 第二次：静默降级 256 维
ck("C1 上游 embedder 静默降级（不抛异常、不报错）",
   m3.stats["rejected_embed_error"] == 0 and how3 != "written")
ck("C2 **维度闸拦下了它**：拒写并记账 rejected_dim_mismatch",
   how3.startswith("rejected_dim_mismatch")
   and m3.stats["rejected_dim_mismatch"] == 1, f"{how3} / {m3.stats}")
integ = m3.integrity()
ck("C3 库保持单 dim 纯净（没有第二种维度混进来）",
   len(integ["dim_histogram"]) == 1, str(integ["dim_histogram"]))
ck("C4 被拒写 ⇒ 内容没进库，检索必然搜不到它（宁可拒写，也不留死记录）",
   all(n.content != DOCS[1] for n in m3.search("非回归闸 冻结套件", top_k=3)))

# ⛔ 如果闸被拆掉会怎样：手动注入一条异维向量，复刻 V5 的 169 条事故
m3b = _os()
n_a, _ = m3b.write("L3", DOCS[0], channel="curated")
n_b, _ = m3b.write("L3", DOCS[1], channel="curated")
m3b.notes[n_b.note_id] = n_b.__class__(
    **{**n_b.__dict__, "embedding": [0.1] * 8})     # 绕过闸：8 维
integ_b = m3b.integrity()
ck("C5 闸被绕过 ⇒ 库里出现两种 dim（这就是要防的形态）",
   len(integ_b["dim_histogram"]) == 2, str(integ_b["dim_histogram"]))
hit = m3b.search("非回归闸 冻结套件 是 准入条件", top_k=3, expand_links=False)
ck("C6 向量通路已断：关掉链接扩散后它**搜索不到**，且静默跳过不报错",
   all(n.content != DOCS[1] for n in hit)
   and m3b.stats["search_dim_mismatch"] > 0, str(m3b.stats))
hit2 = m3b.search("非回归闸 冻结套件 是 准入条件", top_k=3)
ck("C7 但它会经**链接扩散**回来 —— 链接是独立于向量的第二条通路\n"
   "      （向量坏了不至于彻底失忆，代价是也会带回不相关条目）",
   any(n.content == DOCS[1] for n in hit2),
   str([n.content[:10] for n in hit2]))

print()
print("== D 组：reindex 是换嵌入的唯一合法路径（E3）==")
m4 = _os()
for d in DOCS:
    m4.write("L3", d, channel="curated")
before_ids = set(m4.notes)
r = m4.reindex(SynonymEmbedder(64))
ck("D1 reindex 全量成功", r["ok"] == 3 and r["failed"] == 0, str(r))
ck("D2 重算后所有条目 dim 统一为新 dim",
   all(len(n.embedding) == 64 for n in m4.notes.values()))
ck("D3 重算后 embedder_id 全部更新，foreign=0",
   m4.integrity()["foreign_embedder"] == 0, str(m4.integrity()["embedder_histogram"]))
ck("D4 条目本身没丢（reindex 只换向量，不动内容）",
   set(m4.notes) == before_ids)
top1 = m4.search("去重 同内容指纹", top_k=1, expand_links=False)
ck("D5 重算后检索仍然可用（top1 仍是语义最相关的那条）",
   bool(top1) and top1[0].content == DOCS[0],
   str([n.content[:10] for n in top1]))

m5 = _os()
for d in DOCS:
    m5.write("L3", d, channel="curated")
r5 = m5.reindex(_Broken())
ck("D6 重算整体失败 ⇒ **原样回滚**（rolled_back=True，库不变）",
   r5["rolled_back"] is True and len(m5.notes) == 3
   and all(len(n.embedding) == 256 for n in m5.notes.values()), str(r5))
ck("D7 回滚后 embedder_id 仍是原来的（不假装成功）",
   m5.embedder_id.startswith("hashing"), m5.embedder_id)

print()
print("== E 组：嵌入选型闸（E4，用实测到的两组真实数字）==")
gate = EmbeddingSwitchGate()
# 中文组实测：hashing recall 0.40 / margin -0.0471；nomic recall 0.10 / margin -0.0879
cur_zh = BenchReport("hashing:256", 256, 10, 0.40, 0.556, -0.0471)
cand_zh = BenchReport("ollama:nomic:768", 768, 10, 0.10, 0.274, -0.0879)
v_zh = gate.decide(cur_zh, cand_zh)
ck("E1 中文：margin 下降 ⇒ **拒绝**（相似度变高 ≠ 更能区分）",
   (not v_zh.allow) and "判别力下降" in v_zh.reason, v_zh.reason)
# 英文组实测：hashing recall 0.20 / margin -0.0659；nomic recall 0.50 / margin +0.0320
cur_en = BenchReport("hashing:256", 256, 10, 0.20, 0.427, -0.0659)
cand_en = BenchReport("ollama:nomic:768", 768, 10, 0.50, 0.636, 0.0320)
v_en = gate.decide(cur_en, cand_en)
ck("E2 英文：recall +0.30 且 margin 上升 ⇒ **允许**", v_en.allow, v_en.reason)
ck("E3 同一个闸两侧裁决相反 ⇒ 它不是恒放行也不是恒拦截",
   v_zh.allow != v_en.allow)
# 召回没变好 ⇒ 即使 margin 涨了也不切（换嵌入要 reindex，代价真实）
flat = BenchReport("cand", 768, 10, 0.20, 0.427, 0.5)
ck("E4 recall 持平但 margin 大涨 ⇒ 仍拒绝（无收益不付 reindex 代价）",
   not gate.decide(cur_en, flat).allow)
# 当前嵌入坏了 ⇒ 允许切换（修故障优先于比优劣）
cur_err = BenchReport("hashing:256", 256, 0, 0.0, 0.0, 0.0, error="boom")
ck("E5 当前嵌入不可用 ⇒ 允许切换（修故障优先）",
   gate.decide(cur_err, cand_en).allow)
ck("E6 候选不可用 ⇒ 拒绝", not gate.decide(cur_en, cand_zh).allow
   or not gate.decide(cur_en, BenchReport("x", 0, 0, 0, 0, 0, error="boom")).allow)

print()
print("== F 组：反证（把闸拆掉，上面的断言必须转红）==")
# F1 恒放行：**margin 降但 recall 升**这一组才能隔离出"拦的是 margin"。
#    中文实测组是两项同时变差，放宽 margin 仍会被 recall 拦住 —— 那说明
#    闸是双条件，反而测不出 E1 拦的是哪一条。
cur_f1 = BenchReport("cur", 256, 10, 0.20, 0.40, -0.050)
cand_f1 = BenchReport("cand", 768, 10, 0.40, 0.60, -0.100)
ck("F1a margin 降 / recall 升 ⇒ 默认闸**拒绝**（margin 是准入）",
   not gate.decide(cur_f1, cand_f1).allow)
lenient = EmbeddingSwitchGate(min_margin_gain=-1e9)
ck("F1b 放宽 margin 准入 ⇒ 同一组被**放行**（证明 E1 拦的确实是 margin）",
   lenient.decide(cur_f1, cand_f1).allow)
# F2 把 recall 门槛抬到不可能 ⇒ 英文那组（真实收益 +0.30）也被拒
strict = EmbeddingSwitchGate(min_recall_gain=0.9)
ck("F2 抬高 recall 门槛 ⇒ 英文组被**误拒**（证明 E2 放的是 recall 收益）",
   not strict.decide(cur_en, cand_en).allow)
# F3 维度闸**正常工作**时，同样的注入尝试根本进不了库（对照 C5 的绕过）
m6 = _os()
class _Shrinking(HashingEmbedder):
    """反证用：第一次正常、之后返回错误维度。"""
    _n = 0
    def embed(self, texts):
        self._n += 1
        if self._n == 1:
            return super().embed(texts)
        return [[0.1] * 8 for _ in texts]

m6.embedder = _Shrinking(256)
m6.write("L3", DOCS[0], channel="curated")
_n6, how6 = m6.write("L3", DOCS[1], channel="curated")
integ6 = m6.integrity()
ck("F3 维度突变 ⇒ 被闸拦下，库里始终只有一种 dim（闸不是装饰）",
   how6.startswith("rejected_dim_mismatch")
   and len(integ6["dim_histogram"]) == 1, f"{how6} {integ6['dim_histogram']}")
# F4 静默降级版 vs 正确实现：同样"不抛异常"，前者污染库、后者拒写
ck("F4 正确实现与事故实现的分水岭是 `rejected_embed_error` 计数",
   m2.stats["rejected_embed_error"] == 1 and m3.stats["rejected_embed_error"] == 0)

print()
print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}"
      f"  ({len(FAILS)} failures)")
if FAILS:
    for f in FAILS:
        print("  ✗", f)
sys.exit(1 if FAILS else 0)
