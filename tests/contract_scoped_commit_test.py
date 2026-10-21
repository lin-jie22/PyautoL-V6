#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：定点修法（`patch:<env>`）该不该被 held-out 判 overfit（r62）。

═══ 守什么 ═══

  A `capsule_scope` 的判定：只碰定点技能 ⇒ env；沾任何通用层 ⇒ generic
  B 默认口径不变：env 专属胶囊在 held-out 上 0 增益 ⇒ **仍判 overfit**
  C 开 `allow_scoped_commit`：同一颗胶囊 ⇒ 判 `scoped` 且**真的提交**
  D 反证①：通用胶囊（沾路由）开闸后**仍**判 overfit（放宽只对 env 范围生效）
  E 反证②：env 胶囊但把 held-out 那个环境也点上了 ⇒ 仍判 overfit
  F 账本可分辨：`scoped` 不冒充 `generalize`，且带 `scope` 字段
  G 反证④（r63）：定点胶囊却把 held-out **拉低** ⇒ 必须拦
    （`scoped` 只豁免"0 增益"，不豁免"掉分"—— 负数证伪了豁免的前提）

⛔ 为什么必须开这道口（不是"把闸关小"）：
   `patch:<env>` **只对点名的环境生效** ⇒ 它在别的环境上 0 增益是**构造
   必然**，不是"讨好判据"的证据。旧判据拿这个 0 判 overfit（实测 10-20：
   dev 0/5→5/5、held-out 0.0→0.0 ⇒ overfit ⇒ 连续 6 轮 `committed=null`）。
   ⛔ 但它**只**适用于"只碰定点技能、且没碰 held-out 那个环境"的候选：
     沾了路由 / 工具 / 通用技能 / 记忆策略的候选，那些层对 held-out 是有
     影响的 ⇒ 0 增益就真的是没泛化（D、E 两组就是这两条边界）。
   ⛔ 默认仍是旧口径（`allow_scoped_commit=False`）⇒ 历史基线可比。
