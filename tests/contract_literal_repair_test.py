#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：通用字面量修法 —— 闭环的**第二个能力维度**。

## 它为什么必须存在（不是"多加一个技能"）

只有 `fix_control_flow` 时，闭环有一个结构性死角：

  · `revert_control_flow` 刻意**不动字面量**（数值常量常是有意配置）；
  · 于是「常量变异」题永远判 hard（难度定义没错，这是对的）；
  · 唯一能修常量题的是 `patch::<env>`（整个文件换回该环境基线），
    而它**必然**被 held-out 拦 —— 它是"定点化"的定义本身。

⇒ 第一轮修完所有控制流题之后，剩下的题只有定点修法能修，而那条路被闸堵死
   ⇒ 闭环从第 2 轮起**注定停滞**。实测（2026-10-13，默认三轮）：

       round 1  提交成功（dev +0.60 / held-out +0.60）
       round 2  saturated:stagnated
       round 3  saturated:stagnated

这不是"任务不够"，是**能力维度只有一维**。`fix_literals` 补上第二维。

## 契约（四条，每条都配反证）

    A 修得对：常量题修得好；控制流题**一字不动**（不越界）
    B 不改判据：常量题**仍是 hard**（它没进 GENERIC_REPAIRS）
    C 真被走到：L1 路由 + L3 技能装上后，agent **真的调用**了它
    D 反证：换成 restore_baseline ⇒ A 转红；塞进 GENERIC_REPAIRS ⇒ B 转红

⛔ B 是这条契约里最重要的：**把通用修法加进判据集合会改写难度定义**
   （所有常量题变 easy ⇒ 难度跨度消失 ⇒ 泛化闸退化）。这条闸存在的意义
   就是防止后人"顺手"把它加进 GENERIC_REPAIRS。

跑法（离线，秒级）：
    python tests/contract_literal_repair_test.py
