"""机器自审（r60 F）：把「人点头」换成「可复现的机器判据 + 自动回滚」。

═══ 它替代的是什么 ═══

清理链此前的形态是：提案 → 过闸 → **人** `--yes` → 人自己动手改源码。
最后一步不自动，理由写在 `srcmut.apply_edits` 的文档里：「自动改自己的
源码与『判据必须离线可回归』冲突，且删错了不可逆」。

那条理由的前半句是**可以反驳**的，后半句是**必须保留**的：

  · 前半句：判据只要**不来自被测对象**就不冲突。`srcmut.probe` 用的正是
    冻结契约（删前/删后对拍），判决权在外部 ⇒ 「机器自审」成立。
  · 后半句：不可逆 ⇒ 必须先快照、且落盘后**再复验一次**，失败原子回滚。

⇒ 本模块只做两件事：**决定谁有资格点头**（用对拍替代人）、**保证点错了
   能退回去**（快照 + 复验 + 回滚）。它**不新增任何判据** —— 多一把不受
   监督的尺子，正是这个项目一直在防的东西。

═══ 三态（不是二态）═══

  `ok`         对拍通过且落盘成功（复验也过）
  `rejected`   对拍发现差异，或落盘后复验失败（已回滚）
  `unverified` 跑不起来 / 没有契约守着 / 提案没有结构化 edits

⛔ `unverified` 绝不许并入 `ok`：把「没裁决」当「通过」是这个项目最贵的
   那类事故（第 27 条：三态伴生，unknown 绝不并入）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from . import srcmut as S
from .paths import paths

#: 落盘成功
OK = "ok"
#: 对拍发现差异 / 复验失败（已回滚）
REJECTED = "rejected"
#: 没有裁决（跑不起来 / 没契约守着 / 没有可执行编辑）
UNVERIFIED = "unverified"

#: 机器自审时写进 `approved_by` 的值。⛔ 刻意**不是**人名：
#: 事后要能一眼分开「人批的」与「机器对拍过的」，否则审计时两种责任同形。
AUTO_BY = "auto"

__all__ = ["OK", "REJECTED", "UNVERIFIED", "AUTO_BY", "AutoVerdict",
           "plan_edits", "auto_apply"]


@dataclass
class AutoVerdict:
    """一次机器自审的完整结论。"""
    state: str
    reasons: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    #: 落盘前备份的相对路径清单（回滚依据）
    backups: List[str] = field(default_factory=list)
    snapshot_dir: str = ""
    #: 是否已经回滚过（True 表示源码已还原到落盘前）
    rolled_back: bool = False

    @property
    def applied(self) -> bool:
        return self.state == OK

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "reasons": list(self.reasons),
                "evidence": dict(self.evidence),
                "backups": list(self.backups),
                "snapshot_dir": self.snapshot_dir,
                "rolled_back": self.rolled_back}


def plan_edits(prop: Any) -> List[S.Edit]:
    """从提案的 `edits` 恢复成可执行的 `Edit`。

    ⛔ **不从 `patch` 文本猜**：`patch` 是给人读的说明（"删第 714–749 行"），
       解析它等于把"人读的散文"当机器指令 —— 一旦措辞变化就静默失效，
       而失效的表现是"什么都没删"（看起来像成功）。
    ⛔ 单条解析失败就跳过并留痕（调用方据 `len` 判"有没有可执行的"）。
    """
    out: List[S.Edit] = []
    for d in (getattr(prop, "edits", None) or ()):
        try:
            out.append(S.Edit.from_dict(d))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def auto_apply(prop: Any, root: Union[str, Path], *,
               runner: Optional[Any] = None,
               max_contracts: int = 12,
               timeout_s: float = 900.0,
               snapshot_root: Optional[Union[str, Path]] = None,
               verify_after: bool = True,
               apply_to_disk: bool = True) -> AutoVerdict:
    """对一条清理提案做**机器自审并落盘**。返回三态结论。

    `runner` 不传 ⇒ 按 import 关系选契约并现场造一个（`srcmut.contract_runner`）。
    ⛔ 传 `runner` 只用于测试；生产一律现造，避免"用上次的契约清单"。

    `apply_to_disk=False` ⇒ **只对拍不落盘**（演练）。此时结论仍可能是 `ok`
    （意思是"这段删除被冻结契约证明是等价的"），但 `evidence["dry_run"]`
    为真、`backups` 为空 —— 调用方不许把它当成"已经删了"。

    ⛔ 落盘后**必须**复验（`verify_after=True`）：副本上通过不等于真根上通过
       （副本可能少了未跟踪文件）。复验失败 ⇒ 立即回滚，并把
       `rolled_back` 写进结论 —— 「回滚了」与「没写」必须能分辨。
    """
    root = Path(root)
    edits = plan_edits(prop)
    if not edits:
        return AutoVerdict(UNVERIFIED, [
            "提案没有结构化 edits ⇒ 只有人读补丁，机器无从执行"
            "（要自动落盘请让提案器带 edits）"])

    files = sorted({str(e.file) for e in edits})
    contracts, why = S.select_contracts(root, files, max_contracts=max_contracts)
    if not contracts:
        #: ⛔ 「没人守着」与「超预算」修法完全不同，原因原样透出。
        return AutoVerdict(UNVERIFIED, [
            f"没有冻结契约守着 {files} ⇒ {why}"],
            evidence={"files": files, "select_reason": why})

    if runner is None:
        runner = S.contract_runner(contracts, timeout_s=timeout_s)

    trial = S.probe(root, edits, runner=runner)
    ev: Dict[str, Any] = {"files": files, "contracts": contracts,
                          "select_reason": why, "trial": trial}

    if trial.get("unverified"):
        return AutoVerdict(UNVERIFIED,
                           [str(trial.get("reason") or "对拍未完成")],
                           evidence=ev)
    if not trial.get("ok"):
        return AutoVerdict(REJECTED,
                           [str(trial.get("reason") or "对拍发现差异")],
                           evidence=ev)

    snap = (Path(snapshot_root) if snapshot_root is not None
            else paths(root)["autonomy"] / str(int(time.time())))
    if not apply_to_disk:
        ev["dry_run"] = True
        return AutoVerdict(OK, [str(trial.get("reason") or "对拍通过")
                                + "｜演练：**源码未改动**"],
                           evidence=ev)
    ok, why2, n, backups = S.apply_edits_privileged(
        root, edits, snapshot_dir=snap)
    ev.update({"applied_lines": n, "backups": backups,
               "snapshot_dir": str(snap)})
    if not ok:
        #: 应用失败时文件**还没动**（`_apply_now` 先全量校验）⇒ 不是回滚。
        return AutoVerdict(REJECTED, [f"落盘失败：{why2}"],
                           evidence=ev, backups=backups,
                           snapshot_dir=str(snap))

    if verify_after:
        try:
            after = dict(runner(root) or {})
        except Exception as ex:                            # noqa: BLE001
            after = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
        ev["verify_after"] = after
        if after.get("error") or not after.get("ok"):
            rb, rwhy = S.rollback(root, backups, snap)
            ev["rollback_reason"] = rwhy
            return AutoVerdict(
                REJECTED,
                ["落盘后复验未过 ⇒ 触发回滚"
                 + ("" if rb else f"（⛔ 回滚失败：{rwhy}）")],
                evidence=ev, backups=backups, snapshot_dir=str(snap),
                rolled_back=rb)

    return AutoVerdict(OK, [str(trial.get("reason") or "对拍通过")],
                       evidence=ev, backups=backups, snapshot_dir=str(snap))
