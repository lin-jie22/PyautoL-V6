#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`aether check`：把散落的验收动作收成**一条命令、一个退出码**。

═══ 为什么这是缺的功能（而不是"顺手加个脚本"）═══

收口前，"跑一遍全部验收"这件事在 README 里是一段**手工 `for` 循环**：

    for t in tests/contract_*_test.py; do $PY $t; $PY $t --e2e; done

它有两个致命问题，而这两个问题恰好是本项目自己反复惩罚的形态：

  1. ⛔ **它不是闸**。没有 `set -e`、没有失败聚合 ⇒ shell 的退出码等于
     **最后一次迭代**的退出码 ⇒ 中间任何一份契约变红都被吞掉，
     整轮照样"成功"。而 V6 的第一条命题是"每一次自我修改都必须能被
     独立证伪" —— 验证这个命题的动作本身却不可信。
  2. ⛔ **"两种模式都要跑"是 97% 空转**。36 份契约里只有极少数真正读
     `--e2e`，其余的传了也照跑默认套件 ⇒ 几十次纯重复调用，
     而报告上看起来"两种模式都验过了"。

所以 `check` 的设计目标是三条：

  · **聚合**：任何一项失败 ⇒ 整条命令失败（退出码非 0），且报告里
    逐项写明是哪一项、哪一次、退出码多少。
  · **分类**：`pass` / `fail` / `skip` / `timeout` / `error` 五态分开 ——
    "没跑"与"跑过了没问题"绝不能合并（同族教训：doctor 的 skipped）。
  · **可机读**：`--json` 输出结构化报告，人能看、别的程序也能读。

⛔ 不做的事：`check` **不修**任何东西、**不写**账本、**不改**仓库状态。
   它只回答"现在这套代码通过了哪些验收、哪些没跑、哪些失败了"。
   （同 `doctor` 的纪律：体检与治疗分开，治疗各有命令。）
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = ["PASS", "FAIL", "SKIP", "TIMEOUT", "ERROR",
           "Run", "discover", "supports_e2e", "run_checks", "summarize",
           "find_node", "cross_language"]

PASS = "pass"
FAIL = "fail"
SKIP = "skip"
TIMEOUT = "timeout"
ERROR = "error"

#: 单份契约的默认超时（秒）。⛔ 契约里既有秒级快测也有几十秒的沙箱/REA，
#:    给 600s 是"跑不完 ⇒ 记 timeout"，不是"允许无限等"。
DEFAULT_TIMEOUT_S = 600.0

#: 声明"我这份契约支持 --e2e 模式"的标记。契约里写一行
#:     `SUPPORTED_MODES = ("default", "e2e")`
#: 即可；check 静态读源码，不 import（import 契约会执行它的顶层断言）。
_MODES_RE = re.compile(
    r"^\s*SUPPORTED_MODES\s*=\s*\(([^)]*)\)", re.MULTILINE)


@dataclass
class Run:
    """一次契约执行的读数。"""
    script: str
    mode: str                 # default | e2e
    status: str               # pass | fail | skip | timeout | error
    rc: Optional[int] = None
    seconds: float = 0.0
    reason: str = ""
    tail: str = ""            # 失败时的输出尾部（诊断用，有界）

    def to_dict(self) -> Dict[str, Any]:
        return {"script": self.script, "mode": self.mode,
                "status": self.status, "rc": self.rc,
                "seconds": round(self.seconds, 2),
                "reason": self.reason, "tail": self.tail}


