"""环境优先（Environment-First）的任务环境与合成。

2026 的共识（Meta ARE 平台、SWE-smith、R2E-Gym 一致）：
    **agentic 训练/进化的瓶颈不是 GPU，是"环境 + 判据"的供给。**
    SWE-smith 的关键倒置：先建一个**共享**执行环境（O(repos) 而非
    O(instances) 的镜像），再在环境里合成成百上千个任务实例，
    用"是否让既有测试变红"来筛选（execution-based validation）。

V6 照搬这个结构，但缩到可以在本机秒级跑：
    Environment（共享）  ──►  synthesize_task()  ──►  execution 验证（F2P）

四条纪律：
  1. **执行验证优先**：任务是否成立，由"变异后既有测试确实变红"决定，
     不是由"看起来像 bug"决定（SWE-smith 的 Fail-to-Pass 筛选）。
  2. **环境指纹**：每个环境有 fingerprint，判据跨环境不可比
     （V5 教训：口径不同却同表对比）。
  3. **沙箱隔离**：代码执行走 subprocess，超时/崩溃都被记账，
     ⛔ 绝不因"执行器自己挂了"给 reward。
  4. **yield 记账**：合成成功率要统计（合成不出来 = 环境没用），
     不能只看"生成了多少任务"。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ledger import content_hash
from .verifier import Verdict, all_of, error_verdict

PY = sys.executable or "python3"

# 沙箱内执行器：读源码 + 测试 spec，输出 JSON。
# ⛔ 它**不判断**"好不好"，只报"每个断言的实际结果"——判断在 verifier 里。
_RUNNER = r'''
import json, sys, io, traceback, contextlib
src = sys.stdin.read()
payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
tests = payload["tests"]
ns = {"__name__": "task"}
err = None
try:
    exec(compile(src, "task.py", "exec"), ns)
except Exception:
    err = traceback.format_exc()
out = []
for t in tests:
    name = t.get("name", "?")
    if err:
        out.append({"name": name, "passed": False, "evidence": "import-failed: " + err[:200]})
        continue
    try:
        fn = ns.get(t["call"])
        args = t.get("args", [])
        kwargs = t.get("kwargs", {})
        got = fn(*args, **kwargs) if callable(fn) else eval(t["call"], ns)
        if "expect" in t:
            ok = (got == t["expect"])
            ev = f"got={got!r} expect={t['expect']!r}"
        else:
            ok = bool(got)
            ev = f"got={got!r} (truthy check)"
        out.append({"name": name, "passed": bool(ok), "evidence": ev})
    except Exception:
        out.append({"name": name, "passed": False,
                    "evidence": traceback.format_exc().splitlines()[-1][:200]})
print(json.dumps({"results": out, "import_error": err}))
'''


@dataclass(frozen=True)
class Environment:
    """一个共享执行环境 = 源码 + 测试集 + 元信息。"""
    env_id: str
    source: str
    tests: Tuple[Dict[str, Any], ...]
    note: str = ""

    @property
    def fingerprint(self) -> str:
        return content_hash({"env_id": self.env_id, "source": self.source,
                             "tests": list(self.tests)})[:16]

    @staticmethod
    def from_dir(root: str | Path, env_id: str = "") -> "Environment":
        """从目录构建：读 `task.py` + `tests.json`。"""
        root = Path(root)
        src = (root / "task.py").read_text(encoding="utf-8")
        tests = json.loads((root / "tests.json").read_text(encoding="utf-8"))
        if isinstance(tests, dict):
            tests = tests.get("tests", [])
        return Environment(env_id=env_id or root.name, source=src,
                           tests=tuple(tests), note=str(root))

    def to_dict(self, include_source: bool = True) -> Dict[str, Any]:
        return {
            "env_id": self.env_id,
            "fingerprint": self.fingerprint,
            "tests": [dict(t) for t in self.tests],
            "source": self.source if include_source else "",
            "note": self.note,
        }


@dataclass
class ExecResult:
    """一次沙箱执行的原始结果（还不是 Verdict）。"""
    results: List[Dict[str, Any]] = field(default_factory=list)
    import_error: str = ""
    rc: int = -1
    cost_s: float = 0.0
    stdout: str = ""
    stderr: str = ""
    #: 沙箱实际生效情况。⛔ "有沙箱"和"沙箱生效了"是两件事 ——
    #: 这个字段让调用方能分辨，避免把降级当成隔离（V5：绝不静默降级）。
    sandbox: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_passed(self) -> int:
        return sum(1 for r in self.results if r.get("passed"))


# ──────────────────────────────────────────────────────────────────
# 沙箱：进程层加固（跨平台 / 零依赖 / 可实测）
# ──────────────────────────────────────────────────────────────────
# ⛔ 2026-10-13 实测（scripts/probe_sandbox_audit.py）：原来的
#    `subprocess.run(timeout=, cwd=临时目录)` 不是隔离，7 项里 5 项未拦住：
#      · 宿主的 **181 个环境变量全被继承**，其中含 PYAUTOL_VAULT_KEY、
#        TAVILY_API_KEY、WORKBUDDY_PAC_RPC_TOKEN 等**真实密钥**；
#      · 能读 /etc/hosts、能写 /tmp、能 fork 子进程；
#      · 20 MB 输出被 capture_output **全量读进内存**。
#    只有 timeout 对 CPU/内存有效 —— 那条是"超时兜底"，不是隔离。
#    而这里执行的是**合成/模型产出的不可信代码**，所以这是真缺口。

#: 子进程 env **白名单**。⛔ 除这几项外**一律不继承**。
_SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ")

#: 输出上限（字节）。⛔ 不加会 OOM：实测单次 20 MB 全量进内存。
MAX_OUTPUT_BYTES = 4 * 1024 * 1024

#: 地址空间上限（字节）。Python 解释器启动约需 20–80 MB。
RLIMIT_AS_BYTES = 1024 * 1024 * 1024

#: 单文件写入上限（字节）。
RLIMIT_FSIZE_BYTES = 32 * 1024 * 1024


def build_child_env(workdir: str) -> Dict[str, str]:
    """构造子进程的**最小**环境。

    ⛔ 为什么必须做：子进程执行的是不可信代码，而继承宿主 env 会把
       API key / vault key 直接递到它手上（实测 181 个变量，含真密钥）。
       "代码看起来只是在算分"不构成理由 —— 判据只看它**能不能**读到。

    ⛔ 为什么是白名单不是黑名单：黑名单拦不住没见过的名字
       （`WORKBUDDY_PAC_RPC_TOKEN` 这种前缀根本猜不到）。
    """
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.update({
        "HOME": workdir,
        "TMPDIR": workdir,
        "PYTHONHASHSEED": "0",          # 可复现（hash 随机化会让结果漂移）
        "PYTHONDONTWRITEBYTECODE": "1",  # 不往磁盘写 .pyc
        "PYTHONNOUSERSITE": "1",        # 不加载用户 site-packages
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    env.pop("PYTHONPATH", None)         # ⛔ 路径注入的口子，必须掐掉
    env.pop("PYTHONSTARTUP", None)
    return env


def _child_limits(timeout: float, report_path: str) -> Any:
    """子进程的资源上限（在 fork 后、exec 前执行）+ **实测报告**。

    ⛔⛔ 为什么必须有 `report_path`：setrlimit 会**静默失败**。
       2026-10-13 实测 macOS 上 `RLIMIT_AS` 直接抛
       `ValueError: current limit exceeds maximum limit` —— 于是"限制内存"
       这条**根本没生效**，而调用方看到的 `sandbox.rlimits` 却写着 1 GB，
       看起来像一切都设好了。那是**假仪表**（V5 同款病）：
       声明了意图、没验证结果。所以这里把**每一条实际结果**写回文件，
       父进程读出来放进 `sandbox.rlimits_applied`，两者分开显示。

    ⛔ 刻意**不用** `RLIMIT_NPROC`：在 macOS 上它是 **per-uid** 语义，
       会把用户其它进程的计数一起算进去 ⇒ 一旦触发会误杀无关进程。
       限制"只能跑一个进程"这件事靠 OS 层沙箱，不靠这个。

    ⛔ `RLIMIT_CPU` 给 timeout 留缓冲：设得比 timeout 小会先触发
       SIGXCPU，让"超时"和"到点被杀"两种失败混成一种，看不出是哪个。

    ⛔ 整个函数**不许抛**：preexec_fn 抛异常会变成 `SubprocessError`
       让整次执行失败 ⇒ 沙箱问题反而把系统打挂。所以里外都包 try。
    """
    def _apply() -> None:
        import resource
        applied: Dict[str, Any] = {}
        cpu = int(max(1.0, float(timeout))) + 2
        for name, key, val in (
                ("as_bytes", getattr(resource, "RLIMIT_AS", None),
                 RLIMIT_AS_BYTES),
                # ⛔ 备用项：实测 macOS 上 AS / DATA / RSS **三个都**抛
                #    ValueError（宿主已限制，子进程收紧不了）。但在别的
                #    平台上 DATA 可能可用 ⇒ 两个都试，用 `applied` 看哪个
                #    真成了。⛔ 不许因为"AS 通常够用"就只写一条 ——
                #    那样在 AS 不可用的平台上等于没有内存限制。
                ("data_bytes", getattr(resource, "RLIMIT_DATA", None),
                 RLIMIT_AS_BYTES),
                ("fsize_bytes", getattr(resource, "RLIMIT_FSIZE", None),
                 RLIMIT_FSIZE_BYTES),
                ("cpu_s", getattr(resource, "RLIMIT_CPU", None), cpu),
                ("nofile", getattr(resource, "RLIMIT_NOFILE", None), 256),
        ):
            if key is None:
                applied[name] = "unsupported"
                continue
            try:
                resource.setrlimit(key, (val, val))
                applied[name] = True
            except Exception as e:  # noqa: BLE001 —— 记下来，不假装生效
                applied[name] = f"failed: {type(e).__name__}"
        try:
            fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
            try:
                os.write(fd, json.dumps(applied).encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:  # noqa: BLE001
            pass

    return _apply


def _read_capped(path: Path, cap: int) -> Tuple[str, bool]:
    """读文件但**最多** cap 字节，返回 (内容, 是否被截断)。

    ⛔ 截断必须**可见**：静默截断会让"输出巨大"伪装成"输出正常"，
       而解析失败时调用方会误以为是 runner 坏了。
    """
    try:
        with path.open("rb") as f:
            data = f.read(cap + 1)
    except Exception:  # noqa: BLE001
        return "", False
    if len(data) > cap:
        return data[:cap].decode("utf-8", "replace"), True
    return data.decode("utf-8", "replace"), False


def _os_sandbox_cmd(workdir: str) -> Optional[Tuple[List[str], str]]:
    """探测 OS 层沙箱。**能用才返回**，不能返回 None（由调用方记账）。

    ⛔ 刻意做成探测式：2026-10-13 实测，macOS 的 `sandbox-exec` 在
       已沙箱化的宿主里连 `(deny default)` 都 `Operation not permitted`
       （`allow default` 却能跑）⇒ 它不是"没有"，是"这里收紧不了"。
       硬依赖它会让整个执行层在部分环境直接不可用 ⇒ 必须可降级。

    ⛔ 降级**不许静默**：调用方必须把 tier 写进结果并暴露到观测侧。
    """
    import shutil
    import platform

    if platform.system() == "Darwin":
        exe = shutil.which("sandbox-exec")
        if not exe:
            return None
        profile = (
            "(version 1)\n"
            "(deny default)\n"
            "(allow process-exec)\n"
            "(allow process-fork)\n"
            "(allow signal (target self))\n"
            "(allow sysctl-read)\n"
            "(allow file-read* (subpath \"/usr/lib\"))\n"
            "(allow file-read* (subpath \"/usr/local\"))\n"
            "(allow file-read* (subpath \"/opt/homebrew\"))\n"
            "(allow file-read* (subpath \"/Library\"))\n"
            "(allow file-read* (subpath \"/System/Library\"))\n"
            "(allow file-read* (subpath \"/private/tmp\"))\n"
            f"(allow file-read* (subpath {_sb_str(workdir)}))\n"
            f"(allow file-write* (subpath {_sb_str(workdir)}))\n"
        )
        pf = Path(workdir) / "_sandbox.sb"
        try:
            pf.write_text(profile, encoding="utf-8")
        except Exception:  # noqa: BLE001
            return None
        # ⛔ **先自探测一次**：profile 在这个环境到底能不能 apply，
        #    只有真跑一次才知道（实测 deny default 在受限宿主里会失败）。
        try:
            chk = subprocess.run([exe, "-f", str(pf), PY, "-c", "print(1)"],
                                 capture_output=True, text=True, timeout=10)
        except Exception:  # noqa: BLE001
            return None
        if chk.returncode != 0 or "1" not in (chk.stdout or ""):
            return None
        return [exe, "-f", str(pf)], "seatbelt"
    # Linux：bwrap / unshare 存在才用（同样的探测式接入）
    for name, tmpl in (("bwrap", None), ("unshare", None)):
        exe = shutil.which(name)
        if not exe or tmpl is not None:
            continue
        return None  # ⛔ 未实测过 ⇒ 不启用（不声明没验证过的隔离）
    return None


def _sb_str(s: str) -> str:
    """Seatbelt profile 里的字符串字面量（转义，避免路径注入 profile）。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run_in_sandbox(source: str, tests: List[Dict[str, Any]],
                   timeout: float = 10.0) -> ExecResult:
    """跑一次测试。**不做判断**，只报事实。

    ⛔ 崩溃/超时都被如实记账（rc / stderr），绝不返回"通过"。

    ⛔ 隔离分两层，**两层都要如实记账**（`ExecResult.sandbox`）：

        process  —— 跨平台、零依赖、本函数**总是**生效：
                    净化 env（不继承宿主密钥）+ rlimit（内存/写文件/CPU/
                    文件描述符）+ 输出按字节封顶。
        seatbelt —— macOS `sandbox-exec`，**探测式**接入：本函数会先跑一次
                    自探测，失败就**不启用**（实测在已沙箱化的宿主里
                    `deny default` 一律 `Operation not permitted`）。
                    ⛔ 它不生效时**不许静默** —— tier 会落到 `process`
                    并写入结果，让调用方与观测侧看得见。

    ⛔ 注意 `process` 这一层**拦不住文件系统越界**（读 /etc/hosts、
       写 /tmp 仍然可能）。这是诚实的边界，不是"已隔离"；想堵住它
       必须靠 OS 层，而 OS 层在部分环境用不了 —— 所以边界写在这里。
    """
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="v6env_") as td:
        spec = Path(td) / "tests.json"
        runner = Path(td) / "runner.py"
        spec.write_text(json.dumps({"tests": tests}), encoding="utf-8")
        runner.write_text(_RUNNER, encoding="utf-8")

        osb = _os_sandbox_cmd(td)
        cmd = (osb[0] if osb else []) + [PY, str(runner), str(spec)]
        sandbox: Dict[str, Any] = {
            "tier": "seatbelt" if osb else "process",
            "os_sandbox": (osb[1] if osb else None),
            "env_purged": True,
            "fs_isolated": bool(osb),     # ⛔ 只有 OS 层能挡住越界读写
            "rlimits": {"as_bytes": RLIMIT_AS_BYTES,
                        "data_bytes": RLIMIT_AS_BYTES,
                        "fsize_bytes": RLIMIT_FSIZE_BYTES,
                        "cpu_s": int(max(1.0, float(timeout))) + 2,
                        "nofile": 256},
        }

        out_f = Path(td) / "_out.txt"
        err_f = Path(td) / "_err.txt"
        rl_f = Path(td) / "_rlimit.json"
        # ⛔ 写文件而不是 `capture_output=True`：后者会把**全部**输出读进
        #    内存（实测 20 MB 一次），是 OOM 的口子。这里按字节封顶读取。
        try:
            with out_f.open("wb") as fo, err_f.open("wb") as fe:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=fo,
                                     stderr=fe, cwd=td,
                                     env=build_child_env(td),
                                     preexec_fn=_child_limits(timeout,
                                                              str(rl_f)),
                                     close_fds=True)
                try:
                    p.communicate(input=source.encode("utf-8"),
                                  timeout=timeout)
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    p.kill()
                    try:
                        p.communicate(timeout=5)
                    except Exception:  # noqa: BLE001
                        pass
                    rc = 124
        except Exception as e:  # noqa: BLE001
            return ExecResult(rc=1, cost_s=time.time() - t0,
                              stderr=f"{type(e).__name__}: {e}",
                              sandbox=sandbox)

        out, out_cut = _read_capped(out_f, MAX_OUTPUT_BYTES)
        err, err_cut = _read_capped(err_f, MAX_OUTPUT_BYTES)
        sandbox["output_truncated"] = bool(out_cut or err_cut)
        #: ⛔ **实测**生效了什么，与上面的"意图"分开记。两者不一致
        #:    ⇒ 声明的限制是假的（实测 macOS 上 AS 就设不上）。
        try:
            sandbox["rlimits_applied"] = json.loads(
                rl_f.read_text(encoding="utf-8") or "{}")
        except Exception:  # noqa: BLE001
            sandbox["rlimits_applied"] = {"_unreadable": True}
        if rc == 124:
            err = (err or "") + f" | timeout {timeout}s"

    res = ExecResult(rc=rc, cost_s=time.time() - t0, stdout=out, stderr=err,
                     sandbox=sandbox)
    try:
        data = json.loads((out or "{}").strip().splitlines()[-1])
        res.results = data.get("results", [])
        res.import_error = data.get("import_error") or ""
    except Exception:  # noqa: BLE001
        res.stderr = (res.stderr or "") + " | unparsable-runner-output"
    return res


