#!/usr/bin/env python3
"""契约测试：一次磁盘遍历（streaming）—— 读数不能靠"把整份账本放进内存"换来的。

守的是第 30 条宪法：
    **观测不得改变被观测对象的成本结构**。一次 `/api/status` 要同时给出
    统计 / 链校验 / 一致性 / 记忆对账 / 尾部窗口五份读数；它们全都只是
    "遍历一遍"，那就必须只遍历一遍、且不同时持有整份账本。

⛔ 判别力声明（先说哪些断言**不是**战果，避免自欺）：
  · A/B 组是**护栏**：证明流式版与物化版**逐字段相等**。旧代码跑这些
    也全绿 —— 它们的作用是"改坏了立刻红"，不是"证明改好了"。
  · **C1/C2（内存峰值）是唯一有判别力的断言**：把 `_collect` 退回物化
    实现，A/B/G 全绿而 C1/C2 必红（已实测，见 README §"一次遍历"）。
  · D 组（撞键）守的是这次新增的 `duplicate_run_ids`：旧实现用 dict
    静默覆盖，撞键事故不可见。

跑法：
    python3 tests/contract_streaming_test.py [--e2e]
"""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import tracemalloc
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aether.ledger import Ledger, LedgerStatsAgg          # noqa: E402
from aether.serve import (                                 # noqa: E402
    MemoryLedgerAgg, TailWindowAgg, _collect, _event_dict,
    _memory_file_report, memory_report)
from aether.store import Store                             # noqa: E402

FAILS: List[str] = []
N_CK = 0


def ck(name: str, cond: bool, detail: str = "") -> None:
    global N_CK
    N_CK += 1
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILS.append(name)


#: ⛔ 用"少条数 + 大 payload"而不是"多条数"：每条 append 都要 fsync，
#:    而内存峰值 ∝ payload × 条数 ⇒ 判别力不变但耗时降一个量级
#:    （实测 1200 条 × 1 KB 跑 H 组要 32s；400 条 × 4 KB 只要 7s）。
FAT = "y" * 4096


def build(root: Path, n: int, *, pattern=(0, 1, 2)) -> Path:
    """造一份带三类事件的账本。"""
    lp = root / "data" / "ledger.jsonl"
    led = Ledger(lp)
    for i in range(n):
        kind = ("eval.verdict", "memory.write", "harness.commit")[i % 3]
        p: Dict[str, Any] = {"i": i, "text": FAT}
        if kind == "eval.verdict":
            p.update(run_id=f"run-{i}", reward=0.5 + (i % 7) / 100,
                     passed=(i % 2 == 0), harness_id=f"h-{i}")
        elif kind == "memory.write":
            p.update(note_id=f"note-{i}", disposition="written")
        else:
            p["harness_id"] = f"h-{i}"
        led.append(kind, p, actor="test")
    return lp


def materialized_reads(lp: Path, limit: int = 200):
    """**物化**路径的等价读数（对照基准）。

    ⛔ 这里不是"抄一份旧实现"，而是**用公开入口**跑一遍（stats /
    consistency_report / memory_report 都仍然接受 list）。流式版与它
    逐字段相等，才说明流式版没改判据。
    """
    from collections import deque
    from aether.serve import _event_dict
    led = Ledger(lp)
    evs = led.events()
    return {
        "ledger": led.stats(events=evs),
        "events": [_event_dict(e) for e in deque(evs, maxlen=limit)],
        "memory": memory_report(lp.parent / "memory.json", ledger_events=evs),
    }


def peak_of(fn, *a, **kw) -> int:
    tracemalloc.start()
    tracemalloc.reset_peak()
    fn(*a, **kw)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak


# ═══ A 组：`iter_events` 真的不物化 ══════════════════════════════
def group_a() -> None:
    import inspect
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 30)
        led = Ledger(lp)
        ck("A1 iter_events 是生成器（不物化）",
           inspect.isgenerator(led.iter_events()))
        ck("A2 events() == list(iter_events())（逐条同源）",
           [e.hash for e in led.events()]
           == [e.hash for e in Ledger(lp).iter_events()])
        ck("A3 空账本 iter_events 不炸",
           list(Ledger(root / "data" / "nope.jsonl").iter_events()) == [])


# ═══ B 组：流式 == 物化（逐字段相等，含断链/坏行）════════════════
def group_b() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 60)
        got = _collect(root, limit=20)
        want = materialized_reads(lp, limit=20)

        ck("B1 ledger 读数逐字段相等",
           got["ledger"] == want["ledger"],
           f"stream={got['ledger']}\n        bulk={want['ledger']}")
        ck("B2 events 尾部逐条相等",
           got["events"] == want["events"])
        ck("B3 memory 逐字段相等（含对账结论）",
           got["memory"] == want["memory"],
           f"stream={got['memory']}\n        bulk={want['memory']}")

        # ── 坏行 + 断链 ──
        raw = lp.read_text(encoding="utf-8").splitlines()
        lp.write_text("\n".join(raw[:10] + ["{ 这不是 JSON"] + raw[10:20]
                                + [""] + raw[20:]) + "\n", encoding="utf-8")
        g2 = _collect(root, limit=20)
        w2 = materialized_reads(lp, limit=20)
        ck("B4 有坏行时两者仍相等（坏行同源跳过）",
           g2["ledger"] == w2["ledger"] and g2["events"] == w2["events"],
           f"stream={g2['ledger']}\n        bulk={w2['ledger']}")

        # 篡改中间一条（断链）
        lines = lp.read_text(encoding="utf-8").splitlines()
        lines[5] = lines[5].replace('"i":5', '"i":9999')
        lp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        g3 = _collect(root, limit=20)
        w3 = materialized_reads(lp, limit=20)
        ck("B5 断链时两者仍相等（chain_ok 都 False）",
           g3["ledger"] == w3["ledger"],
           f"stream={g3['ledger']}\n        bulk={w3['ledger']}")
        ck("B6 断链被抓到（不是恒绿）",
           g3["ledger"]["chain_ok"] is False
           and g3["ledger"]["first_bad_seq"] == w3["ledger"]["first_bad_seq"])


