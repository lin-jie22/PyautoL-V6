"""反事实层归因（counterfactual layer attribution）：这个变体的能力来自哪一层。

## 为什么需要它

`runtime.assign_credit` 的 turn-level 归因**曾经恒不可用**（2026-10-13 实测：
生产链路上从来没有 `replay_fn`，端到端 evolve 里被调用 **0 次**，
`CreditReport.mode` 永远是 `trajectory` / `available=False`）。
于是"这个变体变好了"只能归给整条 harness，**无法归因到某一层** ——
而 harness 是 5 层的，不知道哪层有用就等于不知道该往哪层继续进化。

⛔ **2026-10-14 已修（r27），本段以上是历史**：`HarnessAgent.replay_fn()`
   提供真反事实重放 ⇒ turn-level **已可用**。现状取证（别再信旧说法）：

       assign_credit(traj, replay_fn=agent.replay_fn(h, spec))
       ⇒ mode='turn_level' / available=True / per_step=[0,0,0,1.0,0]

   账本里也已有真数据：`attribution.step` 事件（此前恒 0）。

⛔ **两个粒度是两回事，别混**：本模块消融的是**层**（L1–L5，5 个）；
   `assign_credit` 消融的是**步**（链上每一项各一步）。步更细，但只对
   `ABLATABLE_STEPS` 里的步有定义 —— 去掉判据步 reward 就没有定义了，
   它返回的 baseline 是**定义**不是"测出来没用"。

本模块把归因做成**可执行**的：runner 是确定性的（同输入 ⇒ 同判定），
所以可以安全地对每一层做**反事实消融**：

    贡献(L) = reward(h) − reward(h 去掉 L)

这不是启发式打分。每一步都是一次真实的执行判定，reward 只来自 verifier。

## 三条纪律

1. **先验证可重放，再给归因**。同一个 harness 跑两次，reward 不同 ⇒
   环境不确定 ⇒ 消融出来的差值混着噪声 ⇒ **标注不可用，不给数字**
   （V5：不可重放时不给近似归因；编出来的归因比不归因更有害）。
2. **消融函数必须与被测层同源**。`LAYER_ABLATIONS` 里"去掉 L1"就是清
   `routing`、"去掉 L2"就是拿掉 `python_exec` —— 与 `agent.py` 里该层
   被读取的方式一致。⛔ 不在脚本里另抄一份词表（V5 纪律：判据同源）。
3. **归因不参与选择**。它和 held-out 一样，只在提交**之后**算一次，
   绝不进入 `group_relative` 或 best 排序（一参与就变成第二张发展集）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .genome import Harness
from .verifier import Verdict

Runner = Callable[[Harness, Dict[str, Any]], Verdict]

#: 每层的"禁用"变换。⛔ 与 agent.py 里该层**被读取的方式**同源：
#:   L0 —— 元规则清空 ⇒ 指令全部失效（agent.directives 读的就是它）
#:   L1 —— 路由清空 ⇒ 技能存在也不可达
#:   L2 —— 拿掉 python_exec ⇒ 连判据都跑不了（REQUIRED_TOOLS 里就有它）
#:   L3 —— 技能清空 ⇒ 没有修法
#:   L4 —— recall_topk=0 ⇒ 记忆通路关闭
LAYER_ABLATIONS: Dict[str, Callable[[Harness], Harness]] = {
    "L0": lambda h: replace(h, meta_rules=""),
    "L1": lambda h: replace(h, routing={}),
    "L2": lambda h: replace(h, tools=tuple(
        t for t in h.tools if t != "python_exec")),
    "L3": lambda h: replace(h, skills={}),
    "L4": lambda h: replace(h, memory_policy={
        **dict(h.memory_policy or {}), "recall_topk": 0}),
}

#: ⛔⛔ r58 改名：`LAYERS` → `ABLATION_LAYERS`。
#:    原名与 `genome.LAYERS` **同名不同物**：那边是"可编辑层"的名字
#:    （`meta_rules` / `routing` / `skills` …），这边是消融实验用的层**代号**
#:    （`L0`–`L4`）。两个东西共用一个名字 ⇒ 改名时另一处不会跟着改，
#:    而且读代码的人会以为它们是同一份名单（第 65 条同形病）。
#:    ⇒ 这里改名，两边都**不**互相 import（它们是不同领域的概念）。
ABLATION_LAYERS: Tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")

#: ⛔ 互为备份的层组合。**单变量消融必然低估它们**：
#: L3（技能）与 L4（记忆通路）在"技能不可达时走记忆"的设计下天然互为备份
#: —— 去掉 L3，L4 接管 ⇒ Δ=0；去掉 L4，L3 还在 ⇒ Δ=0；
#: 但两个一起去掉 ⇒ reward 掉到底。于是只读 per_layer 会得出
#: "这两层都没贡献"的错误结论（实测 scheduler/stateflow 组正是全 0）。
#: 联合消融把这种**结构性冗余**显式读出来。
REDUNDANT_PAIRS: Tuple[Tuple[str, str], ...] = (("L3", "L4"),)


@dataclass
class LayerCredit:
    """一次层归因的结果。⛔ 不可用时 `per_layer` 为空，不给近似值。"""
    per_layer: Dict[str, float] = field(default_factory=dict)
    baseline_reward: float = 0.0
    available: bool = True
    reason: str = ""
    replay_consistent: bool = True
    top_layers: Tuple[str, ...] = ()
    #: 联合消融（见 `REDUNDANT_PAIRS`）：单变量消融低估的**结构性冗余**
    joint_ablation: Dict[str, float] = field(default_factory=dict)
    #: 联合增益 > 各层单独增益之和 ⇒ 这两层互为备份
    redundant: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"per_layer": {k: round(v, 4) for k, v in self.per_layer.items()},
                "baseline_reward": round(self.baseline_reward, 4),
                "available": self.available, "reason": self.reason,
                "replay_consistent": self.replay_consistent,
                "top_layers": list(self.top_layers),
                "joint_ablation": {k: round(v, 4)
                                   for k, v in self.joint_ablation.items()},
                "redundant": list(self.redundant)}


def mean_reward(runner: Runner, h: Harness,
                specs: Sequence[Dict[str, Any]]) -> float:
    """在一组 spec 上跑一遍，取平均 reward（reward 只来自 verifier）。"""
    if not specs:
        return 0.0
    tot = 0.0
    for s in specs:
        try:
            tot += runner(h, s).reward
        except Exception:  # noqa: BLE001 —— 记账，不吞：这里只影响归因
            tot += 0.0
    return tot / len(specs)


def attribute_layers(runner: Runner, h: Harness,
                     specs: Sequence[Dict[str, Any]],
                     layers: Sequence[str] = ABLATION_LAYERS,
                     ablations: Optional[Dict[str, Callable[[Harness], Harness]]] = None
                     ) -> LayerCredit:
    """逐层消融，给出每层的反事实贡献。

    ⛔ 先做**可重放性**检查：同一 harness 连跑两次，reward 必须完全一致。
    不一致 ⇒ 差值里混着环境噪声 ⇒ 标注 `available=False` 且**不给数字**。
    """
    abl = dict(LAYER_ABLATIONS)
    abl.update(ablations or {})
    if not specs:
        return LayerCredit(available=False, reason="没有可归因的任务（specs 为空）")

    r1 = mean_reward(runner, h, specs)
    r2 = mean_reward(runner, h, specs)
    if abs(r1 - r2) > 1e-9:
        # ⛔ 不可重放 ⇒ 不给近似归因（V5：编出来的归因比不归因更有害）
        return LayerCredit(available=False, replay_consistent=False,
                           baseline_reward=r1,
                           reason=(f"环境不可重放（同一 harness 两次 reward "
                                   f"{r1:.4f} / {r2:.4f}）⇒ 层归因不可用，不给近似值"))

    per: Dict[str, float] = {}
    for lv in layers:
        fn = abl.get(lv)
        if fn is None:
            continue
        try:
            ablated = fn(h)
        except Exception as e:  # noqa: BLE001
            per[lv] = 0.0
            continue
        per[lv] = round(r1 - mean_reward(runner, ablated, specs), 6)
    # ── 联合消融：把"互为备份"读出来 ──────────────────────────────
    joint: Dict[str, float] = {}
    redundant: List[str] = []
    for a, b in REDUNDANT_PAIRS:
        fa, fb = abl.get(a), abl.get(b)
        if fa is None or fb is None:
            continue
        try:
            both = fb(fa(h))
        except Exception:  # noqa: BLE001
            continue
        j = round(r1 - mean_reward(runner, both, specs), 6)
        joint[f"{a}+{b}"] = j
        # ⛔ 判据：联合增益**超过**各自单独增益之和 ⇒ 这两层在互相兜底
        if j > (per.get(a, 0.0) + per.get(b, 0.0)) + 1e-9:
            redundant.append(f"{a}+{b}")

    top = tuple(sorted(per, key=lambda k: per[k], reverse=True)[:3])
    flat = all(abs(v) < 1e-9 for v in per.values())
    reason = ("所有层消融后 reward 均不变 ⇒ 这组任务对这 5 层"
              "没有判别力（不是归因失败，是无从判别）" if flat else
              f"归因完成：主要贡献层 {list(top)}")
    if redundant:
        reason += (f"；⚠️ {', '.join(redundant)} 联合消融才显出贡献 ⇒ "
                   "这两层互为备份，单看 per_layer 会低估它们")
    return LayerCredit(per_layer=per, baseline_reward=r1, available=True,
                       replay_consistent=True, top_layers=top,
                       joint_ablation=joint, redundant=tuple(redundant),
                       reason=reason)
