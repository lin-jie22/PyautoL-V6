#!/usr/bin/env python3
"""普查：判据驱动生成补丁层（`aether/synthesize.py`）在真实 bug 上值多少。

═══ ⛔ 本脚本**不实现**合成逻辑，只做"跑 + 量 + 报" ═══

实现全在 `aether/synthesize.py`。原因（157 实踩）：脚本里另写一份解析/生成
⇒ 两处实现必然漂移 ⇒ 契约绿了而产品是另一套 ⇒ 结论是假的。
这里只 import 产品函数，再补上**普查视角**（分布 / 代价 / 逐任务明细）。

═══ 量什么 ═══

  1. 可行性：拿掉答案（不需要 base）后，能不能搜出让判据变绿的补丁
  2. **代价**：候选数 / 实跑沙箱次数 / 墙钟时间（第 71 条：量代价）
  3. **射程**：搜不出的原因要分档（定位失败 / 候选全劣 / 结构级够不到）
  4. **唯一性**：有几个候选能全绿 —— 1 个说明解唯一（overfit 风险低），
     很多个说明判据太松（选哪个都可能错）

用法：
    # 单个任务
    python3 scripts/probe_synth_layer.py --task /tmp/r80/tasks/real_boltons_mathutils_c1c25da
    # 批量（目录下每个任务跑一遍）
    python3 scripts/probe_synth_layer.py --tasks-root /tmp/r80/tasks --out /tmp/synth.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.synthesize import (locate_focus, score_source,  # noqa: E402
                               synth_candidates, synthesize_patch)


# ── 量具自检 ────────────────────────────────────────────────────────
TOY_BUGGY = ("def f(x):\n"
             "    if x > 10:\n"
             "        return 'bad'\n"
             "    return 'ok'\n")
#: ⛔ expect 是**值本身**，不是源码字面量（写 `'bad'` 会带上引号，永不匹配）
TOY_TESTS = [{"name": "t1", "call": "f(10)", "expect": "bad"}]


def selftest() -> list:
    """⛔ 先证本层的尺子可信：一个已知算子级 bug 必须能被搜出来。"""
    res = []

    def ok(c, m):
        res.append((m, bool(c)))

    base_ok, bad, blew = score_source(TOY_BUGGY, TOY_TESTS)
    ok(not blew, "S1 装置能跑（读数成立）")
    ok(base_ok == 0, f"S2 缺陷版确实挂（通过 {base_ok}/1，期望 0）")

    cands = synth_candidates(TOY_BUGGY, ["f"])
    ok(len(cands) > 0, f"S3 候选生成器有产出（{len(cands)} 个）")
    ran = 0
    for _d, c, _m in cands:
        _, _, b = score_source(c, TOY_TESTS)
        ran += (not b)
    # ⛔ 不许提前 break：一 break 就数不全「到底几条真跑成了」（第 24 条）
    ok(ran == len(cands), f"S3b 每条候选都**真的跑成了**（{ran}/{len(cands)}）")

    r = synthesize_patch(TOY_BUGGY, TOY_TESTS)
    ok(r.found, f"S4 ⛔ 能搜出修复（gain={r.gain}）")
    if r.found:
        line = [l for l in r.patch.splitlines() if l.strip().startswith("if ")][0]
        ok(">=" in line, f"S5 方向正确（`>=`）：{line.strip()}")
        ok(len(r.patch.splitlines()) == len(TOY_BUGGY.splitlines()),
           f"S6 产物是**最小编辑**（行数不变 {len(r.patch.splitlines())}）")
    #: ⛔ 反证：判据改向 ⇒ 产出必须跟着变（证明信息源是判据，不是别的）
    flipped = [{"name": "t1", "call": "f(10)", "expect": "ok"}]
    r2 = synthesize_patch(TOY_BUGGY, flipped)
    ok((not r2.found) or r2.patch != r.patch,
       "S7 ⛔ 判据改向 ⇒ 产出跟着变（信息源确实是判据）")
    return res


# ── 单任务 ──────────────────────────────────────────────────────────
def probe(task: Path, max_cands: int) -> dict:
    broken = (task / "broken_source.py").read_text(encoding="utf-8")
    meta = json.loads((task / "tests.json").read_text(encoding="utf-8"))
    tests = meta["tests"]

    t0 = time.time()
    base_ok, bad, blew = score_source(broken, tests)
    if blew:
        return {"task": task.name, "note": f"基线跑不起来：{bad[0][:70]}"}

    bad_set = set(bad)
    texts = [f"{t.get('name', '')} {t.get('call', '')}"
             for t in tests if t.get("name") in bad_set]
    focus = locate_focus(broken, texts)
    cands = synth_candidates(broken, focus, max_cands=max_cands) if focus else []

    #: ⛔ 分布要**逐个候选**跑出来（产品只返回最优一个）—— 全绿候选有几个
    #: 是判断"解是否唯一 / 判据是否太松"的关键读数，不能省。
    dist: dict = {}
    n_full = 0
    for _d, c, _m in cands:
        s, _b, bl = score_source(c, tests)
        if bl:
            continue
        dist[s] = dist.get(s, 0) + 1
        if s == len(tests):
            n_full += 1

    r = synthesize_patch(broken, tests, max_cands=max_cands)
    return {
        "task": task.name, "n_tests": len(tests), "base_ok": base_ok,
        "bad": bad, "focus": focus, "n_cands": len(cands),
        "dist": dist, "n_full": n_full,
        "found": r.found, "desc": r.desc, "gain": r.gain,
        "new_ok": r.new_ok if r.found else None,
        "n_ran": r.n_ran, "n_blew": r.n_blew,
        "render_modes": r.render_modes, "seconds": round(time.time() - t0, 1),
        "patch": r.patch,
    }


def report(rows: list) -> None:
    print(f"\n{'=' * 76}\n普查结果\n{'=' * 76}")
    print(f"{'任务':<44}{'判据':>5}{'基线':>6}{'定位':>12}{'候选':>6}"
          f"{'全绿':>6}{'结果':>10}{'秒':>7}")
    for r in rows:
        if "note" in r:
            print(f"  {r['task']:<42} （{r['note']}）")
            continue
        res = (f"{r['base_ok']}→{r['new_ok']}" if r["found"] else "搜不出")
        print(f"{r['task']:<44}{r['n_tests']:>5}{r['base_ok']:>6}"
              f"{','.join(r['focus']) or '失败':>12}{r['n_cands']:>6}"
              f"{r['n_full']:>6}{res:>10}{r['seconds']:>7}")

    ok_rows = [r for r in rows if r.get("found")]
    print(f"\n搜得出补丁的任务：{len(ok_rows)} / {len(rows)}")
    if ok_rows:
        print(f"代价：候选合计 {sum(r['n_cands'] for r in rows)}、"
              f"沙箱跑分合计 {sum(r['n_ran'] for r in rows)} 次、"
              f"墙钟 {sum(r['seconds'] for r in rows):.0f}s")
        uniq = [r for r in ok_rows if r["n_full"] == 1]
        print(f"⛔ 全绿候选**唯一**的：{len(uniq)}/{len(ok_rows)}"
              f"（唯一 ⇒ overfit 风险低；很多 ⇒ 判据太松，选哪个都可能错）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="", help="单个任务目录")
    ap.add_argument("--tasks-root", default="", help="批量：目录下每个任务")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-cands", type=int, default=400)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-selftest", action="store_true")
    args = ap.parse_args()

    if not args.no_selftest:
        print("=" * 76)
        print("量具自检（先证尺子可信，再拿它量真实 bug）")
        print("=" * 76)
        st = selftest()
        for m, p in st:
            print(f"  {'✅' if p else '❌'} {m}")
        if not all(p for _, p in st):
            print("\n  ⇒ 量具不可信，停机")
            return 1
        print()

    if args.task:
        tasks = [Path(args.task)]
    elif args.tasks_root:
        root = Path(args.tasks_root)
        tasks = sorted(p for p in root.iterdir()
                       if (p / "broken_source.py").exists()
                       and (p / "tests.json").exists())
        if args.limit:
            tasks = tasks[:args.limit]
    else:
        print("✗ 需给 --task 或 --tasks-root")
        return 2

    rows = []
    for t in tasks:
        print(f"── {t.name} …", flush=True)
        r = probe(t, args.max_cands)
        rows.append(r)
        if "note" not in r:
            #: ⛔ 别把 f-string **跨物理行**写：`f"{x\n if c else y}"` 是 PEP 701
            #:    （3.12）才放开的，3.9 下直接 SyntaxError（EOL while scanning
            #:    string literal）⇒ CI 的 3.9 job 就是这么红的，而 3.12/3.13
            #:    全绿 ⇒ 症状是「只有最老那档红，本地还绿」。
            #:    先把条件表达式落到变量里，print 里就不再跨行。
            found = (f"搜出 {r['desc']} gain={r['gain']}"
                     if r['found'] else "搜不出")
            print(f"   基线 {r['base_ok']}/{r['n_tests']}  定位 {r['focus'] or '失败'}"
                  f"  候选 {r['n_cands']}  全绿候选 {r['n_full']}"
                  f"  {found}"
                  f"  （{r['seconds']}s）", flush=True)

    report(rows)

    if args.out:
        Path(args.out).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
