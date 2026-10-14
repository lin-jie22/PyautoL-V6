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

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import (Any, Callable, Dict, FrozenSet, List, Optional, Tuple)

from .ledger import content_hash
from .verifier import Verdict

__all__ = [
    "MODEL_KINDS", "ENV_KINDS", "ALL_KINDS",
    "Step", "Trajectory", "token_estimate",
    "CreditReport", "assign_credit",
    "group_relative",
]


#: 步骤种类：只有 think / call / answer 是策略产生的，observation 来自环境
MODEL_KINDS: FrozenSet[str] = frozenset({"think", "call", "answer"})
ENV_KINDS: FrozenSet[str] = frozenset({"observation", "tool_result", "env"})
#: 🔴 修复二：`Step.kind` **必须**属于二者之一。原版允许任意字符串 ⇒
#:    拼写错误或未来新增 kind（如 "plan"）会同时让 `is_model` / `is_env`
#:    为 False ⇒ token 归属统计**同时漏计**而又不报错（`n_steps` 照算）
#:    ⇒ masking_ratio 被**系统性低估**，而所有数字看起来都自洽。
ALL_KINDS: FrozenSet[str] = MODEL_KINDS | ENV_KINDS

#: 寻址指纹前缀长度（hex 字符数）。⛔ 与 `genome._ID_PREFIX` /
#:    `nongression._ID_PREFIX` / `environment._ID_PREFIX` 保持同值。
#:    `[:16]`（64 位）在 append-only、永不删的账本上长期会碰撞；
#:    轨迹指纹是**寻址键**（"这条轨迹是不是曾经跑过"），不是人眼比对。
_ID_PREFIX = 32


