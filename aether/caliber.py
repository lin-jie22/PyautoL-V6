#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""尺子分区（caliber partition）：**让"哪条读数能跟哪条比"变成可回答的问题**。

═══ 缺口（r57 实测，本轮修） ═══

`aether doctor` 是红的：`judge.boundary` **fail** —— 生产账本里
`eval.verdict` 用了 **14 种判据身份**（`judge_version`，905 条），
外加 1416 条升级前存量（没有该字段）。

它给的出路是"用同一把尺子重跑一组"，而 ⛔ **系统里没有这个工具**
（`rebuild` 只重建投影，重跑判据要重跑评估）⇒ 按第 35b 条，
那条出路当时是**装饰**。

═══ 本模块做什么 ═══

不是"把历史清洗干净"（那要重跑评估，代价大且不可逆），而是三件事：

1. **分区**（`partition`）：把账本按 `judge_version` 切成段，逐段报
   `n / seq 区间 / 是否当前尺子 / 是否已被封存`。⇒ 混了多少把尺子、
   各自覆盖哪一段，第一次可读。
2. **封存**（`seal`）：**显式**声明"这一段退出一切跨轮比较"。
   ⛔ 只**追加**一条 `judge.sealed` 事件，**绝不删改历史**（账本是唯一
   事实源，删事件 = 断链 = 把 `ledger.chain` 判据打红）。
3. **可比视窗**（`comparable_view`）：封存之后，"哪一段能跟当前比"
   有唯一答案。⛔ 空集合语义必须写清楚（第 57 条）：可比视窗为 0
   ⇒ **没有任何历史读数能与当前尺子比**，这是诚实结论，不是 bug。

═══ 为什么封存不是"把闸调松" ═══

doctor 的 `judge.boundary` 判据原来一看到多把尺子就 fail，而它没有
配套动作 ⇒ 只能永远红。现在它 fail 时给的出路是 `aether caliber --seal`，
**这个命令真的存在**，而且封存真的改变读数（`comparable_view` /
`status` 的 `caliber` 段）。
⇒ 判据没松（没封存照样红），多出来的是**一条可执行的处置动作**。

═══ 三条边界 ⛔ ═══

1. 封存**不可撤销但可推进**：再封存一次会把边界挪到更晚的 seq；
   不允许往前挪（那会把已封存的历史重新放出来）。
2. `seq` 取自事件自身。没有 `seq` 的账本 ⇒ `sealed_upto` 为 None，
   **视窗 = 全部**（不静默变空 —— 第 57 条）。
3. 本模块**不做重跑**。重跑判据是研究方向（P2），不是收拾代码能解决的。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from . import judge as J

__all__ = ["SEALED", "partition", "sealed_upto", "comparable_view",
           "seal", "CaliberAgg", "render"]

#: 封存事件名。⛔ 它是**账本里的一个事件**，不是配置文件里的一个开关
#:    ⇒ 谁在哪一刻封存了什么，账本自己说（第 17 条：能力看账本读数）。
SEALED = "judge.sealed"

#: 没有判据身份的存量段（升级前的 1416 条）用什么名字报。
NO_VERSION = "(升级前存量)"


def _seq_of(ev: Any) -> Optional[int]:
    v = getattr(ev, "seq", None)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


#: ⛔ 只认**裁决事件**（与 `doctor.judge.boundary` 同口径）。
#:   不过滤 ⇒ "升级前存量"会把账本里所有非裁决事件（5214 条）都算进去
#:    ⇒ 读数失去意义（实测踩过，别再踩）。
VERDICT_KIND = "eval.verdict"


def _ver_of(ev: Any) -> Optional[str]:
    if getattr(ev, "kind", None) != VERDICT_KIND:
        return None
    p = getattr(ev, "payload", None) or {}
    v = p.get("judge_version") if isinstance(p, dict) else None
    return str(v) if v else None


def _is_verdict(ev: Any) -> bool:
    return getattr(ev, "kind", None) == VERDICT_KIND


def partition(events: Iterable[Any], current: Optional[str] = None) -> Dict[str, Any]:
    """按判据身份切段。返回逐段读数（**不带**任何裁决语义）。"""
    cur = current or J.identity()
    groups: Dict[str, Dict[str, Any]] = {}
    n_total = 0
    n_no_version = 0
    evs = list(events)
    sealed_at = sealed_upto(evs)
    for ev in evs:
        if getattr(ev, "kind", None) == SEALED:
            continue
        if not _is_verdict(ev):
            continue
        v = _ver_of(ev)
        if v is None:
            n_no_version += 1
            continue
        n_total += 1
        g = groups.setdefault(v, {"judge_version": v, "n": 0,
                                  "seq_min": None, "seq_max": None,
                                  "is_current": v == cur})
        g["n"] += 1
        s = _seq_of(ev)
        if s is not None:
            g["seq_min"] = s if g["seq_min"] is None else min(g["seq_min"], s)
            g["seq_max"] = s if g["seq_max"] is None else max(g["seq_max"], s)
    segs = sorted(groups.values(), key=lambda d: (d["seq_min"] is None,
                                                  d["seq_min"] or 0))
    for g in segs:
        g["sealed"] = sealed_at is not None and (
            g["seq_max"] is not None and g["seq_max"] <= sealed_at)
    return {
        "judge": cur,
        "n_verdicts_with_version": n_total,
        "n_no_version": n_no_version,
        "n_segments": len(segs),
        "sealed_upto": sealed_at,
        "segments": segs,
        #: ⛔ 多把尺子本身就是读数，不因封存而消失（封存改变的是**可比**，
        #:    不是"曾经用过几把尺子"这个事实）。
        "n_distinct_versions": len(segs),
    }


