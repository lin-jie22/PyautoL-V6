#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：账本是唯一事实源，且**改一条就断链**。

判据来源（V5 硬教训的制度化）：
  · V5 把状态散在 3 个 DB + 多个 JSON ⇒ 对账要靠猜字段名；
  · V5 热进程回写覆盖文件修改 ⇒ 并发写会静默丢；
  · 2026 self-harness 综述把"可回滚 + 爆炸半径"列为必备属性，
    而没有 append-only 史，回滚就是空话。

本文件守：内容寻址确定性 / 哈希链 / 篡改可检测 / 并发不分叉 / 只读投影可重建。

用法：
    .venv/bin/python tests/contract_ledger_test.py
"""
import hashlib
import json
import os
import subprocess
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


def main():
    from aether.ledger import Ledger, Event, content_hash, canonical_json

    tmp = Path(tempfile.mkdtemp(prefix="v6_ledger_"))

    # ── A 组：内容寻址确定性（跨进程同输入必须同 hash）──────────
    print("A 组：内容寻址确定性")
    payload = {"中文": "判据必须可读不可写", "n": 3, "list": [1, 2]}
    h1 = content_hash(payload)
    code = ("import sys; sys.path.insert(0, %r); "
            "from aether.ledger import content_hash; "
            "print(content_hash(%r))" % (str(ROOT), payload))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(ROOT))
    h2 = out.stdout.strip()
    ck("A1 跨进程 hash 一致（TS/其他语言也能算出来）", h1 == h2, f"{h1[:12]} vs {h2[:12]}")
    ck("A2 键序不影响 hash（sort_keys）",
       content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1}))
    ck("A3 中文不被转义（跨语言解析不会错位）",
       "判据" in canonical_json({"k": "判据"}))
    ck("A4 值变则 hash 变", content_hash({"a": 1}) != content_hash({"a": 2}))

    # ── B 组：哈希链 ──────────────────────────────────────────────
    print("B 组：哈希链")
    p = tmp / "a.jsonl"
    led = Ledger(p)
    for i in range(5):
        led.append("test.event", {"i": i}, actor="test")
    ok, seq, why = led.verify_chain()
    ck("B1 正常追加 ⇒ 链完整", ok, why)
    ck("B2 prev_hash 首尾相接",
       all(e.prev_hash == (led.events()[i - 1].hash if i else Ledger.GENESIS_HASH)
           for i, e in enumerate(led.events())))
    ck("B3 事件数 = 5", len(led.events()) == 5)

    # ── C 组：反证 —— 篡改必须被检测到（证明 B 不是死代码）──────
    print("C 组：反证（篡改可检测）")
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    ev = json.loads(lines[2])
    ev["payload"]["i"] = 999                      # 改中间一条的 payload
    lines[2] = json.dumps(ev, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    bad = tmp / "tampered.jsonl"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    led2 = Ledger(bad)
    ok2, seq2, why2 = led2.verify_chain()
    ck("C1 改中间 payload ⇒ 链断裂", not ok2, f"却报完整（{why2}）")
    ck("C2 断裂定位到被改的那一条", seq2 == 3, f"实得 seq={seq2}")

    # 删中间一行
    del lines[2]
    bad2 = tmp / "deleted.jsonl"
    bad2.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok3, seq3, _ = Ledger(bad2).verify_chain()
    ck("C3 删中间一行 ⇒ 链断裂（不是静默少一条）", not ok3, f"seq={seq3}")

    # ── D 组：并发不分叉 ──────────────────────────────────────────
    print("D 组：并发追加")
    p2 = tmp / "conc.jsonl"
    p2.write_text("", encoding="utf-8")
    n_proc, n_each = 4, 5
    procs = []
    for k in range(n_proc):
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r});"
                f"from aether.ledger import Ledger;"
                f"L=Ledger({str(p2)!r});\n"
                f"for i in range({n_each}): L.append('conc', {{'p': {k}, 'i': i}}, actor='p{k}')")
        procs.append(subprocess.Popen([sys.executable, "-c", code],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      cwd=str(ROOT)))
    for pr in procs:
        pr.communicate(timeout=60)
    led3 = Ledger(p2)
    ok4, _, why4 = led3.verify_chain()
    ck(f"D1 {n_proc} 进程并发 ⇒ 链完整", ok4, why4)
    ck(f"D2 事件一条不丢（{n_proc * n_each} 条）",
       len(led3.events()) == n_proc * n_each, f"实得 {len(led3.events())}")
    ck("D3 seq 严格递增无重复",
       [e.seq for e in led3.events()] == list(range(1, n_proc * n_each + 1)))

    # ── E 组：投影可由账本重建（**双向比对**，不是"跑通就行"）────
    #    ⛔⛔ 2026-10-13 实测：这一组原本是 `n >= 0` / `n_runs >= 0` ——
    #    两个**恒真**断言。它们绿了很久，而"库只是视图"其实从来没被验证：
    #    真跑一遍就发现账本 495 条执行、投影只剩 51 条，且键空间完全不同。
    #    ⇒ 判据必须双向（键集合 + 字段 + 条数），并且配反证组（F）。
    print("E 组：账本 → 投影（双向比对）")
    from aether.store import Store, RunRow

    p3 = tmp / "proj.jsonl"
    L3 = Ledger(p3)
    st1 = Store(tmp / "v1.db")
    L3.append("harness.init", {"harness_id": "h_root", "round_no": 0,
                               "payload": {"meta_rules": "x"}, "parent_id": ""})
    st1.save_snapshot("h_root", {"meta_rules": "x"}, accepted=True, round_no=0)
    for i in range(6):
        rid = f"base:h_root:t{i}#{i + 1}"      # ⛔ 唯一 run_id（模拟修好之后）
        rew = 1.0 if i % 2 == 0 else 0.0
        L3.append("eval.verdict", {
            "run_id": rid, "harness_id": "h_root", "task_id": f"t{i}",
            "reward": rew, "passed": bool(rew), "split": "dev", "tag": "base:",
            "verdict": {}, "caliber": {}})
        st1.save_run(RunRow(run_id=rid, harness_id="h_root", task_id=f"t{i}",
                            reward=rew, passed=bool(rew), gate_ok=False,
                            payload={"i": i}))
    L3.append("harness.commit", {"harness_id": "h_child", "round_no": 1,
                                 "payload": {"meta_rules": "y"},
                                 "parent_id": "h_root"})
    st1.save_snapshot("h_child", {"meta_rules": "y"}, parent_id="h_root",
                      accepted=True, round_no=1)

    cons1 = st1.consistency_report(L3.events())
    ck("E1 正常写入后 投影 == 账本（一致性判据为 ok）", cons1["ok"],
       json.dumps(cons1, ensure_ascii=False))

    st2 = Store(tmp / "v2.db")
    counts = st2.rebuild_from_ledger(L3.events())
    ck("E2 重建条数=账本事件数（6 run + 2 快照，无派生键）",
       counts == {"snapshots": 2, "runs": 6, "derived": 0}, str(counts))
    ck("E3 重建后 run_id 集合与现状**完全相同**（不是「都能读」就算）",
       set(st2.run_ids()) == set(st1.run_ids()),
       f"{sorted(set(st1.run_ids()) - set(st2.run_ids()))[:3]} / "
       f"{sorted(set(st2.run_ids()) - set(st1.run_ids()))[:3]}")
    a = {r.run_id: (r.reward, r.passed, r.harness_id, r.task_id)
         for r in st1.runs(limit=1000)}
    b = {r.run_id: (r.reward, r.passed, r.harness_id, r.task_id)
         for r in st2.runs(limit=1000)}
    ck("E4 重建后字段逐条一致（reward/passed/harness_id/task_id）", a == b,
       str([k for k in a if a[k] != b.get(k)])[:200])
    ck("E5 重建后快照集合一致（**含 harness.init 的初始 harness**）",
       set(st2.snapshot_ids()) == set(st1.snapshot_ids()) == {"h_root", "h_child"},
       f"{st2.snapshot_ids()} vs {st1.snapshot_ids()}")
    ck("E6 重建出来的库与账本一致（ok=True）",
       st2.consistency_report(L3.events())["ok"])
    st1.close()
    st2.close()

    # ── F 组：反证 —— 判据必须能在事故版上转红 ───────────────────
    #    ⛔ 没有反证的判据 = 恒亮绿灯。这里逐条注入 2026-10-13 真实事故。
    print("F 组：反证（事故必须被看见）")
    st3 = Store(tmp / "v3.db")
    L4 = Ledger(tmp / "f.jsonl")
    for i in range(4):
        rid = f"r{i}"
        rew = float(i)
        L4.append("eval.verdict", {"run_id": rid, "harness_id": "h",
                                   "task_id": f"t{i}", "reward": rew,
                                   "passed": rew > 0, "split": "dev"})
        st3.save_run(RunRow(run_id=rid, harness_id="h", task_id=f"t{i}",
                            reward=rew, passed=rew > 0, gate_ok=False,
                            payload={}))
    # F1：同键不同内容 ⇒ 覆盖事故必须被记下来
    overwrote = st3.save_run(RunRow(run_id="r0", harness_id="h", task_id="t0",
                                    reward=9.0, passed=True, gate_ok=False,
                                    payload={"dup": True}))
    ck("F1 同键写入不同内容 ⇒ save_run 报覆盖", overwrote is True)
    ck("F2 覆盖次数进 stats（事故可见，不是静默正确）",
       st3.stats()["overwrite_count"] >= 1, str(st3.stats()))
    # F3：run_id 不唯一 ⇒ 两条不同的执行映射到同一个键 ⇒ 投影**丢历史**
    #     （2026-10-13 真实事故：495 条执行只剩 51 条，就是这么没的）
    L4.append("eval.verdict", {"run_id": "r0", "harness_id": "h",
                               "task_id": "t0", "reward": 5.0,
                               "passed": True, "split": "dev"})
    st3.save_run(RunRow(run_id="r0", harness_id="h", task_id="t0",
                        reward=5.0, passed=True, gate_ok=False, payload={}))
    c3 = st3.consistency_report(L4.events())
    ck("F3 run_id 撞键 ⇒ 投影条数 < 账本条数（丢历史被抓到）",
       c3["count_ok"] is False and c3["projection_runs"] < c3["ledger_verdicts"],
       json.dumps({k: c3[k] for k in ("count_ok", "projection_runs",
                                      "ledger_verdicts")}))
    ck("F4 撞键后 ok=False（闸真的会响，不是恒绿）", c3["ok"] is False)

    # F5：字段漂移（投影被别的东西改过）⇒ 必须检出
    st3.save_run(RunRow(run_id="r1", harness_id="h", task_id="t1",
                        reward=0.0, passed=False, gate_ok=False, payload={}))
    c4 = st3.consistency_report(L4.events())
    ck("F5 同键内容被改 ⇒ drift 被检出", c4["drift_count"] >= 1,
       json.dumps({k: c4[k] for k in ("drift_count", "drifted")}))

    # F6：快照缺失 ⇒ 必须检出（这正是 h0 绕过账本时的症状）
    L4.append("harness.commit", {"harness_id": "h_missing", "round_no": 2,
                                 "payload": {}, "parent_id": "h"})
    c5 = st3.consistency_report(L4.events())
    ck("F6 账本有快照而投影没有 ⇒ snapshot_missing 被检出",
       c5["snapshot_missing"] == ["h_missing"] and c5["ok"] is False,
       json.dumps({k: c5[k] for k in ("snapshot_missing", "ok")}))
    st3.close()

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
