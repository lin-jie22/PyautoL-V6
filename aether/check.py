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
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["PASS", "FAIL", "SKIP", "TIMEOUT", "ERROR",
           "Run", "discover", "supports_e2e", "is_exclusive", "declared_modes",
           "run_checks", "summarize", "find_node", "cross_language"]

#: ⛔ r58 点名：**本模块的 `FAIL` 与 `doctor.FAIL` 同名不同物**，别合并。
#:    · 这里：契约执行的**五态**（pass/fail/skip/timeout/error）；
#:    · 那边：体检项的**三态**（ok/fail/skipped）。
#:    两边的值都叫 `"fail"`，但 `skip` vs `skipped`、`timeout/error` 的有无
#:    说明它们是两套不同的状态机。合并 ⇒ 契约的超时会被体检读成"没查"。
#:    ⇒ 处置是**点名 + 不许互相 import**（见 `contract_zero_event_paths_test`
#:       的同类处置），不是抽公共模块。
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

#: ⛔ **独占契约的静态声明**：`EXCLUSIVE = True` 的契约**必须串行跑**。
#:    与 `SUPPORTED_MODES` 同为**静态读取**（不 import，理由见 `declared_modes`）。
#:    ⛔⛔ 未声明 ⇒ `False`（并发跑），这与"声明了 False"同义 —— 但**默认并发**
#:        是安全的那一侧：把一份普通契约误判成独占只会慢，把一份性能契约
#:        误判成并发则会**测出别人的负载**（读数不可信，且稳定超时）。
_EXCLUSIVE_RE = re.compile(
    r"^\s*EXCLUSIVE\s*=\s*(True|False)\s*$", re.MULTILINE)

