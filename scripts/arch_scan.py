#!/usr/bin/env python3
"""架构体检：**把"系统长什么样"这件事变成可复跑的读数**，而不是一张人画的图。

## 为什么要有它

架构问题有两种：

  ① **静态的**——依赖成环、生产包反向依赖测试包、某个模块没人 import；
  ② **动态的**——模块在依赖图里可达，但**运行时一行都没跑**（第 7 条：
     「有机制」≠「这条路被走到」）。

② 比 ① 贵得多，而且**只能靠跑一次真实流程取证**。所以本脚本三件事一起做：

  A · 依赖图：规模 / 咽喉 / 环 / 包间反向依赖 / 零入度模块
  B · 可达性：从入口 BFS，找出"图里有、但没人走到"的 aether 模块
  C · 零次事件：AST 扫源码声明的事件名 − 账本实到 = **结构性死路清单**
     （第 20 条：找死路用机器化差分，不靠人工列清单）

## ⛔ 三条边界（读数时必须一起看，否则会读错）

  1. `sys.settrace` 只覆盖**本进程**。任务执行走 `subprocess`
     （`environment.py` 的 python_exec）⇒ 子进程里的 aether 代码不计入。
     "覆盖率低"因此有三种可能：真没跑到 / 只在子进程跑 / 被短路。
     ⇒ **覆盖率不是死代码证明**，它只是"值得去查"的线索。
  2. 覆盖率采自**一次** `evolve`。`serve` / `doctor` / `check` / `backup`
     那些入口 0% 是**正常**的（它们本来就不在 evolve 路径上）。
     ⇒ 报告把它们归入"别的入口"，与"可疑"分开列。
  3. 账本实到取自**生产账本** `data/ledger.jsonl`（可用 `--ledger` 换）。
     某事件 0 次 ⇒ 只说明"这份账本里没有"，要区分
     **结构性不可达** 与 **条件未满足**（后者是好事，别误杀）。
  4. tracer 在 `run_cover()` 内部启动，**晚于** `import aether.cli`
     ⇒ 导入期执行的代码不计入。`__init__.py` / `_version.py` / `paths.py`
     偏低是量具偏差，不是"没跑到"。

## 反证组（第 23 条：审计脚本自己要有反证）

`--self-test` 会造两个**已知答案**的人工现场：

  · 一个只被自己引用的孤岛模块 ⇒ B 段必须报 UNREACHED；
  · 一个源码里声明、账本里没有的事件名 ⇒ C 段必须报 ZERO。

抓不到 ⇒ 这套审计**自己**是坏的（静默恒绿），此时它的任何读数都不可用。

用法：
    python3 scripts/arch_scan.py                     # 全量（含跑一次 evolve）
    python3 scripts/arch_scan.py --no-run            # 只做静态 A/B，不跑
    python3 scripts/arch_scan.py --self-test         # 只跑反证组
"""
from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "backups", "console"}

#: evolve 之外的**独立入口**：它们的模块在 evolve 里 0% 是正常的，
#: 不该被报成"可疑死代码"（把它们点名，是为了不让"0%"被读成"有问题"）。
OTHER_ENTRIES = {
    "aether.serve": "HTTP 入口（cli 之外的第二个入口）",
    "aether.ui": "桌面窗口入口",
    "aether.doctor": "体检入口",
    "aether.check": "全量验收入口",
    "aether.backup": "备份入口",
    "aether.discover": "能力发现入口",
    "aether.caps_candidate": "能力候选入口",
    "aether.retrieval": "嵌入选型基准（离线评测用）",
    "aether.interop": "UCI 通用接入层（CLI caps / REA 桥走它）",
    "aether.__main__": "`python -m aether` 转发",
    "aether._version": "版本号常量（被 cli 读，不执行）",
    "aether": "包初始化（在 tracer 启动**之前**就 import 完了 ⇒ 0% 是量具偏差）",
}


