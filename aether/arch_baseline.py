#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""架构基线：**把「巨石在恶化」从一个肉眼读数变成一条有牙的闸**。

═══ 为什么要有它 ═══

r58 的处置是「`cmd_evolve` 807 行不拆，用 arch_scan 的 F 段读数盯着」，
F 段末尾写着「真正要防的是**恶化**：新增一条就说明有人又在入口里写编排」。

⛔ 但那句话当时是**空话**：F 段只打印数字，机器不记得上一次是多少
   ⇒ 「新增一条」只有人眼 diff 才看得出来，而没人会去 diff。
   一个靠人自觉去比对的闸，等价于没有闸（第 7 条：「有机制」≠「被走到」）。

本模块补的就是这一块：**落一份基线，之后每次比**。

═══ ⛔⛔ 三条设计边界（改之前先读） ═══

1. **基线随代码进版本库，不进 `data/`**。
   `data/*.json` 被 `.gitignore` 排除（运行产物不该提交）。而基线是
   **代码的属性**，不是运行数据 ⇒ 放 `data/` 会让 `git clone` 得到一份
   「没有基线」的仓库，闸从第一天起就是 `unknown`（第 57 条：那不是 ok）。

2. **没有基线 ⇒ `unknown`，不是 ok**。
   「没量过」与「量过没恶化」在输出里必须长得不一样 —— 否则这条闸
   装上即恒绿，且恒绿得毫无信息（第 9 条：恒真断言）。

3. **基线可以更新，但抹掉的恶化必须留痕**。
   更新基线天然能把一切恶化洗白。所以 `write()` 会把「这次更新吃掉了
   什么」追加进 `acknowledged`，只增不删 —— 洗白是**显式的、可审计的**，
   不是静默的（与 `judge.sealed` 同样的只追加语义）。

═══ 恶化怎么定义（第 31/32 条：先问这闸在数什么、何时失效） ═══

数的是「架构分层与体积是否在**往坏的方向走**」，三个维度：

  · `new_big_fn`  新增 ≥150 行的函数（含「某个函数跨过了 150 行」）
  · `new_over`    新增「入口层 → 判据层」的直接依赖边
  · `grown`       已有巨石的**绝对**体积增长超过余量

⛔ 为什么不看**相对**增长率：实测 `cli.py` 的历史行数轨迹，单提交
   典型涨 1%~8%、最大 15%（1064→1220）。用相对阈值，正常的大改动
   就会红 ⇒ 红了就更新基线 ⇒ 噪音 + 洗白循环，闸三天失效。
   ⇒ 用**绝对余量**（函数 +100 行 / 模块 +250 行）：语义是「可以长，
   但长到这个量级就该拆了」，且不随基线大小漂移（第 35c 条同形病）。
