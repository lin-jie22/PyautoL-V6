#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：睡眠期巩固 —— 它必须是**跨条聚合**，不是逐条复制。

## 根因（2026-10-14 实测，不是推断）

`MemoryOS.consolidate()` 恒产出 0 条。表层看像是"L4 内容没信息量"，
真根因在**循环结构**：

    for n in pool:
        text = summ([n])                 # ← 逐条调
        self.write("L3", text, ...)      # ← 产物写回

而默认 summarizer `_extractive_summary` 取的是**首句** —— 对单条 note
而言，摘要**恒等于原文**（实测 `摘要 == 原文` 为 True）。于是写回 L3 时
内容指纹必然命中它自己的去重闸 ⇒ 一律 `deduped` ⇒ 产出恒 0。

    生产库 33 条 L4，consolidate 返回 0 条；
    把摘要换成"够长且唯一"的文本 ⇒ 立刻返回 33 条。
    ⇒ 机制本身是通的，卡在"产物 == 输入"。

## 修法

巩固的语义是**聚合**：一组同构的 L4 → 一条跨条归纳（模板骨架 + 各槽位
取值分布）。抽取式，只搬运原文里出现过的数字，不生成新句子。

## 契约（四组，E 组是事故注入反证）

    A 真的在聚合：多 ⇒ 一；产物文本不同于任何单条原文
    B 记账分开：made / deduped / skipped_* 各自独立，不合并成一个数字
    C 无副作用：consolidated 标签只打一次（旧实现重复 11~19 次）
    E 反证：换回逐条复制 ⇒ A 转红；人为污染 tags ⇒ C 转红

⛔ 为什么"记账分开"是硬契约：只报一个数字的话，"没有新信息可归纳"
   与"机制坏了"在输出里长得一模一样 —— 这正是本 bug 藏了两轮的原因。

跑法（离线，秒级）：
    python tests/contract_consolidation_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.embeddings import HashingEmbedder                      # noqa: E402
from aether.memory import (MemoryOS, _extractive_summary,           # noqa: E402
                           _inductive_summary, _skeleton, _slots)

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def mk(rounds, tmpl=None):
    """造一批 CLI 风格的 L4 归档（同一模板，只有数字不同）。"""
    m = MemoryOS(embedder=HashingEmbedder(dim=256))
    t = tmpl or ("第 {i} 轮进化：基线 reward={r:.2f}，候选 {c} 个，被拒 {j} 个，"
                 "结果=回滚，裁决 overfit，易层增益 +1.00，难层增益 +0.00，"
                 "空转 {z}")
    for i in range(1, rounds + 1):
        m.write("L4", t.format(i=i, r=0.40 + 0.10 * i, c=3, j=i, z=1 if i > 1 else 0),
                channel="system", source=f"round{i}")
    return m


print("=" * 72)
print("契约：睡眠期巩固（跨条聚合 + 分账记账 + 无副作用）")
print("=" * 72)

# ── A 组：真的在聚合 ─────────────────────────────────────────────
print("\nA 组 · 巩固必须是多 ⇒ 一的聚合，而不是逐条复制")

m = mk(3)
made = m.consolidate(min_access=0)
rep = m.consolidation_report()
ck("A1 三条同类 L4 ⇒ 产出 1 条（不是 3 条，也不是 0 条）",
   len(made) == 1, f"made={len(made)} rep={rep}")
ck("A2 分账记到 made（不是只返回列表）", rep["made"] == 1, f"rep={rep}")

if made:
    prod = made[0].content
    srcs = [n.content for n in m.notes.values() if n.tier == "L4"]
    ck("A3 产物文本**不同于任何一条原文**（这才是它不会被自我去重吃掉的原因）",
       prod.strip() not in [s.strip() for s in srcs] and all(prod.strip() != s.strip() for s in srcs))
    ck("A4 产物保留模板骨架（可溯源，不是凭空生成的句子）",
       "第 # 轮进化" in prod, prod[:90])
    ck("A5 产物标出槽位取值分布（归纳的意义所在）",
       "槽位" in prod and ("恒为" in prod or "取" in prod), prod[:120])

made2 = m.consolidate(min_access=0)
rep2 = m.consolidation_report()
ck("A6 同样一组再巩固 ⇒ made=0 且 deduped=1（没有新信息就不产出，且**有账**）",
   len(made2) == 0 and rep2["deduped"] == 1, f"rep={rep2}")

# ── B 组：记账必须分开 ───────────────────────────────────────────
print("\nB 组 · 记账分开：made / deduped / skipped_* 各自独立")

m1 = mk(1)
r1 = m1.consolidate(min_access=0)
rep1 = m1.consolidation_report()
ck("B1 只有 1 条 ⇒ made=0 且 skipped_below_min_group=1（不是静默 0）",
   len(r1) == 0 and rep1["skipped_below_min_group"] == 1, f"rep={rep1}")

m0 = MemoryOS(embedder=HashingEmbedder(dim=256))
ck("B2 空池 ⇒ 返回空且分账可区分（pool=0，不是 not_run）",
   m0.consolidate(min_access=0) == [] and m0.consolidation_report()["pool"] == 0
   and m0.consolidation_report().get("note") != "not_run")

m4 = MemoryOS(embedder=HashingEmbedder(dim=256))
m4.write("L4", "第 1 轮：基线 reward=0.40", channel="system", source="round1")
m4.write("L4", "第 2 轮：基线 reward=0.50，候选 3 个，被拒 1 个",
         channel="system", source="round2")
