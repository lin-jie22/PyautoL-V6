#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`doctor`：把散在三个子命令里的诊断聚成**一条命令**。

═══ 为什么这是**缺的功能** ═══

V6 的诊断能力其实不少 —— `status`、`verify-chain`、`rebuild --check`、
`memory ...`。但它们分散在四个入口，且：

  · **没有一条命令能回答"这个系统现在健康吗"**（要人自己记得跑哪几条、
    并按什么顺序解读）；
  · 唯一的"自检"入口在 **TS 监督器**（`supervisor/src/selfcheck.ts`），
    Python 侧没有等价物 ⇒ 只跑 Python 的人（以及 CI）拿不到一次成型的结论；
  · 更关键的是：**"没查过"和"查过没问题"在输出里长得一样** —— 某个
    检查被跳过时，如果没有显式标记，读报告的人会当成通过。

═══ 设计取舍 ═══

1. ⛔⛔ **每项检查三态，不是二态**：`ok` / `fail` / `skipped`。
   `skipped` 必须带**原因**（比如"没有投影库"），且**不计入通过**。
   这是本项目最贵的一条纪律（"不可判别 ≠ 通过"），在这里最容易破：
   聚合器天然倾向于"没有坏消息就是好消息"。

2. ⛔ **不修任何东西**。doctor 只报告。修的动作各自有命令
   （`rebuild` / `backup` / `memory reindex`）—— 混在一起会让"体检"
   变成"未告知就改数据"，而体检恰恰是在数据可疑时跑的。
   每项失败都带 `action`：告诉人该跑哪条命令。

3. ⛔ **退出码诚实**：有 fail ⇒ 1；只有 skipped ⇒ 0（没坏消息），
   但报告里必须看得见 skipped（否则 CI 会以为全查过了）。

4. ⛔ 检查项之间**互不影响**：一项抛异常 ⇒ 记为 fail 并继续下一项
   （体检中途崩了等于没体检，而且崩的那项恰恰最可疑）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .paths import data_dir, paths

__all__ = ["OK", "FAIL", "SKIPPED", "Check", "run", "CHECKS"]


OK = "ok"
FAIL = "fail"
SKIPPED = "skipped"


class Check:
    """一项检查的结果。`status` 三态，`action` 是给人的下一步。"""

    def __init__(self, name: str, status: str, detail: str = "",
                 action: str = "", skipped_reason: str = ""):
        self.name = name
        self.status = status
        self.detail = detail
        self.action = action
        self.skipped_reason = skipped_reason

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "status": self.status,
                "detail": self.detail, "action": self.action,
                "skipped_reason": self.skipped_reason}

    def __repr__(self) -> str:      # pragma: no cover
        return f"<Check {self.name} {self.status}>"


def _paths(root: Path) -> Dict[str, Path]:
    """⛔ 与 cli / serve / backup **同一份布局**（r36）。

    此前这里单独手写了一遍 `root / "data" / ...`（3 个键与 cli 的 `_paths`
    完全重复）⇒ 布局改一次要改两处，漏一处就是"体检说没问题、而它查的是
    另一个文件"。现在直接从 `paths` 取，键名保持不变（调用方只看键）。
    """
    return paths(root)


# ── 各项检查（顺序即报告顺序：先事实源，再派生量）─────────────────
def _check_ledger(root: Path, ctx: Dict[str, Any]) -> Check:
    from .ledger import Ledger
    p = _paths(root)["ledger"]
    if not p.exists():
        return Check("ledger.chain", SKIPPED, "账本文件不存在",
                     "先跑一次 evolve 产生事件", "no_ledger_file")
    led = Ledger(p)
    ok, seq, why = led.verify_chain()
    n = len(led.events())
    return (Check("ledger.chain", OK, f"{n} 条事件，链完整")
            if ok else
            Check("ledger.chain", FAIL,
                  f"断裂 @seq={seq}: {why}",
                  "事实源已不可信：从备份恢复（aether restore）"))


