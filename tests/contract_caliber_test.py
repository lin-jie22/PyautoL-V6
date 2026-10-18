#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：尺子分区（caliber）——「哪条读数能跟哪条比」必须可回答。

## 为什么需要这份契约

r57 实测：`aether doctor` 是红的 —— `judge.boundary` **fail**：生产账本里
`eval.verdict` 用了 **14 种判据身份**。它给的出路是"用同一把尺子重跑一组"，
而 ⛔ **系统里当时没有这个工具** ⇒ 那条出路是装饰（第 35b 条）。

修法不是"把闸调松"，而是给一条**真有的处置动作**：
`aether caliber --seal`（只追加一条 `judge.sealed` 事件、不删历史），
封存后旧尺子段退出一切跨轮比较。

本文件守两件事：
  1. 封存**真的改变读数**（不是改了个状态就说"处理过了"）；
  2. 判据**没有变松**：没有封存事件时，多把尺子**必须**照样 FAIL。

## 组

  O1 分区：多把尺子能被切开，逐段 n / seq 区间正确
  O2 可比视窗：当前尺子段可比，旧段落到 dropped_other
  O3 封存有牙：dropped_sealed 0 → N，且**当前尺子段必须保留**（⛔ 见 O3.3）
  O4 ⛔ 反证：抹掉封存事件 ⇒ 逐字回到未封存的样子
  O5 封存只准前推（不允许把已封存的历史放出来）
  O6 Agg 与纯函数同源（第 13 条：判据不许有两份实现）
  O7 ⛔ 判据没变松：未封存 ⇒ FAIL 且 action 指向真有的命令；封存 ⇒ OK
  O8 空集合语义（第 57 条）：空账本不许崩、不许读出"可比 0 = 健康"
  O9 口径：只认 `eval.verdict`，别的 kind 不许混进来

跑法（纯内存 runner，秒级）：
    python tests/contract_caliber_test.py
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


#: 三把尺子。CUR 是当前尺子（用真 `judge.identity()` 当其中一把，
#: 这样"当前段"是真的当前段 —— 不用假的 id 糊过去，见第 19 条）。
OLD1, OLD2 = "a" * 32, "b" * 32


def _mk_ledger(tmp: Path, rows):
    """rows: [(kind, payload)] → 造一份真账本（走生产 `Ledger.append`）。"""
    from aether.ledger import Ledger
    #: ⛔ 落点必须是 `<root>/data/ledger.jsonl`：`doctor` 走 `paths(root)`，
    #:    造在根目录 ⇒ doctor 读到空账本 ⇒ 「多把尺子 ⇒ FAIL」这条恒绿
    #:    （实测踩到：O7.1 因此假通过）。
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    led = Ledger(tmp / "data" / "ledger.jsonl")
    for i, (kind, payload) in enumerate(rows):
        led.append(kind, payload, actor="contract")
    return led


def _verdict_rows(cur, n_old1=5, n_old2=4, n_cur=3, n_legacy=2, other_kinds=3):
    rows = []
    for _ in range(n_old1):
        rows.append(("eval.verdict", {"judge_version": OLD1, "reward": 0.1}))
    for _ in range(n_old2):
        rows.append(("eval.verdict", {"judge_version": OLD2, "reward": 0.2}))
    for _ in range(n_cur):
        rows.append(("eval.verdict", {"judge_version": cur, "reward": 0.9}))
    for _ in range(n_legacy):
        rows.append(("eval.verdict", {"reward": 0.0}))       # 升级前存量
    for _ in range(other_kinds):
        rows.append(("memory.write", {"note_id": "x"}))      # 别的 kind
    return rows