mm = m4.consolidate(min_access=0)
rep4 = m4.consolidation_report()
ck("B3 槽位数不同 ⇒ 不硬凑产物，且 skip **有账**（不是静默 0）",
   len(mm) == 0
   and (rep4["skipped_not_isomorphic"] + rep4["skipped_below_min_group"]) >= 2,
   f"rep={rep4}")

m9 = MemoryOS(embedder=HashingEmbedder(dim=256))
m9.write("L4", "第 1 轮：基线 reward=0.40，候选 3 个", channel="system", source="round1")
m9.write("L4", "第 2 轮：上限 reward=0.50，阶段 4 个", channel="system", source="round2")
r9 = m9.consolidate(min_access=0)
rep9 = m9.consolidation_report()
ck("B5 槽位数相同但**文字骨架不同** ⇒ 拒绝归纳（不许拿第一条骨架去编造模式）",
   len(r9) == 0 and rep9["skipped_not_isomorphic"] >= 2, f"rep={rep9}")

#: ⛔ 这条直接覆盖生产库的真实情况：旧模板把 8 位 hash 写进内容 ⇒ 数字个数
#:    随 hash 浮动（3~7 槽都有）。若只按 source 分组，新旧会混成一组 ⇒
#:    整组不同构 ⇒ 连新格式一起被 skip（实测 skipped_not_isomorphic=30）。
m10 = MemoryOS(embedder=HashingEmbedder(dim=256))
for i in (1, 2, 3):
    m10.write("L4", f"第 {i} 轮进化：基线 reward=0.40，候选 3 个",
              channel="system", source=f"round{i}")
for i in (1, 2, 3):
    m10.write("L4", (f"第 {i} 轮进化：基线 reward={0.40 + 0.10 * i:.2f}，候选 3 个，"
                     f"被拒 {i} 个，结果=回滚，裁决 overfit，易层增益 +1.00，"
                     f"难层增益 +0.00，空转 1"),
              channel="system", source=f"round{i}")
r10 = m10.consolidate(min_access=0)
ck("B6 同一 source 混着两代模板 ⇒ 按槽位数分代，各归纳 1 条（历史脏数据不拖死新机制）",
   len(r10) == 2, f"made={len(r10)} rep={m10.consolidation_report()}")

ck("B4 没跑过也返回全零表而不是 None（调用方不会拿到 None 才记账）",
   isinstance(MemoryOS(embedder=HashingEmbedder(dim=256)).consolidation_report(), dict))

# ── C 组：无副作用 ───────────────────────────────────────────────
print("\nC 组 · consolidated 标签只打一次（旧实现重复 11~19 次）")

m5 = mk(3)
for _ in range(4):
    m5.consolidate(min_access=0)
worst = max(tuple(n.tags).count("consolidated")
            for n in m5.notes.values() if n.tier == "L4")
ck("C1 连跑 4 次，单条 tags 里 consolidated 最多出现 1 次",
   worst == 1, f"实际={worst}")

m6 = mk(2)
m6.consolidate(min_access=0)
fixed1 = m6.dedup_tags()
fixed2 = m6.dedup_tags()
ck("C2 dedup_tags() 幂等（第二次为 0）", fixed2 == 0, f"第一次={fixed1} 第二次={fixed2}")

# ── E 组：事故注入反证 ───────────────────────────────────────────
print("\nE 组 · 反证（把修复退回去，断言必须转红）")


def per_note_copy(notes):
    """事故版：逐条复制（旧实现的真实行为）。"""
    return _extractive_summary([notes[0]])


m7 = mk(3)
bad = m7.consolidate(min_access=0, summarizer=per_note_copy)
rep7 = m7.consolidation_report()
ck("E1 换回逐条复制 ⇒ made 必须回到 0（证明原 bug 就在这一处，不在别处）",
   len(bad) == 0 and rep7["made"] == 0, f"made={len(bad)} rep={rep7}")
ck("E2 事故版的产出会命中去重 ⇒ deduped 被如实记账",
   rep7["deduped"] >= 1, f"rep={rep7}")

_probe = mk(1)
_only = [n for n in _probe.notes.values() if n.tier == "L4"][0]
ck("E3 逐条摘要确实恒等于原文（根因的直接断言）",
   _extractive_summary([_only]).strip() == _only.content.strip(),
   f"摘要={_extractive_summary([_only])[:40]!r} 原文={_only.content[:40]!r}")

m8 = mk(2)
nid = [k for k, n in m8.notes.items() if n.tier == "L4"][0]
n8 = m8.notes[nid]
before = n8.content
m8.notes[nid] = n8.__class__(**{**n8.__dict__, "tags": ("consolidated",) * 5})
polluted = tuple(m8.notes[nid].tags).count("consolidated")
fixed8 = m8.dedup_tags()
after = tuple(m8.notes[nid].tags).count("consolidated")
ck("E4 人为污染 tags（重复 5 次）⇒ 自愈到 1 次，且内容不变",
   polluted == 5 and fixed8 == 1 and after == 1
   and m8.notes[nid].content == before,
   f"污染={polluted} 修后={after} fixed={fixed8}")

ck("E5 骨架/槽位是纯机械抽取（不编造）：骨架只把数字换成 #",
   _skeleton("第 12 轮 reward=0.50") == "第 # 轮 reward=#"
   and _slots("第 12 轮 reward=0.50") == ("12", "0.50"))

print("\n" + "=" * 72)
print(f"结果：{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED -> ' + str(FAILS)}")
print("=" * 72)
sys.exit(1 if FAILS else 0)
