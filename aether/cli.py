"""命令行入口：让整套机制**离线可跑**（自进化系统的判据必须可回归）。

    python -m aether.cli status            账本完整性 + 库统计
    python -m aether.cli synth             合成任务（看 yield，不是看尝试次数）
    python -m aether.cli evolve --rounds 3 跑一轮完整 self-harness 进化
    python -m aether.cli verify-chain       校验账本哈希链（跨语言监督器同款判据）

⛔ 所有命令**默认离线**：不联网、不调模型。要接真模型用 --provider http。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path) -> Dict[str, Path]:
    return {
        "ledger": root / "data" / "ledger.jsonl",
        "db": root / "data" / "aether.db",
        "task": root / "tasks" / "textscore",
    }


# ──────────────────────────────────────────────────────────────────
# 各子命令
# ──────────────────────────────────────────────────────────────────
def cmd_status(args: argparse.Namespace) -> int:
    from .ledger import Ledger
    from .store import Store
    p = _paths(Path(args.root))
    led = Ledger(p["ledger"])
    st = led.stats()
    print(json.dumps({"ledger": st}, ensure_ascii=False, indent=2))
    if p["db"].exists():
        s = Store(p["db"])
        print(json.dumps({"store": s.stats()}, ensure_ascii=False, indent=2))
        s.close()
    ok = st["chain_ok"]
    print("账本链：" + ("✅ 完整" if ok else f"❌ 断裂 @seq={st['first_bad_seq']} {st['reason']}"))
    return 0 if ok else 1


def cmd_verify_chain(args: argparse.Namespace) -> int:
    from .ledger import Ledger
    led = Ledger(_paths(Path(args.root))["ledger"])
    ok, seq, why = led.verify_chain()
    print("✅ 链完整" if ok else f"❌ 断裂 @seq={seq}: {why}")
    return 0 if ok else 1


def cmd_synth(args: argparse.Namespace) -> int:
    from .environment import Environment, synthesize_tasks, verify_source
    p = _paths(Path(args.root))
    env = Environment.from_dir(p["task"])
    base = verify_source(env)
    print(f"环境 {env.env_id} fingerprint={env.fingerprint} "
          f"基线通过 {base.n_passed}/{len(base.checks)}")
    rep = synthesize_tasks(env, max_n=int(args.max))
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    for t in rep.tasks[:5]:
        print(f"  ✓ {t.task_id}  {t.desc}  F2P={list(t.fail_to_pass)}")
    # ⛔ yield=0 是**失败信号**，不是"这次没合成出来而已"
    if rep.valid == 0:
        print("❌ yield=0：这个环境合成不出有效任务 ⇒ 进化没有燃料")
        return 1
    return 0


def _fact_verifier(note) -> tuple:
    """记忆提升的**对抗验证器**：职责是尽力证伪，不是确认。

    ⛔ 方向不能反：默认"通过"的提升等于没有验证。这里主动找反证
    （内容太短、含占位词、含未验证标记），找到就证伪。
    """
    from .memory import MemNote
    body = (note.content or "").strip()
    if len(body) < 40:
        return False, "内容过短，不足以作为稳定事实"
    if body.lower() in {"pass", "ok", "done", "todo", "n/a"}:
        return False, "占位内容"
    if any(mark in body for mark in ("TODO", "待确认", "可能", "大概是")):
        return False, "含未验证标记（可能/大概是/TODO）"
    return True, "未找到反证"


def _default_runner(env, timeout: float = 10.0):
    """把 harness 变成"能修好环境里的 bug"的执行体。

    ⛔ 这是**演示用** runner：它按 harness 里的 skills 去选修复动作。
    真实 runner 应该跑真 agent；但机制（闸/选择/记账）与 runner 无关，
    这正是依赖注入的意义 —— 先证明机制，再换真执行体。
    """
    from .verifier import Verdict

    def runner(h, spec):
        src = spec.get("broken_source", "")
        if not src:
            return Verdict(passed=False, checks=(), error="spec 缺 broken_source")
        # harness 的技能决定"用哪种修法"：有 fix 技能 ⇒ 用原始正确源码
        known = [k for k in h.skills if k in env.source]
        if "restore_from_baseline" in h.skills:
            fixed = env.source
        elif known:
            fixed = env.source
        else:
            fixed = src          # 没有技能 ⇒ 原样返回（必然不过）
        return _verify_fixed(env, src, fixed, timeout=timeout)
    return runner


def _verify_fixed(env, broken_src, fixed_src, timeout: float = 10.0):
    """判定：修好后是否**所有原本通过的测试都回来了**（可执行判据）。"""
    from .environment import verify_source
    from .verifier import Check, Verdict
    v = verify_source(env, source=fixed_src, timeout=timeout)
    n = len(v.checks)
    return Verdict(passed=v.passed, checks=v.checks,
                   evidence={"n_passed": v.n_passed, "n_total": n,
                             "changed": fixed_src != broken_src},
                   env_fingerprint=env.fingerprint, cost_s=v.cost_s)


def cmd_evolve(args: argparse.Namespace) -> int:
    from .environment import Environment
    from .evolution import EvolutionConfig, EvolutionLoop
    from .genome import Harness
    from .ledger import Ledger
    from .nongression import Baseline, FrozenTask, NonRegressionGate
    from .providers import StubProvider, TemplateProposer
    from .store import Store

    p = _paths(Path(args.root))
    env = Environment.from_dir(p["task"])
    led = Ledger(p["ledger"])
    store = Store(p["db"])

    # 冻结套件：用**原始源码**做基线（它应当全绿），变异体进发展集
    frozen = [FrozenTask(task_id=f"frozen::{env.env_id}",
                         spec={"broken_source": env.source,
                               "topic": env.env_id})]
    caliber = {"seed": int(args.seed), "timeout": 10.0, "split": "frozen"}
    runner = _default_runner(env)

    base_v = runner(Harness(), frozen[0].spec)
    baseline = Baseline(caliber=caliber, verdicts={frozen[0].task_id: base_v},
                        harness_id="seed")
    gate = NonRegressionGate(frozen, baseline)

    rep_synth = None
    from .environment import synthesize_tasks
    rep_synth = synthesize_tasks(env, max_n=int(args.max_tasks))
    dev = [FrozenTask(task_id=t.task_id,
                      spec={"broken_source": t.broken_source, "topic": env.env_id})
           for t in rep_synth.tasks]

    proposer = TemplateProposer(skills={
        "restore_from_baseline": "把被变异的函数恢复到基线实现（可执行、带测试）",
        "merge_keywords_fix": "修复关键词合并的顺序与去重",
    })
    loop = EvolutionLoop(led, gate, dev, runner, proposer, store=store,
                         caliber=caliber,
                         config=EvolutionConfig(group_size=int(args.group)))

    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5, "min_content_len": 150})
    store.save_snapshot(h0.harness_id, h0.to_dict(), accepted=True, round_no=0)

    # ── 记忆层接线（不是摆设：每轮的证据都进记忆，且可查完整性）──
    from .memory import MemoryOS
    mem = MemoryOS(min_content_len=60)
    mem.write("L0", h0.meta_rules, channel="curated", source="seed-harness")

    t0 = time.time()
    final, reports = loop.run(h0, rounds=int(args.rounds))

    for r in reports:
        mem.write("L4",
                  f"第 {r.round_no} 轮进化：基线 reward={r.baseline_reward:.2f}，"
                  f"候选 {len(r.candidates)} 个，"
                  f"结果={'提交 ' + (r.committed or '')[:8] if r.committed else '回滚：' + r.revert_reason}",
                  channel="system", source=f"round{r.round_no}")
        if r.weakness:
            mem.write("L3",
                      f"{r.weakness.kind} / {r.weakness.topic}："
                      f"{'; '.join(r.weakness.evidence[:2])}",
                      channel="learner", source=f"round{r.round_no}")

    # 对抗验证演示：verifier 的职责是**证伪**，找不到反证才提升
    promoted = []
    for nid in list(mem.notes)[:3]:
        ok, why = mem.promote(nid, verifier=_fact_verifier, channel="curated")
        promoted.append({"note": nid, "ok": ok, "why": why})
    # fail-closed：无验证器 ⇒ 拒绝提升
    _, no_verifier_why = mem.promote(list(mem.notes)[0], verifier=None) if mem.notes else (False, "")
    consolidated = mem.consolidate(min_access=0)
    out = {
        "env": env.env_id,
        "synthesis": rep_synth.to_dict(),
        "rounds": [r.to_dict() for r in reports],
        "final_harness_id": final.harness_id,
        "seed_harness_id": h0.harness_id,
        "improved": any(r.improved for r in reports),
        "cost_s": round(time.time() - t0, 2),
        "ledger": led.stats(),
        "memory": mem.integrity(),
        "memory_promote": promoted,
        "memory_promote_without_verifier": no_verifier_why,
        "memory_consolidated": len(consolidated),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    store.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="aether", description="V6 · AETHER CLI")
    ap.add_argument("--root", default=str(ROOT), help="V6 根目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="账本 + 库统计")
    sub.add_parser("verify-chain", help="校验账本哈希链")
    s = sub.add_parser("synth", help="合成任务并统计 yield")
    s.add_argument("--max", default="8")
    e = sub.add_parser("evolve", help="跑 self-harness 进化")
    e.add_argument("--rounds", default="3")
    e.add_argument("--group", default="3")
    e.add_argument("--max-tasks", default="8")
    e.add_argument("--seed", default="0")

    args = ap.parse_args(argv)
    return {
        "status": cmd_status,
        "verify-chain": cmd_verify_chain,
        "synth": cmd_synth,
        "evolve": cmd_evolve,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
