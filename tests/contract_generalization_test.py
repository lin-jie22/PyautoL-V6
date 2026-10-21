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

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
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
                                       PASS_VERDICTS, ProbeResult, VERDICTS,
                                       make_heldout)
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

    def mk_loop(tmp, caps, heldout, ledger=None, **cfg):
        led = ledger or Ledger(Path(tmp) / "ledger.jsonl")
        frozen = [FrozenTask(task_id="frozen::A", spec={"env": DEV, "frozen": True}),
                  FrozenTask(task_id="frozen::B", spec={"env": HO, "frozen": True})]
        cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}
        base = Baseline(caliber=cal,
                        verdicts={t.task_id: _runner(Harness(), t.spec)
                                  for t in frozen},
                        harness_id="seed")
        #: ⛔ 本契约只测**泛化轴**，显式关掉清理轴（与 contract_evolution_test
        #:    的 D 组同纪律）：清理提案器是常设的、不看 weakness，开着它会在
        #:    C 组混入"减负候选" ⇒ 泛化闸的裁决对象不再单一。
        #:    清理走泛化闸的**退化面**（`probe_removal`），由
        #:    contract_cleanup_test 的 E 组单独覆盖。
        return EvolutionLoop(led, NonRegressionGate(frozen, base), dev_tasks(),
                             _runner, FixedProposer(caps), caliber=cal,
                             heldout=heldout,
                             config=EvolutionConfig(group_size=3,
                                                    cleanup_enabled=False,
                                                    **cfg))

    def run_once(tmp, caps, heldout, ledger=None, **cfg):
        loop = mk_loop(tmp, caps, heldout, ledger, **cfg)
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
    #    人工确认记录：
    #      2026-10-13 `partial`       —— 总体涨但只在部分难度层涨
    #                                   （典型：只会修 easy 题）。
    #                                   **拦截**，不放行。
    #                                   见 contract_difficulty_test。
    #      2026-10-16 `removal_safe`  —— 清理候选：held-out **不许变差**
    #                                   （不要求涨）。**放行**。
    #      2026-10-16 `removal_regress` —— 清理候选：held-out 真的掉了
    #                                   ⇒ 删错了。**拦截**。
    #      ⛔ 后两个的由来（别当成"给清理开后门"）：泛化闸问的是
    #         "发展集上涨的那部分是真的吗"，而清理**根本不涨** ⇒ 走
    #         `probe()` 必得 `no_gain` ⇒ 清理在结构上永远提交不了。
    #         那不是闸太严，是**用错了闸**：对清理该问"删完变差没有"。
    #         见 aether/generalization.py 的 `probe_removal` 与
    #         contract_cleanup_test 的 E 组。
    #: ⛔ `scoped` 是**人工确认**过的（2026-10-21，用户拍板放宽定点修法）：
    #:    理由见 aether/generalization.py `capsule_scope` —— `patch:<env>`
    #:    在别的环境上 0 增益是**构造必然**，拿它判 overfit 会把定点修法
    #:    永久封死（实测 dev 0/5→5/5 仍被拦、连续 6 轮 committed=null）。
    #:    ⛔ 它只在 CLI 显式开 `--allow-scoped-commit` 时才产生，且**不算**
    #:    generalize：账本里 `scoped` 与 `generalize` 必须能分开数。
    ck("A3 裁决取值是封闭集合（新增取值须人工确认，"
       "已确认 partial / removal_safe / removal_regress / scoped）",
       set(VERDICTS) == {"generalize", "overfit", "partial", "no_gain",
                         "inconclusive", "removal_safe", "removal_regress",
                         "scoped"},
       f"{VERDICTS}")
    ck("A3b partial 是**拦截**态（新增取值不得偷偷放行）",
       ProbeResult(verdict="partial").blocks is True)
    #: ⛔ A4 同样刻意写死：它守的是**放行集合**，与 A3 的"取名集合"是两件事。
    #:    若改成从 `PASS_VERDICTS` 派生 ⇒ 有人往 `PASS_VERDICTS` 里加一项，
    #:    这条闸会跟着变绿 ⇒ 放行权被悄悄扩大而无人确认。
    #:    ⇒ 这里从 `ProbeResult.blocks` 的**实测行为**反推出放行集合，
    #:      再与写死的期望比对（A4b 才去核对源码常量）。
    _pass_observed = {v for v in VERDICTS if not ProbeResult(verdict=v).blocks}
    ck("A4 ⛔ blocks 实测：放行集合恰好 = generalize + removal_safe + scoped"
       "（多一个放行态 ⇒ 此处转红，须人工确认）",
       _pass_observed == {"generalize", "removal_safe", "scoped"},
       f"{_pass_observed}")
    ck("A4e ⛔ `scoped` 不许被当成 `generalize`（放行 ≠ 证明泛化）",
       ProbeResult(verdict="scoped").verdict != "generalize"
       and ProbeResult(verdict="overfit").blocks is True)
    ck("A4b 实测放行集合与源码 PASS_VERDICTS 一致"
       "（改了源码忘改这里 / 反之 ⇒ 转红，两处不许漂移）",
       _pass_observed == set(PASS_VERDICTS),
       f"实测 {_pass_observed} vs 源码 {set(PASS_VERDICTS)}")
    ck("A4c 集合外的未知裁决一律拦（fail-closed）",
       ProbeResult(verdict="__根本不存在的裁决__").blocks is True)
    ck("A4d removal_regress 是**拦截**态（清理删错了必须拦下）",
       ProbeResult(verdict="removal_regress").blocks is True)
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

    def mk_diff_gate(base_map, cand_map, difficulty, **kw):
        """按 (task_id → reward) 精确控制 base/cand 在 held-out 的表现。

        `**kw` 透传给 `GeneralizationGate`（G 组要用它改 `min_level_headroom`）。
        """
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
            dev_task_ids=["dev0"], base_at_ceiling=None, **kw)
        base_h = Harness(meta_rules="BASE")
        cand_h = Harness(meta_rules="CAND")
        return g, base_h, cand_h, _probe_runner

    #: ⛔ hard 层**刻意放 3 条**（r52 前是 2 条）。`min_level_headroom=3`
    #:    生效后，层内可提升空间不足 3 条会被判「不可判别」⇒ E2 这种
    #:    「必须判挑食」的场景会被它抢先（实测 E2 当场转 inconclusive）。
    #:    ⇒ 改判据后对照组必须重审：这里把层扩到样本足够的规模，让 E2
    #:      考的还是它原本要考的那件事（挑食），而不是"样本恰好只有 2 条"。
    #:      「样本不足确实不判挑食」由 G1 专门守，「样本足够仍判挑食」
    #:      由 G2 专门守 —— 两件事分开，不再挤在同一条里。
    DIFF = {"h_easy": "easy", **{f"h_hard{i}": "hard" for i in range(1, 4)}}
    # E1：通用修法 —— held-out easy 涨、hard 不涨；发展集 hard **同样**不涨
    #     ⇒ 那是能力边界，不是挑食 ⇒ 必须放行（否则进化永久空转）
    base_map = {"h_easy": 0.0, **{f"h_hard{i}": 0.0 for i in range(1, 4)}}
    cand_map = {"h_easy": 1.0, **{f"h_hard{i}": 0.0 for i in range(1, 4)}}
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

    # ── H 组：被泛化闸拦下 ≠ 这一轮结束（r52）──────────────────────
    #
    # ⛔ 背景（r51 的 depth2+均衡口径实测）：`best` 被闸拦 ⇒ 整轮作废、
    #    回滚、下一轮**再提同一个**（基线没变 ⇒ 发展集排序不变 ⇒ best 恒定）
    #    ⇒ 6 轮里 4 轮提同一个 `fix_control_flow`、每次同一句 partial。
    #    ⛔ 修法不是放松闸，而是"这个不行 ⇒ 试下一个（按**发展集**排序的
    #    下一个）" ⇒ H1（held-out 不参与选择）一条没动。
    print("\nH 组：被拦 ⇒ 同轮试下一个（闸一条没少）")

    with tempfile.TemporaryDirectory() as tmp:
        #: 两个候选在**发展集**上一样好（都是 1.0 ⇒ 都过 advantage）：
        #:   `narrow:A` = 只修发展环境 ⇒ 过不了 held-out（overfit）
        #:   `gen`      = 两个环境都修 ⇒ 真泛化
        #: ⇒ 按发展集排序两者并列，遍历序在前的是 narrow ⇒ gate_tries=1 时
        #:   整轮作废；=2 时同轮轮到 gen ⇒ 提交。
        caps_h = [capsule("narrow", DEV), capsule("gen")]
        loop_h1, rep_h1 = run_once(tmp, caps_h, mk_gate())
        ck("H1 默认口径（gate_tries=1）：best 被拦 ⇒ 不提交（与历史一致）",
           rep_h1.committed is None, f"{rep_h1.committed}")

        loop_h2, rep_h2 = run_once(tmp, caps_h, mk_gate(), gate_tries=2)
        ck("H2 gate_tries=2：同轮试下一个 ⇒ **提交**（被拦不再等于整轮作废）",
           rep_h2.committed is not None, f"{rep_h2.revert_reason[:80]}")
        acc = [c for c in rep_h2.candidates if c.accepted]
        ck("H3 提交的必须是 `gen`（真泛化），不是 `narrow`（讨好发展集）",
           len(acc) == 1 and acc[0].capsule_name == "gen",
           f"{[c.capsule_name for c in acc]}")
        ck("H4 ⛔ H1 纪律不破：队列顺序来自**发展集**，不是 held-out"
           "（两个候选 dev 并列 ⇒ 序遍历，闸只做准入）",
           [c.capsule_name for c in rep_h2.candidates[:2]] == ["narrow", "gen"],
           f"{[c.capsule_name for c in rep_h2.candidates]}")
        #: ⛔ 死路账此前只记 best ⇒ 其它候选**永远**进不了账 ⇒ 冷却几乎
        #:    从不触发。这里钉住"每个闯过闸的候选都记一条"。
        n_probe = sum(1 for l in open(Path(tmp) / "ledger.jsonl",
                                      encoding="utf-8") if '"gen.probe"' in l)
        ck("H5 每个闯过闸的候选都记 `gen.probe`（死路账覆盖非 best）",
           n_probe >= 2, f"gen.probe 只记了 {n_probe} 条")
        ck("H6 `gate_probes` 读数出得来（「试了几个」可观测，不是靠猜）",
           len(rep_h2.gate_probes) >= 2, f"{rep_h2.gate_probes}")

        #: H7 反证：把闸换成恒放行 ⇒ gate_tries=1 也会提交（H1 不是恒不提交）
        class _AlwaysPass:
            def probe(self, *a, **k):
                return ProbeResult(verdict="generalize", dev_gain=1.0,
                                   heldout_gain=1.0, n_tasks=3)
        _orig_probe = GeneralizationGate.probe
        GeneralizationGate.probe = lambda self, *a, **k: ProbeResult(
            verdict="generalize", dev_gain=1.0, heldout_gain=1.0, n_tasks=3)
        try:
            _, rep_h7 = run_once(tmp, caps_h, mk_gate())
            ck("H7 反证：闸恒放行 ⇒ gate_tries=1 也提交（H1 不是恒不提交）",
               rep_h7.committed is not None, f"{rep_h7.revert_reason[:60]}")
        finally:
            GeneralizationGate.probe = _orig_probe

        #: H8 默认值本身（第 12 条：默认开的开关不许只靠"不传"来验证）
        ck("H8 ⛔ `EvolutionConfig()` 的 gate_tries 默认是 1（默认=现状）",
           EvolutionConfig().gate_tries == 1,
           str(EvolutionConfig().gate_tries))

    # ── G 组：分层裁决的**样本量**（r52：0/1 与 4/9 在统计上同形）────
    #
    # ⛔ 背景（r51 的 depth2+均衡口径实测）：轮 2 起 held-out 的 hard 层
    #    **只剩 1 条**未解题（7 条里 6 条已被上一代修好），而发展集同层还
    #    剩 9 条 ⇒ 候选在 dev 修好 4/9（+0.138）、在 ho 修好 0/1（+0.000）
    #    ⇒ 闸判「发展集同层涨了 ⇒ 挑食」，**连续 4 轮同一个结论**。
    #    分母是 1 的时候，"没涨"根本不是证据 —— 那是不可判别，不是有罪。
    print("\nG 组：层内可提升空间 —— 样本不足 ⇒ 不可判别，不是挑食")

    #: hard 层 6 条，其中 5 条基线**已经**修好 ⇒ 可提升空间只有 1 条
    DIFF_G = {f"g_hard{i}": "hard" for i in range(6)}
    DIFF_G["g_easy"] = "easy"
    base_g = {"g_easy": 0.0, **{f"g_hard{i}": 1.0 for i in range(5)},
              "g_hard5": 0.0}
    cand_g = {"g_easy": 1.0, **{f"g_hard{i}": 1.0 for i in range(5)},
              "g_hard5": 0.0}
    g_g1, b_g1, c_g1, pr_g1 = mk_diff_gate(base_g, cand_g, DIFF_G)
    p_g1 = g_g1.probe(b_g1, c_g1, pr_g1, dev_gain=0.3,
                      dev_by_difficulty={"easy": 1.0, "hard": 0.1379})
    ck("G1 层内只剩 1 条可提升 ⇒ 判 inconclusive（不是 partial）",
       p_g1.verdict == "inconclusive", f"{p_g1.verdict} / {p_g1.reason[:70]}")
    ck("G1b 仍然**拦截**（不可判别 ≠ 放行）", p_g1.blocks, p_g1.reason[:70])
    ck("G1c 理由必须点明『可提升空间』与出路（补题，不是放宽判据）",
       "可提升空间" in p_g1.reason and "补" in p_g1.reason,
       p_g1.reason[:90])

    #: G2 反证（闸要有牙）：同一形态，但可提升空间 = 3 ⇒ 必须回到挑食
    base_g2 = {"g_easy": 0.0, **{f"g_hard{i}": 0.0 for i in range(3)},
               **{f"g_hard{i}": 1.0 for i in range(3, 6)}}
    cand_g2 = dict(base_g2)
    cand_g2["g_easy"] = 1.0
    g_g2, b_g2, c_g2, pr_g2 = mk_diff_gate(base_g2, cand_g2, DIFF_G)
    p_g2 = g_g2.probe(b_g2, c_g2, pr_g2, dev_gain=0.3,
                      dev_by_difficulty={"easy": 1.0, "hard": 0.1379})
    ck("G2 反证：可提升空间 = 3 ⇒ **仍然**判 partial（说明 G1 不是放宽）",
       p_g2.verdict == "partial" and p_g2.blocks,
       f"{p_g2.verdict} / {p_g2.reason[:70]}")

    #: G3：门槛调到 0 ⇒ 回到改动前的行为（判据可回退，不是写死）
    g_g3, b_g3, c_g3, pr_g3 = mk_diff_gate(base_g, cand_g, DIFF_G,
                                           min_level_headroom=0)
    p_g3 = g_g3.probe(b_g3, c_g3, pr_g3, dev_gain=0.3,
                      dev_by_difficulty={"easy": 1.0, "hard": 0.1379})
    ck("G3 `min_level_headroom=0` ⇒ 回到旧口径（partial）⇒ 判据可回退",
       p_g3.verdict == "partial", f"{p_g3.verdict}")
    #: G5：**r51 轮 2 的真实形态** —— easy 层样本足够且涨了，hard 层只剩
    #:    1 条可提升且没涨 ⇒ hard 不作反证 ⇒ 放行，但**必须点名**（否则
    #:    "样本不足就跳过"会变成悄悄放宽判据的一条暗门）。
    DIFF_G5 = {"g5_easy1": "easy", "g5_easy2": "easy", "g5_easy3": "easy",
               **{f"g5_hard{i}": "hard" for i in range(6)}}
    base_g5 = {**{f"g5_easy{i}": 0.0 for i in range(1, 4)},
               **{f"g5_hard{i}": 1.0 for i in range(5)}, "g5_hard5": 0.0}
    cand_g5 = {**{f"g5_easy{i}": 1.0 for i in range(1, 4)},
               **{f"g5_hard{i}": 1.0 for i in range(5)}, "g5_hard5": 0.0}
    g_g5, b_g5, c_g5, pr_g5 = mk_diff_gate(base_g5, cand_g5, DIFF_G5)
    p_g5 = g_g5.probe(b_g5, c_g5, pr_g5, dev_gain=0.22,
                      dev_by_difficulty={"easy": 1.0, "hard": 0.1379})
    ck("G5 部分层样本不足（另有层样本足够且涨了）⇒ 放行（不作反证）",
       p_g5.verdict == "generalize", f"{p_g5.verdict} / {p_g5.reason[:70]}")
    ck("G5b ⛔ 放行的同时**必须点名**样本不足的层（不许悄悄跳过）",
       "样本不足" in p_g5.reason and "可提升" in p_g5.reason,
       p_g5.reason[:110])
    ck("G5c 样本不足的层**不许**混进 out_of_reach（那是能力边界，两回事）",
       "hard" not in p_g5.out_of_reach, f"{p_g5.out_of_reach}")

    ck("G4 ⛔ 默认值本身：默认门槛是 3（不传参时真的生效）",
       GeneralizationGate(make_heldout(HO, [FrozenTask(
           task_id="z", spec={"env": HO})]), dev_task_ids=["dev0"]
       ).min_level_headroom == 3,
       str(GeneralizationGate(make_heldout(HO, [FrozenTask(
           task_id="z", spec={"env": HO})]),
           dev_task_ids=["dev0"]).min_level_headroom))

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