def _check_projection(root: Path, ctx: Dict[str, Any]) -> Check:
    from .ledger import Ledger
    from .store import Store
    ps = _paths(root)
    if not ps["db"].exists():
        return Check("projection.consistency", SKIPPED, "没有投影库",
                     "跑 aether rebuild 从账本重建", "no_db")
    led = Ledger(ps["ledger"])
    evs = led.events() if ps["ledger"].exists() else []
    s = Store(ps["db"])
    try:
        rep = s.consistency_report(evs)
    finally:
        s.close()
    return (Check("projection.consistency", OK, "投影与账本一致")
            if rep.get("ok") else
            Check("projection.consistency", FAIL,
                  f"不一致：{rep.get('reason') or rep}",
                  "跑 aether rebuild（会先备份旧库）"))


def _check_memory(root: Path, ctx: Dict[str, Any]) -> Check:
    from .memory import MemoryOS
    p = _paths(root)["memory"]
    if not p.exists():
        return Check("memory.integrity", SKIPPED, "没有记忆快照",
                     "跑一次 evolve 会自动写", "no_memory_file")
    try:
        #: ⛔ 走**与生产完全相同**的装载路径（`MemoryOS.load`，不是直接
        #:    构造）：拒载发生在 load 侧（写入侧放行 ≠ 加载侧放行），
        #:    直接构造一个空 MemoryOS 会一份数据都不碰 ⇒ 检查恒绿。
        from .embeddings import HashingEmbedder
        _mem, rep = MemoryOS.load(p, HashingEmbedder(dim=256),
                                  min_content_len=60)
    except Exception as e:      # noqa: BLE001 —— 体检不得因一项崩掉
        return Check("memory.integrity", FAIL, f"{type(e).__name__}: {e}",
                     "检查记忆文件格式 / 嵌入器口径")
    bad = int(rep.get("rejected_embedder_mismatch", 0)) + \
        int(rep.get("rejected_dim_mismatch", 0)) + \
        int(rep.get("rejected_no_embedding", 0))
    return (Check("memory.integrity", OK,
                  f"note={rep.get('notes', '?')} 条，拒载 {bad} 条")
            if bad == 0 else
            Check("memory.integrity", FAIL, f"{bad} 条被拒载：{rep}",
                  "跑 aether memory reindex 重算嵌入"))


def _check_backup(root: Path, ctx: Dict[str, Any]) -> Check:
    """唯一事实源有没有退路。⛔ 这是**最容易被忽略**的一项：
    备份平时用不上，等要用时才发现从来没做过。"""
    from . import backup as B
    bdir = paths(root)["backups"]
    if not bdir.exists():
        return Check("backup.present", FAIL, "没有任何备份（唯一事实源无退路）",
                     "跑 aether backup --to <目录>", "no_backup_dir")
    cands = sorted(p for p in bdir.iterdir()
                   if p.is_dir() and (p / "manifest.json").exists())
    if not cands:
        return Check("backup.present", FAIL, f"{bdir} 里没有有效备份",
                     "跑 aether backup --to <目录>", "no_valid_backup")
    latest = cands[-1]
    ok, problems, m = B.verify(latest)
    age_h = (time.time() - latest.stat().st_mtime) / 3600.0
    if not ok:
        return Check("backup.present", FAIL,
                     f"最新备份 {latest.name} 校验不通过：{problems[:2]}",
                     "重新备份（这份不可信）")
    chain = "链完整" if (m and m.chain_ok) else "链未验/断"
    return Check("backup.present", OK,
                 f"{len(cands)} 份，最新 {latest.name}（{age_h:.1f} 小时前，{chain}）")


def _check_caps(root: Path, ctx: Dict[str, Any]) -> Check:
    """能力清单能不能装载（不装载 ⇒ 所有外部能力调用都是 503）。"""
    from .interop import mount_capabilities
    src = ctx.get("caps") or ""
    if not src:
        return Check("caps.loadable", SKIPPED, "没指定能力清单",
                     "需要外部能力时加 --caps <manifest.json>",
                     "no_manifest")
    try:
        gw, rep = mount_capabilities(src)
    except Exception as e:      # noqa: BLE001
        return Check("caps.loadable", FAIL, f"{type(e).__name__}: {e}",
                     "检查清单 JSON 与 impl 形态")
    skipped = len(rep.skipped)
    return (Check("caps.loadable", OK, f"装载 {len(rep.loaded)} 个能力")
            if skipped == 0 else
            Check("caps.loadable", FAIL,
                  f"装载 {len(rep.loaded)} 个、跳过 {skipped} 个"
                  f"（跳过的会被调用方当成不存在）",
                  "看 skip 原因修正清单"))


