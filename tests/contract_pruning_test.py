#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：产物增殖闸（prune）—— 从"装饰"接成"看得见的闸"。

## 它为什么必须存在

`MemoryOS.prune()` 一直有实现，但**全仓库零调用点** ⇒ 这条闸是装饰。
2026 文献点名的失败模式是 sub-agent / harness artifact proliferation：
产物只增不减会让系统退化。闸没接线 ⇒ 退化发生时不报警、也没出口。

## ⛔ 接线方式：自动路径**只报数不删**

剪枝是**不可逆**的。所以：

    evolve 结束     → prune_dryrun()  ⇒ 只把"将剪 N 条"写进输出（可见）
    memory prune    → prune_dryrun()  ⇒ 默认同上（安全默认）
    memory prune --apply → prune()    ⇒ 真删，**逐条往账本留痕**

"闸被接上"的判据不是"会自动清理"，而是**增殖这件事第一次可读**。

## 契约

    A 判据：L0/L1 永不剪；低信任 / 陈旧零访问 / 超容量各归其位
    B 同源：dry-run 与真剪共用一份判据（不许两份，否则报 5 剪 7）
    C 留痕：删了什么必须进账本；没挂账本要显式报错，不静默
    G 端点：CLI 真的走到 prune（不是只测 MemoryOS 组件）
    E 反证：去掉 L0/L1 保护 ⇒ A 转红；去掉留痕 ⇒ C 转红；拆成两份判据 ⇒ B 转红

⛔ G 组的必要性（宪法第 39 条同族教训）：只测 `MemoryOS.prune` 的话，
   CLI 里那行根本没被调用也能全绿 —— 必须走真 CLI 并断言副作用。

跑法（离线，秒级；用临时 --root，不碰生产库）：
    python tests/contract_pruning_test.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

from aether.embeddings import HashingEmbedder                     # noqa: E402
from aether.ledger import Ledger                                  # noqa: E402
from aether.memory import MemoryOS                                # noqa: E402

FAILS = []
DAY = 86400.0


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def fresh():
    return MemoryOS(embedder=HashingEmbedder(dim=256))


NOW = 1_700_000_000.0

print("=" * 72)
print("契约：产物增殖闸（判据同源 + 不可逆留痕 + 端点真被调用）")
print("=" * 72)

# ── A 组：判据 ───────────────────────────────────────────────────
print("\nA 组 · 剪谁不剪谁")

m = fresh()
m.write("L0", "元规则：奖励只来自执行判据，改 L0 需人工审批。",
        channel="curated", source="seed", now=NOW - 900 * DAY, trust=0.0)
m.write("L1", "路由索引：任务类别到技能的映射表，长期有效不可丢弃。",
        channel="curated", source="seed", now=NOW - 900 * DAY, trust=0.0)
m.write("L3", "低信任的临时观察记录，内容足够长以便通过完整性闸。",
        channel="learner", source="tmp", trust=0.01, now=NOW)
m.write("L3", "陈旧且从没被检索过的记忆，同样写得足够长以满足长度闸。",
        channel="learner", source="tmp", trust=1.0, now=NOW - 400 * DAY)
m.write("L3", "新近写入的普通记忆，不该被任何一条判据剪掉。",
        channel="learner", source="tmp", trust=1.0, now=NOW)
before = len(m.notes)
dry = m.prune_dryrun(min_trust=0.05, stale_s=30 * DAY, now=NOW)
ck("A1 低信任 + 陈旧零访问 ⇒ 各记一条（不是混成一个数字）",
   dry["by_reason"]["low_trust"] == 1 and dry["by_reason"]["stale_unused"] == 1,
   f"{dry}")

victims = m.prune(min_trust=0.05, stale_s=30 * DAY, now=NOW)
living = {n.tier for n in m.notes.values()}
ck("A2 ⛔ L0/L1 **永不剪**（哪怕 trust=0、900 天没访问）",
   "L0" in living and "L1" in living, f"存活 tier={living}")
ck("A3 新近高信任的记忆不被剪",
   any("新近写入" in n.content for n in m.notes.values()))
ck("A4 真剪条数 == dry-run 报的条数", len(victims) == dry["n"],
   f"剪={len(victims)} 报={dry['n']}")
ck("A5 库确实变小了（不是只返回了个列表）", len(m.notes) == before - len(victims))

m2 = fresh()
for i in range(5):
    m2.write("L3", f"容量测试用的第 {i} 条记忆，信任与时效都正常。",
             channel="learner", source="cap", trust=1.0, now=NOW)
d2 = m2.prune_dryrun(max_notes=2, min_trust=0.05, stale_s=30 * DAY, now=NOW)
ck("A6 超容量 ⇒ 按 (access_count, last_access) 剪到上限，归入 over_capacity",
   d2["by_reason"]["over_capacity"] == 3 and d2["n"] == 3, f"{d2}")