#: 声明"我这份契约要求解释器不低于某版本"的标记。契约里写一行
#:     `MIN_PYTHON = (3, 11)`
#: 即可（不写 ⇒ 不设限，与 `requires-python` 同语义）。
#: ⛔⛔ 为什么由**框架**裁定，而不是让契约自己 `try: import tomllib`：
#:    每份契约各写一套 try 边界，很容易滑成"悄悄跳过"（用户只看到绿）；
#:    而 `check` 记 SKIP 会把这条缺口**计进报告**，skip 数看得见。
#: ⛔ 真实起因：`contract_packaging_test.py` 顶层 `import tomllib`（3.11+），
#:    3.9 上整份 ModuleNotFoundError ⇒ 那份契约在 3.9 上**零覆盖**，
#:    而 `requires-python = ">=3.9"` 是仓库自己声明的 —— 不能靠删 job 糊过去。
_MIN_PY_RE = re.compile(
    r"^\s*MIN_PYTHON\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.MULTILINE)


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


#: 失败输出的"信号行"：契约的判据都在这些形态上留痕。
#: ⛔ 不能只认 `❌`：断言失败会先印 `AssertionError` 和 `!` 比较，
#:    traceback 里也可能只有 `Error:` 一行是真正的原因。
#: ⛔⛔ `✗` / `×` 必须单列：`contract_serve_test.py` 用的是 `✗`，
#:    漏了它 ⇒ 那份契约红了以后**一条判据文字都进不了尾巴**，
#:    只剩"退出码 1"（CI 3.9 上就是这样，看得到 I7/I8/I9/I11 的名字
#:    却看不到任何一个 detail，等于给了四个没有诊断的红点）。
_BAD_LINE_RE = re.compile(
    r"❌|✗|×|\bFAIL\b|\bERROR\b|Traceback|AssertionError|"
    r"\bError:|异常|失败|断言|\bassert\b|!=",
    re.IGNORECASE)


def _summarize_out(out: str, ok: bool, *,
                   max_lines: int = 30, max_chars: int = 1800) -> str:
    """把子进程输出压成**有界但带信号**的诊断尾巴。

    ⛔⛔ 失败时**不能只取末 4 行** —— 本仓契约的结尾几乎都是名字清单或
       汇总行（`✅ 全部通过` / `❌ 3 项失败`），真正的断言细节在更靠上的
       位置。实测代价：CI 首跑 9 份红，报告里全部只留下名字清单，
       看不到是哪条断言断的、断了什么 ⇒ 远端排查等于瞎猜。
    ⛔ 所以失败时保留三类行：**信号行**（含 `❌`/`AssertionError`/`!=`…）、
       信号行**上下各一行**（否则只剩一句孤零零的报错）、以及首尾各若干行
       （traceback 的文件行在头、退出信息在尾）。中间无信号的纯清单行省略，
       并**明确写出省略了多少行** —— 省略本身要可见。
    ⛔ 成功时保持原样（末 4 行）：成功不需要细节，日志要干净。
       "成功时也给一大坨"会让真正的红淹没在绿里。
    """
    lines = out.strip().splitlines()
    if ok:
        return "\n".join(lines[-4:])[:400]
    if not lines:
        return ""

    keep = set(range(max(0, len(lines) - 12), len(lines)))   # 尾部：汇总/退出
    keep |= set(range(0, min(6, len(lines))))                # 头部：traceback
    hit = [i for i, ln in enumerate(lines) if _BAD_LINE_RE.search(ln)]
    for i in hit:
        keep.update((i - 1, i, i + 1))                       # 信号行 + 上下文

    idx = sorted(i for i in keep if 0 <= i < len(lines))
    if len(idx) > max_lines:
        # 头尾都保：只留尾巴会丢掉 traceback 的入口行
        head_n = max(2, max_lines // 5)
        idx = idx[:head_n] + idx[-(max_lines - head_n):]
    body = "\n".join(lines[i] for i in idx)
    if len(body) > max_chars:
        body = body[:max_chars] + " …（超长截断）"
    hidden = len(lines) - len(idx)
    if hidden > 0:
        body = f"（共 {len(lines)} 行，省略 {hidden} 行无信号内容）\n" + body
    return body


def find_node() -> Optional[str]:
    """找 node 可执行文件（**口径只有一份**：`discover.which`）。

    ⛔ 此前这里自己写了一遍"PATH → 项目约定路径 → 常见前缀"的查找顺序，
       而 `aether/discover.py` 需要同一件事时又得写第二遍 ⇒
       实测已经出现不一致：`node` 在 discover 里报 `missing`、
       在这里却找得到（因为项目约定目录不在 PATH 上）。
       "有没有装"取决于你问了哪个模块 —— 这正是本项目反复修的形态。
       ⇒ 现在两边都走 `discover.which`，一个实现。
    ⛔ 只查找、不安装、不下载：找到才跑，找不到就**明确报告未跑**。
    """
    from .discover import which
    return which("node") or None


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
    tail = _summarize_out(out, p.returncode == 0)
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


def min_python(script: Path) -> Optional[Tuple[int, int]]:
    """读契约声明的最低解释器版本；**没声明 ⇒ `None`**（不设限）。

    ⛔ 与 `declared_modes` / `is_exclusive` 同为**静态读取**（不 import）：
       import 契约等于在 check 自己的进程里执行它的顶层断言。
    ⛔ 返回 `None`（不设限）与 `(3, 9)`（下限就是 3.9）是两件事，别合并。
    """
    try:
        txt = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _MIN_PY_RE.search(txt)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def is_exclusive(script: Path) -> bool:
    """这份契约是否声明了「必须独占」（**测墙钟 / 测内存**的那类）。

    ⛔⛔ 为什么需要它（2026-10-17 实测）：
        `contract_ops_test` 与 `contract_streaming_test` 是**测量型**契约
        （前者比接口耗时，后者用 `tracemalloc` 量内存峰值）。把它们和另外
        47 份一起丢进 `ThreadPoolExecutor(jobs=4)` ⇒ 4 路争抢 CPU ⇒
        墙钟时间**按并发度放大**：单独跑 113s / 135s，套件里两次独立运行
        **均 >600s 超时**（`pass=49 fail=0 timeout=2`，rc=1）。

    ⛔ 超时只是最显眼的**症状**。真正的病是：**并发下的性能读数根本不可信**
        —— 即使没超时，量到的也是别的契约的负载，判据等于失效。
        ⇒ 所以不能靠"提 timeout"糊过去，那会把失效藏进十分钟里。

    ⛔ 未声明 ⇒ `False`（并发跑）。这是**安全侧**：普通契约被误判成独占
        只是慢一点；性能契约被误判成并发则是读数失真。
    """
    try:
        txt = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    m = _EXCLUSIVE_RE.search(txt)
    return bool(m and m.group(1) == "True")


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
    tail = _summarize_out(out, p.returncode == 0)
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
    ver_skip: Dict[str, str] = {}
    for s in scripts:
        #: ⛔ 版本闸在**最前面**：低于契约声明的 `MIN_PYTHON` 时它连跑都不该跑
        #:    （跑也是 ModuleNotFoundError，那份契约在被测版本上等于零覆盖）。
        #:    记 SKIP 并写清"谁要求什么版本、当前是什么版本"。
        mp = min_python(s)
        if mp and sys.version_info[:2] < mp:
            ver_skip[s.name] = (f"声明 MIN_PYTHON={mp[0]}.{mp[1]}，当前解释器 "
                                f"{sys.version_info[0]}.{sys.version_info[1]}"
                                f" ⇒ 这份契约在该版本上不适用（不是失败）")
            plan.append((s, "version_skip"))
            continue
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
    #: ⛔ **分段调度**：独占契约（测量型）**串行**，其余按 `jobs` 并发。
    #:    混在一段里跑 ⇒ 性能契约在 CPU 争抢下失真并稳定超时
    #:    （根因与实测见 `is_exclusive` 的 docstring）。
    plan_excl = [(s, m) for (s, m) in plan if m != "version_skip" and is_exclusive(s)]
    plan_par = [(s, m) for (s, m) in plan if m != "version_skip" and not is_exclusive(s)]
    done: Dict[tuple, Run] = {}

    #: ⓪ 版本闸段：**不执行**，直接按声明记 SKIP（原因进 `Run.reason`）。
    for s, _m in plan:
        if _m == "version_skip":
            done[(s.name, "version_skip")] = Run(
                s.name, "default", SKIP, None, 0.0, reason=ver_skip[s.name])

    #: ① 独占段：**一次只跑一份**，不与任何契约（含彼此）并发。
    for s, m in plan_excl:
        r = _one(s, m, root_p, timeout_s)
        done[(r.script, r.mode)] = r

    #: ② 并发段
    if jobs > 1 and len(plan_par) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, int(jobs))) as ex:
            for r in ex.map(lambda t: _one(t[0], t[1], root_p, timeout_s),
                            plan_par):
                done[(r.script, r.mode)] = r
    else:
        for s, m in plan_par:
            r = _one(s, m, root_p, timeout_s)
            done[(r.script, r.mode)] = r

    #: ⛔ **按 plan 原序归位**：并行下结果是乱序返回的，直接 append 会把
    #:    A 的失败记到 B 头上 —— 比慢更糟，那是在撒谎。
    runs = [done[(s.name, m)] for s, m in plan]
    if cross_lang and not only:
        #: ⛔ 只在**全量**时跑：跨语言对账是"整体验收"的一环，
        #:    调一份具体契约时（`--only`）跑它只是浪费。
        runs.append(cross_language(root_p))
    n = {k: sum(1 for r in runs if r.status == k)
         for k in (PASS, FAIL, SKIP, TIMEOUT, ERROR)}
    #: ⛔⛔ 版本闸（`MIN_PYTHON`）带来的新风险，必须自己堵住：
    #:    若**契约级**的 run 全部被跳过（解释器比所有契约声明的下限都老），
    #:    那"全绿"是**空的** —— 一个断言都没执行，却和"全量验过"同形。
    #:    这与上面"0 份契约 ⇒ fail-closed"完全同源，不再重复一遍道理。
    _contract_runs = [r for r in runs if r.mode != "cross-language"]
    all_skipped = bool(_contract_runs) and all(
        r.status == SKIP for r in _contract_runs)
    return {
        "root": str(root_p),
        "n_scripts": len(scripts),
        "n_runs": len(runs),
        "jobs": max(1, int(jobs)),
        "counts": n,
        "runs": [r.to_dict() for r in runs],
        "undeclared_modes": undeclared,
        "e2e_not_supported": not_e2e,
        #: ⛔ 被**串行化**的契约必须可见：否则"全绿"里藏着"这些其实没跟
        #:    别人并发跑过" ⇒ 与全量并发验收不同形，输出却长得一样。
        "exclusive": [s.name for s, m in plan_excl],
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
        #: ⛔ `all_skipped` 与 `empty` 是**同一类假绿的两个入口**：前者是
        #:    "一份都没发现"，后者是"发现了但一份都没执行"。两个都 fail-closed。
        "all_skipped": all_skipped,
        "ok": (bool(scripts) and not all_skipped and n[FAIL] == 0
               and n[TIMEOUT] == 0 and n[ERROR] == 0),
        "exit_code": 0 if (scripts and not all_skipped and n[FAIL] == 0
                           and n[TIMEOUT] == 0 and n[ERROR] == 0) else 1,
    }


