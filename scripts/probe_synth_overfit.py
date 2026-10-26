#!/usr/bin/env python3
"""探针：判据驱动生成层的 **overfit 风险**量化 —— 判据要多密，合成才可信？

═══ 为什么必须做这个 ═══

159 条补上了「判据驱动生成补丁」层，并声明了一条**未验证**的风险：

    筛选用的是发展集判据 ⇒ 选出来的必然是"发展集上更绿"的那个
    ⇒ 这就是 overfit 的形状。

而 159 的实验读数里 `verdict=no_gate`（闸没参与）—— 已确认这是**实验装置
固有**（158 的 A 组同样是 no_gate），两组同口径可比，但风险**至今没被测过**。

═══ ⛔⛔ 一个必须先避开的陷阱 ═══

159 里合成补丁与人类修复**逐字一致** ⇒ 拿去 held-out 上跑必然 100% 通过。
那是**恒真验证，没有信息量**（第 122 条：构造必然 ≠ 证据）。

⇒ 本探针量的是**判据稀疏度**：只给合成层看 k 条判据，看它搜出的补丁在
**没给它看的那些判据**上还成不成立。这才是"判据驱动"的固有风险的直接测量：

    判据给少了 ⇒ 它会搜出一个"满足你给的这些、但不满足其余"的补丁
    ⇒ 那不是修复，是**拟合判据**。

═══ 实验设计 ═══

  · 判据池：真实 bug 的全部 N 条判据
  · 子集大小 k = 1, 2, 3, 5, 10, N（**必须**含 ≥1 条在缺陷版上失败的判据，
    否则缺陷版在子集上全绿 ⇒ 合成层判定"无增益可图" ⇒ 不动手）
  · 每个 k 跑多次（不同随机子集，固定 seed ⇒ 可离线复现）
  · 测三个量：
      1. 搜出的补丁是否与**人类修复**逐字一致（金标准）
      2. 补丁在**未见判据**上的通过率（泛化 —— 本实验的主读数）
      3. 对照：人类修复在未见判据上必须 100%（天花板，用来证装置没坏）

用法：
    python3 scripts/probe_synth_overfit.py \
        --task /tmp/r80/tasks/real_boltons_mathutils_c1c25da
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.synthesize import score_source, synthesize_patch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--ks", default="1,2,3,5,10,25")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--max-cands", type=int, default=400)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    task = Path(args.task)
    broken = (task / "broken_source.py").read_text(encoding="utf-8")
    #: ⛔ 金标准 = 人类修复后的源码（判据就是照它校准的）
    human = (task / "task.py").read_text(encoding="utf-8")
    tests = json.loads((task / "tests.json").read_text(encoding="utf-8"))["tests"]
    n = len(tests)

    print("=" * 78)
    print(f"判据稀疏度 → 泛化（overfit 量化）  判据 {n} 条  "
          f"seed={args.seed}  每组 {args.trials} 次")
    print("=" * 78)

    base_ok, bad, blew = score_source(broken, tests)
    if blew:
        print(f"✗ 基线跑不起来：{bad[0][:80]}")
        return 1
    bad_set = set(bad)
    failing = [t for t in tests if t.get("name") in bad_set]
    passing = [t for t in tests if t.get("name") not in bad_set]
    print(f"基线通过 {base_ok}/{n}；失败判据 {len(failing)} 条 ⇒ "
          f"子集必须含至少 1 条失败判据（否则无增益可图 ⇒ 不动手）")
    print(f"失败判据：{[t.get('name') for t in failing]}")

    #: 天花板对照：人类修复在未见过任何东西的情况下必须全绿
    h_ok, h_bad, h_blew = score_source(human, tests)
    print(f"\n量具对照：人类修复源码通过 {h_ok}/{n}"
          + (f"  ⛔ 有失败：{h_bad[:3]}" if h_ok != n or h_blew else ""))
    if h_blew or h_ok != n:
        print("  ⇒ 判据与人类修复不一致 ⇒ **本探针的读数不成立**，停机")
        return 1

    rng = random.Random(args.seed)
    rows = []
    for k in [int(x) for x in args.ks.split(",") if x.strip()]:
        k = min(k, n)
        for trial in range(args.trials):
            #: 固定含 1 条失败判据（随机挑），其余从通过判据里随机补
            seed_fail = rng.choice(failing)
            others = [t for t in passing]
            rng.shuffle(others)
            sub = [seed_fail] + others[:max(0, k - 1)]
            sub_names = {t.get("name") for t in sub}
            held = [t for t in tests if t.get("name") not in sub_names]
            if not held:
                continue

            r = synthesize_patch(broken, sub, max_cands=args.max_cands)
            rec = {"k": k, "trial": trial, "n_held": len(held),
                   "found": r.found, "desc": r.desc,
                   "same_as_human": bool(r.patch) and r.patch == human}
            if r.found:
                g_ok, _gb, g_blew = score_source(r.patch, held)
                f_ok, _fb, f_blew = score_source(r.patch, sub)
                rec.update({
                    "held_ok": None if g_blew else g_ok,
                    "sub_ok": None if f_blew else f_ok,
                    "held_rate": (None if g_blew or not held
                                  else round(g_ok / len(held), 3)),
                })
            else:
                rec.update({"held_ok": None, "sub_ok": None, "held_rate": None})
            rows.append(rec)
            flag = ""
            if r.found and rec["held_rate"] is not None and rec["held_rate"] < 1.0:
                flag = "  ⛔ 过拟合（给它的判据过了，没给的挂了）"
            elif r.found and not rec["same_as_human"]:
                flag = "  ⚠ 与人类修复不同（但在未见判据上也过得去）"
            print(f"  k={k:<3} trial={trial}  给 {len(sub):>2} 条 / 未见 {len(held):>2} 条  "
                  f"{'搜出 ' + r.desc if r.found else '搜不出':<18}"
                  f"未见通过 {str(rec['held_ok']) + '/' + str(rec['n_held']):>8}"
                  f"  与人类一致={rec['same_as_human']}{flag}", flush=True)

    print(f"\n{'=' * 78}\n汇总（按判据稀疏度）\n{'=' * 78}")
    print(f"{'k':>4}{'组数':>6}{'搜出':>6}{'与人类一致':>12}{'未见判据通过率':>16}")
    for k in sorted({r["k"] for r in rows}):
        grp = [r for r in rows if r["k"] == k]
        found = [r for r in grp if r["found"]]
        same = [r for r in found if r["same_as_human"]]
        rates = [r["held_rate"] for r in found if r["held_rate"] is not None]
        avg = round(sum(rates) / len(rates), 3) if rates else None
        print(f"{k:>4}{len(grp):>6}{len(found):>6}{len(same):>12}"
              f"{(str(avg) if avg is not None else '—'):>16}")

    print(f"\n金标准对照：人类修复在**任意**未见判据子集上都必然 100%"
          f"（上面已验：{h_ok}/{n}）⇒ 合成补丁低于 100% 的部分就是 overfit。")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"task": str(task), "n": n, "base_ok": base_ok,
             "failing": [t.get("name") for t in failing],
             "human_ok": h_ok, "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
