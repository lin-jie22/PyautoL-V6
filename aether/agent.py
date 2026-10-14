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
from typing import (Any, Callable, Dict, FrozenSet, List, Optional,
                    Sequence, Tuple)

from .environment import Environment, verify_source
from .genome import parse_route          # 路由语义的唯一解释处（不可另抄一份）
from .memory import MemoryOS
#: ⛔ 轨迹记账（masking / 信用分配）的唯一事实源。2026-10-14 兑现审计实测：
#:    本模块此前**从未**被生产链路 import ⇒ `runtime.Trajectory` 只在
#:    demo 脚本里被手工构造过 ⇔ masking_ratio 在生产上是空账。
#:    这里 import 它，是为了让"每跑一步记一步"真正接到主流程上。
from .runtime import Trajectory
#: ⛔ 唯一事实源：`revert_control_flow` 难度分层也用它，改动会同时
#:    改变"什么算 easy"的定义。
from .repair import revert_boolops, revert_control_flow, revert_literals
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
    #: 这一次执行的**轨迹记账**（model/env token 归属 + masking 比）。
    #: ⛔ 它是账，不是装饰：masking_ratio 过高 ⇒ 这条轨迹的"能力"可能
    #:    来自环境而非策略（见 `runtime.Trajectory`）。
    trajectory: Optional[Trajectory] = None

    @property
    def reward(self) -> float:
        return self.verdict.reward

    def to_dict(self) -> Dict[str, Any]:
        d = {"strategy": self.strategy,
             "layers_used": list(self.layers_used),
             "steps": [{"layer": s.layer, "action": s.action,
                        "ok": s.ok, "detail": s.detail} for s in self.steps],
             "reward": self.verdict.reward}
        if self.trajectory is not None:
            d["masking"] = {
                "model_tokens": self.trajectory.model_tokens,
                "env_tokens": self.trajectory.env_tokens,
                "masking_ratio": round(self.trajectory.masking_ratio, 4),
            }
        return d


#: 层动作 → runtime kind 的映射。⛔ **只做映射，不新造 kind**：
#:    `runtime.MODEL_KINDS` / `ENV_KINDS` 才是判据读的集合。
#:    映射写错 ⇒ 该步两边都不认 ⇒ tokens 计 0 ⇒ masking 变空账
#:    （2026-10-14 兑现审计抓到的正是这个形态）。
_STEP_KIND: Dict[Tuple[str, str], str] = {
    ("L0", "directive"): "think",
    ("L1", "route"): "call",
    ("L2", "require_tools"): "env",
    ("L2", "resolve_env"): "env",
    ("L3", "apply"): "call",
    ("L3", "uci"): "observation",
    ("L4", "recall"): "observation",
}
#: 未知层动作 ⇒ 归到 **env 侧**（保守：宁可高估环境占比去触发告警，
#: 也不能把环境观测算成策略产出 —— 后者会让 reward 看起来来自模型）。
_UNKNOWN_STEP_KIND = "observation"

#: ⛔ **可消融**的层动作 = 真正会改变源码、且去掉它 reward 仍有定义的那些。
#:   只有它们能参与 step 级反事实重放（见 `make_replay_fn`）。
#:   - `L3/apply` —— 链上的每一项修法（最有价值的一条：链上谁真的有用）
#:   - `L4/recall`—— 记忆通路
#:   ⛔ 反过来，**不**在里面的是"消融了就没有 reward 定义"的步骤：
#:     `L5` 验证是判据本身（去掉它 reward 无从产生）、`L1/L2/L0` 是结构性
#:     步骤（去掉它们等于换了个 harness，不是"去掉一步"）。
#:     把它们算进 leave-one-out ⇒ 得到的 Δ 是"换了台机器"，不是"这一步的贡献"
#:     ⇒ 编出来的归因比不归因更有害（V5）。
#:   ⛔ 这份集合是 agent 注入点与轨迹记账的**共同唯一来源**：两边各写一份
#:     迟早漂移（一侧说可消融、另一侧根本没实现跳过 ⇒ 消融静默失效）。
ABLATABLE_STEPS: FrozenSet[Tuple[str, str]] = frozenset(
    {("L3", "apply"), ("L4", "recall")})


