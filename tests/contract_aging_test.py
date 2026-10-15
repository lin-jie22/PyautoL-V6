#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：跨轮退化检测（aging） + 轨迹记账（masking）真的接进了生产链路。

## 为什么有这个测试

2026-10-14 对 `docs/FRONTIER_2026.md` 做了一次**兑现审计**（不是 grep
符号，是运行时取证 + 可达性计数）。结论有两半：

  · 旧清单 §1–§7 的落点 **56/56 全部兑现**；
  · 但 §2 里 `tool-observation masking` / `信用分配` 这一支是**空账** ——
    `aether/runtime.py` 从未被生产链路 import，`Trajectory` 只在 demo
    脚本里被手工构造过。文档自称有落点，实际是**平行于主流程的死代码**。

同时审计暴露了第二件事：已有的 `NonRegressionGate` 是**横截面**的
（这一轮相对冻结基线），它看不到"进来之后还在不在自己变弱"。而 ALE
（Agent Lifespan Engineering, 2026）研究的正是后者 —— 长期退化会让
行为测试看起来正常、准确率却已衰减。

所以本契约盯两件事：
  1. 老化判据要有**判别力**（能分出健康 / 衰减 / 无从判别）；
  2. 轨迹记账要真的由 `HarnessAgent.run()` 产出（不是 demo 里的手工品）。

## 分组

    A 四类退化信号各自可判别
    B 纪律：只看下降 / 无事件标不可判别 / 探针集不可比 ⇒ 不判健康
    C 轨迹记账真的接在生产链路上
    E 反证：注入事故版，断言原判据会翻转

跑法（离线，秒级）：
    python tests/contract_aging_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.aging import (AGING_KINDS, ProbeResult, RoundTrace,      # noqa: E402
                          detect_aging)
from aether.agent import HarnessAgent, trajectory_from_steps          # noqa: E402
from aether.environment import Environment, mutate_source             # noqa: E402
from aether.genome import Harness                                     # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'OK  ' if cond else 'FAIL'}] {name}"
          f"{('  | ' + detail) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def mk(rno, rewards, *, sc=0, con=0, pr=0, cal=None):
    """造一轮纵向读数。"""
    return RoundTrace(
        round_no=rno, harness_id=f"h{rno}",
        probes=tuple(ProbeResult(task_id=f"t{i}", reward=v)
                     for i, v in enumerate(rewards)),
        skill_count=sc, consolidated=con, pruned=pr,
        caliber=dict(cal or {}))


def sig(rep, kind):
    return next(s for s in rep.signals if s.kind == kind)


# ══════════════════════════════════════════════════════════════════
# A 组：四类退化信号各自可判别
# ══════════════════════════════════════════════════════════════════
print("=== A 四类退化信号各自可判别 ===")
ck("A0 恰好四类（不多加 ⇒ 不多一个『没发生即通过』的出口）",
   len(AGING_KINDS) == 4, str(AGING_KINDS))

# compression：巩固之后掉了
rep_c = detect_aging([mk(1, [1.0, 1.0]), mk(2, [0.5, 0.5], con=4)])
sc_ = sig(rep_c, "compression")
ck("A1 compression：巩固后探针下降 ⇒ detected",
   sc_.available and sc_.detected, str(sc_.to_dict()))
ck("A1b 证据是**逐条** Δ（不是只给聚合分）",
   len(sc_.evidence) == 2 and "Δ-0.5000" in sc_.evidence[0],
   str(sc_.evidence))

# interference：装新技能之后此消彼长（均值抹平成 0）
rep_i = detect_aging([mk(1, [1.0, 0.0], sc=1), mk(2, [0.6, 0.4], sc=3)])
si = sig(rep_i, "interference")
ck("A2 interference：一升一降 ⇒ detected（均值抹平也抓得住）",
   si.available and si.detected, str(si.to_dict()))
ck("A2b pattern == interference（此消彼长指纹）",
   si.pattern == "interference" and si.degraded == ("t0",)
   and si.improved == ("t1",), str(si.to_dict()))

# maintenance：剪枝之后掉了
rep_m = detect_aging([mk(1, [1.0, 1.0]), mk(2, [0.7, 0.7], pr=2)])
sm = sig(rep_m, "maintenance")
ck("A3 maintenance：剪枝后探针下降 ⇒ detected",
   sm.available and sm.detected, str(sm.to_dict()))

# revision：口径变更 ⇒ 纵向 Δ 不可比
rep_r = detect_aging([mk(1, [1.0, 1.0], cal={"seed": 1}),
                      mk(2, [1.0, 1.0], cal={"seed": 2})])
