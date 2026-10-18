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

from aether.evolution import EvolutionLoop                # noqa: E402
from aether.ledger import Ledger, LedgerStatsAgg          # noqa: E402
from aether.serve import (                                 # noqa: E402
    MemoryLedgerAgg, TailWindowAgg, _collect, _event_dict,
    _memory_file_report, memory_report)
from aether.store import Store                             # noqa: E402

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

#: ⛔⛔ **必须独占执行**：这份契约测的是**内存峰值**与**遍历次数**
#:    （`tracemalloc` + 构造大对象）。`tracemalloc` 本身 CPU 开销极大，
#:    与别的契约并发 ⇒ 墙钟时间被按并发度放大 ⇒ 实测由 135s 放大到 >600s
#:    超时，且即便不超时，量到的时序也不可信。
#:    ⇒ `aether check` 会把它排进串行段，不与任何契约同时跑。
EXCLUSIVE = True

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


# ── I 组：CLI 也是"读数入口"，而且它从未被数过遍数 ─────────────────
def _legacy_binary_deadend(ledger: Any) -> Dict[str, int]:
    """**第 14 轮的二元实现**（有 bug 的那版）—— 只当反证物，不作 oracle。

    ⛔ 逐字保留（倒序 + `done` 集合），不许"顺手改成更好懂的写法"。
    它的语义是**二元**的：曾 generalize ⇒ 0，否则 ⇒ 全部条数。
    ⛔⛔ 2026-10-13 第 17 轮已把生产改成"连续"语义 ⇒ 这个函数**不再**
    是对拍基准（对拍用 `_naive_streak`）。它现在的唯一用途是反证
    "新语义确实不是旧的二元语义"（J2c）—— 留着它，改回去的人会立刻红。
    """
    out: Dict[str, int] = {}
    done: set = set()
    for ev in reversed(ledger.events()):
        if ev.kind != "gen.probe":
            continue
        p = ev.payload or {}
        key = f"{p.get('capsule', '')}|{p.get('env_pair', 'unknown')}"
        if key in done:
            continue
        if str(p.get("verdict")) == "generalize":
            out[key] = 0
            done.add(key)
        else:
            out[key] = out.get(key, 0) + 1
    return out


#: ⛔ oracle 侧**独立表达**的「不可判别」集合（见 `_naive_streak` 的注释：
#:    照抄生产常量会让对拍失去独立性）。漂移由 `J1b` 断言兜住。
_ORACLE_NO_EVIDENCE = frozenset({"inconclusive"})


def _naive_streak(ledger: Any) -> Dict[str, int]:
    """**朴素"连续"实现** —— 当 oracle 用，不参与生产。

    ⛔ 算法必须**独立于**生产实现，否则对拍是恒真的：
        · 生产：正序流式遍历 + 增量缓存，维护 `streak`（遇 G 归零 / 否则 +1）；
        · 这里：**倒序**从末尾往前数，遇到第一条 `generalize` 就停。
      两者算法不同（一个累加、一个回看），同值才说明语义真的是"连续"。

    ⛔ 它同时充当 J3 的"物化版"峰值基线（`ledger.events()` 物化整份）。
      换成 `_legacy_binary_deadend` 不行 —— 语义不同，值对不上就没法
      把"峰值降了"和"值没变"放在同一条断言里。
    """
    out: Dict[str, int] = {}
    #: ⛔ "停止标记"必须是**独立集合**：若用 `key in out` 判断，第一次赋值
    #:    后它就为真 ⇒ 第二次遇到同一 key 会被 continue 掉 ⇒ 非 G 序列永远
    #:    只数到 1（我第一版就写错了，对拍却仍"绿" —— 因为两边都不对）。
    stopped: set = set()
    for ev in reversed(ledger.events()):
        if ev.kind != "gen.probe":
            continue
        p = ev.payload or {}
        key = f"{p.get('capsule', '')}|{p.get('env_pair', 'unknown')}"
        if key in stopped:
            continue
        v = str(p.get("verdict"))
        #: ⛔ r59：**不可判别**的裁决**不参与**这条账（第 11 条：不可判别
        #:    ≠ 有罪）。倒序回看时它相当于**这条事件不存在** ⇒ 跳过继续
        #:   往前数（生产实现是"不动 streak"，两者等价）。
        #:   ⛔ 这里**刻意硬编码**而不是 import `NO_EVIDENCE_VERDICTS`：
        #:      oracle 只能照抄**定义**，照抄被测对象的常量就失去了独立性
        #:      ⇒ 改生产常量时两边一起错、对拍恒绿。独立性由下面的
        #:      J1b 断言（两边集合必须相等）来兜住漂移。
        if v in _ORACLE_NO_EVIDENCE:
            continue
        if v == "generalize":
            #: ⛔⛔ **停止，但不要清零**！倒序已经数到的那 k 条"末尾连续
            #:    被拦"是真实存在的：序列 `[G, 拦, 拦]` 的连续数是 **2**，
            #:    不是 0。写成 `out[key] = 0`（照抄旧二元实现的写法）就把
            #:    oracle 自己也变成二元的 ⇒ 对拍拿一个错的基准去比，
            #:    J1 实测 **24/40 组假红**（我第一版就踩了）。
            #:    ⇒ oracle 不能照抄被测对象的控制流结构，只能照抄**定义**。
            out[key] = out.get(key, 0)
            stopped.add(key)
        else:
            out[key] = out.get(key, 0) + 1
    return out


