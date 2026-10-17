#!/usr/bin/env python3
"""取证：**候选挤压** —— 提议器每轮到底提名了谁，谁从来没被提名过。

## 起因

r52 结尾实测：`--gate-tries 3` 与 `1` 的轮次**逐字相同**，因为准入队列恒为 1。
当时的归因是"两把挤压叠在一起"：

  ① `advantage` 的组内 z-score 把第二三名判成不合格；
  ② 提议器按**固定顺序**取「链上还没有的技能」（`fix_unaryops` 排第 5
     ⇒ 6 轮里一次都没被提过）。

② 与第 43 条那个「`which` 全局序号把后加维度挤掉」**同构**：固定顺序 +
固定配额这一类结构的通病。但在动手改之前，必须先把"挤压有多大"量出来
（第 24 条：一个维度产出恒为 0，**先怀疑量具，再怀疑世界**）。

## 本脚本做什么

走**真实生产路径**（`aether.cli.main(["evolve", ...])`），只在
`TemplateProposer.propose` 上包一层记录 —— 不改任何判据、不改任何选择逻辑
⇒ 量出来的就是生产实际发生的事（第 1 条：同源）。

报告四张表：

  A · 每轮提名：weakness kind / 提名了哪些胶囊 / 当前链上有什么
  B · 每轮裁决：每个候选的 reward / advantage / reason（看队列为什么是 1）
  C · 跨轮提名计数：哪些技能**从未**被提名（挤压的受害者）
  D · 队列长度：每轮真正进入准入队列（reason 为空）的候选数

⛔ 每个 root 都是**独立临时目录**（只有 data/ 是新的，tasks/ 软链到仓库）
  ⇒ 与历史同口径的对照不受上次运行的记忆污染（r51 的对照纪律）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aether.providers as providers  # noqa: E402
from aether.cli import main as cli_main  # noqa: E402
from aether.evolution import EvolutionLoop as _Loop  # noqa: E402

#: ⛔ r54：冷却记录。提名了却没被评估的候选，只有这里能说清是
#:   「连续被拦达标」还是别的原因 —— 否则"池子枯竭"会被读成"没有候选了"。
_CD: list = []
_orig_cd = _Loop._in_cooldown


def _patched_cd(self, cap_name, env_pair, deadend, round_no):
    cooled, streak = _orig_cd(self, cap_name, env_pair, deadend, round_no)
    _CD.append({"round": round_no, "cap": cap_name, "streak": streak,
                "cooled": bool(cooled)})
    return cooled, streak

#: ⛔ 记录器挂在**类方法**上：`cmd_evolve` 在函数内部 `from .providers
#:    import TemplateProposer` 取的是同一个类对象 ⇒ 包一层即可，不用改生产代码。
_LOG: list = []
_orig_propose = providers.TemplateProposer.propose


def _route_chain(harness) -> list:
    raw = ""
    try:
        raw = (harness.routing or {}).get("repair", "")
    except Exception:                                  # noqa: BLE001
        raw = ""
    return [x for x in str(raw).split("|") if x]


def _patched(self, weakness, harness, n=3):
    out = _orig_propose(self, weakness, harness, n)
    _LOG.append({
        "kind": str((weakness or {}).get("kind", "")),
        "n_asked": n,
        "n_got": len(out),
        "capsules": [getattr(c, "name", str(c)) for c in out],
        "chain_before": _route_chain(harness),
        "pool_order": list(self.skills.keys()),
    })
    return out


def run_once(argv: list) -> dict:
    """跑一次真实 `evolve`，返回它打印的 JSON。"""
    _LOG.clear()
    _CD.clear()
    providers.TemplateProposer.propose = _patched
    _Loop._in_cooldown = _patched_cd
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = cli_main(argv)
    finally:
        providers.TemplateProposer.propose = _orig_propose
        _Loop._in_cooldown = _orig_cd
    s = buf.getvalue()
    if rc != 0 and "{" not in s:
        raise SystemExit(f"evolve 失败 rc={rc}\n{s[-2000:]}")
    return json.loads(s[s.index("{"):])


def make_root() -> str:
    """独立 root：**只有 data/ 是新的**，tasks/ 软链到仓库（环境是常量）。"""
    d = tempfile.mkdtemp(prefix="v6_squeeze_")
    os.symlink(ROOT / "tasks", Path(d) / "tasks")
    return d


def show(out: dict, tag: str) -> dict:
    rounds = out.get("rounds") or []
    print(f"\n{'='*72}\n【{tag}】轮数 {len(rounds)}"
          f"  dev={out.get('dev_env')} ho={out.get('heldout_env')}"
          f"  耗时 {out.get('cost_s')}s\n{'='*72}")

    # ── A · 每轮提名 ────────────────────────────────────────────
    print("\nA · 每轮提名（生产路径实录）")
    for i, lg in enumerate(_LOG, 1):
        print(f"  轮{i} kind={lg['kind']:<14} n={lg['n_asked']}"
              f" 链上={lg['chain_before']}")
        print(f"        提名 → {lg['capsules']}")

    # ── B · 每轮裁决 + D · 队列长度 ─────────────────────────────
    print("\nB · 每轮裁决（reward / advantage / reason）")
    queue_lens = []
    #: ⛔ 每轮「提名了却没被评估」的候选：这是 r54 的核心读数 ——
    #:    没有它，"这一轮只剩 1 个候选"会被读成"没有候选了"。
    dropped_all: list = []
    for r in rounds:
        cands = r.get("candidates") or []
        elig = [c for c in cands if not c.get("reason")]
        queue_lens.append(len(elig))
        #: ⛔ r54：提名了 3 个却只评估了 1 个 ⇒ 差额就是**被冷却吞掉**的。
        #:   不打印这个差集，"池子枯竭"会被读成"没有候选了"。
        nom = set(_LOG[r.get("round_no", 0) - 1]["capsules"]) \
            if 0 < r.get("round_no", 0) <= len(_LOG) else set()
        got = {c.get("capsule") for c in cands}
        dropped = sorted(nom - got)
        dropped_all.append(dropped)
        print(f"  轮{r.get('round_no')} 基线 {r.get('baseline_reward')}"
              f" 提交={r.get('committed')} 提名 {len(nom)}"
              f" 评估 {len(cands)} 准入队列 {len(elig)}"
              + (f" ⛔未评估（冷却/去重）= {dropped}" if dropped else ""))
        for c in cands:
            print(f"      {c.get('capsule'):<28} rew={c.get('reward'):<8}"
                  f" adv={c.get('advantage'):<8}"
                  f" gate={'✓' if c.get('gate_passed') else '✗'}"
                  f" reason={c.get('reason') or '(准入)'}")
        #: ⛔ r54：把**分层**读数打出来。没有它，「ho 该层已饱和」与
        #:   「ho 该层有空间却不涨」在总体层面完全同形 ⇒ 无从判断闸判得对不对。
        #:   ⛔ 数据源是 `candidates[].generalization`（`ProbeResult.to_dict()`
        #:      的完整副本）—— `gate_probes[]` 只留了摘要，没有分层字段。
        for c in cands:
            g2 = c.get("generalization") or {}
            if not g2:
                continue
            print(f"      └ 分层[{c.get('capsule')}] {g2.get('verdict')}"
                  f" ho={g2.get('by_difficulty')}"
                  f" dev={g2.get('dev_by_difficulty')}"
                  f" headroom={g2.get('headroom')} thin={g2.get('thin')}"
                  f" oor={g2.get('out_of_reach')}")

    # ── F · 死路晋升（r54：误判会不会被固化成制度）────────────────
    print("\nF · 死路晋升（哪些「被拦」被写成**可继承**的基因组事实）")
    _prom: Counter = Counter()
    for r in rounds:
        for p in (r.get("facts_promoted") or []):
            _prom[p.get("capsule")] += 1
            print(f"  轮{r.get('round_no')} 晋升 {p.get('capsule')}"
                  f" env_pair={p.get('env_pair')}"
                  f" observations={p.get('observations')}")
    for r in rounds:
        wf = r.get("world_facts") or {}
        keys = [k for k in (wf.get("keys") or {}) if "deadend" in str(k)]
        if keys:
            print(f"  轮{r.get('round_no')} 基因组里已有死路事实 {len(keys)} 条"
                  f"：{keys[:6]}")
            break
    print(f"      ⇒ 晋升总数 {sum(_prom.values())}"
          f"  涉及候选 {sorted(_prom) or '（无）'}")

    # ── G · 冷却实录（提名了 ≠ 被评估）──────────────────────────
    print("\nG · 冷却实录（`_in_cooldown` 的每一次真实判定）")
    for d in _CD:
        print(f"  轮{d['round']} {d['cap']:<28} streak={d['streak']}"
              f" {'⛔冷却（本轮不评估）' if d['cooled'] else '正常评估'}")
    if not _CD:
        print("     （一次都没判过 ⇒ 候选减少与冷却无关）")

    # ── C · 跨轮提名计数 ────────────────────────────────────────
    print("\nC · 跨轮提名计数（谁从来没被提名过）")
    cnt: Counter = Counter()
    for lg in _LOG:
        for nm in lg["capsules"]:
            cnt[nm] += 1
    pool = _LOG[0]["pool_order"] if _LOG else []
    for s in pool:
        print(f"      {s:<28} 被提名 {cnt.get('add_skill:' + s, 0)} 次")
    never = [s for s in pool if cnt.get("add_skill:" + s, 0) == 0]
    print(f"      ⇒ 从未被提名：{never or '（无）'}")

    # ── E · held-out 判别力体检（r53）───────────────────────────
    print("\nE · held-out 判别力（每层还剩几条能涨）")
    for r in rounds:
        g = r.get("generalization") or {}
        hg = r.get("heldout_grow")
        print(f"  轮{r.get('round_no')}裁决={g.get('verdict')}"
              f" headroom={g.get('headroom')} thin={g.get('thin')}"
              f" n_tasks={g.get('n_tasks')}")
        if hg:
            print(f"        体检：{hg.get('note')}"
                  f" 补了 {hg.get('grew')} 条"
                  f" {hg.get('headroom_before')} → "
                  f"{hg.get('headroom_after')}")

    print(f"\nD · 队列长度序列 = {queue_lens}"
          f"  （恒为 1 ⇒ gate_tries 空转）")
    print(f"   提交代数 = {sum(1 for r in rounds if r.get('committed'))}")
    print(f"   reward 轨迹 = {[r.get('baseline_reward') for r in rounds]}")
    return {"queue_lens": queue_lens,
            "nominated": dict(cnt),
            "never": never,
            "dropped": dropped_all,
            "commits": sum(1 for r in rounds if r.get("committed")),
            "rewards": [r.get("baseline_reward") for r in rounds]}


def _strip(base: list, flag: str) -> list:
    """摘掉 `--flag 值`（**两个** argv 元素）。

    ⛔⛔ 只摘一个 ⇒ 下一个参数被当成它的值吃掉 ⇒ 对照组跑的是别的口径，
        而命令行看起来"只是少了个开关"（最难发现的一种对照污染）。
        三个新口径开关（ho-grow / retry-every / ho-grow-variants）都走这里。
    """
    out, skip = [], 0
    for x in base:
        if skip:
            skip = 0
            continue
        if x == flag:
            skip = 1
            continue
        out.append(x)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default="6")
    ap.add_argument("--group", default="3")
    ap.add_argument("--max-tasks", default="12")
    ap.add_argument("--depth", default="2")
    ap.add_argument("--kinds", default="compare,binop,constant,boolop,unaryop")
    ap.add_argument("--envs", default="auto")
    ap.add_argument("--gate-tries", default="1")
    ap.add_argument("--balanced", action="store_true")
    ap.add_argument("--ho-grow", action="store_true",
                    help="开 held-out 判别力自愈（r53 新口径）")
    ap.add_argument("--retry-every", default="0",
                    help="⛔ r54 新口径（默认 0 = 现状）：被闸拒过的候选每被"
                         "跳过这么多轮就放行一次让闸重审")
    ap.add_argument("--ho-grow-variants", default="1",
                    help="⛔ r55 新口径（默认 1 = 现状）：主口径题源撞顶后"
                         "换一种取点顺序再取一批 held-out 题")
    ap.add_argument("--compare", action="store_true",
                    help="同时跑对照组（唯一变量 = 最后一个新口径开关）")
    args = ap.parse_args()

    base = ["--root", "@TMP@", "evolve",
            "--rounds", args.rounds, "--group", args.group,
            "--max-tasks", args.max_tasks, "--depth", args.depth,
            "--kinds", args.kinds, "--envs", args.envs,
            "--gate-tries", args.gate_tries]
    if args.balanced:
        base.append("--balanced")
    if args.ho_grow:
        base.append("--ho-grow")
    #: ⛔ `--retry-every N` 占 **两个** argv 元素：对照时两个都要摘掉，
    #:    只摘一个 ⇒ 下一个参数被当成它的值吃掉 ⇒ 对照组跑的是别的口径，
    #:    而命令行看起来"只是少了个开关"（最难发现的一种对照污染）。
    _retry_on = int(args.retry_every or 0) > 0
    if _retry_on:
        base += ["--retry-every", str(int(args.retry_every))]
    _variants = max(1, int(args.ho_grow_variants or 1))
    if _variants > 1:
        base += ["--ho-grow-variants", str(_variants)]

    roots = []
    try:
        r1 = make_root()
        roots.append(r1)
        out = run_once([x.replace("@TMP@", r1) for x in base])
        tag = "实验组"
        if args.ho_grow:
            tag += " ho-grow"
        if args.balanced:
            tag += " balanced"
        res = show(out, tag)
        if args.compare:
            r2 = make_root()
            roots.append(r2)
            if _variants > 1:
                b2 = _strip(base, "--ho-grow-variants")
                ctag = "对照组（--ho-grow-variants 1，其余逐字相同）"
                var = "--ho-grow-variants"
            elif _retry_on:
                b2 = _strip(base, "--retry-every")
                ctag = "对照组（不开 --retry-every，其余逐字相同）"
                var = "--retry-every"
            else:
                b2 = [x for x in base if x != "--ho-grow"]
                ctag = "对照组（不开 --ho-grow，其余逐字相同）"
                var = "--ho-grow"
            out2 = run_once([x.replace("@TMP@", r2) for x in b2])
            res2 = show(out2, ctag)
            print(f"\n【决定性对照】唯一变量 = {var}")
            print(f"   提交代数 {res['commits']} vs {res2['commits']}")
            print(f"   reward 轨迹 {res['rewards']}")
            print(f"              {res2['rewards']}")
            print(f"   被吞候选   {res.get('dropped')}")
            print(f"              {res2.get('dropped')}")
            print(f"   队列长度 {res['queue_lens']} vs {res2['queue_lens']}")
    finally:
        for d in roots:
            shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
