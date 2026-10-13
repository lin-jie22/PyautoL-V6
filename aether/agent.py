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
from .repair import revert_control_flow, revert_literals   # 唯一事实源（难度分层也用它）
from .verifier import Verdict, error_verdict

#: L0 元规则指令表。**指令是有后果的**，不是注释。
#: 每条指令给出若干识别词（中英皆可），命中即生效。
DIRECTIVES: Dict[str, Tuple[str, ...]] = {
    "no_full_replace": ("禁止整文件替换", "no_full_replace", "no full replace"),
    "require_verification": ("必须执行验证", "require_verification"),
}

#: 完成任务**至少**需要的工具。缺一个 ⇒ 连源码都读不到 / 判据都跑不了。
REQUIRED_TOOLS: Tuple[str, ...] = ("read_file", "python_exec")


def banned_by_L0(strategy: str, ds: FrozenSet[str]) -> bool:
    """L0 元规则是否**禁用**这个策略。

    ⛔ 必须覆盖**所有**"整文件换回基线"的形态，而不只是 `patch:*` 技能。
    2026-10-13 实测：L4 记忆通路恢复出的 `memory_restore` 同样是整文件替换，
    但旧代码只检查 `strategy.startswith("patch:")` ⇒ **L0 禁令有后门**：
    元规则写着【禁止整文件替换】，记忆通路照样能整文件替换。
    一个禁令只要有一条绕过路径，它就不再是禁令。
    """
    if not strategy:
        return False
    if "no_full_replace" in ds:
        return strategy.startswith("patch:") or strategy == "memory_restore"
    return False


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
                 memory: Optional[MemoryOS] = None, timeout: float = 10.0,
                 gateway: Optional[Any] = None):
        #: ⛔ 明确拒绝非 dict：多环境化之后 env_id 是任务的寻址键，传单个
        #:    Environment 会在 run() 里炸成 `'Environment' object has no
        #:    attribute 'get'` —— 一个看不出根因的 AttributeError。
        if not isinstance(envs, dict):
            raise TypeError(
                f"envs 必须是 {{env_id: Environment}} 字典，收到 "
                f"{type(envs).__name__}（单环境时代的老调用方式）")
        self.envs = envs
        self.memory = memory
        self.timeout = timeout
        #: 记忆里"这个环境该怎么修"的标记。召回命中 ⇒ 走记忆通路。
        #: ⛔ 记忆里存的是**修法知识**，不是**答案**。2026-10-13 实测：
        #: 存"整份基线源码"时，L4 通路一召回就把 reward 从 0.0 顶到 1.0
        #: ⇒ 基线已在天花板 ⇒ 再也挖不到弱点 ⇒ 进化循环永久失去动力。
        #: 看起来 reward 满分，实际是真实弱点（没有技能）被记忆掩盖了。
        self.memory_marker = "repair_hint:"
        #: 接入层网关（`interop.Gateway`）。None ⇒ 走不到 UCI 分支，
        #: ⛔ 但**必须显式记账**（step ok=False），不能悄悄退化成"无策略"。
        self.gateway = gateway

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
            if banned_by_L0(skill, ds):
                steps.append(AgentStep(
                    "L0", "directive", False,
                    f"元规则【禁止整文件替换】⇒ {skill} 被禁用（L0 真的有后果）"))
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
                    f"{env.env_id} repair_hint 修法经验", top_k=topk)
                # ⛔⛔ 跨运行**继承**的记忆不得进 L4 通路：它会让同一个种子
                #    harness 的基线随历史漂移（实测空库 base=0.0、装载后 0.6）
                #    ⇒ 结果不可复现，且记忆冒充了能力、掩盖真实弱点。
                #    继承的记忆只供提议器参考，reward 判据必须只依赖本轮执行。
                hit = [n for n in notes
                       if f"{self.memory_marker}{env.env_id}" in (n.content or "")
                       and "inherited" not in (n.tags or ())]
                if hit:
                    strategy = "memory_hint"
                    steps.append(AgentStep(
                        "L4", "recall", True,
                        f"召回 {len(hit)} 条修法记忆 ⇒ 走记忆通路"
                        f"（保守修法，不是整文件替换）"))
                else:
                    steps.append(AgentStep("L4", "recall", False,
                                           "召回到了但没有任何基线指纹"))

        # ── 应用策略 ───────────────────────────────────────────
        used.append("L3")
        applicable = True
        if strategy.startswith("uci:"):
            #: 外部接入的技能（见 interop 模块）。⛔ 技能给的源码**照样**要过
            #: 下面的 `verify_source` —— 判据只来自执行，技能自称修好了不算。
            fixed, applicable = self._uci_apply(
                strategy[len("uci:"):], src, env, spec, steps)
        elif strategy == "fix_control_flow":
            fixed = revert_control_flow(src, env.source)   # 通用但保守
        elif strategy == "fix_literals":
            #: 通用字面量修法：**另一个能力维度**（只对齐数值常量）。
            #: ⛔ 它跟 `fix_control_flow` 一样是通用的（不点名环境），
            #:    换环境照样生效 ⇒ 能不能过 held-out 由闸判，不在这里预判。
            fixed = revert_literals(src, env.source)
        elif strategy.startswith("patch:"):
            # ⛔ 定点修法必须**校验环境是否匹配**。不校验的话它会在任何环境
            #    都"生效" ⇒ 定点退化成通用 ⇒ 泛化闸再也发现不了它。
            target = strategy[len("patch:"):]
            if target == env.env_id:
                fixed = env.source
            else:
                fixed = src
                applicable = False
        elif strategy == "memory_hint":
            # ⛔ 记忆通路走的是**保守通用修法**，不是"把整份基线还给你"。
            #    后者会让基线直接满分 ⇒ 弱点被掩盖 ⇒ 进化永久空转（已实测）。
            #    前者只是让"技能不可达"时仍有路可走，且**修不全** ⇒
            #    天花板仍在，进化的动力还在。
            fixed = revert_control_flow(src, env.source)
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

    # ── 接入层：外部技能（UCI）────────────────────────────────
    @staticmethod
    def uci_skill_id(cid: str) -> str:
        """把一个 capability id 变成 harness 里的技能名。

        ⛔ 前缀 `uci:` 是**契约**：routing 里写 `uci:ext.fixer` 就表示该技能
        由接入层提供。没有这个前缀，L3 只有写死在源码里的那几个分支可用。
        """
        return f"uci:{cid}"

    def _uci_apply(self, cid: str, src: str, env: Any,
                   spec: Dict[str, Any], steps: List[AgentStep]
                   ) -> Tuple[str, bool]:
        """调用一个已注册的外部技能，返回 (修后的源码, 是否适用)。

        UCI 技能契约（**唯一**约定，任何形态的实现照此即可接入）：
            输入  {"source": str, "baseline": str, "spec": {...}}
            输出  {"source": str, 可带任意附加字段}

        ⛔ 调用不成功 ⇒ 返回**原样源码**并记 step=False，绝不返回"部分结果"。
        ⛔ 技能自称成功不算成功：这里**不**看它的自述，只看 status 与
        （若有）checks；最终判定仍然由后面的 `verify_source` 执行给出。
        """
        if self.gateway is None:
            steps.append(AgentStep("L3", "uci", False,
                                   f"未挂载接入层网关 ⇒ 技能 {cid} 不可达"))
            return src, False
        res = self.gateway.invoke(
            cid, {"source": src, "baseline": env.source,
                  "spec": {k: v for k, v in spec.items() if k != "broken_source"}},
            actor=f"agent/{env.env_id}")
        if res.status != "ok":
            steps.append(AgentStep("L3", "uci", False,
                                   f"{cid} 调用 {res.status}：{res.error[:120]}"))
            return src, False
        out = res.output if isinstance(res.output, dict) else None
        new_src = (out or {}).get("source")
        if not isinstance(new_src, str) or not new_src:
            steps.append(AgentStep(
                "L3", "uci", False,
                f"{cid} 输出缺 source 字段（实得 {type(new_src).__name__}）"))
            return src, False
        detail = f"{cid} → {len(new_src)}B（changed={new_src != src}）"
        if res.verified:
            detail += f"，判据 {sum(c.passed for c in res.checks)}/{len(res.checks)}"
        else:
            #: ⛔ 没验证过就**明说**，不写成"已验证通过"
            detail += f"，未验证（{res.unverified_reason}）"
        steps.append(AgentStep("L3", "uci", True, detail))
        return new_src, True

    # ── 适配 Runner 协议 ───────────────────────────────────────
    def as_runner(self) -> Callable[[Any, Dict[str, Any]], Verdict]:
        def runner(h: Any, spec: Dict[str, Any]) -> Verdict:
            return self.run(h, spec).verdict
        return runner
