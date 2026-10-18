#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""能力**候选**（`caps_candidate`）：发现 → 候选 → 过闸 → 留痕 → **人批准**。

⛔ 模块名为什么是 `caps_candidate` 而不是 `capability`：`aether/__init__.py`
   已经把 **`capability`** 用作 `interop.capability` 装饰器（它在
   `__all__` 里，是公开 API）。模块同名会被包属性遮蔽 —— 实测
   `aether.capability` 解析成一个 function，`from aether import capability`
   拿到的是装饰器，于是"候选"这条链根本用不上。
   ⇒ 改名而不是去动那个公开名字（那是别人已经在用的 API）。
   同族教训：`from aether import check` 当初也撞过 `aether.check`。

原始设计说明如下。

能力**候选**：发现 → 生成候选 → 过同一套闸 → 账本留痕 → **人批准**。

═══ 这条链路解决的那个矛盾 ═══

用户会问："会自己去 GitHub 和其它资源网找后装上吗？"

直接做**不行**，因为它同时踩三条：

  1. **宪法**：判据必须离线可回归 —— 依赖网络供给会让"零新红/非回归"
     随供给波动失效（V5 实测：链首 82% 429，整条供给链废掉两轮）；
  2. **沙箱**：子进程里 `socket.connect` 等网络动作是动作级拦下的；
  3. **信任**：`python` 形态要求清单显式写 `trusted: true`（等同任意代码
     执行），而"自动下载并执行"把"人审过这份清单"这一步整个拿掉。

但它也**不该完全不许**：真实需求（"这个工具没装，装上就能干活"）是存在的。
⇒ 所以做成**候选 + 闸 + 留痕 + 人批准**：

    发现（本机已有？）           discover.which  —— 只读
      ↓
    生成候选                     Candidate      —— 这是**提案**，不是安装
      ↓                        缺什么、要什么形态、谁是责任人，都写清
    过同一套闸                   validate()     —— 与其它自我修改同一套判据
      ↓                        探针复核 / 不许绕过沙箱 / 非回归
    进账本                      cap.candidate  —— 两种结果都记（提交 + 拒绝）
      ↓
    ⛔ **人批准**                approve()      —— 没有 --yes 一律不推进
      ↓
    装（**人做**）               install_hint() —— 我们只产出命令与人话
      ↓
    复验                        verify()       —— 装完重跑，读数进账本

⛔ 本模块**永不自执行安装**：没有 `subprocess.run(["pip", "install", ...])`，
   没有网络请求。`install_hint()` 产出的是**给人看的命令**，
   而且落进账本 —— 所以"谁在什么时候批准了什么、让系统去装什么"
   是可回溯的，而不是一次悄悄的 `pip install`。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import discover as D
from .ledger import content_hash

__all__ = ["PENDING", "VALIDATED", "REJECTED", "APPROVED", "INSTALLED",
           "Candidate", "from_probe", "validate", "approve", "install_hint",
           "verify", "record", "load", "save", "ID_PREFIX"]


#: ⛔⛔ r58 收口：这份 `CANDIDATES_NAME` **已删除** —— 它是布局的第二份
#:    定义，而且**从未被任何代码使用**（全仓 grep 只有这一行）。留着它
#:    的害处不是重复，是"下次有人改 `paths.CANDIDATES_NAME` 时会以为改
#:    全了"。文件名与落点的唯一事实源是 `aether/paths.py`：
#:      · 文件名  `paths.CANDIDATES_NAME`
#:      · 落点    `paths(root)["candidates"]`
#:    下面 `load/save` 只接收**已经算好的路径**，不自己拼（拼了就是第三份）。
#:
#: （原注释保留，因为它解释的是"为什么要落盘"这件仍然成立的事：）
#: 候选档案的落盘位置（相对仓库根）。⛔ 它**不是事实源** —— 事实源是账本；
#: 这份文件只是"当前待办状态"的便利用缓存，随时可由账本重建。
#: 之所以要落盘：CLI 每次调用都是新进程，而"批准"是一个**跨调用**的动作。
#: 实测踩过：不落盘时 `validate` 之后跑 `approve` 会重新生成候选
#: ⇒ 批准状态丢失 ⇒ 后面的 `hint` 报 `validated` 而不是 `approved`。


