#!/usr/bin/env python3
"""取证：第三个通用维度（boolop: `and`/`or`）是否真的**正交**。

## 判据（可判定，不靠声明）

三个通用修法各只动自己那一类节点：

        revert_control_flow ⇒ 比较符 / 运算符
        revert_literals     ⇒ 数值常量
        revert_boolops      ⇒ and / or

"正交"必须能被测出来，否则就是自说自话。本脚本跑 3×3 交叉矩阵：

        行 = 变异类型（compare+binop / constant / boolop）
        列 = 修法（flow / literals / boolops）

要求：
  · **对角线全 1.0** —— 每一维修得好自己那类题；
  · **非对角线全 0.0** —— 任取一维修另一类的题都修不好（不越界）。

对角线成立 ⇒ 这一维真的有独立能力；非对角线成立 ⇒ 它不是"换个写法
的同一个修法"，也不靠顺手修好别的层来刷分。

⛔ 全部 reward 来自 `verify_source`（执行判据），没有自述、没有估计。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import (Environment, synthesize_tasks,  # noqa: E402
                                verify_source)
from aether.repair import (revert_boolops, revert_control_flow,  # noqa: E402
                           revert_literals)

ENVS = ("textscore", "eventflow", "stateflow", "scheduler")
MAX_N = int(os.environ.get("PROBE_MAX_N", "12"))
TIMEOUT = 10.0

#: 行 = 变异种类，列 = 修法
MUT_SETS = {
    "compare+binop": ("compare", "binop"),
    "constant": ("constant",),
    "boolop": ("boolop",),
}
REPAIRS = {
    "flow": revert_control_flow,
    "literals": revert_literals,
    "boolops": revert_boolops,
}


def rate(env: Environment, tasks, fn) -> float:
    """修好率：修完能过环境判据的任务占比（执行判据，不是文本比对）。"""
    if not tasks:
        return 0.0
    ok = 0
    for t in tasks:
        try:
            fixed = fn(t.broken_source, env.source)
            if verify_source(env, source=fixed, timeout=TIMEOUT).passed:
                ok += 1
        except Exception:                                       # noqa: BLE001
            continue
    return round(ok / len(tasks), 4)


def main() -> int:
    rows = []
    for name in ENVS:
        env = Environment.from_dir(str(ROOT / "tasks" / name))
        cells = {}
        for mlabel, kinds in MUT_SETS.items():
            tasks = synthesize_tasks(env, max_n=MAX_N, kinds=kinds).tasks
            cells[mlabel] = {"n": len(tasks),
                             **{rlabel: rate(env, tasks, fn)
                                for rlabel, fn in REPAIRS.items()}}
        rows.append({"env": name, "matrix": cells})

    #: ── 结论：对角线 / 非对角线分开统计（不许混成一个数）──────────
    #: ⛔ 对角线只统计**该环境真的造出了这类题**的格子（n>0）。
    #:    stateflow 源码里没有 `and/or` ⇒ boolop 行 n=0 ⇒ 那是"无题可考"，
    #:    不是"修不好"。把两者混成一个布尔值 ⇒ 又造出一对分不开的孪生故障。
    def _is_diag(m: str, m2: str) -> bool:
        return ((m == "compare+binop" and m2 == "flow")
                or (m == "constant" and m2 == "literals")
                or (m == "boolop" and m2 == "boolops"))

    diag, off = [], []
    for r in rows:
        for m, cell in r["matrix"].items():
            for m2, v in cell.items():
                if m2 == "n":
                    continue
                (diag if _is_diag(m, m2) else off).append(
                    (r["env"], m, m2, v, cell["n"]))

    _scored = [x for x in diag if x[4] > 0]
    ok_diag = all(x[3] >= 1.0 for x in _scored)
    ok_off = all(x[3] == 0.0 for x in off)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "max_n": MAX_N,
        "mut_sets": {k: list(v) for k, v in MUT_SETS.items()},
        "verdict": {"diagonal_all_fixed": ok_diag,
                    "off_diagonal_all_zero": ok_off},
        "diagonal": diag, "off_diagonal": off,
        "rows": rows,
    }
    out = ROOT / "docs" / ("probe_boolop_dimension_"
                           + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print("交叉矩阵（行=变异类型，列=修法）→ 修好率\n")
    hdr = "            " + "".join(f"{c:>11s}" for c in REPAIRS)
    print(hdr)
    for r in rows:
        print(f"  {r['env']}")
        for m, cell in r["matrix"].items():
            line = "".join(f"{cell[c]:>11.2f}" for c in REPAIRS)
            print(f"    {m:<12s}{line}   (n={cell['n']})")
    print(f"\n对角线全修好={ok_diag}  非对角线全为 0={ok_off}")
    print(f"→ {out}")
    return 0 if (ok_diag and ok_off) else 1


if __name__ == "__main__":
    sys.exit(main())