def find_node() -> Optional[str]:
    """找 node 可执行文件（PATH → 项目约定路径 → 常见前缀）。

    ⛔ 与 `tests/contract_traceability_test.py` 的 `_find_node` **同顺序**：
       两处各写一份查找逻辑就会漂移（一处找得到、另一处找不到 ⇒
       "跨语言对账跑不跑"取决于你用哪条命令）。
    ⛔ 只查找、不安装、不下载：找到才跑，找不到就**明确报告未跑**。
    """
    import os
    import shutil
    found = shutil.which("node")
    if found:
        return found
    cands: List[Path] = []
    base = Path.home() / ".workbuddy" / "binaries" / "node" / "versions"
    if base.is_dir():
        cands += [d / "bin" / "node" for d in sorted(base.iterdir())]
    cands += [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node"),
              Path("/Applications/DeepSeek Harness.app/Contents/Resources/"
                   "runtime/primary-runtime/dependencies/node")]
    for c in cands:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def cross_language(root: Path, timeout_s: float = 300.0) -> Run:
    """跑一次 TS 监督器的 selfcheck（跨语言一致性）。

    ⛔⛔ 为什么它必须在 `check` 里：跨语言对账此前**只**由
       `contract_traceability_test` 的一步覆盖，而那一步在本机（node 不在
       PATH 上）会走"找不到 node"分支。也就是说 CI 里很可能**整条跨语言
       判据都没跑，而验收报告仍然全绿** —— 这正是本项目最恨的形态
       （"没跑"伪装成"跑过了没问题"）。
       ⇒ 把它提成 `check` 的**显式一步**：跑了就是 pass/fail，
          node 不在就是 skip **并写明原因**（skip ≠ pass）。
    """
    node = find_node()
    if node is None:
        return Run("supervisor/selfcheck.ts", "cross-language", SKIP, None, 0.0,
                   reason="找不到 node ⇒ 跨语言一致性**未验证**（未跑 ≠ 通过）"
                          "；装 Node ≥22 或用 node 的绝对路径重跑")
    sup = root / "supervisor"
    if not (sup / "src" / "selfcheck.ts").is_file():
        return Run("supervisor/selfcheck.ts", "cross-language", SKIP, None, 0.0,
                   reason=f"没有 {sup / 'src' / 'selfcheck.ts'}")
    t0 = time.time()
    try:
        p = subprocess.run([node, "src/selfcheck.ts"], cwd=str(sup),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Run("supervisor/selfcheck.ts", "cross-language", TIMEOUT, None,
                   time.time() - t0, reason=f"超过 {timeout_s:.0f}s 未结束")
    except OSError as e:
        return Run("supervisor/selfcheck.ts", "cross-language", ERROR, None,
                   time.time() - t0, reason=f"{type(e).__name__}: {e}")
    secs = time.time() - t0
    out = (p.stdout or b"").decode("utf-8", "replace")
    tail = "\n".join(out.strip().splitlines()[-4:])[:400]
    if p.returncode == 0:
        return Run("supervisor/selfcheck.ts", "cross-language", PASS, 0, secs,
                   tail=tail)
    return Run("supervisor/selfcheck.ts", "cross-language", FAIL, p.returncode,
               secs, reason=f"退出码 {p.returncode}", tail=tail)


def discover(root: Path) -> List[Path]:
    """找到全部契约测试（按名排序 ⇒ 报告顺序稳定、可逐次对比）。"""
    d = Path(root) / "tests"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("contract_*_test.py") if p.is_file())


def declared_modes(script: Path) -> Optional[Sequence[str]]:
    """读契约源码里声明的支持模式；**没声明 ⇒ `None`**。

    ⛔ 用**静态读取**而不是 import：契约文件的顶层就是断言（跑到 `sys.exit`），
       import 它们等于执行一遍测试，而且是**在 check 自己的进程里**执行
       —— 一份契约失败会带崩体检本身。
    ⛔ 返回 `None`（未声明）与 `("default",)`（声明只支持默认模式）是
       **两件事**，不能合并：前者是"这份契约还没接线"，报告里要点名；
       后者是"它确实只有一种模式"。把未声明当成"只支持默认"，
       就等于把"没接线"静默变成"接线了"。
    """
    try:
        txt = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _MODES_RE.search(txt)
    if not m:
        return None
    parts = [x.strip().strip("'\"").lower()
             for x in m.group(1).split(",") if x.strip()]
    return tuple(parts) if parts else None


