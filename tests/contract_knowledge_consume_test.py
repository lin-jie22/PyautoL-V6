"""知识「消费侧」契约：写入成功 ≠ 可用（第 75 条）。

跑：    python tests/contract_knowledge_consume_test.py

═══ 为什么单独一份 ═══

`contract_learner_test.py` 钉的是**写入侧**（谁能写、去重、配额）与
**加载侧**（save→load 往返），但**检索侧**还有第三道闸 ——
`memory.py:430`：

    if self.min_content_len and len(n.content) < self.min_content_len:
        continue

这三道闸互不抵消：一条内容可以「写入 written + 往返还在」却**永远检索
不到** ⇒ 账本上写着 28 条知识，消费侧读数恒为 0。r59 实测：
19 字空壳 2 条，阶段 B 检索 24 次，`Δaccess = 0`；修成 79/106 字后
同样 24 次检索，`Δaccess = 102`（扣掉写入侧 dedup 后真消费 ≥ 96）。

⛔ 每组都配反证组：单条绿可能只是「字段恒真」。
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是「还没接线」，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS ✓ {name}")
    else:
        print(f"  FAIL ✗ {name}  ← {detail}")
        FAILS.append(name)


#: 生产实测形态（r59）：空壳 vs 修后
SHORT = "missing_skill / t0："
LONG = ("missing_skill / textscore：checks_failed:top_n_basic; "
        "checks_failed:hollow_empty")
Q = "missing_skill textscore checks_failed"


def main() -> int:
    from aether.memory import MemoryOS

    def mk(min_len: int = 0) -> MemoryOS:
        return MemoryOS(min_content_len=min_len)

    print("=" * 68)
    print("A 组 · 检索侧长度闸（`memory.py:430`）—— 短条目永远进不了召回")

    m60 = mk(60)
    n_s, d_s = m60.write("L3", SHORT, channel="learner", source="a")
    n_l, d_l = m60.write("L3", LONG, channel="learner", source="a")
    got60 = {x.note_id for x in m60.search(Q, top_k=10, expand_links=False)}
    ck("A1 ⛔ 19 字条目**不出现在**检索结果里（写入了但读不到）",
       n_s.note_id not in got60, f"got={sorted(got60)}")
    ck("A2 ⛔ 反证（不是「全都检索不到」）：79 字条目**在**结果里",
       n_l.note_id in got60, f"got={sorted(got60)}")

    m0 = mk(0)
    n_s0, _ = m0.write("L3", SHORT, channel="learner", source="a")
    n_l0, _ = m0.write("L3", LONG, channel="learner", source="a")
    got0 = {x.note_id for x in m0.search(Q, top_k=10, expand_links=False)}
    ck("A3 ⛔ 反证（闸**有牙**）：把下限设成 0 ⇒ 同一条短内容**能**被召回",
       n_s0.note_id in got0 and n_l0.note_id in got0, f"got={sorted(got0)}")

    print("B 组 · 三道闸互不抵消：写入 written ∧ 往返还在 ⇒ 仍可能检索不到")

    ck("B1 写入侧对短内容返回 `written`（不拒，只标 `below_min_content`）",
       d_s == "written", d_s)
    ck("B2 ⛔ 写入成功 ∧ 在库中 ∧ 检索不到 ⇒ **死库存**（写入侧读数说成功）",
       d_s == "written" and n_s.note_id in m60.notes
       and n_s.note_id not in got60,
       f"disp={d_s} in_notes={n_s.note_id in m60.notes}")
    ck("B3 ⛔ 对照组：够长的内容三道闸**都过得去**",
       d_l == "written" and n_l.note_id in got60, d_l)

    print("C 组 · `access_count` 有两个增长点 ⇒ 「涨了」不等于「被读了」")

    m = mk(0)
    n1, dd1 = m.write("L3", LONG, channel="learner", source="a")
    a1 = m.notes[n1.note_id].access_count
    n2, dd2 = m.write("L3", LONG, channel="learner", source="b")
    a2 = m.notes[n1.note_id].access_count
    ck("C1 重复写同一内容 ⇒ `deduped` 且 access **+1**（**假消费**）",
       dd2 == "deduped" and a2 == a1 + 1, f"{dd2} {a1}→{a2}")
    m.search(Q, top_k=10, expand_links=False)
    a3 = m.notes[n1.note_id].access_count
    ck("C2 检索命中 ⇒ access **+1**（**真消费**）",
       a3 == a2 + 1, f"{a2}→{a3}")
    ck("C3 ⛔ 两者在读数上**同形** ⇒ 判「真被消费」必须扣掉写入次数，"
       "或另设检索侧独立计数（第 75 条）",
       a2 - a1 == a3 - a2 == 1, f"dedup 增量={a2-a1} search 增量={a3-a2}")

    print("D 组 · `promote` 后按 **note_id** 追踪（r59 实测踩的坑）")

    mp = mk(0)
    np_, _ = mp.write("L3", LONG, channel="learner", source="a")
    ok, why = mp.promote(np_.note_id, verifier=lambda x: (True, "ok"))
    after = mp.notes[np_.note_id]
    ck("D1 promote 成功 ⇒ tier→L2、channel→curated，但 **note_id 不变**",
       ok and after.tier == "L2" and after.channel == "curated"
       and after.note_id == np_.note_id,
       f"ok={ok} why={why} tier={after.tier} ch={after.channel}")
    learner_now = [n for n in mp.notes.values() if n.channel == "learner"]
    ck("D2 ⛔ 反证：只按 `channel=='learner'` 过滤 ⇒ 晋级条目被读成"
       "「消失了」（观测口径必须按 note_id）",
       ok and not learner_now and len(mp.notes) == 1,
       f"learner 剩 {len(learner_now)} 条，全库 {len(mp.notes)} 条")
    ck("D3 ⛔ 反证（D1 有牙）：verifier=None ⇒ fail-closed 不提升",
       mp.promote(np_.note_id)[0] is False)

    print("E 组 · 形态正确 ≠ 可用（第 24 条：形态与可用性要分开量）")

    pat = re.compile(r"^[a-z_]+ / [^：]+：\S")     #: 冒号后**有**证据
    pre = re.compile(r"^[a-z_]+ / [^：]+：")        #: 只看前缀结构
    ck("E1 修后知识形态 = `kind / env：证据`（与生产被消费 1600+ 次的化石同形）",
       bool(pat.match(LONG)) and len(LONG) >= 60,
       f"len={len(LONG)}")
    #: ⛔ 空壳的**前缀结构完全一样**，差别只在冒号后有没有东西 ——
    #:   所以「格式对不对」根本区分不了两者，必须量长度 / 实际可检索性。
    ck("E2 ⛔ 反证：空壳的**前缀形态也对**（`kind / env：`，只是冒号后为空）"
       " ⇒ 别拿形态当可用性判据",
       bool(pre.match(SHORT)) and not bool(pat.match(SHORT))
       and len(SHORT) < 60 and n_s.note_id not in got60,
       f"len={len(SHORT)} 前缀匹配={bool(pre.match(SHORT))} "
       f"完整匹配={bool(pat.match(SHORT))}")

    print("=" * 68)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
    else:
        print("✅ 知识消费侧契约全部通过：短内容三道闸后仍不可召回、"
              "两个 access 增长点可区分、晋级条目按 note_id 追踪")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
