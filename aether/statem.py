#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""状态机的**共用不变式**：机器不许回退「人的裁决」与「终态」。

═══ 这个模块是被一个事故逼出来的（r60 D，2026-10-19 实测）═══

两条链（`caps_candidate` 装工具 / `cleanup` 删代码）都长这样：

    生成提案 → **过闸** → 人批准 → 人执行 → 复验 → 终态

而两条链的「过闸」都写了同一行：

    state = VALIDATED if gate_passed else REJECTED     # 无条件覆写

过闸的本职是**重算闸的结论**，它却顺手把 `state` 也一起覆写了。
于是「批准之后重跑一次过闸」会把人的批准**静默撤销**：

    ① 人批准                  ⇒ approved
    ② 再跑 validate           ⇒ approved → **validated**   （降级）
    ③ 再跑 propose            ⇒ validated → **pending**    （冲回）

生产账本上，这三步是一个人的一次批准被两次机器调用抹掉的全过程：

    seq=6955  2026-10-19 23:56:18  approved   by='linshaojie'   ← 人批的
    seq=7021  2026-10-20 00:52:13  validated                    ← 机器降级
    seq=7022  2026-10-20 00:52:14  pending                      ← 机器冲回

⛔⛔ 它比「状态机没有终态」（r60 A/B）更隐蔽：那两例是**卡住不动**，
    看得见；这一例是**往后退**，而"退回 pending"在界面上和
    "还没提过"长得一模一样 —— 批准消失了，而且没人会问。

═══ 不变式 ═══

    未裁决（秩 < 已裁决线）⇒ 机器可自由覆写；
    已裁决（秩 ≥ 已裁决线）⇒ **只能前进**，回退一律挡住。

⛔ 「已裁决」由**秩**定义，不由状态名定义：`caps_candidate` 的
    `approved`/`installed` 与 `cleanup` 的 `applied`/`executed`
    是同一件事的两种叫法 —— 都是「人已经替这个决定负责了」。
    ⇒ 用一张序表把它们对齐，而不是在两条链里各写一遍判断
      （第 76 条：同一件事两处消费 ⇒ 口径必须同源）。

═══ ⛔⛔ 边界：什么**不归它管**，比它管什么更重要 ═══

① 它只管「不许回退」，**不管「该不该前进」**。
   「能不能到 installed / executed」仍由各自的闸与复验管。
   别把它当状态机引擎用 —— 它只是最后一道保险。

② ⛔ **复验不套它**。`verify()` / `verify_executed()` 的本职就是
   回答「现在还成立吗」，而"装好的工具后来被卸了"是个必须能被
   看见的坏消息。给它套上"不许回退" ⇒ 坏消息消失，比没保护更糟
   （r60 B 已经定过这个口径：复验失败**只留痕、不落盘**，
    "撤销批准"这条路本来就不存在）。

③ 它**不报错、不记因**：挡住回退是默认正确的行为，不是异常。
   调用方自己决定要不要把"被挡住了"说出去 —— 该说的是
   「批准还在，但闸现在的读数是红的」，而不是一句报错。
"""
from __future__ import annotations

from typing import Any, Mapping

__all__ = ["Rank"]


class Rank:
    """一个状态机的**序**，以及在序上画的那条「已裁决线」。

    Parameters
    ----------
    order:
        状态名 → 秩（int）。秩越大越靠后（越接近终态）。
    settled:
        「已裁决」的**最低**状态名。达到它的状态只能前进。

    ⛔⛔ 为什么要显式传 `settled` 而不是"秩 ≥ 2"这种数字：
        秩是**排序用的**，不是**语义用的**。把"已裁决"写成一个裸数字，
        下次有人往中间插一个状态时，那条线会静默移动到别的地方去
        —— 而移动之后系统看起来一切正常（第 57 条：空集合最会撒谎
        的那一家族：看不见的漂移）。
    """

    def __init__(self, order: Mapping[str, int], settled: str) -> None:
        self._order = {str(k): int(v) for k, v in dict(order).items()}
        if settled not in self._order:
            #: ⛔ 建表时**大声失败**，不要静默退化。若 `settled` 拼错，
            #:    退回 0 会让"所有状态都是已裁决" ⇒ 整条链从此不许改状态
            #:    ⇒ 闸失去牙，而没有任何一条读数会变红。
            raise ValueError(
                f"settled={settled!r} 不在序表里 {sorted(self._order)}；"
                f"拼错它会让「不许回退」静默失效（不是变小，是反向）")
        self._settled = self._order[str(settled)]
        self._settled_name = str(settled)

    @property
    def settled_name(self) -> str:
        """已裁决线的状态名（给日志与提示用，别拿它做判断）。"""
        return self._settled_name

    def of(self, state: Any) -> int:
        """状态的秩。**未知状态 ⇒ 0（最低）**。

        ⛔ 不认识的 state 退回最低而不是报错：档案里可能有旧版本写过的
           状态名，那时「不认识」该被当成「没裁决」，而不是让读路径炸掉
           （与 `load()` 坏文件 ⇒ 空表同一条纪律：读路径要稳，第 24 条）。
        """
        return int(self._order.get(str(state), 0))

    def is_settled(self, state: Any) -> bool:
        """是不是「已裁决」（人批过 / 已结案）⇒ 机器不许再回退它。"""
        return self.of(state) >= self._settled

    def move(self, cur: Any, nxt: Any) -> str:
        """从 `cur` 推到 `nxt`；**回退一律挡住**，被挡时返回原状态。

        ⛔ 它**不报错也不记因**（见模块 docstring 边界 ③）。
        ⛔ 同名推进（`cur == nxt`）原样返回：那不是回退，不必惊动谁。
        """
        c, n = str(cur), str(nxt)
        if self.is_settled(c) and self.of(n) < self.of(c):
            return c
        return n
