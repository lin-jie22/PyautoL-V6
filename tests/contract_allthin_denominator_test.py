#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：`all_thin` 的**分母** —— 一个看起来像 bug、实际是正确设计的地方。

## 为什么需要这份契约

r55 在 README 里记了一条"诚实未做"：

    `all_thin` 的分母仍含结构性饱和层（easy 永远 0）⇒ 待单独一轮。

读起来它像 bug：一个永远补不回来的层，凭什么占着"没有判别力"的分母？
⇒ 下一轮几乎必然有人去"修"它。**而那个修法是错的**（见 N2/N3 的反证）。

本文件守的就是：**这个判据不许被"看起来更合理"的改法替换掉**。
它是负结果契约 —— 交付的是"不许改哪里、为什么"，不是"改了什么"。

## 分母的三口径（本文件把它们分开标定）

    V0  现行   分母 = by_diff（**全部层**，含涨了的层）
    V1          分母 = lagging_all（只未涨层）
    V2          分母 = by_diff − saturated（全层剔除结构性饱和）

⛔ V0 ≡ NOT any(headroom[lv] ≥ min_level_headroom)
   ⇒ 语义是「是否存在**任何一层**能提供足够判别力」，正负皆可。
   ⇒ 涨层空间够 = 强正证 = 有判别力 ⇒ 不该判 all_thin（N2 钉住）。
   ⇒ 饱和层 headroom=0 永远不可能让 any(...) 成真 ⇒ 它**不可能**造成
     假放行，也**不可能**单独造成假拦截（N3 钉住，V0 ≡ V2）。

## 组

  N1 分母语义（源码级 + 数学恒等）
  N2 ⛔ 反证：改成 V1（只算未涨层）会引入**假拦截**
  N3 ⛔ 反证：改成 V2 不会改变任何裁决（饱和层不是假红灯的原因）
  N4 出路分叉：饱和层不许给"补题"这种无效出路（第 35b 条）
  N5 判据有牙：门槛真在起作用（不是恒真/恒假断言）

跑法（纯内存 runner，秒级）：
    python tests/contract_allthin_denominator_test.py
