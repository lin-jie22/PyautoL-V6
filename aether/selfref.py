#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自指负载：这套系统有多少产出是在**给自己造机器**。

⛔ 本模块**从 `scripts/probe_self_reference.py` 搬进来的**（2026-10-15）：
   它是交付物的一部分（别的模块要用它做判据 —— 见 `aether/cleanup.py`），
   而"交付物住在 scripts/ 里、由脚本反向提供 API"是依赖方向反了。
   脚本现在只做 CLI 薄壳，扫描判据只有这一份。

原始说明如下。

═══ 它回答什么问题 ═══

"如果让它全自主进化，最后会发生什么？"

推演能给出方向（因为闸挡住了"自欺"，挡不住"自指"），但推演不能给出
**速度与规模**。这个探针把两个最可能的退化形态变成读数：

  ① **零消费方的机制**（死接口）：新增的公开方法/参数/字段没有任何调用方。
     它是最低风险的变异 —— 不破坏任何断言，也不改进任何东西。
     ⛔ 这不是理论：人工普查已经抓到 4 个（`verify_chain(expected_prev_hash=)`、
     `search(max_links_added=)`、`require_reward()`、`Baseline.failed_ids()`）
     —— 靠人翻代码抓 4 个，说明该机械扫。
  ② **只写不读的读数**：写进 report/stats/字段，但没有任何地方读它。
     这是假仪表的前身：它让"我加了观测"与"观测真的能看见"混为一谈。
     实测抓到过 `replay.stats` / `replay.base_reward`（只写不读）、
     `_last_search_embed_error`（写后无读取）。

外加账本侧的**时间序列**：机制类事件与增益类事件的比值怎么走。
⛔ 比值本身不是判据（"多了一台机器"不等于"变差了"）——它是**趋势读数**，
   要连着"有没有真的变好"一起看，所以本探针同时输出 gain 侧的读数。

═══ 纪律 ═══

  · **只读**：不改仓库、不写账本、不跑进化。
  · **不许假装精确**：静态扫描会有假阳性（反射/字符串调用/dynamic）。
    凡是不确定的，一律**不报为死接口**（宁可漏报，不可冤报 ——
    同族：`skipped ≠ pass`、`不可判别 ≠ 有罪`）。
  · 输出**三份**：人读表 + `--json` + 一句可直接引用的结论。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

#: 扫描根。⛔ 是模块级可变量（不是常量）：契约测试会把它指向**临时目录**
#: 来注入人造死接口做反证（见 `contract_self_reference_probe_test`）。
#: 写成 `Path(__file__).parents[1]` 更直白，但那样测试就无法改它了。
ROOT = Path(__file__).resolve().parent.parent

__all__ = ["scan_dead_api", "scan_write_only", "scan_unreachable",
           "mechanism_vs_gain", "report"]

#: 扫这些目录里的源码（`aether/` 是产品代码；tests/scripts 是使用者）。
SOURCE_DIRS = ("aether",)
#: 调用点可能在别处（测试/脚本也消费 API），所以扫描面比源码宽。
CALL_SITE_DIRS = ("aether", "tests", "scripts")

#: ⛔ 这些名字**故意**没有静态调用方，报为死接口是冤报：
DYNAMIC_ALLOW = frozenset({
    #: 协议/魔法方法
    "__init__", "__post_init__", "__repr__", "__str__", "__eq__", "__hash__",
    "__call__", "__len__", "__iter__", "__enter__", "__exit__", "__contains__",
    "__getitem__", "__setitem__", "__delitem__", "__bool__", "__lt__", "__le__",
    "__gt__", "__ge__", "__ne__", "__format__", "__getattr__", "__setattr__",
    "__aiter__", "__anext__", "__await__", "__index__", "__or__", "__add__",
    #: CLI 入口（由 argparse 的 `func` 表或模块级 `__main__` 调）
    "main", "cmd_check", "cmd_doctor", "build_parser",
    #: 反射/序列化入口
    "to_dict", "from_dict", "manifest", "identity", "fingerprint",
    #: ⛔ **框架回调**：静态上看永远"零调用方"，但运行时由框架调。
    #:    实测踩过：`http.server` 的 `do_GET` / `do_POST` / `log_message`
    #:    被当成死代码提了出来（清理提案的假阳）—— 删掉它们会让服务静默
    #:    不响应。凡是"名字由框架约定"的都必须列在这里。
    "do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH", "do_HEAD",
    "log_message", "handle", "setup", "teardown", "runTest",
    #: 探针/脚本的入口函数
    "report", "run", "probe", "discover", "validate", "approve", "verify",
    "install_hint", "merge", "load", "save", "stamp", "compat_report",
    "describe", "record", "summarize", "cross_language", "find_node",
    "to_manifest", "read_from_payload", "parse_version", "is_compatible",
})


