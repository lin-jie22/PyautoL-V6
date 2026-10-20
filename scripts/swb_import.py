#!/usr/bin/env python3
"""把 SWE-bench Verified 的**真实题**物化成 V6 可跑的环境（r62）。

═══ 为什么要这一步 ═══

V6 的判据形态是 `{"call": <表达式>, "args": [...], "expect": <值>}`
（`environment.py` 的子进程执行器：`call` 可调用就走 `fn(*args)`，不可调用
就走 `eval(t["call"], ns)`）。而 SWE-bench Verified 的判据是 pytest nodeid
+ 需要整仓依赖 + 需要容器。

⛔ 所以"挑 20 题接进来"这个说法本身低估了工程量。本脚本的职责是给出
   **硬数字**：500 题里到底有几题能被这个形态原样吃掉，以及为什么其余的
   不行。数字由 `--screen` 打印，别信任何口头估计。

═══ 三层筛选（每层都记原因）═══

  L1 形态：只改 1 文件、1 个 hunk、改动在函数内、FAIL_TO_PASS <= 4
         ⇒ 260/500
  L2 依赖：目标模块的 import 闭包**全在标准库**（有相对导入也算不行 ——
         相对导入意味着需要整个包）⇒ 6/260
  L3 探针：能写出一条**在模块命名空间里可求值**的表达式，且它在
         gold patch 前后给出**不同**结果 ⇒ 本仓库核定 3/6

═══ ⛔ 判据的真值从哪来（这是本脚本最重要的纪律）═══

`expect` **不是手写的**，而是拿 gold patch **应用后**的源码跑一次探针，
把真实返回值当答案。然后立刻反证：同一探针在 patch **前**的源码上必须
给出不同结果 —— 否则这条探针没有鉴别力，当场丢弃并记账。

⇒ 问法是人工的（知道该问什么），答案是机器校准的（真代码说了算）。
   手写 expect 是在用"我以为的行为"当判据，与"判据必须与源码同源"
   （第 1 条）直接冲突。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────
# 手工核定的探针（"问什么"）
# ──────────────────────────────────────────────────────────────────
#: ⛔ 只写 `expr`，**绝不写 `expect`** —— expect 由 gold 版真跑得出（见文件头）。
#: ⛔ 每条 `expr` 都必须是"在模块命名空间里可直接 eval"的表达式，不许
#:    依赖外部构造（否则物化出来的环境在沙箱里跑不起来）。
PROBE_SPECS: Dict[str, Dict[str, Any]] = {
    "django__django-10914": {
        "env_id": "swb_django_10914",
        "why": "FILE_UPLOAD_PERMISSIONS 默认值从 None 改成 0o644",
        "probes": [
            {"name": "upload_perm_default", "expr": "FILE_UPLOAD_PERMISSIONS"},
            {"name": "upload_perm_is_int", "expr": "isinstance(FILE_UPLOAD_PERMISSIONS, int)"},
        ],
    },
    "django__django-12419": {
        "env_id": "swb_django_12419",
        "why": "SECURE_REFERRER_POLICY 默认值从 None 改成 'same-origin'",
        "probes": [
            {"name": "referrer_policy", "expr": "SECURE_REFERRER_POLICY"},
            {"name": "referrer_policy_is_str",
             "expr": "isinstance(SECURE_REFERRER_POLICY, str)"},
        ],
    },
    "django__django-14089": {
        "env_id": "swb_django_14089",
        "why": "OrderedSet 缺 __reversed__ ⇒ reversed() 抛 TypeError",
        "probes": [
            {"name": "orderedset_reversed",
             "expr": "list(reversed(OrderedSet([1, 2, 3])))"},
            {"name": "orderedset_reversed_str",
             "expr": "list(reversed(OrderedSet(['a', 'b'])))"},
            #: ⛔ 这一题的目标模块**没有模块级常量、也没有无参函数**
            #:    ⇒ `auto_probes` 一条都生成不出来 ⇒ 覆盖率恒为 2 条
            #:    ⇒ 合成 `yield=0.0`（实测）。要让它有产率，只能手工给
            #:    "同一个类的其它行为"补探针 —— 这类模块（纯类库）的
            #:    覆盖率**无法自动**拉起来，必须人给问法。
            {"name": "orderedset_iter", "kind": "coverage",
             "expr": "list(OrderedSet([1, 2, 3]))"},
            {"name": "orderedset_len", "kind": "coverage",
             "expr": "len(OrderedSet([1, 2, 3]))"},
            {"name": "orderedset_contains", "kind": "coverage",
             "expr": "2 in OrderedSet([1, 2, 3])"},
            {"name": "orderedset_discard", "kind": "coverage",
             "expr": "(lambda s: (s.discard(2), list(s))[1])(OrderedSet([1, 2, 3]))"},
            {"name": "orderedset_dedup", "kind": "coverage",
             "expr": "list(OrderedSet([1, 1, 2]))"},
        ],
    },
}


# ──────────────────────────────────────────────────────────────────
# L1 / L2 筛选（与 `--screen` 报告同源）
# ──────────────────────────────────────────────────────────────────
def screen(rows: List[Dict[str, Any]], fetch_src) -> Dict[str, Any]:
    """返回逐层数字 + 每层被刷掉的原因分布。

    ⛔ `fetch_src(row)` 由调用方注入（真跑时打网络、契约时用固定桩）——
       否则这份筛选逻辑在离线环境下无法被验证。
    """
    import ast
    import re

    std = set(sys.stdlib_module_names)
    l1: List[Dict[str, Any]] = []
    for r in rows:
        files = re.findall(r"^diff --git a/(\S+) b/", r["patch"], re.M)
        if len(files) != 1 or not files[0].endswith(".py"):
            continue
        hs = re.findall(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(.*)", r["patch"],
                        re.M)
        if len(hs) != 1 or "def " not in hs[0]:
            continue
        f2p = r["FAIL_TO_PASS"]
        if isinstance(f2p, str):
            f2p = json.loads(f2p)
        if not f2p or len(f2p) > 4:
            continue
        l1.append({"instance_id": r["instance_id"], "file": files[0],
                   "f2p": f2p})
    l2, why2 = [], {"needs_thirdparty": 0, "needs_package": 0, "fetch_fail": 0}
    for c in l1:
        row = next(r for r in rows if r["instance_id"] == c["instance_id"])
        src = fetch_src(row)
        if not isinstance(src, str) or src.startswith("__ERR__"):
            why2["fetch_fail"] += 1
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            why2["fetch_fail"] += 1
            continue
        mods, rel = set(), False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    rel = True
                elif node.module:
                    mods.add(node.module.split(".")[0])
        ext = {m for m in mods if m not in std}
        if rel:
            why2["needs_package"] += 1
        elif ext:
            why2["needs_thirdparty"] += 1
        else:
            l2.append({**c, "imports": sorted(mods)})
    return {"n_total": len(rows), "n_l1": len(l1), "n_l2": len(l2),
            "l2_detail": l2, "rejected": why2,
            "n_l3": len([c for c in l2 if c["instance_id"] in PROBE_SPECS]),
            "l3_ids": sorted(i for i in PROBE_SPECS
                             if any(c["instance_id"] == i for c in l2))}


# ──────────────────────────────────────────────────────────────────
# patch 应用（单 hunk，含纯插入）
# ──────────────────────────────────────────────────────────────────
def apply_hunk(src: str, patch: str) -> Tuple[str, str]:
    """把单 hunk 的 patch 应用到 src 上。返回 (新源码, 说明)。

    ⛔ 只支持**单 hunk**（L1 已筛过）。多 hunk 直接抛错，不猜。
    ⛔ 用 `splitlines()` 对齐而不是整块字符串 `replace`：后者在
       行尾空白/CRLF 不一致时会静默替换到错误位置（改了别处还看着成功）。
    """
    lines = patch.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("@@ "))
    except StopIteration:
        raise ValueError("patch 里没有 hunk")
    old: List[str] = []
    new: List[str] = []
    for ln in lines[start + 1:]:
        if ln.startswith("diff --git") or ln.startswith("@@ "):
            break
        if ln.startswith("---") or ln.startswith("+++"):
            continue
        if ln.startswith("-"):
            old.append(ln[1:])
        elif ln.startswith("+"):
            new.append(ln[1:])
        elif ln.startswith(" "):
            old.append(ln[1:])
            new.append(ln[1:])
        elif ln == "":
            old.append("")
            new.append("")
    if not old:
        raise ValueError("hunk 里没有上下文/删除行 ⇒ 无法定位插入点")
    srclines = src.splitlines()
    hits = [i for i in range(len(srclines) - len(old) + 1)
            if srclines[i:i + len(old)] == old]
    if len(hits) != 1:
        raise ValueError(f"上下文匹配到 {len(hits)} 处（要求恰好 1 处）")
    i = hits[0]
    out = srclines[:i] + new + srclines[i + len(old):]
    return "\n".join(out) + ("\n" if src.endswith("\n") else ""), \
        f"替换 {len(old)} 行 → {len(new)} 行 @ 第 {i + 1} 行"


# ──────────────────────────────────────────────────────────────────
# 真值校准：expect 由 gold 版真跑得出
# ──────────────────────────────────────────────────────────────────
def eval_in_ns(src: str, expr: str) -> Tuple[bool, Any, str]:
    """在模块命名空间里求值一条探针。返回 (ok, 值, 错误)。"""
    ns: Dict[str, Any] = {"__name__": "task"}
    try:
        exec(compile(src, "task.py", "exec"), ns)
    except Exception as e:                              # noqa: BLE001
        return False, None, f"import-failed: {type(e).__name__}: {e}"
    try:
        return True, eval(expr, ns), ""                 # noqa: S307
    except Exception as e:                              # noqa: BLE001
        return False, None, f"{type(e).__name__}: {e}"


def calibrate(spec: Dict[str, Any], src_bug: str,
              src_fixed: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """用 gold 版当真值源，生成判据；再用 bug 版反证它有鉴别力。

    ⛔ 返回两个列表：可用的判据 / 被丢弃的探针（带原因）。丢弃必须可见 ——
       "探针没有鉴别力"与"探针写错了"在处理上完全不同。
    """
    ok_tests: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    for p in spec["probes"]:
        f_ok, f_val, f_err = eval_in_ns(src_fixed, p["expr"])
        if not f_ok:
            dropped.append({"name": p["name"], "reason": f"gold 求值失败：{f_err}"})
            continue
        #: ⛔ 标了 `kind="coverage"` 的探针**不做**"必须区分两版"的反证：
        #:    它们的作用是给同一模块的**其它**行为提供覆盖（让合成器有
        #:    东西可撞），与这一道 bug 无关 ⇒ 在 patch 前也通过是应该的。
        #:    ⛔ 不写这个分支 ⇒ 手工补的同类探针会被"无鉴别力"全部丢弃
        #:    （r62 实测：14089 的 5 条同类探针全灭），覆盖率还是上不去。
        if p.get("kind") == "coverage":
            ok_tests.append({"name": p["name"], "call": p["expr"],
                             "expect": f_val, "kind": "coverage",
                             "why_gold": f"gold 版真值 {f_val!r}"})
            continue
        b_ok, b_val, b_err = eval_in_ns(src_bug, p["expr"])
        #: ⛔ 反证：gold 与 bug 必须不同，否则这条探针没有鉴别力。
        same = (b_ok and _split_types_eq(b_val, f_val)) or \
               (not b_ok and not f_ok and b_err.split(":")[0] ==
                f_err.split(":")[0])
        if same:
            dropped.append({"name": p["name"],
                            "reason": f"无鉴别力（gold 与 bug 同为 {f_val!r}）"})
            continue
        ok_tests.append({"name": p["name"], "call": p["expr"],
                         "expect": f_val,
                         "why_gold": f"gold 版真值 {f_val!r}",
                         "why_bug": (f"bug 版：{b_val!r}" if b_ok
                                     else f"bug 版抛错 {b_err}")})
    return ok_tests, dropped


def _split_types_eq(a: Any, b: Any) -> bool:
    """`True == 1` 在 Python 里成立 ⇒ 判据里必须区分类型（否则探针没牙）。"""
    if type(a) is not type(b):
        return False
    try:
        return a == b
    except Exception:                                   # noqa: BLE001
        return False


def auto_probes(src: str) -> List[Dict[str, str]]:
    """从源码里**自动枚举**能直接求值的探针，把判据覆盖率拉到接近 100%。

    ⛔⛔ 为什么必须有这一步（r62 实测）：手工探针只覆盖"那道 bug 的点"，
       而合成器在**整个模块**上撒变异点。真实文件 22K / 2 条判据 ⇒
       覆盖率 0.9% ⇒ 12 次变异全部 `rejected_no_failure`
       （变异成功，但判据毫无变化）⇒ `yield=0.0`。
       这与 V5 的结论同源：**产率瓶颈是判据覆盖率，不是代码真假**。
       判据覆盖率上去了，同样的源码立刻就有产率。

    能自动生成的只有两类（其余需要人给输入，不猜）：
      · 模块级**常量赋值**（`NAME = <可 JSON 序列化的值>`）⇒ 探针 `NAME`
      · 模块级**无参函数** ⇒ 探针 `foo()`
    ⛔ 有参函数**不生成**：猜参数等于用我编的行为当判据（与"手写 expect"
       同一个错）。宁可覆盖率低，不可判据不真。
    """
    import ast
    out: List[Dict[str, str]] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name.startswith("_"):
                continue
            try:
                val = ast.literal_eval(node.value)
            except Exception:                           # noqa: BLE001
                continue
            if isinstance(val, (bool, int, float, str, list, tuple, dict,
                                type(None))):
                out.append({"name": f"const_{name}", "expr": name})
        elif isinstance(node, ast.FunctionDef) and not node.args.args \
                and not node.args.kwonlyargs and node.name.isidentifier() \
                and not node.name.startswith("_"):
            out.append({"name": f"fn_{node.name}", "expr": f"{node.name}()"})
    return out


def calibrate_coverage(src_fixed: str,
                       probes: List[Dict[str, str]]) -> Tuple[List[Dict[str, Any]],
                                                              List[Dict[str, str]]]:
    """自动探针的验收标准：**只要 gold 版能求值、值可序列化**就收。

    ⛔⛔ 标准与 `calibrate` **必须不同**（r62 实测踩过）：自动探针的用途是
       给合成器提供"能变红的判据"（覆盖率），不是"复现那道 bug"。用
       "gold 与 patch 前必须不同"去卡它们 ⇒ 134/138 条全部被丢弃
       （因为 gold patch 只改了 1 个常量，其余常量两版相同）⇒
       覆盖率没上去，`yield` 仍然是 0。
       ⇒ 两类探针、两套标准：
          手工探针  —— 判据的**正确性**（必须区分两版）
          自动探针  —— 判据的**覆盖面**（必须当前版本为绿）
    """
    out: List[Dict[str, Any]] = []
    dropped: List[Dict[str, str]] = []
    for p in probes:
        ok, val, err = eval_in_ns(src_fixed, p["expr"])
        if not ok:
            dropped.append({"name": p["name"], "reason": f"求值失败：{err}"})
            continue
        try:
            json.dumps(val)
        except (TypeError, ValueError):
            dropped.append({"name": p["name"],
                            "reason": f"值不可序列化：{type(val).__name__}"})
            continue
        out.append({"name": p["name"], "call": p["expr"], "expect": val,
                    "kind": "coverage"})
    return out, dropped


def materialize(instance_id: str, out_dir: Path, row: Dict[str, Any],
                fetch_src, use_auto: bool = False) -> Dict[str, Any]:
    """物化一题：写 `task.py` + `tests.json`（真值校准的判据）。

    ⛔⛔ **写进 `task.py` 的必须是被修好的版本，不是 patch 前的 bug 版**
       （r62 实测踩过）：V6 环境的语义是"**这段代码是对的**，变异它来造题"，
       合成器要的是 F2P（变异**前**通过、变异**后**失败）。
       如果放 bug 版 ⇒ 判据从一开始就 fail ⇒ 永远产不出 F2P
       ⇒ 三个环境 `attempted=12 valid=0 yield=0.0`（实测）。
       这与"基线 0/5 导致 advantage 恒 0"是同一个病：**判据没有可失去的
       东西时，任何改动都测不出差别**。
    """
    spec = PROBE_SPECS[instance_id]
    src_bug = fetch_src(row)
    if not isinstance(src_bug, str) or src_bug.startswith("__ERR__"):
        raise RuntimeError(f"取源码失败：{src_bug[:120]}")
    src_fixed, how = apply_hunk(src_bug, row["patch"])
    #: ⛔ 手工探针在前（它们才是"这道题"的判据），自动探针在后（它们负责
    #:    把覆盖率拉起来）。顺序有意义：报数时先看到的是题目本身。
    #: ⛔ 两类**分开校准**（标准不同，见 `calibrate_coverage` 的说明）。
    tests, dropped = calibrate(
        {"probes": list(spec["probes"])}, src_bug, src_fixed)
    auto_tests: List[Dict[str, Any]] = []
    if use_auto:
        seen = {t["call"] for t in tests}
        auto_tests, auto_dropped = calibrate_coverage(
            src_fixed, [p for p in auto_probes(src_fixed)
                        if p["expr"] not in seen])
        dropped += [{**d, "layer": "auto"} for d in auto_dropped]
        tests = tests + auto_tests
    if not tests:
        raise RuntimeError(f"0 条有效判据（丢弃 {len(dropped)} 条）")
    d = out_dir / spec["env_id"]
    d.mkdir(parents=True, exist_ok=True)
    header = (f'"""真实源码：{row["repo"]} · {instance_id}（已含上游修复）。\n\n'
              f"来源：{row['repo']} @ {row['base_commit'][:12]} + 官方 gold patch\n"
              f"该 patch 修的是：{spec['why']}\n"
              f"应用方式：{how}\n\n"
              f"⛔ 这里是**修好之后**的状态：判据全部通过 ⇒ 合成器可以变异它\n"
              f"   造 F2P 题（变异前通过、变异后失败）。\n"
              f"⛔ 原 bug 形态（patch 前）保留在 tests.json 的 `patch_preimage` 里，\n"
              f"   便于核对「这条判据真的能区分两版」。\n"
              f'"""\n')
    (d / "task.py").write_text(header + src_fixed, encoding="utf-8")
    (d / "tests.json").write_text(json.dumps({
        "env_id": spec["env_id"],
        "note": f"由 gold patch 校准的真判据（{len(tests)} 条）。"
                f"expect 来自修复版真跑，不是手写。",
        "source": {"instance_id": instance_id, "repo": row["repo"],
                   "base_commit": row["base_commit"],
                   "f2p": row["FAIL_TO_PASS"] if isinstance(row["FAIL_TO_PASS"], list)
                   else json.loads(row["FAIL_TO_PASS"])},
        "tests": tests,
        "dropped_probes": dropped,
        #: ⛔ 原 bug 形态留档：校验时要证明"这些判据在 patch 前确实全红"
        #:    （只验"修复版全绿"等于只验了一半）。
        "patch_preimage": src_bug,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"env_id": spec["env_id"], "dir": str(d), "n_tests": len(tests),
            "dropped": dropped, "patch": how}


# ──────────────────────────────────────────────────────────────────
# 校验：bug 版必须红、修好版必须绿
# ──────────────────────────────────────────────────────────────────
def verify_env(env_dir: Path) -> Dict[str, Any]:
    """双向校验：修复版**全绿**；patch 前版本**每条真判据都红**。

    ⛔ 只验一半等于没验（r62 实测）：如果只验"修复版全绿"，那么一批
       恒真判据（例如 `isinstance(x, int)` 在两版上都成立）也会通过 ——
       它们对合成器毫无用处，却看起来"判据都过了"。

    ⛔⛔ "patch 前必须全红"只对 **`kind != coverage`** 的判据成立
       （r62 实测）：自动覆盖率探针的作用是"让合成器有东西可撞"，
       它们与**这一道** bug 无关 ⇒ 在 patch 前当然也通过。
       用同一个标准卡两类 ⇒ 10914/12419 报
       `patch 前 2/136 失败（要求全部）` —— 判定红，而系统其实是对的。
       ⇒ 两类判据、两套标准，这条与 `calibrate_coverage` 的说明同源。
    """
    src = (env_dir / "task.py").read_text(encoding="utf-8")
    spec = json.loads((env_dir / "tests.json").read_text(encoding="utf-8"))
    pre = spec.get("patch_preimage") or ""
    real = [t for t in spec["tests"] if t.get("kind") != "coverage"]

    def run(s: str, ts: List[Dict[str, Any]]) -> int:
        bad = 0
        for t in ts:
            ok, val, err = eval_in_ns(s, t["call"])
            if not (ok and _split_types_eq(val, t["expect"])):
                bad += 1
        return bad

    fixed_bad = run(src, spec["tests"])
    pre_bad = run(pre, real) if pre else -1
    return {"env": env_dir.name, "n": len(spec["tests"]), "n_real": len(real),
            "fixed_fails": fixed_bad, "pre_fails": pre_bad,
            "ok": fixed_bad == 0 and (pre_bad == len(real))}


def _http_fetch(row: Dict[str, Any]) -> str:
    import re
    m = re.search(r"^diff --git a/(\S+) b/", row["patch"], re.M)
    url = (f"https://raw.githubusercontent.com/{row['repo']}/"
           f"{row['base_commit']}/{m.group(1)}")
    req = urllib.request.Request(url, headers={"User-Agent": "aether-swb"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:                              # noqa: BLE001
        return f"__ERR__{type(e).__name__}: {e}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SWE-bench Verified → V6 任务环境")
    ap.add_argument("--rows", default="", help="导出的 parquet 行 JSON（list）")
    ap.add_argument("--out", default="", help="物化输出目录")
    ap.add_argument("--screen", action="store_true", help="只报筛选数字")
    ap.add_argument("--verify", default="", help="校验已物化目录")
    ap.add_argument("--auto-probes", action="store_true",
                    help="自动枚举模块级常量/无参函数当探针（把判据覆盖率拉满）")
    args = ap.parse_args(argv)

    if args.verify:
        bad = 0
        for d in sorted(Path(args.verify).iterdir()):
            if not d.is_dir():
                continue
            r = verify_env(d)
            flag = "✅" if r["ok"] else "❌"
            print(f"{flag} {r['env']}: 修复版 {r['fixed_fails']}/{r['n']} 失败（要求 0）、"
                  f"patch 前 {r['pre_fails']}/{r['n']} 失败（要求全部）")
            bad += 0 if r["ok"] else 1
        return 1 if bad else 0

    if not args.rows:
        ap.error("需要 --rows")
    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    #: ⛔ 只有真要报数时才跑全量筛选：L2 对每题都要**取一次源码**
    #:    （260 次网络 ⇒ 实测 12 分钟）。物化只需要 3 题 ⇒ 别为了让
    #:    一句日志好看而付 12 分钟（成本必须与目的匹配）。
    if args.screen or not args.out:
        rep = screen(rows, _http_fetch)
        print(f"L1 形态符合（单文件/单 hunk/函数内/f2p<=4）：{rep['n_l1']}/{rep['n_total']}")
        print(f"L2 零第三方依赖：{rep['n_l2']}/{rep['n_l1']}  （被刷：{rep['rejected']}）")
        print(f"L3 有可求值探针：{rep['n_l3']}/{rep['n_l2']}  {rep['l3_ids']}")
    if not args.out:
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    by_id = {r["instance_id"]: r for r in rows}
    made = []
    for iid in PROBE_SPECS:
        try:
            made.append(materialize(iid, out, by_id[iid], _http_fetch,
                                    use_auto=args.auto_probes))
        except Exception as e:                          # noqa: BLE001
            print(f"❌ {iid}: {type(e).__name__}: {e}")
    for m in made:
        print(f"✅ {m['env_id']}: {m['n_tests']} 条判据 | {m['patch']}"
              f" | 丢弃 {len(m['dropped'])}")
    return 0 if len(made) == len(PROBE_SPECS) else 1


if __name__ == "__main__":
    sys.exit(main())
