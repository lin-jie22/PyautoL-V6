"""探针：`_collect` 的内存峰值到底花在哪（只读，不改任何东西）。

⛔ 本机 load 常年 10+，**单次 wall time 不可用**；但 `tracemalloc` 量的是
**分配字节数**，是结构性计数，与 CPU 负载无关 ⇒ 可复现。

这个探针回答两件事：
  1. 一次 `_collect` 的峰值分配是多少，其中"持有全量事件"占多少；
  2. 流式改造后降了多少（同一脚本，改完再跑一次，两个数对比）。

用法：
    python scripts/probe_memory_audit.py [n_events]
"""
from __future__ import annotations

import json
import sys
import tempfile
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aether.ledger import Ledger          # noqa: E402
from aether.serve import _collect          # noqa: E402

#: 单条事件的 payload 大小：真实账本里 `eval.verdict` 带源码片段/轨迹，
#: 1 KB 是保守估计（实测生产账本平均行长约 400 B～2 KB）。
PAYLOAD_TEXT = "x" * 1024


def build(path: Path, n: int) -> None:
    #: ⛔ 必须是 `root/data/ledger.jsonl` —— `_collect` 就认这个布局。
    #:    探针第一版把账本放在 `root/` 下，结果 `_collect` 读到的是**空账本**，
    #:    峰值 0.00 MB 看起来像"已经很省"，其实是"什么都没读"。
    led = Ledger(path / "data" / "ledger.jsonl")
    for i in range(n):
        kind = ("eval.verdict" if i % 3 == 0
                else "memory.write" if i % 3 == 1 else "harness.commit")
        p: dict = {"i": i, "text": PAYLOAD_TEXT}
        if kind == "eval.verdict":
            p["run_id"] = f"run-{i}"
            p["reward"] = 0.5
            p["passed"] = True
            p["harness_id"] = f"h-{i}"
        elif kind == "memory.write":
            p["note_id"] = f"note-{i}"
            p["disposition"] = "written"
        else:
            p["harness_id"] = f"h-{i}"
        led.append(kind, p, actor="probe")


def measure(root: Path, label: str) -> int:
    tracemalloc.start()
    tracemalloc.reset_peak()
    before = tracemalloc.get_traced_memory()[0]
    _collect(root, limit=200)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"  {label:<28} 峰值 {peak/1024/1024:8.2f} MB  "
          f"(净增 {(peak-before)/1024/1024:8.2f} MB, 结束时常驻 "
          f"{cur/1024/1024:.2f} MB)")
    return peak


def build_db(root: Path, n_runs: int) -> None:
    """造一个**带投影库**的场景。

    ⛔ 必须有这个场景：只造账本时 `_collect` 走 `db 不存在` 分支，测出来
    的峰值会把**投影侧**那一半完全漏掉。而实测里投影侧才是大头 ——
    `runs()` 全量加载 payload 曾占生产峰值的 76%（5.83 / 7.72 MB）。
    """
    from aether.store import Store
    led = Ledger(root / "data" / "ledger.jsonl")
    s = Store(root / "data" / "aether.db")
    try:
        #: 从账本重建投影 ⇒ 与生产形态一致（verdict 带 run_id + payload）
        print(f"       [投影] rebuild: {s.rebuild_from_ledger(led.events())}")
    finally:
        s.close()


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, n)
        lp = root / "data" / "ledger.jsonl"
        size = lp.stat().st_size
        print(f"场景一：账本 {n} 条 / {size/1024/1024:.2f} MB，"
              f"**无**投影库（db 不存在的分支）")
        assert Ledger(lp).events(), "⛔ 账本是空的 ⇒ 后面的读数全是假的"

        # A. 只物化全量事件，不做任何聚合 —— 这就是"持有"的下限成本
        tracemalloc.start()
        tracemalloc.reset_peak()
        evs = Ledger(lp).events()
        peak_hold = tracemalloc.get_traced_memory()[1]
        del evs
        tracemalloc.stop()
        print(f"  {'A 仅 events() 全量持有':<28} 峰值 {peak_hold/1024/1024:8.2f} MB")

        # B. 完整的 _collect（当前实现）
        peak_no_db = measure(root, "B _collect（无投影库）")
        print(f"  ⇒ 账本侧持有占 _collect 峰值的 "
              f"{peak_hold / peak_no_db * 100:.1f}%")

        # C. 加投影库（真实部署形态）
        print(f"\n场景二：同一份账本 + 投影库")
        build_db(root, n)
        peak_db = measure(root, "C _collect（有投影库）")
        print(f"  ⇒ 投影侧比无库时多 {peak_db/1024/1024:.2f} MB "
              f"（{'投影是主要成本' if peak_db > peak_no_db * 1.5 else '已不再是主要成本'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