def sealed_upto(events: Iterable[Any]) -> Optional[int]:
    """最后的封存边界 seq。没有封存事件 ⇒ **None**（视窗 = 全部）。"""
    out: Optional[int] = None
    for ev in events:
        if getattr(ev, "kind", None) != SEALED:
            continue
        p = getattr(ev, "payload", None) or {}
        v = p.get("upto_seq") if isinstance(p, dict) else None
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        out = v if out is None else max(out, v)
    return out


def comparable_view(events: Iterable[Any],
                    current: Optional[str] = None) -> Dict[str, Any]:
    """封存之后，**哪一段还能跟当前的尺子比**。

    ⛔ 返回值里必须有 `n_comparable` 与 `dropped_*` 的分项：
       "可比 = 0" 有三种完全不同的成因（全被封存 / 全是别的尺子 /
       全是存量），合成一个数 ⇒ 无法归因（第 26 条：规模读数要有伴生读数）。
    """
    cur = current or J.identity()
    evs = list(events)
    part = partition(evs, cur)
    at = part["sealed_upto"]
    n_sealed = n_other = n_unknown = n_ok = 0
    for ev in evs:
        if getattr(ev, "kind", None) == SEALED:
            continue
        if not _is_verdict(ev):
            continue
        v = _ver_of(ev)
        if v is None:
            n_unknown += 1
            continue
        s = _seq_of(ev)
        if at is not None and s is not None and s <= at:
            n_sealed += 1
        elif v == cur:
            n_ok += 1
        else:
            n_other += 1
    return {
        "judge": cur,
        "sealed_upto": at,
        "n_segments": part["n_segments"],
        "segments": part["segments"],
        "n_comparable": n_ok,
        "dropped_sealed": n_sealed,
        "dropped_other_caliber": n_other,
        "dropped_no_version": n_unknown,
        #: ⛔⛔ 空集合语义（第 57 条）：`n_comparable == 0` **不是**"没数据"
        #:    —— 它是"没有任何历史读数能与当前尺子比"。分三种成因说清楚，
        #:    不许读成"系统坏了"或"系统很健康"。
        "why_empty": (None if n_ok else
                      "全部被封存" if n_sealed and not n_other and not n_unknown else
                      "没有一条是当前的尺子（历史段用的是旧尺子）"
                      if n_other and not n_sealed else
                      "混合成因：封存段 + 旧尺子段 + 存量段都在"),
        "partition": part,
    }


def seal(ledger: Any, reason: str = "", actor: str = "caliber",
         events: Optional[List[Any]] = None) -> Dict[str, Any]:
    """封存：**追加**一条 `judge.sealed`，把边界推到当前账本末尾。

    ⛔ 只追加、不删改（删 ⇒ 断链 ⇒ `ledger.chain` 转红）。
    ⛔ 只准往前推（第 1 条边界）：试图往前挪 ⇒ 拒绝并说明。
    """
    evs = events if events is not None else list(ledger.iter_events())
    part = partition(evs)
    cur = part["judge"]
    #: ⛔⛔ 边界 = **最后一个非当前尺子段的末尾**，不是"账本末尾"。
    #:    第一版写到账本末尾 ⇒ 把当前尺子的 12 条也一起封了
    #:    （实测 `n_comparable_after = 0`）⇒ 封存完反而一条都不能比，
    #:    那是把"处置"做成了"清空"。封存要退出的是**旧尺子**，不是全部。
    max_seq = None
    for ev in evs:
        if not _is_verdict(ev):
            continue
        v = _ver_of(ev)
        if v is None or v == cur:
            continue
        s = _seq_of(ev)
        if s is not None:
            max_seq = s if max_seq is None else max(max_seq, s)
    prev = part["sealed_upto"]
    if max_seq is None:
        return {"sealed": False, "reason":
                "没有旧尺子段可封存（全部是当前的尺子，或没有 seq）"}
    if prev is not None and max_seq <= prev:
        return {"sealed": False, "reason":
                f"封存边界只准前推：当前已在 seq={prev}，账本末尾 seq={max_seq}"}
    payload = {
        "upto_seq": max_seq,
        "prev_upto_seq": prev,
        "judge": part["judge"],
        "n_segments": part["n_segments"],
        "versions": [g["judge_version"] for g in part["segments"]],
        "n_verdicts_with_version": part["n_verdicts_with_version"],
        "reason": (reason or "")[:200],
    }
    ledger.append(SEALED, payload, actor=actor)
    #: 封存后的视窗**就地算**（不重读盘，也不伪造一个事件对象塞进列表 ——
    #:   那种"造一个像事件的东西"的写法正是第 19 条要排除的 fixture）。
    n_after = n_sealed_after = 0
    for ev in evs:
        if getattr(ev, "kind", None) == SEALED:
            continue
        v = _ver_of(ev)
        if v is None:
            continue
        s = _seq_of(ev)
        if s is not None and s <= max_seq:
            n_sealed_after += 1
        elif v == payload["judge"]:
            n_after += 1
    return {"sealed": True, "upto_seq": max_seq, "prev_upto_seq": prev,
            "payload": payload,
            "n_comparable_after": n_after,
            "dropped_sealed_after": n_sealed_after}


