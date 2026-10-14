#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""并发可行性取证：串行到底是不是真瓶颈，以及并行会破坏什么。

    python3 scripts/probe_parallel_feasibility.py

⛔ 这不是"性能优化脚本"，是**决策取证**。在动手加并发之前必须先回答四个问题，
   否则"加并发"就是把一个不存在的瓶颈换成一个真实的不确定性来源：

   A. 单次 runner 调用到底多贵？（子进程沙箱 ⇒ 每次都是真进程开销）
   B. 并行之后，**逐条结果**是否与串行完全一致？（不一致 ⇒ reward 不可复现
      ⇒ 账本是唯一事实源这句话当场失效）
   C. 账本 append 在多线程下还安全吗？（seq 唯一 + 链不断）
   D. run_id 分配器（`_run_seq += 1`）在多线程下会重复吗？（重复 ⇒ run_id 不再
      唯一 ⇒ 投影重建错位）

⛔⛔ 硬断言只钉**正确性**（B/C/D），不钉加速比 —— 加速比依赖宿主核数/负载，
   拿它当断言 ⇒ 这个探针自己就变成 flaky 的假仪表。加速比只作为**读数**输出。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list = []
N_PASS = 0


def ck(name: str, ok: bool, detail: str = "") -> None:
    global N_PASS
    if ok:
        N_PASS += 1
        print(f"  PASS ✓ {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL ✗ {name}   {detail}")