# ─────────────────────────────────────────────────────────────
# A · 依赖图
# ─────────────────────────────────────────────────────────────
def modname_of(p: Path, base: Path = ROOT) -> str:
    rel = p.relative_to(base).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def collect_files(extra_dir: Path | None = None) -> list:
    root = extra_dir or ROOT
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.endswith(".py"):
                out.append(Path(dp) / f)
    return sorted(out)


def build_graph(files: list, base: Path = ROOT):
    """解析 import，产出 nodes / 内部边。相对导入按**所在包**展开（不是留成 '.'）。"""
    nodes, edges = {}, collections.Counter()
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


def report_graph(nodes, inner) -> dict:
    indeg, outdeg = collections.Counter(), collections.Counter()
    for (s, d) in inner:
        outdeg[s] += 1
        indeg[d] += 1
    adj = collections.defaultdict(set)
    for (s, d) in inner:
        adj[s].add(d)

    print("=" * 74)
    print("A · 依赖图（AST 解析，非人工列清单）")
    print("=" * 74)
    by_pkg = collections.defaultdict(list)
    for m, i in nodes.items():
        by_pkg[m.split(".")[0]].append(i["lines"])
    print(f"  py 文件 {len(nodes)}  总行数 {sum(sum(v) for v in by_pkg.values())}")
    for pkg in sorted(by_pkg):
        print(f"    {pkg:10s} 文件 {len(by_pkg[pkg]):3d}  行数 {sum(by_pkg[pkg]):6d}")

    print("\n  -- 咽喉（被依赖最多，改动爆炸半径最大）--")
    for m, c in indeg.most_common(10):
        print(f"    indeg={c:3d}  {m}")

    # 环：Tarjan
    index, low, stack, on, sccs = {}, {}, [], set(), []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]; counter[0] += 1
        stack.append(v); on.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    sys.setrecursionlimit(100000)
    for v in sorted({s for s, _ in inner} | {d for _, d in inner}):
        if v not in index:
            strong(v)
    print(f"\n  -- 循环依赖：{'无' if not sccs else sccs} --")

    bad = sorted({(s, d) for (s, d) in inner
                  if s.split(".")[0] == "aether"
                  and d.split(".")[0] in ("scripts", "tests", "tasks")})
    print(f"  -- 生产包反向依赖测试/脚本包：{bad if bad else '无'} --")
    return dict(indeg=indeg, adj=adj, sccs=sccs)


# ─────────────────────────────────────────────────────────────
# B · 可达性 + 运行时覆盖
# ─────────────────────────────────────────────────────────────
def report_reach(nodes, adj, prefix: str = "aether", extra_entries=()) -> list:
    """⛔ 入口 = 「不在被测包里的模块」+「被测包里显式命名的入口」。
    两边都能配（self-test 用它造已知答案的现场）。"""
    entries = [m for m in nodes if not m.startswith(prefix)]
    entries += ["aether.__main__", "aether.cli", "aether.serve", "aether.entry",
                "aether.check", "aether.doctor"] if prefix == "aether" else []
    entries += list(extra_entries)
    seen, stack = set(), [e for e in entries if e in nodes]
    while stack:
        v = stack.pop()
        if v in seen:
            continue
        seen.add(v)
        stack.extend(w for w in adj.get(v, ()) if w not in seen)
    unreach = sorted(m for m in nodes if m.startswith(prefix) and m not in seen)
    print("\n" + "=" * 74)
    print(f"B · 静态可达性（从所有入口 BFS，被测包 = {prefix}）")
    print("=" * 74)
    ae = [m for m in nodes if m.startswith(prefix)]
    print(f"  {prefix} 模块 {len(ae)}，可达 {len(ae) - len(unreach)}，不可达 {len(unreach)}")
    for m in unreach:
        print(f"    UNREACHED {m}  ({nodes[m]['lines']} 行)")
    return unreach


def exec_lines_of(p: Path) -> set:
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    return {n.lineno for n in ast.walk(tree) if isinstance(n, ast.stmt)}


