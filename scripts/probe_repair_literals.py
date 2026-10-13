"""取证：一条**通用**的字面量修法，能不能把闭环从停滞里救出来？

## 疑点（来自实测，不是猜测）

默认三轮进化（`aether.cli evolve --rounds 3`）实测：

    round 1  提交成功（dev +0.60 / held-out +0.60，generalize）
    round 2  saturated:stagnated（发展集 3/5，无候选能超过它）
    round 3  saturated:stagnated（同上）

系统自己报的是"任务分布或合成器不够"，但往下挖一层是**修法侧的死角**：

  · `GENERIC_REPAIRS` 只有一个 `revert_control_flow`，它**刻意不动字面量**
    （`aether/repair.py` 的设计：数值常量常常是有意配置，通用修法没立场改）。
  · 于是「常量变异」题**永远判成 hard** —— 难度定义没变，这是对的。
  · 而唯一能修常量题的是 `patch:<env>`（整个文件换回该环境基线），
    它**必然**被 held-out 拦（它是定点化的定义本身）。
  ⇒ 第一轮修完所有控制流题之后，剩下的是"只有定点修法能修"的题，
     而那条路被闸堵死 ⇒ **闭环在第 2 轮起注定停滞**。

这不是"任务不够"，是**能力维度只有一维**：控制流。字面量这一维没有
通用修法可用。

## 候选修法

`revert_literals`：**只**把数值常量按同一遍历序对齐到基线，不动字符串、
不动结构、不动比较/运算。它是通用的（不针对任何环境），不是 `restore_baseline`
（整个文件换回）——它比后者保守得多。

## 判据（三条，缺一条就不值得落地）

    J1 通用性：常量题在 **≥2 个环境** 上被它修好（reward=1）
              —— 只在一个环境上成立 ⇒ 那是巧合或定点化，不是通用能力。
    J2 不越界：控制流题（比较符/运算符变异）**不被**它修好
              —— 若它也修控制流题 ⇒ 实现写错了（它不改 ops）。
    J3 不塌方：加它之后**不是**所有题都变 easy
              —— 若所有题都 easy ⇒ 难度分层失去跨度 ⇒ 泛化闸退化。

⛔ 这是取证脚本，**不改生产代码**：候选修法在这里就地实现，结论成立
   才考虑落地到 `aether/repair.py` / 技能池。
"""
from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether.environment import Environment, synthesize_tasks, verify_source  # noqa: E402

ENVS = ("textscore", "eventflow", "stateflow", "scheduler")
MAX_N = 12


def revert_literals(broken_src: str, base_src: str) -> str:
    """**候选**通用修法：数值常量按遍历序对齐到基线（其余一律不动）。

    ⛔ 与 `restore_baseline` 的区别是**它只动数值常量**：
       字符串、结构、比较符、运算符全部保留 broken 的形态。
       所以它修得好"常量变异"，修不好"控制流变异"。
    """
    b = ast.parse(broken_src)
    g = ast.parse(base_src)

    def nums(tree):
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                    and not isinstance(n.value, bool):
                out.append(n)
        return out

    bo, go = nums(b), nums(g)
    for i in range(min(len(bo), len(go))):
        bo[i].value = go[i].value
    ast.fix_missing_locations(b)
    return ast.unparse(b)


def kind_of(desc: str) -> str:
    if desc.startswith("常量"):
        return "literal"
    if desc.startswith("比较符"):
        return "compare"
    if desc.startswith("运算符"):
        return "binop"
    return "other"


def main() -> int:
    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for name in ENVS:
        env = Environment.from_dir(ROOT / "tasks" / name)
        rep = synthesize_tasks(env, max_n=MAX_N)
        for t in rep.tasks:
            k = kind_of(t.desc)
            try:
                fixed = revert_literals(t.broken_source, env.source)
                r = verify_source(env, source=fixed).reward
            except Exception as e:  # noqa: BLE001 —— 记账
                r = 0.0
                print(f"  ! {t.task_id} 修法异常 {e}")
            rows.append({"env": name, "task": t.task_id, "kind": k,
                         "desc": t.desc, "reward": round(float(r), 3)})
        print(f"[{name}] 合成 {rep.valid}/{rep.attempted} 条", flush=True)

    print(f"\n{'环境':<11}{'形态':<9}{'题数':>5}{'修好':>6}{'修复率':>8}")
    print("-" * 42)
    agg: Dict[Tuple[str, str], List[int]] = {}
    for r in rows:
        agg.setdefault((r["env"], r["kind"]), []).append(1 if r["reward"] >= 1.0 else 0)
    for (env, kind), marks in sorted(agg.items()):
        n = len(marks)
        k = sum(marks)
        print(f"{env:<11}{kind:<9}{n:>5}{k:>6}{k / n:>8.2f}")

    # ── 判据 ────────────────────────────────────────────────────────
    lit_fixed_envs = sorted({e for (e, k), m in agg.items()
                             if k == "literal" and sum(m) > 0})
    lit_n = sum(len(m) for (e, k), m in agg.items() if k == "literal")
    lit_ok = sum(sum(m) for (e, k), m in agg.items() if k == "literal")
    ctrl_n = sum(len(m) for (e, k), m in agg.items() if k in ("compare", "binop"))
    ctrl_ok = sum(sum(m) for (e, k), m in agg.items() if k in ("compare", "binop"))

    checks: List[Tuple[str, bool]] = [
        (f"J1 通用性：常量题在 ≥2 个环境被修好（实测 {lit_fixed_envs}）",
         len(lit_fixed_envs) >= 2),
        (f"J2 不越界：控制流题不被它修好（{ctrl_ok}/{ctrl_n}）",
         ctrl_ok == 0),
        (f"J3 不塌方：常量题也没有全被修好（{lit_ok}/{lit_n} < 全部）",
         lit_ok < lit_n),
        ("J4 常量题确实存在（否则这条修法没有对象）", lit_n > 0),
    ]
    print()
    for name, ok in checks:
        print(f"  {'✅' if ok else '❌'} {name}")

    all_pass = all(o for _, o in checks)
    print()
    if all_pass:
        print("结论：✅ 通用字面量修法成立 ⇒ 值得落地"
              "（进**技能池**，不进 GENERIC_REPAIRS —— 后者会改写难度定义）。")
    else:
        print("结论：❌ 不成立 ⇒ 别落地。")

    out = {"max_n": MAX_N, "rows": rows,
           "agg": {f"{e}/{k}": {"n": len(m), "fixed": sum(m)}
                   for (e, k), m in agg.items()},
           "checks": [{"name": n, "ok": bool(o)} for n, o in checks],
           "all_pass": all_pass, "cost_s": round(time.time() - t0, 2)}
    ts = time.strftime("%Y%m%d_%H%M%S")
    p = ROOT / "docs" / f"probe_repair_literals_{ts}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}   cost={out['cost_s']}s")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