def group_i() -> None:
    print("  （I 组：CLI `status` 不得把账本问三遍）")
    import argparse
    import contextlib
    import io
    import random

    from aether import cli as _cli
    from aether import ledger as _LG

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        lp = build(root, 200)

        # I1. 遍数：一次 cmd_status 只准解析账本一遍
        real_loads = _LG.json.loads
        cnt = {"n": 0}

        def _counting_loads(s, *a, **k):
            cnt["n"] += 1
            return real_loads(s, *a, **k)

        _LG.json.loads = _counting_loads
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(io.StringIO()):
                rc = _cli.cmd_status(argparse.Namespace(root=str(root)))
        finally:
            _LG.json.loads = real_loads
        n_ev = len(Ledger(lp).events())
        ck("I1 cmd_status 只解析账本一遍（旧实现实测 3.01 遍）",
           cnt["n"] <= n_ev * 1.05,
           f"解析 {cnt['n']} 次 / 账本 {n_ev} 条 = {cnt['n']/n_ev:.2f} 遍")

        # I2. 端点/命令走的是哪条路：物化入口 `events()` 必须 0 次
        old_events = Ledger.events
        n_events = {"n": 0}
        old_iter = Ledger.iter_events
        n_iter = {"n": 0}

        def _spy_events(self):
            n_events["n"] += 1
            return old_events(self)

        def _spy_iter(self):
            n_iter["n"] += 1
            return old_iter(self)

        Ledger.events = _spy_events
        Ledger.iter_events = _spy_iter
        try:
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                _cli.cmd_status(argparse.Namespace(root=str(root)))
        finally:
            Ledger.events = old_events
            Ledger.iter_events = old_iter
        #: ⛔ 与 H5 同形：断言的是"走了哪条路"，不是"组件对不对"。
        #:    没有这条，I 组全是"测组件"—— 把 cmd_status 退回物化也全绿。
        ck("I2 cmd_status 走 iter_events（物化入口 events() 调用 0 次）",
           n_events["n"] == 0 and n_iter["n"] >= 1,
           f"events()={n_events['n']} iter_events()={n_iter['n']}")

        # I3. 三条判据同源：cmd_status 的读数 == _collect 的读数
        #: ⛔ CLI 与 HTTP 是**两个入口**，各自算一套就是两套判据 ——
        #:    "控制台说一致、接口说不一致"这种事必须不可能发生。
        #:    ⛔ 投影库要先存在，否则 CLI 会跳过 consistency 分支。
        _d = Store(root / "data" / "aether.db")
        try:
            _d.rebuild_from_ledger(Ledger(lp).events())
        finally:
            _d.close()
        got = _collect(root, limit=200)
        with contextlib.redirect_stdout(io.StringIO()) as b2, \
                contextlib.redirect_stderr(io.StringIO()):
            rc = _cli.cmd_status(argparse.Namespace(root=str(root)))
        #: 从 CLI 的 stdout 里取回它打印的各个 JSON 段（`indent=2` 逐段输出）
        dec = json.JSONDecoder()
        parsed: List[Any] = []
        s, i = b2.getvalue(), 0
        while i < len(s):
            while i < len(s) and s[i] in " \n\t\r":
                i += 1
            if i >= len(s) or s[i] != "{":
                break
            try:
                obj, j = dec.raw_decode(s, i)
            except ValueError:
                break
            parsed.append(obj)
            i = j
        cli_led = next((p["ledger"] for p in parsed if "ledger" in p), None)
        cli_con = next((p["consistency"] for p in parsed
                        if "consistency" in p), None)
        ck("I3 CLI 与 _collect 的账本读数逐字段相同（判据只有一份）",
           cli_led is not None and
           cli_led["count"] == got["ledger"]["count"] and
           cli_led["chain_ok"] == got["ledger"]["chain_ok"] and
           cli_led["by_kind"] == got["ledger"]["by_kind"],
           f"cli={cli_led} vs collect={got['ledger']}")
        ck("I4 CLI 与 _collect 的一致性读数逐字段相同",
           cli_con is not None and cli_con.get("ok") == got["consistency"].get("ok")
           and cli_con.get("missing_count") == got["consistency"].get("missing_count")
           and cli_con.get("drift_count") == got["consistency"].get("drift_count"),
           f"cli ok={cli_con and cli_con.get('ok')} vs "
           f"collect ok={got['consistency'].get('ok')}")

    # ── J 组：热路径 `_deadend_map` 每轮物化整份账本 ────────────────
    print("  （J 组：_deadend_map 不得为算 7 个 key 而物化整份账本）")
    from aether.evolution import EvolutionLoop

    def new_deadend(ledger: Any) -> Dict[str, int]:
        loop = EvolutionLoop.__new__(EvolutionLoop)
        loop.ledger = ledger
        return EvolutionLoop._deadend_map(loop)

    random.seed(7)
    verdicts = ["generalize", "overfit", "inconclusive", None, "GENERALIZE"]
    bad = 0
    #: ⛔ 对拍看的是**语义**不是内存 ⇒ 用小账本（每条 append 都 fsync）
    for _ in range(40):
        with tempfile.TemporaryDirectory() as td:
            led = Ledger(Path(td) / "l.jsonl")
            for i in range(random.randint(0, 24)):
                led.append("gen.probe", {
                    "capsule": random.choice(["a", "b", "c", ""]),
                    "env_pair": random.choice(["p1", "p2", "unknown"]),
                    "verdict": random.choice(verdicts)})
                if random.random() < 0.3:
                    led.append("other.kind", {"x": i})
            if _naive_streak(led) != new_deadend(led):
                bad += 1
    ck("J1 随机对拍 40 组：与**独立算法**（倒序回看）逐 key 相等"
       "（连续语义的正序累加实现）",
       bad == 0, f"{bad} 组不一致")

    #: ⛔ J1b：oracle 硬编码了「不可判别」集合以保持独立性，那就必须有一条
    #:    断言防止它和生产常量**各自漂移**（否则 J1 会在两边一起改错时恒绿）。
    from aether.evolution import EvolutionLoop as _EL
    ck("J1b oracle 的「不可判别」集合与生产常量**逐字相同**（防各自漂移）",
       set(_ORACLE_NO_EVIDENCE) == set(_EL.NO_EVIDENCE_VERDICTS),
       f"oracle={sorted(_ORACLE_NO_EVIDENCE)} "
       f"生产={sorted(_EL.NO_EVIDENCE_VERDICTS)}")
    #: ⛔ 对拍组里**必须**真的出现 inconclusive，否则 J1 是在对拍一个
    #:   永远不触发的分支（第 100 条：判据集与变异空间要相交）。
    ck("J1c 对拍用的 verdict 池里含 inconclusive（否则 J1 恒真）",
       "inconclusive" in verdicts, f"{verdicts}")
    #: ⛔ 补一条**定向**的：不可判别不许打断连续（它是"没判"，不是成功）。
    def _streak_with(seq):
        with tempfile.TemporaryDirectory() as td:
            led = Ledger(Path(td) / "l.jsonl")
            for v in seq:
                led.append("gen.probe", {"capsule": "c", "env_pair": "p",
                                         "verdict": v})
            return new_deadend(led).get("c|p", "MISSING")
    ck("J1d [拦,不可判别,拦] ⇒ 2（不可判别不是成功，不许清零别人的账）",
       _streak_with(("blocked", "inconclusive", "blocked")) == 2,
       f"实得 {_streak_with(('blocked','inconclusive','blocked'))}")
    ck("J1e [拦,拦,不可判别] ⇒ 2（末尾的不可判别不加不算）",
       _streak_with(("blocked", "blocked", "inconclusive")) == 2,
       f"实得 {_streak_with(('blocked','blocked','inconclusive'))}")

    #: ⛔⛔ J2/J2b/J2c 三条一起把"连续"语义钉死 —— 三个方向都得挡住：
    #:     二元（曾成功⇒0）/ 累计（不归零）/ 方向搞反。
    #:     拍板依据见 `evolution._deadend_map` docstring 里的交叉表。
    def _streak_of(seq) -> int:
        with tempfile.TemporaryDirectory() as td:
            led = Ledger(Path(td) / "l.jsonl")
            for v in seq:
                led.append("gen.probe", {"capsule": "c", "env_pair": "p",
                                         "verdict": v})
            return new_deadend(led).get("c|p", "MISSING")

    ck("J2 【已修】[G,拦,拦] ⇒ 2（旧二元实现给 0 ⇒ 成功过的候选永不冷却）",
       _streak_of(("generalize", "blocked", "blocked")) == 2,
       f"实得 {_streak_of(('generalize', 'blocked', 'blocked'))}")

    ck("J2b 遇 generalize **归零**（不是累计）：[拦,拦,G,拦,拦] ⇒ 2 而非 4",
       _streak_of(("blocked", "blocked", "generalize", "blocked",
                   "blocked")) == 2,
       f"实得 {_streak_of(('blocked','blocked','generalize','blocked','blocked'))}")

    #: ⛔ 这条是"修好了"的**可观测证据**：同一个序列在旧二元实现下不冷却、
    #:    在新语义下冷却。只断言值（J2）不证明判据面变了，得断言到阈值。
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "l.jsonl")
        for v in ("generalize", "blocked", "blocked", "blocked"):
            led.append("gen.probe", {"capsule": "c", "env_pair": "p",
                                     "verdict": v})
        new_v = new_deadend(led).get("c|p")
        old_v = _legacy_binary_deadend(led).get("c|p")
        ck("J2c 修复面可观测：[G,拦,拦,拦] 旧语义不冷却(0) / 新语义冷却(≥3)",
           old_v == 0 and new_v >= 3,
           f"旧={old_v} 新={new_v}")

    ck("J2d 末尾停在 generalize ⇒ 0（成功过就重新给机会，不许记旧账）",
       _streak_of(("blocked", "blocked", "generalize")) == 0,
       f"实得 {_streak_of(('blocked','blocked','generalize'))}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        led = Ledger(root / "data" / "l.jsonl")
        #: ⛔ 每条 append 都 fsync ⇒ 条数控制在"峰值信号够用"的下限
        for i in range(80):
            led.append("gen.probe", {"capsule": f"c{i % 5}", "env_pair": "p",
                                     "verdict": "blocked", "pad": FAT[:512]})
            led.append("eval.verdict", {"run_id": f"r{i}", "reward": 0.1,
                                        "passed": False, "pad": FAT})

        def peak_of(fn):
            tracemalloc.start()
            tracemalloc.reset_peak()
            r = fn()
            p = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            return p, r

        p_old, m_old = peak_of(lambda: _naive_streak(led))
        p_new, m_new = peak_of(lambda: new_deadend(led))
        mb = 1024 * 1024
        ck("J3 流式版峰值 < 物化版的 10%（只换实现，值仍逐 key 相等）",
           m_old == m_new and p_new < p_old * 0.10,
           f"值相等={m_old == m_new} 峰值 {p_new/mb:.2f} vs {p_old/mb:.2f} MB")

        #: ⛔ 与 I2/H5 同形：断言"走的是 iter_events 而不是 events()"
        oe, oi = Ledger.events, Ledger.iter_events
        ne, ni = {"n": 0}, {"n": 0}
        Ledger.events = lambda self: (ne.__setitem__("n", ne["n"] + 1), oe(self))[1]
        #: ⛔ 替身必须**透传** `*a, **k`：增量版会传 `since_offset=` /
        #:    `progress=`，写死 `lambda self:` 会 TypeError —— 那是替身
        #:    跟不上接口，不是被测代码错了（2026-10-13 回归里真踩到）。
        Ledger.iter_events = lambda self, *a, **k: (
            ni.__setitem__("n", ni["n"] + 1), oi(self, *a, **k))[1]
        try:
            new_deadend(Ledger(root / "data" / "l.jsonl"))
        finally:
            Ledger.events, Ledger.iter_events = oe, oi
        ck("J4 _deadend_map 走 iter_events（物化入口 events() 调用 0 次）",
           ne["n"] == 0 and ni["n"] == 1,
           f"events()={ne['n']} iter_events()={ni['n']}")

    # ── K 组：`Ledger.tail(n)` 不得为取尾部 n 条而物化整份账本 ────────
    print("  （K 组：tail(n) 只留窗口，不物化全量）")
    with tempfile.TemporaryDirectory() as td:
        led = Ledger(Path(td) / "l.jsonl")
        for i in range(60):
            led.append("eval.verdict", {"run_id": f"r{i}", "pad": FAT})
        tracemalloc.start()
        tracemalloc.reset_peak()
        t = led.tail(5)
        p_tail = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        tracemalloc.start()
        tracemalloc.reset_peak()
        evs = led.events()          #: 物化全量（对照基准）
        p_hold = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        ck("K1 tail(5) 结果与 events()[-5:] 相同",
           t == evs[-5:], f"{len(t)} 条")
        #: ⛔ 窗口必须**远小于**总条数：60 条取 10 条时比值 27.6%，取 5 条时
        #:    ~17% ⇒ 判别力来自"窗口/总量"这个比值，不是条数本身。
        ck("K2 tail(5) 峰值 < 物化全量的 25%",
           p_tail < p_hold * 0.25,
           f"{p_tail/mb:.2f} vs {p_hold/mb:.2f} MB")
        ck("K3 tail(0) / 负 n ⇒ 空列表（绝不退化成不封顶）",
           led.tail(0) == [] and led.tail(-5) == [],
           f"tail(0)={len(led.tail(0))} tail(-5)={len(led.tail(-5))}")


def group_l() -> None:
    """判据侧不得有**隐性上限** —— 一致性判据必须覆盖全量。

    ⛔ 这条守的不是性能，是"判据不能静默少看"：原本 `run_digests()` /
    `run_ids()` 默认 `limit=100000`，而一致性判据正是用**默认**调用的 ⇒
    表超过 10 万行后判据只比前 10 万行，却**照旧报"一致"**。注释当时还写着
    "不传 limit 即可全量" —— 注释与实现矛盾，比没有注释更危险。
    """
    print("  （L 组：判据侧不得有隐性上限）")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        led = Ledger(root / "l.jsonl")
        for i in range(5):
            led.append("eval.verdict", {"run_id": f"r{i}", "reward": 0.5,
                                        "passed": True, "harness_id": "h"})
        s = Store(root / "a.db")
        try:
            s.rebuild_from_ledger(led.events())
            n = s.conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
            ck("L1 判据侧 run_digests()/run_ids() 默认 = **全量**"
               "（旧默认 100000 会在 10 万行后静默截断）",
               n == 5 and len(s.run_digests()) == n and len(s.run_ids()) == n,
               f"COUNT={n} run_digests={len(s.run_digests())} "
               f"run_ids={len(s.run_ids())}")
            ck("L1b 显式传 limit 时上限仍生效（2 ⇒ 2 条；负 ⇒ 0 条）",
               len(s.run_digests(limit=2)) == 2
               and len(s.run_digests(limit=-1)) == 0,
               f"limit=2 → {len(s.run_digests(limit=2))}, "
               f"limit=-1 → {len(s.run_digests(limit=-1))}")

            #: ⛔⛔ 断言的是**签名默认值**，不是"调用时传了什么"。
            #:    我第一版写的是后者 —— 注入"默认改回 100000"的事故版后
            #:    **L 组 5 条全绿**：调用方本来就不传 limit，spy 永远记到
            #:    None ⇒ 恒真断言（与 V5"原语无 success 键"同形）。
            #:    ⇒ 想知道"默认有没有上限"，就得去看那个默认值本身。
            import inspect
            d_ids = inspect.signature(Store.run_ids).parameters["limit"].default
            d_dig = inspect.signature(Store.run_digests).parameters["limit"].default
            ck("L2 run_ids/run_digests 的 limit **默认值是 None**"
               "（判据不得有隐性上限；这是真判据）",
               d_ids is None and d_dig is None,
               f"run_ids 默认={d_ids!r} run_digests 默认={d_dig!r}")

            #: 护栏：判据调用时也不该自己传一个上限
            seen: List[Any] = []
            orig = Store.run_digests

            def _spy(self, limit=None):
                seen.append(limit)
                return orig(self, limit)

            Store.run_digests = _spy
            try:
                agg = s.consistency_agg()
                for ev in led.iter_events():
                    agg.feed(ev)
                rep = agg.result()
            finally:
                Store.run_digests = orig
            ck("L2b 护栏：一致性判据调用 run_digests 时不自带上限",
               seen == [None], f"实得 {seen}")

            #: ⛔ 反证分**两个方向**：`run_ids` 管键级（漏 ⇒ missing 假红），
            #:    `run_digests` 管字段级（漏 ⇒ drift 假绿）。只证明一个方向
            #:    会让人误判风险的朝向。
            orig_ids = Store.run_ids
            Store.run_ids = lambda self, limit=None: orig_ids(self)[:1]
            try:
                agg2 = s.consistency_agg()
                for ev in led.iter_events():
                    agg2.feed(ev)
                rep2 = agg2.result()
            finally:
                Store.run_ids = orig_ids
            ck("L3 反证·假红：键集合被截断 ⇒ 投影里有的被算成 missing",
               rep["ok"] and rep2["missing_count"] > rep["missing_count"],
               f"全量 ok={rep['ok']} missing={rep['missing_count']} / "
               f"截断 ok={rep2['ok']} missing={rep2['missing_count']}")

            #: 造一处真的 drift（投影 reward 与账本不一致），再看截断能否检出
            s.conn.execute("UPDATE runs SET reward=0.9 WHERE run_id='r2'")
            s.conn.commit()
            agg3 = s.consistency_agg()
            for ev in led.iter_events():
                agg3.feed(ev)
            rep3 = agg3.result()
            Store.run_digests = lambda self, limit=None: dict(
                list(orig(self).items())[:1])
            try:
                agg4 = s.consistency_agg()
                for ev in led.iter_events():
                    agg4.feed(ev)
                rep4 = agg4.result()
            finally:
                Store.run_digests = orig
            ck("L4 反证·假绿：drift 真实存在时，字段级截断会**漏检**"
               "（这个方向更危险：判据会照样报 ok）",
               "r2" in rep3["drifted"] and rep4["drift_count"] == 0
               and rep4["ok"],
               f"全量 drifted={rep3['drifted']} / 截断 ok={rep4['ok']} "
               f"drift={rep4['drift_count']}")
        finally:
            s.close()


class _FixedProposer:
    """啥也不提的提议器（M 组只问死路账，跑不到 propose）。"""

    def __init__(self, caps):
        self.caps = caps

    def propose(self, weakness, harness, n=3):
        return list(self.caps)


def _mk_deadend_loop(ledger: Any) -> Any:
    """装一个**只用来问死路账**的 loop。

    ⛔ 刻意走**真实构造函数**：M7 要证明 `__init__` 真的初始化了增量状态。
    用 `EvolutionLoop.__new__` 手工塞属性会绕过这件事 —— 哪天有人删了
    `__init__` 里那两行，测试照样全绿而生产直接崩（假绿）。
    """
    from aether.evolution import EvolutionConfig, EvolutionLoop
    from aether.nongression import Baseline, FrozenTask, NonRegressionGate
    from aether.verifier import Verdict

    def runner(h, spec):
        return Verdict(passed=True, reward=1.0, evidence={}, error="")

    dev = [FrozenTask(task_id="d0", spec={"env": "A"})]
    frozen = [FrozenTask(task_id="fz", spec={"env": "A", "frozen": True})]
    cal = {"seed": 0}
    base = Baseline(caliber=cal, verdicts={}, harness_id="seed")
    #: ⛔ 本组只测"死路账是增量的"，显式关掉清理轴（同 D 组纪律）：
    #:    常设提案器每轮都会往账本里塞候选事件 ⇒ 那会让"扫了多少条"这个
    #:    读数被另一个轴推动，测到的就不再是死路账本身。
    return EvolutionLoop(ledger, NonRegressionGate(frozen, base), dev,
                         runner, _FixedProposer([]), caliber=cal,
                         config=EvolutionConfig(cleanup_enabled=False))


def group_m() -> None:
    """M 组：死路账**不得每轮全扫账本**（增量 + 失效可证）。

    ⛔ 上一轮（4b5db89）已经把 `_deadend_map` 从"物化整份"改成"流式遍历"，
       但它**每轮仍解析全账本**：账本 N 条 × 轮数 R ⇒ O(R×N)。本组守的是
       "只读新增"，以及"一旦不能证明前面的部分没变，就必须退回全量"。

    ⛔⛔ **判别力是注入事故版实测出来的，不是我以为的**（2026-10-13）：

       真判据（对应事故下确实转红）：
         M1  ← 事故「忽略 since_offset」→ 红灯 M1,M6
         M2b ← 事故「去掉链连续性校验」→ 红灯 M2b
         M6  ← 事故「去掉 no_new_but_touched 回退」→ 红灯 M6

       护栏（对应事故下**仍绿**，别拿它当战果）：
         · M2 —— 事故 B 下值**依然同值**：这次 rebuild 前后 payload 相同，
           统计值本来就不变。它守的是"值不错"，真正抓到"抱着旧缓存"的是
           **M2b**（失效必须被记账）。
         · M3 —— 事故 C（去掉 shrink 检查）下也红，但红的只是
           `reasons.shrunk` 计数：**值**是由 M6 那条兜底路径救回来的。
           ⇒ 单看 M3 会以为 shrink 检查在守正确性，其实它守的是可观测性。
         · M4a/M4b —— 事故 D（offset 记在解析前 ⇒ 半行也推进）**两条都绿**：
           半行只可能是文件最后一行，推进后要么链接不上、要么读不到新行，
           **两条兜底路径都会把它拉回全量** ⇒ 最坏是多扫一次，不会永久
           错位。⇒ M4 证明的是"最终仍读得到"，不是"offset 从没错过"。
         · M5 —— 随机对拍，任何实现漂移都会红（覆盖面，非判别力）。
         · M7 —— 证明 `__init__` 真的初始化了（防手塞属性的测试漏掉）。
    """
    print("  （M 组：死路账增量 —— 只读新增，且失效必须能证）")
    import os
    import random

    from aether.ledger import Ledger as _Led

    rng = random.Random(11)
    caps = ["a", "b", "c"]

    def _probe(led, cap=None, verdict=None, pair="p1"):
        return led.append("gen.probe", {
            "capsule": cap if cap is not None else rng.choice(caps),
            "env_pair": pair,
            "verdict": verdict or rng.choice(["generalize", "overfit",
                                              "blocked"])}, actor="t")

    # ── M1 真判据：追加场景下扫描条数 ≈ 账本总数，不随轮数线性增长 ──
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        led = _Led(p)
        loop = _mk_deadend_loop(led)
        rounds, per = 6, 40
        for _ in range(rounds):
            for _ in range(per):
                if rng.random() < 0.7:
                    _probe(led)
                else:
                    led.append("other.kind", {"x": rng.random()}, actor="t")
            loop._deadend_map()
        st = loop._deadend_stats
        total = rounds * per
        ck("M1 追加场景：6 轮只扫 ≈账本总量（旧版 = 轮数×总量 240 vs 1440）",
           st["scanned"] <= total * 1.1 and st["incremental"] >= rounds - 1,
           f"scanned={st['scanned']} total={total} inc={st['incremental']} "
           f"full={st['full']}")

    # ── M2 真判据：账本被 rebuild 重写 ⇒ 必须发现并回退全量 ──
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        led = _Led(p)
        loop = _mk_deadend_loop(led)
        for _ in range(30):
            _probe(led)
        loop._deadend_map()
        #: rebuild：重写整份（内容相同但 hash 重算、文件被重写）
        evs = _Led(p).events()
        p.write_text("", encoding="utf-8")
        led2 = _Led(p)
        for e in evs:
            led2.append(e.kind, dict(e.payload or {}), actor=e.actor)
        for _ in range(5):
            _probe(led2)
        got = loop._deadend_map()
        ck("M2 rebuild（重写整份）后仍与全量 oracle 同值",
           got == _naive_streak(_Led(p)),
           f"got={got} want={_naive_streak(_Led(p))} "
           f"reasons={loop._deadend_stats['reasons']}")
        ck("M2b rebuild 必须被**记为一次失效**（否则外部看不出增量白做）",
           loop._deadend_stats["invalidated"] >= 1,
           f"invalidated={loop._deadend_stats['invalidated']}")

    # ── M3 真判据：账本被 truncate 变短 ⇒ 偏移越界，必须回退 ──
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        led = _Led(p)
        loop = _mk_deadend_loop(led)
        for _ in range(40):
            _probe(led)
        loop._deadend_map()
        lines = p.read_text(encoding="utf-8").splitlines(True)[:10]
        p.write_text("".join(lines), encoding="utf-8")
        got = loop._deadend_map()
        ck("M3 truncate（文件变短）后仍与全量 oracle 同值",
           got == _naive_streak(_Led(p))
           and loop._deadend_stats["reasons"]["shrunk"] >= 1,
           f"got={got} want={_naive_streak(_Led(p))} "
           f"reasons={loop._deadend_stats['reasons']}")

    # ── M4 真判据：半行（并发 append 写到一半）不得污染 offset ──
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        led = _Led(p)
        loop = _mk_deadend_loop(led)
        for _ in range(10):
            _probe(led)
        loop._deadend_map()
        #: 半行：不带换行、JSON 不完整（另一个进程正在写）
        with p.open("a", encoding="utf-8") as f:
            #: ⛔ 半行必须**停在字符串值中间**（`"zz` 而不是 `"zz"`）：
            #:    写成一个"已经是合法 JSON 的前缀"的话，补全后反而变成
            #:    `"zz"z"` ⇒ 非法 JSON ⇒ 两条路径都读不到 ⇒ 断言恒绿。
            f.write('{"seq":9999,"kind":"gen.probe","payload":{"capsule":"zz')
        m_half = loop._deadend_map()
        ck("M4a 半行**不计入**死路账（解析失败的行不许当事件）",
           not any(k.startswith("zz|") for k in m_half), f"got={m_half}")
        #: 补全这一行
        with p.open("a", encoding="utf-8") as f:
            f.write('z","env_pair":"p9","verdict":"overfit"},'
                    '"prev_hash":"x","hash":"y"}\n')
        m_full = loop._deadend_map()
        ck("M4b 补全后**必须**读得到（offset 没被半行推过去 ⇒ 不永久错位）",
           any(k.startswith("zzz|p9") for k in m_full), f"got={m_full}")

    # ── M5 护栏：随机对拍（含 [G,拦,拦] 边界），增量中间多次问 ──
    bad = 0
    for _ in range(60):
        with tempfile.TemporaryDirectory(prefix="v6m_") as td:
            p = Path(td) / "l.jsonl"
            led = _Led(p)
            loop = _mk_deadend_loop(led)
            for v in ("generalize", "overfit", "overfit"):
                _probe(led, cap="a", verdict=v, pair="p1")
            for _ in range(rng.randint(0, 20)):
                _probe(led)
            for _ in range(3):
                loop._deadend_map()
                _probe(led)
            if loop._deadend_map() != _naive_streak(_Led(p)):
                bad += 1
    ck("M5 随机对拍 60 组：增量路径与全量 oracle **逐键同值**",
       bad == 0, f"不一致 {bad} 组")

    # ── M6 真判据：文件被写过却没有新行 ⇒ 不可证明 ⇒ 必须回退全量 ──
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        led = _Led(p)
        loop = _mk_deadend_loop(led)
        for _ in range(10):
            _probe(led)
        loop._deadend_map()
        before = loop._deadend_stats["full"]
        #: touch 文件（mtime 变、大小不变、无新行）
        st0 = os.stat(p)
        os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns + 10 ** 9))
        loop._deadend_map()
        ck("M6 无新行但文件被 touch ⇒ 无法证明缓存成立 ⇒ 回退全量"
           "（fail-closed：宁可多扫一次，不抱可能过时的缓存）",
           loop._deadend_stats["full"] > before
           and loop._deadend_stats["reasons"]["no_new_but_touched"] >= 1,
           f"full {before}->{loop._deadend_stats['full']} "
           f"reasons={loop._deadend_stats['reasons']}")

    # ── M7 真判据：`__new__` 构造的对象也必须能用（类级兜底）──
    #     ⛔ 这不是假想：J 组就是用 `EvolutionLoop.__new__` 手塞属性来测
    #        `_deadend_map` 的 —— 增量状态一变成实例属性，那条路径当场
    #        AttributeError（回归里真的炸了）。与 V5 的 `_exp_store` 同形态。
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        led = _Led(p)
        for _ in range(3):
            _probe(led)
        bare = EvolutionLoop.__new__(EvolutionLoop)
        bare.ledger = led
        try:
            got = bare._deadend_map()
            ok = got == _naive_streak(_Led(p))
            detail = f"got={got}"
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        ck("M7 经 `__new__` 构造（拿不到 `__init__` 赋值）也**不许**崩"
           " ⇒ 增量状态必须有类级兜底",
           ok, detail)

    # ── M8 护栏：真实构造函数下增量状态已初始化 ──
    with tempfile.TemporaryDirectory(prefix="v6m_") as td:
        p = Path(td) / "l.jsonl"
        loop = _mk_deadend_loop(_Led(p))
        ck("M8 `EvolutionLoop.__init__` 初始化了增量状态（空账本 ⇒ 空表）",
           getattr(loop, "_deadend_cache", "MISSING") is None
           and isinstance(getattr(loop, "_deadend_stats", None), dict)
           and loop._deadend_map() == {},
           f"cache={getattr(loop, '_deadend_cache', 'MISSING')} "
           f"stats={getattr(loop, '_deadend_stats', 'MISSING')}")


def main() -> int:
    print("契约测试：一次磁盘遍历（streaming）")
    for name, fn in (("A 不物化", group_a), ("B 流式==物化", group_b),
                     ("C 语义保持", group_c), ("D 内存峰值", group_d),
                     ("E 聚合器同源", group_e), ("F 一致性不失真", group_f),
                     ("G 响应形状", group_g), ("H 另一个端点", group_h),
                     ("I/J/K CLI+热路径", group_i),
                     ("L 判据无隐性上限", group_l),
                     ("M 死路账增量", group_m)):
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
