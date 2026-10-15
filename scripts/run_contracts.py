#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约测试的统一 runner —— **`aether check` 的薄壳**。

═══ 为什么存在 ═══

此前 36 个契约**没有统一入口**：README 给的是一句 shell `for` 循环，
而那句循环**不是闸** —— 没有失败聚合 ⇒ 退出码等于最后一次迭代 ⇒
中间任何一份契约变红都被吞掉，整轮照样"成功"。而 V6 的第一条命题是
"每一次自我修改都必须能被独立证伪"，验证这个命题的动作本身却不可信。

⛔⛔ 本文件**不再自己实现判据**（2026-10-15 收敛）。此前存在两套 runner：

    scripts/run_contracts.py   只跑默认模式（不读 `SUPPORTED_MODES`）
    aether/check.py            两种模式 + 五态分类 + 结构化报告

两套 ⇒ 两份计数口径 ⇒ "跑过了"取决于你用了哪条命令。这与本项目反复惩罚的
"判据有两份实现就等于没有判据"是同一个病。现在**判据只有一份**
（`aether.check`），本文件只保留一个便利：默认并行 + 人类友好的输出。

用法：
    python scripts/run_contracts.py              # 并行 4（默认）
    python scripts/run_contracts.py -j 1         # 串行（稳，便于读输出）
    python scripts/run_contracts.py --filter ledger
    python scripts/run_contracts.py --json       # 与 `aether check --json` 同构
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    from aether import check as C

    ap = argparse.ArgumentParser(prog="run_contracts")
    ap.add_argument("-j", "--jobs", type=int, default=4,
                    help="并行数（默认 4；契约各用临时目录，互不写生产状态）")
    #: ⛔ 保留原参数名 `--filter` 并同时接受 `--only`：改掉参数名会让
    #:    README/CI 里已写好的命令行**静默失效**（argparse 直接报错反而是
    #:    好事，怕的是被当成"跑过了"）。
    ap.add_argument("--filter", "--only", dest="only", default="",
                    help="只跑名字含这个串的契约")
    ap.add_argument("--json", action="store_true", help="输出结构化报告")
    ap.add_argument("--verbose", action="store_true",
                    help="逐份契约打印一行（默认只打失败项与结论）")
    ap.add_argument("--timeout", type=float, default=C.DEFAULT_TIMEOUT_S,
                    help="单份契约超时秒数（超时记 timeout，不是失败）")
    args = ap.parse_args(argv)

    rep = C.run_checks(ROOT, only=args.only or None, timeout_s=args.timeout,
                       jobs=max(1, args.jobs))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return int(rep["exit_code"])
    if args.verbose:
        icons = {"pass": "✅", "fail": "❌", "timeout": "⏱️ ",
                 "error": "💥", "skip": "⏭️ "}
        for r in rep["runs"]:
            print(f"{icons.get(r['status'], '?')} {r['script']:<40} "
                  f"[{r['mode']:<7}] {r['seconds']:>6.1f}s  "
                  f"{r['reason'] or r['tail'][:50]}")
        print()
    print(C.summarize(rep))
    return int(rep["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
