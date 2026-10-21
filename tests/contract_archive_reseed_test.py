#!/usr/bin/env python3
"""契约：存档「取回 → 还原 → 并进候选」的**装配**（r63f）。

═══ 为什么需要它（实测，README 135）═══

r62 加了分层非支配存档，用来打断单线爬坡。J 组把 `resample()` 的选择逻辑
测得很硬（三重排除 + 层间轮转，7 条），G 组把胶囊快照往返测了 —— 但
**两者之间的那段装配一次都没被执行过**：

| 读数 | 结果 |
|---|---|
| 生产账本 `archive.*` 事件 | **0 条**（默认关，从未开启） |
| `contract_archive_test` 端到端轮数 | `--rounds 1` ⇒ 轮首存档必空 |
| 四次实测运行（template 2 轮 / llm 3 轮 / llm+scoped 3 轮 / llm+group4 3 轮） | **11 次 resample 全部 `n_picked=0, n_restored=0`** |
| `archive.resample` 事件的断言 | **0 处**（H3 只断言 `archive.offer`） |

原因不是实现坏了，是**条件从未凑齐**：能取回需要「同一轮里 ≥2 个够格候选，
且其中一条被提交」——提交那条的 harness 变成当前 harness（被 `exclude_harness`
排除），另一条才算"旧基线、待重测"。生产 81 轮里 80 轮只有 1 个候选进闸。

⇒ 内联在 `_run_round` 里的这 48 行**无法被契约构造**，只能靠运气。本契约
把装配抽成 `archive.reseed()` 后逐个条件打回去 —— 抽函数不是为了好看，
是因为**没被跑过的代码，坏没坏不知道**。

═══ 反证的做法 ═══

三重排除的每一条都用**正对照**做反证，不用 monkeypatch：同一条目在换掉
那个条件后**必须能取回**。若换掉后仍然 0，说明 0 是夹具坏了，不是排除造成的。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []

#: ⛔ 必须声明：不声明 ⇒ `check` 会报「未声明支持的模式」。
SUPPORTED_MODES = ("default",)

sys.path.insert(0, str(ROOT))

from aether import archive as A                        # noqa: E402
from aether.archive import Archive, Entry, capsule_to_dict   # noqa: E402
from aether.genome import Capsule                     # noqa: E402


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


GENES = [{"op": "set", "target": "L3.skills.fix_x", "value": "v",
          "rationale": "test"}]


def mk(name: str, layers=("L3.skills.fix_x",), reward: float = 0.5,
       harness_id: str = "H_A", baseline_id: str = "H_OLD",
       genes=None, round_no: int = 1) -> Entry:
    """造一条存档记录。默认：量在 `H_OLD` 上、解自己的 harness 是 `H_A`。"""
    return Entry(capsule={"name": name, "genes": GENES if genes is None else genes,
                          "rationale": "", "origin": "test"},
                 harness_id=harness_id, harness={}, layers=tuple(layers),
                 reward=reward, load_gain=0.0, size_delta=0, round_no=round_no,
                 baseline_reward=0.0, baseline_harness_id=baseline_id,
                 accepted=False)


def arc_with(*entries: Entry) -> Archive:
    a = Archive()
    for e in entries:
        r = a.add(e)
        assert r.get("added") is True, f"夹具没入档：{r}"
    return a


def main() -> int:
    print("=" * 62)
    print("A 组 · 装配是独立可调的函数（内联在 _run_round 里就测不到）")
    print("=" * 62)
    ck("A1 `archive.reseed` 存在且可调（本行改前必须红）",
       hasattr(A, "reseed"),
       "" if hasattr(A, "reseed") else "aether.archive.reseed 不存在")
    if not hasattr(A, "reseed"):
        print("\n" + "=" * 62)
        print(f"❌ 共 {len(FAILS)} 条失败（装配未抽出，后续组无从判定）")
        return 1

    restored, payload, errors = A.reseed(Archive(), "H_NEW", [], 2, 1)
    ck("A2 空存档 ⇒ 三条都是零，且不报错（这是最常见的真实情况）",
       restored == [] and payload["n_picked"] == 0
       and payload["n_restored"] == 0 and errors == [],
       f"{restored} {payload.get('n_picked')}/{payload.get('n_restored')} {errors}")

    print("\n" + "=" * 62)
    print("B 组 · 真取回并装配（从没跑过的那一段）")
    print("=" * 62)
    arc = arc_with(mk("capA", reward=0.7))
    restored, payload, errors = A.reseed(
        arc, "H_NEW", [Capsule(name="fresh", genes=())], 2, 2)
    ck("B1 旧基线 + 不同 harness ⇒ 取回并还原成 1 条",
       len(restored) == 1 and payload["n_restored"] == 1
       and payload["capsules"] == ["capA"],
       f"{payload.get('capsules')} picked={payload.get('n_picked')}")
    ck("B2 还原出来的是**可执行对象**，字段一个不丢（往返逐字相等）",
       bool(restored) and isinstance(restored[0], Capsule)
       and capsule_to_dict(restored[0]) == mk("capA").capsule,
       f"{capsule_to_dict(restored[0]) if restored else None}")
    ck("B3 ⛔ 取回的每一条都带 `stale` 读数（旧基线，不许直接进判据）",
       [s.get("capsule") for s in (payload.get("stale") or [])] == ["capA"]
       and payload["stale"][0].get("stored_at_round") == 1,
       f"{payload.get('stale')}")

    print("\n" + "=" * 62)
    print("C 组 · 三重排除各自有牙（每条配一个正对照）")
    print("=" * 62)
    r0, p0, _ = A.reseed(arc, "H_OLD", [], 2, 2)      # baseline == 当前
    ck("C1 baseline 就是当前 harness ⇒ 不取（量过了，重测没意义）",
       r0 == [] and p0["n_picked"] == 0, f"picked={p0['n_picked']}")
    r0b, _, _ = A.reseed(arc, "H_OTHER", [], 2, 2)
    ck("C1b ⛔ 正对照：把当前 harness 换成第三个 ⇒ **同一条目能取回**",
       len(r0b) == 1, f"{[c.name for c in r0b]}")
    r1, p1, _ = A.reseed(arc, "H_A", [], 2, 2)        # harness_id == 当前
    ck("C2 解自己的 harness 已是当前 ⇒ 不取（等价物，已在谱系上）",
       r1 == [] and p1["n_picked"] == 0, f"picked={p1['n_picked']}")
    r2, p2, _ = A.reseed(arc, "H_NEW",
                         [Capsule(name="capA", genes=())], 2, 2)
    ck("C3 本轮已提过同名 ⇒ 不取（同胶囊同基线 ⇒ 结果必然一样）",
       r2 == [] and p2["n_picked"] == 0, f"picked={p2['n_picked']}")
    r2b, _, _ = A.reseed(arc, "H_NEW", [Capsule(name="别的", genes=())], 2, 2)
    ck("C3b ⛔ 正对照：本轮提的是别的名字 ⇒ 能取回",
       len(r2b) == 1, f"{[c.name for c in r2b]}")

    print("\n" + "=" * 62)
    print("D 组 · 「取回」≠「装配」：被跳过的那些必须可分辨")
    print("=" * 62)
    arc_empty = arc_with(mk("hollow", genes=[]))      # 空壳胶囊
    r3, p3, e3 = A.reseed(arc_empty, "H_NEW", [], 2, 3)
    ck("D1 空壳（genes 为空）⇒ 取回了但不装配，两个数**不同**",
       p3["n_picked"] == 1 and p3["n_restored"] == 0 and r3 == [] and e3 == [],
       f"picked={p3['n_picked']} restored={p3['n_restored']}")
    bad = {"name": "broken", "genes": [{"op": "set"}], "rationale": "",
           "origin": "test"}
    arc_bad = arc_with(Entry(capsule=bad, harness_id="H_A", harness={},
                             layers=("L3.skills.x",), reward=0.4, load_gain=0.0,
                             size_delta=0, round_no=1, baseline_harness_id="H_OLD"))
    try:
        r4, p4, e4 = A.reseed(arc_bad, "H_NEW", [], 2, 4)
        crashed = None
    except Exception as exc:                          # noqa: BLE001
        r4, p4, e4, crashed = [], {}, [], repr(exc)
    ck("D2 ⛔ 坏快照不抛异常，但要留 `archive.restore_failed`（不许静默跳过）",
       crashed is None and p4.get("n_restored") == 0
       and [x.get("kind") for x in e4] == ["archive.restore_failed"],
       f"crash={crashed} errors={[x.get('kind') for x in e4]}")
    ck("D3 失败事件里带 round_no 与 error（账本读者要能定位）",
       bool(e4) and e4[0]["payload"].get("round_no") == 4
       and "target" in str(e4[0]["payload"].get("error")),
       f"{e4[0]['payload'] if e4 else None}")

    print("\n" + "=" * 62)
    print("E 组 · 装配真的接回了循环（不是只在模块里躺着）")
    print("=" * 62)
    ev = (ROOT / "aether" / "evolution.py").read_text(encoding="utf-8")
    ck("E1 `_run_round` 调的是 `reseed(`，不再自己内联一遍",
       "reseed(" in ev, "")
    ck("E2 ⛔ 它取回的东西**并进 capsules**（否则取回来也不参与评分）",
       "capsules += restored" in ev, "")
    ck("E3 ⛔ 失败事件仍由调用方落账（函数不碰 ledger ⇒ 可单测）",
       "archive.restore_failed" not in ev.split("reseed(")[-1][:1200],
       "调用方那段里还硬写着 restore_failed ⇒ 说明没走 errors 通道")

    print()
    print("=" * 62)
    if FAILS:
        print(f"❌ 共 {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
