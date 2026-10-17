#!/usr/bin/env python3
"""探针：**变异维度的采样分布**（r51 的事前取证 + 事后验收）。

要回答的问题只有一个：

    `synthesize_tasks(max_n=N)` 采到的 N 条题，分别属于哪个变异维度？

⛔ 为什么需要它：`which` 是 `_Mutator` 的**全局 DFS 序号** —— 遍历时
    遇到的第 i 个可变异点（不分种类）就是 `which=i`。于是 AST 里数量
    最多的那种算子会把前 N 个序号**全部占满**，后加的维度一条题都进不来。

    r50 实测到的就是这个：`unaryop` 维度单独跑能出 14 条题，但在
    `max_n=12` 的 dev 采样里是 **0 条** —— 不是世界里没有，是**采样
    被别的维度挤掉了**（与 r49 那个「变异采样上限」是同一个病的两面）。

⛔ 判据必须同源：这里**不另写一份**点位枚举，而是真的调用
    `mutate_source(which=i)` 并从它返回的 `desc` 归类 —— 与合成器走
    同一条代码路径（第 2 条纪律：判据/探针必须与被测对象同源）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether.environment import (  # noqa: E402
    ALL_MUTATION_KINDS, DEFAULT_MUTATION_KINDS, Environment,
    mutate_source, synthesize_tasks)

#: ⛔ `desc` → 维度名的归类表。⛔ 它必须与 `_Mutator` 里写的描述**逐字对应**
#:    （`desc` 是唯一能从外部看到的维度指纹）。改 `_Mutator` 的描述文字
#:    ⇒ 这里必须同步，否则归类会静默落到 `?`（那正是"量具自己撒谎"）。
#:    ⇒ 契约 `contract_sampling_test` S1 组盯这件事：逐个 which 反查。
_PREFIX = (
    ("比较符反转", "compare"),
    ("运算符替换", "binop"),
    ("布尔运算", "boolop"),
    ("取反 删除 not", "unaryop"),
    ("字符串常量", "string"),
    ("常量 ", "constant"),
)


def kind_of(desc: str) -> str:
    """从变异描述归类维度。归不上 ⇒ `?`（显式未知，不许猜成别的）。"""
    for pref, name in _PREFIX:
        if desc.startswith(pref):
            return name
    return "?"


#: 一个点位**存在但无法变异**时的标记（不是"点位枚举结束"）。
UNAVAILABLE = "?"


def site_table(env: Environment, kinds, limit: int = 64) -> list:
    """枚举这个环境的变异点维度序列（`which` → 维度）。

    ⛔⛔ 为什么**不能**遇到异常就停（2026-10-17 实测踩到）：
        `mutate_source(which=i)` 抛 `ValueError("没有可变异点")` 有两**种**
        完全不同的原因 ——

          (a) `i` **超出**点位总数（真结束了）；
          (b) 点位**存在但变不动**：`_string_variant` 返回 None（空串）、
              或比较符不在 `_FLIP_OPS` 里（`is` / `in` / `is not` 等）。

        ⛔ 原版一遇异常就 `break` ⇒ 遇到 (b) 就把整个环境判成"点位只有
        几个" ⇒ 低估点位 ⇒ 均衡采样的对照组从一开始就建在错的底数上。
        （第 40 条：**一个维度产出恒为 0 时，先怀疑工具，再怀疑世界。**）

        ⇒ 改成：逐个试到 limit，变不动的点位记 `UNAVAILABLE`，尾部连续的
        `UNAVAILABLE` 才当作"结束了"并裁掉。
    """
    out = []
    for i in range(limit):
        try:
            _, desc = mutate_source(env.source, which=i, kinds=kinds)
        except Exception:                                       # noqa: BLE001
            out.append(UNAVAILABLE)
            continue
        out.append(kind_of(desc))
    while out and out[-1] == UNAVAILABLE:
        out.pop()
    return out


def compare_modes(names, kinds, max_n, root) -> list:
    """同变量对照：顺序采样 vs 按维度均衡采样。

    ⛔ 对照组**必须走真实实现**（`synthesize_tasks(balanced=...)`），
        不能在这里另写一份"轮流取点"再自己判 F2P —— 那是第二套逻辑，
        一旦它与生产实现漂移，对照结论就是假的（第 2 条纪律：探针与被测
        同源）。所以它只做两件事：调真实合成、按 `desc` 归类维度。
    """
    rows = []
    for n in names:
        env = Environment.from_dir(root / n, env_id=n)
        sites = site_table(env, kinds, limit=200)
        rep_s = synthesize_tasks(env, max_n=max_n, kinds=kinds,
                                 balanced=False)
        rep_b = synthesize_tasks(env, max_n=max_n, kinds=kinds,
                                 balanced=True)
        rows.append({
            "env": n, "n_sites": len(sites),
            "site_kinds": dict(Counter(sites)),
            "seq_tried": rep_s.attempted, "seq_valid": len(rep_s.tasks),
            "seq_dims": dict(Counter(kind_of(t.desc) for t in rep_s.tasks)),
            "bal_tried": rep_b.attempted, "bal_valid": len(rep_b.tasks),
            "bal_dims": dict(Counter(kind_of(t.desc) for t in rep_b.tasks)),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--envs", default="", help="逗号分隔；留空=扫 tasks/ 全部")
    ap.add_argument("--kinds", default=",".join(ALL_MUTATION_KINDS))
    ap.add_argument("--max-n", default="12")
    ap.add_argument("--sites", default="40", help="枚举多少个变异点")
    ap.add_argument("--compare", action="store_true",
                    help="同变量对照：顺序采样 vs 按维度均衡采样（要跑判据，慢）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1] / "tasks"
    names = [x.strip() for x in args.envs.split(",") if x.strip()] or sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and (p / "task.py").exists() and (p / "tests.json").exists())
    kinds = tuple(x.strip() for x in args.kinds.split(",") if x.strip())
    max_n = int(args.max_n)

    if args.compare:
        rows = compare_modes(names, kinds, max_n, root)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        print(f"同变量对照：kinds={','.join(kinds)}  尝试次数上限 max_n={max_n}"
              f"（两种模式**尝试次数相同**，只差取点顺序）")
        print()
        print(f"{'env':<12}{'点位':>5}  {'顺序:题/维度':<28}{'均衡:题/维度':<28}")
        print("-" * 80)
        tot_s = Counter()
        tot_b = Counter()
        for r in rows:
            sd = " ".join(f"{k}={v}" for k, v in sorted(r["seq_dims"].items()))
            bd = " ".join(f"{k}={v}" for k, v in sorted(r["bal_dims"].items()))
            print(f"{r['env']:<12}{r['n_sites']:>5}  "
                  f"{str(r['seq_valid']) + ' ' + sd:<28}"
                  f"{str(r['bal_valid']) + ' ' + bd:<28}")
            tot_s.update(r["seq_dims"])
            tot_b.update(r["bal_dims"])
        print()
        sv = sum(tot_s.values())
        bv = sum(tot_b.values())
        print(f"合计出题：顺序 {sv} 条（{len(tot_s)} 个维度） / "
              f"均衡 {bv} 条（{len(tot_b)} 个维度）")
        print("  顺序维度：", " ".join(
            f"{k}={v}" for k, v in sorted(tot_s.items())) or "（无）")
        print("  均衡维度：", " ".join(
            f"{k}={v}" for k, v in sorted(tot_b.items())) or "（无）")
        return 0

    rows = []
    for n in names:
        env = Environment.from_dir(root / n, env_id=n)
        sites = site_table(env, kinds, limit=int(args.sites))
        head = sites[:max_n]
        rep = synthesize_tasks(env, max_n=max_n, kinds=kinds)
        got = Counter(kind_of(t.desc) for t in rep.tasks)
        rows.append({
            "env": n,
            "n_sites_enumerated": len(sites),
            "site_kinds_all": dict(Counter(sites)),
            f"site_kinds_head{max_n}": dict(Counter(head)),
            "n_tasks": len(rep.tasks),
            "tasks_by_kind": dict(got),
            "dims_in_head": len(set(head)),
            "dims_in_tasks": len([k for k, v in got.items() if v]),
        })

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"口径：kinds={','.join(kinds)}  max_n={max_n}  "
          f"envs={len(rows)}（tasks/ 全部）")
    print()
    print(f"{'env':<12}{'点位':>5}{'前N点位维度':>14}{'题数':>6}  出题维度分布")
    print("-" * 78)
    for r in rows:
        head_dims = ",".join(sorted(r[f"site_kinds_head{max_n}"]))
        td = " ".join(f"{k}={v}" for k, v in sorted(r["tasks_by_kind"].items()))
        print(f"{r['env']:<12}{r['n_sites_enumerated']:>5}{head_dims:>14}"
              f"{r['n_tasks']:>6}  {td}")
    print()
    tot_sites = Counter()
    tot_tasks = Counter()
    for r in rows:
        tot_sites.update(r["site_kinds_all"])
        tot_tasks.update(r["tasks_by_kind"])
    print("合计 · 可变异点维度：",
          " ".join(f"{k}={v}" for k, v in sorted(tot_sites.items())))
    print("合计 · 实际出题维度：",
          " ".join(f"{k}={v}" for k, v in sorted(tot_tasks.items())))
    print()
    print("⛔ 读法：如果「可变异点」里有某维度而「实际出题」里它是 0，"
          "那就是被前序维度挤掉了（采样问题，不是世界里没有）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
