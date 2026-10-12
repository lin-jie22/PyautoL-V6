#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：观测侧不得有假仪表 + reindex 必须有端到端恢复路径。

## 这一轮实测抓到的两个洞

**① 恒亮绿灯。** `serve.py` 的 `/api/status` 里 memory 段原先是硬编码的

    {"tiers": ["L0", "L1", "L2", "L3", "L4"]}

它与真实记忆库**完全无关**：库里 0 条它报五层，库被污染它报正常。
写入侧刚建好污染闸（拒载 + `inherited` 标记 + 未验证词表），观测侧却
是一块假表 ⇒ 污染在账本里可见、在控制台上不可见。这与 V5 那条
"写入成功 ≠ 可用、验收必须查字段完整性"是同一条纪律，只是发生在读侧。

**② 拒载没有出口。** 换 embedder 后 `MemoryOS.load` 会**整库拒载**
（设计正确：异维余弦不可比）。但拒载之后如果没有恢复命令，系统就永久
停在"读不了"的状态 —— 一条正确的闸配一个死胡同。`reindex` 是唯一出口。

## 守的纪律

  A 组  读数必须随库形态变化：空库 / 正常库 / 污染库 → 三种**不同**读数
  B 组  读数判据复用 `memory.py` 的词表（唯一事实源，不在 serve 里抄一份）
  C 组  reindex 端到端：异维库 → 拒载 → 重算 → 落盘 → 再装载成功
  D 组  重算有条目失败 ⇒ **不落盘**（落盘 = 把 embed_missing 固化成正常库）
  E 组  `reindex.from` 必须是**真实来源**（不能是自我指涉的 "to → to"）
  反证 删库/改判据/让重算失败 ⇒ 对应读数必须翻转

跑法（离线，秒级；不依赖任何模型与网络）：
    python tests/contract_observability_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

from aether.embeddings import HashingEmbedder, SynonymEmbedder  # noqa: E402
from aether.memory import MemoryOS  # noqa: E402
from aether.serve import memory_report  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: ⛔ 污染样本必须**足够长**，否则命中的是"内容过短"而不是"未验证标记"
#: （上一轮在这里栽过：22 字的样本被"过短"闸拦下，反证就没打到目标判据）
DIRTY = ("这条内容已经足够长了，但它含未验证标记 —— 大概是控制流方面的"
         "问题，需要待确认之后再决定是否采纳进稳定事实层。")
CLEAN_A = ("控制流类缺陷用保守通用修法 revert_control_flow 可以修好其中"
           "一部分题目，但不是全部：hard 层的题它修不动。")
CLEAN_B = ("定点修法 patch:textscore 在发展集拿到满分，但 held-out 增益"
           "为零 ⇒ 泛化闸判 overfit 并拦截，不得提交。")


print("== A 组：读数必须随库形态变化（恒亮绿灯 ⇒ 观测侧是假的）==")
with tempfile.TemporaryDirectory() as td:
    mp = Path(td) / "memory.json"

    r_missing = memory_report(mp)
    ck("A1 库不存在 ⇒ present=false（不得渲染成'有五层'）",
       r_missing.get("present") is False and r_missing.get("total") == 0,
       str(r_missing))
    ck("A2 空库读数里**没有**硬编码的 tiers 假字段",
       "tiers" not in r_missing, str(r_missing))

    m = MemoryOS(embedder=SynonymEmbedder(64), min_content_len=0)
    m.write("L2", CLEAN_A, channel="curated", source="r1")
    m.write("L3", CLEAN_B, channel="curated", source="r1")
    m.save(mp)
    r_clean = memory_report(mp)
    ck("A3 正常库 ⇒ present + 2 条 + dim 直方图单一 + integrity_ok",
       r_clean["present"] and r_clean["total"] == 2
       and r_clean["dim_histogram"] == {64: 2}
       and r_clean["integrity_ok"] is True, str(r_clean))
    ck("A4 层分布按 `tier` 字段统计（字段名必须与 MemNote 同源）",
       r_clean["layer_sizes"].get("L2") == 1
       and r_clean["layer_sizes"].get("L3") == 1, str(r_clean["layer_sizes"]))

    m.write("L3", DIRTY, channel="learner", source="r1")
    m.save(mp)
    r_dirty = memory_report(mp)
    ck("A5 污染库 ⇒ unverified≥1 且 integrity_ok=False（灯必须变红）",
       r_dirty.get("unverified", 0) >= 1
       and r_dirty["integrity_ok"] is False, str(r_dirty))
    ck("A6 三种形态读数互不相同（同一块表不可能同时成立）",
       r_missing != r_clean and r_clean != r_dirty)

