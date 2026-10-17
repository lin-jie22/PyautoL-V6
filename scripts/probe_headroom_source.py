#!/usr/bin/env python3
"""取证：held-out 的「可提升空间」被谁吃掉了 —— 题源上限 vs 静态难度标签。

## 起因（r54 结尾实测，逐字引用）

```
轮5  headroom={'easy': 0, 'hard': 1}  thin=['easy']
     体检：补到上限仍缺：easy/hard（这是卷子的上限，不是候选的错）
     补了 0 条 {'easy': 0, 'hard': 1} → {'easy': 0, 'hard': 1}
轮6  同上，逐字重复
```

两轮 `inconclusive` ⇒ 有候选 reward=1.0 却提交不了。

## 三个互斥假设（第 39 条：必要条件与充分条件必须分开标定）

  H1 **题源枯竭** —— 合成器在这个环境下到顶了，加大 `max_n` 也出不来新题。
                     判据：题数随 max_n 增长曲线是否饱和。
  H2 **判据筛掉** —— 题其实有，但 `grow_fn` 的「基线做不对」这一筛把它们
                     全筛掉了。判据：新题里 `reward < 1.0` 的有几条。
  H3 **结构必然** —— `easy` 的定义是「通用修法（`GENERIC_REPAIRS`，实测
                     只有 **一个** 成员 `revert_control_flow`）能修好」。
                     基线一旦装上 `fix_control_flow`（轮 1 就装了），
                     easy 层就**不可能**再有「基线做不对」的题
                     ⇒ 该层 headroom 结构性归零，且**永远补不回来**。

⛔⛔ H3 若成立，r53 写在裁决 reason 里的「出路：补 held-out 的题数」
     在这条路径上是**装饰性的**（第 35b 条第三次命中）：出路存在，
     但目标不可达 —— 补题的筛子（`reward < 1.0`）与该层的定义
     （「这个修法修得好」）在基线上是同一个事实的两面。

## 同源约定（第 1 条）

· 链的应用走 `HarnessAgent._apply_one` —— 与 `runner` 里那个 `plan`
  循环**逐字同形**，不另抄一份链语义。
· 合成走 `synthesize_tasks`（与 CLI 建 held-out 同一入口）。
· 难度标签走 `difficulty_report`（与 CLI 同一入口）。
· reward 一律来自 `verify_source` 的执行判据，不做字符串估计。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.cli import DEFAULT_ENVS, TASK_CLASS  # noqa: E402
from aether.environment import (DEFAULT_MUTATION_KINDS, Environment,  # noqa: E402
                                synthesize_tasks, verify_source)
from aether.agent import HarnessAgent  # noqa: E402
from aether.nongression import FrozenTask  # noqa: E402
from aether.repair import difficulty_report, revert_control_flow  # noqa: E402

#: r54 轮 5 的基线链（按提交顺序：轮1 boolops → 轮3 literals → 轮4 control_flow）
ROUND5_CHAIN = ("fix_boolops", "fix_literals", "fix_control_flow")
#: 种子链（空）⇒ 裸种子的能力
SEED_CHAIN: tuple = ()

TIMEOUT = 10.0


def build_envs(names):
    return {n: Environment.from_dir(ROOT / "tasks" / n) for n in names}


def synth(env, max_n, depth, kinds, balanced=True):
    return synthesize_tasks(env, max_n=max_n, depth=depth, kinds=kinds,
                            require_behavior=True, balanced=balanced).tasks


def chain_reward(agent, env, spec, src, chain):
    """按链依次应用，返回执行判据的 reward（与 runner 的 `plan` 循环同形）。"""
    steps: list = []
    fixed = src
    for s in chain:
        fixed, _ok = agent._apply_one(s, fixed, env, spec, steps)
    return verify_source(env, source=fixed, timeout=TIMEOUT).reward


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="eventflow",
                    help="held-out 环境（默认 eventflow，与 evolve 默认一致）")
    ap.add_argument("--depth", default="2")
    ap.add_argument("--kinds", default="compare,binop,constant,boolop,unaryop")
    ap.add_argument("--cap", default="60",
                    help="评估条数上限（verify_source 走子进程，很贵）")
    ap.add_argument("--max-ns", default="16,32,64,128,256")
    args = ap.parse_args()

    ho_env_name = args.env
    envs = build_envs([n for n in DEFAULT_ENVS if n == ho_env_name]
                      or [ho_env_name])
    env = envs[ho_env_name]
    agent = HarnessAgent({ho_env_name: env}, memory=None, timeout=TIMEOUT)
    kinds = tuple(x for x in args.kinds.split(",") if x)
    depth = int(args.depth)
    cap = int(args.cap)
    max_ns = [int(x) for x in args.max_ns.split(",") if x]

    print("=" * 68)
    print(f"取证环境 {ho_env_name}  depth={depth}  kinds={'/'.join(kinds)}"
          f"  balanced=True")
    print("=" * 68)

    # ── A 段 · 题源上限曲线（H1）──────────────────────────────
    print("\nA · 题源上限曲线（H1：加大 max_n 还出得来新题吗）")
    pool: dict = {}
    rows = []
    for n in max_ns:
        tasks = synth(env, n, depth, kinds)
        new = [t for t in tasks if t.task_id not in pool]
        for t in new:
            pool[t.task_id] = t
        rows.append((n, len(tasks), len(pool), len(new)))
        print(f"   max_n={n:<5} 本档产出 {len(tasks):<4}"
              f" 累计去重 {len(pool):<4} 本档新增 {len(new)}")
    saturated = len(rows) >= 2 and rows[-1][3] == 0
    print(f"   ⇒ 曲线{'**已饱和**' if saturated else '**仍在增长**'}"
          f"（最后一档新增 {rows[-1][3]} 条）")

    items = list(pool.items())
    print(f"\n   题池共 {len(items)} 条 ⇒ 评估前 {min(cap, len(items))} 条"
          f"（--cap {cap}）")
    items = items[:cap]

    specs = {tid: {"env_id": ho_env_name, "broken_source": t.broken_source,
                   "topic": ho_env_name, "task_class": TASK_CLASS}
             for tid, t in items}

    # ── B 段 · 静态难度标签 ──────────────────────────────────
    #: ⛔ 用真正的 `FrozenTask`，不造一个"长得像"的假对象（第 1 条同源）：
    #:    `FrozenTask.__post_init__` 会把 spec 包成 MappingProxyType，
    #:    而难度分层与闸都靠这个形态工作 —— 假对象会让整段取证"看起来
    #:    能跑"而结论失真（记忆第 41 条：这个坑已栽过一次）。
    def _T(tid, spec):
        return FrozenTask(task_id=tid, spec=spec)

    frozen_like = [_T(tid, sp) for tid, sp in specs.items()]
    lab = difficulty_report(env, frozen_like)["labels"]
    dist = Counter(lab.values())
    print(f"\nB · 静态难度分布（GENERIC_REPAIRS 只有一个成员 "
          f"`revert_control_flow`）")
    print(f"   {dict(dist)}")

    # ── C 段 · headroom 归因（H2 / H3）────────────────────────
    print("\nC · headroom 归因（同一批题，两条链的对照）")
    print(f"   {'链':<34} {'easy 未修好':<14} {'hard 未修好':<14} 总 reward")
    stat: dict = {}
    for name, chain in (("种子链（空）", SEED_CHAIN),
                        ("轮5链 " + "|".join(ROUND5_CHAIN), ROUND5_CHAIN)):
        lv_tot: Counter = Counter()
        lv_bad: Counter = Counter()
        tot = 0.0
        per: dict = {}
        for tid, t in items:
            r = chain_reward(agent, env, specs[tid], t.broken_source, chain)
            per[tid] = r
            lv = str(lab.get(tid, ""))
            lv_tot[lv] += 1
            if r < 1.0:
                lv_bad[lv] += 1
            tot += r
        stat[name] = (dict(lv_bad), dict(lv_tot), per)
        print(f"   {name:<34} {lv_bad.get('easy', 0):>3}/{lv_tot.get('easy', 0):<10}"
              f" {lv_bad.get('hard', 0):>3}/{lv_tot.get('hard', 0):<10}"
              f" {tot / max(1, len(items)):.4f}")

    # ── D 段 · 结构必然性检验（H3）────────────────────────────
    print("\nD · 结构必然性：easy 层「单修法」与「链」是否同结果（H3）")
    _sb, _st, per_r5 = stat[f"轮5链 {'|'.join(ROUND5_CHAIN)}"]
    n_easy = 0
    n_easy_cf_ok = 0
    n_easy_chain_ok = 0
    diff = []
    for tid, t in items:
        if str(lab.get(tid, "")) != "easy":
            continue
        n_easy += 1
        r_cf = verify_source(
            env, source=revert_control_flow(t.broken_source, env.source),
            timeout=TIMEOUT).reward
        r_ch = per_r5.get(tid, 0.0)
        if r_cf >= 1.0:
            n_easy_cf_ok += 1
        if r_ch >= 1.0:
            n_easy_chain_ok += 1
        if (r_cf >= 1.0) != (r_ch >= 1.0):
            diff.append((tid, r_cf, r_ch))
    print(f"   easy 题 {n_easy} 条：`revert_control_flow` 单修法修好 "
          f"{n_easy_cf_ok} 条，轮5链修好 {n_easy_chain_ok} 条")
    print(f"   两者判定不一致的 {len(diff)} 条"
          f"（样本 {[d[0][:18] for d in diff[:3]]}）")
    structural = n_easy > 0 and n_easy_chain_ok == n_easy
    print(f"   ⇒ easy 层{'**结构性饱和**' if structural else '**非结构性**'}"
          f"：链修好了全部 {n_easy_chain_ok}/{n_easy} 条 easy 题"
          f" ⇒ 该层再合成多少题都是 `reward=1.0` ⇒ grow_fn 永远补不进")

    # ── E 段 · 出路检验：加深合成能否造出「轮5链做不对」的题 ──
    print("\nE · 出路检验：depth 加大能否造出「轮5链做不对」的题")
    for d in (depth, depth + 1, depth + 2):
        try:
            ts = synth(env, 64, d, kinds)
        except Exception as e:                              # noqa: BLE001
            print(f"   depth={d}  合成失败 {type(e).__name__}: {e}")
            continue
        seen = set(pool)
        fresh = [t for t in ts if t.task_id not in seen][:24]
        if not fresh:
            print(f"   depth={d}  产出 {len(ts)} 条，去重后无新题")
            continue
        bad = 0
        bad_easy = 0
        for t in fresh:
            sp = {"env_id": ho_env_name, "broken_source": t.broken_source,
                  "topic": ho_env_name, "task_class": TASK_CLASS}
            r = chain_reward(agent, env, sp, t.broken_source, ROUND5_CHAIN)
            if r < 1.0:
                bad += 1
                l2 = difficulty_report(
                    env, [_T(t.task_id, sp)])["labels"].get(t.task_id, "")
                if l2 == "easy":
                    bad_easy += 1
        print(f"   depth={d}  产出 {len(ts):<4} 新题取 {len(fresh):<3}"
              f" ⇒ 轮5链做不对 {bad} 条（其中 easy 层 {bad_easy} 条）")

    # ── F 段 · 复现 ensure_headroom 的补题过程（每一步的筛子）────
    #
    #    ⛔ r54 实测「补了 0 条」，而 C 段显示全池 hard 层还有 9/15 空间。
    #       两者必有一个读数被误读。这里**跑真实的闸方法**（同源），
    #       把 `steps` 与逐条筛子的结果打出来 —— 不让"补不动"停留在
    #       一句文案上（第 10 条：环境故障必须能与"不用补"分辨）。
    print("\nF · 复现 `ensure_headroom`（r54 实测补了 0 条，逐步看筛子）")
    from aether.generalization import GeneralizationGate, make_heldout
    from aether.genome import Harness

    runner = agent.as_runner()
    h_r5 = Harness(
        meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
        tools=("read_file", "write_file", "python_exec"),
        skills={s: f"修法 {s}" for s in ROUND5_CHAIN},
        routing={TASK_CLASS: "|".join(ROUND5_CHAIN)},
        memory_policy={"recall_topk": 5, "min_content_len": 150})

    def _frozen(t):
        return _T(t.task_id, {"env_id": ho_env_name,
                              "broken_source": t.broken_source,
                              "topic": ho_env_name,
                              "task_class": TASK_CLASS})

    #: 模拟 r54 的建卷口径：`--max-tasks 16` ⇒ `synthesize_tasks(max_n=16)`
    base_src = synth(env, 16, depth, kinds)
    suite_tasks = [_frozen(t) for t in base_src]
    lab0 = difficulty_report(env, suite_tasks)["labels"]
    print(f"   建卷 {len(suite_tasks)} 条"
          f"（max_n=16，与 r54 建卷口径同源）"
          f"  静态分布 {dict(Counter(lab0.values()))}")

    gate = GeneralizationGate(
        make_heldout(ho_env_name, suite_tasks, difficulty=lab0),
        dev_task_ids=[],
        grow_fn=lambda n: [_frozen(t) for t in synth(env, n, depth, kinds)],
        difficulty_fn=lambda pend: difficulty_report(env, list(pend))["labels"],
        grow_ceiling=64, grow_max_tasks=96)
    head_before = gate.headroom_of(h_r5, runner)
    print(f"   体检前 headroom = {head_before}")
    rep = gate.ensure_headroom(h_r5, runner, round_no=5)
    print(f"   steps = {rep.get('steps')}")
    print(f"   grew = {rep.get('grew')}   missing = {rep.get('missing')}")
    print(f"   体检后 headroom = {rep.get('headroom_after')}")
    print(f"   note = {rep.get('note')}")

    #: ⛔ 逐条看筛子：题源里「建卷之外」的题，各自卡在哪一关。
    cand_all = [_frozen(t) for t in synth(env, 64, depth, kinds)]
    seen = {t.task_id for t in suite_tasks}
    fresh = [t for t in cand_all if t.task_id not in seen]
    lab_f = difficulty_report(env, fresh)["labels"]
    n_tot: Counter = Counter()
    n_bad: Counter = Counter()
    for t in fresh:
        lv = str(lab_f.get(t.task_id, "") or "")
        r = runner(h_r5, dict(t.spec))
        n_tot[lv] += 1
        if float(getattr(r, "reward", 0.0) or 0.0) < 1.0:
            n_bad[lv] += 1
    print(f"   题源 {len(cand_all)} 条 ⇒ 建卷外 {len(fresh)} 条")
    print(f"   建卷外分层 {dict(n_tot)}")
    print(f"   其中「轮5链做不对」{dict(n_bad)}  ← 这才是 grow_fn 能补的")
    print(f"   ⇒ 补不进的直接原因："
          f"{'题源里没有建卷外的新题' if not fresh else '见上两行'}")

    # ── G 段 · 换采样口径能否拿到新题（r55 的候选出路）────────────
    #
    #    ⛔ A/E 段已证：同一口径下加大 `max_n` 与 `depth` 都到顶（26 条）。
    #       但 r51 实测「顺序 59 条 / 均衡 81 条，同 max_n=12」⇒ 换采样
    #       顺序会换**一批题**。若这条路成立，补题就不用换环境（换环境
    #       会动 held-out 的语义），只在同一个环境里换一把采样尺。
    #    ⛔ 判据：与现有题池去重后的**新增条数**，以及新增题里
    #       「轮5链做不对」的条数（这才是能补进 headroom 的部分）。
    print("\nG · 换采样口径（balanced 取反）能否拿到新题")
    known = set(pool)
    for tag, bal in (("均衡(balanced=True)", True), ("顺序(balanced=False)", False)):
        try:
            ts = synth(env, 64, depth, kinds, balanced=bal)
        except Exception as e:                              # noqa: BLE001
            print(f"   {tag:<24} 合成失败 {type(e).__name__}: {e}")
            continue
        fresh2 = [t for t in ts if t.task_id not in known]
        if not fresh2:
            print(f"   {tag:<24} 产出 {len(ts)} 条 ⇒ 去重后新增 0 条")
            continue
        fl = [_frozen(t) for t in fresh2[:24]]
        lab2 = difficulty_report(env, fl)["labels"]
        n_bad2: Counter = Counter()
        n_tot2: Counter = Counter()
        for t in fl:
            lv = str(lab2.get(t.task_id, "") or "")
            r = runner(h_r5, dict(t.spec))
            n_tot2[lv] += 1
            if float(getattr(r, "reward", 0.0) or 0.0) < 1.0:
                n_bad2[lv] += 1
        print(f"   {tag:<24} 产出 {len(ts):<4} ⇒ 建卷外新增 {len(fresh2):<4}"
              f"（取 {len(fl)} 条评估）")
        print(f"   {'':<24} 分层 {dict(n_tot2)}"
              f" ⇒ 轮5链做不对 {dict(n_bad2)}")
        known |= {t.task_id for t in fresh2}

    print("\n" + "=" * 68)
    print("结论口径")
    print("=" * 68)
    print(f"   H1 题源枯竭：{'成立' if saturated else '不成立'}"
          f"（max_n 最后一档新增 {rows[-1][3]} 条）")
    print(f"   H3 结构必然：{'成立' if structural else '不成立'}"
          f"（链修好 {n_easy_chain_ok}/{n_easy} 条 easy 题）")
    print("   H2 判据筛掉：看 C 段 —— 种子链下 easy 层有空间、轮5链下归零"
          " ⇒ 是基线变强，不是题变少")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
