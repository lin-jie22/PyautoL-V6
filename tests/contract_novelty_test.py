#!/usr/bin/env python3
"""契约：新颖性守卫 —— **已经存在的状态不再提交**（r64）。

═══ 为什么需要它（实测，README 140）═══

`data/ledger.jsonl` 纯诊断（`novelty.blocked_replay`，只读）：

| 读数 | 值 |
|---|---|
| `harness.commit` 总数 | **120** |
| 不同 `(parent_id, capsule)` | **12** |
| 历史上重复提交**已存在状态** | **105 次（87.5%）** |
| 去重后真实状态数 | **18** |
| 单条最狠 | 同一父 + 同一胶囊被提交 **57 遍** |

⇒ 账本写"提交 120 次"，事实是"产生了 12 个新状态"。看账本的人会以为
   自己变强了 120 次 —— 这是**读数撒谎**，不是效率问题。

此前没有任何一层看"这个状态是不是已经有了"：
  · `archive.add` 的重复检查在**闸之后**（浪费已经发生）；
  · `_in_cooldown` 只管"被拦过的"，提交成功的恰恰不在它视野里；
  · 每次运行都从同一个基线出发 ⇒ 算出的子状态与上一轮**逐字相同**。

═══ 守什么 ═══

  A 判据：内容地址（`harness_id`）是唯一身份；空变异 / 已存在都拦；
    `apply_failed` **不在这里吞**（回原路径，那是另一个事实）；
    不同胶囊走到同一状态也被同一个判据拦住（内容地址的白捡收益）。
  B 事实源：账本是事实源（跨运行的重复挡不住内存集合）；
    `parent_id` 也是"存在过的状态"；增量读必须与全量**逐元素相等**；
    文件被截断 / 被改写过 ⇒ 全量重扫（fail-closed）。
  C 接线：真跑两轮、同一父 ⇒ 第一轮提交、第二轮**不提交**且没有
    `gate.result`（省的是真预算）；全轮重复 ⇒ 独立的
    `no_novel_state`（**不许**被读成 `no_candidate_passed`）；
    关掉开关 ⇒ 恢复旧行为（开关有牙，不是恒量）。
  D 诊断：`blocked_replay` 只读、且数与手工数一致。
  E 纪律：模块不 import 任何闸；默认开；CLI 有看得见的关闭；进 run_config。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []
SUPPORTED_MODES = ("default",)

sys.path.insert(0, str(ROOT))

from aether import novelty as N                              # noqa: E402
from aether.genome import (Capsule, Gene, Harness,           # noqa: E402
                           apply_capsule, gene_add_skill)


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def cap_add(name: str, skill: str = "gen") -> Capsule:
    return Capsule(name=name,
                   genes=(gene_add_skill(skill, "修法", rationale="测试用"),))


def main() -> int:
    # ── A 组：判据 ───────────────────────────────────────────────
    print("=" * 62)
    print("A 组 · 判据：内容地址是唯一身份，不另算指纹")
    h = Harness()
    empty = N.SeenStates()
    c1 = cap_add("c1")
    v = N.judge(h, c1, empty)
    ck("A1 全新状态 ⇒ ok，且 id 就是 `apply_capsule` 的结果"
       "（判据与执行侧同源，不另算签名）",
       v.ok and v.harness_id == apply_capsule(h, c1).harness_id,
       f"{v.kind} {v.harness_id[:12]}")
    ck("A2 判出来的 harness 可直接复用（避免同一轮算两遍）",
       v.harness is not None and v.harness.harness_id == v.harness_id)

    empty.add(v.harness_id)
    v2 = N.judge(h, c1, empty)
    ck("A3 同一状态再判一次 ⇒ exists 且 blocks（这是 105/120 次的形状）",
       v2.kind == N.EXISTS and v2.blocks, v2.kind)

    v3 = N.judge(h, Capsule(name="noop", genes=()), empty)
    ck("A4 空变异 ⇒ no_change 且 blocks（与父状态逐字相同，不是'新的'）",
       v3.kind == N.NO_CHANGE and v3.blocks, v3.kind)

    bad = Capsule(name="bad", genes=(Gene(op="bogus", target="L3.skills.x",
                                          value=1, rationale="坏 op"),))
    v4 = N.judge(h, bad, empty)
    ck("A5 ⛔ 应用失败 ⇒ apply_failed 且 **blocks=False**"
       "（不许在这里吞：那是'候选坏了'，不是'重复了'）",
       v4.kind == N.APPLY_FAILED and not v4.blocks, v4.kind)

    #: ⛔ 内容地址的白捡收益：两条**不同**的胶囊，只要落点相同就是同一个状态。
    a = Capsule(name="by_set", genes=(Gene(op="set", target="L3.skills.gen",
                                           value="修法", rationale="写法甲"),))
    b = Capsule(name="by_set_too", genes=(Gene(op="set",
                                               target="L3.skills.gen",
                                               value="修法",
                                               rationale="写法乙（不同）"),))
    ida = apply_capsule(h, a).harness_id
    idb = apply_capsule(h, b).harness_id
    seen2 = N.SeenStates()
    seen2.add(ida)
    ck("A6 ⛔⛔ 两条**不同名字 / 不同 rationale** 的胶囊走到同一状态 ⇒ "
       "第二条被判 exists（内容地址，不靠名字去重）",
       ida == idb and N.judge(h, b, seen2).kind == N.EXISTS, f"{ida[:12]}")

    # ── B 组：事实源 ─────────────────────────────────────────────
    print()
    print("B 组 · 事实源是账本（跨运行的重复，内存集合挡不住）")
    from aether.ledger import Ledger

    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "l.jsonl"
        led = Ledger(lp)
        led.append("harness.commit", {"harness_id": "H1", "parent_id": "H0",
                                      "capsule": "c1", "round_no": 1},
                   actor="test")
        led.append("harness.commit", {"harness_id": "H2", "parent_id": "H1",
                                      "capsule": "c2", "round_no": 2},
                   actor="test")
        led.append("gen.probe", {"harness_id": "HX"}, actor="test")
        s = N.SeenStates().refresh(led)
        ck("B1 全量读：两条提交 ⇒ {H0,H1,H2} 三个状态（父状态也是存在过的）",
           set(s._ids) == {"H0", "H1", "H2"}, str(sorted(s._ids)))
        ck("B2 非 `harness.commit` 事件不进来（`gen.probe` 的 harness_id 不算）",
           "HX" not in s)
        ck("B3 增量读被用上了（不是每轮全扫）",
           s.stats["full"] == 1 and s.stats["reasons"]["first"] == 1,
           str(s.stats))

        #: 增量必须与全量**逐元素相等** —— 只比长度会漏掉"换了一条"。
        led.append("harness.commit", {"harness_id": "H3", "parent_id": "H2",
                                      "capsule": "c3", "round_no": 3},
                   actor="test")
        s.refresh(led)
        full = N.SeenStates().refresh(Ledger(lp))
        ck("B4 ⛔ 增量结果与另起一份全量读**逐元素相等**",
           s._ids == full._ids and s.stats["incremental"] >= 1,
           f"incr={sorted(s._ids)} full={sorted(full._ids)}")

        #: ③ 文件被截断（重建更短的账本）⇒ 偏移越界 ⇒ 必须全量重扫。
        raw = lp.read_bytes()
        keep = raw[: len(raw) // 3]
        lp.write_bytes(keep)
        s.refresh(led)
        ck("B5 ⛔ 账本变短 ⇒ 失效并全量重扫（不许抱着可能过时的缓存）",
           s.stats["invalidated"] >= 1 and "shrunk" in
           {k for k, n in s.stats["reasons"].items() if n},
           str(s.stats["reasons"]))

        #: ② 没有新增却被写过（同样可疑）⇒ 记一次失效。
        lp.write_bytes(lp.read_bytes())
        s.refresh(led)
        ck("B6 无新增但文件指纹变了 ⇒ 计入 invalidated（fail-closed 且可观测）",
           s.stats["invalidated"] >= 1, str(s.stats["invalidated"]))

    # ── C 组：接线（真跑两轮，同一个父）──────────────────────────
    print()
    print("C 组 · 接线：守卫必须在**闸之前**（闸与 held-out 的成本已经付掉了）")
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.generalization import GeneralizationGate, make_heldout
    from aether.genome import Harness as H
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Check, Verdict

    DEV, HO = "A", "B"

    def _ok(tid):
        return Verdict(passed=True, checks=(Check(tid, True, "got=1"),),
                       evidence={"env": tid})

    def _bad(tid):
        return Verdict(passed=False, checks=(Check(tid, False, "got=0"),),
                       evidence={"env": tid})

    def runner(hh, spec):
        if spec.get("frozen"):
            return _ok("frozen")
        if "gen" in hh.skills:
            return _ok(spec.get("env", ""))
        return _bad(spec.get("env", ""))

    frozen = [FrozenTask(task_id="frozen::A", spec={"env": DEV, "frozen": True}),
              FrozenTask(task_id="frozen::B", spec={"env": HO, "frozen": True})]
    dev = [FrozenTask(task_id=f"dev{i}", spec={"env": DEV}) for i in range(3)]
    cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}

    class FixedProposer:
        """每轮都提同一个胶囊 —— 正是"从同一基线重爬"的形状。"""

        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    def mk(td, name, **cfg):
        led = Ledger(Path(td) / f"{name}.jsonl")
        base = Baseline(caliber=cal,
                        verdicts={t.task_id: runner(H(), t.spec)
                                  for t in frozen},
                        harness_id="seed")
        #: ⛔ 必须是 `GeneralizationGate`（不是裸 `HeldOutSuite`）：
        #:    循环读 `heldout.env_pair`，两者协议不同（鸭子类型不同界面）。
        gate_ho = GeneralizationGate(
            make_heldout(HO, [FrozenTask(task_id=f"ho{i}", spec={"env": HO})
                              for i in range(3)]),
            dev_task_ids=[t.task_id for t in dev], base_at_ceiling=1.0)
        return EvolutionLoop(
            led, NonRegressionGate(frozen, base), dev, runner,
            FixedProposer([cap_add("c1")]), caliber=cal,
            heldout=gate_ho,
            config=EvolutionConfig(group_size=1, cleanup_enabled=False,
                                   **cfg)), led

    def count(led, kind):
        return sum(1 for e in led.iter_events() if e.kind == kind)

    with tempfile.TemporaryDirectory() as td:
        loop, led = mk(td, "on")
        r1 = loop.run_round(H(), round_no=1)
        ck("C1 第一轮：状态是新的 ⇒ 照常提交（守卫**不误伤**）",
           r1.committed is not None and r1.duplicates_blocked == 0,
           f"committed={r1.committed} blocked={r1.duplicates_blocked} "
           f"reason={r1.revert_reason[:50]}")

        g_before, c_before = count(led, "gate.result"), count(led, "harness.commit")
        #: ⛔ 同一个父（模拟"第二次运行从同一基线重爬"）⇒ 子状态逐字相同。
        r2 = loop.run_round(H(), round_no=2)
        ck("C2 ⛔⛔ 第二轮：同一个父 + 同一个胶囊 ⇒ **不提交**（这是 105/120 的形状）",
           r2.committed is None and r2.duplicates_blocked == 1
           and count(led, "harness.commit") == c_before,
           f"committed={r2.committed} blocked={r2.duplicates_blocked}")
        ck("C3 ⛔ 守卫在**闸之前**：被拦的候选没有产生 `gate.result`"
           "（省的是真预算，不只是记账）",
           count(led, "gate.result") == g_before,
           f"gate={g_before}->{count(led, 'gate.result')}")
        ck("C4 账本里有 `evolution.novelty_rejected`（拦了必须留痕，不许静默）",
           count(led, "evolution.novelty_rejected") == 1,
           str(count(led, "evolution.novelty_rejected")))

        #: ⛔ 全轮重复 ⇒ 必须与"闸拦下"分开说：前者换招也无用，
        #:    后者值得重试。混在一起会把"没新东西"读成"候选太差"。
        r3 = loop.run_round(H(), round_no=3)
        ck("C5 ⛔⛔ 全部候选都是已存在状态 ⇒ 独立原因 `no_novel_state` + "
           "`idle=True`（**不许**记成 `no_candidate_passed` / `generalization_gate`）",
           r3.idle and "no_novel_state" in r3.revert_reason
           and "no_candidate_passed" not in r3.revert_reason,
           r3.revert_reason[:70])
        ck("C6 `evolution.no_novelty` 事件落账（'为什么这一轮没做事'可查）",
           count(led, "evolution.no_novelty") >= 1,
           str(count(led, "evolution.no_novelty")))

    with tempfile.TemporaryDirectory() as td:
        #: ⛔ 反证：关掉开关 ⇒ 第二、三轮必须照旧提交（恢复旧口径）。
        #:    否则"守卫生效"可能只是"夹具本来就只提交一次"。
        loop2, led2 = mk(td, "off", novelty_guard=False)
        q1 = loop2.run_round(H(), round_no=1)
        q2 = loop2.run_round(H(), round_no=2)
        q3 = loop2.run_round(H(), round_no=3)
        ck("C7 ⛔⛔ 反证：`novelty_guard=False` ⇒ 三轮全部提交"
           "（证明 C2/C5 不是夹具的必然结果）",
           all(r.committed is not None for r in (q1, q2, q3))
           and count(led2, "harness.commit") == 3,
           f"commits={count(led2, 'harness.commit')}")
        ck("C8 关掉时零 novelty 事件（关 = 真的不建、不读账本）",
           count(led2, "evolution.novelty_rejected") == 0
           and count(led2, "evolution.no_novelty") == 0)

    # ── D 组：诊断函数 ───────────────────────────────────────────
    print()
    print("D 组 · `blocked_replay` 纯诊断：守卫到底值多少")
    with tempfile.TemporaryDirectory() as td:
        lp = Path(td) / "d.jsonl"
        led = Ledger(lp)
        #: 造：H1 从 H0 出发、H1 再被提交一次（重复）、H2 从 H1 出发。
        for hid, pid in (("H1", "H0"), ("H1", "H0"), ("H2", "H1")):
            led.append("harness.commit", {"harness_id": hid, "parent_id": pid,
                                          "capsule": "c", "round_no": 1},
                       actor="test")
        before = lp.read_bytes()
        rep = N.blocked_replay(led)
        ck("D1 3 条提交里 1 条是重复 ⇒ n_blocked=1（与手工数一致）",
           rep["n_commits"] == 3 and rep["n_blocked"] == 1
           and rep["n_distinct_states"] == 3,
           json.dumps({k: rep[k] for k in
                       ("n_commits", "n_blocked", "n_distinct_states")}))
        ck("D2 ⛔ 只读：跑完账本**逐字节不变**",
           lp.read_bytes() == before)

    # ── E 组：纪律 ───────────────────────────────────────────────
    print()
    print("E 组 · 纪律")
    src = (ROOT / "aether" / "novelty.py").read_text(encoding="utf-8")
    ck("E1 ⛔ 模块不 import 任何闸（守卫只答'状态在不在'，不答'好不好'）",
       "from .generalization" not in src and "from .nongression" not in src
       and "from .verifier" not in src, "")
    ck("E2 ⛔ 默认**开**（这不是实验口径，是读数正确性）",
       EvolutionConfig().novelty_guard is True, "")
    cli_src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("E3 CLI 有**看得见的关闭** `--no-novelty`（默认关的开关是假仪表；"
       "默认开的开关也必须能看见地关掉）",
       '"--no-novelty"' in cli_src and "no_novelty" in cli_src, "")
    ck("E4 口径进 `run_config`（换的是候选来源 ⇒ 开与关的账本不可比）",
       '"novelty_guard"' in cli_src, "")
    ck("E5 ⛔ 拦截点必须在 `apply_capsule` **之前**且不复用闸后的代码"
       "（放在闸后 ⇒ 省不下预算，等于白做）",
       src.count("apply_capsule") >= 1
       and cli_src.count("novelty_guard") >= 2, "")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条未过：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