def verify_source(env: Environment, source: Optional[str] = None,
                  timeout: float = 10.0) -> Verdict:
    """对某个源码版本做**可执行判定** —— 这就是 reward 的来源。

    ⛔ evidence 是真实断言结果（got/expect），不是"跑过了"。
    """
    src = source if source is not None else env.source
    try:
        ex = run_in_sandbox(src, list(env.tests), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return error_verdict(f"env/{env.env_id}", e)

    #: ⛔ 每个判定都带上**它是在什么隔离级别下产出的**。
    #:    判定结果的可信程度依赖这一点：一份"通过"如果来自未被 fs 隔离的
    #:    执行，它可能被越界读写污染过 —— 不看 tier 就无法区分。
    sb = dict(ex.sandbox or {})
    sb.pop("rlimits", None)          # 逐条记太吵，tier 才是判读需要的
    if ex.sandbox and not ex.sandbox.get("fs_isolated"):
        _warn_fs_not_isolated_once()

    if ex.rc != 0 or ex.import_error:
        return Verdict(passed=False, checks=(), error=ex.import_error or ex.stderr[:200],
                       evidence={"rc": ex.rc, "stderr": ex.stderr[:200],
                                 "sandbox": sb},
                       env_fingerprint=env.fingerprint, cost_s=ex.cost_s)

    checks = [(r["name"], bool(r["passed"]), str(r.get("evidence", "")))
              for r in ex.results]
    v = all_of(env.env_id, checks)
    return Verdict(passed=v.passed, checks=v.checks,
                   evidence={"n_passed": ex.n_passed, "n_total": len(ex.results),
                             "env_id": env.env_id, "sandbox": sb},
                   env_fingerprint=env.fingerprint, cost_s=ex.cost_s)


#: ⛔ 只提示一次：fs 未隔离是**环境属性**，不是每次执行的事件，
#:    每次都打会把真正的告警淹掉（V5：狼来了）。
_FS_WARNED = False


def _warn_fs_not_isolated_once() -> None:
    global _FS_WARNED
    if _FS_WARNED:
        return
    _FS_WARNED = True
    print("⚠️ 沙箱只到进程层（env 已净化 / 资源受限 / 输出封顶），"
          "文件系统**未隔离**：OS 层沙箱在本环境不可用 ⇒ "
          "被执行代码仍可能越界读写或 fork 子进程。"
          "这是已知边界（见 docs/ARCHITECTURE.md §14）", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────
# 任务合成（SWE-smith 式：变异源码 → 执行验证 → 只留真让测试变红的）
# ──────────────────────────────────────────────────────────────────
_FLIP_OPS = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.GtE: ast.Lt,
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
}


class _Mutator(ast.NodeTransformer):
    """AST 级变异（SWE-smith 的 Procedural Mod 的迷你版）。"""

    def __init__(self, which: int = 0):
        self.which = which
        self.seen = 0
        self.done = False
        self.desc = ""

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        if self.done:
            return node
        self.seen += 1
        if self.seen - 1 == self.which and node.ops:
            old = type(node.ops[0])
            new = _FLIP_OPS.get(old)
            if new is not None:
                node.ops[0] = new()
                self.done = True
                self.desc = f"比较符反转 {old.__name__}→{new.__name__}"
        return node

    def visit_BinOp(self, node: ast.BinOp):
        self.generic_visit(node)
        if self.done:
            return node
        self.seen += 1
        if self.seen - 1 == self.which:
            old = type(node.op)
            new = _FLIP_OPS.get(old)
            if new is not None and not isinstance(node.left, ast.Str):
                node.op = new()
                self.done = True
                self.desc = f"运算符替换 {old.__name__}→{new.__name__}"
        return node

    def visit_Constant(self, node: ast.Constant):
        if self.done or not isinstance(node.value, (int, float)):
            return node
        self.seen += 1
        if self.seen - 1 == self.which:
            node.value = node.value + 1
            self.done = True
            self.desc = f"常量 {node.value - 1}→{node.value}"
        return node


def mutate_source(source: str, which: int = 0) -> Tuple[str, str]:
    """对源码做一次 AST 变异，返回 (新源码, 描述)。

    ⛔ 语法错误 ⇒ 抛异常（不静默返回原文）—— 静默返回原文会造出
    "看起来合成成功但什么都没变"的假任务。
    """
    tree = ast.parse(source)
    m = _Mutator(which)
    new_tree = m.visit(tree)
    ast.fix_missing_locations(new_tree)
    if not m.done:
        raise ValueError(f"没有可变异点（which={which}）")
    return ast.unparse(new_tree), m.desc


@dataclass
class TaskInstance:
    """合成出来的任务实例：一个"坏掉"的环境 + 判据。"""
    task_id: str
    env_id: str
    broken_source: str
    desc: str
    fingerprint: str
    fail_to_pass: Tuple[str, ...] = ()     # 变异后变红的测试名

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "env_id": self.env_id, "desc": self.desc,
                "fingerprint": self.fingerprint, "fail_to_pass": list(self.fail_to_pass)}