def main() -> int:
    from aether import caliber as C
    from aether import judge as J

    CUR = J.identity()
    tmp = Path(tempfile.mkdtemp(prefix="v6_caliber_contract_"))

    def fresh(**kw):
        d = Path(tempfile.mkdtemp(prefix="v6_cal_fresh_"))
        led = _mk_ledger(d, _verdict_rows(CUR, **kw))
        return d, led

    # ──────────────────────────────────────────────────────────
    print("\n== O1 组：分区（多把尺子能被切开）==")
    d1, led1 = fresh()
    evs = list(led1.iter_events())
    p1 = C.partition(evs)
    ck("O1.1 三把尺子 ⇒ 3 段", p1["n_segments"] == 3, f"{p1['n_segments']}")
    ck("O1.2 带身份的裁决计数正确（5+4+3）",
       p1["n_verdicts_with_version"] == 12, f"{p1['n_verdicts_with_version']}")
    ck("O1.3 ⛔ 升级前存量单列（2 条，不混进任何一段）",
       p1["n_no_version"] == 2, f"{p1['n_no_version']}")
    segs = {s["judge_version"]: s for s in p1["segments"]}
    ck("O1.4 逐段 n 正确", segs[OLD1]["n"] == 5 and segs[OLD2]["n"] == 4
       and segs[CUR]["n"] == 3,
       f"{ {k[:4]: v['n'] for k, v in segs.items()} }")
    ck("O1.5 只有当前尺子的段 `is_current`",
       sum(1 for s in p1["segments"] if s["is_current"]) == 1)
    ck("O1.6 未封存 ⇒ `sealed_upto` 为 None（不是 0）",
       C.sealed_upto(evs) is None, f"{C.sealed_upto(evs)}"

       )

    # ──────────────────────────────────────────────────────────
    print("\n== O2 组：可比视窗==")
    v2 = C.comparable_view(evs)
    ck("O2.1 可比 = 当前尺子那一段（3）", v2["n_comparable"] == 3,
       f"{v2['n_comparable']}")
    ck("O2.2 旧尺子落到 dropped_other（9）",
       v2["dropped_other_caliber"] == 9, f"{v2['dropped_other_caliber']}")
    ck("O2.3 存量落到 dropped_no_version（2）",
       v2["dropped_no_version"] == 2, f"{v2['dropped_no_version']}")
    ck("O2.4 未封存 ⇒ dropped_sealed = 0", v2["dropped_sealed"] == 0)

    # ──────────────────────────────────────────────────────────
    print("\n== O3 组：封存有牙（真改变读数）==")
    rep = C.seal(led1, reason="契约 O3")
    ck("O3.1 seal 成功", rep["sealed"] is True, f"{rep.get('reason')}")
    evs2 = list(led1.iter_events())
    v3 = C.comparable_view(evs2)
    ck("O3.2 dropped_sealed 0 → 9", v3["dropped_sealed"] == 9,
       f"{v3['dropped_sealed']}")
    ck("O3.3 ⛔⛔ 封存**不许**把当前尺子段一起封掉（可比仍为 3）",
       v3["n_comparable"] == 3,
       f"{v3['n_comparable']}（第一版写到账本末尾 ⇒ 归零，那是把处置做成清空）")
    ck("O3.4 封存是**追加**（事件数 +1，历史一条没删）",
       len(evs2) == len(evs) + 1, f"{len(evs)} → {len(evs2)}")
    ck("O3.5 封存边界 = 最后一个旧段末尾（不是账本末尾）",
       v3["sealed_upto"] is not None and v3["sealed_upto"] <= max(
           e.seq for e in evs2 if getattr(e, "seq", None) is not None),
       f"{v3['sealed_upto']}")

    # ──────────────────────────────────────────────────────────
    print("\n== O4 组：⛔ 反证（抹掉封存事件 ⇒ 必须回到原样）==")
    d4, led4 = fresh()
    evs4 = list(led4.iter_events())
    base4 = C.comparable_view(evs4)
    C.seal(led4, reason="契约 O4（随后抹掉）")
    lp = led4.path
    kept = []
    for line in lp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if json.loads(line).get("kind") == C.SEALED:
            continue
        kept.append(line)
    lp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    after4 = C.comparable_view(list(led4.iter_events()))
    ck("O4.1 抹掉后 dropped_sealed 回到 0",
       after4["dropped_sealed"] == base4["dropped_sealed"] == 0)
    ck("O4.2 抹掉后 n_comparable 回到 3",
       after4["n_comparable"] == base4["n_comparable"] == 3,
       f"{after4['n_comparable']} vs {base4['n_comparable']}")
    ck("O4.3 ⛔ 承上：有/无封存事件结论**相反** ⇒ O3 不是恒真断言",
       v3["dropped_sealed"] != after4["dropped_sealed"],
       "两者相同 ⇒ 封存是死的")

    # ──────────────────────────────────────────────────────────
    print("\n== O5 组：封存只准前推==")
    again = C.seal(led1, reason="第二次")
    ck("O5.1 同一账本再封存 ⇒ 被拒（不许把已封存的历史放出来）",
       again["sealed"] is False, f"{again.get('reason')}")
    ck("O5.2 拒绝理由讲得清", "前推" in str(again.get("reason", "")))

    # ──────────────────────────────────────────────────────────
    print("\n== O6 组：Agg 与纯函数同源（第 13 条）==")
    agg = C.CaliberAgg()
    for e in evs2:
        agg.feed(e)
    r6 = agg.result()
    ck("O6.1 n_comparable 同源", r6["n_comparable"] == v3["n_comparable"],
       f"{r6['n_comparable']} vs {v3['n_comparable']}")
    ck("O6.2 dropped_sealed 同源",
       r6["dropped_sealed"] == v3["dropped_sealed"],
       f"{r6['dropped_sealed']} vs {v3['dropped_sealed']}")
    ck("O6.3 n_segments 同源", r6["n_segments"] == v3["n_segments"])
    ck("O6.4 dropped_no_version 同源",
       r6["dropped_no_version"] == v3["dropped_no_version"])

    # ──────────────────────────────────────────────────────────
    print("\n== O7 组：⛔ 判据没变松（doctor 接线）==")
    from aether import doctor as D

    def judge_check(root: Path):
        r = D.run(root, caps="", checks=[D._check_judge])
        return r["checks"][0]

    d_u, _ = fresh()                       # 未封存
    cu = judge_check(d_u)
    ck("O7.1 多把尺子 + 未封存 ⇒ **必须** FAIL（判据没被调松）",
       cu["status"] == D.FAIL, f"{cu['status']} / {cu['detail'][:60]}")
    ck("O7.2 ⛔ 出路指向**真有的**命令（第 35b 条）",
       "caliber" in (cu.get("action") or "") and "--seal" in (cu.get("action") or ""),
       f"{cu.get('action')}")
    d_s, led_s = fresh()                   # 已封存
    C.seal(led_s, reason="契约 O7")
    cs = judge_check(d_s)
    ck("O7.3 封存后 ⇒ OK", cs["status"] == D.OK, f"{cs['status']}")
    ck("O7.4 ⛔ 承上：O7.1 与 O7.3 结论相反 ⇒ 不是恒真/恒假断言",
       cu["status"] != cs["status"], "两者相同 ⇒ 判据是死的")
    # 单把尺子（反证：不许恒红）
    d_one = Path(tempfile.mkdtemp(prefix="v6_cal_one_"))
    _mk_ledger(d_one, [("eval.verdict", {"judge_version": CUR})] * 4)
    c1 = judge_check(d_one)
    ck("O7.5 只有一把尺子 ⇒ 本来就该 OK（不许恒红）",
       c1["status"] == D.OK, f"{c1['status']}")

    # ──────────────────────────────────────────────────────────
    print("\n== O8 组：空集合语义（第 57 条）==")
    d_e = Path(tempfile.mkdtemp(prefix="v6_cal_empty_"))
    evs_e = list(_mk_ledger(d_e, []).iter_events())
    v8 = C.comparable_view(evs_e)
    ck("O8.1 空账本 ⇒ 0 段、不崩", v8["n_segments"] == 0)
    ck("O8.2 空账本 ⇒ sealed_upto = None（不许当作「已封存到 0」）",
       v8["sealed_upto"] is None)
    ck("O8.3 ⛔ `n_comparable == 0` 必须带**成因**（不许读成健康）",
       bool(v8["why_empty"]), f"{v8['why_empty']}")
    ck("O8.4 空账本 seal ⇒ 拒绝并说明",
       C.seal(_mk_ledger(d_e, []))["sealed"] is False)

    # ──────────────────────────────────────────────────────────
    print("\n== O9 组：口径（只认 eval.verdict）==")
    d9 = Path(tempfile.mkdtemp(prefix="v6_cal_kind_"))
    _mk_ledger(d9, [("memory.write", {"note_id": "x"})] * 7
               + [("eval.verdict", {"judge_version": OLD1})] * 2)
    v9 = C.comparable_view(list(_mk_ledger(d9, []).iter_events()))
    p9 = C.partition(list(_mk_ledger(d9, [("memory.write", {"note_id": "x"})] * 7
                                     + [("eval.verdict", {"judge_version": OLD1})] * 2
                                     ).iter_events()))
    ck("O9.1 ⛔ 别的 kind 不许被算成「升级前存量」",
       p9["n_no_version"] == 0, f"{p9['n_no_version']}（不过滤会把 7 条记成存量）")
    ck("O9.2 只有 1 段（OLD1）", p9["n_segments"] == 1, f"{p9['n_segments']}")
    _ = v9

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