# ── B 组：判据同源 ───────────────────────────────────────────────
print("\nB 组 · dry-run 与真剪必须共用一份判据")

m3 = fresh()
m3.write("L3", "低信任记忆内容足够长以通过完整性闸的检查条件。",
         channel="learner", source="t", trust=0.01, now=NOW)
n_before = len(m3.notes)
d3 = m3.prune_dryrun(min_trust=0.05, now=NOW)
ck("B1 dry-run **不删**（库大小不变）", len(m3.notes) == n_before)
ck("B2 dry-run 明确标注未执行（applied=False）", d3["applied"] is False)


class SplitBrain(MemoryOS):
    """事故版：dry-run 用另一份判据（多剪一条）。"""

    def prune_dryrun(self, **kw):
        r = super().prune_dryrun(**kw)
        return {**r, "n": r["n"] + 1}


m4 = SplitBrain(embedder=HashingEmbedder(dim=256))
m4.write("L3", "低信任记忆内容足够长以通过完整性闸的检查条件。",
         channel="learner", source="t", trust=0.01, now=NOW)
d4 = m4.prune_dryrun(min_trust=0.05, now=NOW)
v4 = m4.prune(min_trust=0.05, now=NOW)
ck("B3 ⛔ dry-run 报的数与真剪不一致 ⇒ 必须判失败（两份判据是灾难）",
   len(v4) != d4["n"], f"dry={d4['n']} real={len(v4)}")

# ── C 组：不可逆操作必须留痕 ─────────────────────────────────────
print("\nC 组 · 删了什么必须能回答")

tmp = tempfile.mkdtemp(prefix="v6_prune_")
led = Ledger(os.path.join(tmp, "ledger.jsonl"))
m5 = fresh()
m5.attach_ledger(led)
m5.write("L3", "被剪掉的这条记忆必须有账本痕迹，否则无从追责。",
         channel="learner", source="t", trust=0.01, now=NOW)
v5 = m5.prune(min_trust=0.05, now=NOW)
#: ⛔ `Ledger.events()` 返回的是 `Event` 对象（seq/ts/**kind**/actor/payload/
#:    prev_hash/hash），不是 dict，而且类型字段叫 `kind` 不叫 `type`。
#:    判据必须与被测对象同源 —— 别照抄 JSON 输出里的 `.get()`。
evs = [e for e in led.events() if getattr(e, "kind", None) == "memory.pruned"]
ck("C1 挂了账本 ⇒ 每条删除都 append `memory.pruned`",
   len(v5) == 1 and len(evs) == 1, f"victims={len(v5)} events={len(evs)}")
if evs:
    p = getattr(evs[0], "payload", None) or {}
    ck("C2 留痕带内容哈希与 tier（事后能回答'那条是什么'）",
       p.get("tier") == "L3" and bool(p.get("content_hash")), f"{p}")
ck("C3 prune_report() 给出分账（不是只返回 id 列表）",
   m5.prune_report()["n"] == 1 and "remaining" in m5.prune_report())

m6 = fresh()          # ⛔ 故意不挂账本
m6.write("L3", "没挂账本时剪枝不能静默：必须报错，而不是装作没事。",
         channel="learner", source="t", trust=0.01, now=NOW)
m6.prune(min_trust=0.05, now=NOW)
ck("C4 ⛔ 没挂账本 ⇒ **显式记失败原因**（不留痕的删除是数据事故）",
   bool(getattr(m6, "_ledger_error", "")), repr(getattr(m6, "_ledger_error", "")))

# ── G 组：端点真的被走到（不只看组件）──────────────────────────
print("\nG 组 · CLI 端点（测组件 ≠ 测端点）")

from aether.cli import main as cli_main                            # noqa: E402
import io                                                          # noqa: E402
from contextlib import redirect_stdout                            # noqa: E402


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(argv)
    return rc, buf.getvalue()


m7 = fresh()
#: ⛔ 路径必须落在 CLI 认定的位置：`--root tmp` ⇒ `tmp/data/memory.json`。
#:    存到别处的话 CLI 读到的是**空库** ⇒ prune 报 0 条、rc 还是 0 ⇒ 断言
#:    全绿但一条真数据都没碰到（我第一版就这么假绿过）。
_mem_dir = os.path.join(tmp, "data")
os.makedirs(_mem_dir, exist_ok=True)
p7 = os.path.join(_mem_dir, "memory.json")
#: ⛔ 内容必须写够 60 字符：CLI 的 `MemoryOS.load` 带 `min_content_len=60`，
#:    太短会在**装载阶段**就被拒 ⇒ 库里 0 条 ⇒ 看起来像 "prune 把库剪空了"
#:    （我第一版就是这么假红的）。造数据也要守被测对象的闸。
m7.write("L3", "CLI 端点测试用的低信任记忆。这条必须写得足够长，否则 CLI "
               "装载时的最小长度闸会直接把它拒掉，于是测试会看到零条并误判成"
               " prune 把整个库剪空了 —— 那是装载闸在工作，不是剪枝闸。",
         channel="learner", source="t", trust=0.01, now=NOW)