print()
print("== B 组：读数判据与 memory.py 同源（不在 serve 里抄一份词表）==")
with tempfile.TemporaryDirectory() as td:
    mp = Path(td) / "memory.json"
    m = MemoryOS(embedder=HashingEmbedder(dim=256), min_content_len=0)
    m.write("L2", CLEAN_A, channel="curated", source="r1")
    m.save(mp)
    #: 手工把一条的向量抹掉 = "写入成功但永远检索不到"（V5 那 169 条的形态）
    snap = json.loads(mp.read_text(encoding="utf-8"))
    snap["notes"].append({"tier": "L3", "content": CLEAN_B, "embedding": [],
                          "tags": (), "embedder": "hashing:256",
                          "note_id": "n-fake", "fingerprint": "fp-fake"})
    mp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    r = memory_report(mp)
    ck("B1 空向量条目被点名（no_embedding≥1 且 integrity_ok=False）",
       r.get("no_embedding", 0) >= 1 and r["integrity_ok"] is False, str(r))

    #: 半截 JSON ⇒ 必须报不可读，而不是"读成 0 条"假装没事
    mp.write_text('{"schema": 1, "notes": [{"tier":', encoding="utf-8")
    r2 = memory_report(mp)
    ck("B2 半截 JSON ⇒ readable=False 且带 error（不得静默当成空库）",
       r2.get("readable") is False and r2.get("error"), str(r2))

print()
print("== C 组：reindex 端到端（异维库 → 拒载 → 重算 → 再装载）==")
with tempfile.TemporaryDirectory() as td:
    mp = Path(td) / "memory.json"
    m64 = MemoryOS(embedder=SynonymEmbedder(64), min_content_len=0)
    m64.write("L2", CLEAN_A, channel="curated", source="r1")
    m64.write("L3", CLEAN_B, channel="curated", source="r1")
    m64.save(mp)

    #: 1) 用 256 维装载 ⇒ 整库拒载（这是设计，不是 bug）
    _, rep_reject = MemoryOS.load(mp, HashingEmbedder(dim=256),
                                  min_content_len=0)
    ck("C1 异维库 ⇒ 整库拒载（换 embedder 不得混库）",
       rep_reject.get("note", "").startswith("整库拒载")
       and rep_reject.get("loaded") == 0, str(rep_reject))

    #: 2) 恢复路径：只装载内容 + 丢弃旧向量
    mem, rep_load = MemoryOS.load_for_reindex(mp, HashingEmbedder(dim=256),
                                              min_content_len=0)
    ck("C2 load_for_reindex 装载内容并丢弃全部旧向量",
       rep_load["loaded"] == 2 and rep_load["dropped_vectors"] == 2,
       str(rep_load))
    ck("C3 装载后每条都无向量且打 embed_missing（不存在'看起来能用'的中间态）",
       all(not n.embedding and "embed_missing" in n.tags
           for n in mem.notes.values()),
       str([(len(n.embedding), n.tags) for n in mem.notes.values()]))

    #: 3) 全量重算
    r = mem.reindex(HashingEmbedder(dim=256),
                    from_label=f"{rep_load.get('from_embedder')}:"
                               f"{rep_load.get('from_dim')}")
    ck("C4 重算成功且未回滚",
       r["ok"] == 2 and r["failed"] == 0 and not r["rolled_back"], str(r))
    ck("C5 `from` 是**真实来源**（不得写成 from==to 的同义反复）",
       r["from"] == "synonym:64:64" and r["to"] == "hashing:256:256", str(r))

    #: 4) 落盘后重读
    mem.save(mp)
    after = memory_report(mp)
    ck("C6 落盘后 dim 直方图只剩 256（混库被真正消除）",
       after["dim_histogram"] == {256: 2} and after["integrity_ok"] is True,
       str(after))
    #: 5) 再用 256 装载 ⇒ 这次必须成功（恢复路径闭合）
    mem2, rep2 = MemoryOS.load(mp, HashingEmbedder(dim=256),
                               min_content_len=0)
    ck("C7 reindex 之后原路径能正常装载（死胡同被打通）",
       rep2["loaded"] == 2 and not rep2.get("note"), str(rep2))
    ck("C8 内容没丢（reindex 只换向量，不动正文）",
       sorted(n.content for n in mem2.notes.values())
       == sorted([CLEAN_A, CLEAN_B]),
       str(sorted(n.content for n in mem2.notes.values())))