"""
import json
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _ok(tid):
    from aether.verifier import Check, Verdict
    return Verdict(passed=True, checks=(Check(tid, True, "got=1 expect=1"),),
                   evidence={"env": tid})


def _bad(tid):
    from aether.verifier import Check, Verdict
    return Verdict(passed=False, checks=(Check(tid, False, "got=0 expect=1"),),
                   evidence={"env": tid})


def _runner(h, spec):
    """env A = 发展环境；env B = held-out。

    认的技能名（与生产同形：定点修法就叫 `patch:<env>`）：
      `gen`        ⇒ 两个环境都修好
      `fix_x`      ⇒ **不被认**（通用技能但没真修好 —— D 组要的"通用但无效"）
      `patch:<env>`⇒ 只修点名的那个环境
    """
    env = spec.get("env", "")
    if spec.get("frozen"):
        return _ok("frozen")
    if "gen" in h.skills or f"patch:{env}" in h.skills:
        return _ok(env)
    return _bad(env)


def main():
    from aether.evolution import EvolutionConfig
    from aether.generalization import (GeneralizationGate, capsule_scope,
                                       make_heldout, PASS_VERDICTS, VERDICTS)
    from aether.genome import Capsule, Gene, Harness

    DEV, HO = "A", "B"

    def cap(genes, name="c"):
        return Capsule(name=name, genes=tuple(genes))

    def g_skill(key, name="k"):
        return Gene(op="set", target=f"L3.skills.{key}", value="修法",
                    rationale="测试用")

    # ── A 组：能力范围的判定 ────────────────────────────────────
    print("A 组：capsule_scope 判定")
    s_env = capsule_scope(cap([g_skill("patch:A")]))
    ck("A1 只碰 `L3.skills.patch:A` ⇒ kind=env, envs=[A]",
       s_env.get("kind") == "env" and s_env.get("envs") == [DEV], str(s_env))
    s_gen = capsule_scope(cap([g_skill("fix_control_flow")]))
    ck("A2 只碰通用技能 ⇒ kind=generic", s_gen.get("kind") == "generic",
       str(s_gen))
    #: ⛔ A3 的语义在 r62 被修正过一次：**层名不是能力**。路由指向一个
    #:    **通用**技能 ⇒ 那条路由在别的环境上也真能调起东西 ⇒ generic。
    s_mix = capsule_scope(cap([g_skill("patch:A"),
                               Gene(op="append", target="L1.routing.repair",
                                    value="fix_control_flow", rationale="x")]))
    ck("A3 ⛔ 路由指向**通用**技能 ⇒ generic（哪怕同时也改了定点技能）",
       s_mix.get("kind") == "generic", str(s_mix))
    #: ⛔ 但路由 / 工具表里写的若是**定点技能名**，它到别的环境上调不动
    #:    任何东西 ⇒ 那两处不承载通用能力 ⇒ 仍是 env（且要能复核）。
    s_neu = capsule_scope(cap([
        g_skill("patch:A"),
        Gene(op="set", target="L1.routing.repair", value="patch:A",
             rationale="为了让新技能可达"),
        Gene(op="append", target="L2.tools", value="patch:A", rationale="x")]))
    ck("A6 路由/工具表都只挂定点技能名 ⇒ 仍判 env（这就是放宽的那一步）",
       s_neu.get("kind") == "env" and s_neu.get("envs") == [DEV], str(s_neu))
    ck("A6b ⛔ 哪些目标被当成「中性」必须留痕（放行要可复核）",
       len(s_neu.get("neutral_targets") or []) == 2, str(s_neu.get("neutral_targets")))
    s_tool = capsule_scope(cap([
        g_skill("patch:A"),
        Gene(op="append", target="L2.tools", value="read_file", rationale="x")]))
    ck("A7 ⛔ 工具表里出现真工具名 ⇒ generic（放宽只到定点名字为止）",
       s_tool.get("kind") == "generic", str(s_tool))
    s_two = capsule_scope(cap([g_skill("patch:A"), g_skill("patch:B")]))
    ck("A4 同时点两个环境 ⇒ env, envs=[A,B]",
       s_two.get("kind") == "env" and s_two.get("envs") == [DEV, HO], str(s_two))
    ck("A5 死名 `patch::A`（双冒号）**不算**定点技能 ⇒ generic",
       capsule_scope(cap([g_skill("patch::A")])).get("kind") == "generic")

    # ── 端到端夹具：内存 runner + 真闸 + 真循环 ──────────────────
    from aether.evolution import EvolutionLoop
    from aether.ledger import Ledger
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate

    def dev_tasks(n=3):
        return [FrozenTask(task_id=f"dev{i}", spec={"env": DEV})
                for i in range(n)]

    def ho_tasks(n=3):
        return [FrozenTask(task_id=f"ho{i}", spec={"env": HO})
                for i in range(n)]

    class FixedProposer:
        def __init__(self, caps):
            self.caps = caps

        def propose(self, weakness, harness, n=3):
            return list(self.caps)

    def run_once(caps, **cfg):
        td = tempfile.mkdtemp(prefix="v6scoped_")
        led = Ledger(Path(td) / "ledger.jsonl")
        frozen = [FrozenTask(task_id="frozen::A", spec={"env": DEV,
                                                        "frozen": True}),
                  FrozenTask(task_id="frozen::B", spec={"env": HO,
                                                        "frozen": True})]
        cal = {"seed": 0, "timeout": 10.0, "split": "frozen"}
        base = Baseline(caliber=cal,
                        verdicts={t.task_id: _runner(Harness(), t.spec)
                                  for t in frozen},
                        harness_id="seed")
        loop = EvolutionLoop(
            led, NonRegressionGate(frozen, base), dev_tasks(), _runner,
            FixedProposer(caps), caliber=cal,
            #: ⛔ 传**闸**不是传 suite：循环要读 `heldout.env_pair`，
            #:    而 suite 上没有这个字段（contract_generalization 同口径）。
            heldout=GeneralizationGate(
                make_heldout(HO, ho_tasks()),
                dev_task_ids=[t.task_id for t in dev_tasks()],
                base_at_ceiling=1.0),
            config=EvolutionConfig(group_size=1, cleanup_enabled=False, **cfg))
        rep = loop.run_round(Harness(), round_no=1)
        return rep, led

    def verdict_of(rep):
        cands = rep.candidates or []
        return (cands[0].generalization or {}).get("verdict") if cands else None

    print("\nB 组：默认口径（历史）—— env 专属胶囊仍被判 overfit")
    rep_b, _ = run_once([cap([g_skill("patch:A")])])
    ck("B1 默认口径判 overfit", verdict_of(rep_b) == "overfit",
       str(verdict_of(rep_b)))
    ck("B2 默认口径不提交", rep_b.committed is None, str(rep_b.committed))
    ck("B3 默认配置是 False（历史口径）",
       EvolutionConfig().allow_scoped_commit is False)

    print("\nC 组：开 allow_scoped_commit —— 同一颗胶囊判 scoped 并真的提交")
    rep_c, led_c = run_once([cap([g_skill("patch:A")])],
                            allow_scoped_commit=True)
    ck("C1 判 scoped（不是 overfit）", verdict_of(rep_c) == "scoped",
       str(verdict_of(rep_c)))
    ck("C2 真的提交了（committed 非空）", rep_c.committed is not None,
       str(rep_c.committed))
    ck("C3 `scoped` 在放行集合里，但**不在** VERDICTS 里冒充 generalize",
       "scoped" in PASS_VERDICTS and "scoped" in VERDICTS
       and "scoped" != "generalize")
    rows = [json.loads(x) for x in
            (Path(led_c.path).read_text(encoding="utf-8").splitlines())
            if x.strip()]
    probes = [r["payload"] for r in rows if r["kind"] == "gen.probe"]
    ck("C4 账本记了 scoped + scope 字段（可复核「凭什么放行」）",
       bool(probes) and probes[0].get("verdict") == "scoped"
       and (probes[0].get("scope") or {}).get("kind") == "env",
       str(probes[:1])[:200])
    ck("C5 ⛔ 不许记成 generalize（否则「泛化了几次」被灌水）",
       probes[0].get("verdict") != "generalize")

    print("\nD 组：反证① —— 通用胶囊开闸后**仍**判 overfit")
    #: 这条胶囊在发展集上**真涨**（改了 `patch:A`），但它同时动了路由 ⇒
    #: 能力范围是 generic ⇒ 路由对 held-out 是有影响的 ⇒ 0 增益就是真没泛化
    #: ⇒ 不许豁免。⛔ 若这里也放行，等于把闸整个关掉。
    #: ⛔ 关键差别：路由指向的是**通用**技能名（`fix_control_flow`）而不是
    #:    `patch:A` —— 那条路由在别的环境上也可能调起通用修法 ⇒ 承载通用能力。
    rep_d, _ = run_once([cap([g_skill("patch:A"),
                              Gene(op="append", target="L1.routing.repair",
                                   value="fix_control_flow", rationale="x")])],
                        allow_scoped_commit=True)
    ck("D1 ⛔ 通用范围（沾路由）⇒ 即使发展集涨满、held-out 0 ⇒ 仍 overfit",
       verdict_of(rep_d) == "overfit", str(verdict_of(rep_d)))
    ck("D2 且不提交", rep_d.committed is None, str(rep_d.committed))

    print("\nE 组：反证② —— 能力范围点到了 held-out 那个环境 ⇒ 仍 overfit")
    #: ⛔ 这一组直接调闸（不走整轮）：规则是「范围里含 held-out ⇒ 不给豁免」，
    #:    而"效果"取决于 harness。让 harness 只真修了 A，胶囊元数据却说它
    #:    也碰了 B ⇒ 隔离出**规则本身**（否则 B 也被修好 ⇒ held-out 真涨 ⇒
    #:    那是 generalize，测不到这条边界）。
    from aether.genome import apply_capsule as _apply
    _g = GeneralizationGate(make_heldout(HO, ho_tasks()),
                            dev_task_ids=[t.task_id for t in dev_tasks()],
                            base_at_ceiling=1.0)
    _h0 = Harness()
    _h1 = _apply(_h0, cap([g_skill("patch:A")]))
    _pr = _g.probe(_h0, _h1, _runner, dev_gain=1.0,
                   capsule=cap([g_skill("patch:A"), g_skill("patch:B")]),
                   allow_scoped=True)
    ck("E1 ⛔ scope.envs 含 held-out ⇒ 不再是「构造必然」⇒ 仍判 overfit",
       _pr.verdict == "overfit", f"{_pr.verdict} {_pr.reason[:80]}")
    ck("E2 裁决里带 scope（读得出它为什么没被豁免）",
       (_pr.scope or {}).get("envs") == [DEV, HO], str(_pr.scope))
    _pr2 = _g.probe(_h0, _h1, _runner, dev_gain=1.0,
                    capsule=cap([g_skill("patch:A")]), allow_scoped=True)
    ck("E3 反证：同一 harness、范围只写 A ⇒ 判 scoped（差别只在 scope）",
       _pr2.verdict == "scoped", f"{_pr2.verdict} {_pr2.reason[:80]}")

    print("\nF 组：反证③ —— 关掉开关，C 组那颗胶囊必须退回 overfit")
    rep_f, _ = run_once([cap([g_skill("patch:A")])], allow_scoped_commit=False)
    ck("F1 ⛔ 开关真的承重（关 ⇒ overfit，开 ⇒ scoped）",
       verdict_of(rep_f) == "overfit" and verdict_of(rep_c) == "scoped",
       f"{verdict_of(rep_f)} vs {verdict_of(rep_c)}")

    print("\nG 组：反证④ —— 定点胶囊却把 held-out 拉低 ⇒ 必须拦（r63）")
    #: ⛔ `scoped` 的成立理由只有**一句**：「held-out 上 0 增益是构造必然」。
    #:    **负数不是必然** —— 它恰恰是"陪衬真的碰到了别的环境"的实据
    #:    （典型：`op="set"` 的路由把 held-out 原本依赖的通用修法**顶掉**，
    #:    链上只剩一个在 B 里因 env 门控调不动的定点技能）。
    #:    拿负数一起当"没资格判" = 放行一次**真回归**。
    #: ⛔ 与 r63 实跑的区别：提交的 `258a11ba` 走 `append`（链尾追加，旧的仍在）
    #:    ⇒ ho_gain=0.0 没踩到；踩到需要 `set` 语义 + held-out 原本有可用兜底。
    from aether.genome import parse_route as _parse_route

    def _runner_chain(h, spec):
        """链语义（与生产同形）：`patch:<env>` 只对点名环境可用，通用技能到处可用。"""
        env = spec.get("env", "")
        if spec.get("frozen"):
            return _ok("frozen")
        usable = [s for s in _parse_route(h.routing.get("repair"))
                  if s in h.skills
                  and (not str(s).startswith("patch:") or s == f"patch:{env}")]
        if env == DEV:
            return _ok(env) if f"patch:{DEV}" in usable else _bad(env)
        if not any(not str(s).startswith("patch:") for s in usable):
            return _bad(env)
        #: ⛔ **不许让基线贴天花板**：基线 3/3 会先撞 `base_at_ceiling` 而判
        #:    `inconclusive`（那是"无从判别"），根本走不到 scoped 分支 ⇒
        #:    夹具会"通过得莫名其妙"。留一条修不好的 ⇒ 基线 2/3。
        return _bad(env) if spec.get("idx") == 2 else _ok(env)

    h_seed = Harness(routing={"repair": "gen_fix"},
                     skills={"gen_fix": "通用兜底修法"})
    cap_reg = cap([g_skill(f"patch:{DEV}"),
                   Gene(op="set", target="L1.routing.repair",
                        value=f"patch:{DEV}", rationale="为了让新技能可达")])
    h_reg = _apply(h_seed, cap_reg)
    _ho3 = [FrozenTask(task_id=f"ho{i}", spec={"env": HO, "idx": i})
            for i in range(3)]
    _gg = GeneralizationGate(make_heldout(HO, _ho3),
                             dev_task_ids=[t.task_id for t in dev_tasks()],
                             base_at_ceiling=1.0)
    pr_g = _gg.probe(h_seed, h_reg, _runner_chain, dev_gain=1.0,
                     capsule=cap_reg, allow_scoped=True)
    ck("G1 夹具自证：发展集真涨、held-out 真掉、且基线未贴天花板",
       pr_g.dev_gain > 0 and pr_g.heldout_gain < 0
       and pr_g.base_heldout < 1.0,
       f"dev={pr_g.dev_gain:+.3f} ho={pr_g.heldout_gain:+.3f} "
       f"base={pr_g.base_heldout:.3f}")
    ck("G2 ⛔ 负数**不是**构造必然 ⇒ 必须拦（放行它 = 放行一次真回归）",
       pr_g.blocks and pr_g.verdict != "scoped",
       f"{pr_g.verdict} blocks={pr_g.blocks} ho={pr_g.heldout_gain:+.3f}")
    ck("G3 拦也要留痕：scope / neutral_targets 照记（读得出它靠什么被豁免过）",
       (pr_g.scope or {}).get("kind") == "env"
       and bool((pr_g.scope or {}).get("neutral_targets")), str(pr_g.scope))

    print("\n" + "=" * 62)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：定点修法能提交，通用候选仍要真跨环境涨，开关可分辨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
