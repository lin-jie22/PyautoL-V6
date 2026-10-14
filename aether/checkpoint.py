#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""断点续跑（checkpoint / resume）：进化循环必须能从「上次停在哪」接着跑。

═══ 为什么这是**缺的功能**而不是"锦上添花" ═══

进化循环是 V6 唯一的写路径：一轮里要跑发展集、老化探针、held-out、
记忆巩固、归因，还要往账本 append 一串事件。`--rounds 20` 跑 20 轮，
第 17 轮 Ctrl-C（或宿主 OOM、或 LLM 超时）⇒ **前 16 轮全部作废**，
下一次只能从第 1 轮重跑。⛔ 更糟的是"重跑"并不是"重来一遍"：

  · 账本已经写了 16 轮的事件 ⇒ 重跑会把它们**再写一遍**
    （账本 append-only，没有幂等键）⇒ 读数翻倍、因果链里出现两个
    "第 3 轮"；
  · `_run_seq` 的锚来自账本 seq ⇒ 锚变大 ⇒ run_id 与上次不同
    ⇒ 投影对账时"这两次运行"看着像两次独立实验，其实是一次被劈成两半。

也就是说：**没有 checkpoint，中断的代价不只是浪费时间，而是产出一份
自己跟自己对不上的账本**。这正是 V6 反复惩罚的形态。

═══ 设计取舍 ═══

1. ⛔⛔ **checkpoint 不是事实源，是派生量**。账本才是唯一事实源。
   checkpoint 只存"恢复到哪儿"的**最小必要信息**，不存账本内容、
   不存 run 明细（那些都能从账本/store 重建）。
   ⇒ 代价：checkpoint 丢了大不了从头跑；收益：不可能出现"checkpoint
   与账本不一致却没人发现"（下述第 2 条会校验它们）。

2. ⛔⛔ **resume 必须校验，且要说清是**哪一种**对不上**。
   三条独立判据，缺一条就会出现"接着跑但跑的不是同一个实验"：

     · `caliber_fp` 不同  ⇒ 换了尺子（seed / 口径 / 任务集变了）
       ⇒ 前后两半不是同一个实验，接起来毫无意义；
     · `ledger_seq` 比 checkpoint 记录的**小** ⇒ 账本被回退/换了库
       ⇒ checkpoint 指向的事件不在了；
     · `cfg_fp` 不同      ⇒ 进化配置变了（比如 stop_after_idle_rounds）
       ⇒ 终止条件不同，"还剩几轮"这个量本身就不成立。

   ⛔ 失败一律 **fail-closed**：拒绝恢复并给出可行动的原因。
   静默"恢复成功"比拒绝危险得多 —— 前者会产出一份看起来完整、
   实际是两次实验缝合出来的账本。

3. **原子写**：`os.replace` 落盘。中断可能发生在**写 checkpoint 的半途**
   （这正是 resume 最需要的时刻）⇒ 半截文件比没有文件更糟：
   读到半截 JSON 若被当成"损坏 ⇒ 从头跑"还算安全，被当成"字段缺失
   ⇒ 用默认值"就会静默跑错。本模块对损坏一律抛 `CheckpointError`，
   **不填默认值**。

4. **只在轮末写**。轮中写 ⇒ checkpoint 与"一轮写到一半的账本"配对
   ⇒ 恢复后这一轮被跑第二遍（与 `RunBudget` 只在轮末查挂钟同源）。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

#: ⛔ 版本号**只在这里定义**（与 `_version.py` 的 CODE_VERSION 是两回事：
#:    那个是代码版本，这个是**磁盘格式**版本 —— 格式变了必须能被发现，
#:    否则旧 checkpoint 会被新代码按新字段解读 ⇒ 静默跑错）。
CP_FORMAT = 1

__all__ = [
    "CP_FORMAT",
    "CheckpointError",
    "Checkpoint",
    "fingerprint",
    "write",
    "read",
    "verify",
    "verify_or_raise",
]


class CheckpointError(Exception):
    """checkpoint 不可恢复。**必须**带人类可读的原因（要打印给调用方）。"""