m7.write("L0", "元规则：奖励只来自执行判据，改 L0 需人工审批。这条同样要写够"
               "长度，否则装载阶段就被最小长度闸拒掉，根本轮不到剪枝去判断"
               "该不该剪它 —— 而 L0 是永不剪的那一类。",
         channel="curated", source="seed", trust=1.0, now=NOW)
m7.save(p7)

rc7, out7 = run_cli(["--root", tmp, "memory", "prune"])
ck("G1 `memory prune` 默认 dry-run（rc=0 且明确说未删除）",
   rc7 == 0 and "dry-run" in out7, f"rc={rc7} out={out7[:120]}")
#: ⛔ 只看 rc 和输出字样会**假绿**：空库时 rc 也是 0、字样也在，但一条真数据
#:    都没碰到。必须断言"报出的条数 > 0"才算真的跑到了数据。
_m = re.search(r"将剪 (\d+) 条", out7)
ck("G1b dry-run 报出的条数 > 0（否则是空库假绿，断言等于没写）",
   bool(_m) and int(_m.group(1)) >= 1, f"out={out7[:200]}")
n_after_dry = len(MemoryOS.load(p7, HashingEmbedder(dim=256))[0].notes)
ck("G2 dry-run **真的没删**（落盘条数不变）", n_after_dry == 2, f"notes={n_after_dry}")

rc8, out8 = run_cli(["--root", tmp, "memory", "prune", "--apply"])
n_after_apply = len(MemoryOS.load(p7, HashingEmbedder(dim=256))[0].notes)
ck("G3 `--apply` 真删了，且 L0 保住（剩 1 条）",
   rc8 == 0 and n_after_apply == 1, f"rc={rc8} notes={n_after_apply} out={out8[:160]}")
ck("G4 真删路径**声明了留痕**（输出里出现留痕字样）", "留痕" in out8, out8[:200])

# ── E 组：反证 ───────────────────────────────────────────────────
print("\nE 组 · 反证（把保护退掉，断言必须转红）")


class NoProtect(MemoryOS):
    """事故版：去掉 L0/L1 永不剪的保护。"""

    def _select_prune_victims(self, *, max_notes, min_trust, stale_s, ts):
        victims, why = super()._select_prune_victims(
            max_notes=max_notes, min_trust=min_trust, stale_s=stale_s, ts=ts)
        extra = [n.note_id for n in self.notes.values()
                 if n.tier in ("L0", "L1")]
        return victims + extra, why


mE = NoProtect(embedder=HashingEmbedder(dim=256))
mE.write("L0", "元规则：奖励只来自执行判据，改 L0 需人工审批。",
         channel="curated", source="seed", trust=0.0, now=NOW - 900 * DAY)
mE.prune(min_trust=0.05, now=NOW)
ck("E1 去掉 L0/L1 保护 ⇒ L0 **会**被剪（证明 A2 这条断言真的在守东西）",
   not any(n.tier == "L0" for n in mE.notes.values()))


class NoTrace(MemoryOS):
    """事故版：剪了不留痕。"""

    def prune(self, **kw):
        self.ledger = None
        return super().prune(**kw)


mE2 = NoTrace(embedder=HashingEmbedder(dim=256))
led2 = Ledger(os.path.join(tmp, "ledger2.jsonl"))
mE2.attach_ledger(led2)
mE2.write("L3", "不留痕版本剪掉的记忆，事后根本查不到它存在过。",
          channel="learner", source="t", trust=0.01, now=NOW)
mE2.prune(min_trust=0.05, now=NOW)
ck("E2 去掉留痕 ⇒ _ledger_error 被点亮（证明 C1/C4 不是摆设）",
   bool(getattr(mE2, "_ledger_error", "")),
   repr(getattr(mE2, "_ledger_error", "")))

mE3 = fresh()
mE3.write("L3", "同源性反证用的低信任记忆，内容长度满足完整性闸要求。",
          channel="learner", source="t", trust=0.01, now=NOW)
dE3 = mE3.prune_dryrun(min_trust=0.05, now=NOW)
vE3 = mE3.prune(min_trust=0.05, now=NOW)
ck("E3 默认实现下 dry-run == 真剪（同源的正向断言）",
   dE3["n"] == len(vE3), f"dry={dE3['n']} real={len(vE3)}")

print("\n" + "=" * 72)
print(f"结果：{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED -> ' + str(FAILS)}")
print("=" * 72)
sys.exit(1 if FAILS else 0)
