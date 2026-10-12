#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真模型端到端：把"模型到底赢没赢"变成一个**可复现的读数**。

## 为什么要有这个脚本

真 proposer 在端到端循环里"跑通了"和"赢过了"是两件事：

    跑通 = 模型产出合法 capsule、过语法闸、能被应用、参与选择
    赢过 = 它提的变异同时过了非回归闸 **和** 泛化闸，被真正提交

⛔ 这两件事在日志里长得几乎一样（都有候选、都有 reward、都有裁决），
但没有 "赢过" 那一列时，很容易把"跑通了"当成"模型会自我改进了"。
这里把区分它们的数字单独打出来，每次跑都能和上一次直接对表。

## 判据一条都不放宽

这个脚本**只观测**，不改任何判据、不放宽任何闸。如果模型赢不了，
结论就是"这个模型在这个任务上赢不了" —— 那是模型的能力边界，
不是机制缺陷（机制由 `contract_proposer_test.py` 独立保证）。

用法：
    python scripts/llm_e2e.py                      # 默认 Qwen3.5-4B，2 轮
    python scripts/llm_e2e.py --model xxx --rounds 3
    python scripts/llm_e2e.py --no-llm             # 对照：模板提议器（离线）
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if not ROOT.joinpath("tasks").exists():
    sys.exit("找不到 tasks/ —— 请在仓库根运行")

import aether.cli as cli  # noqa: E402


class Args:
    def __init__(self, root: str, **kw):
        self.root = root
        self.rounds = kw.get("rounds", "2")
        self.group = "3"
        self.max_tasks = kw.get("max_tasks", "12")
        self.capsules = "both"
        self.seed = "0"
        self.dev_env = kw.get("dev_env", "textscore")
        self.heldout_env = kw.get("heldout_env", "eventflow")
        self.heldout = "on"
        self.proposer = kw.get("proposer", "llm")
        self.llm_model = kw.get("model", "")
        self.heldout_floor = "0.0"
        self.embedder = "hash"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen3.5-4B:latest")
    ap.add_argument("--rounds", default="2")
    ap.add_argument("--max-tasks", default="12")
    ap.add_argument("--dev-env", default="textscore")
    ap.add_argument("--heldout-env", default="eventflow")
    ap.add_argument("--root", default="")
    ap.add_argument("--no-llm", action="store_true",
                    help="对照组：用模板提议器（离线，秒级）")
    ap.add_argument("--no-rejected", action="store_true",
                    help="对照组：**不**把失败史注入 prompt（单变量："
                         "隔离'失败史究竟是帮忙还是把模型逼死'）")
    a = ap.parse_args()

    if a.no_rejected:
        #: ⛔ 只在本脚本里切断注入，**不改生产代码**：
        #    失败史是"记忆影响提议"，和上一轮"记忆把进化卡死"同一个形态，
        #    必须能用单变量对照证明它是帮忙而不是添乱。
        from aether.providers import MemoryAwareProposer
        MemoryAwareProposer._rejected_for = lambda self, w: []

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = a.root or td
        if not a.root:
            import shutil
            shutil.copytree(ROOT / "tasks", Path(td) / "tasks")
        t0 = time.time()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_evolve(Args(root, rounds=a.rounds, max_tasks=a.max_tasks,
                                dev_env=a.dev_env, heldout_env=a.heldout_env,
                                proposer="template" if a.no_llm else "llm",
                                model=a.model))
        el = time.time() - t0
        j = json.loads(buf.getvalue())

    prop = j.get("proposer") or {}
    rows = []
    n_valid = 0
    n_gate_ok = 0
    n_gen_ok = 0
    for r in j.get("rounds", []):
        g = r.get("generalization") or {}
        if g.get("verdict") == "generalize":
            n_gen_ok += 1
        for c in r.get("candidates", []):
            n_valid += 1
            if c.get("gate_passed"):
                n_gate_ok += 1
        rows.append((r["round_no"], r["baseline_reward"],
                     len(r.get("candidates", [])),
                     str(r.get("committed"))[:8], g.get("verdict"),
                     g.get("heldout_gain")))

    print("=" * 64)
    print(f"真模型端到端（{a.model if not a.no_llm else 'template 对照组'}）"
          f"  dev={a.dev_env} held-out={a.heldout_env}  {el:.1f}s")
    print("=" * 64)
    print(f"{'轮':<4}{'基线':<8}{'候选数':<8}{'提交':<10}{'泛化裁决':<12}{'held-out 增益'}")
    for row in rows:
        print(f"{row[0]:<4}{row[1]:<8}{row[2]:<8}{str(row[3]):<10}"
              f"{str(row[4]):<12}{row[5]}")
    print("-" * 64)
    print(f"产出候选（过语法闸）        {n_valid}")
    print(f"  └ 过非回归闸              {n_gate_ok}")
    print(f"  └ 泛化闸放行              {n_gen_ok}")
    print(f"最终 improved               {j.get('improved')}")
    print()
    print("⛔ 判读口径：")
    print("  跑通 = 产出候选 > 0 且有过非回归闸的")
    print("  赢过 = improved == True（必须同时过泛化闸）")
    if prop.get("error"):
        print(f"  ⚠️ 提议器报错：{prop['error']}")
    if prop.get("rejects"):
        print(f"  ⚠️ 解析记账：{prop['rejects']}")
    if prop.get("rejected_shown"):
        print(f"  注入的失败史：{prop['rejected_shown']}")
    print(f"  注入了 held-out 规则：{prop.get('heldout_note_shown')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