def fingerprint(obj: Any) -> str:
    """对任意 JSON-able 对象算稳定指纹（键序无关）。

    ⛔ 键序必须无关：dict 的插入顺序在不同代码路径下会变（比如 config
    里某个字段这次先塞、下次后塞）⇒ 内容相同却算出不同指纹 ⇒ resume
    被无故拒绝（假红），而"无故拒绝"看久了就会被绕过。
    """
    try:
        raw = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=str)
    except (TypeError, ValueError) as e:  # 不可序列化 ⇒ 指纹无从谈起
        raise CheckpointError(f"无法计算指纹（对象不可序列化）：{e}") from e
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Checkpoint:
    """一次进化运行的**可恢复点**。全部字段都是"恢复所必需"的。"""
    #: 已完成到第几轮（1-based，0 = 一轮都没跑完）。
    round_no: int
    #: 本次运行**计划**跑几轮（用于算"还剩几轮"）。
    rounds_total: int
    #: 当前 harness 的完整 payload（不是引用）。⛔ 存 payload 不存 id：
    #:   id 要靠 store 查，而 store 是**投影**（可从账本重建，也可能没跟上）
    #:   ⇒ 恢复时查不到 ⇒ 又变成从头跑。payload 自足，代价只是文件大一点。
    harness: Dict[str, Any]
    harness_id: str = ""
    #: 写 checkpoint 时账本的 seq。resume 时用它判"账本有没有被回退"。
    ledger_seq: int = 0
    #: run_id 锚（跨运行不重置，见 `EvolutionLoop._run_seq`）。
    run_seq: int = 0
    #: 口径指纹（seed / 任务集 / caliber）。不同 ⇒ 不是同一个实验。
    caliber_fp: str = ""
    #: 进化配置指纹。不同 ⇒ "还剩几轮"这个量不成立。
    cfg_fp: str = ""
    #: 磁盘格式版本（读时校验，不兼容直接拒绝）。
    format: int = CP_FORMAT
    saved_at: float = field(default_factory=time.time)
    #: 已完成的轮次摘要（**只用于展示与对账**，不参与恢复判据）。
    done_rounds: Tuple[Dict[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": self.format,
            "round_no": self.round_no,
            "rounds_total": self.rounds_total,
            "harness": self.harness,
            "harness_id": self.harness_id,
            "ledger_seq": self.ledger_seq,
            "run_seq": self.run_seq,
            "caliber_fp": self.caliber_fp,
            "cfg_fp": self.cfg_fp,
            "saved_at": self.saved_at,
            "done_rounds": list(self.done_rounds),
        }

    @property
    def rounds_left(self) -> int:
        """还剩几轮（不会为负）。"""
        return max(0, self.rounds_total - self.round_no)

    @property
    def complete(self) -> bool:
        return self.rounds_left <= 0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Checkpoint":
        """⛔ 缺字段一律抛错，**不填默认值**。

        半截文件/旧格式最容易的表现就是"字段缺失"。默认值会把
        "ledger_seq 没了"变成 `ledger_seq=0` ⇒ 随后任何 seq ≥ 0 的账本
        都能通过校验 ⇒ **校验形同虚设**（与 V5 "假仪表"同族）。
        """
        if not isinstance(d, dict):
            raise CheckpointError(f"checkpoint 必须是对象，实得 {type(d).__name__}")
        missing = [k for k in ("round_no", "rounds_total", "harness")
                   if k not in d]
        if missing:
            raise CheckpointError(f"checkpoint 缺字段 {missing} ⇒ 拒绝恢复"
                                  f"（缺字段不填默认值，否则校验形同虚设）")
        if not isinstance(d["harness"], dict):
            raise CheckpointError(
                f"checkpoint.harness 必须是对象，实得 {type(d['harness']).__name__}")
        return cls(
            round_no=int(d["round_no"]),
            rounds_total=int(d["rounds_total"]),
            harness=dict(d["harness"]),
            harness_id=str(d.get("harness_id", "")),
            ledger_seq=int(d.get("ledger_seq", 0)),
            run_seq=int(d.get("run_seq", 0)),
            caliber_fp=str(d.get("caliber_fp", "")),
            cfg_fp=str(d.get("cfg_fp", "")),
            format=int(d.get("format", 0)),
            saved_at=float(d.get("saved_at", 0.0)),
            done_rounds=tuple(dict(x) for x in (d.get("done_rounds") or ())),
        )


def write(path: str, cp: Checkpoint) -> str:
    """原子写 checkpoint。返回落盘路径。

    ⛔ 必须原子：`os.replace` 是 rename，同分区内是原子的。直接
    `open(path,"w")` 写到一半被中断 ⇒ 留下半截 JSON ⇒ 而半截文件恰好
    最可能出现在"正需要 resume"的时候（宿主崩了）。
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    payload = json.dumps(cp.to_dict(), ensure_ascii=False, sort_keys=True,
                         indent=2)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".cp_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        #: 写失败 ⇒ 绝不留下临时文件（留下会被下次当成"有 checkpoint"）
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read(path: str) -> Checkpoint:
    """读 checkpoint。任何异常都转成 `CheckpointError`（带原因）。

    ⛔ 不吞异常：调用方需要知道"为什么不能恢复"，而不是拿到一个
    字段全空的 Checkpoint 继续跑（那是把"不可判别"当成"没问题"）。
    """
    if not os.path.exists(path):
        raise CheckpointError(f"checkpoint 不存在：{path}（先跑一次不带 "
                              f"--resume 的 evolve 才会生成）")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise CheckpointError(f"checkpoint 读失败：{e}") from e
    if not raw.strip():
        raise CheckpointError(f"checkpoint 是空文件：{path}（写入被中断）")
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise CheckpointError(
            f"checkpoint 不是合法 JSON（{e}）⇒ 写入被中断过，拒绝恢复"
            f"（半截文件不按默认值补全）") from e
    cp = Checkpoint.from_dict(d)
    if cp.format != CP_FORMAT:
        raise CheckpointError(
            f"checkpoint 格式版本 {cp.format} ≠ 当前 {CP_FORMAT} ⇒ 拒绝恢复"
            f"（旧格式请重新跑一次；不猜测旧字段的含义）")
    return cp


def verify(cp: Checkpoint, *, ledger_seq: int, caliber: Any,
           cfg: Any) -> Tuple[bool, str, str]:
    """校验 checkpoint 与**当前运行环境**是否还是同一个实验。

    返回 `(ok, kind, reason)`。`kind` 是**机器可读**的失配类别：

      ``        —— 一致，可以恢复
      `caliber` —— 换了尺子（口径/任务集/seed 变了）
      `cfg`     —— 进化配置变了 ⇒ "还剩几轮"不成立
      `ledger`  —— 账本被回退或换了库

    ⛔ 三类分开：合起来报一句"环境不一致"会让人去改 checkpoint，
    而正确处置分别是"别恢复，重新跑"/"重新跑"/"找对账本"。
    """
    if cp.caliber_fp and fingerprint(caliber) != cp.caliber_fp:
        return (False, "caliber",
                f"口径指纹不同（checkpoint {cp.caliber_fp} vs 当前 "
                f"{fingerprint(caliber)}）⇒ 前后不是同一个实验，接起来无意义。"
                f" 出路：不带 --resume 重跑（尺子换了，旧进度不可继承）")
    if cp.cfg_fp and fingerprint(cfg) != cp.cfg_fp:
        return (False, "cfg",
                f"进化配置指纹不同（checkpoint {cp.cfg_fp} vs 当前 "
                f"{fingerprint(cfg)}）⇒ 「还剩 {cp.rounds_left} 轮」这个量"
                f"本身不成立。 出路：不带 --resume 重跑")
    if ledger_seq < cp.ledger_seq:
        return (False, "ledger",
                f"账本 seq {ledger_seq} < checkpoint 记录的 {cp.ledger_seq}"
                f" ⇒ 账本被回退或换了库。 出路：确认 --root 指向同一个目录")
    return (True, "", "")


def verify_or_raise(cp: Checkpoint, *, ledger_seq: int, caliber: Any,
                    cfg: Any) -> None:
    """`verify` 的 fail-closed 包装：不一致直接抛。"""
    ok, kind, reason = verify(cp, ledger_seq=ledger_seq, caliber=caliber,
                              cfg=cfg)
    if not ok:
        raise CheckpointError(f"[{kind}] {reason}")
