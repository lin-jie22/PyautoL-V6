#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""陈述性知识：把"学到的关于世界的事实"变成**可继承、可选、可回滚**的。

═══ 缺口 ═══

harness 的层此前全是**指令/配置**（`routing` / `tools` / `skills` /
`memory_policy` / `prompts` / `meta_rules`）—— 回答"怎么做"。
而实际决定成败的知识不是技能，是**关于这个世界的事实**：

  · 哪对 `(候选, 环境对)` 是死路（探测 15 次、overfit 15 次、0 次通过）；
  · 哪些外部工具会超时（REA/Ghidra 在负载下会挤爆 300s）；
  · 哪些口径值不可信（`dev_floor` 太低时 0.1 的增益是噪声）。

这些现在住在**循环的内存缓存**里（`_deadend_cache`、冷却、`step_credit`），
于是：

  · **不会被继承** —— 换一个 harness（或换一次运行）就丢光；
  · **不能被选择** —— 它不在基因组里，闸与泛化闸都看不见它；
  · **不能被回滚** —— 它不是账本上的一次 `harness.commit`。

⇒ 系统学到的洞察全是**过程性**的，不是**陈述性**的。
   这是这套架构里我认为最大的设计缺口（比任何单个闸的细节都大）。

═══ 本模块提供什么 ═══

  · **键的形状**（作用域命名约定）：`deadend:` / `tool:` / `caliber:`，
    以及一个**规范化的环境对**表示（顺序无关：`a|b` == `b|a`）。
  · **读写**：`get` / `remember` / `forget`，都返回新的 `Harness`
    （走 `apply_gene` ⇒ 与基因编辑**同一条路径**，因此自动获得
    爆炸半径、身份寻址、可回滚）。
  · **晋升判据**：什么时候"该把一条观察写成事实"。
    ⛔ 这是本模块的核心判据，也是最容易搞错的地方：
       观察 → 事实的门槛不能是"发生过一次"。一条一次性的偶发被写成事实，
       会被**继承**下去、影响后续所有轮的提议 —— 那是把一个噪声点
       固化成制度。所以门槛是：**同一件事被独立观察到 ≥ N 次**（默认 3），
       并且**带证据来源**（哪几轮的哪些事件）。
  · **过期**：事实可以被证伪。`forget` 是显式动作；`should_recheck`
    给出"这条事实该重验了"的读数（老化：一条事实从未被重新确认，
    却一直在影响决策，那就是在吃陈年旧账）。

═══ 为什么不直接用循环里的死路缓存 ═══

那个缓存是**从账本重建的派生量**（文档明说），并且是**本轮**的。
它解决的是"这一轮怎么少跑几次"；本模块解决的是"**跨代继承**什么知识"。
两者职责不同 ⇒ 不合并（合并会让"读到旧账"与"学到新知识"混成一件事）。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .genome import Gene, Harness, apply_gene

__all__ = ["SCOPE_DEADEND", "SCOPE_TOOL", "SCOPE_CALIBER",
           "env_pair_key", "fact_key", "get", "remember", "forget",
           "should_recheck", "promote", "summarize", "PROMOTE_MIN_OBS",
           "STALE_ROUNDS"]

#: 作用域前缀（键的命名约定）。
SCOPE_DEADEND = "deadend"
SCOPE_TOOL = "tool"
SCOPE_CALIBER = "caliber"

#: "观察 → 事实"的门槛：同一件事要**独立观察到**这么多次才配写成事实。
#: ⛔ 不能是 1：一次偶发被写成事实，会被**继承**下去影响后续所有轮
#:    （把噪声点固化成制度）。默认 3 与死路冷却的口径一致。
PROMOTE_MIN_OBS = 3

#: 一条事实"多少轮没被重新确认"就算陈旧。
STALE_ROUNDS = 20


def env_pair_key(a: str, b: str) -> str:
    """环境对的**规范化**表示：顺序无关。

    ⛔ 顺序有关会把 `(x, y)` 与 `(y, x)` 记成两条不同的死路 ⇒
       冷却只对一半生效，而读数上看不出来（实测踩过同形态：
       `env_pair` 原先根本没记账，15 条全是 None）。
    """
    x, y = str(a), str(b)
    return f"{x}|{y}" if x <= y else f"{y}|{x}"


def fact_key(scope: str, *parts: str) -> str:
    """拼一条事实的键：`<作用域>:<段1>|<段2>...`。

    ⛔ 段之间统一用 `|`，作用域与段之间用 `:` —— `apply_gene` 的
       `_split_target` 按 `.` 切，所以键里**不许出现 `.`**
       （出现的话会被切成两层 target，行为不是报错而是改错 key）。
    """
    def _clean(p: str) -> str:
        return str(p).replace(".", "_").replace(":", "-").replace("|", "/")
    segs = [*[str(s) for s in parts]]
    return f"{_clean(scope)}:" + "|".join(_clean(s) for s in segs)


