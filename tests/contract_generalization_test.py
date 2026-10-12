#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：跨环境泛化闸 —— **"发展集涨了"不等于"变强了"**。

非回归闸只能回答"有没有变坏"，回答不了另一个方向的问题：
在单一判据集上做选择，最优策略会滑向"针对判据硬编码"——它每一轮都在涨，
但它没有变强。这是 reward hacking 在自进化系统里的温和形态，也是最难被
发现的：所有数字都很好看，而且每一步都真的通过了执行判据。

本文件守的是三条硬纪律：
  H1 held-out **不参与选择**（它一参与选择就变成第二张发展集，闸自我取消）
  H2 held-out 与发展集**结构性不重叠**（构造期就校验，不靠调用方自觉）
  H3 发展集涨而 held-out 不涨 ⇒ 判 overfit，**不提交**

以及两条"闸不是装饰"的反证（D 组）。

跑法（纯内存 runner，秒级）：
    python tests/contract_generalization_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


# ── 一个纯内存的 runner：环境 A / B，两类技能 ──────────────────────
def _ok(tid):
    from aether.verifier import Check, Verdict
    return Verdict(passed=True, checks=(Check(tid, True, "got=1 expect=1"),),
                   evidence={"env": tid})


def _bad(tid):
    from aether.verifier import Check, Verdict
    return Verdict(passed=False, checks=(Check(tid, False, "got=0 expect=1"),),
                   evidence={"env": tid})


def _runner(h, spec):
    """env A = 发展环境，env B = held-out 环境。

    `gen`          ⇒ 两个环境都修好（真泛化）
    `narrow:<env>` ⇒ 只修点名的那一个环境（讨好判据）
    """
    env = spec.get("env", "")
    if spec.get("frozen"):
        return _ok("frozen")
    if "gen" in h.skills:
        return _ok(env)
    if f"narrow:{env}" in h.skills:
        return _ok(env)
    return _bad(env)


