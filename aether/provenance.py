#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出处（provenance）：账本必须能回答"这份数据是谁写的、按哪版格式写的"。

═══ 为什么这是**缺的功能**而不是"锦上添花" ═══

V6 的第一条命题是"事实只有一份 = 账本"。但账本里原先**没有任何出处**：

  · 事件身份只有 `seq / ts / kind / actor / payload / prev_hash`
    （`ledger.Event.identity()`，跨语言 hash 契约的一部分）；
  · `LEDGER_VERSION = 1` **只参与算 hash**，从不落盘写成人类可读的字段；
  · 账本里查不到"这份数据是哪个代码版本写的"。

后果是**两种事故**，而它们的报错都指向错误的方向：

  1. **格式漂移 ⇒ 报"链断了"**。读者按新规则复算 hash，与文件里的旧 hash
     对不上 ⇒ `verify_chain` 报 `hash 不一致（期望 X 实得 Y）`。
     现场看起来像"账本被篡改/损坏"，真相是"代码换了、数据没迁移"。
  2. **跨版本比读数**。`status` 的投影对账、`evolution` 的历史对照、
     监督器的跨轮判据，都会拿"不同代码版本产出的事件"当同一口径比 ——
     而没有任何一处能发现这件事。

⛔ 这两条正是 V6 反复惩罚的形态："把缺失伪装成存在"和"不可比却比了"。
   所以出处必须是**结构**：写一次、读时校验、对不上时**说清是哪一种对不上**。

═══ 设计取舍 ═══

· **不改跨语言 hash 契约**：`format_version` 不放事件顶层，而是作为
  各写入侧 payload 里的一个键（`src_version` / `format_version`）。
  `payload` 本来就参与 hash，所以历史事件不受影响，TS 侧也不需要同步改
  `identity()`。这与 `sid`（会话归属）当年的做法同源。
· **写一次足够**：每个写入侧只在自己**第一条**事件上声明一次（账本
  append-only ⇒ 后续事件属于同一个写入者）。声明太频繁会把账本撑大，
  声明太少则无法归因 —— 关键是这条声明**不可伪造地被读出来**。
· **兼容判据 fail-closed，但只说"不可判别"不说"有罪"**：账本没有出处
  （历史存量）⇒ 判 `unknown` 并**放行**（与"不可判别 ≠ 有罪"同族：
  升级前的事件不该被判成非法）；有出处且不兼容 ⇒ 拒绝并把两个版本都报出来。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ._version import CODE_VERSION, LEDGER_FORMAT

#: ⛔ 版本号的**唯一事实源**在 `_version.py`（见该文件里的依赖方向说明）。
#:    这里只做再导出，方便 `provenance.CODE_VERSION` 这类读法。
__all__ = [
    "CODE_VERSION", "LEDGER_FORMAT",
    "KEY_CODE_VERSION", "KEY_FORMAT",
    "COMPAT_OK", "COMPAT_UNKNOWN", "COMPAT_INCOMPATIBLE",
    "Recorded", "parse_version", "is_compatible",
    "read_from_payload", "stamp", "compat_report", "describe",
]

#: 出处字段名（payload 里的键）。集中定义，避免各写入侧拼字符串。
KEY_CODE_VERSION = "src_version"
KEY_FORMAT = "format_version"

#: 兼容性判定结果。
#:   ok         —— 声明了出处且兼容
#:   unknown    —— 账本里没有出处（升级前存量）⇒ 不可判别，**不拦**
#:   incompatible —— 声明了出处但与当前代码不兼容 ⇒ 拦
COMPAT_OK = "ok"
COMPAT_UNKNOWN = "unknown"
COMPAT_INCOMPATIBLE = "incompatible"

#: 版本号形如 `主.次.补[(+|-)?后缀]`。⛔ 只做**形状**校验，不做语义比较：
#:    "0.10 比 0.9 新"这类判断需要完整的版本语义，而我们的兼容判据是按
#:    **主版本**比（见 `is_compatible`），形状校验只用来挡住"把一句话
#:    塞进版本字段"这种事。
_VERSION_RE = re.compile(r"^\d+(\.\d+)*([-+][0-9A-Za-z.\-]+)?$")


def parse_version(v: Any) -> Optional[Tuple[int, ...]]:
    """把版本号解析成数字元组；形状不对 ⇒ `None`（不是抛错）。

    ⛔ 返回 `None` 而不是抛异常：调用方在**读账本**路径上，账本里的值
    可以是不受控的（人为编辑 / 别的语言写的）。读路径的判据是
    "能判就读、不能判就报 unknown"，不是"崩掉"。
    """
    if not isinstance(v, str):
        return None
    m = _VERSION_RE.match(v.strip())
    if not m:
        return None
    head = v.strip().split("-")[0].split("+")[0]
    try:
        return tuple(int(x) for x in head.split("."))
    except ValueError:                                     # pragma: no cover
        return None


def is_compatible(recorded: Any, current: Optional[str] = None) -> Optional[bool]:
    """账本声明的代码版本与当前是否兼容。**不可判别 ⇒ `None`**。

    口径：**主版本相同即兼容**（`0.x` 期间按次版本比 —— 见下）。
      · `0.y.z`：次版本不同 ⇒ 不兼容（0.x 是快速迭代期，次版本会改
        账本/读数的语义；把它当兼容会让"跨版本比读数"这件事重新发生）；
      · `>=1.0.0`：主版本相同 ⇒ 兼容（语义化版本的承诺）。

    ⛔ 三态不能合并成两态：`None`（不可判别）与 `False`（明确不兼容）
       的处置完全不同（前者放行并标注，后者拒绝）。V5 教训：把
       "不可判别"判成"有罪"，会让所有未标注的历史配置一夜之间失效。
    """
    cur = parse_version(current if current is not None else CODE_VERSION)
    got = parse_version(recorded)
    if got is None or cur is None:
        return None
    if not cur or not got:
        return None
    if cur[0] == 0 and got[0] == 0:
        #: 0.x：第一个分量恒为 0，真正区分语义的是第二个分量。
        return (len(cur) > 1 and len(got) > 1 and cur[1] == got[1])
    return cur[0] == got[0]