# ═══ C 组：语义保持（合并遍历不得改变读数）══════════════════════
def group_c() -> None:
    """⛔ 这是本轮最危险的改动点：旧 `stats()` 是**两遍**（by_kind 一遍 +
    verify_chain 一遍），流式版合并成一遍。若断链时图省事跟着 break，
    `count` / `by_kind` 就只统计到断点 —— 而断链恰恰是最需要完整读数
    的时刻。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 60)
        lines = lp.read_text(encoding="utf-8").splitlines()
        lines[5] = lines[5].replace('"i":5', '"i":9999')
        lp.write_text("\n".join(lines) + "\n", encoding="utf-8")

        led = Ledger(lp)
        st = led.stats()
        n_all = len(led.events())
        ck("C1 断链时 count 仍是**全量**条数（不是断链位置）",
           st["count"] == n_all, f"count={st['count']} 实际={n_all}")
        ck("C2 断链时 by_kind 仍是全量",
           sum(st["by_kind"].values()) == n_all,
           f"by_kind 合计={sum(st['by_kind'].values())} 实际={n_all}")
        ck("C3 断链时 verify_chain 与 stats 结论同源",
           led.verify_chain()[1] == st["first_bad_seq"]
           and led.verify_chain()[0] == st["chain_ok"])

        agg = led.stats_agg()
        for ev in led.iter_events():
            agg.feed(ev)
        ck("C4 stats_agg 与 stats 逐字段相等",
           agg.result() == st)


# ═══ D 组：内存峰值（本轮唯一有判别力的断言）════════════════════
def group_d() -> None:
    """判据：`_collect` 的峰值必须显著低于"光是物化整份账本"的峰值。

    ⛔ 为什么只认这个：五个消费者共享一条流这件事，**在外 observable 的
    唯一痕迹就是内存**。事件数、文件 open 次数、`iter_events` 调用次数
    在物化版里全都一样（物化版也只调一次 `iter_events`）⇒ 那些断言是
    恒绿护栏，不是判据。
    """
    n = 300
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, n)
        size = lp.stat().st_size

        def hold():
            evs = Ledger(lp).events()
            return len(evs)

        peak_hold = peak_of(hold)
        #: ⛔ 窗口 50 条 ≪ 300 条总数：否则"保留尾部"自己就是峰值主体
        cnt = peak_of(_collect, root, 50)
        mb = 1024 * 1024
        print(f"       [实测] 账本 {n} 条 / {size/mb:.2f} MB；"
              f"物化持有 {peak_hold/mb:.2f} MB，_collect {cnt/mb:.2f} MB")
        ck("D1 _collect 峰值 < 物化持有的 60%",
           cnt < peak_hold * 0.6,
           f"_collect={cnt/mb:.2f}MB 物化={peak_hold/mb:.2f}MB "
           f"比值={cnt/peak_hold:.2f}")
        ck("D2 _collect 峰值 < 账本字节数（不放大）",
           cnt < size,
           f"_collect={cnt/mb:.2f}MB 账本={size/mb:.2f}MB")

        # 规模翻倍的增长率：真流式下峰值不该跟着账本线性放大
        with tempfile.TemporaryDirectory() as td2:
            root2 = Path(td2)
            lp2 = build(root2, n * 2)
            size2 = lp2.stat().st_size
            cnt2 = peak_of(_collect, root2, 50)
            print(f"       [实测] {n*2} 条 / {size2/mb:.2f} MB；"
                  f"_collect {cnt2/mb:.2f} MB "
                  f"（规模 ×2 ⇒ 峰值 ×{cnt2/cnt:.2f}）")
            ck("D3 规模翻倍时峰值增长 < 2×（尾部窗口不随账本变长）",
               cnt2 < cnt * 2.0, f"{cnt/mb:.2f} → {cnt2/mb:.2f} MB")


# ═══ E 组：聚合器与公开入口共用逻辑（不许有第二份判据）══════════
def group_e() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 40)
        led = Ledger(lp)
        evs = led.events()

        a1 = led.stats_agg()
        for e in evs:
            a1.feed(e)
        ck("E1 LedgerStatsAgg == stats(events=)",
           a1.result() == led.stats(events=evs))

        # 记忆对账：agg 与 memory_report 必须同结论
        mp = root / "data" / "memory.json"
        mp.write_text('{"dim":64,"embedder_id":"hash","notes":[{"note_id":"note-1"}]}',
                      encoding="utf-8")
        a2 = MemoryLedgerAgg()
        for e in evs:
            a2.feed(e)
        out, fids = _memory_file_report(mp)
        ck("E2 MemoryLedgerAgg == memory_report(ledger_events=)",
           {**out, **a2.result(fids)} == memory_report(mp, ledger_events=evs))

        a3 = TailWindowAgg(limit=5)
        for e in evs:
            a3.feed(e)
        ck("E3 TailWindowAgg 只留最后 limit 条",
           len(a3.result()) == 5
           and [d["seq"] for d in a3.result()] == [e.seq for e in evs[-5:]])
        ck("E4 TailWindowAgg(limit=0) 不留事件",
           TailWindowAgg(limit=0).result() == [])


# ═══ F 组：一致性判据流式化后不失真 ═════════════════════════════
def group_f() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 60)
        db = root / "data" / "aether.db"
        s = Store(db)
        try:
            evs = Ledger(lp).events()
            s.rebuild_from_ledger(evs)
            agg = s.consistency_agg()
            for e in evs:
                agg.feed(e)
            streamed = agg.result()
            bulk = s.consistency_report(evs)
            ck("F1 consistency_agg == consistency_report（逐字段）",
               {k: v for k, v in streamed.items() if k != "duplicate_run_ids"}
               == {k: v for k, v in bulk.items() if k != "duplicate_run_ids"},
               f"stream={streamed}\n        bulk={bulk}")
            ck("F2 重建后一致性 ok（投影 == 账本）",
               streamed["ok"] is True, str(streamed))

            # ── 撞键：同一 run_id 写两次 ──
            Ledger(lp).append("eval.verdict",
                              {"run_id": "run-0", "reward": 9.9,
                               "passed": False, "harness_id": "h-0"},
                              actor="test")
            evs2 = Ledger(lp).events()
            agg2 = s.consistency_agg()
            for e in evs2:
                agg2.feed(e)
            r2 = agg2.result()
            ck("F3 撞键被记账（不再静默覆盖）",
               r2["duplicate_run_ids"] == 1, str(r2))
            ck("F4 撞键不改变 keyed 计数语义（去重后仍算 1）",
               r2["ledger_runs_keyed"] == len(
                   {e.payload.get("run_id") for e in evs2
                    if e.kind == "eval.verdict" and e.payload.get("run_id")}),
               str(r2))

            # ── 投影丢历史：删掉一批 run ──
            s.conn.execute("DELETE FROM runs WHERE run_id LIKE 'run-1%'")
            s.conn.commit()
            agg3 = s.consistency_agg()
            for e in evs2:
                agg3.feed(e)
            r3 = agg3.result()
            ck("F5 投影丢历史 ⇒ ok=False 且 missing_count > 0",
               r3["ok"] is False and r3["missing_count"] > 0, str(r3))
        finally:
            s.close()


# ═══ G 组：`_collect` 的整体形状不变 ════════════════════════════
def group_g() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, 30)
        got = _collect(root, limit=5)
        #: ⛔ `snapshots_total` / `snapshots_truncated` 是**新增**键（切了
        #:    就要说切了），不是删除或改名 —— 新增键对既有消费者是安全的。
        ck("G1 键集合与旧版一致（只多不少）",
           set(got) == {"ledger", "events", "store", "consistency", "runs",
                        "snapshots", "snapshots_total",
                        "snapshots_truncated", "memory", "version"},
           str(sorted(got)))
        ck("G1b 无投影库时也带 total/truncated（不得缺键）",
           got["snapshots_total"] == 0
           and got["snapshots_truncated"] is False, str(got.get("snapshots")))
        ck("G2 events 不超过 limit",
           len(got["events"]) == 5, str(len(got["events"])))
        ck("G3 无投影库时 consistency 为空 dict（不造假读数）",
           got["consistency"] == {}, str(got["consistency"]))
        ck("G4 无记忆库时 memory.present=False 且**不做**对账",
           got["memory"]["present"] is False
           and "ledger_trace" not in got["memory"], str(got["memory"]))


# ═══ H 组：另一个端点也必须流式（只修一半等于没修）══════════════
def group_h() -> None:
    """/api/events 是**同形态**的问题：`led.replay()` 物化全量再切尾部。

    ⛔ 上一轮只把 `/api/status` 流式化了；这里为了取尾部 200 条，把
    1533 条全量解析**两遍**（列表推导再造一份）⇒ 峰值 6.79 MB。
    """
    from collections import deque
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 400)
        led = Ledger(lp)

        #: ⛔ 窗口必须**远小于**总条数：否则"保留尾部"本身就是峰值主体，
        #:    流式的收益被自己的响应体吃掉（实测 400 条取尾部 200 条只有 1.9×，
        #:    取尾部 50 条是 8×）。
        old = [_event_dict(e)
               for e in deque(led.replay(), maxlen=50)]
        agg = TailWindowAgg(50)
        for e in led.iter_replay():
            agg.feed(e)
        new = agg.result()
        ck("H1 iter_replay == replay 的尾部（逐条相等）", old == new)

        a2 = TailWindowAgg(10)
        for e in led.iter_replay(kinds=["memory.write"]):
            a2.feed(e)
        ck("H2 iter_replay(kinds=) 过滤正确",
           [d["kind"] for d in a2.result()] == ["memory.write"] * 10)
        a3 = TailWindowAgg(1000)
        for e in led.iter_replay(since_seq=300):
            a3.feed(e)
        ck("H3 iter_replay(since_seq=) 过滤正确",
           all(d["seq"] >= 300 for d in a3.result())
           and len(a3.result()) == 400 - 299)

        peak_old = peak_of(lambda: deque(Ledger(lp).replay(), maxlen=50))
        peak_new = peak_of(lambda: _drain(lp))
        mb = 1024 * 1024
        print(f"       [实测] /api/events 物化 {peak_old/mb:.2f} MB → "
              f"流式 {peak_new/mb:.2f} MB（{peak_old/peak_new:.1f}×）")
        ck("H4 /api/events 流式峰值 < 物化的 50%",
           peak_new < peak_old * 0.5,
           f"{peak_new/mb:.2f} vs {peak_old/mb:.2f} MB")

        #: ⛔⛔ **H4 测的是组件，不是端点**。把 `do_GET` 里那两行退回
        #:    `replay()` 物化后，H1–H4 **依然全绿**（实测 34/34）—— 因为
        #:    没人真的走到那条代码。必须打真 HTTP + 计数"物化入口被调用
        #:    几次"，这才是"端点走的是哪条路"的可证伪形式。
        with tempfile.TemporaryDirectory() as td3:
            r3 = Path(td3)
            build(r3, 60)
            import aether.serve as S
            import aether.ledger as LG
            from http.server import ThreadingHTTPServer
            import http.client, threading
            S.Handler.root = r3
            S.Handler.console_dir = None
            srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()

            calls = {"replay": 0, "iter_replay": 0}
            _o_r, _o_ir = LG.Ledger.replay, LG.Ledger.iter_replay

            def _r(self, *a, **k):
                calls["replay"] += 1
                return _o_r(self, *a, **k)

            def _ir(self, *a, **k):
                calls["iter_replay"] += 1
                return _o_ir(self, *a, **k)
            LG.Ledger.replay, LG.Ledger.iter_replay = _r, _ir
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request("GET", "/api/events?limit=2")
                resp = c.getresponse()
                body = json.loads(resp.read().decode("utf-8"))
                c.close()
                ck("H5 端点真的走流式：replay() 调用 0 次、iter_replay 1 次",
                   calls["replay"] == 0 and calls["iter_replay"] == 1,
                   f"replay={calls['replay']} iter_replay={calls['iter_replay']}")
                ck("H6 且功能正确（返回 ≤ limit 条事件）",
                   resp.status == 200 and len(body.get("events", [])) == 2,
                   f"{resp.status} {len(body.get('events', []))}")
            finally:
                LG.Ledger.replay, LG.Ledger.iter_replay = _o_r, _o_ir
                srv.shutdown()
                srv.server_close()


def _drain(lp: Path) -> List[Dict[str, Any]]:
    """模拟 `/api/events` 的流式取尾部。"""
    agg = TailWindowAgg(50)
    for e in Ledger(lp).iter_replay():
        agg.feed(e)
    return agg.result()


def main() -> int:
    print("契约测试：一次磁盘遍历（streaming）")
    for name, fn in (("A 不物化", group_a), ("B 流式==物化", group_b),
                     ("C 语义保持", group_c), ("D 内存峰值", group_d),
                     ("E 聚合器同源", group_e), ("F 一致性不失真", group_f),
                     ("G 响应形状", group_g), ("H 另一个端点", group_h)):
        print(f"\n{name}")
        fn()
    print(f"\n{N_CK - len(FAILS)}/{N_CK} 通过")
    if FAILS:
        print("失败：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
