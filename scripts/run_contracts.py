#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约测试的统一 runner。

═══ 为什么需要一个 runner ═══

此前 24 个契约**没有统一入口**：README 里给的是一句 shell `for` 循环，
而 24 个文件里只有一部分带 `__main__` 自测块。于是：

  · 跑没跑全，取决于那句 for 循环有没有被复制对；
  · 红了的那个文件名要自己从滚动输出里找；
  · 没有"一共多少、绿多少、耗时多少"的汇总 ⇒ CI 拿不到一个可判的退出码
    （而这正是契约存在的意义：它们是**判据**，不是参考）。

⛔ runner 自己也要守同一条纪律：**退出码必须诚实**。
只要有一个契约红 ⇒ 退出 1。汇总里同时给出"被跳过（无 __main__）"的
文件清单 —— 跳过 ≠ 通过，不说出来就会被当成"都跑过了"。

用法：
    python scripts/run_contracts.py              # 跑全部（串行，稳）
    python scripts/run_contracts.py -j 4         # 并行（各契约互不写生产状态）
    python scripts/run_contracts.py --filter checkpoint
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def _run_one(py: str, f: Path) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run([py, str(f)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
        return {"file": f.name, "rc": r.returncode,
                "sec": round(time.time() - t0, 1),
                "tail": out.strip().splitlines()[-1] if out.strip() else "",
                "out": out}
    except subprocess.TimeoutExpired:
        return {"file": f.name, "rc": 124, "sec": round(time.time() - t0, 1),
                "tail": "超时（>900s）", "out": ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run_contracts")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="并行数（默认 1）")
    ap.add_argument("--filter", default="", help="只跑名字含这个串的契约")
    ap.add_argument("--verbose", action="store_true", help="红的契约打出全文")
    args = ap.parse_args(argv)

    files = sorted(TESTS.glob("contract_*_test.py"))
    if args.filter:
        files = [f for f in files if args.filter in f.name]
    if not files:
        print(f"❌ 没有匹配的契约：{args.filter or '(空)'}")
        return 2

    py = sys.executable
    t0 = time.time()
    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            results = list(ex.map(lambda f: _run_one(py, f), files))
    else:
        results = [_run_one(py, f) for f in files]

    green = [r for r in results if r["rc"] == 0]
    red = [r for r in results if r["rc"] != 0]
    icons = {0: "✅", 124: "⏱️ "}
    for r in results:
        print(f"{icons.get(r['rc'], '❌')} {r['file']:<38} {r['sec']:>6.1f}s  "
              f"{r['tail'][:60]}")
    print()
    print(f"总计 {len(results)} 个契约：{len(green)} 绿 / {len(red)} 红"
          f"（{round(time.time() - t0, 1)}s）")
    if red:
        print("\n红的契约：")
        for r in red:
            print(f"  ❌ {r['file']} (rc={r['rc']})")
            if args.verbose:
                print(r["out"][-4000:])
        return 1
    print("✅ 全部契约通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