def run_cover(rounds: int = 2, max_tasks: str = "12") -> dict:
    """跑一次真实 evolve，统计 aether 模块的行覆盖率（本进程）。"""
    from aether.cli import main as cli_main

    exec_lines = {str(p): exec_lines_of(p) for p in sorted((ROOT / "aether").glob("*.py"))}
    hit = collections.defaultdict(set)

    def _tracer(frame, event, arg):
        if frame.f_code.co_filename.startswith(str(ROOT / "aether")):
            if event == "line":
                hit[frame.f_code.co_filename].add(frame.f_lineno)
            return _tracer
        return None

    d = tempfile.mkdtemp(prefix="v6_archscan_")
    os.symlink(ROOT / "tasks", Path(d) / "tasks")
    buf = io.StringIO()
    sys.settrace(_tracer)
    try:
        with contextlib.redirect_stdout(buf):
            rc = cli_main(["evolve", "--rounds", str(rounds),
                           "--max-tasks", max_tasks, "--root", d])
    finally:
        sys.settrace(None)
    out = buf.getvalue()
    rep = {}
    if "{" in out:
        try:
            rep = json.loads(out[out.index("{"):])
        except ValueError:
            pass

    print("\n" + "=" * 74)
    print(f"C · 运行时覆盖（一次真实 evolve：rounds={rounds} max-tasks={max_tasks}，本进程）")
    print("=" * 74)
    if rep:
        print(f"  rc={rc} 轮数={len(rep.get('rounds') or [])} "
              f"dev={rep.get('dev_env')} ho={rep.get('heldout_env')} "
              f"cost={rep.get('cost_s')}s")
        for r in rep.get("rounds") or []:
            print(f"    轮{r.get('round_no')} 基线={r.get('baseline_reward')} "
                  f"提交={r.get('committed')} 候选={len(r.get('candidates') or [])}")
    rows = []
    for f, lines in exec_lines.items():
        got = len(hit.get(f, set()) & lines)
        rows.append((got, len(lines), Path(f).name))
    print(f"\n  {'覆盖率':>7}  {'命中/可执行':>12}  模块")
    for got, tot, name in sorted(rows, key=lambda r: (r[0] / r[1] if r[1] else 0)):
        cov = got / tot if tot else 0.0
        mod = "aether." + name[:-3]
        tag = ""
        if got == 0:
            tag = "  ⛔ZERO"
            if mod in OTHER_ENTRIES:
                tag += f"（别的入口：{OTHER_ENTRIES[mod]}）"
        elif cov < 0.10:
            tag = "  ⚠<10%"
        print(f"  {cov * 100:6.2f}%  {got:5d}/{tot:5d}   {name}{tag}")
    print("\n  ⛔ 覆盖率低 ≠ 死代码：见文件头「三条边界」。"
          "它只是「值得去查」的线索。")
    return dict(rep=rep, root=d,
                cover={n: (g, t) for g, t, n in rows})


# ─────────────────────────────────────────────────────────────
# C · 零次事件（源码声明 − 账本实到）
# ─────────────────────────────────────────────────────────────
EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9_]+)+$")
#: 这些不是事件名，是算子/层代号等，AST 兜底会误抓 ⇒ 显式排除。
NOT_EVENTS = {"binop", "boolop", "compare", "constant", "string", "unaryop"}


def declared_events(files) -> dict:
    out = collections.defaultdict(list)
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                nm = getattr(f, "attr", None) or getattr(f, "id", None)
                if nm in ("append", "append_many", "emit", "record",
                          "write_event", "log_event"):
                    for a in n.args[:1]:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                                and EVENT_NAME_RE.match(a.value):
                            out[a.value].append(f"{p.name}:{n.lineno}")
    return {k: v for k, v in out.items() if k.split(".")[0] not in NOT_EVENTS
            and k not in NOT_EVENTS}


