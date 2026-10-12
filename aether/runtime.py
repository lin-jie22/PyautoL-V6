"""运行期：轨迹、token 归属记账、信用分配。

2026 agentic RL 最关键的实现细节是 **tool-observation masking**：
    工具返回的 token 不是策略生成的，算 loss 时必须 mask 掉；
    否则模型在学"预测它控制不了的外部文本"，训练不稳且只会复述检索。

V6 不做梯度更新（那是训练侧的事），但把**同样的记账**搬到进化侧：
    · `model_tokens` —— harness 自己产生的（决策、生成）
    · `env_tokens`   —— 环境返回的（观察、工具输出）

为什么进化侧也要记：信用分配（credit assignment）在进化里同样成立 ——
一个变体"看起来变好了"，得知道**是它自己做的**还是**环境喂给它的**
（例如工具返回里恰好带了答案）。不记这个账，"能力提升"里会混进环境噪声。

信用分配：reward 只在轨迹末尾（r(τ) ∈ {0,1}），但需要知道该归给谁。
V6 提供两种：
    · trajectory-level（默认，诚实但稀疏）
    · turn-level 近似：leave-one-out —— 去掉某一步后 reward 是否变化
      （对可重放环境成立；重放不成立时**必须显式标注为不可用**，
       不能拿近似值冒充归因）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .ledger import content_hash
from .verifier import Verdict

# 步骤种类：只有 think / call 是策略产生的，observation 来自环境
MODEL_KINDS = frozenset({"think", "call", "answer"})
ENV_KINDS = frozenset({"observation", "tool_result", "env"})


@dataclass(frozen=True)
class Step:
    """轨迹一步。tokens 归属**必须**在构造时就分好，事后猜不准。"""
    kind: str
    content: str = ""
    n_tokens: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_model(self) -> bool:
        return self.kind in MODEL_KINDS

    @property
    def is_env(self) -> bool:
        return self.kind in ENV_KINDS


@dataclass
class Trajectory:
    """一次任务执行 = 一条轨迹。"""
    task_id: str
    harness_id: str = ""
    steps: List[Step] = field(default_factory=list)
    verdict: Optional[Verdict] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def add(self, kind: str, content: str = "", n_tokens: Optional[int] = None,
            **meta: Any) -> "Trajectory":
        if n_tokens is None:
            n_tokens = token_estimate(content)
        self.steps.append(Step(kind=kind, content=content,
                               n_tokens=int(n_tokens), meta=meta))
        return self

    # ── token 归属（masking 的同源记账）──────────────────────────
    @property
    def model_tokens(self) -> int:
        return sum(s.n_tokens for s in self.steps if s.is_model)

    @property
    def env_tokens(self) -> int:
        return sum(s.n_tokens for s in self.steps if s.is_env)

    @property
    def masking_ratio(self) -> float:
        """env token 占比。过高 ⇒ 这条轨迹的"能力"可能来自环境而非策略。"""
        tot = self.model_tokens + self.env_tokens
        return (self.env_tokens / tot) if tot else 0.0

    @property
    def reward(self) -> float:
        return self.verdict.reward if self.verdict else 0.0

    @property
    def fingerprint(self) -> str:
        return content_hash({
            "task_id": self.task_id,
            "harness_id": self.harness_id,
            "steps": [(s.kind, s.content[:200], s.n_tokens) for s in self.steps],
        })[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "harness_id": self.harness_id,
            "fingerprint": self.fingerprint,
            "reward": self.reward,
            "model_tokens": self.model_tokens,
            "env_tokens": self.env_tokens,
            "masking_ratio": round(self.masking_ratio, 4),
            "n_steps": len(self.steps),
            "verdict": self.verdict.to_dict() if self.verdict else None,
        }


def token_estimate(text: str) -> int:
    """粗估 token 数（中英混排：中文按 1 字 ≈ 1 token，英文按 4 字符 ≈ 1）。

    ⛔ 只是**估算**，用于记账与 masking ratio，不用于计费。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    rest = len(text) - cjk
    return cjk + max(1, rest // 4)


# ──────────────────────────────────────────────────────────────────
# 信用分配
# ──────────────────────────────────────────────────────────────────
@dataclass
class CreditReport:
    """信用分配结果。"""
    mode: str                                  # trajectory | turn_level
    available: bool = True
    reason: str = ""
    per_step: List[float] = field(default_factory=list)
    top_steps: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "available": self.available,
                "reason": self.reason, "top_steps": self.top_steps,
                "per_step": [round(x, 4) for x in self.per_step]}


def assign_credit(traj: Trajectory,
                  replay_fn: Optional[Callable[[str, List[Step]], float]] = None
                  ) -> CreditReport:
    """给轨迹的每一步分配信用。

    - 没有 replay_fn ⇒ 只能做 trajectory-level（诚实标注 available=False
      的 turn-level，而不是编一个数字）。
    - 有 replay_fn（可重放环境）⇒ leave-one-out：去掉第 i 步后 reward 掉了多少。

    ⛔ 不能重放时**绝不**用启发式编造 per_step —— V5 的教训：
    "没有证据就别报数字"，编出来的归因比不归因更有害。
    """
    if replay_fn is None:
        return CreditReport(mode="trajectory", available=False,
                            reason="环境不可重放 ⇒ turn-level 归因不可用（不给近似值）",
                            per_step=[])

    base = traj.reward
    per: List[float] = []
    for i in range(len(traj.steps)):
        ablated = [s for j, s in enumerate(traj.steps) if j != i]
        try:
            r = replay_fn(traj.task_id, ablated)
        except Exception:  # noqa: BLE001
            r = base
        per.append(round(base - r, 6))     # 去掉它 reward 掉了多少 ⇒ 它的贡献

    top = sorted(range(len(per)), key=lambda i: per[i], reverse=True)[:3]
    return CreditReport(mode="turn_level", available=True, per_step=per, top_steps=top)


def group_relative(scores: List[float]) -> List[float]:
    """GRPO 的组内相对优势：A_i = (r_i - mean) / std。

    2026 的默认选择：一个终端奖励、G 个采样，用组内均值作 baseline，
    **不需要 critic**（rollout 很贵时这是大优势）。
    V6 用它做**变体选择**：同一任务下 G 个 harness 变体谁该被留下。

    ⛔ std=0（全同）⇒ 优势全 0：没有信息量，不该选任何变体
    （返回 0 而不是 NaN，避免"除零后变成很大的数"这种经典 bug）。
    """
    n = len(scores)
    if n == 0:
        return []
    mean = sum(scores) / n
    if n == 1:
        return [0.0]
    var = sum((s - mean) ** 2 for s in scores) / n
    std = var ** 0.5
    if std < 1e-12:
        return [0.0] * n
    return [(s - mean) / std for s in scores]