#: CJK / 东亚字符的范围。
#: 🟡 修复六：原版只覆盖 **CJK 统一汉字主块**（U+4E00–U+9FFF）——
#:    漏了扩展 A 区、平假名、片假名、Hangul、全角符号，而
#:    `providers._estimate_prompt_tokens` 至少覆盖了平假名与韩文。
#:    同一个系统里两套 token 估算 ⇒ `n_tokens` 与 `token_budget` 交叉
#:    对比时漂移。这里补齐范围，并注明"应与 providers 侧统一"。
_CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),   # CJK 统一汉字主块
    (0x3400, 0x4DBF),   # CJK 扩展 A 区（约 6000 汉字）
    (0x3040, 0x309F),   # 日文平假名
    (0x30A0, 0x30FF),   # 日文片假名
    (0xAC00, 0xD7AF),   # 韩文 Hangul 音节
    (0xF900, 0xFAFF),   # CJK 兼容汉字
    (0xFF00, 0xFFEF),   # 全角符号
)


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def token_estimate(text: str) -> int:
    """粗估 token 数（中英混排：CJK 按 1 字 ≈ 1 token，英文按 4 字符 ≈ 1）。

    ⛔ 只是**估算**，用于记账与 masking ratio，不用于计费。
    ⛔ CJK 范围必须与 `providers._estimate_prompt_tokens` 一致 ——
        两套估算会让 `n_tokens` 与 `token_budget` 交叉对比时漂移。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    rest = len(text) - cjk
    return cjk + max(1, rest // 4)


@dataclass(frozen=True)
class Step:
    """轨迹一步。tokens 归属**必须**在构造时就分好，事后猜不准。"""
    kind: str
    content: str = ""
    n_tokens: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        #: 🔴 修复二：kind 必须属于 MODEL_KINDS | ENV_KINDS。
        #:    未知 kind 会让 `is_model` / `is_env` 同时为 False ⇒
        #:    token 归属统计**同时漏计**而又不报错（`n_steps` 照算）
        #:    ⇒ masking_ratio 被系统性低估。
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError(
                f"Step.kind 必须是非空字符串，实得 {self.kind!r}")
        if self.kind not in ALL_KINDS:
            raise ValueError(
                f"未知 Step.kind={self.kind!r}；"
                f"model 类 {sorted(MODEL_KINDS)} / env 类 {sorted(ENV_KINDS)}")
        if not isinstance(self.content, str):
            raise TypeError(
                f"Step.content 必须是 str，实得 {type(self.content).__name__}")
        #: ⛔ 排除 bool —— `True` 是 `int` 的子类，会被静默当成 1。
        if (not isinstance(self.n_tokens, int)
                or isinstance(self.n_tokens, bool)
                or self.n_tokens < 0):
            raise ValueError(
                f"Step.n_tokens 必须是非负整数，实得 {self.n_tokens!r}")
        #: 🟡 `Step` 是 frozen 但 `meta` 是可变 dict —— 浅冻结顶层
        #:    （与 `verifier.Verdict.evidence` / `nongression.Baseline.verdicts`
        #:    同款）。
        if self.meta is None:
            object.__setattr__(self, "meta", MappingProxyType({}))
        elif not isinstance(self.meta, Mapping):
            raise TypeError(
                f"Step.meta 必须是 Mapping，实得 {type(self.meta).__name__}")
        else:
            object.__setattr__(self, "meta",
                               MappingProxyType(dict(self.meta)))

    @property
    def is_model(self) -> bool:
        return self.kind in MODEL_KINDS

    @property
    def is_env(self) -> bool:
        return self.kind in ENV_KINDS


#: ⛔ `Trajectory` 用 `init=False` + 手写 `__init__`：让 `steps` 成为**只读
#:    property**（内部存 `_steps`），唯一修改入口是 `add()`（会算 n_tokens）。
#:    原版 `steps: List[Step]` 是可变 list ⇒ 调用方可以绕过 `add()` 直接
#:    `append(Step(n_tokens=0))` ⇒ token 归属**静默漏计**，而文件开头恰好
#:    承诺"tokens 归属必须在构造时就分好"。
@dataclass(init=False)
class Trajectory:
    """一次任务执行 = 一条轨迹。

    ⛔ `steps` 是**只读视图**（tuple）—— 修改必须走 `add()`，确保
        `n_tokens` 有归属（不会漏计）。直接 `traj.steps.append(...)`
        会 `AttributeError`（tuple 没有 append）。
    """
    task_id: str
    harness_id: str
    verdict: Optional[Verdict]
    meta: Dict[str, Any]
    _steps: List[Step] = field(default_factory=list, repr=False)

    def __init__(self, task_id: str, harness_id: str = "",
                 steps: Optional[List[Step]] = None,
                 verdict: Optional[Verdict] = None,
                 meta: Optional[Dict[str, Any]] = None) -> None:
        #: 🟠 修复五：`steps` 构造参数保留（供反序列化 / 测试用），
        #:    但内部存到 `_steps`，对外只暴露只读 `steps` property。
        #:    ⇒ 直接 `traj.steps.append(...)` 会抛 AttributeError（tuple
        #:    没有 append），**唯一**修改入口是 `add()`（会算 n_tokens）。
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError(
                f"Trajectory.task_id 必须是非空字符串，实得 {task_id!r}")
        self.task_id = task_id
        self.harness_id = str(harness_id or "")
        self.verdict = verdict
        self.meta = dict(meta or {})
        if steps:
            for i, s in enumerate(steps):
                if not isinstance(s, Step):
                    raise TypeError(
                        f"steps[{i}] 必须是 Step，实得 {type(s).__name__}")
            self._steps = list(steps)
        else:
            self._steps = []

    @property
    def steps(self) -> Tuple[Step, ...]:
        """只读视图。⛔ 修改必须走 `add()`，确保 n_tokens 有归属。"""
        return tuple(self._steps)

    def add(self, kind: str, content: str = "",
            n_tokens: Optional[int] = None, **meta: Any) -> "Trajectory":
        if n_tokens is None:
            n_tokens = token_estimate(content)
        self._steps.append(Step(kind=kind, content=content,
                                n_tokens=int(n_tokens), meta=meta))
        return self

    # ── token 归属（masking 的同源记账）──────────────────────────
    @property
    def model_tokens(self) -> int:
        return sum(s.n_tokens for s in self._steps if s.is_model)

    @property
    def env_tokens(self) -> int:
        return sum(s.n_tokens for s in self._steps if s.is_env)

    @property
    def masking_ratio(self) -> Optional[float]:
        """env token 占比。

        🟡 修复十：**无 step 时返回 `None`（未定义）**，不是 0.0 ——
            0.0 会被下游读成"无需 masking"，而"没有可判定的内容"是
            另一件事。空轨迹与"全是 model token"不应共用一个值。
        """
        tot = self.model_tokens + self.env_tokens
        return (self.env_tokens / tot) if tot else None

    @property
    def has_verdict(self) -> bool:
        """🟠 修复三：显式区分"未判定"与"判定为 0"。"""
        return self.verdict is not None

    @property
    def reward(self) -> float:
        """reward 只在**有 verdict** 时才成立。

        ⛔ `verdict=None` 时返回 0.0 是**误导**：调用方无法区分"没判定"
            和"判定为 0"。用 `has_verdict` 做显式判断；想要"必须已判定"
            的语义用 `require_reward()`。
        """
        return self.verdict.reward if self.verdict is not None else 0.0

    def require_reward(self) -> float:
        """同 `reward`，但**没有 verdict 时抛异常**。

        ⛔ 给"轨迹必然已执行完"的调用点用 —— 让"忘了跑 verify"在调用点
            立刻暴露，而不是传一个静默的 0.0 到下游。
        """
        if self.verdict is None:
            raise RuntimeError(
                f"轨迹 {self.task_id!r} 尚无 verdict（未执行 verify）⇒ "
                f"reward 未定义；如需 0.0 语义请用 `.reward`")
        return self.verdict.reward

    @property
    def fingerprint(self) -> str:
        """整条轨迹的内容指纹（**寻址键**）。

        🟠 修复四：原版用 `s.content[:200]` 截断 —— 前 200 字符相同、
            后面不同的两条轨迹会**共享指纹**。改用**完整 content 的哈希**：
            指纹长度不随轨迹膨胀（长轨迹的指纹输入仍小），但内容不同
            ⇒ 哈希不同 ⇒ 指纹不同。
        """
        return content_hash({
            "task_id": self.task_id,
            "harness_id": self.harness_id,
            "steps": [(s.kind, content_hash(s.content), s.n_tokens)
                      for s in self._steps],
        })[:_ID_PREFIX]

    def to_dict(self) -> Dict[str, Any]:
        #: 🟠 修复五（附）：按 kind 汇总 n_tokens —— 让"某个 kind 的
        #:    n_tokens=0"（漏计）**可见**。原版只有 n_steps 与总 token。
        by_kind: Dict[str, Dict[str, int]] = {}
        for s in self._steps:
            slot = by_kind.setdefault(s.kind, {"n": 0, "tokens": 0})
            slot["n"] += 1
            slot["tokens"] += s.n_tokens
        mr = self.masking_ratio
        return {
            "task_id": self.task_id,
            "harness_id": self.harness_id,
            "fingerprint": self.fingerprint,
            "reward": self.reward,
            #: ⛔ 显式暴露 —— 让"reward 是未判定还是判定为 0"可区分
            "has_verdict": self.has_verdict,
            "model_tokens": self.model_tokens,
            "env_tokens": self.env_tokens,
            #: ⛔ 空轨迹是 `None`（未定义），不是 0.0
            "masking_ratio": (round(mr, 4) if mr is not None else None),
            "n_steps": len(self._steps),
            "steps_by_kind": by_kind,
            "verdict": self.verdict.to_dict() if self.verdict else None,
        }


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
    #: 🟡 修复九：`top_steps` 是位置索引 —— 轨迹变了含义就变（追加 step
    #:    后索引指向不同的 step；跨轨迹不可比）。`top_step_desc` 记录**内容
    #:    摘要**（kind + 前 40 字符），让报告在跨运行/跨轨迹比较时仍有意义。
    top_step_desc: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "available": self.available,
                "reason": self.reason, "top_steps": self.top_steps,
                "top_step_desc": self.top_step_desc,
                "per_step": [round(x, 4) for x in self.per_step]}


def assign_credit(
        traj: Trajectory,
        replay_fn: Optional[Callable[[str, str, List[Step]], float]] = None
        ) -> CreditReport:
    """给轨迹的每一步分配信用。

    - 没有 replay_fn ⇒ 只能做 trajectory-level（诚实标注 available=False
      的 turn-level，而不是编一个数字）。
    - 有 replay_fn（可重放环境）⇒ leave-one-out：去掉第 i 步后 reward 掉了多少。

    ⛔ 不能重放时**绝不**用启发式编造 per_step —— V5 的教训：
    "没有证据就别报数字"，编出来的归因比不归因更有害。

    🟡 修复七：`replay_fn` 的签名是 `(task_id, harness_id, steps) -> reward`。
        `harness_id` 必须传入 —— "去掉第 i 步"的语义依赖"用哪个 harness
        重放"，原版不传 harness 让这个契约完全不明。

    🔴 修复一：任何一次 `replay_fn` 异常 ⇒ **整个报告降级为
        `available=False`** 并保留失败原因。原版把异常静默当成"这一步
        贡献为 0"且 `available=True` —— 那是**撒谎**：调用方无法区分
        "归因跑成 0" 和 "归因根本没跑成"。
    """
    if replay_fn is None:
        return CreditReport(
            mode="trajectory", available=False,
            reason="环境不可重放 ⇒ turn-level 归因不可用（不给近似值）",
            per_step=[])

    base = traj.reward
    steps = traj.steps
    per: List[float] = []
    replay_errors: List[str] = []
    for i in range(len(steps)):
        ablated = [s for j, s in enumerate(steps) if j != i]
        try:
            r = replay_fn(traj.task_id, traj.harness_id, ablated)
        except Exception as ex:  # noqa: BLE001
            #: ⛔ 重放失败 ⇒ **不能**静默当成 0。整个报告降级并保留原因。
            replay_errors.append(f"step[{i}]: {type(ex).__name__}: {ex}")
            continue
        per.append(round(base - float(r), 6))

    if replay_errors:
        return CreditReport(
            mode="turn_level", available=False,
            reason=(f"重放失败 {len(replay_errors)}/{len(steps)} 步："
                    f"{'; '.join(replay_errors[:3])}"),
            per_step=[])

    top = sorted(range(len(per)), key=lambda i: per[i], reverse=True)[:3]
    #: 🟡 修复九：记录 top step 的内容摘要（跨运行可比）
    desc = [f"[{i}] {steps[i].kind}: {steps[i].content[:40]}"
            for i in top]
    return CreditReport(mode="turn_level", available=True,
                        per_step=per, top_steps=top, top_step_desc=desc)


def group_relative(scores: List[float]) -> List[float]:
    """GRPO 的组内相对优势：A_i = (r_i - mean) / std。

    2026 的默认选择：一个终端奖励、G 个采样，用组内均值作 baseline，
    **不需要 critic**（rollout 很贵时这是大优势）。
    V6 用它做**变体选择**：同一任务下 G 个 harness 变体谁该被留下。

    ⛔ std=0（全同）⇒ 优势全 0：没有信息量，不该选任何变体
    （返回 0 而不是 NaN，避免"除零后变成很大的数"这种经典 bug）。

    🟡 修复八：显式拒绝 NaN / Inf —— `nan < 1e-12` 是 False ⇒ 优势值
        会全是 NaN ⇒ 下游排序结果不稳定（NaN 与任何数比较都 False），
        整批统计被污染。非有限值直接抛错。
    """
    n = len(scores)
    if n == 0:
        return []
    #: ⛔ 逐个校验（顺序：先类型，再 NaN，再 Inf）
    for i, s in enumerate(scores):
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            raise TypeError(
                f"scores[{i}] 必须是数字，实得 {type(s).__name__}")
        #: NaN 与任何值比较都 False，所以用 `s != s` 判（不能用 `s in (...)`）
        if s != s:
            raise ValueError(
                f"scores[{i}] 是 NaN（会污染整组优势：结果不稳定）")
        if s == float("inf") or s == float("-inf"):
            raise ValueError(
                f"scores[{i}] 是 {s}（无穷会污染整组优势）")
    vals = [float(s) for s in scores]
    mean = sum(vals) / n
    if n == 1:
        return [0.0]
    var = sum((s - mean) ** 2 for s in vals) / n
    std = var ** 0.5
    if std < 1e-12:
        return [0.0] * n
    return [(s - mean) / std for s in vals]