"""
from __future__ import annotations

import ast
import collections
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .paths import paths as _paths

__all__ = [
    "BIG_FN_LINES", "MARGIN_FN", "MARGIN_MOD",
    "ENTRY_LAYERS", "VERDICT_LAYERS", "SCHEMA_VERSION",
    "STATE_OK", "STATE_WORSE", "STATE_UNKNOWN",
    "BaselineError",
    "modname_of", "build_graph", "measure", "measure_package", "diff",
    "read", "write", "baseline_path",
]

#: 函数超过这么多行 = 巨石（与 r58 F 段口径**完全一致**，不许两头各改一处）。
BIG_FN_LINES = 150
#: 已有巨石允许再长的**绝对**行数（超过 ⇒ 恶化）。见文件头「为什么是绝对值」。
MARGIN_FN = 100
#: 已有模块允许再长的绝对行数。模块比函数长得快是正常的，余量给得更大。
MARGIN_MOD = 250

#: 判据层：入口**不该**直接依赖这些（依赖了 ⇒ 入口事实上在当编排层用）。
#: ⛔ 现状 cli 就依赖其中几条（809 行的 `cmd_evolve` 是历史产物）⇒
#:    判「有越界」会恒红；本模块只判「**新增**越界」。
VERDICT_LAYERS = ("verifier", "judge", "nongression", "generalization",
                  "genome", "environment")
ENTRY_LAYERS = ("cli", "serve", "ui", "entry")

SCHEMA_VERSION = 1

STATE_OK = "ok"
STATE_WORSE = "worse"
#: ⛔ 不可判别（没基线 / 基线坏 / 基线是空的）—— **不等于** ok。
STATE_UNKNOWN = "unknown"


class BaselineError(RuntimeError):
    """基线文件存在但读不出一份可用基线。

    ⛔ 单独一个异常类型而不是返回 `None`：返回 None 会把「坏了」和
       「没有」混成一种含义（第 25 条：一个值两种含义就会读错）。
    """


def baseline_path(root) -> Path:
    """基线的落点。⛔ 走 `paths()`，不在这里再拼一遍（第 46 条）。"""
    return _paths(root)["arch_baseline"]


def modname_of(p: Path, base: Path) -> str:
    rel = p.relative_to(base).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def build_graph(files: Iterable[Path], base: Path):
    """解析 import，产出 nodes / 内部边。相对导入按**所在包**展开。

    ⛔⛔ 这是 `scripts/arch_scan.py` 同名函数的**唯一实现**（r59 从脚本
       搬进生产包）。搬它的原因：F 段读数与基线闸必须是**同一个量具** ——
       两份实现迟早会给出两个数字，那时「读数说 6 个、闸说 5 个」，
       而没人知道该信谁（第 13 条：判据只有一份实现）。
    """
    nodes: Dict[str, Any] = {}
    edges: collections.Counter = collections.Counter()
    for p in files:
        m = modname_of(p, base)
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            rel = str(p.relative_to(base))
        except ValueError:
            rel = str(p)
        nodes[m] = dict(path=rel, lines=src.count("\n") + 1)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                #: ⛔ 别叫 `base`：它会被当成函数参数 `base` 的重新绑定，
                #:    第二个文件起 `p.relative_to(base)` 就用了上一次的相对
                #:    包名 ⇒ 报 "not in the subpath of '.'"（实测踩到）。
                pkg = ".".join(m.split(".")[:-1]) if n.level else ""
                if n.level:
                    tgt = ".".join(x for x in (pkg, n.module or "") if x)
                    if n.module:
                        edges[(m, tgt)] += 1
                    else:
                        for a in n.names:          # `from . import a, b`
                            edges[(m, f"{tgt}.{a.name}" if tgt else a.name)] += 1
                elif n.module:
                    edges[(m, n.module)] += 1
            elif isinstance(n, ast.Import):
                for a in n.names:
                    edges[(m, a.name)] += 1
    real = set(nodes)
    inner = collections.Counter({(s, d): c for (s, d), c in edges.items()
                                 if s in real and d in real and s != d})
    return nodes, inner


def measure(files: Iterable[Path], inner, pkg: str = "aether") -> Dict[str, Any]:
    """量一批文件的三项读数。返回的形状即基线里存的形状。

    ⛔ `pkg` 是**参数**：反证组用的是人造包 `pkg`，写死 "aether" ⇒
       反证现场一个函数都扫不到 ⇒ 读数恒 0（第 24 条同形病，r58 踩过）。
    """
    files = list(files)
    mods: Dict[str, int] = {}
    fns: Dict[str, int] = {}
    for p in files:
        #: ⛔ 被测包是**参数**，见上。
        if p.parent.name != pkg:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        mods[p.name] = src.count("\n") + 1
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = (n.end_lineno or n.lineno) - n.lineno + 1
                if span >= BIG_FN_LINES:
                    fns[f"{p.name}:{n.name}"] = span
    over: List[str] = []
    adj: Dict[str, set] = collections.defaultdict(set)
    for (s, d) in inner:
        adj[s].add(d)
    for e in ENTRY_LAYERS:
        mod = f"{pkg}.{e}"
        for v in VERDICT_LAYERS:
            if f"{pkg}.{v}" in adj.get(mod, ()):
                over.append(f"{e} → {v}")
    return {"modules": mods, "big_fn": fns, "over": sorted(over)}


def measure_package(pkg_dir: Path, pkg: str = "aether") -> Dict[str, Any]:
    """`doctor` 侧的入口：只扫一个包目录（不需要全仓文件列表）。

    ⛔ `over` 只数 `pkg.X → pkg.Y` 的**直接**边 ⇒ 与全仓图算出来的结果
       一致（间接边不参与判定）。这条前提写在这里，改判定方式时要复查。
    """
    files = sorted(Path(pkg_dir).glob("*.py"))
    _nodes, inner = build_graph(files, Path(pkg_dir).parent)
    return measure(files, inner, pkg=pkg)


def diff(base: Optional[Dict[str, Any]], cur: Dict[str, Any],
         *, margin_fn: int = MARGIN_FN,
         margin_mod: int = MARGIN_MOD) -> Dict[str, Any]:
    """比基线。三态：`ok` / `worse` / `unknown`。

    ⛔ 变小**不算**恶化，但要单独列出来（`shrunk`）：一个函数从 300 行
       降到 120 行是**拆成功了**，读报告的人必须看得见，否则会以为
       基线没更新而白高兴。
    """
    out: Dict[str, Any] = {
        "state": STATE_UNKNOWN, "new_big_fn": [], "new_over": [],
        "grown_fn": [], "grown_mod": [], "shrunk": [],
        "gone_big_fn": [], "gone_over": [], "reason": "",
    }
    #: ⛔ 第 57 条：空集合当分母最会撒谎。基线缺失/空 ⇒ 不可判别，
    #:    绝不因为「cur 里没有新增项」就报 ok —— 那正是恒绿的来源。
    if not base or not base.get("modules"):
        out["reason"] = ("没有基线（或基线是空的）⇒ 是否恶化不可判别"
                         if not base else "基线里没有模块读数 ⇒ 不可判别")
        return out
    bm: Dict[str, int] = dict(base.get("modules") or {})
    bf: Dict[str, int] = dict(base.get("big_fn") or {})
    bo: List[str] = list(base.get("over") or [])
    cm: Dict[str, int] = dict(cur.get("modules") or {})
    cf: Dict[str, int] = dict(cur.get("big_fn") or {})
    co: List[str] = list(cur.get("over") or [])

    out["new_big_fn"] = sorted(
        f"{k}（{v} 行）" for k, v in cf.items() if k not in bf)
    out["new_over"] = sorted(set(co) - set(bo))
    out["grown_fn"] = sorted(
        f"{k}：{bf[k]} → {cf[k]}（+{cf[k] - bf[k]}，余量 {margin_fn}）"
        for k in cf if k in bf and cf[k] - bf[k] > margin_fn)
    out["grown_mod"] = sorted(
        f"{k}：{bm[k]} → {cm[k]}（+{cm[k] - bm[k]}，余量 {margin_mod}）"
        for k in cm if k in bm and cm[k] - bm[k] > margin_mod)
    out["shrunk"] = sorted(
        f"{k}：{bf[k]} → {cf[k]}（-{bf[k] - cf[k]}）"
        for k in cf if k in bf and bf[k] - cf[k] > 0)
    #: ⛔ 第 49 条：一个分支的观测不许比另一个少。巨函数被拆到 **150 行
    #:    以下** 就从 `big_fn` 里消失了 ⇒ 既不进 `shrunk`（两边都要有）
    #:    也不进 `new_big_fn`（它是变少不是变多）⇒ 「拆成功了」这件最好的
    #:    事反而**在报告里完全看不见**。单列 `gone_*`，且它必须显眼。
    out["gone_big_fn"] = sorted(
        f"{k}（曾是 {bf[k]} 行 ⇒ 现在 <{BIG_FN_LINES} 行）"
        for k in bf if k not in cf)
    out["gone_over"] = sorted(set(bo) - set(co))
    n = (len(out["new_big_fn"]) + len(out["new_over"])
         + len(out["grown_fn"]) + len(out["grown_mod"]))
    out["state"] = STATE_WORSE if n else STATE_OK
    out["n_worse"] = n
    return out


def read(path) -> Optional[Dict[str, Any]]:
    """读基线。没有 ⇒ `None`；读得出但坏了 ⇒ 抛 `BaselineError`。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BaselineError(f"{p} 读不出来：{type(e).__name__}: {e}") from e
    if not isinstance(obj, dict) or obj.get("schema") != SCHEMA_VERSION:
        raise BaselineError(
            f"{p} 的 schema 是 {obj.get('schema') if isinstance(obj, dict) else '?'}"
            f"，本代码要 {SCHEMA_VERSION}")
    b = obj.get("baseline")
    if not isinstance(b, dict):
        raise BaselineError(f"{p} 里没有 baseline 段")
    return b


