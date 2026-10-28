#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**合成层停靠点**（README 167）。

═══ 为什么有这份契约 ═══

判据侧已经量过三道门（装载 164 / 判据 165 / 形态 166），可「**判据有鉴别力
之后为什么还修不好**」从没被归过因。r85 留下一个读数：

    boltons：40 commit → 6/10 个 commit 有 F2P → **只修好 2 个**

那 4 个失败在哪？没有这张表，`found=False` 就只是一个数：看不出是该
扩候选集、该修定位器、还是该修沙箱。**连续三轮「开门收益 0」之后，再靠
猜选下一道门是不负责任的**（第 24 条：一个维度恒 0，先怀疑量具）。

⛔⛔ 本契约盯的是**格的划分本身**够不够格当诊断依据：

  · A 六格**各自可达**（每条 commit 恰好一格 ⇒ 先守恒）
  · B ⛔⛔ **判据顺序不许重排**：基线跑挂时 `focus` 必然为空 ⇒
        先判 focus 会把「量具炸了」误记成「定位不到」（第 25 条）
  · C `n_ran == 0`（候选一条都没跑成）**不许**落进 `S3_no_gain`
        ⇒ 那会把「尺子没量」记成「世界没有解」
  · D `S5_full` 只是**必要**条件（161 契约 P3 已证），本契约只保证它是
        六格里的**最后一格**，不保证它等于修好
  · E 格名**写死对拍**（改名字 ⇒ 跨轮读数不可比）
  · F 真实读数（落盘缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）

═══ ⛔ 这是诊断层 ═══

`gate` 只依赖 `SynthResult` 自身字段，**不看 base、不看人类修复**
⇒ 可离线回归。它不扩大搜索空间、不改候选形状。

跑法（离线、秒级）：
    python tests/contract_synth_gate_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

from aether.synthesize import (                               # noqa: E402
    GATE_BLEW, GATE_FULL, GATE_NO_CANDS, GATE_NO_FOCUS,
    GATE_NO_GAIN, GATE_PARTIAL, SynthResult)

SKIPS: list = []
FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


def mk(**kw) -> SynthResult:
    """造一个只带指定字段的 SynthResult（其余留默认）。"""
    base = {"n_tests": 5}
    base.update(kw)
    return SynthResult(**base)