def get(h: Harness, key: str) -> Optional[Dict[str, Any]]:
    """读一条事实（不存在 ⇒ `None`，不抛错）。"""
    v = (h.world_facts or {}).get(key)
    return dict(v) if isinstance(v, dict) else (None if v is None else {"kind": "note", "value": v})


def remember(h: Harness, key: str, **fields: Any) -> Harness:
    """把一条事实写进 harness（**合并**已有断言，不覆盖）。

    ⛔ 走 `apply_gene` 而不是直接 `replace(h, world_facts=...)`：
       这样它和任何其它基因编辑**同一条路径** ⇒ 自动获得爆炸半径、
       内容寻址、以及"能被 `harness.commit` 记进账本、能被回滚"。
    """
    return apply_gene(h, Gene(
        op="append", target=f"L5.world_facts.{key}", value=dict(fields),
        rationale=f"world_fact:{key}"))


def forget(h: Harness, key: str) -> Harness:
    """忘掉一条事实（**可回滚**：这是事实与"藏在缓存里的洞察"的关键差别）。"""
    return apply_gene(h, Gene(
        op="remove", target=f"L5.world_facts.{key}",
        rationale=f"forget:{key}"))


def promote(observations: Iterable[Dict[str, Any]], *,
            min_obs: int = PROMOTE_MIN_OBS) -> Dict[str, Dict[str, Any]]:
    """把**观察**聚成**候选事实**（观察 → 事实的闸）。

    输入是若干条观察，每条至少要有 `key`；可选 `round_no`、`evidence`。
    输出：`{key: {…聚合出的断言…}}`，**只含达到门槛的**。

    ⛔ 门槛是"独立观察次数 ≥ `min_obs`"，不是"发生过一次"。理由：事实会被
       **继承**并影响后续所有轮的提议 ⇒ 把一次偶发固化成制度，
       比不学更坏（它会长期地把系统从一条可能可行的路上赶开）。
    ⛔ 同时要求**来源可追溯**：聚合结果里带 `observations`（轮号列表），
       没有来源的"事实"与"凭感觉"没有区别。
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for o in observations:
        if not isinstance(o, dict):
            continue
        k = str(o.get("key") or "")
        if not k:
            continue
        buckets.setdefault(k, []).append(o)
    out: Dict[str, Dict[str, Any]] = {}
    for k, obs in buckets.items():
        if len(obs) < min_obs:
            continue
        rounds = sorted({int(o.get("round_no")) for o in obs
                         if o.get("round_no") is not None})
        kinds = {str(o.get("kind") or "") for o in obs if o.get("kind")}
        out[k] = {
            "kind": (sorted(kinds)[0] if kinds else "note"),
            "observations": len(obs),
            "rounds": rounds[:20],
            #: ⛔ 把"这是几次观察得出的"写进事实本身：读的时候能判可信度，
            #:    而不是看到一个孤零零的 `streak: 4` 不知道它从哪来。
            "confirmed_round": (rounds[-1] if rounds else None),
        }
    return out


def should_recheck(h: Harness, *, current_round: int,
                   stale_rounds: int = STALE_ROUNDS) -> List[str]:
    """哪些事实"该重验了"。

    ⛔ 判据：一条事实若**从未**在近 `stale_rounds` 轮里被重新确认，
       却一直在影响决策（被继承、被读取）—— 那就是在吃陈年旧账。
       这不是错误，是需要**重新观察**的信号（世界变了）。
    """
    out: List[str] = []
    for k, v in (h.world_facts or {}).items():
        if not isinstance(v, dict):
            continue
        conf = v.get("confirmed_round")
        if conf is None:
            out.append(k)          # 连"哪一轮确认的"都没有 ⇒ 必然要重验
            continue
        try:
            if int(current_round) - int(conf) > stale_rounds:
                out.append(k)
        except (TypeError, ValueError):
            out.append(k)
    return sorted(out)


def summarize(h: Harness) -> Dict[str, Any]:
    """这一代 harness **知道什么**（按作用域分类的读数）。

    ⛔ 这个读数的用途：让"继承了多少知识"变成可见的。此前洞察住在内存里，
       没人能回答"这一轮比上一轮多知道了什么"。
    """
    by_scope: Dict[str, List[str]] = {}
    for k in sorted((h.world_facts or {})):
        scope = str(k).split(":", 1)[0] or "(无作用域)"
        by_scope.setdefault(scope, []).append(k)
    total = sum(len(v) for v in by_scope.values())
    return {"n": total, "by_scope": {k: len(v) for k, v in by_scope.items()},
            "keys": by_scope,
            "note": ("陈述性知识：可继承、可被选择、可被回滚。"
                     "⛔ 它与循环里的死路缓存**职责不同**：那个是"
                     "本轮少跑几次的派生量，这个是跨代继承什么。")}