@dataclass(frozen=True)
class Recorded:
    """账本里读出来的一条出处声明。"""
    source: str = ""            # 事件 kind / 文件名等"这条声明来自哪"
    code_version: str = ""      # 写它的代码版本（空 = 没声明）
    format_version: int = 0     # 写它的格式版本（0 = 没声明）

    @property
    def declared(self) -> bool:
        return bool(self.code_version) or self.format_version > 0

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source,
                "code_version": self.code_version,
                "format_version": self.format_version,
                "declared": self.declared}


def read_from_payload(payload: Any, *, source: str = "") -> Recorded:
    """从一条事件的 payload 里读出处声明。

    ⛔ `payload` 是不可信输入（账本可编辑、别的语言可写）⇒ 形状不对
       一律降级成"没声明"，绝不抛错（读路径崩掉比读到空更糟）。
    """
    if not isinstance(payload, dict):
        return Recorded(source=source)
    cv = payload.get(KEY_CODE_VERSION)
    fv = payload.get(KEY_FORMAT)
    return Recorded(
        source=source,
        code_version=cv.strip() if isinstance(cv, str) else "",
        format_version=fv if isinstance(fv, int) and not isinstance(fv, bool)
        else 0,
    )


def stamp(payload: Optional[Dict[str, Any]] = None, *,
          with_format: bool = True) -> Dict[str, Any]:
    """给一条 payload **盖章**（不改已有的键）。

    ⛔ 只在"第一条事件"上调用（见模块 docstring 的取舍）：每个写入侧的
       第一条事件声明一次即可，逐条盖会把账本撑大。
    ⛔ `setdefault`：调用方若显式传了出处（例如测试要构造旧版本事件），
       不许被这里覆盖 —— 那是"伪造历史"。
    """
    body: Dict[str, Any] = dict(payload or {})
    body.setdefault(KEY_CODE_VERSION, CODE_VERSION)
    if with_format:
        body.setdefault(KEY_FORMAT, LEDGER_FORMAT)
    return body


def compat_report(recorded: Recorded,
                  current_code: Optional[str] = None) -> Dict[str, Any]:
    """把一次出处声明的兼容性结论整理成可观测读数。

    ⛔ 三种结论的**处置**不同，必须在读数里分开：
      · `ok`            —— 声明了且兼容；
      · `incompatible`  —— 声明了但不兼容（格式版本不同 或 代码版本不兼容）
                           ⇒ 调用方应当拒绝继续用这份数据做裁决；
      · `unknown`       —— 没声明（升级前存量）⇒ 放行，但读数要写明
                           "这份数据的出处不可判别"。
    """
    cur = current_code if current_code is not None else CODE_VERSION
    out: Dict[str, Any] = {
        "state": COMPAT_UNKNOWN,
        "recorded": recorded.to_dict(),
        "current_code_version": cur,
        "current_format_version": LEDGER_FORMAT,
        "reasons": [],
    }
    if not recorded.declared:
        out["reasons"].append(
            "账本里没有出处声明（升级前的存量）⇒ 无法判别它由哪个版本写出；"
            "按『不可判别 ≠ 有罪』放行，但读数不可跨版本比较")
        return out
    bad = []
    if recorded.format_version and recorded.format_version != LEDGER_FORMAT:
        bad.append(f"格式版本 {recorded.format_version} != 当前 {LEDGER_FORMAT}"
                   f"（改格式必须迁移 + 留痕，否则会以『链断了』的形式报错）")
    ver_ok = is_compatible(recorded.code_version, cur)
    if ver_ok is False:
        bad.append(f"代码版本 {recorded.code_version} 与当前 {cur} 不兼容"
                   f"（0.x 期按次版本比、1.x 起按主版本比）")
    elif ver_ok is None and recorded.code_version:
        #: ⛔⛔ 这里**不能**沉默：`is_compatible` 返回 `None` 有两种来源 ——
        #:    ① 账本没声明版本（已在上面按 `unknown` 放行）；
        #:    ② 声明了但**形状非法**（"一句话不是版本" / 空串）。
        #:    第 ② 种必须判**不兼容**：形状都对不上，凭什么说它兼容？
        #:    （实测踩过：原来只判 `is False`，于是乱填的版本字段被判成
        #:     `ok` —— "不能判定"被当成了"没问题"。）
        bad.append(f"账本声明的代码版本 {recorded.code_version!r} 形状非法"
                   f"（期望 主.次.补）⇒ 无法判定兼容，按不兼容处理")
    if bad:
        out["state"] = COMPAT_INCOMPATIBLE
        out["reasons"] = bad
    else:
        out["state"] = COMPAT_OK
        out["reasons"] = ["出处声明与当前代码兼容"]
    return out


def describe() -> Dict[str, Any]:
    """给 `--version` / `/api/status` / 报告用的自述读数。"""
    return {
        "code_version": CODE_VERSION,
        "format_version": LEDGER_FORMAT,
        "note": ("账本出处契约：每个写入侧在自己的第一条事件里声明一次 "
                 f"`{KEY_CODE_VERSION}` / `{KEY_FORMAT}`；"
                 "兼容性由 `provenance.compat_report` 判定（三态，"
                 "不可判别放行、不兼容拒绝）"),
    }