def supports_e2e(script: Path) -> bool:
    """这份契约是否声明支持 `--e2e`（未声明 ⇒ 不跑第二遍）。"""
    modes = declared_modes(script)
    return bool(modes and "e2e" in modes)


def _one(script: Path, mode: str, root: Path,
         timeout_s: float) -> Run:
    """跑一份契约的一次模式。**任何异常都落到 status 上**，不外传。"""
    cmd = [sys.executable, "-B", str(script)]
    if mode == "e2e":
        cmd.append("--e2e")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(root), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return Run(script.name, mode, TIMEOUT, None, time.time() - t0,
                   reason=f"超过 {timeout_s:.0f}s 未结束")
    except OSError as e:
        return Run(script.name, mode, ERROR, None, time.time() - t0,
                   reason=f"{type(e).__name__}: {e}")
    secs = time.time() - t0
    out = (p.stdout or b"").decode("utf-8", "replace")
    tail = "\n".join(out.strip().splitlines()[-4:])[:400]
    if p.returncode == 0:
        return Run(script.name, mode, PASS, 0, secs, tail=tail)
    return Run(script.name, mode, FAIL, p.returncode, secs,
               reason=f"退出码 {p.returncode}", tail=tail)


def run_checks(root: str | Path,
               only: Optional[str] = None,
               modes: Sequence[str] = ("default", "e2e"),
               timeout_s: float = DEFAULT_TIMEOUT_S,
               jobs: int = 1,
               cross_lang: bool = True) -> Dict[str, Any]:
    """跑一遍验收，返回结构化报告。

    `only`：只跑文件名包含该子串的契约（调试用，报告里会写明是子集）。
    `jobs`：并行数。⛔ 契约之间**互不写生产状态**（各自用临时目录），
        所以并行安全；但结果**按输入序归位**（错位会把 A 的失败记到 B
        头上，比慢更糟）。
    ⛔ 子集运行时报告必须**显式标注**：一份"只跑了一份契约"的报告
       与"全绿"长得一样的话，那就是假绿灯。
    """
    root_p = Path(root)
    scripts = discover(root_p)
    if only:
        scripts = [s for s in scripts if only in s.name]
    #: 先算出"每份契约要跑哪几次"，再统一执行 —— 顺序与计划分开，
    #: 结果才能按计划序归位（并行下尤其重要）。
    plan: List[tuple] = []
    undeclared: List[str] = []
    not_e2e: List[str] = []
    for s in scripts:
        modes_decl = declared_modes(s)
        if modes_decl is None:
            undeclared.append(s.name)
        want = list(modes_decl) if modes_decl is not None else ["default"]
        if "default" in modes and "default" in want:
            plan.append((s, "default"))
        if "e2e" in modes:
            if supports_e2e(s):
                plan.append((s, "e2e"))
            else:
                not_e2e.append(s.name)
    if jobs > 1 and len(plan) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, int(jobs))) as ex:
            runs = list(ex.map(lambda t: _one(t[0], t[1], root_p, timeout_s),
                               plan))
    else:
        runs = [_one(s, m, root_p, timeout_s) for s, m in plan]
    if cross_lang and not only:
        #: ⛔ 只在**全量**时跑：跨语言对账是"整体验收"的一环，
        #:    调一份具体契约时（`--only`）跑它只是浪费。
        runs.append(cross_language(root_p))
    n = {k: sum(1 for r in runs if r.status == k)
         for k in (PASS, FAIL, SKIP, TIMEOUT, ERROR)}
    return {
        "root": str(root_p),
        "n_scripts": len(scripts),
        "n_runs": len(runs),
        "jobs": max(1, int(jobs)),
        "counts": n,
        "runs": [r.to_dict() for r in runs],
        "undeclared_modes": undeclared,
        "e2e_not_supported": not_e2e,
        "subset": bool(only),
        "only": only or "",
        #: ⛔ 成败只看 fail/timeout/error —— **skip 不算通过**，
        #:    但它也不该让整条命令红（那是"不可判别 ≠ 有罪"）。
        #:    未声明 e2e 同样不拦，但必须在报告里可见。
        #: ⛔⛔ **0 份契约 = 无从判别 ⇒ fail-closed**（与 doctor 的
        #:    "空套件不许恒绿"同源）。实测踩过：临时根目录下 0 份契约时
        #:    报告是 `✅ 全部通过：0 份契约` —— 一个"什么都没验"的绿色
        #:    结论，与"全量验过"在输出上几乎同形。契约测试是这套系统的
        #:    判据本身，判据为空必须当失败处理。
        "empty": len(scripts) == 0,
        "ok": (bool(scripts) and n[FAIL] == 0 and n[TIMEOUT] == 0
               and n[ERROR] == 0),
        "exit_code": 0 if (scripts and n[FAIL] == 0 and n[TIMEOUT] == 0
                           and n[ERROR] == 0) else 1,
    }


