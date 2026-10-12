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
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent


#: 自带环境。**两个是刻意的**：一个当发展环境，一个当 held-out。
#: 只有一个环境时，"变强了"和"学会了讨好判据"无法区分。
DEFAULT_ENVS = ("textscore", "eventflow")

#: 任务类别。**路由按它来**，不按环境名 —— 见 TemplateProposer.route_key。
TASK_CLASS = "repair"


def _paths(root: Path) -> Dict[str, Path]:
    return {
        "ledger": root / "data" / "ledger.jsonl",
        "db": root / "data" / "aether.db",
        "task": root / "tasks" / "textscore",
        "tasks_root": root / "tasks",
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


def _default_runner(envs, memory=None, timeout: float = 10.0):
    """把 harness 变成"能修好环境里的 bug"的执行体（多环境版）。

    ⛔ 执行逻辑现在住在 `agent.HarnessAgent`：harness 的 **5 层都会影响结果**
    （L0 元规则可禁用修法 / L1 路由决定技能可不可达 / L2 工具缺失直接失败 /
    L3 是修法本身 / L4 记忆是第二条通路）。

    ⛔ 两类技能的区别，它把 "reward hacking" 变成可判定的：

        fix_control_flow        —— **保守通用修法**：任何环境都能修，但修不全
        patch::<env_id>         —— **定点修法**：只在点名的那个环境生效，修得全

    在**单一环境**的评测里，后者**严格更好**（发展集分数更高）⇒ 它一定会被
    选中 ⇒ 一定会被提交。只有 held-out 能把这两者分开。
    """
    from .agent import HarnessAgent
    return HarnessAgent(envs, memory=memory, timeout=timeout).as_runner()


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
    from .environment import Environment, synthesize_tasks
    from .evolution import EvolutionConfig, EvolutionLoop
    from .generalization import GeneralizationGate, make_heldout
    from .genome import Harness
    from .ledger import Ledger
    from .nongression import Baseline, FrozenTask, NonRegressionGate
    from .providers import StubProvider, TemplateProposer
    from .store import Store

    p = _paths(Path(args.root))
    envs = {name: Environment.from_dir(p["tasks_root"] / name)
            for name in DEFAULT_ENVS}
    dev_name = args.dev_env
    ho_name = args.heldout_env
    if dev_name not in envs or ho_name not in envs:
        raise SystemExit(f"未知环境：dev={dev_name} heldout={ho_name}，可用 {sorted(envs)}")
    if dev_name == ho_name:
        raise SystemExit("发展环境与 held-out 环境不能是同一个 ⇒ 泛化闸会自我取消")

    led = Ledger(p["ledger"])
    store = Store(p["db"])
    runner = _default_runner(envs)

    # ⛔ 种子 harness **必须先建**：冻结基线要用它跑。
    #    用空 `Harness()` 跑基线 ⇒ 连工具都没有 ⇒ 基线全红 ⇒
    #    非回归闸"没有原本通过的任务"⇒ 对任何候选都放行的空闸（V5 同款陷阱）。
    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5, "min_content_len": 150})

    # 冻结套件：**两个环境**的原始源码都应当全绿
    frozen = [FrozenTask(task_id=f"frozen::{name}",
                         spec={"env_id": name, "broken_source": e.source,
                               "topic": name, "task_class": TASK_CLASS})
              for name, e in envs.items()]
    caliber = {"seed": int(args.seed), "timeout": 10.0, "split": "frozen"}
    baseline = Baseline(
        caliber=caliber,
        verdicts={t.task_id: runner(h0, t.spec) for t in frozen},
        harness_id="seed")
    gate = NonRegressionGate(frozen, baseline)

    rep_synth = synthesize_tasks(envs[dev_name], max_n=int(args.max_tasks))
    dev = [FrozenTask(task_id=t.task_id,
                      spec={"env_id": dev_name, "broken_source": t.broken_source,
                            "topic": dev_name, "task_class": TASK_CLASS})
           for t in rep_synth.tasks]

    # held-out：**另一个环境**的合成任务。它不参与候选之间的比较。
    heldout = None
    rep_ho = None
    diff_report = None
    if args.heldout == "on":
        from .repair import difficulty_report
        rep_ho = synthesize_tasks(envs[ho_name], max_n=int(args.max_tasks))
        if rep_ho.tasks:
            ho_tasks = [
                FrozenTask(task_id=t.task_id,
                           spec={"env_id": ho_name,
                                 "broken_source": t.broken_source,
                                 "topic": ho_name, "task_class": TASK_CLASS})
                for t in rep_ho.tasks]
            # ⛔ held-out 也要有**难度跨度**：全是简单题的话，它会放行
            #    "只会修简单题"的候选。难度由"通用修法能否修好"执行判定。
            diff_report = difficulty_report(envs[ho_name], ho_tasks)
            if not diff_report["spread"]:
                print(f"⚠️ held-out 只有单一难度（easy {diff_report['n_easy']}"
                      f" / hard {diff_report['n_hard']}）⇒ 判别力不足，"
                      f"它无法区分「真泛化」与「只会修这类题」", file=sys.stderr)
            heldout = GeneralizationGate(
                make_heldout(ho_name, ho_tasks,
                             difficulty=diff_report["labels"]),
                dev_task_ids=[t.task_id for t in dev],
                heldout_floor=float(args.heldout_floor),
                base_at_ceiling=1.0)
        else:
            print(f"⚠️ held-out 环境 {ho_name} 合成 yield=0 ⇒ 泛化闸无从裁决",
                  file=sys.stderr)

    # ⛔ 两类技能刻意并列：通用但保守 vs 定点但只此一地。
    #    在**发展集**上后者严格更好 ⇒ 一定会被选中 ⇒ 一定会被提交，
    #    除非泛化闸拦住它。这就是 reward hacking 的可执行形态。
    pool = {
        "fix_control_flow": "保守通用修法：只恢复控制流（比较/运算），不动字面量",
        f"patch:{dev_name}": f"定点修法：{dev_name} 整个文件换回基线（换环境即失效）",
    }
    if args.capsules == "general":
        pool = {"fix_control_flow": pool["fix_control_flow"]}
    elif args.capsules == "narrow":
        pool = {f"patch:{dev_name}": pool[f"patch:{dev_name}"]}
    # ⛔ 路由键用**任务类别**而不是环境名：按环境名路由本身就是一种定点化，
    #    换环境就失效。泛化与否在路由层就已经是结构选择了。
    template = TemplateProposer(skills=pool, route_key=TASK_CLASS)
    proposer: Any = template
    if getattr(args, "proposer", "template") == "llm":
        # 真模型提议器。**同协议**替换模板提议器 —— 闸一个不少：
        # 语法闸（L0 禁改/白名单）→ 解析闸 → 非回归闸 → 泛化闸 → 能力地板。
        from .providers import LLMProposer, OllamaProvider
        model = getattr(args, "llm_model", "") or os.environ.get(
            "V6_OLLAMA_MODEL", "Qwen3.5-4B:latest")
        try:
            proposer = LLMProposer(OllamaProvider(model=model, think=False))
            print(f"🧠 proposer=llm（{model}）", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 真模型不可用（{type(e).__name__}: {e}）⇒ 回退模板提议器",
                  file=sys.stderr)
            proposer = template
    loop = EvolutionLoop(led, gate, dev, runner, proposer, store=store,
                         caliber=caliber, heldout=heldout,
                         config=EvolutionConfig(group_size=int(args.group)))

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
    # ⛔ 提议器诊断必须落盘：看到"0 个候选"时要能分辨
    #    「闸在工作」/「模型没接通」/「输出被截断」—— 三者处置完全不同，
    #    缺了它，进化循环"正确地什么都不做"和"供给根本没通"长得一模一样。
    diag = proposer.diagnostics() if hasattr(proposer, "diagnostics") else \
        {"name": "template", "error": "", "rejects": []}
    if diag.get("error"):
        print(f"ℹ️ proposer 诊断：{diag}", file=sys.stderr)
    out = {
        "dev_env": dev_name,
        "heldout_env": ho_name if heldout is not None else None,
        "proposer": diag,
        "synthesis": rep_synth.to_dict(),
        "synthesis_heldout": rep_ho.to_dict() if rep_ho is not None else None,
        "heldout_difficulty": ({k: v for k, v in diff_report.items()
                                if k != "labels"} if diff_report else None),
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
    e.add_argument("--max-tasks", default="12")
    e.add_argument("--capsules", default="both",
                   choices=["both", "general", "narrow"],
                   help="提议池：both=两类都给（定点修法必赢）/ general / narrow")
    e.add_argument("--seed", default="0")
    e.add_argument("--dev-env", default="textscore", help="发展集所在环境")
    e.add_argument("--heldout-env", default="eventflow", help="held-out 环境（不参与选择）")
    e.add_argument("--heldout", default="on", choices=["on", "off"],
                   help="off ⇒ 关闭泛化闸（用于对照：过拟合候选会被提交）")
    e.add_argument("--proposer", default="template", choices=["template", "llm"],
                   help="变异提议器：template（离线确定性）/ llm（真模型）")
    e.add_argument("--llm-model", default="", help="真模型名（默认 Qwen3.5-4B:latest）")
    e.add_argument("--heldout-floor", default="0.0",
                   help="held-out 增益地板（低于它判 overfit）")

    args = ap.parse_args(argv)
    return {
        "status": cmd_status,
        "verify-chain": cmd_verify_chain,
        "synth": cmd_synth,
        "evolve": cmd_evolve,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
