#!/usr/bin/env python3
"""⛔ **真实 bug 修复的「形态普查」** —— 回答一个能决定方向的问题。

═══ 为什么需要这个脚本 ═══

上一轮（README 152）的结论是「12 个真实 bug，1 个能被表达，0 个被修好」，
对照组（V6 自造题）第 1 轮就修好。这两个读数放在一起，还差最后一环：

    「我挑的样本不好」与「这两类题根本不是同一种东西」长得一模一样。

前者是采样问题（换一批样本就行），后者是**结构问题**（换多少批都不行）。
区分它们的判据只能是**形态**：

    V6 的修法族（`aether/repair.py` 的 5 个 `revert_*`）全部是
    「把某一类 AST 节点按位置对齐回基线」——它们是 V6 **自己的变异算子**
    的对称逆运算（该文件的注释里逐字写着"加一个变异维度，必须同时加它的
    对称修法"）。

    ⇒ 一个修复**只有在形态上属于"某个算子/常量在某位置被换掉了"**时，
      才落在这 5 个修法的射程内。新增语句、改分支结构、换算法 —— 都不在。

本脚本不猜：对每个真实 commit 拿父版本（broken）与本版本（fixed）的同一
个源文件，**解析成 AST 逐节点比形状**，把差异分成两类：

    operator    —— 树形同构，差异只出现在「算子类型 / 常量值 / not 的增删」
                   ⇒ 5 个 revert_* 里至少有一个理论上够得着
    structural  —— 树形不同构（语句增删、分支改结构、函数改写）
                   ⇒ 没有任何一个 revert_* 够得着

═══ ⛔ 读数纪律 ═══

  · **逐条记账**：每个 sha 一行，不许只报一个总数；
  · **分母要说清**：`git log` 里 message 像 bug fix 的不一定真是 bug fix，
    也不一定配了测试 ⇒ 每一步筛掉多少都要打印（第 57 条：空集合当分母）；
  · **分类函数必须自检**：5eb6cc0（新增两行 `if k < 0: k += self.len`）
    已知必须判 `structural` ⇒ 开工前先跑这条，判错就是量具坏了（第 24 条）；
  · **对照组同源**：同一把尺子去量 V6 自己 `synthesize_tasks` 造出来的题
    ⇒ 如果那把尺子量 V6 的题不是 100% operator，说明尺子错了，不是世界错了。

跑法：
    python scripts/realbug_shape_census.py --repo /tmp/realrepo/boltons
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 只有这些字段的差异算「算子级」：其余一切不同都算结构级。
#:    与 `aether/repair.py` 的五个维度逐条对应，不许自己加项：
#:      revert_control_flow ⇒ Compare/BinOp 的 op
#:      revert_literals     ⇒ Constant 数值
#:      revert_strings      ⇒ Constant 字符串
#:      revert_boolops      ⇒ BoolOp 的 and/or
#:      revert_unaryops     ⇒ UnaryOp(Not) 的增删
_OP_FIELDS = {
    "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow", "LShift", "RShift",
    "BitOr", "BitAnd", "BitXor",
    "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "Is", "IsNot", "In", "NotIn",
    "And", "Or", "Not", "USub", "UAdd", "Invert",
}


def _strip_unary(node: ast.AST) -> ast.AST:
    """⛔ 剥掉 `not` 包装再比形状。

    `revert_unaryops` 支持「not 的增删」⇒ 那**确实是** V6 够得着的形态
    （该维度的注释里写了按树形位置对齐）。不剥掉的话，`x` vs `not x`
    会被判成结构差异 ⇒ 把 V6 能修的误判成不能修 ⇒ 低估它。
    """
    while isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        node = node.operand
    return node


def _children(a: ast.AST) -> list:
    """⛔⛔ **算子节点不算子节点** —— 这是本脚本最容易错的一处。

    `ast.Compare` 的 `ops=[Gt()]` 里那个 `Gt()` **也是** `iter_child_nodes`
    吐出来的子节点。于是 `a > b` 与 `a >= b` 在树形上会被判成
    `Gt` vs `GtE` ⇒ 类型不同 ⇒ 不同构 ⇒ **算子级差异被误判成结构级**。

    若不剔除，`revert_control_flow`（专修比较符）射程内的修复会全部被
    算成"结构级" ⇒ 普查结论偏悲观 —— 而且偏得悄无声息（这正是第 24 条：
    某维度恒为一个极端值，先怀疑量具）。自检 2 就是为它准备的。
    """
    return [c for c in ast.iter_child_nodes(a)
            if not isinstance(c, (ast.operator, ast.cmpop,
                                   ast.boolop, ast.unaryop))]


def _same_shape(a: ast.AST, b: ast.AST) -> bool:
    """只比**树形**（节点类型 + 子节点数 + 递归），不比任何字段值。"""
    a = _strip_unary(a)
    b = _strip_unary(b)
    if type(a) is not type(b):
        return False
    ca = _children(a)
    cb = _children(b)
    if len(ca) != len(cb):
        return False
    return all(_same_shape(x, y) for x, y in zip(ca, cb))


def _op_diff(a: ast.AST, b: ast.AST, out: list) -> None:
    """收集两类节点之间的**算子级**差异（形状已确认同构）。"""
    a2, b2 = _strip_unary(a), _strip_unary(b)
    if isinstance(a2, ast.UnaryOp) != isinstance(b2, ast.UnaryOp):
        out.append("UnaryOp/not 增删")
    for f in ("op", "ops"):
        if hasattr(a, f) or hasattr(b, f):
            va = getattr(a, f, None)
            vb = getattr(b, f, None)
            ta = type(va).__name__ if va is not None else None
            tb = type(vb).__name__ if vb is not None else None
            if ta != tb:
                out.append(f"{f}:{ta}→{tb}")
                continue
            if isinstance(va, list) and isinstance(vb, list):
                for x, y in zip(va, vb):
                    if type(x).__name__ != type(y).__name__:
                        out.append(f"op:{type(x).__name__}→{type(y).__name__}")
    if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
        if a.value != b.value:
            out.append(f"Constant:{a.value!r}→{b.value!r}")
    for x, y in zip(_children(a), _children(b)):
        _op_diff(x, y, out)


def classify(broken_src: str, fixed_src: str) -> dict:
    """把一个修复判成 operator / structural，并给出**证据**而不是结论。"""
    try:
        ba = ast.parse(broken_src)
        fa = ast.parse(fixed_src)
    except SyntaxError as e:
        #: ⛔ 解析失败是**第三种**结论，不许塞进"修不好"那一类（第 144 条：
        #:    一个语法错误不许伪装成一种缺陷形态）。
        return {"kind": "unparsed", "detail": f"{type(e).__name__}: {e}"}
    if not _same_shape(ba, fa):
        #: 找出第一个不同构的位置，作为**诊断**（否则"结构级"是个黑箱结论）
        return {"kind": "structural",
                "detail": _first_shape_mismatch(ba, fa)}
    diffs: list = []
    _op_diff(ba, fa, diffs)
    #: ⛔ 同构但差异不含算子 ⇒ 例如只改了变量名、docstring。那是**重构**
    #:    不是修复；V6 的修法族同样够不着（没有对应的算子维度）。
    kinds = {d.split(":")[0] for d in diffs}
    if not diffs or not (kinds & {"op", "ops", "Constant", "UnaryOp/not 增删"}):
        return {"kind": "other", "detail": f"同构但非算子差异：{diffs[:3]}"}
    #: ⛔ 必须细分：改**算子**（`>`→`>=`）与改**字符串常量**（多数是
    #:    错误消息 / docstring）在 V6 里完全不是一回事 —— 后者改的是文本，
    #:    真实 pytest 判据**测不到** ⇒ 它落在 `revert_strings` 射程内也
    #:    不构成任务。混在一起报会高估"V6 够得着的真实 bug"数量。
    has_op = bool(kinds & {"op", "ops", "UnaryOp/not 增删"})
    consts = [d for d in diffs if d.startswith("Constant:")]
    sub = "op" if has_op else ("const_str" if any(
        isinstance(_lit_of(d), str) for d in consts) else "const_num")
    return {"kind": "operator", "sub": sub,
            "detail": "; ".join(diffs[:6]), "n_diff": len(diffs)}


def _lit_of(diff: str):
    """从 `Constant:'a'→'b'` 这种诊断串里取出**新值**，用于分 str/num。"""
    try:
        tail = diff.split("→", 1)[1]
        return ast.literal_eval(tail)
    except Exception:  # noqa: BLE001
        return None


def _first_shape_mismatch(a: ast.AST, b: ast.AST, path: str = "") -> str:
    a2, b2 = _strip_unary(a), _strip_unary(b)
    if type(a2) is not type(b2):
        return f"{path}{type(a).__name__}→{type(b).__name__}"
    ca = _children(a)
    cb = _children(b)
    if len(ca) != len(cb):
        return f"{path}{type(a).__name__} 子节点 {len(ca)}→{len(cb)}"
    for i, (x, y) in enumerate(zip(ca, cb)):
        r = _first_shape_mismatch(x, y, f"{path}{type(x).__name__}[{i}].")
        if r:
            return r
    return ""


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(repo),
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--limit", default="120", help="最多普查多少个候选")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"✗ {repo} 不是 git 仓库")
        return 1

    # ── 开工前先自检量具（⛔ 顺序不许反）──────────────────────────
    ok = _selftest()
    print(f"量具自检：{'✅ 通过' if ok else '❌ 未通过'}")
    if not ok:
        print("  ⇒ 分类函数本身不可信，普查结果无意义，停机")
        return 1

    # ── 1) 枚举候选（⛔ 每一步筛掉多少都要打印）─────────────────
    allsha = _git(repo, "log", "--format=%H").split()
    print(f"\n仓库总 commit：{len(allsha)}")

    cands = []
    for sha in allsha:
        subj = _git(repo, "log", "-1", "--format=%s", sha).strip()
        if not _looks_like_fix(subj):
            continue
        files = [l.strip() for l in
                 _git(repo, "show", "--name-only", "--format=", sha).splitlines()
                 if l.strip()]
        srcs = [f for f in files
                if f.startswith("boltons/") and f.endswith(".py")
                and "__init__" not in f]
        tests = [f for f in files if f.startswith("tests/")]
        if len(srcs) != 1:
            continue                     # 只收单文件修复（判据形态要求）
        if not tests:
            continue                     # 没动测试 ⇒ 多半不是 bug fix
        cands.append((sha[:7], srcs[0], subj))
        if len(cands) >= int(args.limit):
            break

    print(f"  看起来像 bug fix 且单源文件+动过测试：{len(cands)}")

    # ── 2) 逐个分类 ─────────────────────────────────────────────
    rows = []
    for sha, src, subj in cands:
        broken = _git(repo, "show", f"{sha}^:{src}")
        fixed = _git(repo, "show", f"{sha}:{src}")
        if not broken or not fixed:
            rows.append({"sha": sha, "kind": "missing", "detail": "取不到两版源码"})
            continue
        r = classify(broken, fixed)
        r["sha"] = sha
        r["module"] = src
        r["subject"] = subj[:60]
        rows.append(r)

    # ── 3) 逐条记账（⛔ 不许只报总数）────────────────────────────
    print(f"\n{'=' * 74}\n逐条（{len(rows)} 个真实修复）\n{'=' * 74}")
    for r in rows:
        sub = f"/{r['sub']}" if r.get("sub") else ""
        print(f"  {r['sha']}  {r['kind'] + sub:<18} {r['module']:<28} "
              f"{(r.get('detail') or '')[:40]}")

    tally: dict = {}
    subs: dict = {}
    for r in rows:
        tally[r["kind"]] = tally.get(r["kind"], 0) + 1
        if r["kind"] == "operator":
            subs[r.get("sub", "?")] = subs.get(r.get("sub", "?"), 0) + 1
    print(f"\n{'=' * 74}\n形态分布\n{'=' * 74}")
    for k in sorted(tally):
        print(f"  {k:<12} {tally[k]:>4}  ({tally[k] / max(len(rows), 1):.1%})")
    if subs:
        print(f"  operator 细分（⛔ 改算子 vs 改字符串常量不是一回事）：{subs}")

    n_op = tally.get("operator", 0)
    print(f"\n  ⇒ 落在这 5 个 revert_* 射程内的：{n_op}/{len(rows)}")

    # ── 4) 对照组：同一把尺子量 V6 自己造的题 ────────────────────
    ctrl = _control_group(repo)
    print(f"\n{'=' * 74}\n对照组（同一把尺子量 V6 `synthesize_tasks` 造的题）\n"
          f"{'=' * 74}")
    if ctrl is None:
        print("  ⚠️ 对照组没跑成 ⇒ 上面的分布缺一个「尺子准不准」的校准，"
              "读数要打折看")
    else:
        print(f"  V6 自造题 {ctrl['n']} 道，形态分布：{ctrl['tally']}")
        print(f"  ⇒ operator 占比 {ctrl['op_ratio']:.1%}"
              f"（题源与修法族同源 ⇒ 应当接近 100%）")

    out = Path(args.out) if args.out else None
    if out:
        out.write_text(json.dumps({"rows": rows, "tally": tally,
                                   "control": ctrl},
                                  ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\n  明细已写入 {out}")
    return 0


def _looks_like_fix(subj: str) -> bool:
    s = subj.lower()
    return any(w in s for w in ("fix", "resolve", "handle", "correct",
                                "bug", "repair", "regression"))


def _selftest() -> bool:
    """⛔ 量具自检：三条已知答案，判错就是尺子坏了（第 24 条）。

    ① 5eb6cc0 的真实 diff（新增 `if k < 0: k += self.len` + 条件改写）
       ⇒ 必须 structural（这是上一轮唯一进得了判据却没修好的那个 bug）
    ② 纯比较符翻转（`a > b` → `a >= b`）⇒ 必须 operator
    ③ 纯常量翻转（`x = 1` → `x = 2`）⇒ 必须 operator
    ④ 只改变量名（`x` → `y`）⇒ 必须 other（同构但不算修复形态）
    """
    cases = [
        ("def f(k):\n    if k >= n:\n        raise IndexError(k)\n    return m\n",
         "def f(k):\n    if k < 0:\n        k += n\n    if k < 0 or k >= n:\n"
         "        raise IndexError(k)\n    return m\n", "structural"),
        ("def f(a, b):\n    return a > b\n",
         "def f(a, b):\n    return a >= b\n", "operator"),
        ("x = 1\n", "x = 2\n", "operator"),
        ("x = 1\n", "y = 1\n", "other"),
        ("def f(a):\n    return not a\n", "def f(a):\n    return a\n", "operator"),
    ]
    ok = True
    for i, (b, f, want) in enumerate(cases, 1):
        got = classify(b, f)["kind"]
        flag = "✓" if got == want else "✗"
        if got != want:
            ok = False
        print(f"    {flag} 自检{i}: 期望 {want:<11} 实得 {got:<11}"
              f"  （{classify(b, f).get('detail', '')[:36]}）")
    return ok


def _control_group(repo: Path) -> dict | None:
    """⛔ 对照组：用**同一把尺子**量 V6 自己的出题器造出来的题。

    这不是为了证明 V6 厉害 —— 是为了校准尺子。如果 V6 的题（由算子变异
    生成）量出来不是 100% operator，那说明 `classify` 判错了，上面那份
    真实分布就不可信。
    """
    try:
        from aether.environment import Environment, synthesize_tasks
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 导入失败：{type(e).__name__}: {e}")
        return None
    #: 用 **V6 自己的标准题源**（仓库 `tasks/`）当环境：它才是"V6 平时在
    #:    哪类题上跑出 121 次成功提交"的那个世界。⛔ 不能挑挑拣拣 ——
    #:    拿全部环境，否则又是一次"我选的样本"。
    tasks_root = ROOT / "tasks"
    dirs = sorted(p for p in tasks_root.iterdir()
                  if p.is_dir() and (p / "task.py").is_file()
                  and (p / "tests.json").is_file())
    if not dirs:
        print(f"  ⚠️ {tasks_root} 下没有可用环境")
        return None
    tally: dict = {}
    n = 0
    for d in dirs[:12]:
        try:
            env = Environment.from_dir(d)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 跳过 {d.name}：{type(e).__name__}: {e}"[:90])
            continue
        try:
            rep = synthesize_tasks(env, max_n=4, timeout=10.0)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ {d.name} 出题失败：{type(e).__name__}: {e}"[:90])
            continue
        for t in (rep.tasks or []):
            r = classify(t.broken_source, env.source)
            tally[r["kind"]] = tally.get(r["kind"], 0) + 1
            n += 1
    if not n:
        print("  ⚠️ 一个题都没造出来 ⇒ 对照组无从建立")
        return None
    return {"n": n, "tally": tally, "op_ratio": tally.get("operator", 0) / n,
            "envs": len(dirs)}


if __name__ == "__main__":
    sys.exit(main())