def main() -> int:
    print("═══ 合成层停靠点契约（README 167）═══\n")

    # ── A 组：六格各自可达 ──────────────────────────────────────────
    print("== A 组：六格**各自可达**（否则有一格是死代码）==")
    grid = {
        GATE_BLEW: mk(base_ok=-1, n_blew=1, focus=(), n_cands=0, n_ran=0),
        GATE_NO_FOCUS: mk(base_ok=3, focus=(), n_cands=0, n_ran=0),
        GATE_NO_CANDS: mk(base_ok=3, focus=("sp",), n_cands=0, n_ran=0),
        GATE_NO_GAIN: mk(base_ok=3, focus=("sp",), n_cands=10, n_ran=10),
        GATE_PARTIAL: mk(base_ok=3, new_ok=4, patch="x", focus=("sp",),
                         n_cands=10, n_ran=10),
        GATE_FULL: mk(base_ok=3, new_ok=5, patch="x", focus=("sp",),
                      n_cands=10, n_ran=10),
    }
    for want, r in grid.items():
        ck(f"A {want} 可达", r.gate == want, f"got={r.gate}")
    ck("A7 六格互不相同（格是有区分力的，不是别名）",
       len({r.gate for r in grid.values()}) == 6,
       str(sorted(r.gate for r in grid.values())))

    # ── B 组：⛔⛔ 判据顺序不许重排 ──────────────────────────────────
    print("\n== B 组：⛔⛔ 顺序 —— 基线炸时 focus 必然为空，先判谁？==")
    blew = mk(base_ok=-1, n_blew=1, focus=(), n_cands=0, n_ran=0)
    ck("B1 ⛔ 基线跑挂 ⇒ S0，不许被 focus 空抢先判成 S1",
       blew.gate == GATE_BLEW, f"got={blew.gate}")
    ck("B2 基线跑挂时 focus 确实是空的（这正是陷阱的形状）",
       blew.focus == ())
    #: 反证：如果只看 focus，`blew` 与下面这条**长得一模一样**
    lookalike = mk(base_ok=3, focus=(), n_cands=0, n_ran=0)
    ck("B3 ⛔ 两者字段几乎相同，只有 base_ok 不同 ⇒ 必须靠 base_ok 分开",
       lookalike.gate == GATE_NO_FOCUS and blew.gate == GATE_BLEW,
       f"{lookalike.gate} vs {blew.gate}")

    # ── C 组：n_ran == 0 不许落 S3 ──────────────────────────────────
    print("\n== C 组：候选一条都没跑成 ⇒ 「尺子没量」，不是「没有解」==")
    allblew = mk(base_ok=3, focus=("sp",), n_cands=10, n_ran=0, n_blew=10)
    ck("C1 ⛔ 候选全跑挂 ⇒ S0（不是 S3_no_gain）",
       allblew.gate == GATE_BLEW, f"got={allblew.gate}")
    ck("C2 对照：真跑了但没增益才落 S3",
       grid[GATE_NO_GAIN].gate == GATE_NO_GAIN)
    ck("C3 ⛔ 两者 n_cands 相同，只靠 n_ran 分开 ⇒ 不许合并这两格",
       allblew.n_cands == grid[GATE_NO_GAIN].n_cands)

    # ── D 组：S5 只是必要条件 ───────────────────────────────────────
    print("\n== D 组：S5 是**最后一格**，不等于「修好」==")
    ck("D1 full_pass=True 才落 S5（found 但没全绿 ⇒ S4）",
       grid[GATE_FULL].full_pass and not grid[GATE_PARTIAL].full_pass,
       f"full={grid[GATE_FULL].full_pass} partial={grid[GATE_PARTIAL].full_pass}")
    ck("D2 S4 的 found=True（它的病是**凑数**，不是没找到）",
       grid[GATE_PARTIAL].found)
    #: ⛔ full_pass 只是必要条件：两条不同改法都全绿 ⇒ S5 也选中了错的那个
    alt = mk(base_ok=3, new_ok=5, patch="y", focus=("sp",),
             n_cands=10, n_ran=10)
    ck("D3 ⛔ 反证：`new_ok >= n_tests` 内还有另一种补丁也落 S5 "
       "⇒ S5 不是充分条件（161 契约 P3 同款）",
       alt.gate == GATE_FULL and grid[GATE_FULL].gate == GATE_FULL
       and alt.patch != grid[GATE_FULL].patch)

    # ── E 组：格名写死对拍 ──────────────────────────────────────────
    print("\n== E 组：格名**写死**（改名 ⇒ 跨轮读数不可比）==")
    want_names = {
        "GATE_BLEW": "S0_blew", "GATE_NO_FOCUS": "S1_no_focus",
        "GATE_NO_CANDS": "S2_no_cands", "GATE_NO_GAIN": "S3_no_gain",
        "GATE_PARTIAL": "S4_partial", "GATE_FULL": "S5_full",
    }
    got_names = {
        "GATE_BLEW": GATE_BLEW, "GATE_NO_FOCUS": GATE_NO_FOCUS,
        "GATE_NO_CANDS": GATE_NO_CANDS, "GATE_NO_GAIN": GATE_NO_GAIN,
        "GATE_PARTIAL": GATE_PARTIAL, "GATE_FULL": GATE_FULL,
    }
    for k, v in want_names.items():
        ck(f"E {k} == {v!r}", got_names[k] == v, f"got={got_names[k]!r}")

    # ── F 组：真实读数（缺失则跳过并披露）──────────────────────────
    print("\n== F 组：真实读数（boltons 端到端落盘）==")
    p = Path("/tmp/auto_unlock_boltons40.json")
    if not p.exists():
        skip("F 真实读数", f"缺 {p}（跑 probe_auto_unlock.py --out 生成）")
    else:
        import json
        from collections import Counter
        rows = json.loads(p.read_text(encoding="utf-8"))
        ck("F1 落盘非空", len(rows) > 0, str(len(rows)))
        gated = [r for r in rows if "gate" in r]
        ck("F2 每条进到合成层的 commit 都有 gate",
           len(gated) == len([r for r in rows
                              if r.get("stage") not in ("no_criteria",
                                                        "no_f2p")]),
           f"{len(gated)} vs {len(rows)}")
        dist = Counter(r["gate"] for r in gated)
        print(f"      合成层停靠点分布：{dict(sorted(dist.items()))}")
        ck("F3 ⛔ 至少两格有正例 ⇒ 分布不是「全落一格」",
           len(dist) >= 2, str(dict(dist)))
        #: ⛔ 关键交叉：S3（搜不出增益）里有多少形态上根本够不到
        if any("reach" in r for r in gated):
            cross = Counter((r.get("reach"), r.get("gate")) for r in gated)
            print(f"      reach × gate：{dict(sorted(cross.items(), key=lambda x: str(x[0])))}")
            s3 = [r for r in gated if r.get("gate") == GATE_NO_GAIN]
            unreachable = [r for r in s3 if r.get("reach") in ("L1", "X")]
            print(f"      S3 共 {len(s3)} 条，其中形态档 L1/X（够不到）"
                  f" {len(unreachable)} 条")

    print()
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    msg = "✅ 六格各自可达、顺序不重排、S0 收两种同形、S5 只当必要条件"
    if SKIPS:
        msg += f"（{len(SKIPS)} 项跳过：未覆盖 ≠ 通过）"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
