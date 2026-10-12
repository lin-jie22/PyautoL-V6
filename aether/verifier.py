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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

# ⛔ 这些键一旦出现在 payload 里，判定作废 —— 挡住"自述即奖励"这条捷径
SELF_REPORT_KEYS = frozenset({
    "self_reported", "claimed_success", "agent_says", "self_score",
    "llm_score", "model_judged", "claimed_passed", "self_eval",
})


class SelfReportDetected(Exception):
    """判定里混进了模型自述/打分 ⇒ 奖励不可信。"""


@dataclass(frozen=True)
class Check:
    """单条检查（可执行判据的最小单元）。"""
    name: str
    passed: bool
    evidence: str = ""      # ⛔ 必填性由 Verdict.validate() 强制

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "evidence": self.evidence}


@dataclass(frozen=True)
class Verdict:
    """一次验证的裁决。**冻结** —— 入了账本就不能改。

    - passed: 是否通过（reward 的来源，只有 0/1）
    - checks: 逐条检查（可审计）
    - evidence: 外部可观察证据（文件 sha256 / 测试输出 / DB 行 / 退出码）
    - env_fingerprint: 环境快照指纹（同一判据在不同环境下不可比）
    """
    passed: bool
    checks: Tuple[Check, ...] = ()
    evidence: Dict[str, Any] = field(default_factory=dict)
    env_fingerprint: str = ""
    cost_s: float = 0.0
    error: str = ""

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
        """
        if not self.checks:
            return False, "没有任何 check ⇒ 这不是判定，是宣称"
        for c in self.checks:
            if not c.evidence.strip():
                return False, f"check '{c.name}' 缺 evidence"
        hit = SELF_REPORT_KEYS & set(self.evidence)
        if hit:
            return False, f"evidence 含自述/打分字段 {sorted(hit)} ⇒ 奖励不可信"
        return True, ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reward": self.reward,
            "checks": [c.to_dict() for c in self.checks],
            "evidence": self.evidence,
            "env_fingerprint": self.env_fingerprint,
            "cost_s": round(self.cost_s, 4),
            "error": self.error,
        }


class Verifier(Protocol):
    """验证器协议。**输入是环境状态，不是 agent 输出**。"""

    def __call__(self, env_state: Dict[str, Any]) -> Verdict: ...


def assert_no_self_report(payload: Dict[str, Any]) -> None:
    """静态闸：payload 里出现自述/打分键 ⇒ 抛异常。

    放在**写入侧**（构造 Verdict 之前），而不是读取侧 —— 事后过滤等于
    允许这条通路存在，等于没挡。
    """
    hit = SELF_REPORT_KEYS & set(payload or {})
    if hit:
        raise SelfReportDetected(
            f"payload 含自述/打分字段 {sorted(hit)}：奖励必须来自执行判据")


def env_fingerprint(state: Dict[str, Any]) -> str:
    """环境指纹：同一判据在不同环境/不同任务下不可比 ⇒ 必须显式带上。

    V5 的教训：两轮评估口径不同却拿分数对比（wait_terminal ON/OFF），
    ⇒ 分数差 0.07 全是口径差。指纹让"口径变了"变成可检测事件。
    """
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False,
                   default=str).encode("utf-8")
    ).hexdigest()[:16]


# ──────────────────────────────────────────────────────────────────
# 组合子：把小判据拼成大判据（保持 reward 只来自执行）
# ──────────────────────────────────────────────────────────────────
def all_of(name: str, results: List[Tuple[str, bool, str]]) -> Verdict:
    """全部通过才通过。每项 (检查名, 是否通过, 证据)。"""
    checks = tuple(Check(n, bool(p), str(e)) for n, p, e in results)
    passed = all(c.passed for c in checks) and bool(checks)
    return Verdict(passed=passed, checks=checks,
                   evidence={"summary": f"{sum(c.passed for c in checks)}/{len(checks)}"},
                   env_fingerprint=env_fingerprint([n for n, _, _ in results]))


def any_of(name: str, results: List[Tuple[str, bool, str]]) -> Verdict:
    checks = tuple(Check(n, bool(p), str(e)) for n, p, e in results)
    return Verdict(passed=any(c.passed for c in checks), checks=checks,
                   evidence={"summary": f"{sum(c.passed for c in checks)}/{len(checks)}"},
                   env_fingerprint=env_fingerprint([n for n, _, _ in results]))


def from_exit_code(name: str, code: int, stdout: str, stderr: str = "",
                   expect: int = 0) -> Verdict:
    """最常见的可执行判据：跑一个命令，看退出码。

    evidence 必须是**真实输出**（截断但保留原文片段），不能是"跑过了"。
    """
    ok = (code == expect)
    return Verdict(
        passed=ok,
        checks=(Check(f"{name}.exit_code", ok,
                      f"exit={code} expect={expect}"),),
        evidence={
            "exit_code": code,
            "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest()[:16],
            "stdout_head": stdout[:400],
            "stderr_head": stderr[:200],
        },
        env_fingerprint=env_fingerprint({"name": name, "expect": expect}),
    )


def error_verdict(name: str, exc: BaseException) -> Verdict:
    """验证器自己崩了 ⇒ reward 0，且**必须显式留痕**（不是静默通过）。

    V5 教训：异常被吞掉 ⇒ 假成功。这里的 error 字段会进账本。
    """
    return Verdict(passed=False, checks=(), error=f"{type(exc).__name__}: {exc}",
                   evidence={"failed_verifier": name})


def assert_verdict_sane(v: Verdict) -> Verdict:
    """判据闸：不 sane 的 Verdict 直接作废（reward 归 0）。"""
    ok, why = v.validate()
    if not ok:
        return Verdict(passed=False, checks=v.checks, evidence=v.evidence,
                       env_fingerprint=v.env_fingerprint, error=f"VERDICT_INVALID: {why}")
    assert_no_self_report(v.evidence)
    return v


def check_verdict(payload: Dict[str, Any]) -> Verdict:
    """从 dict（账本/JSON 里读回来的）构造 Verdict 并做 sanity 检查。"""
    checks = tuple(Check(c["name"], bool(c["passed"]), c.get("evidence", ""))
                   for c in payload.get("checks", []))
    v = Verdict(
        passed=bool(payload.get("passed", False)),
        checks=checks,
        evidence=payload.get("evidence", {}) or {},
        env_fingerprint=payload.get("env_fingerprint", ""),
        cost_s=float(payload.get("cost_s", 0.0)),
        error=payload.get("error", ""),
    )
    return assert_verdict_sane(v)