sr = sig(rep_r, "revision")
ck("A4 revision：口径变更 ⇒ detected（不是比分数，是可比性失效）",
   sr.available and sr.detected, str(sr.to_dict()))
ck("A4b 分数没变也照样报（可比性 ≠ 分数）",
   abs(rep_r.overall_delta) < 1e-9 and sr.detected,
   f"overall={rep_r.overall_delta} detected={sr.detected}")

# ══════════════════════════════════════════════════════════════════
# B 组：纪律
# ══════════════════════════════════════════════════════════════════
print("\n=== B 纪律 ===")

# B1 上升不算退化
rise = detect_aging([mk(1, [0.0, 0.0], sc=1), mk(2, [1.0, 1.0], sc=3)])
ck("B1 上升 ⇒ 不报警（退化只看下降）",
   not sig(rise, "interference").detected and rise.healthy,
   str(sig(rise, "interference").to_dict()))

# B2 无事件 ⇒ available=False ⇒ 进 undetermined（**不是**通过）
noev = detect_aging([mk(1, [1.0, 1.0]), mk(2, [0.1, 0.1])])
ck("B2 无事件 ⇒ 四类全部 available=False",
   all(not s.available for s in noev.signals),
   str([s.kind for s in noev.signals if s.available]))
ck("B2b 无事件 ⇒ 全部列入 undetermined（可判别数 0/4）",
   len(noev.undetermined) == 4, str(noev.undetermined))
ck("B2c undetermined 与 healthy 是两件事（不因『没看到』就算过）",
   "未判别" in noev.reason, noev.reason)

# B3 探针集变化 ⇒ 不可比 ⇒ 不判健康
inc = detect_aging([mk(1, [1.0, 1.0]), mk(2, [1.0])])
ck("B3 探针集跨轮变化 ⇒ comparable=False",
   inc.comparable is False, str(inc.to_dict())[:160])
ck("B3b 不可比 ⇒ healthy=False（fail-closed）",
   inc.healthy is False, str(inc.reason))

# B4 空探针集 ⇒ 无从判别
empty = detect_aging([mk(1, []), mk(2, [])])
ck("B4 空探针集 ⇒ comparable=False 且 healthy=False",
   empty.comparable is False and empty.healthy is False, str(empty.reason))

# B5 只有一轮 ⇒ 无从判别（没有"跨轮"）
one = detect_aging([mk(1, [1.0])])
ck("B5 只有一轮 ⇒ 四类均无事件 ⇒ 记 undetermined",
   len(one.undetermined) == 4, str(one.undetermined))


# ══════════════════════════════════════════════════════════════════
# C 组：轨迹记账真的接在生产链路上
# ══════════════════════════════════════════════════════════════════
print("\n=== C 轨迹记账接进生产链路 ===")

_env = Environment.from_dir(ROOT / "tasks" / "scheduler")
_tree = __import__("ast").parse(_env.source)
_broken, _ = mutate_source(_tree, which=0)

_h = Harness(meta_rules="", routing={"repair": "fix_literals"},
             tools=("read_file", "python_exec"),
             skills={"fix_literals": "x"},
             memory_policy={"recall_topk": 0})
_spec = {"env_id": "scheduler", "broken_source": _broken,
         "task_class": "repair", "topic": "scheduler"}
_res = HarnessAgent(envs={"scheduler": _env}).run(_h, _spec)

ck("C1 run() 产出 trajectory（不再是 demo 手工品）",
   _res.trajectory is not None, "trajectory is None")
_t = _res.trajectory
ck("C2 trajectory 有步骤（不是空账）", len(_t.steps) > 0,
   f"steps={len(_t.steps)}")
ck("C3 model_tokens 与 env_tokens 都 > 0（两侧都在记）",
   _t.model_tokens > 0 and _t.env_tokens > 0,
   f"model={_t.model_tokens} env={_t.env_tokens}")
ck("C4 masking_ratio ∈ (0,1)（不是恒 0 / 恒 1 的空账）",
   0.0 < _t.masking_ratio < 1.0, str(_t.masking_ratio))
ck("C5 kind 全部落在 runtime 的集合里（同源，没另造）",
   all(s.is_model or s.is_env for s in _t.steps),
   str([s.kind for s in _t.steps]))
ck("C6 to_dict 暴露 masking 读数（外部可见）",
   "masking" in _res.to_dict()
   and "masking_ratio" in _res.to_dict()["masking"],
   str(_res.to_dict().get("masking")))