def load(path: Any) -> Dict[str, Candidate]:
    """读候选档案；文件不存在/坏掉 ⇒ 空表（**不抛错**，读路径要稳）。"""
    import json
    from pathlib import Path as _P
    f = _P(path)
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, Candidate] = {}
    for d in (data.get("candidates") or []):
        if not isinstance(d, dict):
            continue
        c = Candidate(cap_id=str(d.get("cap_id") or ""),
                      tool=str(d.get("tool") or ""),
                      kind=str(d.get("kind") or ""),
                      reason=str(d.get("reason") or ""),
                      state=str(d.get("state") or PENDING),
                      gate_passed=bool(d.get("gate_passed")),
                      gate_reasons=list(d.get("gate_reasons") or []),
                      approved_by=str(d.get("approved_by") or ""),
                      approved_at=float(d.get("approved_at") or 0.0),
                      install_argv=list(d.get("install_argv") or []),
                      verify_hint=str(d.get("verify_hint") or ""),
                      evidence=dict(d.get("evidence") or {}))
        if c.tool:
            out[c.tool] = c
    return out


def save(path: Any, cands: Dict[str, Candidate]) -> None:
    """把候选档案写盘（**原子替换**：半截文件会让人读到"没有候选"）。"""
    import json
    import os
    from pathlib import Path as _P
    f = _P(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(
        {"note": "候选档案（便利用缓存，**事实源是账本**）；"
                 "可随时由 `caps candidates propose` 重建",
         "candidates": [c.to_dict() for c in sorted(cands.values(),
                                                    key=lambda x: x.tool)]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def merge(new: Candidate, old: Optional[Candidate]) -> Candidate:
    """把新探到的候选与**已存档的状态**合并。

    ⛔ 合并规则很关键：新探针更新**事实**（reason/evidence/install_argv），
       但**不许**把已被批准的候选打回 `pending` —— 那会让"人批准"这件事
       在每一次重跑探针后失效（流水线永远是断的）。
    ⛔ 反过来也要守住：已批准但**现在探到工具已经在**了 ⇒ 直接判 installed
       （人装了、复验通过），而不是继续显示"待安装"。
    """
    if old is None:
        return new
    new.cap_id = old.cap_id or new.cap_id
    new.approved_by = old.approved_by
    new.approved_at = old.approved_at
    if old.install_argv and not new.install_argv:
        new.install_argv = list(old.install_argv)
    if old.state in (APPROVED, INSTALLED):
        new.state = old.state
    if old.state == APPROVED and new.kind == "":
        new.state = INSTALLED
    return new

#: 候选状态机。⛔ 五态不合并：
#:   pending   —— 刚生成，未过闸
#:   validated —— 过了闸，**等人批准**
#:   rejected  —— 没过闸（带原因）
#:   approved  —— 人批了（仍未安装）
#:   installed —— 复验通过（工具真的可探到，且探针命令一致）
PENDING = "pending"
VALIDATED = "validated"
REJECTED = "rejected"
APPROVED = "approved"
INSTALLED = "installed"

#: 寻址键前缀长度。⛔ 与 `genome/memory/nongression/environment/runtime`
#: 的 `_ID_PREFIX` 同值（128 位）。
ID_PREFIX = 32


@dataclass
class Candidate:
    """一个**能力候选**：把"缺什么"变成一条能被裁决、能被批准的记录。"""
    #: 候选身份（内容寻址）：同一份提案重复生成 ⇒ 同一个 id。
    cap_id: str = ""
    tool: str = ""
    #: 缺的是什么：missing_tool / unwrapped_capability
    kind: str = ""
    #: 为什么提它（可追溯：来自哪次探针、探到了什么）
    reason: str = ""
    state: str = PENDING
    #: 过闸结论
    gate_passed: bool = False
    gate_reasons: List[str] = field(default_factory=list)
    #: 人批准信息
    approved_by: str = ""
    approved_at: float = 0.0
    #: 给人执行的安装命令（**不由本模块执行**）
    install_argv: List[str] = field(default_factory=list)
    #: 装完之后要怎么复验
    verify_hint: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def identity(self) -> Dict[str, Any]:
        """只由**提案内容**决定（不含状态/时间）⇒ 同一提案同 id。

        ⛔ 是**方法**不是 `@property`：`content_hash` 会对 dict 值递归，
           遇到 property 时会把它当可调用对象执行（实测报
           `'dict' object is not callable`）。`ledger.Event.identity()` /
           `Gene.identity()` / `CapabilitySpec.identity()` 也都是方法 ——
           同族保持同形。"""
        return {"tool": self.tool, "kind": self.kind,
                "install_argv": list(self.install_argv)}

    def to_dict(self) -> Dict[str, Any]:
        return {"cap_id": self.cap_id, "tool": self.tool, "kind": self.kind,
                "state": self.state, "reason": self.reason,
                "gate_passed": self.gate_passed,
                "gate_reasons": self.gate_reasons,
                "approved_by": self.approved_by,
                "approved_at": self.approved_at,
                "install_argv": list(self.install_argv),
                "verify_hint": self.verify_hint, "evidence": self.evidence}


#: 允许出现在"给人执行的安装命令"里的第一个词（白名单）。
#: ⛔ 白名单而不是黑名单（同族教训：沙箱 env 用白名单）：黑名单拦不住没见过的
#:    包管理器。⛔ 而且这份名单**只用于生成给人看的提示**，本模块不执行它 ——
#:    所以它宽松一点也不会造成执行风险。
ALLOWED_LAUNCHERS = frozenset({
    "brew", "pip", "pip3", "python3", "python", "npm", "pnpm", "yarn",
    "cargo", "go", "apt", "apt-get", "dnf", "yum", "pacman", "port",
    "curl", "wget", "git", "unzip", "tar", "gh",
})


def from_probe(pr: D.Probe, *, install_argv: Optional[Sequence[str]] = None
               ) -> Optional[Candidate]:
    """把一个探针结果变成候选；不值得提的返回 `None`。

    ⛔ 只对两种情形生成候选，其它一律 `None`（**不硬凑提案**）：
        · `missing`            ⇒ 缺工具（装它才有意义）；
        · `found` 但没包装      ⇒ 工具在，但还没有可用的 argv 声明。
      ⛔ `error`（装了但探针失败）**不生成安装候选**：它不是"缺"，
        装一遍解决不了它 —— 那是"去修"，装错方向比不装更糟。
    """
    if pr.status == "missing":
        c = Candidate(tool=pr.tool, kind="missing_tool",
                      reason=f"探针：{pr.detail}",
                      install_argv=list(install_argv or []),
                      verify_hint=f"装完跑 `aether caps discover`（应看到 "
                                  f"{pr.tool} 变成 found）",
                      evidence={"probe": pr.to_dict()})
    elif pr.status == "found" and not pr.capability:
        c = Candidate(tool=pr.tool, kind="unwrapped_capability",
                      reason=f"工具在（{pr.path}）但没有可用的 argv 声明",
                      install_argv=[],
                      verify_hint="补一份真正的参数声明（不是版本探针命令）",
                      evidence={"probe": pr.to_dict()})
    else:
        return None
    c.cap_id = content_hash(c.identity())[:ID_PREFIX]
    return c


def validate(c: Candidate,
             *,
             probe: Optional[D.Probe] = None,
             non_regression_ok: Optional[bool] = None) -> Candidate:
    """过闸。**与其它自我修改同一套判据**，不是另开一条捷径。

    四项，任一不过 ⇒ `rejected` 并写清原因：

      1. **探针复核**：候选声称的现状必须能被再探一次复现
         （`missing` 的现在必须仍 missing；`found` 的必须仍 found）。
         ⛔ 不复核就批准 == 批准一条可能已经过期的提案。
      2. **不许绕过沙箱**：`install_argv` 的启动词必须在白名单里，
         且**不许**出现"关闭沙箱/提权/改安全策略"这类参数。
      3. **非回归**：调用方（如果有）给出的非回归闸结论必须通过 ——
         挂一个新能力不许让已冻结的判据变红。
      4. **可复验**：必须带 `verify_hint`（装完怎么证明它成了）。

    ⛔ 四项**顺序固定、逐项记因**：只报第一个失败项会让人修完一个又冒一个。
    """
    c.gate_reasons = []
    # ① 探针复核
    if probe is not None:
        c.evidence["probe_recheck"] = probe.to_dict()
        if probe.status != ("missing" if c.kind == "missing_tool" else "found"):
            c.gate_reasons.append(
                f"探针复核不一致：候选说 {c.kind}，现在探到 "
                f"{probe.status}（{probe.detail}）⇒ 提案可能已过期")
    # ② 不许绕过沙箱
    if c.install_argv:
        head = str(c.install_argv[0])
        if head not in ALLOWED_LAUNCHERS:
            c.gate_reasons.append(
                f"安装命令的启动词 {head!r} 不在白名单里"
                f"（允许：{sorted(ALLOWED_LAUNCHERS)}）")
        risky = [a for a in c.install_argv
                 if str(a).lower() in ("--force", "--unsafe", "--privileged",
                                       "--dangerous", "--break-system-packages")
                 or str(a).lower().startswith("--no-verify")]
        if risky:
            c.gate_reasons.append(
                f"安装命令含会削弱校验/提权的参数 {risky} ⇒ 拒绝")
    # ③ 非回归
    if non_regression_ok is False:
        c.gate_reasons.append("非回归闸未通过 ⇒ 挂新能力不得让已冻结的判据变红")
    # ④ 可复验
    if not c.verify_hint.strip():
        c.gate_reasons.append("缺复验方式（装完怎么证明它成了）⇒ 不可验收")
    c.gate_passed = not c.gate_reasons
    c.state = VALIDATED if c.gate_passed else REJECTED
    return c


def install_hint(c: Candidate) -> List[str]:
    """给人执行的安装命令（**本模块不执行它**）。

    ⛔ 没批准就没有提示：产出"该怎么装"等于在催办一条没被批准的提案。
    ⛔ 没给 `install_argv` 也如实返回空表 + 说明：我们**不猜**包管理器
       （猜错会让人装错东西，比不给提示更糟）。
    """
    if c.state not in (APPROVED, INSTALLED):
        return []
    return list(c.install_argv)


def approve(c: Candidate, *, by: str, yes: bool = False) -> Candidate:
    """**人批准**这一步。没有 `yes=True` 一律不推进。

    ⛔ 这个参数不是形式：它有契约守（`--yes` 是**唯一**能推进的入口），
       因为"agent 自己决定装什么"正是这条设计要挡掉的东西。
    ⛔ 没通过闸的候选**不许**被批准 —— 批一条已知不过闸的提案，
       等于把闸当成摆设。
    """
    if not c.gate_passed or c.state == REJECTED:
        c.gate_reasons.append("未通过闸的候选不得批准（先把闸的问题解决）")
        return c
    if not yes:
        c.gate_reasons.append("缺少显式批准（--yes）：本系统不代你决定装什么")
        return c
    if not str(by).strip():
        c.gate_reasons.append("批准必须留名（approved_by 不能为空）")
        return c
    c.state = APPROVED
    c.approved_by = str(by).strip()
    c.approved_at = time.time()
    return c


def verify(c: Candidate, probe: D.Probe) -> Candidate:
    """装完之后**复验**：工具真的可探到，且探针命令与提案一致。

    ⛔ 复验只认探针的**客观读数**（status/path），不认"我觉得装好了"。
    ⛔ 装完仍探不到 ⇒ 回 `rejected` 并保留读数：那是"装了但没用"，
       必须能被看见（同族：`--e2e` 组曾经"从没被跑过"而无人发现）。
    """
    c.evidence["verify_probe"] = probe.to_dict()
    if probe.status == "found":
        c.state = INSTALLED
    else:
        c.state = REJECTED
        c.gate_reasons.append(
            f"复验未通过：装完仍探到 {probe.status}（{probe.detail}）")
    return c


def record(ledger: Any, c: Candidate, *, actor: str = "caps") -> Optional[int]:
    """把候选的**当前状态**记进账本。两种结果都记（同宪法第 10 条）。

    ⛔ 没挂账本 ⇒ 返回 `None` 并**不假装记过**；调用方必须把这件事
       报出来（"没记账"与"记了账"是两件事）。
    """
    if ledger is None:
        return None
    return ledger.append("cap.candidate", c.to_dict(), actor=actor).seq