def main():
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.generalization import (GeneralizationGate, HeldOutSuite,
                                       ProbeResult, VERDICTS, make_heldout)
    from aether.genome import Capsule, Gene, Harness
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate

    DEV, HO = "A", "B"

    def dev_tasks(n=3):
        return [FrozenTask(task_id=f"dev{i}", spec={"env": DEV}) for i in range(n)]

    def ho_tasks(n=3):
        return [FrozenTask(task_id=f"ho{i}", spec={"env": HO}) for i in range(n)]

    def mk_gate(**kw):
        return GeneralizationGate(make_heldout(HO, ho_tasks()),
                                  dev_task_ids=[t.task_id for t in dev_tasks()],
                                  base_at_ceiling=1.0, **kw)

    def capsule(kind, env=DEV):
        key = "gen" if kind == "gen" else f"narrow:{env}"
        return Capsule(name=kind,
                       genes=(Gene(op="set", target=f"L3.skills.{key}",
                                   value="修法", rationale="测试用"),))

    class FixedProposer:
        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    def mk_loop(tmp, caps, heldout, ledger=None):
        led = ledger or Ledger(Path(tmp) / "ledger.jsonl")
        frozen = [FrozenTask(task_id="frozen::A", spec={"env": DEV, "frozen": True}),
                  FrozenTask(task_id="frozen::B", spec={"env": HO, "frozen": True})]
        cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}
        base = Baseline(caliber=cal,
                        verdicts={t.task_id: _runner(Harness(), t.spec)
                                  for t in frozen},
                        harness_id="seed")
        return EvolutionLoop(led, NonRegressionGate(frozen, base), dev_tasks(),
                             _runner, FixedProposer(caps), caliber=cal,
                             heldout=heldout, config=EvolutionConfig(group_size=3))

    def run_once(tmp, caps, heldout, ledger=None):
        loop = mk_loop(tmp, caps, heldout, ledger)
        rep = loop.run_round(Harness(), round_no=1)
        return loop, rep

    # ── A 组：结构与纪律 ───────────────────────────────────────────
    print("A 组：结构 + 三条硬纪律")
    g = mk_gate()
    ck("A1 闸可构造且 probe 可调用", callable(getattr(g, "probe", None)))

    try:
        GeneralizationGate(make_heldout(HO, ho_tasks()), dev_task_ids=["ho0"])
        ck("A2 H2：held-out 与发展集重叠 ⇒ 构造期抛异常（不靠自觉）", False,
           "竟然没抛")
    except ValueError as e:
        ck("A2 H2：held-out 与发展集重叠 ⇒ 构造期抛异常（不靠自觉）",
           "重叠" in str(e), str(e))

    # ⛔ 这条刻意写死：新增一个裁决取值，必须**有人来这里确认是有意的**。
    #    （改成 `>=` 就废了这条闸 —— 那时任何人都能悄悄加一种"放行"态。）
    #    2026-10-13 人工确认新增 `partial`：总体涨但只在部分难度层涨
    #    （典型：只会修 easy 题）。它**拦截**，不放行。见 contract_difficulty_test。
    ck("A3 裁决取值是封闭集合（新增取值须人工确认，此处已确认 partial）",
       set(VERDICTS) == {"generalize", "overfit", "partial", "no_gain",
                         "inconclusive"}, f"{VERDICTS}")
    ck("A3b partial 是**拦截**态（新增取值不得偷偷放行）",
       ProbeResult(verdict="partial").blocks is True)
    ck("A4 blocks 语义：只有 generalize 放行（未知一律拦，fail-closed）",
       ProbeResult(verdict="generalize").blocks is False
       and all(ProbeResult(verdict=v).blocks for v in VERDICTS
               if v != "generalize"))
    ck("A5 held-out 任务数不足 ⇒ inconclusive 且拦截",
       mk_gate(min_tasks=99).probe(Harness(), Harness(), _runner,
                                   dev_gain=1.0).verdict == "inconclusive")

    # ── B 组：判别力 ───────────────────────────────────────────────
    print("B 组：判别力（同一个 dev 增益，held-out 不同 ⇒ 裁决不同）")
    from aether.genome import apply_capsule
    gh = apply_capsule(Harness(), capsule("gen"))
    nh = apply_capsule(Harness(), capsule("narrow"))

    p_gen = g.probe(Harness(), gh, _runner, dev_gain=1.0)
    p_nar = g.probe(Harness(), nh, _runner, dev_gain=1.0)
    ck("B1 通用修法 ⇒ generalize 且 held-out 增益 > 0",
       p_gen.verdict == "generalize" and p_gen.heldout_gain > 0,
       f"{p_gen.verdict} {p_gen.heldout_gain}")
    ck("B2 定点修法 ⇒ overfit（dev 涨但 held-out 纹丝不动）",
       p_nar.verdict == "overfit" and p_nar.dev_gain > 0
       and p_nar.heldout_gain <= 0,
       f"{p_nar.verdict} dev={p_nar.dev_gain} ho={p_nar.heldout_gain}")
    ck("B3 两个候选 dev 增益相同 ⇒ 差别只可能来自 held-out（判据同源）",
       p_gen.dev_gain == p_nar.dev_gain)
    ck("B4 发展集没涨 ⇒ no_gain（不会被误记成 overfit）",
       g.probe(Harness(), gh, _runner, dev_gain=0.0).verdict == "no_gain")

    # ── C 组：接入进化循环 ─────────────────────────────────────────
    print("C 组：接入进化循环（过拟合候选必须被拦下）")
    with tempfile.TemporaryDirectory(prefix="v6gen_") as td:
        _, r_nar = run_once(td, [capsule("narrow")], mk_gate())
        ck("C1 overfit 候选 ⇒ 不提交", r_nar.committed is None,
           f"{r_nar.committed}")
        ck("C2 不提交的原因被说明（能说清为什么）",
           "generalization_gate" in r_nar.revert_reason, r_nar.revert_reason)

        _, r_gen = run_once(td, [capsule("gen")], mk_gate())
        ck("C3 generalize 候选 ⇒ 提交", r_gen.committed is not None,
           r_gen.revert_reason)

        # C4/C5：held-out **不得**参与选择 —— 换一个反向的 held-out runner，
        # dev 侧的判定必须一字不差。
        g_normal = GeneralizationGate(
            make_heldout(HO, ho_tasks()),
            dev_task_ids=[t.task_id for t in dev_tasks()],
            base_at_ceiling=1.0)
        g_inverted = GeneralizationGate(
            make_heldout(HO, ho_tasks()),
            dev_task_ids=[t.task_id for t in dev_tasks()],
            base_at_ceiling=1.0)
        g_inverted.evaluate = lambda h, runner: (
            (1.0 if any(k.startswith("narrow:") for k in h.skills) else 0.0),
            [])

        led1 = Ledger(Path(td) / "l1.jsonl")
        led2 = Ledger(Path(td) / "l2.jsonl")
        _, r1 = run_once(td, [capsule("gen"), capsule("narrow")], g_normal, led1)
        _, r2 = run_once(td, [capsule("gen"), capsule("narrow")], g_inverted, led2)

        def dev_eval_sig(led):
            out = []
            for e in led.events():
                if e.kind == "eval.verdict" and e.payload.get("split") == "dev":
                    out.append((e.payload.get("harness_id"), e.payload.get("reward")))
            return out

        ck("C4 H1：换掉 held-out 的打分方式 ⇒ 发展集判定**一字不差**"
           "（held-out 没参与选择）",
           dev_eval_sig(led1) == dev_eval_sig(led2),
           f"{dev_eval_sig(led1)[:3]} vs {dev_eval_sig(led2)[:3]}")

        seqs = {}
        for e in led1.events():
            seqs.setdefault(e.kind, []).append(e.seq)
        ck("C5 探针跑在评估之后（gen.probe 晚于所有 dev 判定）",
           bool(seqs.get("gen.probe"))
           and max(seqs["gen.probe"]) > max(seqs["eval.verdict"]),
           f"{seqs.get('gen.probe')} vs {max(seqs.get('eval.verdict', [0]))}")

    # ── D 组：反证（证明这道闸不是装饰）──────────────────────────
    print("D 组：反证 —— 改闸的结果，行为必须翻转")
    with tempfile.TemporaryDirectory(prefix="v6gen_") as td:
        # D1：把闸改成恒放行 ⇒ overfit 候选**必须**被提交
        g_open = mk_gate()
        g_open.probe = lambda b, c, r, dev_gain=0.0, **kw: ProbeResult(
            verdict="generalize", dev_gain=dev_gain, heldout_gain=1.0,
            n_tasks=3, reason="事故版：恒放行")
        _, r_open = run_once(td, [capsule("narrow")], g_open)
        ck("D1 事故版：闸恒放行 ⇒ overfit 候选被提交（C1 不是恒不提交）",
           r_open.committed is not None, r_open.revert_reason)

        # D2：把闸改成恒拦截 ⇒ generalize 候选**必须**不被提交
        g_shut = mk_gate()
        g_shut.probe = lambda b, c, r, dev_gain=0.0, **kw: ProbeResult(
            verdict="overfit", dev_gain=dev_gain, heldout_gain=0.0,
            n_tasks=3, reason="事故版：恒拦截")
        _, r_shut = run_once(td, [capsule("gen")], g_shut)
        ck("D2 事故版：闸恒拦截 ⇒ generalize 候选不被提交（C3 不是恒提交）",
           r_shut.committed is None, f"{r_shut.committed}")

        # D3：把 held-out 换成与发展集**同一个环境** ⇒ 过拟合候选应被误放行
        #     （证明"跨环境"是必要条件，不是修饰词）
        g_same = GeneralizationGate(
            make_heldout(DEV, [FrozenTask(task_id=f"x{i}", spec={"env": DEV})
                               for i in range(3)]),
            dev_task_ids=[t.task_id for t in dev_tasks()],
            base_at_ceiling=1.0)
        p_same = g_same.probe(Harness(), nh, _runner, dev_gain=1.0)
        ck("D3 事故版：held-out 与发展集同环境 ⇒ 定点修法被误判泛化"
           "（所以 H2 的不重叠是硬约束）",
           p_same.verdict == "generalize", f"{p_same.verdict}")

    # ── E 组：难度分层的**同层对照**（守"自指死锁"不再复发）────────
    #
    # ⛔ 背景（2026-10-13 实测事故）：`hard` 的定义是「通用修法修不好」
    #    ⇒ 任何通用修法在 hard 层的增益必然为 0 ⇒ 只要判据是
    #    "某层没涨 ⇒ 拦截"，唯一能泛化的修法就会被唯一能鉴别泛化的闸
    #    永久拦住 ⇒ 端到端 evolve 三轮 × 两组全部不提交，improved=False。
    #    修法：分层裁决要与发展集**同层**对照，区分「能力边界」与「挑食」。
    print("\nE 组：难度分层同层对照 —— 能力边界 ≠ 挑食")

    def mk_diff_gate(base_map, cand_map, difficulty):
        """按 (task_id → reward) 精确控制 base/cand 在 held-out 的表现。"""
        tasks = [FrozenTask(task_id=t, spec={"env": HO, "probe": 1})
                 for t in sorted(base_map)]
        suite = make_heldout(HO, tasks, difficulty=difficulty)
        # 用 meta_rules 里的标记区分 base / cand（runner 只看 harness）
        def _probe_runner(h, spec):
            tid = spec.get("env", "") + "|" + str(spec.get("probe", ""))
            tid = str(spec.get("tid", ""))
            m = cand_map if "CAND" in (h.meta_rules or "") else base_map
            return _ok(tid) if m.get(tid, 0.0) > 0 else _bad(tid)

        suite_tasks = [FrozenTask(task_id=t, spec={"tid": t})
                       for t in sorted(base_map)]
        g = GeneralizationGate(
            make_heldout(HO, suite_tasks, difficulty=difficulty),
            dev_task_ids=["dev0"], base_at_ceiling=None)
        base_h = Harness(meta_rules="BASE")
        cand_h = Harness(meta_rules="CAND")
        return g, base_h, cand_h, _probe_runner

    DIFF = {"h_easy": "easy", "h_hard1": "hard", "h_hard2": "hard"}
    # E1：通用修法 —— held-out easy 涨、hard 不涨；发展集 hard **同样**不涨
    #     ⇒ 那是能力边界，不是挑食 ⇒ 必须放行（否则进化永久空转）
    base_map = {"h_easy": 0.0, "h_hard1": 0.0, "h_hard2": 0.0}
    cand_map = {"h_easy": 1.0, "h_hard1": 0.0, "h_hard2": 0.0}
    g_e, b_h, c_h, pr_runner = mk_diff_gate(base_map, cand_map, DIFF)
    p_e = g_e.probe(b_h, c_h, pr_runner, dev_gain=0.6,
                    dev_by_difficulty={"easy": 1.0, "hard": 0.0})
    ck("E1 能力边界：hard 未涨但发展集同层也未涨 ⇒ generalize（放行）",
       p_e.verdict == "generalize", f"{p_e.verdict} / {p_e.reason[:60]}")
    ck("E1b 放行时必须点名 out_of_reach（裁决可审计）",
       tuple(p_e.out_of_reach) == ("hard",), f"{p_e.out_of_reach}")

    # E2：挑食候选 —— held-out easy 涨、hard 不涨，但发展集 hard **涨了**
    #     ⇒ 同一层，换个环境就修不动 ⇒ 真挑食 ⇒ 必须拦截
    g_e2, b2, c2, pr2 = mk_diff_gate(base_map, cand_map, DIFF)
    p_e2 = g_e2.probe(b2, c2, pr2, dev_gain=1.0,
                      dev_by_difficulty={"easy": 1.0, "hard": 1.0})
    ck("E2 挑食：hard 未涨但发展集同层涨了 ⇒ partial（拦截）",
       p_e2.verdict == "partial" and p_e2.blocks,
       f"{p_e2.verdict} / {p_e2.reason[:60]}")

    # E3：拿不到同层对照 ⇒ **fail-closed 拦截**，但裁决必须是 inconclusive
    #     （"缺数据"）而不是 partial（"有挑食证据"）—— V5：不可判别 ≠ 有罪。
    #     两者的处置完全不同：前者该去补发展集的该难度层，后者该改候选。
    g_e3, b3, c3, pr3 = mk_diff_gate(base_map, cand_map, DIFF)
    p_e3 = g_e3.probe(b3, c3, pr3, dev_gain=0.6)
    ck("E3 无对照数据 ⇒ fail-closed 拦截，且判 inconclusive（不是 partial）",
       p_e3.verdict == "inconclusive" and p_e3.blocks, f"{p_e3.verdict}")
    ck("E3b inconclusive 的理由必须点明『发展集没有该难度层』（可据以补数据）",
       "没有" in p_e3.reason and "难度层" in p_e3.reason, p_e3.reason[:60])

    # E4 反证：把对照判据反过来（发展集涨 ⇒ 当作超纲）⇒ E1/E2 必须翻转
    g_e4, b4, c4, pr4 = mk_diff_gate(base_map, cand_map, DIFF)
    _orig = g_e4.probe

    def _inverted_probe(base_h, cand_h, runner, dev_gain,
                        dev_by_difficulty=None):
        flipped = {k: (-v if v > 0 else 1.0)
                   for k, v in (dev_by_difficulty or {}).items()}
        return _orig(base_h, cand_h, runner, dev_gain,
                     dev_by_difficulty=flipped)

    p_e4a = _inverted_probe(b4, c4, pr4, 0.6,
                            {"easy": 1.0, "hard": 0.0})
    p_e4b = _inverted_probe(b4, c4, pr4, 1.0,
                            {"easy": 1.0, "hard": 1.0})
    ck("F5 反证：对照判据反转 ⇒ E1/E2 裁决互换（说明 E 组不是巧合）",
       p_e4a.verdict == "partial" and p_e4b.verdict == "generalize",
       f"E1→{p_e4a.verdict} E2→{p_e4b.verdict}")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