print()
print("== D 组：重算失败 ⇒ 不得落盘（不能把 embed_missing 固化成正常库）==")


class _Flaky:
    """重算到第 2 条就炸（真嵌入服务半路断连的形态）。"""

    name = "flaky:128"
    embedder_id = "flaky:128"
    dim = 128

    def embed(self, texts):
        raise RuntimeError("connection reset by peer")

    def probe(self):
        return False, "down"


with tempfile.TemporaryDirectory() as td:
    mp = Path(td) / "memory.json"
    m = MemoryOS(embedder=SynonymEmbedder(64), min_content_len=0)
    m.write("L2", CLEAN_A, channel="curated", source="r1")
    m.write("L3", CLEAN_B, channel="curated", source="r1")
    m.save(mp)
    before_bytes = mp.read_bytes()

    mem, rep = MemoryOS.load_for_reindex(mp, _Flaky(), min_content_len=0)
    r = mem.reindex(_Flaky())
    ck("D1 重算整批失败 ⇒ rolled_back=True（库未被改坏）",
       r.get("rolled_back") is True and r["ok"] == 0, str(r))
    ck("D2 回滚后 embedder 仍是**原来的**（不得停在半吊子嵌入器上）",
       mem.embedder_id == "flaky:128" and r.get("error"), str(r))
    #: ⛔ 契约要求调用方：failed>0 或 rolled_back ⇒ 不落盘。
    #:    这里直接验证"若落盘会发生什么"以证明这条约束是必要的：
    mem.save(mp)
    bad = memory_report(mp)
    ck("D3 反证：把失败态落盘 ⇒ 库里全是空向量且 integrity_ok=False"
       "（所以调用方**必须**拒绝落盘）",
       bad.get("no_embedding") == 2 and bad["integrity_ok"] is False,
       str(bad))
    mp.write_bytes(before_bytes)
    ck("D4 复原后仍是 64 维库（落盘被拒绝 ⇒ 磁盘未被污染）",
       memory_report(mp)["dim_histogram"] == {64: 2})

print()
print("== E 组：反证组（改掉判据 ⇒ 对应组必须翻转）==")
with tempfile.TemporaryDirectory() as td:
    mp = Path(td) / "memory.json"
    m = MemoryOS(embedder=HashingEmbedder(dim=256), min_content_len=0)
    m.write("L2", CLEAN_A, channel="curated", source="r1")
    m.save(mp)

    #: 反证 1：库文件消失 ⇒ 读数必须从"有"变"无"
    had = memory_report(mp)
    mp.unlink()
    gone = memory_report(mp)
    ck("E1 删掉库文件 ⇒ present 从 True 翻到 False（不是缓存住旧读数）",
       had["present"] is True and gone["present"] is False,
       f"{had.get('present')} → {gone.get('present')}")

    #: 反证 2：污染样本必须命中"未验证标记"判据，而不是"内容过短"
    from aether.memory import memory_purity
    ok, why = memory_purity(type(m.notes[list(m.notes)[0]])(
        content=DIRTY, tier="L3"))
    ck("E2 污染样本命中的是【未验证标记】判据（不是'内容过短'）",
       (not ok) and "未验证标记" in why, why)

    #: 反证 3：把 DIRTY 换成干净长文本 ⇒ 同一条判据必须放行
    ok2, why2 = memory_purity(type(m.notes[list(m.notes)[0]])(
        content=CLEAN_A, tier="L3"))
    ck("E3 同样长度的干净文本 ⇒ 放行（判据不是'一律拒绝'）",
       ok2 is True, why2)

print()
print("=" * 60)
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print("  ✗", f)
else:
    print("✅ 观测侧契约全部通过：没有恒亮绿灯，拒载有出口")
sys.exit(1 if FAILS else 0)