def trajectory_from_steps(task_id: str, harness_id: str,
                          steps: Sequence[AgentStep],
                          verdict: Optional[Verdict] = None) -> Trajectory:
    """把一次执行的层动作序列折算成**轨迹账**。

    ⛔ 为什么在**返回前**一次性折算，而不是每步各记一次：
       两处记账迟早会漂移（V5：同一份事实被两个地方写 ⇒ 必有一个是假的）。
       这里 steps 是唯一事实源，Trajectory 是它的**投影**。

    每一步都会带上身份 `(layer, action, occ)` 写进 `Step.meta` ——
    这是 step 级反事实重放能定位"去掉了哪一步"的唯一依据。
    """
    t = Trajectory(task_id=task_id, harness_id=harness_id)
    occ: Dict[Tuple[str, str], int] = {}
    for s in steps:
        kind = _STEP_KIND.get((s.layer, s.action), _UNKNOWN_STEP_KIND)
        key = (s.layer, s.action)
        k = occ.get(key, 0)
        occ[key] = k + 1
        t.add(kind, f"{s.layer}/{s.action}: {s.detail}",
              layer=s.layer, action=s.action, occ=k,
              ablatable=(key in ABLATABLE_STEPS))
    if verdict is not None:
        t.verdict = verdict
    return t


