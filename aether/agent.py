"""Harness 驱动的确定性执行体（agent）。

## 为什么需要它（这一轮的动机）

上一版的 runner 只读 `h.skills` —— 于是 harness 的 5 层里**只有 L3 会影响
reward**。改 L0/L1/L2/L4 对结果毫无影响，进化循环永远不可能发现"路由没接通"
"工具没开放""记忆召回不够"这类真实弱点。5 层基因组因此是装饰性的。

这个 agent 让每一层都**真的**有后果：

    L0 meta_rules    —— 元规则出现 `禁止整文件替换` ⇒ 整文件换回基线的修法
                        被禁用。于是"改 L0 需人工审批"不再是口号：
                        它真的会改变系统的能力边界。
    L1 routing       —— 技能必须**被路由到**才可达。路由没接通 ⇒ 技能存在
                        也用不上（这正是 Capsule 必须存在的原因：
                        单加技能是一个基因，但过不了闸）。
    L2 tools         —— 没有 `read_file` / `python_exec` ⇒ 连读源码和执行
                        验证都做不到 ⇒ 直接失败。
    L3 skills        —— 具体修法策略。
    L4 memory_policy —— 技能不可达时的**第二条通路**：从记忆里召回这个环境
                        的基线指纹。`recall_topk<=0` ⇒ 这条路断掉。

⛔ 它仍然**不调模型**（确定性）。这不是偷懒：机制的因果链必须能被单变量
对照验证，模型只能被评测。真 agent 走同一个 `Runner` 协议注入，接口不变。

⛔ 另一个刻意的设计：**路由键是"任务类别"而不是"环境名"**。
   按环境名路由（`textscore → 修法`）本身就是一种定点化，换环境就失效；
   按任务类别路由（`repair → 修法`）才是通用的。泛化与否在这里第一次
   变成**路由层的结构选择**，而不只是策略层的行为差异。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from .environment import Environment, verify_source
from .memory import MemoryOS
from .repair import revert_control_flow          # 唯一事实源（难度分层也用它）
from .verifier import Verdict, error_verdict

#: L0 元规则指令表。**指令是有后果的**，不是注释。
#: 每条指令给出若干识别词（中英皆可），命中即生效。
DIRECTIVES: Dict[str, Tuple[str, ...]] = {
    "no_full_replace": ("禁止整文件替换", "no_full_replace", "no full replace"),
    "require_verification": ("必须执行验证", "require_verification"),
}

#: 完成任务**至少**需要的工具。缺一个 ⇒ 连源码都读不到 / 判据都跑不了。
REQUIRED_TOOLS: Tuple[str, ...] = ("read_file", "python_exec")


def directives(meta_rules: str) -> FrozenSet[str]:
    """从 L0 元规则里解析出生效的指令集。

    ⛔ 未识别的元规则**不产生**任何指令（fail-open 到"无约束"）：
    元规则是给人看的自由文本，不能因为写了一句没登记的话就让整个系统瘫掉。
    但**已识别**的指令必须严格执行 —— 那才是元规则的意义。
    """
    t = (meta_rules or "").lower()
    return frozenset(name for name, keys in DIRECTIVES.items()
                     if any(k.lower() in t for k in keys))


@dataclass(frozen=True)
class AgentStep:
    """一次层内动作的记录。**每一层都留痕**，否则无法归因。"""
    layer: str
    action: str
    ok: bool
    detail: str = ""


@dataclass
class AgentResult:
    verdict: Verdict
    steps: Tuple[AgentStep, ...] = ()
    strategy: str = ""
    layers_used: Tuple[str, ...] = ()

    @property
    def reward(self) -> float:
        return self.verdict.reward

    def to_dict(self) -> Dict[str, Any]:
        return {"strategy": self.strategy,
                "layers_used": list(self.layers_used),
                "steps": [{"layer": s.layer, "action": s.action,
                           "ok": s.ok, "detail": s.detail} for s in self.steps],
                "reward": self.verdict.reward}


class HarnessAgent:
    """按 harness 的 5 层去完成一个任务，并产出**可执行判据**。"""

    def __init__(self, envs: Dict[str, Environment],
                 memory: Optional[MemoryOS] = None, timeout: float = 10.0):
        self.envs = envs
        self.memory = memory
        self.timeout = timeout
        #: 记忆里"这个环境的基线"的标记。召回命中 ⇒ 可以用记忆修。
        self.memory_marker = "baseline:"

    # ── 主流程 ──────────────────────────────────────────────────
    def run(self, h: Any, spec: Dict[str, Any]) -> AgentResult:
        env = self.envs.get(str(spec.get("env_id", "")))
        if env is None:
            return AgentResult(
                verdict=error_verdict("agent", ValueError(
                    f"未知 env_id={spec.get('env_id')!r}")),
                steps=(AgentStep("L2", "resolve_env", False, "env 不存在"),),
                strategy="", layers_used=("L2",))

        src = str(spec.get("broken_source") or env.source)
        task_class = str(spec.get("task_class") or "repair")
        topic = str(spec.get("topic") or "")
        steps: List[AgentStep] = []
        used: List[str] = []

        # ── L2 工具闸门 ────────────────────────────────────────
        tools = set(h.tools)
        missing = [t for t in REQUIRED_TOOLS if t not in tools]
        used.append("L2")
        if missing:
            steps.append(AgentStep("L2", "require_tools", False,
                                   f"缺工具 {missing} ⇒ 读不到源码/跑不了判据"))
            return AgentResult(
                verdict=error_verdict(f"agent/{env.env_id}",
                                      PermissionError(f"缺工具 {missing}")),
                steps=tuple(steps), strategy="", layers_used=tuple(used))
        steps.append(AgentStep("L2", "require_tools", True,
                               "+".join(REQUIRED_TOOLS) + " 可用"))

        # ── L1 路由 → L3 技能 ──────────────────────────────────
        used.append("L1")
        routed = h.routing.get(task_class) or h.routing.get(topic) or ""
        skill = ""
        if routed and routed in h.skills:
            skill = routed
            steps.append(AgentStep("L1", "route", True,
                                   f"{task_class} → {skill}"))
        elif routed:
            steps.append(AgentStep("L1", "route", False,
                                   f"路由指向不存在的技能 {routed!r}"))
        else:
            steps.append(AgentStep("L1", "route", False,
                                   f"没有可达路由（task_class={task_class}）"))

        # ── L0 元规则闸门（会**禁用**具体策略）──────────────────
        ds = directives(h.meta_rules)
        if ds:
            used.append("L0")
            if "no_full_replace" in ds and skill.startswith("patch:"):
                steps.append(AgentStep(
                    "L0", "directive", False,
                    "元规则【禁止整文件替换】⇒ 该修法被禁用（L0 真的有后果）"))
                skill = ""
            else:
                steps.append(AgentStep("L0", "directive", True,
                                       f"元规则生效 {sorted(ds)}，未禁用当前修法"))

        # ── L4 记忆通路（技能不可达时的第二条路）────────────────
        strategy = skill
        if not strategy and self.memory is not None:
            used.append("L4")
            topk = int(h.memory_policy.get("recall_topk", 0) or 0)
            if topk <= 0:
                steps.append(AgentStep("L4", "recall", False,
                                       "recall_topk<=0 ⇒ 记忆通路关闭"))
            else:
                notes = self.memory.search(
                    f"{env.env_id} 基线 baseline", top_k=topk)
                hit = [n for n in notes
                       if f"{self.memory_marker}{env.env_id}" in (n.content or "")]
                if hit:
                    strategy = "memory_restore"
                    steps.append(AgentStep(
                        "L4", "recall", True,
                        f"召回 {len(hit)} 条带基线指纹的记忆 ⇒ 走记忆通路"))
                else:
                    steps.append(AgentStep("L4", "recall", False,
                                           "召回到了但没有任何基线指纹"))

        # ── 应用策略 ───────────────────────────────────────────
        used.append("L3")
        applicable = True
        if strategy == "fix_control_flow":
            fixed = revert_control_flow(src, env.source)   # 通用但保守
        elif strategy.startswith("patch:"):
            # ⛔ 定点修法必须**校验环境是否匹配**。不校验的话它会在任何环境
            #    都"生效" ⇒ 定点退化成通用 ⇒ 泛化闸再也发现不了它。
            target = strategy[len("patch:"):]
            if target == env.env_id:
                fixed = env.source
            else:
                fixed = src
                applicable = False
        elif strategy == "memory_restore":
            fixed = env.source                             # 记忆里的基线
        else:
            fixed = src                                    # 无策略 ⇒ 原样
        steps.append(AgentStep(
            "L3", "apply", bool(strategy) and applicable,
            f"strategy={strategy or 'none'}，changed={fixed != src}"
            + ("" if applicable else f"（对 {env.env_id} 不适用，定点修法失配）")))

        v = verify_source(env, source=fixed, timeout=self.timeout)
        return AgentResult(
            verdict=Verdict(
                passed=v.passed, checks=v.checks,
                evidence={**(v.evidence or {}), "strategy": strategy,
                          "layers_used": sorted(set(used)),
                          "n_steps": len(steps)},
                error=v.error, env_fingerprint=env.fingerprint, cost_s=v.cost_s),
            steps=tuple(steps), strategy=strategy,
            layers_used=tuple(sorted(set(used))))

    # ── 适配 Runner 协议 ───────────────────────────────────────
    def as_runner(self) -> Callable[[Any, Dict[str, Any]], Verdict]:
        def runner(h: Any, spec: Dict[str, Any]) -> Verdict:
            return self.run(h, spec).verdict
        return runner
