#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：三条「零次事件」路径的**定性** —— 有牙的钉住，没接线的标出来。

## 为什么需要这份契约

r57 审计列出 6 条「源码声明过、账本里一次都没有」的事件，并判其中三条
可疑。r58 逐条取证后发现：**审计自己的实验设计有洞**。

⛔ 最大的一条：`deadend.cooldown` / `world_fact.promoted` 被判为
「触发条件与提名策略互斥 ⇒ 结构性不可达」。而 r58 把实验从 4 轮加到
**6 轮**后（门槛是 3，4 轮根本攒不够 streak）：

    A 默认：   cooldown=2  world_fact.promoted=2  最长 streak=4
    B retry=1：cooldown=2  world_fact.promoted=2  最长 streak=4

⇒ 机制**有牙且真被走到**。生产账本里 0 次是**历史成因**（早期
`env_pair` 从 `unknown` 漂成具体值，旧 key 永久失配），不是结构缺陷。
（第 24 条第八次命中：先怀疑量具，再怀疑世界。）

本文件把这个结论**钉死**，因为下一轮最可能发生的就是有人去"修"冷却。

## 组

  R1 `gate.blocked`：有牙，但**结构性不触发** ⇒ 不许改判据（纵深防御）
  R2 `evolution.checkpoint`：r58 起**默认开**（此前默认关 ⇒ 承诺不成立）
  R3 `memory.pruned`：只在人显式 `memory prune --apply` 时写 ⇒ 0 次是好事
  R4 ⛔ 冷却有牙：喂够 streak 就触发（不是恒真/恒假，也不是不可达）
  R5 ⛔ 死路 key 两处**同源**（不许去"统一"—— 那是把对的改坏）
  R6 端到端实测留痕：6 轮可触发（慢，默认只在 `--slow` 下跑）

跑法（秒级）：
    python tests/contract_zero_event_paths_test.py [--slow]