def report_zero_events(files, ledger_path: Path, prod_only: bool = True) -> dict:
    """⛔ `prod_only`：只把 **aether/ 下声明的** 事件当作"生产事件"。

    契约测试会往临时账本里写 `c.d` / `t.one` 这类假 kind（fixture）——
    它们永远不该出现在生产账本里，报成"零次"纯属噪音，而且会稀释真正
    的死路（第 19 条：归因前先排除 fixture）。
    """
    if prod_only:
        files = [f for f in files
                 if f.parent.name == "aether" and f.name != "__init__.py"]
    seen = collections.Counter()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            seen[str(d.get("kind", "?"))] += 1
    decl = declared_events(files)
    live = {k: (seen.get(k, 0), decl[k][:2]) for k in decl if seen.get(k)}
    zero = {k: decl[k][:2] for k in decl if not seen.get(k)}
    print("\n" + "=" * 74)
    print(f"D · 零次事件（源码声明 − 账本实到；账本={ledger_path}）")
    print("=" * 74)
    print(f"  账本 {sum(seen.values())} 条 / {len(seen)} 种；"
          f"源码声明 {len(decl)} 种，其中**零次 {len(zero)} 种**")
    print("\n  -- 实到 --")
    for k, (c, loc) in sorted(live.items(), key=lambda kv: -kv[1][0]):
        print(f"    {c:6d}  {k:28s} {loc}")
    print("\n  -- ⛔ 零次：源码声明过，这份账本里一次都没有 --")
    for k, loc in sorted(zero.items()):
        print(f"    {k:32s} {loc}")
    print("\n  ⛔ 零次有两种含义，必须分开标定：**结构性不可达**（要修）"
          " vs **条件未满足**（好事，别误杀）。")
    return dict(seen=seen, zero=zero, live=live)


# ─────────────────────────────────────────────────────────────
# 反证组
# ─────────────────────────────────────────────────────────────
def self_test() -> int:
    """造两个**已知答案**的人工现场，验证本审计自己没有静默恒绿。"""
    print("=" * 74)
    print("反证组：本审计必须能抓到它自己声称能抓的东西")
    print("=" * 74)
    ok = True
    tmp = Path(tempfile.mkdtemp(prefix="v6_arch_selftest_"))
    (tmp / "pkg").mkdir()
    (tmp / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "pkg" / "island.py").write_text("X = 1\n", encoding="utf-8")
    (tmp / "pkg" / "main.py").write_text(
        "def f(led):\n    led.append('zzz.never_happens', {})\n", encoding="utf-8")
    files = collect_files(tmp)
    nodes, inner = build_graph(files, tmp)
    unreach = report_reach(nodes, collections.defaultdict(
        set, {s: {d} for (s, d) in inner}), prefix="pkg",
        extra_entries=("pkg.main",))
    hit_island = any("island" in m for m in unreach)
    print(f"  [1] 孤岛模块必须被报 UNREACHED … {'OK' if hit_island else '❌ 没抓到'}")
    ok &= hit_island

    fake_ledger = tmp / "ledger.jsonl"
    fake_ledger.write_text(json.dumps({"kind": "aaa.real"}) + "\n", encoding="utf-8")
    #: 人造现场的包不叫 aether ⇒ 必须关掉 prod_only，否则过滤会把反证自己吃掉。
    z = report_zero_events(files, fake_ledger, prod_only=False)
    hit_zero = "zzz.never_happens" in z["zero"]
    print(f"  [2] 未发生事件必须被报 ZERO … {'OK' if hit_zero else '❌ 没抓到'}")
    ok &= hit_zero

    print(f"\n  反证组 {'全绿 ⇒ 本审计有牙' if ok else '❌ 红了 ⇒ 本审计自己在静默恒绿'}"
          f"（退出码 {0 if ok else 1}）")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V6 架构体检（依赖 / 可达 / 零次事件）")
    ap.add_argument("--no-run", action="store_true", help="只做静态 A/B/D，不跑 evolve")
    ap.add_argument("--self-test", action="store_true", help="只跑反证组")
    ap.add_argument("--ledger", default=str(ROOT / "data" / "ledger.jsonl"))
    ap.add_argument("--rounds", default="2")
    ap.add_argument("--max-tasks", default="12")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()

    files = collect_files()
    nodes, inner = build_graph(files)
    g = report_graph(nodes, inner)
    report_reach(nodes, g["adj"])
    if not a.no_run:
        run_cover(int(a.rounds), a.max_tasks)
    report_zero_events(files, Path(a.ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