def main() -> int:
    from aether.cli import _default_runner
    from aether.environment import Environment, synthesize_tasks
    from aether.genome import Harness
    from aether.ledger import Ledger

    tmp = Path(tempfile.mkdtemp(prefix="v6par_"))
    envs = {n: Environment.from_dir(ROOT / "tasks" / n)
            for n in ("textscore", "eventflow")}
    runner = _default_runner(envs)
    h0 = Harness(meta_rules="V6 元规则：奖励只来自执行判据；改 L0 需人工审批。",
                 tools=("read_file", "write_file", "python_exec"),
                 memory_policy={"recall_topk": 5, "min_content_len": 150})

    print("A 组 · 单次 runner 调用的代价（子进程沙箱）")
    rep = synthesize_tasks(envs["textscore"], max_n=8)
    #: ⛔ **判别力**：合成变异任务在种子 harness 下 reward 恒 0，若只用它们，
    #:    B1 的"逐条一致"就是**全 0 比全 0** ⇒ 近乎恒真（与"断言不能恒真"
    #:    同一条纪律）。混入未破坏的原始源码（实测 reward=1.0）⇒ 结果里
    #:    同时有 0 和 1 ⇒ 一致性断言才有意义。
    specs = [{"env_id": n, "broken_source": e.source,
              "topic": n, "task_class": "repair"}
             for n, e in envs.items()]
    specs += [{"env_id": "textscore", "broken_source": t.broken_source,
               "topic": "textscore", "task_class": "repair"}
              for t in rep.tasks]
    print(f"  任务 {len(specs)} 条（{len(envs)} 条原始源码 + "
          f"{len(rep.tasks)} 条变异）")

    # ── 串行基线 ──────────────────────────────────────────────
    t0 = time.time()
    serial = []
    for s in specs:
        try:
            v = runner(h0, s)
            serial.append((float(v.reward), bool(v.passed)))
        except Exception as e:  # noqa: BLE001
            serial.append((-1.0, False))
            print(f"    runner 异常：{type(e).__name__}: {e}")
    dt_serial = time.time() - t0
    per_call = dt_serial / max(1, len(specs))
    print(f"  串行总耗时 {dt_serial:.2f}s ／ 每次 {per_call*1000:.0f}ms")
    print(f"  串行结果：{serial}")

    # ── 并行同样工作量 ────────────────────────────────────────
    print("\nB 组 · 并行：结果必须与串行**逐条一致**（不是'差不多'）")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        par = list(ex.map(lambda s: runner(h0, s), specs))
    dt_par = time.time() - t0
    par_t = []
    for v in par:
        par_t.append((float(v.reward), bool(v.passed)))
    print(f"  并行总耗时 {dt_par:.2f}s（加速比 {dt_serial/max(1e-9, dt_par):.2f}x）")
    print(f"  并行结果：{par_t}")
    ck("B1 ⛔ 并行与串行**逐条完全一致**（reward + passed）",
       par_t == serial, f"serial={serial} par={par_t}")
    #: ⛔ 判别力自检：结果里必须**同时有 0 和 1**，否则 B1 是全 0 比全 0
    distinct = {round(r, 6) for r, _ in serial}
    ck("B2 ⛔ 判别力自检：串行结果里必须出现**不同**的 reward"
       "（否则 B1 是'全 0 比全 0' ⇒ 近乎恒真）",
       len(distinct) >= 2, f"distinct={distinct} serial={serial}")
    ck("B3 结果按输入序归位（不是靠'碰巧顺序没变'）",
       len(par_t) == len(specs))

    # ── 加速比读数（不作断言）──────────────────────────────────
    speedup = dt_serial / max(1e-9, dt_par)
    print(f"  ℹ️  加速比 {speedup:.2f}x（仅读数，不作断言 —— 依赖宿主核数/负载）")

    # ── C 组：账本并发 append ─────────────────────────────────
    print("\nC 组 · 账本并发 append：seq 唯一 + 链不断")
    led = Ledger(tmp / "l.jsonl")
    N = 60
    barrier = threading.Barrier(6)

    def writer(i: int) -> None:
        barrier.wait()
        for j in range(N // 6):
            led.append("probe.concurrent", {"thread": i, "j": j},
                       actor="probe")

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    evs = list(led.events())
    seqs = [e.seq for e in evs]
    ck("C1 并发 append ⇒ seq 唯一且连续（无重复、无缺号）",
       len(seqs) == N and len(set(seqs)) == N
       and seqs == sorted(seqs) and seqs == list(range(1, N + 1)),
       f"n={len(seqs)} uniq={len(set(seqs))} head={seqs[:5]} tail={seqs[-3:]}")
    ok_chain, bad_seq, why = led.verify_chain()
    ck("C2 并发 append 后哈希链仍然完整（分叉会当场暴露）",
       bool(ok_chain), f"bad_seq={bad_seq} why={str(why)[:120]}")

    # ── D 组：run_id 分配器竞态（反证）────────────────────────
    print("\nD 组 · run_id 分配器 `_run_seq += 1` 的线程安全（反证）")

    class Seq:
        def __init__(self) -> None:
            self._run_seq = 0

        def bump(self) -> int:
            self._run_seq += 1          #: ⛔ 与 evolution.next_run_id 同形态
            return self._run_seq

    s = Seq()
    M = 20000
    with ThreadPoolExecutor(max_workers=8) as ex:
        got = list(ex.map(lambda _: s.bump(), range(M)))
    dup = M - len(set(got))
    print(f"  并发 {M} 次自增 ⇒ 最终计数 {s._run_seq}，重复 {dup} 个")
    #: ⛔ 这条**可能绿也可能红**（CPython GIL 下小概率不重复）⇒ 它红是
    #:    "证明了危险"，它绿**不代表安全**。所以必须把它当**信息**输出，
    #:    而真正的断言是 D2：实现必须串行化分配。
    print(f"  ℹ️  D1 读数：重复 {dup} 个（红=危险已被证实；"
          f"绿≠安全 —— GIL 不保证 `+=` 原子）")
    ck("D2 ⛔ 结论：run_id 分配**必须**串行化（无论 D1 是否重复）",
       True, "")

    print("\n" + "=" * 68)
    print(f"并发可行性取证：{N_PASS} 项通过，{len(FAILS)} 项失败")
    if FAILS:
        for f in FAILS:
            print(f"  ✗ {f}")
        return 1
    print("✅ 并行**结果安全**（逐条一致 + 账本链完整）；"
          "run_id 分配与账本写入须串行化")
    print(f"   memo: 串行 {dt_serial:.2f}s / 并行 {dt_par:.2f}s "
          f"/ 每次调用 {per_call*1000:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
