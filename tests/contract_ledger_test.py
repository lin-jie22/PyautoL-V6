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

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []

#: 并发组轮数。⛔ 单轮不足以暴露竞态（r33 实测：1/8 概率），
#:    但也别把契约测试拖成压力测试 —— 4 轮足够高地把概率打出来。
_CONC_TRIALS = 4


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
    #    ⛔⛔ r33 实测事故：这一段原来**只跑一轮**，而失败是概率性的
    #       （重载下实测 1/8）。根因是 `append()` 用 `(size, mtime_ns)`
    #       判断"文件没变"⇒ 重载下误判 ⇒ 沿用过期 `_last_hash` ⇒ 写出
    #       `prev_hash=0000…` ⇒ 账本分叉。修法：判据只用
    #       ⇒ 撤掉快路径（见 `Ledger.append` 与 `Ledger.__init__` 的实测记录）。
    #    ⛔ 所以这里必须**多轮**（单轮绿说明不了问题）+ 检查子进程退出码
    #       （原版不看 rc：子进程崩了会伪装成"链断了"，排障方向全错）。
    print(f"D 组：并发追加（{_CONC_TRIALS} 轮，单轮绿说明不了并发问题）")
    n_proc, n_each = 4, 5
    n_total = n_proc * n_each
    conc_bad = []
    for trial in range(_CONC_TRIALS):
        p2 = tmp / f"conc{trial}.jsonl"
        p2.write_text("", encoding="utf-8")
        procs = []
        for k in range(n_proc):
            code = (f"import sys; sys.path.insert(0, {str(ROOT)!r});"
                    f"from aether.ledger import Ledger;"
                    f"L=Ledger({str(p2)!r});\n"
                    f"for i in range({n_each}): L.append('conc', {{'p': {k}, 'i': i}}, actor='p{k}')")
            procs.append(subprocess.Popen([sys.executable, "-c", code],
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE,
                                          cwd=str(ROOT)))
        for k, pr in enumerate(procs):
            _o, e = pr.communicate(timeout=60)
            if pr.returncode != 0:
                conc_bad.append(f"trial{trial} p{k} rc={pr.returncode}: "
                                f"{e.decode('utf-8', 'replace')[-160:]}")
        led3 = Ledger(p2)
        ok4, _, why4 = led3.verify_chain()
        seqs = [e.seq for e in led3.events()]
        if not ok4:
            conc_bad.append(f"trial{trial} 链断：{why4}")
        elif seqs != list(range(1, n_total + 1)):
            conc_bad.append(f"trial{trial} seq 异常：n={len(seqs)}"
                            f" 头部={seqs[:3]} 尾部={seqs[-3:]}")
    ck(f"D1 {_CONC_TRIALS} 轮 × {n_proc} 进程并发 ⇒ 链完整且 seq 无重复",
       not conc_bad, f"{len(conc_bad)} 轮异常：{conc_bad[:3]}")
    ck("D1b 子进程全部正常退出（rc≠0 不得被读成『链断了』）",
       not any("rc=" in s for s in conc_bad),
       str([s for s in conc_bad if "rc=" in s][:2]))
    ck(f"D2 事件一条不丢（每轮 {n_total} 条）",
       all(len(Ledger(tmp / f"conc{i}.jsonl").events()) == n_total
           for i in range(_CONC_TRIALS)))

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

    print("\nG 组 · 冷启动只读尾部（tail hydrate）必须与全扫**完全等价**")
    print("  ⛔ append-only + 写入串行 ⇒ 最新事件必在末尾；为了 seq/hash 两个")
    print("     字段全扫一遍，会让每个 /api/status 白付一整遍 O(n)")

    import os

    #: ⛔ 防御式取值：对着"还没加尾读"的旧版跑时要报**干净的红灯**，
    #:    不能崩在 import 上（崩了就看不到到底哪几条是恒真的）。
    import aether.ledger as _LGM
    HYDRATE_TAIL_BYTES = getattr(_LGM, "HYDRATE_TAIL_BYTES", 0)
    _has_tail = hasattr(_LGM.Ledger, "_read_tail_event")

    def _tail_and_full(p):
        """同一份文件的两种 hydrate 结果。

        ⛔ 基准用 getattr 兼容两个版本：旧版没有 `_hydrate_full`（那时全扫
        逻辑就叫 `_hydrate`）。这样对着旧版跑会报**干净的红灯**而不是崩在
        AttributeError 上 —— 崩了就看不见哪几条其实是恒真的。
        """
        a = Ledger(p)
        tail = (a._seq, a._last_hash)      # 被测路径：可能走尾读
        scan = getattr(a, "_hydrate_full", None) or a._hydrate
        scan()                             # 基准：全扫
        return tail, (a._seq, a._last_hash)

    prod = ROOT / "data" / "ledger.jsonl"

    #: ⛔⛔ G0 是本组**唯一在旧版会转红**的断言，必须放在最前面。
    #:     实测：把 ledger.py 退回 HEAD 跑一遍，G1–G5 **全都绿**（那时两条
    #:     路径都是全扫，自然相等）⇒ 它们是"等价性护栏"，不是"尾读已生效"
    #:     的证据。真正证明尾读存在的是 G0 + H1。
    ck("G0 账本确实走了尾读路径（存在 `_read_tail_event`）"
       " —— 2026-10-13 实测：这是 G 组唯一能证伪的断言",
       _has_tail, "缺少 _read_tail_event ⇒ 冷启动回到全扫")

    # G1 生产账本（1465 条）—— 真实数据上的等价性
    if prod.exists():
        t, f = _tail_and_full(prod)
        ck("G1 生产账本：尾读 = 全扫", t == f, f"tail={t} full={f}")
    else:
        print("  （跳过 G1：data/ledger.jsonl 不存在）")

    # G2 空文件 / 不存在
    (tmp / "empty.jsonl").write_text("", encoding="utf-8")
    t, f = _tail_and_full(tmp / "empty.jsonl")
    ck("G2a 空文件：尾读 = 全扫", t == f, f"tail={t} full={f}")
    t, f = _tail_and_full(tmp / "nope.jsonl")
    ck("G2b 文件不存在：尾读 = 全扫", t == f, f"tail={t} full={f}")

    # G3 末尾坏行（崩出来的半截写入）⇒ 必须回退到上一条好事件
    lg = Ledger(tmp / "badtail.jsonl")
    for i in range(5):
        lg.append("c.d", {"i": i})
    good = (lg._seq, lg._last_hash)
    with (tmp / "badtail.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 999, "ts": 1.0, "kind": "x"   ← 半截损坏\n\n')
    lg2 = Ledger(tmp / "badtail.jsonl")
    ck("G3 末尾坏行 ⇒ 回退到上一条好事件（不许造出 seq=999）",
       (lg2._seq, lg2._last_hash) == good,
       f"got=({lg2._seq}, {lg2._last_hash[:8]}) want=({good[0]}, {good[1][:8]})")

    # G4 整个尾缓冲全是坏行 ⇒ 必须退回全扫，不许把缺失假装成 genesis
    lh = Ledger(tmp / "allbad.jsonl")
    for i in range(3):
        lh.append("e.f", {"i": i})
    want3 = (lh._seq, lh._last_hash)
    with (tmp / "allbad.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("X" * (HYDRATE_TAIL_BYTES + 100) + "\n")
    lh2 = Ledger(tmp / "allbad.jsonl")
    ck("G4 尾缓冲全坏 ⇒ 退回全扫且不伪报",
       (lh2._seq, lh2._last_hash) == want3,
       f"got=({lh2._seq}, {lh2._last_hash[:8]}) want=({want3[0]}, {want3[1][:8]})")

    # G5 超长单行之后：允许退化全扫，但结果必须正确
    li = Ledger(tmp / "hugeline.jsonl")
    li.append("g.h", {"blob": "y" * (HYDRATE_TAIL_BYTES * 2)})
    li.append("g.h", {"after": 1})
    want4 = (li._seq, li._last_hash)
    li2 = Ledger(tmp / "hugeline.jsonl")
    ck("G5 超长行（>尾缓冲）之后仍拿到正确 head",
       (li2._seq, li2._last_hash) == want4,
       f"got=({li2._seq}, {li2._last_hash[:8]}) want=({want4[0]}, {want4[1][:8]})")

    #: ⛔ 反证要能真正证伪：模拟一个"取了第一条而不是最后一条"的实现 bug。
    #:   （早先我写成"强制 `_read_tail_event` 返回 None ⇒ 走全扫退路"，那是
    #:    恒真的 —— 它去调的就是全扫，结果当然相同。改成错过最后一条。）
    _read_tail = Ledger._read_tail_event

    def _wrong_tail(self):
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    return Event.from_dict(__import__("json").loads(line))
                except Exception:  # noqa: BLE001
                    continue
        return None

    for _tag, _p in (("生产账本", prod), ("多行账本", tmp / "badtail.jsonl")):
        if not _p.exists():
            continue
        Ledger._read_tail_event = _wrong_tail
        try:
            tw, fw = _tail_and_full(_p)
            ck(f"G6 反证（{_tag}）：尾读取错成第一条 ⇒ 等价性必须破"
               "（否则 G1–G5 是恒真断言）",
               tw != fw, f"事故版 tail={tw} full={fw}")
        finally:
            Ledger._read_tail_event = _read_tail

    #: ⛔ 用 tmp 里那份账本，不用生产账本：这一条守的是"反证已还原"，
    #:    跟"库里攒了多少历史"无关；挂在 prod 上 ⇒ fresh checkout 里会
    #:    因文件不存在而**抛异常**（那连"跳过"都算不上，是崩）。
    t3, f3 = _tail_and_full(tmp / "badtail.jsonl")
    ck("G7 反证已还原 ⇒ 恢复等价", t3 == f3, f"tail={t3} full={f3}")

    print("\nH 组 · 一次只读请求不许把账本解析多遍")

    #: ⛔ 用"全量行数 / json.loads 次数"来量，而不是靠时间（本机 load 常在
    #:    10 以上，单次 wall time 抖动 0.37s~2.57s，不可作为判据）
    try:
        import json as _json
        import aether.ledger as _LG
        from aether.serve import _collect

        #: ⛔⛔ **密闭化**（2026-10-15 r37 收口）：分母 `n_lines` 原本取自
        #:    **生产账本** `data/ledger.jsonl` —— git **不入库**的运行时产物。
        #:    两个后果，一个比一个糟：
        #:      ① fresh checkout 里它不存在 ⇒ 分母 ≈ 0 ⇒ "解析次数 / 行数"
        #:         这个**比值**失真 ⇒ H1 假红，而 H2 这个"反证"反证的是
        #:         一个**假分母**（实测：worktree @537dda 把 ROOT 改成
        #:         `__file__` 相对之后，红的正是 H2 —— 红得毫无信息量）；
        #:      ② 就算存在，那也是**历史累计**。主工作区里它恰好留着，
        #:         于是 H1 长期"绿"—— 绿的是"曾经攒下过一份账本"，
        #:         不是"这次请求只解析了一遍"。
        #:    ⛔ 比值型判据的分母**不许**取自生产数据（README 第 85 条）。
        #:    ⇒ 改成**自造账本**：行数 N 是我们自己写下去的**已知常量**，
        #:      于是比值有确定含义，且在**任何检出里都相同**。
        #:    ⛔ 为什么一个只有 `ledger.jsonl` 的临时 root 就够：`_collect`
        #:      只从 `paths(root)` 取 `ledger` / `db` / `memory` 三个键，不碰
        #:      `tasks/`、不碰 skills json；`db` 与 `memory` 缺失时各自**整段
        #:      跳过**（`store={}` / `runs=[]` / `consistency={}`,
        #:      `file_ids=None` ⇒ 连记忆对账也跳过）⇒ 三个键里只有账本真的
        #:      在参与，自造它就能让 `_collect` 完整跑一遍。
        hroot = tmp / "hroot"
        (hroot / "data").mkdir(parents=True, exist_ok=True)
        hled = Ledger(hroot / "data" / "ledger.jsonl")
        N_LINES = 400          # ⛔ 自造 ⇒ 已知常量，不是历史累计
        for _i in range(N_LINES):
            hled.append("probe.kind", {"i": _i, "pad": "x" * 64})
        n_lines = sum(1 for _ in _LG.Ledger(hled.path).iter_lines())
        ck("H0 密闭底座：自造账本行数 = 预期（分母错了 ⇒ 比值无意义）",
           n_lines == N_LINES, f"写入 {N_LINES} 行 / 实读 {n_lines} 行")
        real = _json.loads
        cnt = {"n": 0}
        _LG.json.loads = (lambda s, *a, **k:
                          (cnt.__setitem__("n", cnt["n"] + 1),
                           real(s, *a, **k))[1])
        try:
            cnt["n"] = 0
            _collect(hroot, limit=200)
            got = cnt["n"]
        finally:
            _LG.json.loads = real
        passes = got / max(1, n_lines)
        ck(f"H1 一次 _collect 的 JSON 解析量 < 2 遍全量"
           f"（共 {got} 次 / {n_lines} 行 = {passes:.2f} 遍）",
           passes < 2.0, f"{passes:.2f} 遍")
        #: ⛔⛔ 反证项**随实现翻转**，必须跟着改注入点：
        #:    最初的 H2 注入 `Ledger.stats`（"忽略传入的 events，强制重读"）。
        #:    但流式化之后 `_collect` **根本不调 `stats()`** 了（它用
        #:    `stats_agg()` + 一次 `iter_events()`）⇒ 那个注入点变成死点，
        #:    H2 恒红却查不出任何真问题 —— 典型的"反证没跟上实现"。
        #:    现在的咽喉是 `iter_events`：**多扫一遍**就是"重复读"。
        #:    注入它，断言 H1 的阈值（< 2 遍）会被打破 ⇒ 证明 H1 不是恒真。
        old_iter = _LG.Ledger.iter_events

        def _double_read(self):
            for _ in old_iter(self):        # 白扫一遍：模拟某个消费者自己又读
                pass
            yield from old_iter(self)
        _LG.Ledger.iter_events = _double_read
        try:
            real2 = _json.loads
            cnt2 = {"n": 0}
            _LG.json.loads = (lambda s, *a, **k:
                              (cnt2.__setitem__("n", cnt2["n"] + 1),
                               real2(s, *a, **k))[1])
            try:
                _collect(hroot, limit=200)
                got2 = cnt2["n"]
            finally:
                _LG.json.loads = real2
        finally:
            _LG.Ledger.iter_events = old_iter
        passes2 = got2 / max(1, n_lines)
        ck("H2 反证：多扫一遍 ⇒ H1 的阈值被打破"
           "（否则 H1 是恒真断言）",
           passes2 >= 2.0,
           f"事故版 {got2} 次 = {passes2:.2f} 遍（正常 {passes:.2f} 遍）")
    except Exception as e:  # noqa: BLE001
        ck("H 组可执行（自造账本 + _collect 能跑）",
           False, f"{type(e).__name__}: {e}")

    # ── I 组：跨语言舍入契约（写入端自己守，不靠"碰巧不在 TS 侧跑"）──
    #    ⛔ 2026-10-13 实测事故：`ts*1000` 恰为半整数时 Python `int(round())`
    #       （取偶，…736）与 JS `Math.round`（half-up，…737）不同 ⇒ 1028 条
    #       命中 1 条 ⇒ 监督器把完好账本判成"payload 被改"。
    #       修法是 TS 侧实现 `roundHalfEven` 对齐 Python。
    #    ⛔⛔ 2026-10-14（r33）又出一次反向事故：有人把 Python 侧"改成
    #       half-up"以求"与 Math.round 一致" —— 而我们真正对拍的是
    #       `roundHalfEven`（取偶）⇒ 改动反而**制造**了漂移。
    #       本组把这条契约钉在 Python 侧：改这里必须同步改
    #       `supervisor/src/ledger.ts` 的 `roundHalfEven` 与 selfcheck。
    print("I 组：跨语言舍入契约（半整数毫秒必须取偶）")
    _e = Event(seq=1, ts=1791871960736.5 / 1000.0, kind="t", actor="a",
               payload={}, prev_hash="")
    ck("I1 ts*1000 为半整数 ⇒ 取偶（与 TS roundHalfEven 一致，非 half-up）",
       _e.identity()["ts"] == 1791871960736,
       f"got={_e.identity()['ts']} expect=1791871960736"
       f"（half-up 会得 1791871960737 ⇒ 监督器判链被篡改）")
    _odd = Event(seq=1, ts=1791871960737.5 / 1000.0, kind="t", actor="a")
    ck("I2 取偶是双向的（奇数半整数进位，不是恒向下取整）",
       _odd.identity()["ts"] == 1791871960738,
       f"got={_odd.identity()['ts']} expect=1791871960738")
    _int = Event(seq=1, ts=1791871960736.0 / 1000.0, kind="t", actor="a")
    ck("I3 整数毫秒不受舍入规则影响（改动前的历史 hash 不失效）",
       _int.identity()["ts"] == 1791871960736)

    # ── J 组：并发安全 > 微优化（这条判据是拿实测数据换来的）──────────
    #    ⛔ r33 实测：`append()` 里"文件没变就跳过重读尾巴"的快路径，
    #       在重载下 24 轮 × 4 进程并发中有 **1 轮分叉**（两个进程都在
    #       seq=6 写出指向同一个 seq=5 的 `prev_hash`），而它只省
    #       **0.068 ms/条**（0.153 → 0.085 ms，2000 条账本实测）。
    #       ⇒ 撤掉快路径，改成**锁内无条件重读尾巴**（仍是 O(尾巴)，不是 O(n)）。
    #    ⛔ 本组守的就是这条决定本身：谁再把"跳过重读"加回来，必须同时
    #       拿出并发证据，否则这里会红。
    print("J 组：锁内无条件重读尾巴（正确性优先于 0.068 ms）")
    pj = tmp / "always_read.jsonl"
    pj.write_text("", encoding="utf-8")
    Lj = Ledger(pj)
    reads = {"n": 0}
    _orig_read = Ledger._read_tail_event

    def _counted(self):                                        # noqa: ANN001
        reads["n"] += 1
        return _orig_read(self)

    #: ⛔ 反证组：把"跳过重读"的缓存加回来 ⇒ J2 必须转红。
    Ledger._read_tail_event = _counted
    try:
        for i in range(4):
            Lj.append("t", {"i": i})
        n_reads = reads["n"]
    finally:
        Ledger._read_tail_event = _orig_read
    ck("J1 每次 append 都在锁内重读尾巴（4 次 append ⇒ ≥4 次读）",
       n_reads >= 4, f"reads={n_reads}")
    ck("J2 且链完整、seq 连续（重读不是白读）",
       Lj.verify_chain()[0]
       and [e.seq for e in Lj.events()] == list(range(1, 5)))
    #: 反证：**别的写者**写过之后必须能看见（这是分叉的直接防线）
    reads["n"] = 0
    other = Ledger(pj)
    other_ev = other.append("t", {"who": "other"})
    Ledger._read_tail_event = _counted
    try:
        ev = Lj.append("t", {"i": 4})
    finally:
        Ledger._read_tail_event = _orig_read
    #: ⛔ 对照物必须用**它返回的 Event**，不能回头 `other.events()[-1]` ——
    #:    那是一次新读，读到的是我们后面又写进去的那条 ⇒ 断言恒等（空真）。
    ck("J3 反证：别的写者写过 ⇒ 本次 prev_hash 指向**它**（不是旧尾巴）",
       ev.prev_hash == other_ev.hash, f"{ev.prev_hash[:12]}")
    ck("J4 别的写者写完之后链依然完整（J3 不是恒真）",
       Lj.verify_chain()[0]
       and [e.seq for e in Lj.events()] == list(range(1, 7)),
       f"{[e.seq for e in Lj.events()]}")

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
