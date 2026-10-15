"""通用接入层（UCI, Universal Capability Interface）—— 任何 agent / skill
都挂得上、叫得动、叫不动时说得清为什么叫不动。

## 为什么必须有这一层（改造前的真实形态）

改造前 V6 里"技能"和"agent"是**两处硬编码**：

    Harness.skills: Dict[str, str]      # 技能是**字符串**
    agent.py 的 if/elif strategy == ... # 执行逻辑写死在分支里

⇒ 加一个技能 = 改 `agent.py` 源码；加一个 agent = 手写一个恰好长成
`runner(h, spec) -> Verdict` 的函数，而这个约定**没有任何地方声明**
（没有协议类、没有版本、没有能力声明、没有失败分类）。

于是外部的东西——别人项目里的一个函数、一个 CLI 工具、一个 HTTP 服务、
一个 MCP server——**根本没有入口**。它们既不能声明自己能干什么，也不能
被路由到，更不能被调用。V6 的进化循环因此只能进化"它自己已经认识的那几个
字符串"。

## 这一层提供什么

    spec      声明：我是谁、我要什么、我给什么、我需要什么权限
    adapter   适配：把任意形态（函数/对象/CLI/HTTP）变成同一个 callable
    registry  注册：内容寻址 + 冲突检测 + 可导出清单（跨语言可校验）
    gateway   调用：校验 → 预算 → 执行 → 判据 → 记账，每步 fail-closed

## ⛔⛔ 三条宪法级约束（本模块是它们的执行点）

**1. 调用结果不产 reward。**
`InvocationResult` **没有** reward 字段，只有 `checks`。reward 只由
`Verdict` 产生（`verifier.py` 是唯一定义）。于是"外部实现自称成功"
在结构上**不可能**变成奖励 —— 这条路被去掉了，不是被过滤了。
（V5 血泪：自述/打分一旦有通路，reward hacking 就必然发生。）

**2. 绝不静默降级。**
未注册 ⇒ `unavailable`（不是 no-op 返回空）；输入不合规 ⇒ `rejected`
（不是"尽力调一把"）；声明的必填输出缺失 ⇒ `failed`（不是 ok）；
适配不出来 ⇒ 抛 `AdapterError` 并列出试过的形态。
⇒ 调用方**永远**能从 status 里看出"到底哪一环断了"。

**3. 不可信实现不许 inproc。**
inproc 适配器**无法强制超时**（Python 杀不掉一个正在跑的线程）⇒
`trusted=False` 的能力只允许 subprocess / network 形态。这条不是洁癖：
"声明了超时但实际不会中断"就是假仪表，假仪表比没有仪表更危险。
subprocess 形态 `timeout_enforced="forced"`，inproc 是 `"none"`，
每一条结果里都带着这个字段 —— 不许假装装上了。

## 判据归属

外部实现自己说 ok 不算 ok。要变成可信判定，spec 必须提供 `checker`：
一个可调用，接到 `(args, output)` 返回 `Verdict`（或 `Check`）。
没有 checker 的结果 `verified=False` 且带 `unverified_reason` ——
**显性地**说"这条没验证过"，而不是给一个假的 verified=True。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .ledger import Ledger, canonical_json, content_hash
from .verifier import (Check, Verdict, assert_verdict_sane, env_fingerprint,
                       error_verdict)

__all__ = [
    "KINDS", "STATUSES", "TRANSPORTS",
    "ParamSpec", "CapabilitySpec", "Budget", "Invocation", "InvocationResult",
    "AdapterError", "CapabilityError", "BaseAdapter",
    "CallableAdapter", "ObjectAdapter", "CliAdapter", "HttpAdapter", "adapt",
    "Registry", "Gateway", "capability",
    "LoadReport", "load_manifest", "bind_capability",
]


KINDS = ("skill", "agent", "verifier", "tool")
#: ⛔ status 是**诊断分类**，不是"成功/失败"二字。调用方要能据此分诊。
STATUSES = ("ok", "failed", "rejected", "timeout", "unavailable")
TRANSPORTS = ("inproc", "subprocess", "network")

#: JSON 类型名 → Python 类型。⛔ `bool` 必须在 `int` 之前判（bool 是 int 子类）。
_TYPES: Dict[str, Tuple[type, ...]] = {
    "any": (object,),
    "str": (str,),
    "int": (int,),
    "float": (float,),
    "num": (int, float),
    "bool": (bool,),
    "dict": (dict,),
    "list": (list, tuple),
    "null": (type(None),),
}


class CapabilityError(Exception):
    """接入层的显性失败。**必须**带 status 与原因，不许裸抛。"""
    def __init__(self, status: str, message: str, **meta: Any):
        #: 🟡 修复五：status 必须属于 STATUSES。拼错会污染 Gateway.stats
        #:    （拼错的键永远计不到数，且永远不会有人发现）。
        #:    ⛔ 用 ValueError 而不是静默归一 —— 这是**程序员错误**，
        #:    不属于"运行时 CapabilityError 家族"；`except CapabilityError`
        #:    不该捕获它。
        if status not in STATUSES:
            raise ValueError(
                f"CapabilityError.status 必须属于 {STATUSES}，实得 {status!r}")
        super().__init__(message)
        self.status = status
        self.message = message
        self.meta = meta


class AdapterError(CapabilityError):
    """适配不出来 / 适配器配置非法。"""
    def __init__(self, message: str, **meta: Any):
        super().__init__("unavailable", message, **meta)


# ──────────────────────────────────────────────────────────────────
# 1. 契约层
# ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParamSpec:
    """一个参数的声明。"""
    type: str = "any"
    required: bool = True
    default: Any = None
    desc: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "required": bool(self.required),
                "default": self.default, "desc": self.desc}

    def check(self, name: str, value: Any) -> str:
        """返回错误描述，空串表示通过。"""
        if value is None:
            return "" if not self.required else f"缺少必填参数 '{name}'"
        if self.type == "any":
            return ""
        if self.type == "bool":
            # ⛔ bool 必须先于 int 判：type 写成 int 时 1/0 不该被当成 bool 过关，
            #    反过来 bool 写进 int 字段也不该过（那是另一类 bug）。
            if not isinstance(value, bool):
                return f"参数 '{name}' 应为 bool，实得 {type(value).__name__}"
            return ""
        if self.type in ("int", "float") and isinstance(value, bool):
            return f"参数 '{name}' 应为 {self.type}，实得 bool"
        exp = _TYPES.get(self.type)
        if exp is None:
            return f"未知类型声明 '{self.type}'（参数 '{name}'）"
        if not isinstance(value, exp):
            return (f"参数 '{name}' 应为 {self.type}，"
                    f"实得 {type(value).__name__}")
        return ""


@dataclass(frozen=True)
class CapabilitySpec:
    """能力声明。**冻结** —— 改声明就是改能力，指纹会变。

    ⛔ `trusted` 与 `effects` 是**能力声明不是承诺**：声明不代表运行时真的
    被限制住（那是适配器/沙箱的事）。它们的用途是让网关在**调用前**就能
    拒绝不该发生的组合（不可信 + inproc / 声明无网络却要发请求），
    以及让清单可被审计 —— 声明与实测不符本身就是可检测事件。
    """
    id: str
    kind: str = "skill"
    version: str = "0.0.0"
    summary: str = ""
    inputs: Dict[str, ParamSpec] = field(default_factory=dict)
    outputs: Dict[str, ParamSpec] = field(default_factory=dict)
    tags: Tuple[str, ...] = ()
    timeout_s: float = 10.0
    sandbox: str = "process"          # none | process | seatbelt
    trusted: bool = False             # ⛔ False ⇒ 不许 inproc
    effects: Tuple[str, ...] = ()     # read_fs write_fs network exec
    #: 判据钩子：没有它 ⇒ 结果 verified=False（外部自称成功不算成功）
    checker: Optional[Callable[[Dict[str, Any], Any], Any]] = None

    def __post_init__(self) -> None:
        if not self.id or not str(self.id).strip():
            raise ValueError("CapabilitySpec.id 不得为空")
        if self.kind not in KINDS:
            raise ValueError(f"未知 kind={self.kind!r}，应为 {KINDS}")
        if self.sandbox not in ("none", "process", "seatbelt"):
            raise ValueError(f"未知 sandbox={self.sandbox!r}")
        if float(self.timeout_s) <= 0:
            raise ValueError(f"timeout_s 必须 > 0，实得 {self.timeout_s}")

    @property
    def fingerprint(self) -> str:
        """声明指纹。**不含** checker（它是代码，不可序列化）。

        ⛔ 声明变了 ⇒ 指纹变 ⇒ 调用方会发现"我用的不是我以为的那个能力"。
        """
        return content_hash({
            "id": self.id, "kind": self.kind, "version": self.version,
            "inputs": {k: v.to_dict() for k, v in self.inputs.items()},
            "outputs": {k: v.to_dict() for k, v in self.outputs.items()},
            "tags": list(self.tags), "timeout_s": round(float(self.timeout_s), 3),
            "sandbox": self.sandbox, "trusted": bool(self.trusted),
            "effects": list(self.effects),
        })[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "version": self.version,
            "summary": self.summary,
            "inputs": {k: v.to_dict() for k, v in self.inputs.items()},
            "outputs": {k: v.to_dict() for k, v in self.outputs.items()},
            "tags": list(self.tags), "timeout_s": float(self.timeout_s),
            "sandbox": self.sandbox, "trusted": bool(self.trusted),
            "effects": list(self.effects),
            "has_checker": self.checker is not None,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class Budget:
    """一次调用的资源预算。超预算即 fail-closed，不"尽力跑完"。"""
    timeout_s: float = 10.0
    max_output_bytes: int = 1_000_000

    #: 🟡 修复四：非法的 Budget 在**构造时**就该被拒 ——
    #:    `Budget(timeout_s=0)` / `max_output_bytes=-1` 依赖下游每个适配器
    #:    自己 max(0.01, ...) 夹取，是碰运气不是纪律。
    def __post_init__(self) -> None:
        if float(self.timeout_s) <= 0:
            raise ValueError(
                f"Budget.timeout_s 必须 > 0，实得 {self.timeout_s}")
        if int(self.max_output_bytes) <= 0:
            raise ValueError(
                f"Budget.max_output_bytes 必须 > 0，实得 {self.max_output_bytes}")

    def clamped_by(self, spec: CapabilitySpec) -> "Budget":
        """取 spec 声明与本次预算中**更严**的那个。

        ⛔ 不能让调用方把预算调到比声明还宽 —— 声明是能力自己承认的边界。
        """
        return Budget(
            timeout_s=min(float(self.timeout_s), float(spec.timeout_s)),
            max_output_bytes=int(self.max_output_bytes),
        )


@dataclass
class Invocation:
    """调用请求。"""
    capability: str
    args: Dict[str, Any] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)
    actor: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    #: 🔴 修复一：`invocation_id` 必须是**普通字段**，在构造时钉死。
    #:    之前写成 property ⇒ 每次访问都生成新 UUID ⇒ 同一个 Invocation
    #:    上访问两次拿到不同 id，任何 `assert inv.invocation_id ==
    #:    inv.invocation_id` 都永远不通过；调试时 `f"id={inv.invocation_id}"`
    #:    打两次也不一致。
    invocation_id: str = ""

    def __post_init__(self) -> None:
        if not self.invocation_id:
            self.invocation_id = uuid.uuid4().hex[:16]


@dataclass
class InvocationResult:
    """调用结果。

    ⛔ **故意没有 reward 字段**：reward 只由 `Verdict` 产生。外部实现
    自称成功在结构上无法变成奖励 —— 这条路不存在，不是被过滤掉的。
    """
    status: str
    capability: str = ""
    capability_fingerprint: str = ""
    output: Any = None
    checks: Tuple[Check, ...] = ()
    error: str = ""
    cost_s: float = 0.0
    verified: bool = False
    unverified_reason: str = ""
    transport: str = ""
    timeout_enforced: str = ""        # forced | cooperative | none
    #: ⛔ **声明 vs 实际的落差记账**（2026-10-14 r29）：
    #:    `timeout_enforced="none"` 只说"这个形态杀不掉"，但**这次到底
    #:    超没超时**没有读数 ⇒ 调用方看到 status=ok 会以为预算兑现了。
    #:    · `timeout_declared_s`              —— 本次**生效**的超时预算
    #:      （= min(调用方预算, spec 声明)，与真正喂给适配器的是同一个值）
    #:    · `timeout_declared_but_unenforced` —— 真跑超了却没被记为 timeout
    #:    ⛔ 判据里必须排除 `status == "timeout"`：那正是**兑现**的样子
    #:      （被中断了，耗时≈预算），把它算成落差就是颠倒黑白。
    timeout_declared_s: float = 0.0
    timeout_declared_but_unenforced: bool = False
    sandbox: Dict[str, Any] = field(default_factory=dict)
    invocation_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def verdict(self) -> Optional[Verdict]:
        """把 checks 折成一个 Verdict（只有**有 check** 时才成立）。

        ⛔ 没有 check 就返回 None —— "没有判据"不等于"判据通过"。
        """
        if not self.checks:
            return None
        return Verdict(
            passed=all(c.passed for c in self.checks),
            checks=self.checks,
            evidence={"capability": self.capability,
                      "n_checks": len(self.checks)},
            env_fingerprint=env_fingerprint({"cap": self.capability}),
            cost_s=self.cost_s,
            error=self.error,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "capability": self.capability,
            "capability_fingerprint": self.capability_fingerprint,
            "output": self.output,
            "checks": [c.to_dict() for c in self.checks],
            "error": self.error, "cost_s": round(self.cost_s, 4),
            "verified": self.verified,
            "unverified_reason": self.unverified_reason,
            "transport": self.transport,
            "timeout_enforced": self.timeout_enforced,
            "timeout_declared_s": round(float(self.timeout_declared_s), 4),
            "timeout_declared_but_unenforced": bool(
                self.timeout_declared_but_unenforced),
            "sandbox": self.sandbox, "invocation_id": self.invocation_id,
            "meta": self.meta,
        }


# ──────────────────────────────────────────────────────────────────
# 2. 适配器层
# ──────────────────────────────────────────────────────────────────
@dataclass
class BaseAdapter:
    """适配器基类。

    ⛔ `timeout_enforced` 必须**如实**填：
        forced       —— 真的能中断（subprocess / 网络）
        cooperative  —— 只能靠实现自己检查 deadline
        none         —— 根本做不到（inproc）
    写 `forced` 而实际做不到 = 假仪表，比没有仪表更危险。
    """
    transport: str = "inproc"
    timeout_enforced: str = "none"
    detail: str = ""
    #: ⛔ `seatbelt_active` 必须**探测出来**，不许硬编码。
    #:   2026-10-14 前它在记账里写死 `False`：即使将来 OS 层沙箱可用
    #:   （换环境 / 换 Linux bubblewrap），记账也永远说 False ⇒ 与
    #:   `rlimits_applied` 同一个坑（假仪表）。
    #:   ⇒ 由**适配器自己**在真正跑之前探测并写回；没探测过 ⇒ False
    #:     且 `sandbox_detail` 必须说明原因（不许空串冒充"已确认没有"）。
    seatbelt_active: bool = False
    sandbox_detail: str = "未探测"

    def __call__(self, args: Dict[str, Any], budget: Budget) -> Any:
        raise NotImplementedError

    def probe(self) -> Tuple[bool, str]:
        """可用性探测。⛔ 失败要说**为什么**，不许返回 (False, "")。"""
        return True, ""


@dataclass
class CallableAdapter(BaseAdapter):
    """函数 / lambda / 任何 callable。"""
    fn: Callable[..., Any] = None     # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.transport = "inproc"
        self.timeout_enforced = "none"
        self.detail = f"callable {getattr(self.fn, '__name__', type(self.fn).__name__)}"

    def __call__(self, args: Dict[str, Any], budget: Budget) -> Any:
        return self.fn(**args)

    def probe(self) -> Tuple[bool, str]:
        if not callable(self.fn):
            return False, f"不是 callable：{type(self.fn).__name__}"
        return True, ""


@dataclass
class ObjectAdapter(BaseAdapter):
    """对象：按 run / invoke / __call__ 顺序找入口。

    ⛔ 找不到入口 ⇒ 抛错（不退化成"什么都不做"）。入口优先级固定，
    不许"哪个能用用哪个" —— 那会让同名不同语义的方法被随机选中。
    """
    obj: Any = None
    entry: str = ""

    def __post_init__(self) -> None:
        self.transport = "inproc"
        self.timeout_enforced = "none"
        for name in ("run", "invoke", "execute", "__call__"):
            if hasattr(self.obj, name) and callable(getattr(self.obj, name)):
                self.entry = name
                break
        if not self.entry:
            raise AdapterError(
                f"对象 {type(self.obj).__name__} 没有可调用入口"
                f"（试过 run/invoke/execute/__call__）")
        self.detail = f"{type(self.obj).__name__}.{self.entry}"

    def __call__(self, args: Dict[str, Any], budget: Budget) -> Any:
        return getattr(self.obj, self.entry)(**args)

    def probe(self) -> Tuple[bool, str]:
        ok = bool(self.entry) and callable(getattr(self.obj, self.entry, None))
        return ok, ("" if ok else f"入口 {self.entry!r} 不可调用")


@dataclass
class CliAdapter(BaseAdapter):
    """命令行工具：argv + stdin(JSON) → stdout(JSON)。

    ⛔ `shell=False`（永不）—— argv 里带 shell 元字符会变成命令注入。
    ⛔ env **净化**：不继承宿主环境变量（密钥泄漏的常规入口），只给
    PATH/HOME/LANG/TMPDIR 这类最小集。
    ⛔ 超时是**真的**能杀（subprocess.run(timeout=) ⇒ kill），
    所以 `timeout_enforced="forced"` —— 这里才敢写 forced。
    """
    argv: List[str] = field(default_factory=list)
    env_allow: Tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
    cwd: Optional[str] = None

    def __post_init__(self) -> None:
        self.transport = "subprocess"
        self.timeout_enforced = "forced"
        self.detail = "cli " + " ".join(str(a) for a in self.argv[:3])
        if not self.argv:
            raise AdapterError("CliAdapter.argv 不得为空")

    #: ── OS 层沙箱：**探测式**接入（与 environment 同一纪律）─────────
    #: ⛔ 为什么在这里也做一遍：`sandbox-exec` 在本环境实测不可用
    #:    （rc=71 / `Operation not permitted`），但这不是"永远不会有" ——
    #:    换环境、或将来接 Linux bubblewrap 就能用。写死 False 会让记账
    #:    在它可用之后仍然说 False ⇒ 假仪表。
    #: ⛔ 探测失败**必须留原因**（不许空串）：调用方要能区分
    #:    "探测过，确实没有" 与 "根本没探测"。
    def _resolve_seatbelt(self) -> List[str]:
        try:
            from .environment import _os_sandbox_cmd
        except Exception as ex:  # noqa: BLE001
            self.seatbelt_active = False
            self.sandbox_detail = f"探测不可用（import 失败：{ex}）"
            return []
        try:
            got = _os_sandbox_cmd(self.cwd or os.getcwd())
        except Exception as ex:  # noqa: BLE001
            self.seatbelt_active = False
            self.sandbox_detail = f"探测抛异常：{type(ex).__name__}"
            return []
        if not got:
            self.seatbelt_active = False
            self.sandbox_detail = "探测过：本环境 OS 层沙箱不可用（已降级为进程隔离）"
            return []
        self.seatbelt_active = True
        self.sandbox_detail = f"OS 层沙箱已套用：{got[1]}"
        return list(got[0])

    def __call__(self, args: Dict[str, Any], budget: Budget) -> Any:
        payload = json.dumps(args, ensure_ascii=False).encode("utf-8")
        env = {k: os.environ[k] for k in self.env_allow if k in os.environ}
        prefix = self._resolve_seatbelt()
        try:
            p = subprocess.run(
                prefix + [str(a) for a in self.argv], input=payload,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=max(0.01, float(budget.timeout_s)),
                env=env, shell=False, cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            raise CapabilityError("timeout", f"CLI 超时 {budget.timeout_s}s")
        except FileNotFoundError as e:
            raise CapabilityError(
                "unavailable", f"可执行文件不存在：{self.argv[0]}（{e}）")
        if p.returncode != 0:
            #: ⛔⛔ 非零退出码**不等于**"没有任何诊断"。实测（REA 桥）：
            #:    子进程把完整的诊断信封写在 **stdout**（JSON），stderr 是空的，
            #:    然后以非零码退出表示"这次没成功"。原实现直接
            #:    `raise CapabilityError("failed", "…" + stderr[:300])`
            #:    ⇒ **stdout 被整个丢掉** ⇒ 调用方只看到"退出码 1"，
            #:    看不到桥精心写的 `timeout_reason` / `degraded` / `full_output`。
            #:    这与桥自己的注释（"超时单独开一个读数，不许混进 degraded"）
            #:    直接矛盾 —— 它认真分了诊，我们在适配层把诊断扔了。
            #:    ⇒ 先**尽力**解析 stdout 信封，把诊断带进 error 与 meta。
            #:    ⛔ 只解析、不改变判定：退出码非 0 仍然是 failed，
            #:      绝不因为"信封看起来像 ok"就放行（那是把失败伪装成成功）。
            env, why = None, ""
            try:
                cand = json.loads(p.stdout.decode("utf-8", "replace"))
                if isinstance(cand, dict):
                    env = cand
            except Exception:  # noqa: BLE001 —— 不是 JSON 就算了，不因此报错
                env = None
            if env is not None:
                why = str(env.get("reason") or env.get("message")
                          or env.get("timeout_reason") or "")
            err_txt = p.stderr.decode("utf-8", "replace").strip()
            detail = why or err_txt[:300] or "(子进程没有给出任何诊断)"
            #: ⛔⛔ 分诊**必须保留**：桥把"我们等不到结果"（timed_out）与
            #:    "工具判失败"（failed）分得很清楚（它自己的注释写着
            #:    "超时单独开一个读数，不许混进 degraded"），但适配层原来
            #:    一律压成 `failed` ⇒ 调用方拿到的分诊比子进程能给的**更粗**
            #:    ⇒ "超时了该重试" 与 "工具说不行" 变成同一件事。
            #:    ⛔ 只在子进程**明确**说自己超时才改判：靠退出码猜是骗人。
            st = "failed"
            if env is not None and (env.get("timed_out") is True
                                    or bool(env.get("timeout_reason"))):
                st = "timeout"
            raise CapabilityError(
                st,
                f"CLI 退出码 {p.returncode}：{detail}",
                child_envelope=env, child_stdout_tail=(
                    p.stdout.decode("utf-8", "replace")[-800:] if env is None
                    else ""))

        #: 🟠 修复二：**拒绝静默截断**。原先 `p.stdout[:max_output_bytes]`
        #:    会把超长输出砍成半截 JSON ⇒ 解析失败 ⇒ 返回 `{"_raw": "..."}`
        #:    ⇒ 调用方拿到一个语法合法但内容残缺的结果，无法区分
        #:    "CLI 输出的是纯文本" vs "JSON 被截断了"。
        #:    截断会让错误伪装成成功，与"绝不静默降级"同源。
        limit = int(budget.max_output_bytes)
        if len(p.stdout) > limit:
            raise CapabilityError(
                "failed",
                f"CLI 输出 {len(p.stdout)} 字节超过预算 {limit} 字节"
                f"（截断会产出半截 JSON，错误伪装成成功）")

        txt = p.stdout.decode("utf-8", "replace").strip()
        if not txt:
            return None
        try:
            return json.loads(txt)
        except Exception:  # noqa: BLE001
            #: ⛔ 不是合法 JSON ⇒ **原样交出并标注**，不伪装成解析成功
            return {"_raw": txt}

    def probe(self) -> Tuple[bool, str]:
        import shutil
        exe = str(self.argv[0]) if self.argv else ""
        if not exe:
            return False, "argv 为空"
        #: 🟠 修复三：绝对路径**不等于**可执行文件存在。原先
        #:    `os.path.isabs(exe)` 为真就直接判"可用"，实际调时才
        #:    FileNotFoundError ⇒ 注册时报"已就位"、调用时说"没有这个文件"
        #:    —— 与"声明超时但杀不掉"同族的假仪表。
        if os.path.isabs(exe):
            if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
                return False, f"路径不可执行或不存在 {exe!r}"
            return True, ""
        if not shutil.which(exe):
            return False, f"PATH 里找不到 {exe!r}"
        return True, ""


@dataclass
class HttpAdapter(BaseAdapter):
    """HTTP 服务：POST JSON。

    ⛔ 响应**按字节封顶**（不然一个坏服务能把宿主内存吃光）。
    ⛔ 超时交给 urlopen（真的会中断）⇒ `timeout_enforced="forced"`。
    ⛔ 只走 stdlib urllib（宪法：零第三方依赖）。
    """
    url: str = ""
    method: str = "POST"
    headers: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.transport = "network"
        self.timeout_enforced = "forced"
        self.detail = f"http {self.method} {self.url}"
        if not str(self.url).lower().startswith(("http://", "https://")):
            raise AdapterError(f"HttpAdapter.url 必须是 http(s)，实得 {self.url!r}")

    def __call__(self, args: Dict[str, Any], budget: Budget) -> Any:
        from urllib import request, error as urlerror
        data = json.dumps(args, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.url, data=data, method=self.method,
            headers={"Content-Type": "application/json", **self.headers})
        limit = int(budget.max_output_bytes)
        try:
            with request.urlopen(
                    req, timeout=max(0.01, float(budget.timeout_s))) as r:
                #: 🟠 修复二：`read(limit + 1)` 多读一字节探测是否超限 ——
                #:    直接 `read(limit)` 会把超长响应悄悄砍到 limit 字节，
                #:    解析半截 JSON 失败后伪装成 `{"_raw": ...}`。
                raw = r.read(limit + 1)
                if len(raw) > limit:
                    raise CapabilityError(
                        "failed",
                        f"HTTP 响应超过预算 {limit} 字节"
                        f"（截断会产出半截 JSON，错误伪装成成功）")
        except urlerror.URLError as e:
            raise CapabilityError("unavailable", f"HTTP 请求失败：{e}")
        except CapabilityError:
            #: ⛔ 上面为"超限"主动抛的 CapabilityError 必须**原样透传**，
            #:    不能被下面的 `except Exception` 当成 HTTP 异常重新包装。
            raise
        except Exception as e:  # noqa: BLE001  —— 含 socket.timeout
            if "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower():
                raise CapabilityError("timeout", f"HTTP 超时 {budget.timeout_s}s")
            raise CapabilityError("failed", f"HTTP 异常：{type(e).__name__}: {e}")
        txt = raw.decode("utf-8", "replace").strip()
        if not txt:
            return None
        try:
            return json.loads(txt)
        except Exception:  # noqa: BLE001
            return {"_raw": txt}

    def probe(self) -> Tuple[bool, str]:
        return True, ""   # 网络服务的可用性只有在真调时才知道，不谎报


def adapt(impl: Any) -> BaseAdapter:
    """把**任意形态**适配成统一 callable。

    探测顺序（固定，不许"哪个能用用哪个"）：
        BaseAdapter → callable → 对象(run/invoke/execute/__call__)
        → http(s):// 字符串 → 可执行文件路径

    ⛔⛔ 探测不出来 ⇒ **抛 AdapterError 并列出试过的全部形态**。
    绝不退化成一个"什么都不做的适配器" —— 那会让调用方以为调用成功了
    （status=ok、output=None），是最坏的一种静默降级。
    """
    tried: List[str] = []
    if isinstance(impl, BaseAdapter):
        return impl
    #: 🔵 修复九.1：`pathlib.Path` 在某些 Python 版本上恰好有 `__call__`
    #:    （或用户自定义了子类）⇒ 会被误判成 callable。PathLike 一律
    #:    走"路径"分支，不走 callable 分支。
    if callable(impl) and not isinstance(impl, (str, os.PathLike)):
        return CallableAdapter(fn=impl)
    tried.append("callable(函数/lambda)")
    if isinstance(impl, str) and impl.lower().startswith(("http://", "https://")):
        return HttpAdapter(url=impl)
    tried.append("http(s):// URL")
    if isinstance(impl, (str, os.PathLike)):
        p = os.fspath(impl)
        return CliAdapter(argv=[p])
    tried.append("可执行文件路径")
    if not isinstance(impl, (str, int, float, bool, list, dict, tuple)):
        try:
            return ObjectAdapter(obj=impl)
        except AdapterError as e:
            tried.append(f"对象入口（{e.message}）")
    if isinstance(impl, (list, tuple)) and impl and all(
            isinstance(x, str) for x in impl):
        return CliAdapter(argv=list(impl))
    tried.append("argv 列表")
    raise AdapterError(
        f"无法把 {type(impl).__name__} 适配成 capability；已尝试：{tried}")


# ──────────────────────────────────────────────────────────────────
# 3. 注册表
# ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Entry:
    spec: CapabilitySpec
    adapter: BaseAdapter


class Registry:
    """能力注册表。

    ⛔ 重复 id 注册**必须**抛错（除非指纹完全一致 = 同一个能力注册两次）：
    两个不同的实现抢同一个 id ⇒ 调用方拿到的是"最后注册的那个"，
    进化循环会以为自己在 A/B 对比，实际在对比同一个东西。
    """

    def __init__(self) -> None:
        self._by_id: Dict[str, _Entry] = {}

    def register(self, spec: CapabilitySpec, impl: Any = None,
                 adapter: Optional[BaseAdapter] = None,
                 replace: bool = False) -> CapabilitySpec:
        if not isinstance(spec, CapabilitySpec):
            raise TypeError(f"spec 必须是 CapabilitySpec，实得 {type(spec).__name__}")
        ad = adapter if adapter is not None else adapt(impl)
        if not isinstance(ad, BaseAdapter):
            raise TypeError(f"adapter 必须是 BaseAdapter，实得 {type(ad).__name__}")
        #: ⛔ 不可信实现不许 inproc：inproc 杀不掉，声明的超时是假的
        if not spec.trusted and ad.transport == "inproc":
            raise CapabilityError(
                "rejected",
                f"不可信能力 {spec.id!r} 不得用 inproc 适配器"
                f"（无法强制超时 ⇒ 声明的 timeout_s 是假仪表）；"
                f"请声明 trusted=True 或改用 CLI/HTTP 形态")
        prev = self._by_id.get(spec.id)
        if prev is not None and not replace:
            if prev.spec.fingerprint != spec.fingerprint:
                raise CapabilityError(
                    "rejected",
                    f"能力 id {spec.id!r} 已注册且声明不同"
                    f"（旧指纹 {prev.spec.fingerprint} / 新 {spec.fingerprint}）；"
                    f"要覆盖请显式 replace=True")
            #: 🔵 修复九.4：指纹相同但适配器可能不同（同一能力，两个 HTTP
            #:    endpoint）。原先静默替换 —— 这不是"重装同一个能力"就是
            #:    "换了实现却忘了改版本"，两种含义完全不同。给出警告，
            #:    让人有机会分辨。
            if (type(prev.adapter) is not type(ad)
                    or prev.adapter.detail != ad.detail):
                warnings.warn(
                    f"能力 {spec.id!r} 指纹相同但适配器实现不同 "
                    f"（{prev.adapter.detail} → {ad.detail}），将覆盖。"
                    f"若是有意换实现，请同时更新 spec.version。",
                    UserWarning, stacklevel=2)
        ok, why = ad.probe()
        if not ok:
            raise CapabilityError(
                "unavailable", f"能力 {spec.id!r} 适配器不可用：{why}")
        self._by_id[spec.id] = _Entry(spec=spec, adapter=ad)
        return spec

    def get(self, cid: str) -> Optional[CapabilitySpec]:
        e = self._by_id.get(str(cid))
        return e.spec if e else None

    def entry(self, cid: str) -> Optional[_Entry]:
        return self._by_id.get(str(cid))

    def list(self, kind: str = "", tag: str = "") -> List[CapabilitySpec]:
        out = []
        for e in self._by_id.values():
            if kind and e.spec.kind != kind:
                continue
            if tag and tag not in e.spec.tags:
                continue
            out.append(e.spec)
        return sorted(out, key=lambda s: s.id)

    def find(self, *, kind: str = "", tag: str = "",
             provides: str = "") -> List[CapabilitySpec]:
        """按能力发现：`provides` 匹配输出字段名。"""
        out = self.list(kind=kind, tag=tag)
        if provides:
            out = [s for s in out if provides in s.outputs]
        return out

    @property
    def fingerprint(self) -> str:
        """整份清单的指纹（可跨运行/跨语言比对）。"""
        return content_hash([s.to_dict() for s in self.list()])[:16]

    def manifest(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.list()]

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, cid: object) -> bool:
        return str(cid) in self._by_id


def capability(cid: str, *, kind: str = "skill", version: str = "0.0.0",
               summary: str = "", tags: Tuple[str, ...] = (),
               #: ⛔ 默认 True 不是"放宽"：装饰器只能产生 inproc 适配器
               #:    （代码就在本进程里跑），inproc 本身就意味着可信；
               #:    外部实现请**显式** trusted=False 并走 CLI/HTTP 形态，
               #:    那时 register() 会拒绝 inproc 组合。
               timeout_s: float = 10.0, trusted: bool = True,
               effects: Tuple[str, ...] = (), sandbox: str = "process",
               checker: Optional[Callable[[Dict[str, Any], Any], Any]] = None,
               inputs: Optional[Dict[str, ParamSpec]] = None,
               outputs: Optional[Dict[str, ParamSpec]] = None,
               registry: Optional[Registry] = None) -> Callable[[Any], Any]:
    """装饰器：把一个函数/类注册成 capability。

    ⛔ 装饰器形态**只能是 inproc** ⇒ 要求 `trusted=True`，否则注册时就被
    拒（不可信 + inproc 的组合在 register 里拦）。这不是刁难：
    装饰器是最容易"顺手注册一堆不可信外部函数"的入口。
    """
    def deco(impl: Any) -> Any:
        spec = CapabilitySpec(
            id=cid, kind=kind, version=version, summary=summary or (
                getattr(impl, "__doc__", "") or "").strip().split("\n")[0],
            inputs=inputs or {}, outputs=outputs or {}, tags=tags,
            timeout_s=timeout_s, sandbox=sandbox, trusted=trusted,
            effects=effects, checker=checker)
        (registry if registry is not None else _default_registry()).register(
            spec, impl)
        return impl
    return deco


_DEFAULT: Optional[Registry] = None


def _default_registry() -> Registry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Registry()
    return _DEFAULT


# ──────────────────────────────────────────────────────────────────
# 3.5 声明式装载：接一个外部能力**不用改 Python 代码**
# ──────────────────────────────────────────────────────────────────
@dataclass
class LoadReport:
    """装载报告。⛔ 每条 skip **必须带原因** —— 静默跳过 = 能力凭空消失。"""
    loaded: List[str] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "n_loaded": len(self.loaded),
                "loaded": self.loaded, "n_skipped": len(self.skipped),
                "skipped": self.skipped}

    @property
    def ok(self) -> bool:
        return not self.skipped


def _param(d: Dict[str, Any]) -> ParamSpec:
    return ParamSpec(type=str(d.get("type", "any")),
                     required=bool(d.get("required", True)),
                     default=d.get("default"), desc=str(d.get("desc", "")))


def _spec_from(d: Dict[str, Any]) -> CapabilitySpec:
    cid = str(d.get("id", "")).strip()
    if not cid:
        raise CapabilityError("rejected", "清单条目缺 id")
    kind = str(d.get("kind", "skill"))
    if kind not in KINDS:
        raise CapabilityError("rejected", f"{cid}: 未知 kind={kind!r}")
    #: 🟡 修复六：`CapabilitySpec.__post_init__` 里的校验（sandbox、timeout_s）
    #:    抛 `ValueError`。若不转成 `CapabilityError`，`load_manifest` 的
    #:    `except CapabilityError` 分支接不到 ⇒ 会被归为 `status="failed"`
    #:    ⇒ 一条**清单写错**（`sandbox: "dockerr"`）看起来像"能力运行出错"，
    #:    排查方向全反。这是**输入不合法**，应归 `"rejected"`。
    try:
        return CapabilitySpec(
            id=cid, kind=kind, version=str(d.get("version", "0.0.0")),
            summary=str(d.get("summary", "")),
            inputs={k: _param(v) for k, v in (d.get("inputs") or {}).items()},
            outputs={k: _param(v) for k, v in (d.get("outputs") or {}).items()},
            tags=tuple(d.get("tags", ()) or ()),
            timeout_s=float(d.get("timeout_s", 10.0)),
            sandbox=str(d.get("sandbox", "process")),
            #: ⛔ trusted 默认 **False**：清单里导入任意 Python 对象 = 任意代码
            #:   执行进本进程 ⇒ 必须是写清单的人的知情选择，不是默认值。
            trusted=bool(d.get("trusted", False)),
            effects=tuple(d.get("effects", ()) or ()),
        )
    except ValueError as ex:
        raise CapabilityError("rejected", f"{cid}: {ex}")


def _adapter_from(d: Dict[str, Any]) -> BaseAdapter:
    """按 impl.transport 造适配器。

    ⛔ `python` 形态会 `importlib` 导入任意模块 ⇒ 等同任意代码执行。
    这是"接入"的本质代价，但不许是**隐性**的：必须清单里显式写
    `trusted: true`，否则 register() 会拒绝 inproc 组合。
    """
    impl = d.get("impl") or {}
    t = str(impl.get("transport", "")).strip()
    if t == "cli":
        argv = impl.get("argv")
        if not argv:
            raise CapabilityError("rejected", f"{d.get('id')}: cli 缺 argv")
        a = CliAdapter(argv=[str(x) for x in argv])
        if impl.get("env_allow"):
            a.env_allow = tuple(str(x) for x in impl["env_allow"])
        return a
    if t == "http":
        return HttpAdapter(url=str(impl.get("url", "")),
                           method=str(impl.get("method", "POST")),
                           headers=dict(impl.get("headers") or {}))
    if t == "python":
        target = str(impl.get("target", "")).strip()
        if not target or (":" not in target and "." not in target):
            raise CapabilityError(
                "rejected", f"{d.get('id')}: python 形态 target 需为 "
                            f"'pkg.mod:obj' 或 'pkg.mod'，实得 {target!r}")
        mod_name, _, attr = target.partition(":")
        try:
            import importlib
            obj = importlib.import_module(mod_name)
        except Exception as ex:  # noqa: BLE001
            raise CapabilityError(
                "unavailable", f"{d.get('id')}: 导入 {mod_name} 失败："
                               f"{type(ex).__name__}: {ex}")
        if attr:
            obj = getattr(obj, attr, None)
            if obj is None:
                raise CapabilityError(
                    "unavailable", f"{d.get('id')}: {mod_name} 无属性 {attr!r}")
        return adapt(obj)
    raise CapabilityError(
        "rejected",
        f"{d.get('id')}: 未知 transport={t!r}（应为 cli/http/python）")


def bind_capability(h: Any, cid: str, summary: str = "",
                    *, route_from: str = "", append: bool = True) -> Any:
    """把一个已注册能力**绑进 harness**（加到 L3.skills，可顺便加 L1 路由）。

    ⛔ 为什么必须显式绑定：harness 的技能层是**基因组的一部分**，
    "这个 harness 有哪些技能"必须由 harness 自己声明 —— 否则技能注册表
    一变，同一个 harness_id 的行为就变了，进化结果不可复现。
    （`h.skills` 变了 ⇒ `harness_id` 变 ⇒ 这正是它该有的样子。）

    `route_from` 给的是**任务类别**（不是环境名）：按环境名路由本身就是
    定点化，换环境就失效（agent.py 顶部的设计取舍）。

    ⛔⛔ `append`（默认 True）= **把能力追加到链尾，不是顶掉整条链**。
        2026-10-14 实测：L1.routing 已是**有序技能链**（§26），而这里
        旧代码写的是 `routing[route_from] = key` ⇒ 绑定一个 UCI 能力会
        把链上原有技能全部抹掉。两个写入点（这里 / `apply_gene`）语义
        不一致 ⇒ 同一个 harness 是"能叠加"还是"互斥"，取决于技能是
        提议器加的还是外部绑的。现在两者都走 `genome.route_with`。
    """
    import dataclasses

    from .genome import route_with
    key = f"uci:{cid}"
    skills = dict(getattr(h, "skills", {}) or {})
    want = summary or cid
    #: 🔵 修复九.2：同名重复绑定会静默替换 summary。只在**真的不同**时
    #:    警告 —— 幂等地重复绑一次不该刷屏（那会让警告变成噪声而被忽略）。
    if key in skills and skills[key] != want:
        warnings.warn(
            f"能力 {cid!r} 已在 harness 中绑定（key={key!r}），"
            f"summary 将被覆盖：{skills[key]!r} → {want!r}",
            UserWarning, stacklevel=2)
    skills[key] = want
    routing = dict(getattr(h, "routing", {}) or {})
    if route_from:
        routing = route_with(routing, str(route_from), key, append=append)
    return dataclasses.replace(h, skills=skills, routing=routing)


def load_manifest(registry: Registry, src: Union[str, Dict[str, Any]],
                  replace: bool = False) -> LoadReport:
    """从 JSON 清单装载能力。

    ⛔ **逐条**装载、逐条记因：一条坏掉不该让整份清单失效（那会让"某个
    能力没挂上"表现为"清单没加载"，排查方向全错）；但每条 skip 都必须
    有原因 —— 静默跳过等于能力凭空消失。
    """
    if isinstance(src, dict):
        data, source = src, "<dict>"
    else:
        #: 🟡 修复七：顶部已 `import os`，此处无需再 import。
        p = os.fspath(src)
        source = p
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:  # noqa: BLE001
            rep = LoadReport(source=source)
            rep.skipped.append({"id": "*", "reason": f"读清单失败：{type(ex).__name__}: {ex}"})
            return rep
    rep = LoadReport(source=source)
    items = data.get("capabilities") if isinstance(data, dict) else data
    if not isinstance(items, list):
        rep.skipped.append({"id": "*", "reason": "清单缺 capabilities 数组"})
        return rep
    for d in items:
        cid = str(d.get("id", "?"))
        if not isinstance(d, dict):
            rep.skipped.append({"id": cid, "status": "rejected",
                                "reason": "清单条目必须是对象"})
            continue
        #: ⛔⛔ JSON 清单**结构上**无法携带判据：`checker` 是代码，不可
        #:    序列化。写进清单会被**静默忽略** ⇒ 能力永远是
        #:    `verified=false`，而声明者以为自己挂上了判据（假仪表）。
        #:    实测（2026-10-14 r29）：清单接入的能力 100% `no_checker`。
        #:    ⇒ fail-closed：明确拒绝，并指出两条正当出路。
        if "checker" in d:
            rep.skipped.append({
                "id": cid, "status": "rejected",
                "reason": "JSON 清单不支持 checker（判据是代码，不可序列化）"
                          "⇒ 写在这里会被静默忽略，能力将永远 verified=false。"
                          "要带判据：① 用代码注册 CapabilitySpec(checker=...)；"
                          "② 或交给下游（如 agent 的 verify_source）判定"})
            continue
        try:
            spec = _spec_from(d)
            ad = _adapter_from(d)
            registry.register(spec, adapter=ad, replace=replace)
            rep.loaded.append(cid)
        except CapabilityError as e:
            rep.skipped.append({"id": cid, "status": e.status,
                                "reason": e.message})
        except Exception as ex:  # noqa: BLE001
            rep.skipped.append({"id": cid, "status": "failed",
                                "reason": f"{type(ex).__name__}: {ex}"})
    return rep


def mount_capabilities(src: Union[str, Dict[str, Any], None],
                       ledger: Optional[Ledger] = None
                       ) -> Tuple[Any, LoadReport]:
    """装载能力并返回可直接用的网关 —— **两个入口唯一的装载接线**。

    ⛔ 为什么必须抽出来：CLI（`cli.cmd_caps`）与 HTTP（`serve.main` 启动 +
    `/api/capabilities/reload` 热装载）此前**各自**写了一遍
    `Registry() → load_manifest() → Gateway()`。虽然底层都落到
    `load_manifest`（判据同源），但**入口接线**有两份 ⇒ 一边加了打印 skip、
    另一边没加，就会出现"CLI 看得到被拒的能力、HTTP 一声不响"的幽灵差异
    （本项目反复栽过：判据有两份实现 = 没有判据）。

    ⛔ `src` 为空 ⇒ 用源码内默认注册表（`_default_registry`），报告为空
    （无来源可报，`source=""` 而不是伪造一个路径）。

    返回 `(gateway, report)`。**调用方负责打印/暴露 skip** —— 装载报告
    不自己吞掉：能力没挂上却一声不响 = 调用时才发现 404。

    ⛔ `ledger`（2026-10-15 收口）：此前这里**不收 ledger** ⇒ CLI / HTTP /
       doctor 三个入口构造出来的 gateway 都没有账本 ⇒ 调用能力时
       `cap.invoke` 事件**一条都写不出**（实测账本 0 次）。不传 = 保持旧行为
       （某些纯探测调用不需要账），但**生产入口必须传**。
    """
    if not src:
        return Gateway(_default_registry(), ledger=ledger), LoadReport()
    reg = Registry()
    return Gateway(reg, ledger=ledger), load_manifest(reg, src)


# ──────────────────────────────────────────────────────────────────
# 4. 网关
# ──────────────────────────────────────────────────────────────────
class Gateway:
    """统一调用入口。

    每一步都 fail-closed，且**每一步的失败都能被分诊**
    （unavailable / rejected / timeout / failed）：

        解析 → 输入校验 → 预算收紧 → 执行 → 输出校验 → 判据 → 记账

    ⛔ 记不清是哪一环断的调用层，等于把"没调用成功"和"调用成功但没用"
    混成一类 —— 那正是 V5 里"假成功"的温床。
    """

    def __init__(self, registry: Optional[Registry] = None,
                 ledger: Optional[Ledger] = None,
                 default_budget: Optional[Budget] = None):
        #: ⛔ 必须写 `is not None`：Registry 定义了 `__len__`，空注册表在布尔
        #:    上下文里是 **falsy** ⇒ `registry or ...` 会静默换成默认空表
        #:    ⇒ 所有调用都返回 unavailable（冒烟时实测 8/8 全 unavailable，
        #:    而已注册数是 0 —— 最容易误判成"注册没生效"）。
        self.registry = registry if registry is not None else _default_registry()
        self.ledger = ledger
        self.default_budget = (default_budget if default_budget is not None
                               else Budget())
        #: 🔵 修复九.3：`invoked` 不是 status。原先与 STATUSES 并列塞进
        #:    同一个 dict ⇒ "分诊统计"里混入了一个非分诊字段。拆出来。
        self.stats: Dict[str, int] = {s: 0 for s in STATUSES}
        self.n_invoked: int = 0

    # ── 主入口 ────────────────────────────────────────────────
    def invoke(self, cid: str, args: Optional[Dict[str, Any]] = None,
               *, budget: Optional[Budget] = None, actor: str = "",
               context: Optional[Dict[str, Any]] = None,
               write_ledger: bool = True) -> InvocationResult:
        """调用一个能力。⛔ 挂了 ledger 就**默认**记 `cap.invoke` 账。

        ⛔⛔ `write_ledger` 此前默认 `False`，且**全代码库没有任何调用方
           传 `True`**（grep 只有定义与使用两处）⇒ 账本里 `cap.invoke`
           事件 **0 次**。这不是"从没调过能力"，是"调用了也不记账"——
           把记账写成默认关的开关，等于仪表盘上永远指零（与第 80 条
           "静默忽略 = 把没生效伪装成在跑"同族，这次落在记账上）。
           现在默认 True：不想记的调用方必须**显式**传 False。
        """
        inv = Invocation(capability=str(cid), args=dict(args or {}),
                         budget=budget or self.default_budget,
                         actor=actor, context=dict(context or {}))
        self.n_invoked += 1
        t0 = time.time()
        res = self._invoke_inner(inv)
        res.cost_s = time.time() - t0
        #: ⛔ 落差记账必须在 `cost_s` 定下来之后算 —— 早一秒都读不到实际耗时
        self._note_timeout_gap(res, inv)
        self.stats[res.status] = self.stats.get(res.status, 0) + 1
        if write_ledger and self.ledger is not None:
            self._append_ledger(res, inv)
        return res

    def _note_timeout_gap(self, res: InvocationResult,
                          inv: Invocation) -> None:
        """把「声明的超时」与「实际耗时」的落差写进结果。

        ⛔⛔ 为什么必须单独记账：`timeout_enforced="none"` 只描述**形态**
        （inproc 杀不掉），不描述**这一次**。实测（2026-10-14 r29）：
        声明 `timeout_s=0.5` 的 inproc 技能实跑 **1.50s** 未被中断，而结果
        里 status 仍是 `ok` —— 调用方只看 status 会以为预算兑现了。这与
        §28「声明与实际的落差必须记账」是同一条纪律，只是这次落在超时上。

        ⛔ 生效预算 = `min(调用方预算, spec 声明)`，与适配器真正拿到的
        是**同一个值**（记另一个口径就是假仪表）。
        ⛔ 容差 0.05s：覆盖计时与进程调度抖动。`status == "timeout"`
        必须排除 —— 被中断正是**兑现**的样子，算成落差就是颠倒黑白。
        """
        e = self.registry.entry(inv.capability)
        if e is None:
            return
        try:
            declared = min(float(inv.budget.timeout_s),
                           float(e.spec.timeout_s))
        except (TypeError, ValueError):
            return
        if declared <= 0:
            return
        res.timeout_declared_s = declared
        res.timeout_declared_but_unenforced = (
            res.cost_s > declared + 0.05 and res.status != "timeout")

    def _invoke_inner(self, inv: Invocation) -> InvocationResult:
        # ① 解析
        e = self.registry.entry(inv.capability)
        if e is None:
            return InvocationResult(
                status="unavailable",
                capability=inv.capability,
                error=f"未注册的 capability {inv.capability!r}"
                      f"（已注册 {len(self.registry)} 个："
                      f"{[s.id for s in self.registry.list()][:8]}）",
                invocation_id=inv.invocation_id)
        spec, ad = e.spec, e.adapter

        # ② 输入校验（fail-closed：不合规就 rejected，绝不"尽力调一把"）
        bad = [m for k, ps in spec.inputs.items()
               if (m := ps.check(k, inv.args.get(k)))]
        unknown = [k for k in inv.args if k not in spec.inputs] if spec.inputs else []
        if bad:
            return InvocationResult(
                status="rejected", capability=spec.id,
                capability_fingerprint=spec.fingerprint,
                error="输入校验失败：" + "；".join(bad),
                transport=ad.transport, timeout_enforced=ad.timeout_enforced,
                invocation_id=inv.invocation_id,
                meta={"unknown_args": unknown} if unknown else {})
        #: 未声明的参数**不拦**（很多外部实现接受 **kwargs），但记下来
        #: —— 声明外的输入是"契约漂移"的早期信号。

        # ③ 预算收紧
        b = inv.budget.clamped_by(spec)

        # ④ 执行
        try:
            out = ad(inv.args, b)
        except CapabilityError as ce:
            return InvocationResult(
                status=ce.status, capability=spec.id,
                capability_fingerprint=spec.fingerprint, error=ce.message,
                transport=ad.transport, timeout_enforced=ad.timeout_enforced,
                invocation_id=inv.invocation_id, meta=dict(ce.meta))
        except Exception as ex:  # noqa: BLE001
            #: ⛔ 实现自己抛的异常 = failed（不是 ok），并**保留**异常类型
            return InvocationResult(
                status="failed", capability=spec.id,
                capability_fingerprint=spec.fingerprint,
                error=f"{type(ex).__name__}: {ex}",
                transport=ad.transport, timeout_enforced=ad.timeout_enforced,
                invocation_id=inv.invocation_id)

        # ⑤ 输出校验（声明的必填输出缺失 ⇒ failed，不是 ok）
        res = InvocationResult(
            status="ok", capability=spec.id,
            capability_fingerprint=spec.fingerprint, output=out,
            transport=ad.transport, timeout_enforced=ad.timeout_enforced,
            #: 🟡 修复八：`sandbox` 不能是永远空的 dict（假仪表：让调用方
            #:    以为沙箱信息可用，读到 {} 却以为是"没沙箱"）。如实填：
            #:    · `declared`      —— spec 声明了什么
            #:    · `transport`     —— 实际走什么形态
            #:    · `enforced`      —— 超时是否真的能中断
            #:    · `seatbelt_active` —— seatbelt 沙箱是否真的装上了
            #:    · `seatbelt_detail` —— 为什么装/没装（不许空串）
            #:    · `declared_but_unenforced` —— **声明与实际的落差**
            #:    ⛔ 不要用 `transport in ("subprocess","network")` 冒充
            #:    "沙箱已装上" —— 进程隔离 ≠ seatbelt 沙箱。
            #:    ⛔ 2026-10-14（r28）：`seatbelt_active` 原为**硬编码
            #:       False**（假仪表 —— 将来真套上了也永远报 False）。
            #:       现改为读适配器自己探测的结果。
            #:    ⛔⛔ 新增 `declared_but_unenforced`：spec 写
            #:       `sandbox="seatbelt"` 而实际没套上，之前**没有任何读数**
            #:       暴露这件事 ⇒ 调用方会以为声明生效了。这是"声明了但没
            #:       兑现"，与 §28「落点要兑现，不只是存在」同一条纪律。
            sandbox={
                "declared": spec.sandbox,
                "transport": ad.transport,
                "enforced": ad.timeout_enforced,
                "seatbelt_active": bool(
                    getattr(ad, "seatbelt_active", False)),
                "seatbelt_detail": str(
                    getattr(ad, "sandbox_detail", "") or "适配器未报告"),
                "declared_but_unenforced": (
                    spec.sandbox == "seatbelt"
                    and not bool(getattr(ad, "seatbelt_active", False))),
            },
            invocation_id=inv.invocation_id,
            meta={"unknown_args": unknown} if unknown else {})
        missing: List[str] = []
        if spec.outputs:
            if not isinstance(out, dict):
                res.status = "failed"
                res.error = (f"声明了 outputs 但实现返回 {type(out).__name__}"
                             f"（应为 dict）")
                return res
            for k, ps in spec.outputs.items():
                m = ps.check(k, out.get(k))
                if m:
                    missing.append(m)
        if missing:
            #: ⛔ 结果已经产生了，但契约没兑现 ⇒ 记为 failed 且**保留 output**
            #:    （调试要看），只是不给"ok"
            res.status = "failed"
            res.error = "输出校验失败：" + "；".join(missing)
            return res

        # ⑥ 判据
        self._apply_checker(spec, inv.args, out, res)
        return res

    def _apply_checker(self, spec: CapabilitySpec, args: Dict[str, Any],
                       out: Any, res: InvocationResult) -> None:
        """把外部输出变成**可执行判据**。

        ⛔ 外部实现自称 ok 不算 ok。三条通路，优先级固定：
           a) spec.checker      —— 能力自己声明的判据 ⇒ verified=True
           b) 输出是 Verdict    —— 已经是判据 ⇒ 过 sanity 闸
           c) 都没有            ⇒ verified=False + 显性 reason
        ⛔ (c) 绝不给 verified=True —— "没验证过"和"验证通过"是两回事。
        """
        if spec.checker is not None:
            try:
                v = spec.checker(args, out)
            except Exception as ex:  # noqa: BLE001
                res.status = "failed"
                res.error = f"checker 抛异常：{type(ex).__name__}: {ex}"
                res.verified = False
                res.unverified_reason = "checker_raised"
                return
            if isinstance(v, Verdict):
                res.checks = v.checks
                res.verified = True
                if not v.passed:
                    res.status = "failed"
                    res.error = v.error or "判据未通过"
            elif isinstance(v, Check):
                res.checks = (v,)
                res.verified = True
                if not v.passed:
                    res.status = "failed"
                    res.error = v.evidence or "判据未通过"
            else:
                #: checker 返回了不是判据的东西 ⇒ **不假装验证过**
                res.verified = False
                res.unverified_reason = (
                    f"checker 返回 {type(v).__name__}，不是 Verdict/Check")
            return
        if isinstance(out, Verdict):
            sane = assert_verdict_sane(out)
            res.checks = sane.checks
            res.verified = True
            if not sane.passed:
                res.status = "failed"
                res.error = sane.error or "判据未通过"
            return
        if isinstance(out, dict) and "checks" in out and "passed" in out:
            try:
                from .verifier import check_verdict
                v = check_verdict(out)
                res.checks = v.checks
                res.verified = True
                if not v.passed:
                    res.status = "failed"
                    res.error = v.error or "判据未通过"
                return
            except Exception as ex:  # noqa: BLE001
                res.verified = False
                res.unverified_reason = f"输出自称 Verdict 但不合法：{ex}"
                return
        res.verified = False
        res.unverified_reason = "no_checker：没有可执行判据，结果未被验证"

    def _append_ledger(self, res: InvocationResult, inv: Invocation) -> None:
        if self.ledger is None:
            return
        self.ledger.append("cap.invoke", {
            "capability": res.capability,
            "capability_fingerprint": res.capability_fingerprint,
            "status": res.status, "verified": res.verified,
            "unverified_reason": res.unverified_reason,
            "transport": res.transport,
            "timeout_enforced": res.timeout_enforced,
            "n_checks": len(res.checks),
            "args_hash": content_hash(inv.args)[:16],
            "actor": inv.actor, "cost_s": round(res.cost_s, 4),
            "error": res.error[:300],
        }, actor=inv.actor or "gateway")

    # ── 打通 Runner 协议：让外部 agent 能被 EvolutionLoop 注入 ──
    def as_runner(self, cid: str, **kw: Any) -> Callable[..., Verdict]:
        """把注册的 agent 包装成 V6 的 Runner 协议 `(h, spec) -> Verdict`。

        ⛔ 没有 check 的调用**不能**假装成通过：返回带 error 的 Verdict
        （reward=0），而不是"跑过了就算过"。
        """
        def runner(h: Any, spec: Dict[str, Any]) -> Verdict:
            res = self.invoke(cid, dict(spec or {}), **kw)
            v = res.verdict
            if v is not None:
                return assert_verdict_sane(v)
            return error_verdict(
                f"cap/{cid}",
                RuntimeError(f"status={res.status} verified=False"
                             f"（{res.unverified_reason}）"
                             f" error={res.error[:200]}"))
        return runner
