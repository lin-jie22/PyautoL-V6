#!/usr/bin/env python3
"""诊断：链跑完**仍修不好**的题，缺口落在哪 —— 关键在**收集口径是否一致**。

## 起因

`probe_growth_ceiling` A 段实测：16 条 hard 题里链修好 12 条，**残差 4 条**，
desc 全为「常量 x→y」。其中一条 evidence：

        got=['review', 1]  expect=['review', False]

`False` 变成 `1` —— 变异器 `visit_Constant` 用 `isinstance(v, (int, float))`
选点，而 `bool` 是 `int` 的子类 ⇒ **bool 被当成数值常量 +1**。
修法 `revert_literals` 的收集器却**排除 bool** ⇒ 两边"常量序列"长度不同
⇒ `min(len)` 只对齐前 N 个 ⇒ **后面全部错位，把本来对的常量改错**。

⛔⛔ 这是自洽性 bug：修法为了"不把 `if x` 变成 `if 1`"而排除 bool，
    结果因为与变异器口径不一致，造成了更严重的错位污染。
    本脚本把两种口径下的序列长度都打出来，让错位**可见**。
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import (Environment, mutate_source, verify_source)  # noqa: E402
from aether.repair import (classify_difficulty, revert_control_flow,  # noqa: E402
                           revert_literals)

ENVS = ("textscore", "eventflow", "stateflow", "scheduler")
MAX_N = 12
TIMEOUT = 10.0


def chain_apply(src: str, base: str) -> str:
    out = src
    for fn in (revert_control_flow, revert_literals):
        out = fn(out, base)
    return out


def consts(src: str, include_bool: bool) -> list:
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            if not include_bool and isinstance(n.value, bool):
                continue
            out.append(n.value)
    return out


def diff_types(a_src: str, b_src: str, cap: int = 6) -> list:
    """按 walk 序列出前 `cap` 个**不同**节点（跳过 Module：它含全部子节点）。"""
    na = list(ast.walk(ast.parse(a_src)))
    nb = list(ast.walk(ast.parse(b_src)))
    out = []
    for i in range(min(len(na), len(nb))):
        if isinstance(na[i], ast.Module):
            continue
        if ast.dump(na[i]) != ast.dump(nb[i]):
            out.append({"i": i, "type": type(na[i]).__name__,
                        "fixed": ast.dump(na[i])[:70],
                        "baseline": ast.dump(nb[i])[:70]})
            if len(out) >= cap:
                break
    return out


def main() -> int:
    rows = []
    for name in ENVS:
        env = Environment.from_dir(str(ROOT / "tasks" / name))
        base = env.source
        nb_base, wb_base = len(consts(base, False)), len(consts(base, True))
        resid = []
        for i in range(MAX_N):
            try:
                src, desc = mutate_source(base, which=i)
            except Exception:                                  # noqa: BLE001
                continue
            try:
                after = verify_source(env, source=src, timeout=TIMEOUT)
            except Exception:                                  # noqa: BLE001
                continue
            if after.passed:
                continue
            fixed = chain_apply(src, base)
            r = verify_source(env, source=fixed, timeout=TIMEOUT).reward
            if r >= 1.0:
                continue
            resid.append({
                "task_id": f"{name}::m{i}", "desc": desc, "r_chain": r,
                "n_const": {"base_nobool": nb_base, "base_withbool": wb_base,
                            "broken_nobool": len(consts(src, False)),
                            "broken_withbool": len(consts(src, True)),
                            "fixed_nobool": len(consts(fixed, False)),
                            "fixed_withbool": len(consts(fixed, True))},
                "consts_aligned": consts(fixed, True) == consts(base, True),
                "diff_types": diff_types(fixed, base),
            })
        rows.append({"env": name, "n_residual": len(resid),
                     "residual": resid})

    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"),
               "note": "残差 = 链(control_flow+literals)跑完仍 reward<1 的 hard 题",
               "rows": rows}
    out = ROOT / "docs" / ("probe_residual_diag_"
                           + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    tot = sum(r["n_residual"] for r in rows)
    print(f"残差合计 = {tot}\n")
    for r in rows:
        if not r["residual"]:
            continue
        print(f"── {r['env']}")
        for x in r["residual"]:
            print(f"  {x['task_id']}  desc={x['desc']!r}  r_chain={x['r_chain']}")
            print(f"      常量长度 {x['n_const']}")
            print(f"      consts_aligned={x['consts_aligned']}")
            for d in x["diff_types"]:
                print(f"      diff[{d['i']}] {d['type']}: "
                      f"fixed={d['fixed']} base={d['baseline']}")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