def write(path, cur: Dict[str, Any], *, note: str = "",
          prev: Optional[Dict[str, Any]] = None,
          acked: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """落一份基线。**只追加**留痕：更新时吃掉的恶化进 `acknowledged`。

    ⛔ 为什么留痕比禁止更新好：禁止更新 ⇒ 人只能删文件重来，而删文件
       没有痕迹；留痕 ⇒ 「这次是谁、因为什么、抹掉了多少」永远可查，
       且 `acknowledged` 的长度本身就是「被接受过多少次恶化」的读数。
    """
    p = Path(path)
    hist: List[Dict[str, Any]] = list(acked or [])
    d = diff(prev, cur) if prev else None
    if d and d["state"] == STATE_WORSE:
        hist.append({
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": note or "（未注明原因）",
            "n_worse": d["n_worse"],
            "delta": {k: d[k] for k in
                      ("new_big_fn", "new_over", "grown_fn", "grown_mod")},
        })
    obj = {
        "schema": SCHEMA_VERSION,
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "baseline": cur,
        "n_acknowledged": len(hist),
        "acknowledged": hist,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    #: 原子写：写坏一半的基线会让闸变成 `unknown`（fail-closed 方向），
    #: 但那是**假**的 unknown —— 别给人添这种麻烦。
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(p)
    return obj


def render(d: Dict[str, Any]) -> str:
    """把 diff 渲染成人能读的一段（doctor 的 detail 用）。"""
    if d["state"] == STATE_UNKNOWN:
        return d["reason"]
    n = d.get("n_worse", 0)
    if d["state"] == STATE_OK:
        s = "与基线相比没有恶化"
        for key, word in (("gone_big_fn", "个巨函数被拆掉了"),
                          ("gone_over", "条越界消失了"),
                          ("shrunk", "处变小")):
            if d.get(key):
                s += f"；{len(d[key])} {word}"
        return s
    parts = []
    if d["new_big_fn"]:
        parts.append(f"新增巨函数 {len(d['new_big_fn'])} 个：" +
                     "、".join(d["new_big_fn"][:3]))
    if d["new_over"]:
        parts.append("新增越界 " + "、".join(d["new_over"][:3]))
    if d["grown_fn"]:
        parts.append(f"巨函数变长 {len(d['grown_fn'])} 个：" +
                     "、".join(d["grown_fn"][:3]))
    if d["grown_mod"]:
        parts.append(f"模块变长 {len(d['grown_mod'])} 个：" +
                     "、".join(d["grown_mod"][:3]))
    #: ⛔ 恶化与改善可能同时发生（拆掉一个、又长出一个）⇒ 不许只念坏的，
    #:    否则「去拆巨石」这件事在报告里得不到任何正反馈。
    good = []
    if d.get("gone_big_fn"):
        good.append(f"{len(d['gone_big_fn'])} 个巨函数被拆掉")
    if d.get("gone_over"):
        good.append(f"{len(d['gone_over'])} 条越界消失")
    if d.get("shrunk"):
        good.append(f"{len(d['shrunk'])} 处变小")
    s = f"恶化 {n} 项 ⇒ " + "；".join(parts)
    return s + ("（但同时：" + "、".join(good) + "）" if good else "")


def compare(root, pkg_dir: Optional[Path] = None,
            pkg: str = "aether") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """一次做完「量 + 比」。`doctor` 与 `arch_scan` 都走这里（同源）。

    ⛔⛔ 默认量 `<root>/aether`，**不是**量「当前代码所在的那个包」。
       后者看着更省事（不用传参），但 `aether doctor --root /备份副本`
       会变成「拿 A 仓库的代码量出体积，去比 B 仓库的基线」—— 两个
       读数不同源，比出来的结论没有意义（第 1 条）。
       ⇒ 基线在 `<root>` 下，被测包也必须在 `<root>` 下；`root` 下没有
       `aether/` 时才退回当前包（单仓开发时两者是同一个，不影响）。
    """
    if pkg_dir is None:
        cand = Path(root) / pkg
        pkg_dir = cand if cand.is_dir() else Path(__file__).resolve().parent
    cur = measure_package(pkg_dir, pkg=pkg)
    p = baseline_path(root)
    try:
        base = read(p)
    except BaselineError as e:
        cur["_baseline_error"] = str(e)      # ⛔ 第 35d 条：进返回结构
        return diff(None, cur), cur
    d = diff(base, cur)
    d["baseline_path"] = str(p)
    d["n_acknowledged"] = 0
    if p.exists():
        try:
            d["n_acknowledged"] = int(json.loads(
                p.read_text(encoding="utf-8")).get("n_acknowledged", 0))
        except (OSError, ValueError):
            pass
    return d, cur