"""
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

from aether.environment import Environment, synthesize_tasks, verify_source  # noqa: E402
from aether.repair import (GENERIC_REPAIRS, classify_difficulty,  # noqa: E402
                           restore_baseline, revert_literals)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: 只取两个环境，保证秒级：判据要的是"跨环境"，不是"全环境"。
ENV_NAMES = ("textscore", "eventflow")
ENVS = {n: Environment.from_dir(ROOT / "tasks" / n) for n in ENV_NAMES}


def _pick(max_n=12):
    """合成并分类：(env, task) 按变异形态分组。"""
    out = {"literal": [], "control": []}
    for n, e in ENVS.items():
        for t in synthesize_tasks(e, max_n=max_n).tasks:
            d = t.desc or ""
            if d.startswith("常量"):
                out["literal"].append((n, t))
            elif d.startswith(("比较符", "运算符")):
                out["control"].append((n, t))
    return out


PICK = _pick()
LIT = PICK["literal"]
CTL = PICK["control"]

print(f"== 样本：常量题 {len(LIT)} 条 / 控制流题 {len(CTL)} 条 ==")
ck("样本：两类变异都采到了（否则下面的判据无从对照）",
   len(LIT) > 0 and len(CTL) > 0, f"literal={len(LIT)} control={len(CTL)}")


def _reward(env_name, task, fn) -> float:
    e = ENVS[env_name]
    try:
        fixed = fn(task.broken_source, e.source)
        return float(verify_source(e, source=fixed).reward)
    except Exception:  # noqa: BLE001 —— 修法抛错 ⇒ reward 0，不吞
        return 0.0


print("\n== A 组：修得对，且不越界 ==")
lit_ok = {n for n, t in LIT if _reward(n, t, revert_literals) >= 1.0}
ck("A1 常量题被它修好（至少 1 条）", len(lit_ok) > 0,
   f"修好的环境={sorted(lit_ok)}")
ck("A2 通用性：≥2 个环境的常量题都被修好（不是只此一地）",
   len(lit_ok) >= 2, f"修好的环境={sorted(lit_ok)}")
ctl_fixed = [(n, t.task_id) for n, t in CTL
             if _reward(n, t, revert_literals) >= 1.0]
ck("A3 不越界：控制流题**不被**它修好（它不改 ops）",
   not ctl_fixed, f"被修好={ctl_fixed}")
unchanged = sum(1 for n, t in CTL
                if revert_literals(t.broken_source, ENVS[n].source)
                == t.broken_source)
ck("A4 对控制流题**一字不动**（不是『改了但没修好』）",
   unchanged == len(CTL), f"一字不动 {unchanged}/{len(CTL)}")

print("\n== B 组：它**没有**改写难度定义 ==")
ck("B1 `fix_literals` 不在 GENERIC_REPAIRS（进去了 ⇒ 常量题变 easy ⇒ "
   "难度跨度消失 ⇒ 泛化闸退化）",
   "fix_literals" not in GENERIC_REPAIRS,
   f"现有={sorted(GENERIC_REPAIRS)}")
hard_kept = []
for n, t in LIT:
    lv = classify_difficulty(ENVS[n], t.broken_source)
    hard_kept.append((n, t.task_id, lv))
ck("B2 常量题**仍是 hard**（难度判据未被新修法污染）",
   all(lv == "hard" for _n, _t, lv in hard_kept),
   f"实测={hard_kept}")

print("\n== C 组：有机制 ≠ 这条路被走到 ==")
from aether.cli import _default_runner, TASK_CLASS          # noqa: E402
from aether.genome import Harness                           # noqa: E402

lit_env, lit_task = LIT[0]
envs = {n: Environment.from_dir(ROOT / "tasks" / n) for n in ENV_NAMES}
runner = _default_runner(envs)
h = Harness(
    meta_rules="V6 元规则：奖励只来自执行判据。",
    tools=("read_file", "write_file", "python_exec"),
    skills={"fix_literals": "通用字面量修法：只对齐数值常量"},
    routing={TASK_CLASS: "fix_literals"},
    memory_policy={"recall_topk": 5})
spec = {"env_id": lit_env, "broken_source": lit_task.broken_source,
        "topic": lit_env, "task_class": TASK_CLASS}
v = runner(h, spec)
strategy = (v.evidence or {}).get("strategy", "")
ck("C1 agent 真的把路由走到了 `fix_literals`（不是『技能装了没人调』）",
   strategy == "fix_literals", f"strategy={strategy!r}")
ck("C2 走通之后 reward=1（判据只来自执行，不是自称）",
   float(v.reward) >= 1.0, f"reward={v.reward}")

print("\n== D 组：反证（事故注入，判据必须能转红）==")
# D1 把通用字面量修法换成"整文件换回基线" ⇒ 控制流题也会被修好 ⇒ A3 必须能抓到
ctl_fixed_bad = [(n, t.task_id) for n, t in CTL
                 if _reward(n, t, restore_baseline) >= 1.0]
ck("D1 事故版（restore_baseline）会被 A3 抓到：它把控制流题也修好了",
   len(ctl_fixed_bad) > 0,
   f"反证失效：事故版也没修好控制流题 ⇒ A3 是假判据")

# D2 把它塞进 GENERIC_REPAIRS ⇒ 常量题变 easy ⇒ B2 必须能抓到
_orig = dict(GENERIC_REPAIRS)
try:
    GENERIC_REPAIRS["revert_literals"] = revert_literals
    lv_after = classify_difficulty(ENVS[lit_env], lit_task.broken_source)
    ck("D2 事故版（塞进 GENERIC_REPAIRS）会被 B2 抓到：常量题变 easy 了",
       lv_after == "easy",
       f"反证失效：塞进去后仍是 {lv_after} ⇒ B2 是假判据")
finally:
    GENERIC_REPAIRS.clear()
    GENERIC_REPAIRS.update(_orig)
ck("D3 事故版已还原（判据集合回到原状）",
   "revert_literals" not in GENERIC_REPAIRS)

print()
if FAILS:
    print(f"❌ {len(FAILS)} 条失败：{FAILS}")
    sys.exit(1)
print("✅ 全部通过")