"""
import sys
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
    return Verdict(passed=True, checks=(Check(str(tid), True, "got=1"),),
                   evidence={"env": str(tid)})


def _bad(tid):
    from aether.verifier import Check, Verdict
    return Verdict(passed=False, checks=(Check(str(tid), False, "got=0"),),
                   evidence={"env": str(tid)})


HO = "B"


def mk_gate(base_map, cand_map, difficulty, **kw):
    """按 (task_id → reward) 精确控制 base/cand 在 held-out 上的表现。

    ⛔ 与 `contract_generalization_test.mk_diff_gate` 同形（同源 fixture）：
       用 `meta_rules` 区分 base / cand，runner 只看 harness。
    """
    from aether.generalization import GeneralizationGate, make_heldout
    from aether.genome import Harness
    from aether.nongression import FrozenTask

    suite_tasks = [FrozenTask(task_id=t, spec={"tid": t})
                   for t in sorted(base_map)]

    def runner(h, spec):
        tid = str(spec.get("tid", ""))
        m = cand_map if "CAND" in (h.meta_rules or "") else base_map
        return _ok(tid) if m.get(tid, 0.0) > 0 else _bad(tid)

    g = GeneralizationGate(make_heldout(HO, suite_tasks, difficulty=difficulty),
                           dev_task_ids=["dev0"], base_at_ceiling=None, **kw)
    return g, Harness(meta_rules="BASE"), Harness(meta_rules="CAND"), runner


def levels(**kw):
    """`{层名: [task_id...]}` → difficulty 映射。"""
    diff = {}
    for lv, ids in kw.items():
        for t in ids:
            diff[t] = lv
    return diff


def main():
    from aether.generalization import GeneralizationGate  # noqa: F401

    gen = Path(ROOT / "aether" / "generalization.py").read_text(encoding="utf-8")

    print("== N1 组：分母语义（源码级 + 数学恒等）==")
    #: ⛔ 分母是 `by_diff`（全部层），**不是** `lagging_all`（只未涨层）。
    ck("N1.1 `all_thin` 的分母是 `by_diff`（含涨层，不只是未涨层）",
       "headroom.get(lv, 0) < self.min_level_headroom for lv in by_diff"
       in gen, "源码里找不到该分母 ⇒ 语义已被改掉")
    ck("N1.2 ⛔ 判定在**所有分支之前**（与 headroom/thin/saturated 同源）",
       gen.index("all_thin = bool(by_diff)") < gen.index(
           'return ProbeResult(\n                verdict="overfit"'),
       "all_thin 落到了分支之后 ⇒ 又会出现 r54 那种临界点不连续")
    ck("N1.3 `thin` 的分母是 `lagging_all`（只未涨层）—— 与 all_thin **不同**",
       "thin = sorted(lv for lv in lagging_all" in gen,
       "两处口径不同是本文件的主题；若被统一，N2 的论证需重审")

    print("\n== N2 组：⛔ 反证 —— 改成 V1（只算未涨层）会引入假拦截 ==")
    #: 造：easy 涨满且空间 6（≥3）= 强正证；hard 未涨且只有 2 条空间 = thin。
    #:    V0 = any(6≥3) ⇒ False ⇒ 不判 all_thin ⇒ 放行（并点名 hard）
    #:    若按 V1（只算未涨层）= all(2<3) = True ⇒ 判 all_thin ⇒ **拦截**
    #:    ⇒ V1 会把"有强正证、无有效反证"判成"没有证据" ⇒ 假拦截。
    EASY = [f"e{i}" for i in range(6)]
    HARD = [f"h{i}" for i in range(2)]
    DIFF = levels(easy=EASY, hard=HARD)
    base = {t: 0.0 for t in EASY + HARD}
    cand = {t: (1.0 if t in EASY else 0.0) for t in EASY + HARD}
    g, bh, ch, rn = mk_gate(base, cand, DIFF)
    p = g.probe(bh, ch, rn, dev_gain=0.5,
                dev_by_difficulty={"easy": 1.0, "hard": 0.0})
    ck("N2.1 涨层空间 6（≥3）⇒ 现行**放行**（V0=False，有强正证）",
       p.verdict == "generalize", f"{p.verdict} / {p.reason[:80]}")
    ck("N2.2 放行时仍点名未计入的 thin 层（不点名 = 悄悄放宽）",
       tuple(p.thin) == ("hard",) and "hard" in p.reason,
       f"thin={p.thin}")
    ck("N2.3 数学：本例 V1=True ≠ V0=False（两种口径此处**真的不同**）",
       all(p.headroom.get(lv, 0) < g.min_level_headroom
           for lv in ("hard",)) and p.headroom.get("easy", 0)
       >= g.min_level_headroom,
       f"headroom={p.headroom} mlh={g.min_level_headroom}")
    #: ⛔ 真反证：**跑生产代码**证明分母含涨层是有牙的 —— 把门槛抬到
    #:    涨层空间也不达标，裁决必须翻转（不是恒真断言）。
    g7, b7, c7, r7 = mk_gate(base, cand, DIFF, min_level_headroom=7)
    p7 = g7.probe(b7, c7, r7, dev_gain=0.5,
                  dev_by_difficulty={"easy": 1.0, "hard": 0.0})
    ck("N2.4 ⛔ 反证：门槛抬到 7 ⇒ 涨层也不达标 ⇒ 必须转为拦截",
       p7.verdict == "inconclusive" and p7.blocks,
       f"{p7.verdict} ⇒ 说明分母含涨层真的在起作用（有牙）")
    ck("N2.5 承上：N2.1 放行与 N2.4 拦截只差门槛 ⇒ 判据可回退、可验证",
       p.verdict != p7.verdict, "两者同判据 ⇒ N2.1 是恒真断言")

    print("\n== N3 组：⛔ 反证 —— 饱和层不是假红灯的原因（V0 ≡ V2）==")
    #: 复现 r56 轮 5 的真实读数：easy 结构性饱和（headroom=0，必然不涨），
    #: hard 6 条题里只有 2 条可涨、涨了 1 条。
    #:    V0 = all(0<3, 2<3) = True
    #:    V2 = all over (by_diff − sat) = all(2<3) = True  ⇒ 恒等
    #: ⛔ 我第一版量具把 V2 分母写成 `lagging_all − sat`（= []）⇒ 读出
    #:    False ⇒ 差点据此"修"掉判据（而方向是放宽 ⇒ 会放过不可判别的证据）。
    E3 = [f"s{i}" for i in range(3)]
    H6 = [f"k{i}" for i in range(6)]
    #: s* 基线全修好 ⇒ headroom 0；k0..k3 基线已修好，k4/k5 未修好
    #: ⇒ headroom[hard] = 2；cand 修好 k4 ⇒ 增益 1/6
    base2 = {**{t: 1.0 for t in E3},
             **{t: (1.0 if i < 4 else 0.0) for i, t in enumerate(H6)}}
    cand2 = {**{t: 1.0 for t in E3},
             **{t: (1.0 if i < 5 else 0.0) for i, t in enumerate(H6)}}
    g2, b2, c2, r2 = mk_gate(base2, cand2, levels(easy=E3, hard=H6))
    p2 = g2.probe(b2, c2, r2, dev_gain=0.1,
                  dev_by_difficulty={"easy": 0.0, "hard": 0.1})
    ck("N3.1 复现真实读数：easy 饱和（headroom 0）、hard 空间 2",
       p2.headroom.get("easy") == 0 and p2.headroom.get("hard") == 2,
       f"headroom={p2.headroom}")
    ck("N3.2 现行判 inconclusive（拦截）",
       p2.verdict == "inconclusive" and p2.blocks, f"{p2.verdict}")
    ck("N3.3 `saturated` 点名 easy（不是只活在文案里）",
       tuple(p2.saturated) == ("easy",), f"{p2.saturated}")
    #: ⛔ 核心断言：V2（正确分母 = by_diff − saturated）与 V0 **恒等**。
    v0 = all(p2.headroom.get(lv, 0) < g2.min_level_headroom
             for lv in p2.by_difficulty)
    v2 = all(p2.headroom.get(lv, 0) < g2.min_level_headroom
             for lv in p2.by_difficulty if lv not in set(p2.saturated))
    ck("N3.4 ⛔ V0 ≡ V2（剔除饱和层不改变判定）⇒ 饱和层不是假红灯原因",
       v0 == v2, f"V0={v0} V2={v2}")
    #: ⛔ 反证：错误分母（lagging_all − saturated）在本例为空 ⇒ False
    #:    ⇒ 谁按它改，这条断言就是他会遇到的事。这里断言**不许**那样算。
    lag = sorted(lv for lv in p2.by_difficulty
                 if p2.by_difficulty[lv] <= g2.heldout_floor)
    wrong_den = [lv for lv in lag if lv not in set(p2.saturated)]
    ck("N3.5 ⛔ 错误分母（lagging−sat）在本例为**空** ⇒ 它给出 False",
       wrong_den == [], f"{wrong_den}")
    ck("N3.6 ⛔ 承上：空分母**不许**被读成'有判别力'（量具必须显式处理）",
       bool(p2.by_difficulty) and v0 is True,
       "空分母 ⇒ 放行了不可判别的证据，这是 r56 第一版量具的错")

    print("\n== N4 组：出路分叉 —— 饱和层不许给\"补题\"这种无效出路 ==")
    #: 第 35b 条：闸写在 reason 里的「出路」，系统必须真的有那条路。
    #:   r55 F 段实测：饱和层建卷外的题基线**全做对** ⇒ 一条都补不进。
    ck("N4.1 有饱和层时 reason 点名「结构性饱和」",
       "结构性饱和" in p2.reason, p2.reason[:100])
    ck("N4.2 ⛔ 出路**不对饱和层**说补题（本例未涨层全饱和 ⇒ 不说补题）",
       "补 easy" not in p2.reason and "补题对它们无效" in p2.reason,
       p2.reason[-120:])
    ck("N4.3 饱和层的出路是「换更难的题或换环境」并明示当前无此出路",
       "换更难的题" in p2.reason and "当前系统无此出路" in p2.reason,
       p2.reason[-140:])
    #: ⛔ 承 N3.5：本例涨了的层是 hard（空间 2）。文案不许说成"所有层都
    #:    结构性饱和" —— hard **涨了**，不是饱和，只是空间小。说错会让
    #:    下游去找错的原因（第 35d 条：文案里的量不许失真）。
    ck("N4.3b ⛔ 文案不许说「所有层都结构性饱和」（hard 涨了，不是饱和）",
       "所有层都结构性饱和" not in p2.reason, p2.reason[-160:])
    ck("N4.3c 文案点名涨了的层及其空间（让出路可核查）",
       "hard" in p2.reason and "可提升空间 2 条" in p2.reason,
       p2.reason[-160:])
    #: ⛔ 对照组：无饱和层的 all_thin ⇒ 出路就是补题，不该出现"结构性饱和"。
    A2 = [f"a{i}" for i in range(2)]
    B2 = [f"b{i}" for i in range(2)]
    base3 = {t: 0.0 for t in A2 + B2}
    cand3 = {**{t: (1.0 if t == "a0" else 0.0) for t in A2},
             **{t: 0.0 for t in B2}}
    g3, b3, c3, r3 = mk_gate(base3, cand3, levels(easy=A2, hard=B2))
    p3 = g3.probe(b3, c3, r3, dev_gain=0.3,
                  dev_by_difficulty={"easy": 0.5, "hard": 0.0})
    ck("N4.4 对照组：无饱和层 ⇒ 判 all_thin 拦截",
       p3.verdict == "inconclusive" and not p3.saturated,
       f"{p3.verdict} saturated={p3.saturated}")
    ck("N4.5 ⛔ 对照组 reason **不含**「结构性饱和」（文案不撒谎）",
       "结构性饱和" not in p3.reason, p3.reason[-120:])
    ck("N4.6 对照组出路就是补题",
       "补 " in p3.reason, p3.reason[-120:])

    print("\n== N5 组：判据有牙（门槛真在起作用，不是恒真/恒假）==")
    g0, b0, c0, r0 = mk_gate(base3, cand3, levels(easy=A2, hard=B2),
                             min_level_headroom=0)
    p0 = g0.probe(b0, c0, r0, dev_gain=0.3,
                  dev_by_difficulty={"easy": 0.5, "hard": 0.0})
    ck("N5.1 `min_level_headroom=0` ⇒ 回到旧口径（不判 all_thin）",
       p0.verdict != "inconclusive", f"{p0.verdict}")
    g9, b9, c9, r9 = mk_gate(base3, cand3, levels(easy=A2, hard=B2),
                             min_level_headroom=99)
    p9 = g9.probe(b9, c9, r9, dev_gain=0.3,
                  dev_by_difficulty={"easy": 0.5, "hard": 0.0})
    ck("N5.2 `min_level_headroom=99` ⇒ 每层都不足 ⇒ 判 all_thin 拦截",
       p9.verdict == "inconclusive" and p9.blocks, f"{p9.verdict}")
    ck("N5.3 ⛔ 承上：N5.1 与 N5.2 结论相反 ⇒ N4/N3 两组不是恒真断言",
       p0.verdict != p9.verdict, "两者相同 ⇒ 判据是死的")

    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