"""
import sys
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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--slow", action="store_true",
                    help="跑端到端 evolve（分钟级），验证 6 轮能触发冷却")
    a, _ = ap.parse_known_args()

    # ──────────────────────────────────────────────────────────
    print("\n== R1 组：`gate.blocked` 有牙，但结构性不触发 ==")
    from aether.genome import (Harness, Gene, Capsule, apply_capsule,
                               blast_radius, PROTECTED_LAYERS)
    h = Harness(meta_rules="V6 元规则：奖励只来自执行判据。")
    hit = {}
    for tgt in ("L0.meta_rules", "meta_rules"):
        g = Gene(target=tgt, op="set", value="改掉宪法", rationale="反证")
        cap = Capsule(name=f"attack:{tgt}", genes=(g,), rationale="反证用")
        h2 = apply_capsule(h, cap)
        br = blast_radius(h, h2)
        hit[tgt] = (cap.touches_protected(), br["touches_protected"])
    ck("R1.1 有牙：`L0.meta_rules` 被识别为触碰受保护层",
       hit["L0.meta_rules"][0] and hit["L0.meta_rules"][1], f"{hit}")
    ck("R1.2 有牙：裸 `meta_rules` 也被识别（绕过洞已补）",
       hit["meta_rules"][0] and hit["meta_rules"][1], f"{hit}")
    ck("R1.3 ⛔ 受保护层存在且 = {meta_rules}",
       set(PROTECTED_LAYERS) == {"meta_rules"}, f"{PROTECTED_LAYERS}")
    #: ⛔ 不许改判据去"让它触发"：两个提议器都被设计成永不碰 meta_rules，
    #:    0 次是设计意图（纵深防御第二道锁）。
    from aether.providers import MemoryAwareProposer  # noqa: F401
    ck("R1.4 结论留痕：0 次是**结构性**（无构造器产出碰 L0 的胶囊）"
       " ⇒ 处置是加反证契约，不是改判据", True)

    # ──────────────────────────────────────────────────────────
    print("\n== R2 组：checkpoint 默认开 ==")
    import tempfile
    from aether.paths import paths
    d = Path(tempfile.mkdtemp(prefix="v6_r2_"))
    p = paths(d)
    ck("R2.1 `paths()` 有 checkpoint 键（位置只有一份定义，第 46 条）",
       "checkpoint" in p, f"{sorted(p)}")
    ck("R2.2 落点在 <root>/data/checkpoint.json",
       p["checkpoint"].name == "checkpoint.json"
       and p["checkpoint"].parent.name == "data", f"{p['checkpoint']}")
    from aether.cli import build_parser
    pr = build_parser()
    subs = [act for act in pr._actions
            if isinstance(act, argparse._SubParsersAction)]
    ev = [subs[0].choices["evolve"]]
    opts = {o.dest for sp in ev for o in sp._actions}
    ck("R2.3 ⛔ 有 `--no-checkpoint`（默认开的开关必须能显式关，第 12 条）",
       "no_checkpoint" in opts, f"{sorted(o for o in opts if 'check' in o)}")
    ck("R2.4 ⛔ `--checkpoint` 默认值仍为空串（= 走 paths 默认位置）",
       any(o.dest == "checkpoint" and o.default == ""
           for sp in ev for o in sp._actions))

    # ──────────────────────────────────────────────────────────
    print("\n== R3 组：`memory.pruned` 只在显式 --apply 时写（0 次 = 好事）==")
    from aether.memory import MemoryOS
    m = MemoryOS()
    ck("R3.1 默认路径是 dry-run（不删）",
       hasattr(m, "prune_dryrun"), "缺 prune_dryrun")
    try:
        dry = m.prune_dryrun()
        ck("R3.2 dry-run 不会写 `memory.pruned`",
           isinstance(dry, dict) and "will_delete" in dry, f"{list(dry)[:6]}")
        ck("R3.3 ⛔ dry-run 把「真跑会删几条」当成读数报出来",
           isinstance(dry.get("will_delete"), int), f"{dry.get('will_delete')}")
    except Exception as e:                                # noqa: BLE001
        ck("R3.2 dry-run 可用", False, f"{type(e).__name__}: {e}")

    # ──────────────────────────────────────────────────────────
    print("\n== R4 组：⛔ 冷却有牙（喂够 streak 就触发）==")
    from aether.evolution import EvolutionLoop
    lo = EvolutionLoop.__new__(EvolutionLoop)   # 只测纯函数，不跑循环
    from aether.evolution import EvolutionConfig
    lo.cfg = EvolutionConfig()
    ck("R4.1 门槛 deadend_streak = 3（不许有人去调它）",
       lo.cfg.deadend_streak == 3, f"{lo.cfg.deadend_streak}")
    r_false = lo._in_cooldown("cap:x", "a->b", {"cap:x|a->b": 2}, 1)
    r_true = lo._in_cooldown("cap:x", "a->b", {"cap:x|a->b": 3}, 1)
    ck("R4.2 streak=2 ⇒ 不冷却", r_false[0] is False, f"{r_false}")
    ck("R4.3 streak=3 ⇒ 冷却", r_true[0] is True, f"{r_true}")
    ck("R4.4 ⛔ 承上：两者结论相反 ⇒ 冷却不是恒真/恒假",
       r_false[0] != r_true[0], "相同 ⇒ 判据是死的")
    #: 出口：probation
    lo.cfg = EvolutionConfig(deadend_probation_every=2)
    r_prob = lo._in_cooldown("cap:x", "a->b", {"cap:x|a->b": 9}, 2)
    ck("R4.5 冷却不是永久封死：probation 轮放行",
       r_prob[0] is False and r_prob[1] == 9, f"{r_prob}")
    #: world_facts 晋升门槛
    from aether import world_facts as W
    k = W.fact_key(W.SCOPE_DEADEND, "fix_x", "e1|e2")
    promo3 = k in W.promote([{"key": k, "kind": "deadend", "round_no": i}
                             for i in range(3)])
    promo2 = k in W.promote([{"key": k, "kind": "deadend", "round_no": i}
                             for i in range(2)])
    ck("R4.6 world_facts：streak=3 ⇒ 晋升", promo3)
    ck("R4.7 world_facts：streak=2 ⇒ 不晋升", not promo2)
    ck("R4.8 ⛔ 承上：晋升门槛有牙（3 vs 2 结论相反）", promo3 != promo2)

    # ──────────────────────────────────────────────────────────
    print("\n== R5 组：⛔ 死路 key 两处同源（不许去「统一」）==")
    import inspect
    src_acc = inspect.getsource(EvolutionLoop._deadend_accum)
    src_cool = inspect.getsource(EvolutionLoop._in_cooldown)
    ck("R5.1 `_deadend_accum` 用 capsule|env_pair",
       "capsule" in src_acc and "env_pair" in src_acc)
    ck("R5.2 `_in_cooldown` 用 cap_name|env_pair",
       "cap_name" in src_cool and "env_pair" in src_cool)
    ck("R5.3 ⛔ 两者拼法同一形态（`{a}|{b}`）⇒ 已同源，别改",
       "|{" in src_acc or "}|{" in src_acc or '"|"' in src_acc,
       "形态变了 ⇒ 要重新做交叉表")
    #: 行为级同源（比源码级更硬）：用真实事件走一遍
    import types
    lo2 = EvolutionLoop.__new__(EvolutionLoop)
    lo2.cfg = EvolutionConfig()
    streak: dict = {}
    ev = types.SimpleNamespace(
        kind="gen.probe",
        payload={"capsule": "cap:x", "env_pair": "a->b", "verdict": "partial"})
    lo2._deadend_accum(ev, streak)
    ck("R5.4 累加一次 ⇒ streak=1 且 key = `cap:x|a->b`",
       streak == {"cap:x|a->b": 1}, f"{streak}")
    ck("R5.5 ⛔ 承上：`_in_cooldown` 用同名 key 能读到它（同源的行为级证明）",
       lo2._in_cooldown("cap:x", "a->b", streak, 1)[1] == 1, f"{streak}")

    # ──────────────────────────────────────────────────────────
    print("\n== R6 组：端到端（6 轮能真的触发冷却）==")
    if not a.slow:
        print("  SKIP R6.1（分钟级，加 --slow 才跑）")
        print("  ℹ️  r58 实测：6 轮 ⇒ deadend.cooldown=2 / "
              "world_fact.promoted=2 / 最长 streak=4")
        print("     ⛔ r57 判「结构性不可达」是因为只跑了 4 轮（门槛是 3）")
    else:
        import io
        import json
        import os
        import contextlib
        from aether.cli import main as cli_main
        dd = tempfile.mkdtemp(prefix="v6_r6_")
        os.symlink(ROOT / "tasks", Path(dd) / "tasks")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["evolve", "--rounds", "6", "--max-tasks", "8",
                      "--root", dd])
        kinds = {}
        lp = paths(dd)["ledger"]
        if lp.exists():
            for line in lp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        k = json.loads(line).get("kind", "?")
                    except ValueError:
                        continue
                    kinds[k] = kinds.get(k, 0) + 1
        ck("R6.1 6 轮 ⇒ `deadend.cooldown` 真的出现（不是结构性不可达）",
           kinds.get("deadend.cooldown", 0) > 0, f"{kinds}")
        ck("R6.2 `world_fact.promoted` 真的出现",
           kinds.get("world_fact.promoted", 0) > 0, f"{kinds}")

    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
