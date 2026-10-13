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

    @property
    def invocation_id(self) -> str:
        return uuid.uuid4().hex[:16]


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

    def __call__(self, args: Dict[str, Any], budget: Budget) -> Any:
        payload = json.dumps(args, ensure_ascii=False).encode("utf-8")
        env = {k: os.environ[k] for k in self.env_allow if k in os.environ}
        try:
            p = subprocess.run(
                [str(a) for a in self.argv], input=payload,
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
            raise CapabilityError(
                "failed",
                f"CLI 退出码 {p.returncode}：{p.stderr.decode('utf-8', 'replace')[:300]}")
        raw = p.stdout[:int(budget.max_output_bytes)]
        txt = raw.decode("utf-8", "replace").strip()
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
        if not (os.path.isabs(exe) or shutil.which(exe)):
            return False, f"找不到可执行文件 {exe!r}"
        return True, ""


@dataclass
class HttpAdapter(BaseAdapter):
    """HTTP 服务：POST JSON。

    ⛔ 响应**按字节封顶**（不然一个坏服务能把宿主内存吃光）。
    ⛔ 超时交给 urlopen（真的会中断）⇒ `timeout_enforced="forced"`。
    ⛔ 只走 stdlib urllib（宪���：零第三方依赖）。
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
        try:
            with request.urlopen(req, timeout=max(0.01, float(budget.timeout_s))) as r:
                raw = r.read(int(budget.max_output_bytes))
        except urlerror.URLError as e:
            raise CapabilityError("unavailable", f"HTTP 请求失败：{e}")
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
    if callable(impl):
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
                    *, route_from: str = "") -> Any:
    """把一个已注册能力**绑进 harness**（加到 L3.skills，可顺便加 L1 路由）。

    ⛔ 为什么必须显式绑定：harness 的技能层是**基因组的一部分**，
    "这个 harness 有哪些技能"必须由 harness 自己声明 —— 否则技能注册表
    一变，同一个 harness_id 的行为就变了，进化结果不可复现。
    （`h.skills` 变了 ⇒ `harness_id` 变 ⇒ 这正是它该有的样子。）

    `route_from` 给的是**任务类别**（不是环境名）：按环境名路由本身就是
    定点化，换环境就失效（agent.py 顶部的设计取舍）。
    """
    import dataclasses
    key = f"uci:{cid}"
    skills = dict(getattr(h, "skills", {}) or {})
    skills[key] = summary or cid
    routing = dict(getattr(h, "routing", {}) or {})
    if route_from:
        routing[str(route_from)] = key
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
        import os
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
        self.stats: Dict[str, int] = {s: 0 for s in STATUSES}
        self.stats["invoked"] = 0

    # ── 主入口 ────────────────────────────────────────────────
    def invoke(self, cid: str, args: Optional[Dict[str, Any]] = None,
               *, budget: Optional[Budget] = None, actor: str = "",
               context: Optional[Dict[str, Any]] = None,
               write_ledger: bool = False) -> InvocationResult:
        inv = Invocation(capability=str(cid), args=dict(args or {}),
                         budget=budget or self.default_budget,
                         actor=actor, context=dict(context or {}))
        self.stats["invoked"] += 1
        t0 = time.time()
        res = self._invoke_inner(inv)
        res.cost_s = time.time() - t0
        self.stats[res.status] = self.stats.get(res.status, 0) + 1
        if write_ledger and self.ledger is not None:
            self._append_ledger(res, inv)
        return res

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