def summarize(rep: Dict[str, Any]) -> str:
    """人读摘要（**结论先行**，细节在 `--json` 里）。"""
    c = rep["counts"]
    lines: List[str] = []
    if rep.get("empty"):
        head = "❌ 一份契约都没发现（无从判别 ⇒ 按失败处理）"
    elif rep.get("all_skipped"):
        head = ("❌ 全部契约都被跳过（一个断言都没执行 ⇒ 按失败处理，"
                "不许当绿）")
    else:
        head = "✅ 全部通过" if rep["ok"] else "❌ 有未通过项"
    lines.append(f"{head}：{rep['n_scripts']} 份契约，"
                 f"{rep['n_runs']} 次执行 "
                 f"(pass={c[PASS]} fail={c[FAIL]} skip={c[SKIP]} "
                 f"timeout={c[TIMEOUT]} error={c[ERROR]})")
    #: ⛔ 0 份契约有**两种截然不同的原因**，出路也相反（第 97 条同族）：
    #:    ① 装成 wheel 的用户 —— `tests/` 是仓库资产，刻意不进包。
    #:       他该跑的是 `aether selfcheck`（秒级、随包分发）。
    #:    ② 仓库里的人把 tests/ 弄丢了 —— 那是真事故，要去查。
    #:    合成一句"判失败"会让 ① 以为东西坏了、让 ② 以为正常。
    #:    ⛔ 判据只看"仓库资产在不在"（与 `paths.looks_like_repo` 同源口径），
    #:       不看路径里有没有 site-packages。
    if rep.get("empty"):
        rp = Path(rep.get("root") or ".")
        if not (rp / "aether" / "paths.py").is_file():
            lines.append("   ℹ️ 这里不是 V6 仓库根 ⇒ 多半是装出来的包："
                         "`tests/` 是仓库资产，不进 wheel。")
            lines.append("   要验这套安装是否自洽，跑：aether selfcheck")
        else:
            lines.append(f"   ⛔ 这是 V6 仓库根（{rp}），却没有 tests/ 里的契约 "
                         f"⇒ 仓库不完整，不是安装形态的问题")
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
    #: ⛔ SKIP 不许只留一个数字：`skip=1` 说不了"哪份"和"为什么不适用"。
    #:    跨语言那一步另有专门点名（下面），这里管**契约级**的 SKIP。
    for r in rep["runs"]:
        if r["status"] == SKIP and r["mode"] != "cross-language":
            lines.append(f"  ⏭ {r['script']}：{r['reason']}")
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