@dataclass
class SynthesisReport:
    attempted: int = 0
    valid: int = 0
    rejected_no_failure: int = 0
    rejected_error: int = 0
    tasks: List[TaskInstance] = field(default_factory=list)

    @property
    def yield_rate(self) -> float:
        return (self.valid / self.attempted) if self.attempted else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"attempted": self.attempted, "valid": self.valid,
                "rejected_no_failure": self.rejected_no_failure,
                "rejected_error": self.rejected_error,
                "yield_rate": round(self.yield_rate, 4)}


def synthesize_tasks(env: Environment, max_n: int = 8,
                     timeout: float = 10.0) -> SynthesisReport:
    """从一个环境合成任务实例：**只保留真让测试变红的**（Fail-to-Pass）。

    ⛔ yield 必须统计：合成不出任务 = 这个环境没用，得记账，
    不能拿"尝试次数"冒充"任务数"。
    """
    rep = SynthesisReport()
    before = verify_source(env, timeout=timeout)
    before_names = {c.name for c in before.checks if c.passed}

    for i in range(max_n):
        rep.attempted += 1
        try:
            src, desc = mutate_source(env.source, which=i)
        except Exception:  # noqa: BLE001
            rep.rejected_error += 1
            continue
        try:
            after = verify_source(env, source=src, timeout=timeout)
        except Exception:  # noqa: BLE001
            rep.rejected_error += 1
            continue
        after_names = {c.name for c in after.checks if c.passed}
        broken = tuple(sorted(before_names - after_names))
        if not broken:
            rep.rejected_no_failure += 1
            continue
        rep.valid += 1
        rep.tasks.append(TaskInstance(
            task_id=f"{env.env_id}::m{i}",
            env_id=env.env_id,
            broken_source=src,
            desc=desc,
            fingerprint=content_hash(src)[:16],
            fail_to_pass=broken,
        ))
    return rep