def summarize(rep: Dict[str, Any]) -> str:
    """人读摘要（**结论先行**，细节在 `--json` 里）。"""
    c = rep["counts"]
    lines: List[str] = []
    if rep.get("empty"):
        head = "❌ 一份契约都没发现（无从判别 ⇒ 按失败处理）"
    else:
        head = "✅ 全部通过" if rep["ok"] else "❌ 有未通过项"
    lines.append(f"{head}：{rep['n_scripts']} 份契约，"
                 f"{rep['n_runs']} 次执行 "
                 f"(pass={c[PASS]} fail={c[FAIL]} skip={c[SKIP]} "
                 f"timeout={c[TIMEOUT]} error={c[ERROR]})")
    if rep["subset"]:
        lines.append(f"⚠️ 这是**子集**（--only {rep['only']}）⇒ "
                     f"不能当作全量验收")
    for r in rep["runs"]:
        if r["status"] in (FAIL, TIMEOUT, ERROR):
            lines.append(f"  ❌ [{r['mode']}] {r['script']} "
                         f"{r['reason']}")
            if r["tail"]:
                for ln in r["tail"].splitlines():
                    lines.append(f"       {ln}")
    if rep["undeclared_modes"]:
        lines.append(f"⚠️ {len(rep['undeclared_modes'])} 份契约没声明支持的模式"
                     f"（未声明 ≠ 只有一种模式）⇒ 只按默认模式跑："
                     + "、".join(rep["undeclared_modes"][:6])
                     + ("…" if len(rep["undeclared_modes"]) > 6 else ""))
    #: ⛔ 跨语言那一步被跳过时**必须单独点名**：它是"唯一事实源在两端
    #:    算不算同一个东西"的判据，混在总数里最容易被忽略。
    for r in rep["runs"]:
        if r["mode"] == "cross-language" and r["status"] == SKIP:
            lines.append(f"⚠️ 跨语言一致性**未验证**：{r['reason']}")
    if rep["e2e_not_supported"]:
        lines.append(f"ℹ️ {len(rep['e2e_not_supported'])} 份契约声明不支持 "
                     f"--e2e ⇒ 未跑第二遍（省下的不是覆盖，是重复）")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:      # pragma: no cover
    """独立运行（`python -m aether.check`）：等同 `aether check`。"""
    import argparse
    ap = argparse.ArgumentParser(prog="aether.check",
                                 description="V6 全量验收（一条命令、一个退出码）")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--only", default="", help="只跑文件名含该子串的契约")
    ap.add_argument("--json", action="store_true", help="输出结构化报告")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("-j", "--jobs", type=int, default=1, help="并行数（默认 1）")
    ns = ap.parse_args(argv)
    rep = run_checks(ns.root, only=ns.only or None, timeout_s=ns.timeout,
                     jobs=ns.jobs)
    print(json.dumps(rep, ensure_ascii=False, indent=2)
          if ns.json else summarize(rep))
    return int(rep["exit_code"])


if __name__ == "__main__":                              # pragma: no cover
    sys.exit(main())
