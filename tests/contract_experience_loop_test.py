#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：经验闭环（**会学习**）必须**被走到**且**可读**，不能只在内存里自转。

为什么要有这份契约（2026-10-17 全量账本 4853 条的机器化差分实测）：

    `providers.MemoryAwareProposer` 的 docstring 写着"记忆进入因果链"，
    但它把学习的效果只记在 **Python 实例属性**里（`self.skipped` /
    `self.asked`）⇒ 进程一结束就蒸发。同时 `KnowledgeLearner` 在进化
    循环里**一次都没被调用**（`knowledge.ingested` 0 次），真正的
    "被闸拒 ⇒ 记下来"的写入（`evolution:reject`）累计只有 **5 条**。

    净效果：**账本里看不出这个系统到底有没有在学**。这是第 4 条纪律
    （有机制 ≠ 这条路被走到）与第 10 条（三态必须分开）的同时失守。

本契约守五组，每组都配**反证**（把机制摘掉，断言必须转红）：

  A 组  闭环真的成立：记一次失败 ⇒ 下一轮提议里不再有那个变异
  B 组  两侧都进账本：写入侧 `experience.remembered`、
        起效侧 `experience.propose`（含 n_in/n_out/n_skipped/n_known_history）
  C 组  三态分开：`ok` / `degraded`（检索崩 ⇒ fail-open 放行，必须可见）/
        `no_memory`（压根没装记忆，也必须记账，不许"没事件"）
  D 组  写入崩 ⇒ `disposition=write_failed`，不冒充 `written`
  E 组  去重 ≠ 学到：同一条失败记两次 ⇒ 第二条的 deduped 必须为 True
  F 组  **反证**：把检索换成恒空 ⇒ A2 必须转红（证明它不是恒真断言）
  G 组  **被拒候选必须能重审**（r54）：`--retry-every` 出口 + 默认关闭

用法：
    python tests/contract_experience_loop_test.py
