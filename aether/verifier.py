"""RLVR 内核：奖励只来自**可执行判据**，绝不来自模型自述或打分。

2026 的核心共识（Agentic RL 综述 / Meta ARE / SWE-smith 一致）：
    "if you can execute a check, you have a reward function."
    —— 只要能执行一个检查，你就有奖励函数。

V6 的三条硬约束（本模块是它们的执行点）：

1. **奖励 = 外部可观察状态的判定**，不是文本相似度、不是 LLM 打分。
   LLM 可以给 shaping/提示，但**不能进 reward**（reward hacking 的入口）。

2. **Verdict 必须带 evidence**：没有证据的判定不算判定。
   V5 的教训：`.txt 文件被错判为成功`、`write_file 越界却报成功`、
   `写入端降级成功但 embedding=NULL` —— 全是"判定成功但没有证据"。

3. **判据同源**：复用被测对象自己的函数，不在验证器里重写一套
   （V5 实测：对比脚本里重写判据 ⇒ 差 3.8pt，与效应同量级）。

反作弊（reward hacking 防护）—— 2026 文献点名的头号失败模式：
    · `Verdict` 不允许携带 agent 自述字段（结构上去掉这条通路）；
    · `assert_no_self_report()` 静态闸：payload 里出现 self_reported /
      claimed_success 之类键 ⇒ 判定作废；
    · verifier 的输入是 **env_state**（外部状态快照）而非 agent 输出。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import (Any, Callable, Dict, FrozenSet, List, Optional, Protocol,
                    Tuple)

#: ⛔ 复用账本的规范化序列化：NaN/Infinity / 超范围 int 会在这里**抛错**
#:    而不是静默产出垃圾哈希（与账本用同一套跨语言纪律）。
from .ledger import canonical_json

__all__ = [
    "SELF_REPORT_KEYS",
    "SelfReportDetected",
    "Check", "Verdict", "Verifier",
    "assert_no_self_report", "assert_verdict_sane", "check_verdict",
    "env_fingerprint", "error_verdict", "from_exit_code",
    "all_of", "any_of",
]

# ⛔ 这些键一旦出现在 payload 里（**含嵌套**），判定作废 ——
#    挡住"自述即奖励"这条捷径。
SELF_REPORT_KEYS: FrozenSet[str] = frozenset({
    "self_reported", "claimed_success", "agent_says", "self_score",
    "llm_score", "model_judged", "claimed_passed", "self_eval",
})


class SelfReportDetected(Exception):
    """判定里混进了模型自述/打分 ⇒ 奖励不可信。"""


# ──────────────────────────────────────────────────────────────────
# 递归键扫描：SELF_REPORT_KEYS 必须穿透嵌套
# ──────────────────────────────────────────────────────────────────
def _iter_keys(obj: Any):
    """递归产出 obj 里**所有** Mapping 的键（含嵌套）。

    ⛔ 原版只查顶层 ⇒ `{"metrics": {"llm_score": 0.9}}` 一字不差穿过
    两道闸 —— "结构上去掉自述通路"的承诺被绕过。任何包装层都能躲开
    顶层匹配，所以匹配必须递归。
    """
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield k
            yield from _iter_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_keys(item)


def _self_report_hits(payload: Any) -> set:
    """payload（含嵌套）里命中的自述键集合。"""
    return SELF_REPORT_KEYS & set(_iter_keys(payload))


def assert_no_self_report(payload: Dict[str, Any]) -> None:
    """静态闸：payload 里出现自述/打分键 ⇒ 抛异常。

    放在**写入侧**（构造 Verdict 之前），而不是读取侧 —— 事后过滤等于
    允许这条通路存在，等于没挡。

    ⛔⛔ 递归检查：原版只看顶层键 ⇒ 一个 `{"metrics": {"llm_score": ...}}`
        / `{"agent": {"self_reported": True}}` 就能把自述偷渡进 evidence。
        "结构上去掉自述通路"必须是对**整棵 evidence 树**成立，不是只对顶层。
    """
    if not isinstance(payload, Mapping):
        raise SelfReportDetected(
            f"payload 必须是 Mapping，实得 {type(payload).__name__}")
    hit = _self_report_hits(payload)
    if hit:
        raise SelfReportDetected(
            f"payload 含自述/打分字段 {sorted(hit)}（含嵌套）：奖励必须来自执行判据")


@dataclass(frozen=True)
class Check:
    """单条检查（可执行判据的最小单元）。"""
    name: str
    passed: bool
    evidence: str = ""      # ⛔ 必填性由 Verdict.validate() 强制

    def __post_init__(self) -> None:
        #: 🟡 修复六：非 bool 的 `passed` 会静默翻车（`bool("false") == True`）。
        #:    在**构造时**拦住，不让它进入 verdict。同时把 evidence=None
        #:    规范化成 ""，避免 validate() 里 `None.strip()` 崩。
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(
                f"Check.name 必须是非空字符串，实得 {self.name!r}")
        if not isinstance(self.passed, bool):
            raise ValueError(
                f"Check.passed 必须是 bool，实得 {type(self.passed).__name__}"
                f"（字符串 'false' 会被 bool() 判为 True，静默翻成通过）")
        if self.evidence is None:
            object.__setattr__(self, "evidence", "")
        if not isinstance(self.evidence, str):
            raise ValueError(
                f"Check.evidence 必须是 str，实得 {type(self.evidence).__name__}")

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "evidence": self.evidence}


@dataclass(frozen=True)
class Verdict:
    """一次验证的裁决。**冻结** —— 入了账本就不能改。

    - passed: 是否通过（reward 的来源，只有 0/1）
    - checks: 逐条检查（可审计）
    - evidence: 外部可观察证据（文件 sha256 / 测试输出 / DB 行 / 退出码）
    - env_fingerprint: 环境快照指纹（同一判据在不同环境下不可比）

    ⛔ `frozen=True` 只冻结**字段赋值**；`evidence` 是 dict ⇒
        `v.evidence["x"] = 1` 之前是合法的。`__post_init__` 把它包成
        `MappingProxyType`（浅不可变），把"冻结"从**口头承诺**变成
        **语言保证**：任何试图修改 `v.evidence[...]` 的代码会当场抛
        `TypeError`，而不是静默改掉一个已裁决的判定。
    """
    passed: bool
    checks: Tuple[Check, ...] = ()
    evidence: Dict[str, Any] = field(default_factory=dict)
    env_fingerprint: str = ""
    cost_s: float = 0.0
    error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError(
                f"Verdict.passed 必须是 bool，实得 {type(self.passed).__name__}")
        if not isinstance(self.checks, tuple):
            #: ⛔ 允许 list 传入但**转为 tuple**（frozen dataclass 的字段应
            #:    该是不可变类型）；不让它静默留下一个可变的 list。
            object.__setattr__(self, "checks", tuple(self.checks))
        if self.evidence is None:
            #: 🔴 修复六（补齐）：`Verdict` 是 `frozen=True` ⇒ 普通赋值会抛
            #:    `FrozenInstanceError`（实测 `Verdict(passed=True,
            #:    evidence=None)` 直接崩）。同一个 `__post_init__` 里其他
            #:    字段都走了 `object.__setattr__`，只有这一行漏了
            #:    ⇒ "evidence=None 归一化"这条分支是**死的且会炸**。
            object.__setattr__(self, "evidence", {})
        if not isinstance(self.evidence, Mapping):
            raise TypeError(
                f"Verdict.evidence 必须是 Mapping，实得 {type(self.evidence).__name__}")
        #: 🟡 修复十：浅冻结 —— 顶层键不可增删改；嵌套 dict 仍可变（这是
        #:    已知边界：完全深冻结需要递归替换 + 反序列化时还原，代价大）。
        #:    `to_dict()` 会转回普通 dict，账本序列化不受影响。
        object.__setattr__(self, "evidence",
                           MappingProxyType(dict(self.evidence)))

    # ── 判据同源：分数只有这一个定义，别处不许重写 ──────────────
    @property
    def reward(self) -> float:
        """RLVR 的 r(τ) ∈ {0,1}。**唯一**奖励定义。"""
        if self.error:
            return 0.0
        return 1.0 if self.passed else 0.0

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    def validate(self) -> Tuple[bool, str]:
        """Verdict 自检：没有 evidence 的判定不算判定。

        ⛔ 这是 V5 血泪的制度化：`artifact_is_hollow` 那条纪律在这里变成
        Verdict 的构造约束 —— "判定成功但拿不出证据"直接判无效。

        ⛔ 自述检测**必须递归**（见 `assert_no_self_report`）：原版只查
        顶层 evidence 键 ⇒ `{"a": {"self_score": ...}}` 能穿过。
        """
        if not self.checks:
            return False, "没有任何 check ⇒ 这不是判定，是宣称"
        #: 🟡 新增：检查名唯一性 —— 两条同名 check 会静默共存，统计与
        #:    审计都会出歧义（"check_x 过了吗"得到两个答案）。
        seen: set = set()
        for c in self.checks:
            if c.name in seen:
                return False, f"check 名重复：{c.name!r}"
            seen.add(c.name)
            if not c.evidence.strip():
                return False, f"check '{c.name}' 缺 evidence"
        #: ⛔ 递归扫描含嵌套的 evidence
        hit = _self_report_hits(self.evidence)
        if hit:
            return False, f"evidence 含自述/打分字段 {sorted(hit)}（含嵌套）⇒ 奖励不可信"
        return True, ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            #: ⛔ 派生字段：由 passed/error 决定，**读取时忽略**。放在这里
            #:    只是为了人眼方便 —— 反序列化（`check_verdict`）不读它。
            "reward": self.reward,
            "checks": [c.to_dict() for c in self.checks],
            #: MappingProxyType → dict，账本 JSON 序列化需要纯 dict
            "evidence": dict(self.evidence),
            "env_fingerprint": self.env_fingerprint,
            "cost_s": round(self.cost_s, 4),
            "error": self.error,
        }


class Verifier(Protocol):
    """验证器协议。**输入是环境状态，不是 agent 输出**。

    ⛔ 契约（弱，由调用方保证）：`env_state` 至少包含
        · `env_id`: str          —— 环境标识
        · `source`:  str          —— 被测源码/产物内容
        · 环境特有字段（任务 id、随机种子等）
    不同环境用不同结构 ⇒ 同一份 verifier 不能跨环境通用；`env_fingerprint`
    依赖 `env_state` 是**完整快照**而不是"环境名哈希"，见其 docstring。
    """

    def __call__(self, env_state: Dict[str, Any]) -> Verdict: ...


def env_fingerprint(state: Dict[str, Any]) -> str:
    """环境指纹：同一判据在不同环境/不同任务下不可比 ⇒ 必须显式带上。

    V5 的教训：两轮评估口径不同却拿分数对比（wait_terminal ON/OFF），
    ⇒ 分数差 0.07 全是口径差。指纹让"口径变了"变成可检测事件。

    ⛔⛔ `state` 必须是**完整环境快照**，不是"环境名"。指纹的用途是
        "两轮是否同口径"，如果只喂 `{"env_id": "x"}`，那它只是"环境名
        哈希"，检测不出"任务变了 / 种子变了 / 参数变了"。

    ⛔⛔ 用 `ledger.canonical_json` 而不是裸 `json.dumps`：
        · 数字规范化（`1.0 → 1`）两端一致 ⇒ TS 监督器可复算；
        · NaN/Infinity / 超范围 int **抛错**（不再静默产出垃圾指纹）；
        · 与账本**同一套**序列化 ⇒ 跨语言确定性得到保证。
        原版 `default=str` 会让 `Path`/`set`/`datetime` 静默变字符串，
        `set` 的 `str` 还依赖 `PYTHONHASHSEED`（跨进程不确定）。
    """
    if not isinstance(state, Mapping):
        raise TypeError(
            f"env_fingerprint 需要 Mapping，实得 {type(state).__name__}")
    return hashlib.sha256(
        canonical_json(dict(state)).encode("utf-8")).hexdigest()[:32]


# ──────────────────────────────────────────────────────────────────
# 组合子：把小判据拼成大判据（保持 reward 只来自执行）
# ──────────────────────────────────────────────────────────────────
def _coerce_pass(name: str, p: Any) -> bool:
    """组合子入参校验：`p` 必须是 bool。

    ⛔ `bool("false") == True`、`bool([]) == False` —— 静默归一非 bool
        会把外部输入里的字符串/容器变成"通过/不通过"，与"绝不静默降级"
        同族的病。非 bool 直接抛错。
    """
    if not isinstance(p, bool):
        raise ValueError(
            f"组合子 {name!r} 的结果必须是 bool，实得 {type(p).__name__}"
            f"（bool('false') 会得到 True）")
    return p


def _coerce_caliber(caliber: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """组合子的口径声明：必须是 Mapping 或 None。"""
    if caliber is None:
        return None
    if not isinstance(caliber, Mapping):
        raise TypeError(
            f"caliber 必须是 Mapping 或 None，实得 {type(caliber).__name__}")
    return dict(caliber)


def all_of(name: str, results: List[Tuple[str, bool, str]], *,
           caliber: Optional[Dict[str, Any]] = None) -> Verdict:
    """全部通过才通过。每项 (检查名, 是否通过, 证据)。

    ⛔ `results[i][1]` **必须是真 bool** —— 传 `"false"` / `1` / `None`
    会被 `_coerce_pass` 当场拒绝（`bool("false") == True` 是静默翻转）。

    ⛔ 组合子的指纹**不能**为空：同名 check 在不同 `expect` / 不同参数下
    产出**不同判据**，指纹相同则"口径变了"检测不到（V5 那条"分数不可跨
    口径比较"的教训原样复活）。所以指纹 = check 名集合 ⊕ `caliber`
    （`caliber` 可省，省略时不进指纹 —— 但**绝不退化成空串**）。

    🔴 修复六（补齐）：原实现把 `bool(p)` 写在 `_coerce_pass` 调用**之前**
    ⇒ `isinstance(p, bool)` 恒真 ⇒ 上面那条校验是**死的**（实测
    `all_of("n", [("c","false","ev")])` 不抛错且 `passed=True`）。
    同一改动还把指纹从 `env_fingerprint([names])` 改成 `cl is not None`
    才算 ⇒ 不传 `caliber`（唯一生产调用点 `environment.evaluate` 就不传）
    时指纹退化成 `""` ⇒ 口径闸静默失效。两处都补齐。
    """
    cl = _coerce_caliber(caliber)
    checks = tuple(
        Check(n, _coerce_pass(name, p), str(e)) for n, p, e in results)
    passed = all(c.passed for c in checks) and bool(checks)
    fp = env_fingerprint({"checks": [n for n, _, _ in results],
                          "caliber": cl})
    return Verdict(passed=passed, checks=checks,
                   evidence={"summary": f"{sum(c.passed for c in checks)}/{len(checks)}"},
                   env_fingerprint=fp)


def any_of(name: str, results: List[Tuple[str, bool, str]], *,
           caliber: Optional[Dict[str, Any]] = None) -> Verdict:
    """任一项通过即通过。`caliber` 语义同 `all_of`。"""
    cl = _coerce_caliber(caliber)
    checks = tuple(
        Check(n, _coerce_pass(name, p), str(e)) for n, p, e in results)
    fp = env_fingerprint({"checks": [n for n, _, _ in results],
                          "caliber": cl})
    return Verdict(passed=any(c.passed for c in checks), checks=checks,
                   evidence={"summary": f"{sum(c.passed for c in checks)}/{len(checks)}"},
                   env_fingerprint=fp)


#: `from_exit_code` 里 stdout / stderr 头尾各保留多少字符。
_EXIT_HEAD_CHARS = 400
_EXIT_TAIL_CHARS = 200


def from_exit_code(name: str, code: int, stdout: str, stderr: str = "",
                   expect: int = 0) -> Verdict:
    """最常见的可执行判据：跑一个命令，看退出码。

    evidence 必须是**真实输出**（截断但保留原文片段），不能是"跑过了"。

    ⛔ 只保留 head 会**丢掉关键错误** —— Python traceback 的末尾是错误
        本身，pytest 的 FAILED 摘要在末尾。加 tail + 长度，让审计者
        至少知道"输出总长"和"末尾发生了什么"。
    """
    ok = (code == expect)
    return Verdict(
        passed=ok,
        checks=(Check(f"{name}.exit_code", ok,
                      f"exit={code} expect={expect}"),),
        evidence={
            "exit_code": code,
            "stdout_len": len(stdout),
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest()[:16],
            "stdout_head": stdout[:_EXIT_HEAD_CHARS],
            "stdout_tail": (stdout[-_EXIT_TAIL_CHARS:]
                            if len(stdout) > _EXIT_HEAD_CHARS else ""),
            "stderr_head": stderr[:_EXIT_HEAD_CHARS],
            "stderr_tail": (stderr[-_EXIT_TAIL_CHARS:]
                            if len(stderr) > _EXIT_HEAD_CHARS else ""),
        },
        env_fingerprint=env_fingerprint({"name": name, "expect": expect}),
    )


def error_verdict(name: str, exc: BaseException, *,
                  env_fingerprint_str: str = "") -> Verdict:
    """验证器自己崩了 ⇒ reward 0，且**必须显式留痕**（不是静默通过）。

    V5 教训：异常被吞掉 ⇒ 假成功。这里的 error 字段会进账本。

    ⛔ `env_fingerprint_str` 可传环境指纹 —— 崩溃的 verifier 也要能标明
        "崩在什么环境"，否则"所有崩溃"在账本里长得一模一样，无法按
        环境归因。
    """
    return Verdict(passed=False, checks=(),
                   error=f"{type(exc).__name__}: {exc}",
                   evidence={"failed_verifier": name},
                   env_fingerprint=env_fingerprint_str)


def assert_verdict_sane(v: Verdict) -> Verdict:
    """判据闸：不 sane 的 Verdict 直接作废（reward 归 0）。

    ⛔⛔ **保留原始 error**：原版把 `v.error` **覆盖**成 `VERDICT_INVALID`
        ⇒ 一个"连接超时的验证器"和一个"返回空 checks 的验证器"在账本里
        长得**一模一样** —— 而前者要修代码、后者要修判据，处置完全不同。
        把 invalid 原因**附加**在原始 error 之后。
    """
    ok, why = v.validate()
    if not ok:
        combined = (f"{v.error}｜INVALID: {why}" if v.error
                    else f"VERDICT_INVALID: {why}")
        return Verdict(passed=False, checks=v.checks,
                       evidence=dict(v.evidence),
                       env_fingerprint=v.env_fingerprint,
                       error=combined)
    assert_no_self_report(v.evidence)
    return v


def check_verdict(payload: Dict[str, Any]) -> Verdict:
    """从 dict（账本/JSON 里读回来的）构造 Verdict 并做 sanity 检查。

    ⛔ 输入是**不可信**的（账本 append-only 但能被编辑；TS 侧结果要
        走这条路）。类型不对时**抛错**，不静默修补 —— 修补会把"账本里
        一条畸形记录"伪装成"一条合法记录"。

    ⛔ 忽略 `payload["reward"]`：reward 只从 `passed` / `error` 派生。
        账本里那个字段是**给人看的冗余**，读到它**不作数**（否则"改一行
        账本 reward"就成了一条后门）。
    """
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"check_verdict 需要 Mapping，实得 {type(payload).__name__}")
    raw_checks = payload.get("checks")
    if raw_checks is None:
        raw_checks = []
    if not isinstance(raw_checks, (list, tuple)):
        raise ValueError(
            f"checks 必须是 list/tuple，实得 {type(raw_checks).__name__}")
    checks: List[Check] = []
    for i, c in enumerate(raw_checks):
        if not isinstance(c, Mapping):
            raise ValueError(
                f"checks[{i}] 必须是 Mapping，实得 {type(c).__name__}")
        name = c.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"checks[{i}] 缺 name 或 name 非字符串")
        p = c.get("passed")
        if not isinstance(p, bool):
            raise ValueError(
                f"checks[{i}].passed 必须是 bool，实得 {type(p).__name__}"
                f"（字符串 'false' 会被 bool() 判为 True）")
        ev = c.get("evidence", "")
        if not isinstance(ev, str):
            raise ValueError(
                f"checks[{i}].evidence 必须是 str，实得 {type(ev).__name__}")
        checks.append(Check(name, p, ev))
    raw_ev = payload.get("evidence")
    if raw_ev is None:
        raw_ev = {}
    if not isinstance(raw_ev, Mapping):
        raise ValueError(
            f"evidence 必须是 Mapping，实得 {type(raw_ev).__name__}")
    #: 🔴 修复六（补齐）：顶层 `passed` 与 `checks[*].passed` 必须**同一套
    #:    严格策略**。原实现这里仍是 `bool(payload.get("passed", False))`
    #:    ⇒ 账本里一条 `{"passed": "false", ...}`（人为编辑 / 跨语言侧
    #:    写坏）会被读成 `passed=True` / `reward=1.0` —— 正是本函数上面
    #:    刚刚逐条拒绝的那种病，同一函数里双标。
    raw_passed = payload.get("passed")
    if raw_passed is None:
        raw_passed = False
    if not isinstance(raw_passed, bool):
        raise ValueError(
            f"passed 必须是 bool，实得 {type(raw_passed).__name__}"
            f"（字符串 'false' 会被 bool() 判为 True）")
    v = Verdict(
        passed=raw_passed,
        checks=tuple(checks),
        evidence=dict(raw_ev),       # ⛔ 不再 `or {}` 吞掉 [] / "" / 0
        env_fingerprint=str(payload.get("env_fingerprint", "")),
        cost_s=float(payload.get("cost_s", 0.0)),
        error=str(payload.get("error", "")),
    )
    return assert_verdict_sane(v)