class CaliberAgg:
    """流式聚合：与 `status` 的**同一次**账本遍历共用（不许多遍历一遍）。

    ⛔ 与 `partition` / `comparable_view` **同一份逻辑**：本类只是把它们
       改成单遍流式，判据有两份实现就等于没有判据（第 13 条）。
    """

    def __init__(self, current: Optional[str] = None) -> None:
        self.cur = current or J.identity()
        self.groups: Dict[str, Dict[str, Any]] = {}
        self.n_no_version = 0
        self.sealed_at: Optional[int] = None
        self.n_sealed_ev = 0

    def feed(self, ev: Any) -> None:
        kind = getattr(ev, "kind", None)
        if kind != SEALED and kind != VERDICT_KIND:
            return
        if kind == SEALED:
            self.n_sealed_ev += 1
            p = getattr(ev, "payload", None) or {}
            try:
                v = int(p.get("upto_seq"))
            except (TypeError, ValueError):
                return
            self.sealed_at = v if self.sealed_at is None else max(self.sealed_at, v)
            return
        v = _ver_of(ev)
        if v is None:
            self.n_no_version += 1
            return
        g = self.groups.setdefault(v, {"n": 0, "seq_min": None, "seq_max": None})
        g["n"] += 1
        s = _seq_of(ev)
        if s is not None:
            g["seq_min"] = s if g["seq_min"] is None else min(g["seq_min"], s)
            g["seq_max"] = s if g["seq_max"] is None else max(g["seq_max"], s)

    def result(self) -> Dict[str, Any]:
        segs = []
        for v, g in sorted(self.groups.items(),
                           key=lambda kv: (kv[1]["seq_min"] is None,
                                           kv[1]["seq_min"] or 0)):
            segs.append({"judge_version": v, "n": g["n"],
                         "seq_min": g["seq_min"], "seq_max": g["seq_max"],
                         "is_current": v == self.cur,
                         "sealed": self.sealed_at is not None
                         and g["seq_max"] is not None
                         and g["seq_max"] <= self.sealed_at})
        at = self.sealed_at
        n_ok = sum(s["n"] for s in segs
                   if s["is_current"] and not s["sealed"])
        n_sealed = sum(s["n"] for s in segs if s["sealed"])
        n_other = sum(s["n"] for s in segs
                      if not s["is_current"] and not s["sealed"])
        return {
            "judge": self.cur,
            "n_segments": len(segs),
            "n_comparable": n_ok,
            "dropped_sealed": n_sealed,
            "dropped_other_caliber": n_other,
            "dropped_no_version": self.n_no_version,
            "sealed_upto": at,
            "segments": segs,
        }


def render(rep: Dict[str, Any]) -> str:
    """人读的文本（命令输出用）。"""
    L = [f"当前尺子 {str(rep.get('judge'))[:16]}",
         f"段数 {rep.get('n_segments')}  可比 {rep.get('n_comparable')}"
         f"  已封存 {rep.get('dropped_sealed')}"
         f"  别的尺子 {rep.get('dropped_other_caliber')}"
         f"  升级前存量 {rep.get('dropped_no_version')}"
         f"  封存边界 {rep.get('sealed_upto')}"]
    for s in (rep.get("segments") or []):
        flag = "✓当前" if s.get("is_current") else "  旧 "
        if s.get("sealed"):
            flag += "·已封存"
        L.append(f"  {flag}  {str(s.get('judge_version'))[:16]}"
                 f"  n={s.get('n'):<5d} seq {s.get('seq_min')}–{s.get('seq_max')}")
    if rep.get("n_comparable") == 0 and rep.get("why_empty"):
        L.append(f"  ⛔ 可比视窗为空 ⇒ {rep['why_empty']}")
    return "\n".join(L)