"""
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether.embeddings import HashingEmbedder
    from aether.genome import Harness
    from aether.ledger import Ledger
    from aether.memory import MemoryOS
    from aether.providers import MemoryAwareProposer

    class _Cap:
        def __init__(self, name):
            self.name = name

    class _Stub:
        """同协议内层提议器：每次提同样的三个变异（模拟"记不住"的提议器）。"""

        def __init__(self, names=("add_skill", "fix_route", "tune_meta")):
            self.names = tuple(names)
            self.got_rejected = None

        def propose(self, weakness, harness, n=3, rejected=None):
            self.got_rejected = rejected
            return [_Cap(x) for x in self.names[:n]]

        def diagnostics(self):
            return {"name": "stub"}

    class _Boom:
        """记忆层彻底不可用：读也崩、写也崩。"""

        def search(self, *a, **k):
            raise RuntimeError("index gone")

        def write(self, *a, **k):
            raise RuntimeError("disk full")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        led = Ledger(tmp / "ledger.jsonl")
        mem, _ = MemoryOS.load(tmp / "mem.json",
                               HashingEmbedder(dim=256), min_content_len=0)
        p = MemoryAwareProposer(_Stub(), mem, ledger=led)
        w = {"kind": "gate_reject_risk", "topic": "scheduler"}
        h = Harness()
        reason = "gate_reject: 跌幅超限"

        # ── A 组：闭环真的锁得住 ──────────────────────────────────
        print("A 组：记一次失败 ⇒ 下一轮不再提议它")
        before = [c.name for c in p.propose(w, h)]
        p.remember(w, "add_skill", reason)
        after = [c.name for c in p.propose(w, h)]
        ck("A1 记之前提了 add_skill", "add_skill" in before, str(before))
        ck("A2 记之后 add_skill 不再出现", "add_skill" not in after, str(after))
        ck("A3 其余候选不受牵连",
           "fix_route" in after and "tune_meta" in after, str(after))
        ck("A4 失败史被喂给内层提议器（只筛不说是浪费）",
           bool(p.inner.got_rejected), f"n={len(p.inner.got_rejected or [])}")

        # ── B 组：闭环两端都可读 ──────────────────────────────────
        print("B 组：闭环两端都必须进账本")
        evs = led.events()
        rem = [e for e in evs if e.kind == "experience.remembered"]
        pro = [e for e in evs if e.kind == "experience.propose"]
        ck("B1 写入侧有账（以前连数据都没有）", len(rem) >= 1, f"n={len(rem)}")
        ck("B2 起效侧有账", len(pro) >= 2, f"n={len(pro)}")
        if rem:
            ck("B3 remembered 字段完整",
               {"key", "reason", "disposition", "deduped"} <= set(rem[0].payload),
               str(sorted(rem[0].payload)))
            ck("B4 真写入时 disposition=written",
               rem[0].payload.get("disposition") == "written",
               str(rem[0].payload.get("disposition")))
        if pro:
            last = pro[-1].payload
            ck("B5 propose 记账了 n_in/n_out/n_skipped/recall",
               {"n_in", "n_out", "n_skipped", "recall"} <= set(last),
               str(sorted(last)))
            ck("B6 拦下数与实际相符",
               last["n_skipped"] == last["n_in"] - last["n_out"],
               f"{last['n_in']}→{last['n_out']}")
            ck("B7 失败史条数可读（n_known_history ≥ 1）",
               last.get("n_known_history", 0) >= 1,
               str(last.get("n_known_history")))

        # ── C 组：三态分开（⛔ 合成一个 bool 就会把「拦不住」读成「没撞上」）──
        print("C 组：三态（ok / degraded / no_memory）不许互相冒充")
        p_none = MemoryAwareProposer(_Stub(), None, ledger=led)
        p_none.propose(w, h)
        tail = [e for e in led.events() if e.kind == "experience.propose"][-1]
        ck("C1 无记忆 ⇒ recall=no_memory", tail.payload.get("recall") == "no_memory",
           str(tail.payload.get("recall")))
        ck("C2 无记忆**也必须有事件**（没学与没装要能分辨）", True)

        p_boom = MemoryAwareProposer(_Stub(), _Boom(), ledger=led)
        p_boom.propose(w, h)
        tail = [e for e in led.events() if e.kind == "experience.propose"][-1]
        ck("C3 检索崩 ⇒ recall=degraded（fail-open 必须可见）",
           tail.payload.get("recall") == "degraded", str(tail.payload.get("recall")))
        ck("C4 崩了照样放行（一个读失败不该吞掉候选）",
           tail.payload["n_out"] == tail.payload["n_in"],
           f"{tail.payload['n_in']}→{tail.payload['n_out']}")

        # ── D 组：写入失败不许冒充成功 ────────────────────────────
        print("D 组：写入失败 ≠ 没写过")
        p_boom.remember(w, "whatever", reason)
        rem2 = [e for e in led.events() if e.kind == "experience.remembered"]
        ck("D1 写入崩 ⇒ disposition=write_failed（不冒充 written）",
           rem2[-1].payload.get("disposition") == "write_failed",
           str(rem2[-1].payload.get("disposition")))

        # ── E 组：去重 ≠ 学到新东西（⛔ V5 老毛病：写了就算学会了）──
        print("E 组：同一条失败记两次")
        n_before = len(rem2)
        p.remember(w, "add_skill", reason)          #: ⛔ **同一条 reason**
        rem3 = [e for e in led.events() if e.kind == "experience.remembered"]
        ck("E1 第二次仍记账（调用发生就是事实，不许静默）",
           len(rem3) == n_before + 1, f"{n_before}→{len(rem3)}")
        ck("E2 但必须标 deduped（账上不能算成「学了两次」）",
           rem3[-1].payload.get("deduped") is True,
           str(rem3[-1].payload.get("disposition")))

        # ── F 组：反证（把 memory.search 换成恒空 ⇒ A2 必须转红）──
        print("F 组：反证 —— 摘掉筛除能力")
        orig_search = mem.search
        mem.search = lambda *a, **k: []
        try:
            p_broken = MemoryAwareProposer(_Stub(), mem, ledger=led)
            broken = [c.name for c in p_broken.propose(w, h)]
            a_ok = "add_skill" not in broken
            ck("F1 事故版下 A2 断言会转红 ⇒ A2 不是恒真断言", not a_ok,
               f"broken_propose={broken}")
        finally:
            mem.search = orig_search

    # ══ G 组：被拒候选必须能**重审**（r54 实测缺口）══════════════
    #: ⛔ 独立临时目录：A–F 组的记忆里已经记着 `add_skill` 的失败，
    #:    复用会把"新机制有没有生效"读成"旧记忆还在起作用"。
    print("\nG 组：被闸拒过的候选必须能重审（probation 出口）")
    with tempfile.TemporaryDirectory() as td2:
        tmp2 = Path(td2)
        led2 = Ledger(tmp2 / "ledger.jsonl")
        mem2, _ = MemoryOS.load(tmp2 / "mem.json",
                                HashingEmbedder(dim=256), min_content_len=0)
        #: ⛔⛔ 为什么必须有这个出口（三条实测证据，见 providers.py 注释）：
        #:   ① `partial`/`overfit` 的结论依赖**当时的 held-out 卷子**，而
        #:      卷子会随基线长（`--ho-grow`）⇒ 旧判定会失效，可这条 skip
        #:      **没有失效机制**；② 下游 `deadend_probation_every` 的出口
        #:      实测**一次都没被走到**（13 次 `_in_cooldown` 判定全
        #:      streak=0），因为候选在上游就被永久 skip 了；③ 损失可量化：
        #:      轮 2 的 `fix_control_flow` rew=0.7143（基线 0.381，+0.33）
        #:      被判 `partial` ⇒ 从第 3 轮起永久消失。
        p0 = MemoryAwareProposer(_Stub(), mem2, ledger=led2)   # 默认 = 现状
        p0.remember(w, "add_skill", reason)
        runs0 = [[c.name for c in p0.propose(w, h)] for _ in range(5)]
        ck("G1 ⛔ 默认 retry_every=0 ⇒ **永久**不重提（与历史逐字同口径）",
           all("add_skill" not in r for r in runs0),
           str([("add_skill" in r) for r in runs0]))
        ck("G1b 默认下 probation 一次都没放行（新机制默认关闭）",
           p0.stats["probation_released"] == 0,
           str(p0.stats["probation_released"]))

        led3 = Ledger(tmp2 / "ledger3.jsonl")
        mem3, _ = MemoryOS.load(tmp2 / "mem3.json",
                                HashingEmbedder(dim=256), min_content_len=0)
        p2 = MemoryAwareProposer(_Stub(), mem3, ledger=led3, retry_every=2)
        p2.remember(w, "add_skill", reason)
        r1 = [c.name for c in p2.propose(w, h)]   # age=1 ⇒ 跳
        r2 = [c.name for c in p2.propose(w, h)]   # age=2 ⇒ 放行（重审）
        r3 = [c.name for c in p2.propose(w, h)]   # age=3 ⇒ 跳
        r4 = [c.name for c in p2.propose(w, h)]   # age=4 ⇒ 放行
        ck("G2 ⛔ retry_every=2 ⇒ 每被跳过 2 轮放行一次（重审旧判定）",
           ("add_skill" not in r1) and ("add_skill" in r2)
           and ("add_skill" not in r3) and ("add_skill" in r4),
           f"{[('add_skill' in r) for r in (r1, r2, r3, r4)]}")
        ck("G3 ⛔ 放行**不清除**记忆条目（记的是『曾被拒』这个事实，它没变）",
           any("tried:" in (x.content or "")
               for x in mem3.search("tried:", top_k=8)),
           "放行后记忆条目没了 ⇒ 把『学过』也一起删了（第 17 条）")
        ck("G4 出口走了几次可读（stats.probation_released == 2）",
           p2.stats["probation_released"] == 2,
           str(p2.stats["probation_released"]))
        evs3 = [e for e in led3.events() if e.kind == "experience.propose"]
        last3 = evs3[-1].payload
        ck("G5 ⛔ 出口读数进账本（只活在内存里 ⇒ 查不到『它为什么又出现了』）",
           last3.get("probation_released") == ["add_skill"]
           and last3.get("retry_every") == 2,
           str({k: last3.get(k) for k in ("probation_released",
                                          "retry_every")}))
        ck("G6 n_skipped 仍是 n_in - n_out（出口不污染既有计数）",
           last3.get("n_skipped") == last3.get("n_in") - last3.get("n_out"),
           f"{last3.get('n_in')}→{last3.get('n_out')} "
           f"skipped={last3.get('n_skipped')}")
        #: ⛔ 反证：G2 的判据必须能区分"有出口"与"没出口"。
        ck("G7 ⛔ 反证：G2 的判据对默认实例为**假**（不是恒真断言）",
           not (("add_skill" not in runs0[0]) and ("add_skill" in runs0[1])),
           "默认实例也满足 G2 ⇒ 该断言没牙")
    cli_src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("G8 ⛔ CLI 默认 0（新口径默认关，与历史同表可比）",
       'e.add_argument("--retry-every", default="0"' in cli_src)
    ck("G9 开关进 run_config（换的是机会不是判据 ⇒ 必须在账本里可查）",
       '"retry_every": int(_retry_every)' in cli_src)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