def step_identities(steps: Sequence[Any]) -> List[Tuple[str, str, int]]:
    """从轨迹步里取出身份序列（与 `trajectory_from_steps` 写入端同源）。

    ⛔ 判据必须与被测对象同源：这里读的是 `Step.meta` 里**写入端写下的**
       字段，不另抄一份词表。
    """
    out: List[Tuple[str, str, int]] = []
    for s in steps:
        m = getattr(s, "meta", None) or {}
        layer = m.get("layer")
        action = m.get("action")
        if not layer or action is None:
            continue
        out.append((str(layer), str(action), int(m.get("occ", -1))))
    return out


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
    def run(self, h: Any, spec: Dict[str, Any],
            skip: Sequence[Tuple[str, str, int]] = ()) -> AgentResult:
        """跑一次任务。`skip` 是**反事实消融**：跳过指定的层动作。

        ⛔ 为什么消融必须**留下占位步**（记录 ok=False 而不是不记录）：
           leave-one-out 靠 `(layer, action, occ)` 定位"去掉了哪一步"。
           少一步 ⇒ 后续所有 occ 整体前移 ⇒ 第二次消融就对不上号了
           ⇒ 归因静默错位。占位步让 steps 数量恒定，occ 才稳定。

        ⛔ `skip` 只对 `ABLATABLE_STEPS` 生效。试图消融判据步（L5）或
           结构步（L1/L2/L0）**不会**真的跳过任何东西 —— 那不是"这一步
           没贡献"，是"去掉它 reward 就没有定义了"。对它们返回 baseline
           reward（贡献 0）是**定义**，不是近似。
        """
        tid = str(spec.get("task_id") or spec.get("env_id") or "task")
        hid = str(getattr(h, "harness_id", "") or "")
        skip = frozenset(tuple(x) for x in (skip or ()))
        env = self.envs.get(str(spec.get("env_id", "")))
        if env is None:
            _s: Tuple[AgentStep, ...] = (
                AgentStep("L2", "resolve_env", False, "env 不存在"),)
            return AgentResult(
                verdict=error_verdict("agent", ValueError(
                    f"未知 env_id={spec.get('env_id')!r}")),
                steps=_s, strategy="", layers_used=("L2",),
                trajectory=trajectory_from_steps(tid, hid, _s))

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

        # ── L1 路由 → L3 技能链 ────────────────────────────────
        #: ⛔ 路由值是**链**（有序技能序列），不是单值。2026-10-14 实测：
        #:    单值路由下"装一个新技能"= 把旧的顶掉 ⇒ 两个通用能力维度
        #:    （控制流 / 字面量）互斥 ⇒ 闭环第一轮之后必然停滞。
        #:    链让能力**可叠加**：新技能追加到链尾，旧的仍在链上。
        used.append("L1")
        routed = h.routing.get(task_class) or h.routing.get(topic) or ""
        chain = parse_route(routed)
        if chain:
            missing = [s for s in chain if s not in h.skills]
            steps.append(AgentStep(
                "L1", "route", any(s in h.skills for s in chain),
                f"{task_class} → 链 {list(chain)}"
                + (f"（未装载的 {missing} 已跳过）" if missing else "")))
        else:
            steps.append(AgentStep("L1", "route", False,
                                   f"没有可达路由（task_class={task_class}）"))

        # ── L0 元规则闸门（会**禁用**链上具体策略）──────────────
        ds = directives(h.meta_rules)
        banned: List[str] = []
        if ds:
            used.append("L0")
            banned = [s for s in chain if banned_by_L0(s, ds)]
            if banned:
                steps.append(AgentStep(
                    "L0", "directive", False,
                    f"元规则【禁止整文件替换】⇒ {banned} 被禁，从链上摘除"
                    f"（L0 真的有后果）"))
            else:
                steps.append(AgentStep("L0", "directive", True,
                                       f"元规则生效 {sorted(ds)}，未禁用链上任何修法"))
        #: 真正会被应用的：装载了 且 未被 L0 禁用
        active = tuple(s for s in chain if s in h.skills and s not in banned)

        # ── L4 记忆通路（技能不可达时的第二条路）────────────────
        #: ⛔ 消融记账器：每 append 一个**可消融**步就要消耗一次 occ，
        #:    这样 run 里的序号与 `trajectory_from_steps` 写下的 meta 完全
        #:    一致（两者是同一份事实的两个视角，不能各数各的）。
        occ: Dict[Tuple[str, str], int] = {}

        def _consume(layer: str, action: str) -> bool:
            """消耗一个 (layer, action) 的序号，返回这一步是否**被消融**。"""
            k = occ.get((layer, action), 0)
            occ[(layer, action)] = k + 1
            return (layer, action, k) in skip

        strategy = "|".join(active)
        if not active and self.memory is not None:
            used.append("L4")
            topk = int(h.memory_policy.get("recall_topk", 0) or 0)
            if topk <= 0:
                _consume("L4", "recall")     # 无行为可跳，但要占住序号
                steps.append(AgentStep("L4", "recall", False,
                                       "recall_topk<=0 ⇒ 记忆通路关闭"))
            elif _consume("L4", "recall"):
                steps.append(AgentStep(
                    "L4", "recall", False,
                    "消融：反事实重放跳过召回 ⇒ 不走记忆通路（占位步）"))
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

        # ── 应用策略链（能力**叠加**）──────────────────────────
        #: ⛔ 语义是"叠加"不是"依次尝试到通过"：后者要为链上每项各跑一次
        #:    执行判据 ⇒ reward 变成"试出来的"，违反"reward 只来自一次执行"
        #:    这条唯一来源纪律，且成本随链长线性增长（观测改变了被测成本）。
        #:    叠加只跑**一次**判据，成不成立由它一次说了算。
        used.append("L3")
        plan = ("memory_hint",) if strategy == "memory_hint" else active
        fixed = src
        applied: List[str] = []
        for s in plan:
            prev = fixed
            if _consume("L3", "apply"):
                #: ⛔ 占位步：**必须** append，否则后续 occ 整体前移，
                #:    leave-one-out 第二次消融就对不上号（归因静默错位）。
                steps.append(AgentStep(
                    "L3", "apply", False,
                    f"链项 {s}：消融（反事实重放跳过，不应用）"))
                continue
            new_fixed, applicable = self._apply_one(s, prev, env, spec, steps)
            if applicable:
                fixed = new_fixed
                applied.append(s)
            steps.append(AgentStep(
                "L3", "apply", applicable,
                f"链项 {s}：changed={applicable and new_fixed != prev}"
                + ("" if applicable
                   else f"（对 {env.env_id} 不适用 ⇒ 该项跳过，链继续）")))
        _consume("L3", "apply")            # 汇总步也占一个序号
        steps.append(AgentStep(
            "L3", "apply", bool(applied),
            f"链={list(plan) or ['none']} 生效={applied} 总 changed={fixed != src}"))

        v = verify_source(env, source=fixed, timeout=self.timeout)
        vd = Verdict(
            passed=v.passed, checks=v.checks,
            evidence={**(v.evidence or {}), "strategy": strategy,
                      #: ⛔ `chain` 是**归因**字段：链上哪一项真的改了源码。
                      #: 没有它，"链修好了这道题"就说不清是谁修的。
                      "chain": list(plan), "applied": list(applied),
                      "layers_used": sorted(set(used)),
                      "n_steps": len(steps)},
            error=v.error, env_fingerprint=env.fingerprint, cost_s=v.cost_s)
        return AgentResult(
            verdict=vd, steps=tuple(steps), strategy=strategy,
            layers_used=tuple(sorted(set(used))),
            trajectory=trajectory_from_steps(tid, hid, steps, vd))

    def _apply_one(self, strategy: str, src: str, env: Any,
                   spec: Dict[str, Any], steps: List[AgentStep]
                   ) -> Tuple[str, bool]:
        """应用链上的**一项**策略，返回 (修后的源码, 是否适用)。

        ⛔ 与旧的单值分支一一对应，语义一处都没改 —— 变的只是"被调用几次、
        结果怎么叠加"。这样"链"不会顺手改变任何单项修法的行为，
        新旧对照才是同口径的。
        """
        if strategy.startswith("uci:"):
            #: 外部接入的技能（见 interop 模块）。⛔ 技能给的源码**照样**要过
            #: 后面的 `verify_source` —— 判据只来自执行，技能自称修好了不算。
            return self._uci_apply(
                strategy[len("uci:"):], src, env, spec, steps)
        if strategy == "fix_control_flow":
            return revert_control_flow(src, env.source), True   # 通用但保守
        if strategy == "fix_literals":
            #: 通用字面量修法：**另一个能力维度**（只对齐数值常量）。
            #: ⛔ 它跟 `fix_control_flow` 一样是通用的（不点名环境），
            #:    换环境照样生效 ⇒ 能不能过 held-out 由闸判，不在这里预判。
            return revert_literals(src, env.source), True
        if strategy == "fix_boolops":
            #: 第三维：**布尔运算**（and/or）。与前两维修的不是同一类节点，
            #:    所以既能被单独提名进链，也不该顺手把别的层的题修好。
            return revert_boolops(src, env.source), True
        if strategy.startswith("patch:"):
            # ⛔ 定点修法必须**校验环境是否匹配**。不校验的话它会在任何环境
            #    都"生效" ⇒ 定点退化成通用 ⇒ 泛化闸再也发现不了它。
            if strategy[len("patch:"):] == env.env_id:
                return env.source, True
            return src, False
        if strategy == "memory_hint":
            # ⛔ 记忆通路走的是**保守通用修法**，不是"把整份基线还给你"。
            #    后者会让基线直接满分 ⇒ 弱点被掩盖 ⇒ 进化永久空转（已实测）。
            #    前者只是让"技能不可达"时仍有路可走，且**修不全** ⇒
            #    天花板仍在，进化的动力还在。
            return revert_control_flow(src, env.source), True
        return src, True                                       # 未知 ⇒ 原样

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
        #: ⛔ 挂上 agent 引用：**反事实重放的唯一入口**。
        #:    Runner 协议只有 `(harness, spec) -> Verdict`，光有它无法
        #:    "去掉某一步再跑一次" ⇒ step 级信用分配拿不到 replay_fn
        #:    ⇒ `assign_credit` 恒为 `available=False`（2026-10-13 实测 0 次）。
        #:    ⛔ 该缺口已于 2026-10-14（r27）由下面 `replay_fn()` 修掉；
        #:       挂这个引用**正是**修复的一部分 —— 别当遗留注释删掉。
        #:    这里显式带上宿主对象，让消费方能 `getattr(runner, "agent")`。
        runner.agent = self            # type: ignore[attr-defined]
        return runner

    # ── 反事实重放：让 step 级信用分配**真的可用** ────────────────
    def replay_fn(self, h: Any, spec: Dict[str, Any]
                  ) -> Optional[Callable[[str, List[Any]], float]]:
        """为 `runtime.assign_credit` 造一个**真** replay_fn。

        ⛔ 先做**可重放性自检**：同一个 harness + 同一个 spec 连跑两次，
           reward 必须完全一致。不一致 ⇒ 差值里混着环境噪声 ⇒ 返回 `None`
           ⇒ `assign_credit` 保持 `available=False`。
           （不可重放时给数字 = 编造归因，比不归因更有害 —— V5。）

        ⛔ 为什么这不是"层归因的近似"：`attribution.attribute_layers` 消融的
           是**层**（5 个），这里消融的是**步**（链上每一项各自一步），
           而且每一步都是一次完整重跑，reward 只来自 verifier。
        """
        try:
            r1 = self.run(h, spec)
            r2 = self.run(h, spec)
        except Exception:  # noqa: BLE001
            return None
        if abs(float(r1.reward) - float(r2.reward)) > 1e-9:
            return None                      # 不可重放 ⇒ 不给数字
        traj = r1.trajectory
        if traj is None or not getattr(traj, "steps", None):
            return None
        base_ids = step_identities(traj.steps)
        base_reward = float(r1.reward)
        stats: Dict[str, int] = {"calls": 0, "non_ablatable": 0,
                                 "ablatable": 0}

        def replay(task_id: str, ablated: List[Any]) -> float:
            stats["calls"] += 1
            present = set(step_identities(ablated))
            missing = [i for i in base_ids if i not in present]
            if not missing:
                return base_reward
            real = [i for i in missing if (i[0], i[1]) in ABLATABLE_STEPS]
            if not real:
                #: 消融的是判据步/结构步 ⇒ 去掉它 reward 就没有定义。
                #: 贡献记 0 是**定义**，不是"测出来它没用"。
                stats["non_ablatable"] += 1
                return base_reward
            stats["ablatable"] += 1
            try:
                return float(self.run(h, spec, skip=tuple(real)).reward)
            except Exception:  # noqa: BLE001
                return base_reward

        replay.stats = stats            # type: ignore[attr-id]
        replay.base_reward = base_reward  # type: ignore[attr-id]
        return replay
