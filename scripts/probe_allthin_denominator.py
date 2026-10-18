#!/usr/bin/env python3
"""取证：`all_thin` 的**分母**到底装了哪些层，其中有多少是恒定不可用的。

## 起因

r55 结尾记了一条诚实未做：

    `all_thin` 的分母仍含结构性饱和层（easy 永远 0）⇒ 待单独一轮。

而在读源码时发现第二处口径不一致（此前没人提过）：

    generalization.py:630  all_thin = all(headroom[lv] < mlh for lv in by_diff)
                                                                    ^^^^^^^
                                                        **全部层**，含涨了的层
    generalization.py:687  「⛔ 只对**未涨**的层谈样本量：已经涨了的层
                             不需要这层豁免」（同一函数里 `thin` 的纪律）

`thin` 的分母是 `lagging_all`（只含未涨层），`all_thin` 的分母是 `by_diff`
（含涨了的层）。两处同形却不同源 —— 第 13 条。

## 为什么这两件事必须分开标定（第 39 条）

    A · 涨了的层进了分母
        「涨了」是执行判据给出的**确定事实**（reward 0→1 是跑出来的），
        不因为这一层只有 1 条题就变成「可能是运气」。而 headroom 小只让
        「**没涨**」不可判别（涨没涨都说不清），不能反过来抹掉「涨了」。
        ⇒ 一个层明明提供了正证，却因为「它太小」被算成「没有证据可用」。

    B · 结构性饱和层（saturated）进了分母
        headroom == 0 ⇒ 这一层的读数**必然**是 0，不随候选变化 ⇒ 它是
        **恒定的**不可用 ⇒ 放在分母里 = 恒定贡献「不可用」。
        ⛔ 但出路不同：thin 要补题（--ho-grow 能做），saturated 要换更难的
        题/换环境（当前系统没有这条出路）⇒ 它进了分母就是**永远补不回来**
        的假红灯。

两者的修法完全不同，混在一起判必然错一个。

## 本脚本做什么

走**真实生产路径**（`aether.cli.main(["evolve", ...])`），在
`GeneralizationGate.probe` 上包一层记录 —— 不改任何判据 ⇒ 量出来的就是
生产实际发生的事（第 1 条：同源）。

⛔ 三种分母的取值**不是另抄一份逻辑**：只用到闸自己的输出字段
（`by_difficulty` / `headroom` / `saturated` / `verdict`）和闸自己的常量
（`heldout_floor` / `min_level_headroom` / `min_tasks` / `base_at_ceiling`），
公式与源码逐字同形 ⇒ 唯一变量就是"分母装了哪些层"。

报告五张表：

  A · 每次 probe 的完整读数（层增益 / headroom / 三口径 all_thin / 分支归因）
  B · **涨了的层**进了分母的实例（A 类假红灯）
  C · **saturated 层**进了分母的实例（B 类假红灯）
  D · 假红灯是否真改变了裁决（推演：修正后 verdict 会变成什么）
  E · 汇总：三口径取值序列 + 不一致次数
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aether.generalization as gen_mod  # noqa: E402
from aether.cli import main as cli_main  # noqa: E402
from aether.evolution import EvolutionLoop as _Loop  # noqa: E402

#: 每次 probe 的观测记录。
_ROWS: list = []
#: 当前轮次（由 `_run_round` 的 hook 写入）。
_CUR = {"round": -1}

_orig_probe = gen_mod.GeneralizationGate.probe
_orig_round = _Loop._run_round


def _patched_round(self, h, round_no=0):
    _CUR["round"] = int(round_no)
    try:
        return _orig_round(self, h, round_no=round_no)
    finally:
        _CUR["round"] = -1


def _patched_probe(self, *a, **kw):
    res = _orig_probe(self, *a, **kw)
    try:
        _ROWS.append(_observe(self, res))
    except Exception as e:                                     # noqa: BLE001
        #: ⛔ 观测挂了必须看得见，不许静默丢行（第 10 条）。
        _ROWS.append({"error": f"{type(e).__name__}: {e}"})
    return res


def _observe(gate, res) -> dict:
    """从闸的**输出**推演三种分母口径。公式与源码逐字同形。"""
    by_diff = dict(res.by_difficulty or {})
    head = dict(res.headroom or {})
    dev_by = dict(res.dev_by_difficulty or {})
    floor = float(gate.heldout_floor)
    mlh = int(gate.min_level_headroom)
    sat = set(res.saturated or ())

    lagging_all = sorted(lv for lv in by_diff if by_diff[lv] <= floor)
    #: 涨了的层（> floor）。⛔ 这是 A 类假红灯的来源：它们有正证。
    rose = sorted(lv for lv in by_diff if by_diff[lv] > floor)

    def _all(levels):
        return bool(levels) and all(head.get(lv, 0) < mlh for lv in levels)

    #: V0 = 现行（分母 = 全部层）；V1 = 只未涨层；V2 = 全层剔除饱和。
    #: ⛔⛔ V2 的分母是 **by_diff − saturated**，**不是** lagging_all − sat。
    #:    第一版我写成了后者，轮 5/6 读出「剔除后 all_thin 翻 False ⇒
    #:    裁决被改变 2 次」。那是**量具错**：lagging 全是饱和层时分母变空，
    #:    `bool([])` ⇒ False，被读成"有判别力"。而真实语义是「所有未涨层
    #:    都是零信息」⇒ 该看剩下的**涨层**够不够，不是直接判有判别力。
    #:    第 24 条第六次命中：读数反常，**先怀疑量具，再怀疑世界**。
    v0 = _all(sorted(by_diff))
    v1 = _all(lagging_all)
    v2 = _all([lv for lv in sorted(by_diff) if lv not in sat])

    #: ⛔ 分支归因：不看文案（第 35d 条），按源码的分支条件逐条排除。
    branch = "other"
    if res.n_tasks < int(gate.min_tasks):
        branch = "min_tasks"
    elif (gate.base_at_ceiling is not None
          and res.base_heldout >= float(gate.base_at_ceiling) - 1e-9):
        branch = "at_ceiling"
    elif res.verdict == "no_gain":
        branch = "no_gain"
    elif res.verdict == "overfit":
        branch = "overfit"
    elif res.verdict == "partial":
        branch = "partial"
    elif lagging_all:
        #: 源码在 all_thin 之前有 cherry / undecidable 两个分支。
        cherry = [lv for lv in lagging_all
                  if lv not in (res.thin or ())
                  and dev_by.get(lv) is not None
                  and dev_by[lv] > float(gate.dev_floor)]
        undec = [lv for lv in lagging_all
                 if lv not in (res.thin or ())
                 and dev_by.get(lv) is None]
        out_of_reach = [lv for lv in lagging_all
                        if lv not in (res.thin or ())
                        and dev_by.get(lv) is not None
                        and dev_by[lv] <= float(gate.dev_floor)]
        if cherry:
            branch = "cherry"
        elif undec and not out_of_reach:
            branch = "undecidable"
        elif v0:
            branch = "all_thin"
        else:
            branch = "generalize"
    #: ⛔ 修正后：同一个分支序，只把 v0 换成 v1 / v2。
    #:    all_thin 分支的前置条件是 cherry 空 且 (undec 空 或 out_of_reach 非空)
    #:    ⇒ v 翻成 False 后必然落到放行的 generalize（源码里它下面就是放行）。
    return {"round": int(_CUR["round"]),
            "verdict": res.verdict,
            "branch": branch,
            "by_difficulty": {k: round(v, 4) for k, v in by_diff.items()},
            "headroom": dict(head),
            "thin": list(res.thin or ()),
            "saturated": sorted(sat),
            "rose": rose,
            "lagging_all": lagging_all,
            "v0": bool(v0), "v1": bool(v1), "v2": bool(v2),
            "v2_denominator": sorted(lv for lv in by_diff if lv not in sat),
            "rose_thin": sorted(lv for lv in rose if head.get(lv, 0) < mlh),
            "ho_gain": round(res.heldout_gain, 4),
            "dev_gain": round(res.dev_gain, 4)}


def make_root() -> str:
    """独立 root：**只有 data/ 是新的**，tasks/ 软链到仓库（环境是常量）。"""
    d = tempfile.mkdtemp(prefix="v6_allthin_")
    os.symlink(ROOT / "tasks", Path(d) / "tasks")
    return d


def run_once(argv):
    _ROWS.clear()
    _CUR["round"] = -1
    gen_mod.GeneralizationGate.probe = _patched_probe
    _Loop._run_round = _patched_round
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = cli_main(argv)
    finally:
        gen_mod.GeneralizationGate.probe = _orig_probe
        _Loop._run_round = _orig_round
    try:
        out = json.loads(buf.getvalue())
    except Exception:                                          # noqa: BLE001
        out = {}
    return rc, out, buf.getvalue(), list(_ROWS)


BASE = ["evolve", "--root", "@TMP@", "--rounds", "6", "--seed", "7",
        "--depth", "2", "--max-tasks", "16"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", default="6")
    ap.add_argument("--depth", default="2")
    ap.add_argument("--max-tasks", default="16")
    ap.add_argument("--kinds",
                    default="compare,binop,constant,boolop,unaryop")
    ap.add_argument("--balanced", action="store_true")
    ap.add_argument("--ho-grow", action="store_true")
    ap.add_argument("--ho-grow-variants", default="1")
    ap.add_argument("--retry-every", default="0")
    args = ap.parse_args()

    base = ["evolve", "--root", "@TMP@", "--rounds", str(args.rounds),
            "--seed", "7", "--depth", str(args.depth),
            "--max-tasks", str(args.max_tasks),
            "--kinds", str(args.kinds)]
    if args.balanced:
        base.append("--balanced")
    if args.ho_grow:
        base += ["--ho-grow", "--ho-grow-variants", str(args.ho_grow_variants)]
    if int(args.retry_every or 0) > 0:
        base += ["--retry-every", str(args.retry_every)]

    td = make_root()
    try:
        argv = [x.replace("@TMP@", td) for x in base]
        rc, out, raw, rows = run_once(argv)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    errs = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]

    print("=" * 70)
    print("A · 每次 probe 的完整读数（三口径 all_thin / 分支归因）")
    print("=" * 70)
    for i, r in enumerate(rows):
        print(f"  [{i}] 轮{r['round']} {r['verdict']:<13} "
              f"branch={r['branch']:<12} ho={r['ho_gain']:+.4f} "
              f"dev={r['dev_gain']:+.4f}")
        print(f"       by_diff={r['by_difficulty']}")
        print(f"       headroom={r['headroom']}  涨了的层={r['rose']}")
        print(f"       thin={r['thin']} saturated={r['saturated']} "
              f"lagging_all={r['lagging_all']}")
        print(f"       all_thin: 现行V0={r['v0']}  "
              f"只未涨层V1={r['v1']}  再剔除饱和V2={r['v2']}")
    if errs:
        print(f"  ⛔ 观测失败 {len(errs)} 次：{errs[:3]}")

    print()
    print("=" * 70)
    print("B · A 类：涨了的层进了分母（它提供了正证却被算成\"没证据\"）")
    print("=" * 70)
    a_rows = [r for r in rows if r["rose_thin"]]
    if not a_rows:
        print("  （无实例：本口径下没有\"涨了且 headroom<门槛\"的层）")
    for r in a_rows:
        print(f"  轮{r['round']} 涨了但被算进分母的层={r['rose_thin']}")
        print(f"       headroom={r['headroom']} V0={r['v0']} V1={r['v1']}"
              f"  ⇒ 剔除后 all_thin {r['v0']} → {r['v1']}")

    print()
    print("=" * 70)
    print("C · B 类：结构性饱和层进了分母（读数必然为 0，恒定不可用）")
    print("=" * 70)
    b_rows = [r for r in rows if r["saturated"]]
    if not b_rows:
        print("  （无实例：本口径下没有 headroom==0 的未涨层）")
    for r in b_rows:
        print(f"  轮{r['round']} saturated={r['saturated']} "
              f"headroom={r['headroom']}")
        print(f"       V0={r['v0']}（分母 {sorted(r['by_difficulty'])}）  "
              f"V2={r['v2']}（分母 {r['v2_denominator']}）")
        print(f"       ⇒ 剔除饱和后 all_thin {r['v0']} → {r['v2']}"
              f"{'  ⇒ 恒等，饱和层未造成假红灯' if r['v2'] == r['v0'] else '  ⇒ 有差异'}")

    print()
    print("=" * 70)
    print("D · 假红灯是否真的改变了裁决（推演修正后的 verdict）")
    print("=" * 70)
    flipped = [r for r in rows if r["branch"] == "all_thin"
               and (r["v1"] != r["v0"] or r["v2"] != r["v0"])]
    if not flipped:
        print("  （无：本口径下三口径取值一致，或不一致处不影响最终分支）")
    for r in flipped:
        print(f"  轮{r['round']} 现行={r['verdict']}(all_thin)  "
              f"by_diff={r['by_difficulty']}")
        print(f"       headroom={r['headroom']} thin={r['thin']} "
              f"saturated={r['saturated']}")
        print(f"       V0={r['v0']} V1={r['v1']} V2={r['v2']}")
        for tag, v in (("V1 只未涨层", r["v1"]), ("V2 再剔除饱和", r["v2"])):
            if not v:
                print(f"       ⇒ 按 {tag}：all_thin 翻 False ⇒ 落到放行"
                      f"（generalize）⇒ **裁决被改变**")

    print()
    print("=" * 70)
    print("E · 汇总")
    print("=" * 70)
    print(f"  probe 调用总数          {len(rows)}")
    print(f"  all_thin 分支次数       "
          f"{sum(1 for r in rows if r['branch'] == 'all_thin')}")
    print(f"  A 类（涨了仍进分母）    {len(a_rows)}")
    print(f"  B 类（饱和层进分母）    {len(b_rows)}")
    print(f"  ⛔ 裁决被改变的次数     {len(flipped)}")
    print(f"  V0≡V2（饱和层无影响）  "
          f"{sum(1 for r in rows if r['v0'] == r['v2'])}/{len(rows)}")
    print(f"  V0≠V1 的次数（涨层决定）"
          f"{sum(1 for r in rows if r['v0'] != r['v1'])}")
    print(f"  V0 序列  {''.join('1' if r['v0'] else '0' for r in rows)}")
    print(f"  V1 序列  {''.join('1' if r['v1'] else '0' for r in rows)}")
    print(f"  V2 序列  {''.join('1' if r['v2'] else '0' for r in rows)}")
    #: ⛔ 字段名以 `RoundReport.to_dict` 为准（不猜）：轮次在 `rounds` 里。
    rounds = out.get("rounds") or []
    print()
    print(f"  baseline 轨迹  "
          f"{[round(r.get('baseline_reward'), 4) for r in rounds]}")
    print(f"  提交            {[r.get('committed') for r in rounds]}")
    if not rounds:
        print("  （out 未解析出 rounds，原始输出尾部：）")
        print("   " + "\n   ".join(raw.splitlines()[-6:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