def _check_writable(root: Path, ctx: Dict[str, Any]) -> Check:
    #: ⛔ 与其余检查项同源（`paths.data_dir`）：手写 `root / "data"` 就是
    #:    "体检说 data/ 可写、而它测的其实是另一个目录"的来源。
    d = data_dir(root)
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return Check("data.writable", FAIL, f"{type(e).__name__}: {e}",
                     "检查目录权限 / --root 是否指错")
    return Check("data.writable", OK, f"{d} 可写")


def _check_provenance(root: Path, ctx: Dict[str, Any]) -> Check:
    """账本的**出处**是否可判别、是否兼容当前代码。

    ⛔ 为什么这是体检项而不是"锦上添花"：账本是唯一事实源，而它的格式
       版本此前**只参与算 hash、从不落盘** ⇒ 格式一旦漂移，读者只会得到
       一句误导性的"链断了"（看起来像被篡改），而"这份数据是谁写的、
       能不能拿来跟现在的读数比"根本无从判断。

    三态处置（与 `provenance.compat_report` 同源）：
      · `ok`           —— 有声明且兼容；
      · `unknown`      —— 没声明（升级前存量）⇒ **ok 但写明"不可判别"**
                          （"不可判别 ≠ 有罪"：升级前的账本不该被判非法）；
      · `incompatible` —— 有声明但不兼容 ⇒ **fail**，并给出迁移/恢复动作。
    """
    from .ledger import Ledger
    p = _paths(root)["ledger"]
    if not p.exists():
        return Check("ledger.provenance", SKIPPED, "账本文件不存在",
                     "先跑一次 evolve 产生事件", "no_ledger_file")
    rep = Ledger(p).source_compat()
    rec = rep["recorded"]
    if rep["state"] == "incompatible":
        return Check("ledger.provenance", FAIL,
                     "；".join(rep["reasons"]),
                     "这份账本不能与当前代码的读数混比：先按 "
                     "docs/ARCHITECTURE 的迁移说明升级数据，或从备份恢复")
    if rep["state"] == "unknown":
        return Check("ledger.provenance", OK,
                     "账本没有出处声明（升级前存量）⇒ 出处不可判别；"
                     "本次读数不得与声明了版本的账本跨版本比较")
    return Check("ledger.provenance", OK,
                 f"由 {rec['code_version']} 写出（格式版本 "
                 f"{rec['format_version']}）⇒ 与当前代码兼容")


CHECKS: List[Callable[[Path, Dict[str, Any]], Check]] = [
    _check_ledger, _check_provenance, _check_projection, _check_memory,
    _check_backup, _check_caps, _check_writable,
]


def run(root: str | Path, caps: str = "",
        checks: Optional[List[Callable[[Path, Dict[str, Any]], Check]]] = None
        ) -> Dict[str, Any]:
    """跑一遍体检。返回报告（不修改任何东西）。"""
    root_p = Path(root)
    ctx = {"caps": caps}
    results: List[Check] = []
    for fn in (checks if checks is not None else CHECKS):
        try:
            results.append(fn(root_p, ctx))
        except Exception as e:  # noqa: BLE001 —— 一项崩了继续下一项
            results.append(Check(getattr(fn, "__name__", "check"), FAIL,
                                 f"检查自身抛异常：{type(e).__name__}: {e}",
                                 "这是 doctor 的 bug 或环境缺失，请报告"))
    n_fail = sum(1 for r in results if r.status == FAIL)
    n_skip = sum(1 for r in results if r.status == SKIPPED)
    return {"root": str(root_p),
            "checks": [r.to_dict() for r in results],
            "n_checks": len(results), "n_fail": n_fail, "n_skipped": n_skip,
            "healthy": n_fail == 0,
            #: ⛔ skipped **不等于**通过：这条提示必须出现在报告里，
            #:    否则"没坏消息"会被读成"全查过了"。
            "note": ("" if n_skip == 0 else
                     f"{n_skip} 项被跳过（未查 ≠ 通过）："
                     + "、".join(r.name for r in results
                                 if r.status == SKIPPED)),
            "exit_code": 1 if n_fail else 0}