# C7 masking 随轨迹内容变化 ⇒ 是真统计不是常量
_a = trajectory_from_steps("t", "h", _res.steps)
_long = Harness(routing={"repair": "fix_literals"},
              tools=("read_file", "python_exec"),
              skills={"fix_literals": "x"})
_b = trajectory_from_steps("t", "h", _res.steps + _res.steps)
ck("C7 步骤翻倍 ⇒ token 账跟着变（真统计）",
   _b.model_tokens != _a.model_tokens,
   f"{_a.model_tokens} vs {_b.model_tokens}")

# ══════════════════════════════════════════════════════════════════
# E 组：反证 —— 注入事故版，断言原判据会翻转
# ══════════════════════════════════════════════════════════════════
print("\n=== E 反证组（判据必须有判别力）===")

import aether.aging as AG

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

# E1 事故版：判据退化为"永远健康" ⇒ 衰减轨迹必须被误判为健康
_orig = AG.detect_aging


def always_healthy(traces, eps=AG.DEFAULT_EPS):
    return AG.AgingReport(rounds=len(traces), comparable=True,
                          signals=(), reason="事故版：永远健康")


AG.detect_aging = always_healthy
try:
    sick = AG.detect_aging([mk(1, [1.0, 1.0]), mk(2, [0.0, 0.0], con=3)])
finally:
    AG.detect_aging = _orig
ck("E1 事故版（永远健康）⇒ 衰减轨迹被误判为健康",
   sick.healthy is True, str(sick.reason))
ck("E1b 原版对同一条轨迹判不健康（⇒ 断言不是恒真的）",
   detect_aging([mk(1, [1.0, 1.0]), mk(2, [0.0, 0.0], con=3)]).healthy
   is False)

# E2 事故版：只看**均值**不看逐条 ⇒ 干扰指纹（一升一降）会漏
def mean_only(traces, eps=AG.DEFAULT_EPS):
    """事故版：只比较均值。一升一降 ⇒ 均值不变 ⇒ 判健康。"""
    if len(traces) < 2:
        return AG.AgingReport(rounds=len(traces), comparable=False,
                              reason="轮次不足")
    out = []
    for kind in AGING_KINDS:
        ev_rounds = [i for i in range(1, len(traces))
                     if AG._events(kind, traces[i - 1], traces[i])]
        if not ev_rounds:
            out.append(AG.AgingSignal(kind=kind, available=False,
                                      detected=False, delta=0.0))
            continue
        d = min(traces[i].mean_reward - traces[i - 1].mean_reward
                for i in ev_rounds)
        out.append(AG.AgingSignal(kind=kind, available=True,
                                  detected=d < -eps, delta=d))
    return AG.AgingReport(rounds=len(traces), comparable=True,
                          signals=tuple(out), reason="事故版：只看均值")


AG.detect_aging = mean_only
try:
    blind = AG.detect_aging([mk(1, [1.0, 0.0], sc=1),
                             mk(2, [0.6, 0.4], sc=3)])
finally:
    AG.detect_aging = _orig
ck("E2 事故版（只看均值）⇒ 干扰指纹被漏掉",
   not sig(blind, "interference").detected, str(sig(blind, "interference")))
ck("E2b 原版对同一条轨迹抓得住（⇒ 逐条判据真的在守东西）",
   sig(detect_aging([mk(1, [1.0, 0.0], sc=1), mk(2, [0.6, 0.4], sc=3)]),
       "interference").detected)

# E3 事故版：不可比也放行 ⇒ 必须被抓
def comparable_always(traces, eps=AG.DEFAULT_EPS):
    return AG.AgingReport(rounds=len(traces), comparable=True, signals=(),
                          reason="事故版：不检查可比性")


AG.detect_aging = comparable_always
try:
    lax = AG.detect_aging([mk(1, [1.0, 1.0]), mk(2, [1.0])])
finally:
    AG.detect_aging = _orig
ck("E3 事故版（不检查可比性）⇒ 换尺子也判健康",
   lax.healthy is True and lax.comparable is True)
ck("E3b 原版判不可比且不健康",
   detect_aging([mk(1, [1.0, 1.0]), mk(2, [1.0])]).healthy is False)


# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
if FAILS:
    print(f"契约失败 {len(FAILS)} 项：")
    for f in FAILS:
        print(f"  · {f}")
    print("=" * 62)
    sys.exit(1)
print("契约全部通过")
print("=" * 62)
