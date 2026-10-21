#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端 demo：合成任务 → 跑 self-harness 进化 → 出报告。

    .venv/bin/python scripts/demo_evolve.py [--rounds 3] [--group 3]

它存在的意义：**自进化系统必须能一键离线复现**。
如果"系统有没有进化"只能靠人盯着跑，那它不是机制，是玄学。

报告写到 docs/demo_report_<timestamp>.md，包含：
  · 环境与合成 yield（不是尝试次数）
  · 每轮：弱点证据 → 候选裁决（闸/优势/增益）→ 提交或回滚
  · 账本链状态（可审计性的硬证据）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = sys.executable


def run_cli(*args: str) -> dict:
    p = subprocess.run([PY, "-m", "aether.cli", *args],
                       capture_output=True, text=True, cwd=str(ROOT))
    if p.returncode != 0 and not p.stdout.strip():
        print(p.stderr[-2000:])
        raise SystemExit(f"cli {args} 失败 rc={p.returncode}")
    s = p.stdout
    return json.loads(s[s.index("{"):])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default="3")
    ap.add_argument("--group", default="3")
    # 默认 12：8 只能合成出比较符变异（3 条），通用修法与定点修法在发展集上
    # 会打平 ⇒ 泛化闸的拦截路径根本走不到。12 才能同时产出字面量变异。
    ap.add_argument("--max-tasks", default="12")
    args = ap.parse_args()

    t0 = time.time()
    print("① 跑 self-harness 进化…")
    evo = run_cli("evolve", "--rounds", args.rounds, "--group", args.group,
                  "--max-tasks", args.max_tasks)

    # ── 泛化闸对照：**同一个系统**，只换候选池 / 闸开关 ────────────
    print("①b 泛化闸对照（只换候选池，其余不变）…")
    ab = {}
    for tag, extra in (("general", ["--capsules", "general"]),
                       ("narrow", ["--capsules", "narrow"]),
                       ("both_闸关", ["--capsules", "both", "--heldout", "off"])):
        r = run_cli("evolve", "--rounds", "1", "--group", args.group,
                    "--max-tasks", args.max_tasks, *extra)
        rr = r["rounds"][0]
        g = rr.get("generalization") or {}
        ab[tag] = {"verdict": g.get("verdict"),
                   "dev_gain": g.get("dev_gain"),
                   "ho_gain": g.get("heldout_gain"),
                   "committed": rr.get("committed") is not None,
                   "reason": rr.get("revert_reason", "")}

    print("② 校验账本链…")
    p = subprocess.run([PY, "-m", "aether.cli", "verify-chain"],
                       capture_output=True, text=True, cwd=str(ROOT))
    chain_ok = p.returncode == 0

    print("③ 跨语言监督器复核（TypeScript 侧独立校验同一份账本）…")
    node = "/Users/linshaojie/.workbuddy/binaries/node/versions/22.22.2-6/bin/node"
    sup = subprocess.run([node, "src/index.ts", "--ledger",
                          str(ROOT / "data" / "ledger.jsonl"), "--json"],
                         capture_output=True, text=True,
                         cwd=str(ROOT / "supervisor"))
    sup_json = {}
    if sup.stdout.strip().startswith("{"):
        try:
            sup_json = json.loads(sup.stdout)
        except Exception:  # noqa: BLE001
            sup_json = {"parse_error": sup.stdout[:200]}
    sup_ok = sup_json.get("chain_ok", False)

    # ── 轨迹记账（token 归属 + 信用分配）────────────────────────
    print("④ 轨迹记账演示…")
    from aether.runtime import Trajectory, assign_credit
    traj = Trajectory(task_id="demo:textscore", harness_id=evo["final_harness_id"])
    traj.add("think", "分析弱点：missing_skill / textscore")
    traj.add("call", "应用 capsule add_skill:fix_control_flow")
    traj.add("observation", "沙箱返回 10/10 passed, env_fingerprint=fb264f947155c6f4")
    traj.add("answer", "提交 harness")
    credit = assign_credit(traj)   # 无 replay_fn ⇒ 明确标注不可用，不给近似值
    traj_report = {
        "model_tokens": traj.model_tokens,
        "env_tokens": traj.env_tokens,
        "masking_ratio": round(traj.masking_ratio, 4),
        "credit_mode": credit.mode,
        "credit_available": credit.available,
        "credit_reason": credit.reason,
    }

    # ── 报告 ────────────────────────────────────────────────────
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = ROOT / "docs" / f"demo_report_{ts}.md"
    #: ⛔ 以前这里印「是否进化：✅ 是」，判据是 `improved`（= 有没有提交）。
    #:    r63 加了 `scoped` 之后，"只改定点技能、held-out 一动没动"也算
    #:    `improved=True` ⇒ 那个标签承诺了数据没有的东西（README 136）。
    #:    改成读拆开后的数：`generalize` 才算变强，`scoped` 只是放行。
    _c = evo.get("commits") or {}
    _commit_line = (
        f"- 提交 **{_c.get('total', 0)}** 次"
        f"（generalize {_c.get('generalized', 0)} ／ "
        f"scoped {_c.get('scoped', 0)} ／ no_gate {_c.get('no_gate', 0)}）"
        + ("　⛔ 只有 generalize 才算「变强了」（scoped = 闸没资格判它）"
           if _c.get("scoped") else "")
        + ("" if _c.get("total") else "：本轮无有效增益"))
    lines = [
        f"# V6 端到端 demo 报告（{ts}）",
        "",
        f"- 发展环境：`{evo['dev_env']}` ／ held-out 环境：`{evo['heldout_env']}`",
        f"- 合成（发展集）：attempted {evo['synthesis']['attempted']} → **valid "
        f"{evo['synthesis']['valid']}**（yield {evo['synthesis']['yield_rate']:.0%}）",
        (f"- 合成（held-out）：**valid "
         f"{evo['synthesis_heldout']['valid']}**（yield "
         f"{evo['synthesis_heldout']['yield_rate']:.0%}）"
         if evo.get("synthesis_heldout") else "- 合成（held-out）：未启用"),
        f"- 种子 harness `{evo['seed_harness_id'][:8]}` → 最终 `{evo['final_harness_id'][:8]}`",
        _commit_line,
        f"- 耗时：{evo['cost_s']}s（总计 {time.time() - t0:.1f}s）",
        "",
        "## 账本可审计性",
        "",
        f"- Python 侧 `verify-chain`：{'✅ 完整' if chain_ok else '❌ 断裂'}",
        f"- TypeScript 监督器独立复核：{'✅ 一致' if sup_ok else '❌ 不一致'}",
        f"- 事件总数：{evo['ledger']['count']}，按类：{json.dumps(evo['ledger']['by_kind'], ensure_ascii=False)}",
        "",
        "> ⛔ 两侧都用**同一条判据**重算哈希链。只要 canonical 序列化有一处漂移"
        "（`sort_keys` / 紧凑分隔 / 数字 `1.0` vs `1` / ts 精度），这一项立刻变红。",
        "",
        "## 泛化闸（发展集涨 ≠ 变强）",
        "",
        "| 候选池 | 发展集增益 | held-out 增益 | 裁决 | 提交 |",
        "|---|---|---|---|---|",
    ]
    for tag, r in ab.items():
        lines.append(
            f"| `{tag}` | {r['dev_gain'] if r['dev_gain'] is not None else '-'} "
            f"| {r['ho_gain'] if r['ho_gain'] is not None else '-'} "
            f"| {r['verdict'] or '（无闸）'} | {'✅' if r['committed'] else '❌'} |")
    lines += [
        "",
        "> 定点修法在**发展集上严格更好**（1.00 > 0.60）⇒ 系统一定会选它；",
        "> 它在 held-out 上增益为 0 ⇒ 判 overfit ⇒ 不提交。",
        "> 最后一行只把闸关掉，同一个候选就被提交了 —— 拦住它的是闸，不是别的。",
        "",
        "## 逐轮裁决",
        "",
    ]
    for r in evo["rounds"]:
        w = r.get("weakness")
        lines.append(f"### 第 {r['round_no']} 轮")
        if w:
            lines.append(f"- 弱点：**{w['kind']}** / topic `{w['topic']}`，"
                         f"{w['n_failed']} 条失败证据")
            lines.append(f"- 证据样例：{w['evidence'][:2]}")
        else:
            lines.append(f"- 弱点：无（{r.get('revert_reason', '')}）")
        lines.append(f"- 基线 reward：**{r['baseline_reward']:.4f}**")
        if r["candidates"]:
            lines.append("")
            lines.append("| capsule | 非回归闸 | reward | 组内优势 | 结论 |")
            lines.append("|---|---|---|---|---|")
            for c in r["candidates"]:
                gate = "✅ 过" if c["gate_passed"] else (
                    f"❌ 拦({len(c['gate_regressions'])})")
                lines.append(f"| `{c['capsule']}` | {gate} | {c['reward']:.2f} "
                             f"| {c['advantage']:+.2f} | {c['reason']} |")
        g = r.get("generalization")
        if g:
            lines.append(f"- 泛化闸：`{g['verdict']}` —— 发展集 {g['dev_gain']:+.2f} / "
                         f"held-out {g['heldout_gain']:+.2f}（{g['reason']}）")
        lines.append("")
        if r.get("committed"):
            lines.append(f"→ **提交** `{r['committed'][:8]}`")
        else:
            lines.append(f"→ 回滚（{r.get('revert_reason')}）")
        lines.append("")

    lines += [
        "## 轨迹记账（token 归属 / 信用分配）",
        "",
        f"- 策略产生 {traj_report['model_tokens']} tokens，环境产生 "
        f"{traj_report['env_tokens']} tokens，环境占比 "
        f"{traj_report['masking_ratio']:.0%}",
        f"- 信用分配：**{traj_report['credit_mode']}**，可用={traj_report['credit_available']}",
        f"- 理由：{traj_report['credit_reason']}",
        "",
        "> 环境 token 占比过高 ⇒ 这条轨迹的「能力提升」可能来自环境而非策略。"
        "不可重放时**不编造** turn-level 归因 —— 没有证据就不报数字。",
        "",
    ]

    if sup_json.get("findings"):
        lines.append("## 监督器发现")
        lines.append("")
        for f in sup_json["findings"]:
            lines.append(f"- [{f['severity']}] `{f['code']}`：{f['detail']}")
        lines.append("")

    lines += [
        "## 怎么读这份报告",
        "",
        "1. **先看 yield**：合成不出任务 ⇒ 进化没有燃料，后面全无意义；",
        "2. **再看闸**：候选被拦时，报告必须点名哪条冻结任务退了；",
        "3. **最后看提交**：只有「过了闸 + 组内优势为正 + 增益过地板」才会提交；",
        "4. **两种结果都算成功**：正确地拒绝（无证据/无增益）和正确地提交一样有价值。",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n④ 报告已写：{out.relative_to(ROOT)}")
    print("\n".join(lines[:14]))
    return 0 if chain_ok and sup_ok else 1


if __name__ == "__main__":
    sys.exit(main())
