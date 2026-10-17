#!/usr/bin/env python3
"""泛化闸「同层对照」探针：**dev 的 hard 层与 held-out 的 hard 层是同一层吗？**

⛔ 背景（r51 结尾留下的天花板）：`--depth 2 --balanced` 是唯一真产出
   「跨维度叠加题」的组合，但死锁修完之后，轮 2–5 **全部**判 `partial`，
   理由永远是同一句：「held-out 的 hard 层未涨，而发展集同层有增益 ⇒ 挑食」。

⛔ 本探针要证伪/证实的是：**这个「同层」真的是同一层吗？**
   难度标签由「通用修法能否修好」定义（`repair.classify_difficulty`），
   而 `GENERIC_REPAIRS` 只含 `revert_control_flow` ⇒

       easy = control_flow 修得好（compare / binop 单点）
       hard = 其它**一切**（constant / boolop / unaryop / string / 叠加题）

   ⇒ 「hard」是一个**大杂烩**：它把五个互不相干的维度装进同一个桶。
   ⇒ dev 的 hard 桶与 held-out 的 hard 桶里装的**可以完全是不同维度**。
   ⇒ 于是「dev hard 涨了、ho hard 没涨」不能唯一推出「挑食」——
      它同样可以推出「ho 的 hard 桶里没有这个候选会修的那类题」。

⛔ 判据（第 21/39 条：闸恒红先做反证；对照只差一个变量）：
   ① **维度签名对齐表**：两个 hard 桶里各修法组合的题数分布；
   ② **候选扫描**：对每个修法子集跑真 runner + 真闸，看 verdict 分布
      ⇒ **全红** = 该口径下闸恒红（病在判据，不在候选）；
   ③ **反证组**：定点技能 `patch::<dev_env>`（只在发展集环境生效）
      ⇒ 它是教科书式的挑食，闸**必须**拦住。不拦 = 闸没牙。

⛔ 与 `probe_gate_discrimination.py` 的区别：那个探的是**非回归闸**
   （「有没有变坏」），这个探的是**泛化闸**（「变好是不是真的」）。

用法：
    python3 scripts/probe_level_alignment.py                  # 默认 depth2+均衡
    python3 scripts/probe_level_alignment.py --depth 1        # 旧口径对照
    python3 scripts/probe_level_alignment.py --scan pairs     # 扩大扫描
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aether.cli import TASK_CLASS, _default_runner           # noqa: E402
from aether.environment import (ALL_MUTATION_KINDS,          # noqa: E402
                                Environment, synthesize_tasks)
from aether.generalization import (GeneralizationGate,       # noqa: E402
                                   make_heldout)
from aether.genome import Harness                            # noqa: E402
from aether.nongression import FrozenTask                    # noqa: E402
from aether.repair import (difficulty_report, grow_for_difficulty,  # noqa: E402
                           repair_rewards)
from dataclasses import replace                              # noqa: E402
from probe_gate_discrimination import seed_harness           # noqa: E402

#: 策略名 → 修法函数名。⛔ 与 `aether/agent.py` 的 `KNOWN_STRATEGIES` 同源：
#: 这里**不许**另抄一份映射（第 1 条：探针与被测同源）。
REPAIRS = {
    "fix_control_flow": "revert_control_flow",
    "fix_literals": "revert_literals",
    "fix_boolops": "revert_boolops",
    "fix_strings": "revert_strings",
    "fix_unaryops": "revert_unaryops",
}


def _repair_fns() -> dict:
    """取**真实**修法函数对象（不是名字），与 `classify_difficulty` 同源。"""
    import aether.repair as R
    return {k: getattr(R, v) for k, v in REPAIRS.items()}


def _wrap(t, env_name: str) -> FrozenTask:
    return FrozenTask(task_id=t.task_id,
                      spec={"env_id": env_name,
                            "broken_source": t.broken_source,
                            "topic": env_name, "task_class": TASK_CLASS})


def build_tasks(env: Environment, max_tasks: int, depth: int, balanced: bool,
                kinds, with_fallback: bool, grow_levels=()) -> list:
    """复现 `cmd_evolve` 里发展集的构造（grow 补层 + r51 的 depth 降级补层）。

    ⛔ `grow_levels` 非空时要走 `grow_for_difficulty`：cli 是照着 held-out
       的难度层把 dev 采样**翻倍**的（实测 12→24→32）⇒ 不 grow 的话题数
       对不上，而题数恰恰是本探针的分母。
    """
    def synth(n, d):
        return synthesize_tasks(env, max_n=n, depth=d, kinds=kinds,
                                balanced=balanced).tasks
    tasks = synth(max_tasks, depth)
    if grow_levels:
        g = grow_for_difficulty(env, lambda n: synth(n, depth),
                                grow_levels, max_n=max_tasks)
        tasks = g["tasks"]
    if with_fallback and depth >= 2:
        labels = difficulty_report(env, [_wrap(t, env.env_id)
                                         for t in tasks])["labels"]
        if "easy" not in set(labels.values()):
            extra = synth(max_tasks, 1)
            seen = {t.task_id for t in tasks}
            tasks = list(tasks) + [t for t in extra if t.task_id not in seen]
    return tasks


def dim_table(env: Environment, tasks, fns: dict) -> dict:
    """每题：**哪些修法能独立修好它** —— 这才是「能力维度」。

    ⛔ 不用 desc 前缀猜维度：depth=2 的 desc 是组合的，猜必错。
       用「哪个修法真能修好」定义维度，与候选的能力**同源**
       （候选 = 修法集合 ⇒ 它修得好 ⇔ 维度命中）。
    """
    return {t.task_id: sorted(k for k, v in
                              repair_rewards(env, t.broken_source,
                                             fns).items() if v >= 1.0)
            for t in tasks}


def _bucket(dist: dict, labels: dict, level: str) -> Counter:
    c: Counter = Counter()
    for tid, ds in dist.items():
        if labels.get(tid) != level:
            continue
        c[tuple(ds) if ds else ("<无单一修法可修>",)] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default="textscore")
    ap.add_argument("--heldout", default="eventflow")
    ap.add_argument("--max-tasks", default="12")
    ap.add_argument("--depth", default="2")
    ap.add_argument("--balanced", action="store_true", default=True)
    ap.add_argument("--no-balanced", dest="balanced", action="store_false")
    ap.add_argument("--scan", default="single",
                    choices=("single", "pairs", "all", "none"))
    args = ap.parse_args()

    kinds = ALL_MUTATION_KINDS
    dev_env = Environment.from_dir(ROOT / "tasks" / args.dev, env_id=args.dev)
    ho_env = Environment.from_dir(ROOT / "tasks" / args.heldout,
                                  env_id=args.heldout)
    fns = _repair_fns()
    t0 = time.time()

    # ── ① 造题（与 cmd_evolve 同口径）──────────────────────────────
    dev_src = build_tasks(dev_env, int(args.max_tasks), int(args.depth),
                          args.balanced, kinds, with_fallback=True,
                          grow_levels=("easy", "hard"))
    ho_src = build_tasks(ho_env, int(args.max_tasks), int(args.depth),
                         args.balanced, kinds, with_fallback=False,
                         grow_levels=("easy", "hard"))
    dev = [_wrap(t, args.dev) for t in dev_src]
    ho = [_wrap(t, args.heldout) for t in ho_src]

    dev_diff = difficulty_report(dev_env, dev)
    ho_diff = difficulty_report(ho_env, ho)
    print(f"发展集  {args.dev:<10} {len(dev):>3} 题  "
          f"easy {dev_diff['n_easy']:>2} / hard {dev_diff['n_hard']:>2}")
    print(f"held-out {args.heldout:<9} {len(ho):>3} 题  "
          f"easy {ho_diff['n_easy']:>2} / hard {ho_diff['n_hard']:>2}")

    # ── ② 维度签名对齐表 ──────────────────────────────────────────
    dev_dims = dim_table(dev_env, dev_src, fns)
    ho_dims = dim_table(ho_env, ho_src, fns)

    print("\n── 维度构成（每个桶里，各「可修修法组合」的题数）──")
    sigs = {}
    for tag, dist, labels in (("dev", dev_dims, dev_diff["labels"]),
                              ("ho ", ho_dims, ho_diff["labels"])):
        for lv in ("easy", "hard"):
            b = _bucket(dist, labels, lv)
            sigs[(tag, lv)] = set(b)
            if not b:
                continue
            print(f"  {tag} {lv}:")
            for k, n in sorted(b.items(), key=lambda kv: (-kv[1], kv[0])):
                print(f"      {n:>3}  {'+'.join(k)}")

    d_h = sigs[("dev", "hard")]
    h_h = sigs[("ho ", "hard")]
    print(f"\n  ⛔ hard 桶维度签名：dev {len(d_h)} 种 / ho {len(h_h)} 种 / "
          f"交集 {len(d_h & h_h)} 种")
    print(f"     dev hard 独有：{sorted(d_h - h_h)}")
    print(f"     ho  hard 独有：{sorted(h_h - d_h)}")
    #: ⛔ 不对称比：**两边 hard 桶里"能被某个修法修好"的题占比**
    for tag, dist, labels in (("dev", dev_dims, dev_diff["labels"]),
                              ("ho ", ho_dims, ho_diff["labels"])):
        ids = [t for t in labels if labels[t] == "hard"]
        fixable = sum(1 for t in ids if dist.get(t))
        print(f"     {tag} hard 里「至少有一个单一修法能修好」的："
              f"{fixable}/{len(ids)}")

    if args.scan == "none":
        return 0

    # ── ③ 候选扫描：真 runner + 真闸 ──────────────────────────────
    envs = {args.dev: dev_env, args.heldout: ho_env}
    runner = _default_runner(envs)
    gate = GeneralizationGate(
        make_heldout(args.heldout, ho, difficulty=ho_diff["labels"]),
        dev_task_ids=[t.task_id for t in dev],
        heldout_floor=0.0, base_at_ceiling=1.0)

    names = sorted(REPAIRS)
    if args.scan == "single":
        combos = [(n,) for n in names] + [tuple(names)]
    elif args.scan == "pairs":
        combos = [(n,) for n in names] + list(itertools.combinations(names, 2))
    else:
        combos = [c for r in range(1, len(names) + 1)
                  for c in itertools.combinations(names, r)]

    #: ⛔⛔ 两处同源（第一次跑全 0 就是栽在这两条上）：
    #:    · `harness_id` 是 **property**（内容寻址），不是构造参数；
    #:    · harness 的 **5 层都会影响结果**（agent 的 L0 元规则 / L2 工具）。
    #:      只给 `skills` ⇒ L2 工具为空 ⇒ 修法**不可达** ⇒ dev/ho 全 0
    #:      ⇒ 每个候选都判 `no_gain`（看起来像「题太难」，其实是探针自己的
    #:      装置故障 —— 与第 11 条「没跑起来 ≠ 没通过」完全同形）。
    #:      ⇒ 基底必须照抄 `cmd_evolve` 的种子 harness，只改 skills 一层。
    base_h = seed_harness()
    base_dev_vs = [runner(base_h, t.spec) for t in dev]
    base_ho_vs = [runner(base_h, t.spec) for t in ho]
    base_dev = sum(v.reward for v in base_dev_vs) / max(1, len(dev))
    base_ho = sum(v.reward for v in base_ho_vs) / max(1, len(ho))
    #: 基线的**分层** reward（gain 必须减基线，不能拿 0 当基线）
    base_by_lv: dict = {}
    for t, v in zip(dev, base_dev_vs):
        lv = dev_diff["labels"].get(t.task_id)
        if lv:
            base_by_lv.setdefault(lv, []).append(v.reward)
    base_by_lv = {lv: sum(xs) / len(xs) for lv, xs in base_by_lv.items()}

    print(f"\n── 候选扫描（{len(combos)} 个修法子集；基线 dev {base_dev:.3f}"
          f" / ho {base_ho:.3f}；分层基线 {base_by_lv}）──")
    rows = []
    #: ⛔ per-task 通过矩阵：`(task_id → 修好它的候选子集列表)`。
    #:    用它算**组合签名**（见下），单修法签名会低估链条能力。
    per_task_dev: dict = {}
    per_task_ho: dict = {}
    for combo in combos:
        #: ⛔ L1 路由是**链**（有序技能序列）：只给 skills 不给路由 ⇒
        #:    agent 报「没有可达路由」⇒ 技能存在但不可达 ⇒ reward 0
        #:    （第三处同源。教训与上面两条一样：先打印真实返回，别猜）。
        h = replace(seed_harness(), skills={n: REPAIRS[n] for n in combo},
                    routing={TASK_CLASS: "|".join(combo)})
        d_vs = [runner(h, t.spec) for t in dev]
        for t, v in zip(dev, d_vs):
            if v.reward >= 1.0:
                per_task_dev.setdefault(t.task_id, []).append(combo)
        for t in ho:
            if runner(h, t.spec).reward >= 1.0:
                per_task_ho.setdefault(t.task_id, []).append(combo)
        dev_r = sum(v.reward for v in d_vs) / max(1, len(dev))
        by_lv_b: dict = {}
        for t, v in zip(dev, d_vs):
            lv = dev_diff["labels"].get(t.task_id)
            if lv:
                by_lv_b.setdefault(lv, []).append(v.reward)
        dev_by_lv = {lv: sum(xs) / len(xs) - base_by_lv.get(lv, 0.0)
                     for lv, xs in by_lv_b.items()}
        res = gate.probe(base_h, h, runner, dev_r - base_dev, dev_by_lv)
        rows.append({"skills": "+".join(combo), "dev": round(dev_r, 4),
                     "dev_gain": round(dev_r - base_dev, 4),
                     "verdict": res.verdict,
                     "ho_gain": round(res.heldout_gain, 4),
                     "by_lv": {k: round(v, 4) for k, v
                               in res.by_difficulty.items()},
                     "dev_by_lv": {k: round(v, 4) for k, v in dev_by_lv.items()},
                     "out_of_reach": list(res.out_of_reach)})
        print(f"  {rows[-1]['skills']:<44} dev {dev_r:.3f} "
              f"({dev_r - base_dev:+.3f}) | {res.verdict:<13} "
              f"ho {res.heldout_gain:+.3f} by_lv {rows[-1]['by_lv']} "
              f"dev_by_lv {rows[-1]['dev_by_lv']}")

    vc = Counter(r["verdict"] for r in rows)
    print(f"\n  verdict 分布：{dict(vc)}")
    passed = [r["skills"] for r in rows if r["verdict"] == "generalize"]
    print(f"  放行（generalize）的候选：{passed}")

    # ── ③b 组合签名：单修法签名会**低估**链条能力（多个技能依次应用）──
    #:    所以真正的"能力维度"= 修好它所需的最小技能组合。
    def _min_combo(per_task: dict, tid: str) -> str:
        cs = per_task.get(tid)
        if not cs:
            return "<没有任何子集能修好>"
        best = min(cs, key=lambda c: (len(c), c))
        return "+".join(best)

    print("\n── 组合签名（修好该题所需的**最小**技能组合）──")
    for tag, per_task, tasks, labels in (
            ("dev", per_task_dev, dev, dev_diff["labels"]),
            ("ho ", per_task_ho, ho, ho_diff["labels"])):
        sig: Counter = Counter()
        for t in tasks:
            lv = labels.get(t.task_id)
            if lv != "hard":
                continue
            sig[_min_combo(per_task, t.task_id)] += 1
        print(f"  {tag} hard:")
        for k, n in sorted(sig.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"      {n:>3}  {k}")

    # ── ④ 反证组：教科书式挑食（定点技能，只在发展集环境生效）─────
    #: ⛔ **只装定点技能**，不装任何通用修法（第一版同时装了 5 个通用
    #:    修法 ⇒ ho 也被修到 1.0 ⇒ 反证组自动放行，看起来像"闸没牙"）。
    #:    那不是闸的问题，是**对照没隔离变量**（第 39 条）。
    print("\n── 反证组：定点技能 patch::<dev_env>（只在发展集环境生效）──")
    cheat = replace(seed_harness(),
                    skills={f"patch::{args.dev}": "定点修法"},
                    routing={TASK_CLASS: f"patch::{args.dev}"})
    d_cheat = sum(runner(cheat, t.spec).reward for t in dev) / max(1, len(dev))
    res_c = gate.probe(base_h, cheat, runner, d_cheat - base_dev, None)
    print(f"  dev {d_cheat:.3f}（gain {d_cheat - base_dev:+.3f}）⇒ "
          f"{res_c.verdict} | ho {res_c.heldout_gain:+.3f}")
    print(f"  理由：{res_c.reason[:220]}")

    print("\n⛔ 判读：")
    print("   · 反证组被拦 ⇒ 闸有牙；没被拦 ⇒ 闸形同虚设（另一类坏）；")
    print("   · 若『所有修法子集全红』⇒ 该口径下闸恒红 ⇒ 病在判据"
          "（同层对照的两个层不是同一层），不在候选。")
    print(f"\n耗时 {time.time() - t0:.1f}s")

    out = {"dev": args.dev, "heldout": args.heldout, "depth": int(args.depth),
           "balanced": args.balanced,
           "dev_easy": dev_diff["n_easy"], "dev_hard": dev_diff["n_hard"],
           "ho_easy": ho_diff["n_easy"], "ho_hard": ho_diff["n_hard"],
           "hard_sig_dev": sorted(map(list, d_h)),
           "hard_sig_ho": sorted(map(list, h_h)),
           "rows": rows, "cheat_verdict": res_c.verdict,
           "cheat_ho_gain": round(res_c.heldout_gain, 4)}
    (ROOT / "data" / "probe_level_alignment.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("读数已写入 data/probe_level_alignment.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