def _iter_py(dirs: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for d in dirs:
        base = ROOT / d
        if base.is_dir():
            out += sorted(p for p in base.rglob("*.py")
                          if "__pycache__" not in p.parts)
    return out


def _defined_api(path: Path) -> List[Tuple[str, int, str]]:
    """源码里定义的公开函数/方法：`(名字, 行号, 种类)`。"""
    out: List[Tuple[str, int, str]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and not node.name.startswith("__"):
                continue
            kind = "method" if any(
                isinstance(p, ast.FunctionDef) and node in ast.walk(p)
                for p in tree.body if isinstance(p, ast.ClassDef)) else "function"
            out.append((node.name, node.lineno, kind))
    return out


def scan_dead_api(*, quiet: bool = True) -> Dict[str, Any]:
    """找**零调用方**的公开函数/方法（按保守口径：只报"确定没人叫"的）。

    ⛔ 保守口径（宁可漏报，不可冤报）：
       ① 名字出现在**任何** `.py` 的 `Name`/`Attribute` 节点里 ⇒ 不算死；
       ② 名字出现在**任何**字符串字面量里 ⇒ 不算死（反射/argparse/装饰器
          常把函数名当字符串用）；
       ③ 名字在 `DYNAMIC_ALLOW` 里 ⇒ 不算死；
       ④ `tests/contract_*` 里出现过 ⇒ 不算死（测试守着它 = 有人用它）。
    """
    #: ① / ②：全仓收集"被用到的名字"
    used: Set[str] = set()
    for p in _iter_py(CALL_SITE_DIRS):
        txt = p.read_text(encoding="utf-8", errors="replace")
        try:
            t = ast.parse(txt)
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.Attribute):
                used.add(n.attr)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                #: 字符串里出现标识符形状的名字 ⇒ 保守地算"可能被用"
                for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{2,}", n.value):
                    used.add(m.group(0))
    dead: List[Dict[str, Any]] = []
    for f in _iter_py(SOURCE_DIRS):
        for name, line, kind in _defined_api(f):
            if name in DYNAMIC_ALLOW or name in used:
                continue
            dead.append({"file": str(f.relative_to(ROOT)), "line": line,
                         "name": name, "kind": kind})
    return {"dead_api": dead, "n_dead": len(dead),
            "note": ("保守口径：名字只要在别处以标识符**或字符串**出现过就不算死；"
                     "`DYNAMIC_ALLOW` 里的（协议方法 / CLI 入口 / 反射入口）"
                     "一律不报。⇒ 这是**下界**，真实死接口只会更多。")}


def scan_write_only() -> Dict[str, Any]:
    """找**只写不读**的语句（`x.y = ...` / `d["k"] = ...` 之后没人读）。

    ⛔ 只扫一种最清楚的形态：**对 self 属性的一次性赋值**，且该属性名
    在全仓除了赋值行外**没有**任何读形态（`.attr` 取值 / `getattr`）。
    这种形态最容易确定（`replay.stats = stats` 就是），也最容易假阳，
    所以判据收得很紧：赋值行不计入"读"。
    """
    assigns: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    reads: Counter = Counter()
    for f in _iter_py(CALL_SITE_DIRS):
        txt = f.read_text(encoding="utf-8", errors="replace")
        try:
            t = ast.parse(txt)
        except SyntaxError:
            continue
        rel = str(f.relative_to(ROOT))
        for n in ast.walk(t):
            #: 赋值：`self.X = ...` 或 `obj.X = ...`
            if isinstance(n, ast.Assign):
                for tgt in n.targets:
                    if (isinstance(tgt, ast.Attribute)
                            and isinstance(tgt.value, ast.Name)):
                        assigns[tgt.attr].append((rel, n.lineno))
            #: 读：`x.attr` 出现在**非 Store 上下文**里
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                reads[n.attr] += 1
    write_only: List[Dict[str, Any]] = []
    for attr, sites in assigns.items():
        #: 读次数为 0，或者读次数比赋值次数还少（只读自己的赋值行）
        if reads.get(attr, 0) == 0:
            write_only.append({"attr": attr,
                               "n_assign": len(sites),
                               "first_site": f"{sites[0][0]}:{sites[0][1]}"})
    write_only.sort(key=lambda d: (-d["n_assign"], d["attr"]))
    return {"write_only": write_only, "n_write_only": len(write_only),
            "note": ("只看 `obj.attr` 赋值而全仓无 `x.attr` 读取的形态。"
                     "⇒ 同样是**下界**：`d['k'] = v` 这类键写入没扫"
                     "（键名太泛，假阳率会爆）。")}


#: 终止语句：它们**之后**的同级语句在控制流上永不可达。
#: ⛔ 只认"语句块顶层的裸终止语句"—— 嵌在 `if` / `try` 里的不算
#:    （`if x: return 1` 之后的代码在 x 为假时**是可达的**）。
_TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def scan_unreachable() -> Dict[str, Any]:
    """找**控制流上永不可达**的语句块（`return` 之后粘着的复制件）。

    ═══ 它与前两类扫描的性质不同：这是**唯一可证明**的一类 ═══

        · `scan_dead_api`   —— **启发式**：名字没在别处出现过 ⇒ 不算死。
              它永远只能是**下界** —— 反射 / 字符串调用 / 框架回调静态
              不可见 ⇒ 它只撑得住「提案给人看」，撑不住「判定」。
        · `scan_write_only` —— 同为启发式（读形态扫不全）。
        · `scan_unreachable`—— **语法级确定**：终止语句之后的同级语句，
              控制流永远到不了 ⇒ 删掉它**行为等价**。
              这是**证明**，不是「测试通过了」。

    ⛔ 保守口径（宁可漏报，不可冤报，与前两类同纪律）：
       ① **只扫 `body`，不扫 `orelse` / `finalbody` / `handlers`**：
          · `while/for` 的 `else` 在循环正常结束时**是可达的**；
          · `try` 的 `finally` 在 `return` 之后**仍会执行**（那是 with/try
            自己的语义，不是「后面的语句」）；
          · `except` 分支同理。⇒ 一律不扫 ⇒ 只会漏报。
       ② **只认语句块顶层的裸终止语句**：`if x: return 1` 里的 `return`
          嵌在 `If.body` 里，不是本块的顶层 ⇒ 后面的代码在 `x` 为假时
          可达 ⇒ 不报。（而 `If.body` 自己只有一条语句，也没有后继。）
       ③ 嵌套块只留**最外层**：`return` 之后一个 `if` 内部的终止语句
          产生的区间已被外层覆盖 ⇒ 去重丢弃。⛔ 不这么做，同一段会被
          报两次，而 **条数是减负读数** ⇒ 重复计数会让读数失真
          （与「计数与 evidence 必须分开存」同族）。
       ④ 扫不到 ⇒ 空列表，不抛错。

    ⛔ 为什么这一类是「能让系统提议改自己源码」的关键：
       前两类的判据是「看起来没人用」，谁也不敢据此自动改；
       这一类的判据是「执行流压根到不了」⇒ 它可以过闸。
    """
    spans: List[Dict[str, Any]] = []
    for f in _iter_py(SOURCE_DIRS):
        txt = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            continue
        rel = str(f.relative_to(ROOT))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for i, st in enumerate(body):
                if not isinstance(st, _TERMINATORS):
                    continue
                tail = body[i + 1:]
                if not tail:
                    break                    #: 一个块只取第一处，见 ③
                first, last = tail[0], tail[-1]
                spans.append({
                    "file": rel,
                    "kind": type(st).__name__.lower(),
                    "terminator_line": int(getattr(st, "lineno", 0) or 0),
                    "start_line": int(getattr(first, "lineno", 0) or 0),
                    "end_line": int(getattr(last, "end_lineno", None)
                                    or getattr(last, "lineno", 0) or 0),
                    "n_stmts": len(tail),
                })
                break
    #: ③ 去重：丢弃被其它区间完整包含的内层区间（同一段只算一条）。
    spans.sort(key=lambda d: (d["start_line"], -d["end_line"]))
    kept: List[Dict[str, Any]] = []
    for s in spans:
        if any(s["file"] == k["file"] and s["start_line"] >= k["start_line"]
               and s["end_line"] <= k["end_line"] for k in kept):
            continue
        kept.append(s)
    kept.sort(key=lambda d: (d["file"], d["start_line"]))
    return {"unreachable": kept, "n_unreachable": len(kept),
            "n_lines": sum(max(0, d["end_line"] - d["start_line"] + 1)
                           for d in kept),
            "note": ("控制流级判定：终止语句之后的**同级**语句永不可达 ⇒ "
                     "删掉行为等价（**证明**，不是测试通过）。保守面：只扫 "
                     "`body`、只认顶层裸终止语句 ⇒ 同样是**下界**。")}


#: 机制类事件：它们是"系统在给自己造机器"的痕迹。
#: ⛔ 这里放**裸名**（`_bare()` 已去掉 `harness.` / `eval.` / `cap.` 前缀）。
#:    实测踩过两次同一个坑：先写裸 `verdict` 而实际是 `eval.verdict`
#:    ⇒ 增益恒 0；改成全名后 `_bare()` 又把全名去掉了 ⇒ 机制恒 0。
#:    事件名比较必须先归一化，且两张表用**同一种命名**。
MECHANISM_KINDS = frozenset({
    "init", "commit", "revert", "result", "probe", "trace", "layer", "step",
    "mined", "noop", "stopped", "saturated", "run_config", "write",
    "candidate", "invoke",
})
#: 增益类事件：它们**直接**记录"有没有变好"。
GAIN_KINDS = frozenset({"verdict"})


def _bare(kind: str) -> str:
    """事件名归一化：账本里是 `eval.verdict` / `cap.candidate` 这类带前缀的，
    而判据表里写的是裸名。

    ⛔ 实测踩过：`GAIN_KINDS` 里写 `eval.verdict`，而实际 kind 也是
       `eval.verdict` —— 但表里我写成了裸 `verdict`，于是**增益事件数恒为 0**、
       比值恒为 None。一个"看起来有读数"的假仪表：它不报错，只是永远指向
       同一个结论（"没有增益事件"）。事件名比较必须先归一化。
    """
    return kind.split(".", 1)[-1] if "." in kind else kind


def mechanism_vs_gain(ledger: Path) -> Dict[str, Any]:
    """账本时间序列：机制事件 vs 增益事件的比值怎么走。

    ⛔ 比值不是判据：机制多不等于变差（闸也是机制）。它只回答
       "系统的努力花在哪一侧"。所以这里同时把 **gain 侧**的读数算出来：
       每轮 reward 的分布、提交/回滚比、noop 占比 —— 两边一起看才有意义。
    """
    if not ledger.is_file():
        return {"present": False, "note": f"没有账本：{ledger}"}
    per_round: Dict[int, Counter] = defaultdict(Counter)
    n_events = 0
    commits = 0
    reverts = 0
    noops = 0
    commits_with_gain = 0
    commits_without_gain_field = 0
    gains: List[float] = []
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:                                  # noqa: BLE001
            continue
        n_events += 1
        kind = str(ev.get("kind") or "")
        p = ev.get("payload") or {}
        rnd = p.get("round_no")
        try:
            rnd = int(rnd)
        except (TypeError, ValueError):
            rnd = -1
        bare = _bare(kind)
        side = ("gain" if bare in GAIN_KINDS
                else "mech" if bare in MECHANISM_KINDS else "other")
        per_round[rnd][side] += 1
        if kind == "harness.commit":
            commits += 1
            g = p.get("gain")
            if g is None:
                commits_without_gain_field += 1
            else:
                gains.append(float(g))
                if float(g) > 0:
                    commits_with_gain += 1
        elif kind == "harness.revert":
            reverts += 1
        elif kind == "evolve.noop":
            noops += 1
    #: ⛔⛔ `eval.verdict` **没有 `round_no`**（实测 1380 条全无）⇒ 逐轮
    #:    比值算不出来。可用的分组键只有 `sid`（会话）/ `run_id` / `tag`。
    #:    这不是探针的偷懒，是账本的事实：**增益读数与机制读数不在同一套
    #:    轮次坐标里** —— 于是"某一轮花了多少机器、换来多少增益"这个问题
    #:    在现有账本上**无法回答**。把它记成读数而不是绕过它。
    #:    这里退而求其次：按 `sid`（会话）分组，两侧都按同一个键聚合。
    per_sid: Dict[str, Counter] = defaultdict(Counter)
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:                                  # noqa: BLE001
            continue
        p2 = ev.get("payload") or {}
        sid = str(p2.get("sid") or "(no-sid)")
        bare2 = _bare(str(ev.get("kind") or ""))
        side2 = ("gain" if bare2 in GAIN_KINDS
                 else "mech" if bare2 in MECHANISM_KINDS else "other")
        per_sid[sid][side2] += 1
    by_sid = []
    for sid, c in per_sid.items():
        by_sid.append({"sid": sid[:12], "mech": c["mech"], "gain": c["gain"],
                       "ratio": (round(c["mech"] / c["gain"], 2)
                                 if c["gain"] else None)})
    by_sid.sort(key=lambda d: -(d["mech"] + d["gain"]))

    #: 逐轮比值（**只有带 round_no 的事件能进这里** ⇒ 增益侧必然为 0，
    #:  这是账本的形状，不是探针的 bug；读的时候要看这一条注释）
    trend: List[Dict[str, Any]] = []
    for rnd in sorted(per_round):
        if rnd < 0:
            continue
        c = per_round[rnd]
        mech, gain = c["mech"], c["gain"]
        trend.append({"round": rnd, "mech": mech, "gain": gain,
                      "ratio": round(mech / gain, 2) if gain else None})
    #: ⛔⛔ 总量必须从**按会话**的表求和，不能从 `trend`（只含带 round_no
    #:    的事件）—— 增益事件恰恰**不带 round_no** ⇒ 从 trend 求和会得到
    #:    `gain_total = 0`，于是一个"不报错、但永远说没有增益"的假仪表。
    #:    实测：这条被 `contract_self_reference_probe_test` 的 D4 抓到。
    tot_mech = sum(d["mech"] for d in by_sid)
    tot_gain = sum(d["gain"] for d in by_sid)
    #: ⛔ 这一项是**废读数**，保留是为了让读者看见它为什么废：
    #:    `harness.revert` 的 payload **没有** `gain` 键 ⇒
    #:    "commit 里 gain>0 的比例"恒等于 1.0（因为只有成功的 commit 才写
    #:    这个键，而成功的 commit 必然 gain>0 —— 循环论证）。
    #:    真正有信息量的是**幅度分布**与**回滚占比**。
    yield_is_vacuous = (commits_without_gain_field == 0 and commits > 0
                        and commits_with_gain == commits)
    return {
        "present": True, "n_events": n_events,
        "commits": commits, "reverts": reverts, "noops": noops,
        "commits_with_gain": commits_with_gain,
        "gain_min": (round(min(gains), 4) if gains else None),
        "gain_max": (round(max(gains), 4) if gains else None),
        "gain_median": (round(sorted(gains)[len(gains) // 2], 4)
                        if gains else None),
        "gain_at_risk": (round(sum(1 for g in gains if g < 0.5) / len(gains), 3)
                         if gains else None),
        "commit_yield_is_vacuous": yield_is_vacuous,
        "commit_yield": (round(commits_with_gain / commits, 3)
                         if commits else None),
        "revert_ratio": (round(reverts / (commits + reverts), 3)
                         if (commits + reverts) else None),
        "noop_ratio": (round(noops / (commits + reverts + noops), 3)
                       if (commits + reverts + noops) else None),
        "mech_total": tot_mech, "gain_total": tot_gain,
        "mech_gain_ratio": (round(tot_mech / tot_gain, 2) if tot_gain else None),
        "by_sid": by_sid,
        "verdict_has_round_no": False,
        "join_note": ("⛔ 增益事件（`eval.verdict`）**不带 round_no** ⇒ "
                      "「某一轮花了多少机器、换来多少增益」这个问题在现有"
                      "账本上无法回答。按 sid 分组的比值是能算的那种近似。"),
        "trend": trend,
    }


def report(*, ledger: Path, quiet: bool = True) -> Dict[str, Any]:
    return {"dead_api": scan_dead_api(quiet=quiet),
            "write_only": scan_write_only(),
            "ledger": mechanism_vs_gain(ledger)}


def _main(argv: Sequence[str]) -> int:
    import argparse
    #: ⛔⛔ 账本路径来自 `paths`（布局的**唯一事实源**），不在这里手拼
    #:    `ROOT / "data" / "ledger.jsonl"`。手拼会被
    #:    `tests/contract_entrypoints_test.py` 的 E1 静态闸抓到 ——
    #:    实测它真的响过：本模块从 `scripts/` 搬进 `aether/` 时把这行
    #:    一起搬了进来 ⇒ 布局悄悄长出了第五份，而 E1 是唯一发现它的东西
    #:    （搬到 `aether/` 之后它才进 E1 的扫描面：此前它住在 scripts/，
    #:     静态闸根本不看那里）。
    #:    ⛔ 用**绝对** import 且延迟到函数内：本模块要能被 importlib
    #:    单独加载（契约测试靠它注入临时 ROOT 做反证），相对 import 在
    #:    那种加载方式下会 `ImportError: no known parent package`。
    from aether.paths import paths as _layout
    ap = argparse.ArgumentParser(prog="probe_self_reference",
                                 description="自指负载探针（只读）")
    ap.add_argument("--ledger", default=str(_layout(ROOT)["ledger"]))
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    rep = report(ledger=Path(ns.ledger))
    if ns.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    da = rep["dead_api"]
    wo = rep["write_only"]
    lg = rep["ledger"]
    print("=" * 72)
    print("自指负载探针（**只读**：不改仓库、不写账本、不跑进化）")
    print("=" * 72)
    print(f"\n① 零消费方的公开接口：{da['n_dead']} 个（**下界**）")
    for d in da["dead_api"][:20]:
        print(f"   · {d['file']}:{d['line']}  {d['name']}()  [{d['kind']}]")
    if da["n_dead"] > 20:
        print(f"   … 还有 {da['n_dead'] - 20} 个")
    print(f"   口径：{da['note'][:70]}…")

    print(f"\n② 只写不读的属性：{wo['n_write_only']} 个（**下界**）")
    for w in wo["write_only"][:15]:
        print(f"   · {w['attr']}  赋值 {w['n_assign']} 次，首处 {w['first_site']}")
    print(f"   口径：{wo['note'][:70]}…")

    print("\n③ 账本：机制事件 vs 增益事件")
    if not lg.get("present"):
        print(f"   {lg.get('note')}")
    else:
        print(f"   事件总数 {lg['n_events']}；机制 {lg['mech_total']} / "
              f"增益 {lg['gain_total']} ⇒ 比值 {lg['mech_gain_ratio']}")
        print(f"   提交 {lg['commits']}（其中带增益 "
              f"{lg['commits_with_gain']} ⇒ yield {lg['commit_yield']}）/ "
              f"回滚 {lg['reverts']} / 空转 {lg['noops']}")
        print(f"   回滚占比 {lg['revert_ratio']}；空转占比 {lg['noop_ratio']}")
        print("   按**会话**分组的比值（能算的那种；见下面的 join_note）：")
        for d in lg["by_sid"][:8]:
            print(f"     sid {d['sid']:<14} mech={d['mech']:<5} "
                  f"gain={d['gain']:<5} ratio={d['ratio']}")
        print(f"   ⛔ {lg['join_note']}")
    print("\n⛔ 这些是**读数不是判据**：机制多不等于变差（闸也是机制）。"
          "要判好坏必须连着 gain 侧一起看 —— 上面第 ③ 节给了两边。")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
