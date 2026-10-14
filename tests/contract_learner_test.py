"""知识摄入（learner 通道）契约。

跑：    python tests/contract_learner_test.py

钉的是四条**结构性**边界（其余闸在 `MemoryOS` 侧，本模块只复用不重抄）：
  1. learner 写权威层（L0–L2）⇒ **被拒**，且**不得降级**到 L3；
  2. 同内容重复摄入 ⇒ **并 access 而不是新增行**（去重复用写入侧闸）；
  3. 超配额 ⇒ 截断**并记账**（不是静默少写几条）；
  4. 写入成功 ⇒ **真的检索得到**（可达性取证，不是"报告说成功"）。

⛔ 每组都配反证/对照组：单条绿可能只是"字段恒真"。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS ✓ {name}")
    else:
        print(f"  FAIL ✗ {name}  ← {detail}")
        FAILS.append(name)


def main() -> int:
    from aether.ledger import Ledger
    from aether.learner import EVIDENCE_WINDOW, KnowledgeItem, KnowledgeLearner
    from aether.memory import MemoryOS

    tmp = Path(tempfile.mkdtemp(prefix="v6_learner_"))

    def fresh(name: str):
        led = Ledger(tmp / f"{name}.jsonl")
        mem = MemoryOS()
        mem.attach_ledger(led)
        return mem, led, KnowledgeLearner(mem, ledger=led)

    print("=" * 68)
    print("A 组 · learner 只写 L3/L4（写入权由通道定，不由声明定）")

    mem, led, lr = fresh("a")
    #: 越权：显式要求写 L2（事实层）
    rep = lr.ingest([KnowledgeItem(content="权威事实不该由 learner 写",
                                   source="untrusted", tier="L2")])
    ck("A1 ⛔ learner 写 L2 ⇒ 被拒（通道闸真生效）",
       rep.n_rejected == 1 and rep.n_written == 0
       and any("rejected_channel" in k for k in rep.by_disposition),
       f"{rep.by_disposition}")

    #: ⛔⛔ 反证核心：**不得降级**。若实现"好心"降到 L3，A1 仍绿（L2 没写进
    #: 去）但知识**真的进了库**，声明者会以为自己写进了某层 —— 假仪表。
    l2_notes = [n for n in mem.notes.values() if n.tier == "L2"]
    l3_notes = [n for n in mem.notes.values() if n.tier == "L3"]
    ck("A2 ⛔⛔ 被拒后**不得降级**入库（L2 空 且 L3 也没有这条）",
       not l2_notes and not l3_notes,
       f"L2={len(l2_notes)} L3={len(l3_notes)}")

    mem2, led2, lr2 = fresh("a2")
    rep2 = lr2.ingest([KnowledgeItem(content="这条可以进 L3", source="s",
                                     tier="L3")])
    ck("A3 对照组：写 L3 ⇒ 成功（证明 A1 是通道闸拦的，不是 learner 全拒）",
       rep2.n_written == 1 and rep2.ok, f"{rep2.by_disposition}")

    print("B 组 · 去重：同内容不新增行（复用写入侧指纹闸）")
    mem3, led3, lr3 = fresh("b")
    same = KnowledgeItem(content="Python 的 tomllib 只读，不能写 TOML",
                         source="docs.python.org", keywords=("python", "tomllib"))
    r1 = lr3.ingest([same])
    r2 = lr3.ingest([same])
    ck("B1 第二次摄入同内容 ⇒ deduped（不是新增一行）",
       r1.n_written == 1 and r2.n_deduped == 1 and r2.n_written == 0,
       f"first={r1.by_disposition} second={r2.by_disposition}")

    #: ⛔ 计数必须真实：摄入 50 条同内容 ⇒ 1 written + 49 deduped，
    #:    而不是"50 written"。这是 V5「39x 重复」的正面照妖镜。
    mem4, led4, lr4 = fresh("b2")
    r3 = lr4.ingest([same] * 50)
    ck("B2 ⛔ 重复摄入 50 次 ⇒ 库里只 1 条（49 次 deduped，不是 50 条入库）",
       r3.n_written == 1 and r3.n_deduped == 49
       and sum(1 for n in mem4.notes.values() if n.content == same.content) == 1,
       f"written={r3.n_written} deduped={r3.n_deduped} "
       f"库内={len(mem4.notes)}")

    print("C 组 · 配额：截断必须记账（不能静默少写）")
    mem5, led5, lr5 = fresh("c")
    lr5.max_items = 3
    r4 = lr5.ingest([KnowledgeItem(content=f"配额测试第 {i} 条内容",
                                   source="s") for i in range(10)])
    ck("C1 ⛔ 超配额 ⇒ n_truncated 记账（10 条只进 3，丢的 7 条有数）",
       r4.n_input == 3 and r4.n_truncated == 7 and r4.n_written == 3,
       f"input={r4.n_input} truncated={r4.n_truncated}")
    ck("C2 被截断 ⇒ ok=False（退出码/调用方能看出来，不像'都进去了'）",
       r4.ok is False, f"ok={r4.ok}")

    print("D 组 · 可达性取证：写入成功 ⇒ 真的检索得到")
    mem6, led6, lr6 = fresh("d")
    r5 = lr6.ingest([KnowledgeItem(content="Rust 的所有权模型避免了数据竞争",
                                   source="rust-book", keywords=("rust",))])
    ck("D1 ⛔ 写入后**真的能检索到**（不是只有报告说 written）",
       r5.retrievable is True and r5.n_written == 1,
       f"retrievable={r5.retrievable} probe={r5.retrieve_probe[:24]!r}")

    #: 反证：没写入 ⇒ retrievable 是 None（**没测**），不是 False
    mem7, led7, lr7 = fresh("d2")
    r6 = lr7.ingest([KnowledgeItem(content="", source="empty")])
    ck("D2 ⛔ 没写入 ⇒ retrievable=None（没测 ≠ 测了不通过，不能混）",
       r6.retrievable is None and r6.n_written == 0,
       f"retrievable={r6.retrievable}")

    print("E 组 · 账本与报告：计数与 evidence 分离")
    r7 = lr7.ingest([KnowledgeItem(content=f"批量第 {i} 条", source="s")
                     for i in range(20)])
    ck("E1 ⛔ 计数是**真实总数**，evidence 只是有界窗口（不是把总数截断）",
       r7.n_input == 20 and len(r7.evidence) <= EVIDENCE_WINDOW,
       f"n_input={r7.n_input} evidence={len(r7.evidence)}")
    evs = [e for e in led7.events() if e.kind == "knowledge.ingested"]
    ck("E2 一次摄入 = 账本一条 knowledge.ingested（含被拒的）",
       len(evs) >= 1 and "n_rejected" in evs[-1].payload,
       f"{len(evs)} 条")
    ck("E3 账本里 channel 恒为 learner（不允许调用方改写写入权）",
       bool(evs) and evs[-1].payload.get("channel") == "learner",
       str(evs[-1].payload.get("channel")) if evs else "无")
    ck("E4 报告可序列化（to_dict 进 JSON，不靠 repr）",
       isinstance(json.dumps(r7.to_dict(), ensure_ascii=False), str))

    print("F 组 · 反证：闸被绕过时会怎样（证明上面几组不是恒真）")
    #: ⛔ 直接调 memory.write 并**伪称** curated ⇒ 能写进 L2。
    #:    这说明"能不能写"完全由通道定 —— 所以 learner 的 channel 不能
    #:    是可改参数（否则这条纪律就被调用方自己废掉了）。
    mem8, led8, _ = fresh("f")
    _, disp = mem8.write("L2", "用 curated 通道写权威层", channel="curated")
    ck("F1 反证：同一条内容走 curated ⇒ 放行（写入权真的由通道定）",
       disp == "written" and any(n.tier == "L2" for n in mem8.notes.values()),
       disp)
    ck("F2 ⛔ learner 的 channel 不可被改写成 curated（构造后仍是 learner）",
       KnowledgeLearner(mem8).channel == "learner")

    print("G 组 · 持久化往返：写入侧放行 ≠ 加载侧放行（2026-10-14 实测）")
    #: ⛔ 这组来自 CLI 实测事故：摄入 2 条短知识 ⇒ 进程内 `written=2 /
    #:    retrievable=True`（看着完全成功），而 `save → load` 往返后 **2 条
    #:    全被 `rejected_unverified`（内容过短）拒载** ⇒ 知识根本没留下。
    #:    ⇒ 只在进程内取证 = 假仪表；必须查"存下来还能不能读回来"。
    import tempfile as _tf
    from aether.embeddings import HashingEmbedder
    _d = Path(_tf.mkdtemp(prefix="v6_learner_g_"))
    mem9, led9, lr9 = fresh("g")
    mem9.min_content_len = 40
    r8 = lr9.ingest([KnowledgeItem(content="这条太短", source="s")])
    ck("G1 ⛔ 过短内容 written 但必须**标出来**（n_below_min_content>0）",
       r8.n_written == 1 and r8.n_below_min_content == 1,
       f"written={r8.n_written} below={r8.n_below_min_content}")

    #: 真往返：save 到磁盘再 load 回来
    mem9.save(_d / "m.json")
    _, rl = MemoryOS.load(_d / "m.json", HashingEmbedder(dim=256),
                          min_content_len=40)
    ck("G2 ⛔ 往返后这条**真的没了**（证明 G1 的读数指的不是假想敌）",
       int((rl or {}).get("rejected_unverified", 0)) >= 1
       and int((rl or {}).get("loaded", 0)) == 0,
       f"{rl}")

    #: 对照组：够长的内容往返后必须还在（否则"过短"这个理由就是编的）
    mem10, led10, lr10 = fresh("g2")
    mem10.min_content_len = 40
    long = ("这是一条足够长的外部知识：它必须超过加载侧的最小长度闸，"
            "才能在保存与重新加载之后仍然留在记忆库里被检索到。")
    lr10.ingest([KnowledgeItem(content=long, source="s")])
    mem10.save(_d / "m2.json")
    m11, rl2 = MemoryOS.load(_d / "m2.json", HashingEmbedder(dim=256),
                             min_content_len=40)
    ck("G3 对照组：够长的内容往返后**还在**（证明拒载确实是因为过短）",
       int((rl2 or {}).get("loaded", 0)) == 1
       and int((rl2 or {}).get("rejected_unverified", 0)) == 0
       and any(n.content == long for n in m11.notes.values()),
       f"{rl2}")

    print("=" * 68)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
    else:
        print("✅ 知识摄入契约全部通过：越权不降级、重复不入库、"
              "截断有记账、写入能检索")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
