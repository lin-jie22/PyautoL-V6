#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V6 ← REA 桥：把 REA（Reverse Engineer Anything）接成 V6 的 cli 能力。

协议由 `aether.interop.CliAdapter` 规定，**必须**照它来：

    · 本脚本被 argv 起子进程（shell=False，无注入面）；
    · stdin 收到**一个** JSON（= 能力清单声明的 inputs）；
    · stdout 必须吐**恰好一个** JSON（吐不出来会被包成 {"_raw": ...}，
      调用方拿到一个语法合法但结构不对的东西 —— 比报错更糟）；
    · 退出码非 0 ⇒ 适配器判 failed，并把 stderr 前 300 字节带回去。

## 为什么"换通道"而不是"截断"

⛔ `CliAdapter` 输出超预算时**直接报错**，不截断（截断会产出半截 JSON，
   错误伪装成成功）。而 REA 一次分析实测输出 **543KB** —— 还只是个
   5 文件的玩具样本。真实目标只会更大。

    ⇒ 全量结果**落盘**，`stdout` 只回**摘要信封 + 落盘路径**。
      这不是"丢信息"，是换通道：摘要字段全部取自 REA 自己的 Evidence
      信封，一个字都没编造；要全量就按 `full_output.path` 去读。

## 为什么必须白名单命令

⛔ 桥的输入来自外部（stdin JSON）。若把 `command` 直接拼进 argv，
   这个桥就退化成"任意命令执行器"，且因为跑在 V6 进程的子进程里，
   比直接给 shell 更难审计。白名单里**只有**取证过参数形态的命令
   （`rea <cmd> --help` 逐条看过）。

## 为什么要求 path 是绝对路径

⛔ 相对路径会随 cwd 漂移 ⇒ 同一次调用在 `aether.cli` / `aether.serve`
   两个入口下解析到不同文件。与 r36 收布局常量是同一条纪律：
   能漂移的东西不许从外部传。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aether.paths import paths, resolve_root                  # noqa: E402


def _data_root() -> Path:
    """证据该写到**哪个根**下。

    ⛔⛔ 不许用模块级 `ROOT = __file__ 的父父目录`：那是**代码位置**，不是
        **数据位置**。两者在开发态碰巧相同，装成 wheel 之后 `ROOT` 变成
        `site-packages` ⇒ 证据写进 `site-packages/data/rea`（r61 修的 Y109
        在脚本里又活了一遍）。

    ⛔ 顺序由 `paths.resolve_root()` 一处说了算（① --root ② $AETHER_ROOT
        ③ 仓库 ④ 向上找 .aether ⑤ 用户数据目录），本脚本**不另写一份**。
        `CliAdapter` 会在启动子进程时显式注入 `AETHER_ROOT`（它是净化
        env 的，不继承宿主）—— 那条注入就是这里的输入。
    """
    try:
        return resolve_root()
    except Exception:                                      # noqa: BLE001
        return ROOT


def _rel_to_root(p: Path) -> str:
    """把证据路径转成**相对数据根**的短路径（转不了就返回绝对路径）。

    ⛔⛔ 别再用 `relative_to(ROOT)`：那是**代码根**。数据根由
        `$AETHER_ROOT` / `--root` / 安装态决定，与代码根无关 ——
        两者一旦不同，`relative_to` 直接抛 `ValueError`，整条能力 rc=1，
        而报错只说"不在子路径下"（r64 实测：`AETHER_ROOT` 指到临时目录，
        `doctor` / `analyze` / `capabilities` 三条全崩）。
    ⛔ 这是 Y109 的第三种形态：前两次是**写错位置**，这次是**读错根**。
       判据统一成"相对 `_data_root()`"，转不了也不许抛 —— 读数难看
       比整条能力崩掉好（第 97 条：返回值语义要能区分"没读到"与"坏了"）。
    """
    try:
        return str(Path(p).relative_to(_data_root()))
    except (ValueError, OSError):
        return str(p)


#: 受管 node 的**发现**顺序（⛔ 不写死用户名/版本号，见 r67）。
#:
#: 历史：这里原本是三条写死的绝对路径。它们在**本机**是对的，但仓库上传
#: GitHub 后 ⇒ 别人 clone 下来跑 `caps invoke rea.doctor`，得到的是
#: `unavailable: 可执行文件不存在：<别人的家目录>/...`，看起来像"REA 没装"，
#: 实际是"这份代码只在这台机器上成立"。⛔ 而 `rea_bridge` 是**验证过**的能力
#: （契约 A–J 全绿）⇒ 这种不可移植最贵：能力是真的，但只在一个人手里是真的。
#:
#: 顺序：显式 env → PATH（`shutil.which`）→ 受管家目录 → brew/usr 常见位置。
#: ⛔ 每一条都要求 `isfile` 命中，不做"猜一个路径凑合用" —— 拼错一个字符的
#:    绝对路径会表现成"node 不存在"，比空着更难查。
def _first_file(*cands: str) -> str:
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def _glob_latest(*patterns: str, want: str = "file") -> str:
    """取字典序最大（= 版本最高）的**类型正确**的命中，无命中返回空串。

    ⛔ `want` 不是可选项（r67 实测）：`~/tools/ghidra_*` 同时命中
       `ghidra_12.1.4_PUBLIC` 与 `ghidra_12.1.4_PUBLIC.zip`，而排序时
       **`.zip` 更大** ⇒ 不按类型过滤就会把压缩包当成 ghidra 目录，
       然后 `isdir` 失败 ⇒ 引擎静默消失（表现成"这台机器没装 ghidra"）。
    """
    import glob as _glob

    ok = os.path.isdir if want == "dir" else os.path.isfile
    for pat in patterns:
        hits = sorted(h for h in _glob.glob(os.path.expanduser(pat)) if ok(h))
        if hits:
            return hits[-1]
    return ""


def _discover_node() -> str:
    """受管 node 的绝对路径；找不到返回空串（由调用方显式报错）。"""
    env = os.environ.get("V6_NODE", "").strip()
    if env:
        return env if os.path.isfile(env) else ""
    return _first_file(
        shutil.which("node") or "",
        _glob_latest("~/.workbuddy/binaries/node/versions/*/bin/node"),
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        "/usr/bin/node",
    )


def _discover_rea(node: str) -> str:
    """REA CLI（`rea.mjs`）的绝对路径：先 env，再受管 workspace，再 node 同级。"""
    env = os.environ.get("V6_REA_MJS", "").strip()
    if env:
        return env if os.path.isfile(env) else ""
    cands = ["~/.workbuddy/binaries/node/workspace/node_modules/rea-agents/scripts/rea.mjs"]
    if node:
        #: 受管布局：<versions>/<ver>/bin/node ←→ <workspace>/node_modules/...
        cands.append(str(Path(node).resolve().parents[3]
                          / "workspace" / "node_modules" / "rea-agents"
                          / "scripts" / "rea.mjs"))
    cands.append("~/.npm-global/lib/node_modules/rea-agents/scripts/rea.mjs")
    return _first_file(*[os.path.expanduser(c) for c in cands])


NODE = _discover_node()
REA = _discover_rea(NODE)


def _discover_engine_env() -> Dict[str, str]:
    """REA 的引擎变量。⛔ **存在才加** —— 写死一个不存在的路径 = 声明了
    却没有（假仪表）。引擎没装时这里静默留空，由 REA 自己报 missing。"""
    out: Dict[str, str] = {}
    ghidra = os.environ.get("GHIDRA_INSTALL_DIR", "").strip() or _glob_latest(
        "~/tools/ghidra_*", "/opt/ghidra_*", "/usr/local/ghidra_*", want="dir")
    if ghidra and os.path.isdir(ghidra):
        out["GHIDRA_INSTALL_DIR"] = ghidra
    jadx = os.environ.get("REA_JADX_MCP_JAR", "").strip() or _glob_latest(
        "~/.local/share/rea/android/*.jar", "/opt/rea/android/*.jar")
    if jadx:
        out["REA_JADX_MCP_JAR"] = jadx
    return out

#: 命令白名单：bridge 名 → (REA CLI 命令名, 位置参数名 or None, 可选开关)
#: 开关：(CLI flag, 允许的枚举 or None(自由字符串))
#: ⛔ 枚举**必须**与 `rea <cmd> --help` 的输出一致，不照抄二手文档。
COMMANDS: Dict[str, Tuple[str, Optional[str], Dict[str, Tuple[str, Optional[Tuple[str, ...]]]]]] = {
    "analyze_javascript_application": (
        "analyze-javascript-application", "path",
        {"artifact_format": ("--artifact-format",
                             ("auto", "asar", "directory")),
         "integrity_policy": ("--integrity-policy",
                              ("fail", "record-and-continue"))},
    ),
    #: ⛔⛔ 走**这条**才是 jadx 通道，不是 `analyze`（2026-10-15 实测取证）：
    #:    `rea analyze <x.apk>` 的自动候选只有 `["ghidra","hopper","ida"]`
    #:    三个**可执行体** provider，全部以
    #:        "Ghidra v1 imports executable targets, not archive targets."
    #:    拒掉 archive 目标 ⇒ 报 `capability_unavailable`，且 jadx **根本不在候选里**。
    #:    APK 必须走 `inspect-android-package`（其 provider 内部才拉 jadx MCP）。
    "inspect_android_package": ("inspect-android-package", "path", {}),
    "analyze": (
        "analyze", "path",
        {"provider": ("--provider", None),
         "integrity_policy": ("--integrity-policy",
                              ("fail", "record-and-continue"))},
    ),
    "capabilities": ("capabilities", None, {}),
    "doctor": ("doctor", None, {"target": ("--target", None)}),
}

#: ⛔⛔ **隐式依赖**：REA 收尾确认 worker 进程组退出时，会 exec 一次
#:    `ps -axo pid=,ppid=,pgid=,uid=,stat=,command=`（`dist/process/
#:    ProcessOwnershipObservation.js`）。这条依赖**没写在任何文档里**。
#:    在本沙箱里 `ps` 的 exec 被拒（errno = EPERM；`pgrep` 却可用）
#:    ⇒ REA 拿不到进程表 ⇒ fail-closed ⇒ 每次调用都记
#:      `{"code":"cleanup_incomplete","category":"execution_failure"}` 并丢弃结果（rc=1）。
#:    真人很容易在这里误判：去看 `message` 是同一句泛泛的话，看 `reason`
#:    才知道是"读不到进程表"，再往下钻一层才是 `spawn EPERM`。
#:    ⇒ 补一个**真实**的进程表提供器（libproc 实现，零第三方依赖），
#:      并把"到底用的哪个"如实报给调用方（`process_table_source`）。
SHIM_SRC = ROOT / "scripts" / "shims" / "ps_shim.c"
#: ⛔ 探测缓存：本脚本一次调用内可能跑多次，别重复探测。
#:    ⛔ 只缓存**机器级结论**（系统 ps 能不能用），不缓存含路径的返回值。
_PS_PROBE: Dict[str, Any] = {}


def _ps_usable(exe: str) -> bool:
    """这个 `ps` 可执行体**真的**能跑出进程表吗？

    ⛔ 判据必须"跑一次"而不是"文件存在"：本环境里 `/bin/ps` **存在**
       且 `-` 权限位齐全，只是 exec 被沙箱拒 ⇒ 只看 `which` 会误判成可用。
    """
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL",
                                      "TMPDIR") if k in os.environ}
    try:
        proc = subprocess.run([exe, "-p", "1"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=10, shell=False)
        return proc.returncode == 0
    except Exception:                                      # noqa: BLE001
        return False


def _process_table_prefix() -> Tuple[Optional[str], str]:
    """返回 `(要前置到 PATH 的目录 or None, 来源标记)`。

    ⛔ 为什么**只在系统 ps 不可用时**才接管：我们的兼容层只保证
       "进程表列得出来 + 僵尸可判定"，不追求与 ps 的标志位全等。
       系统 ps 能跑就让它跑 —— 那才是权威实现，接管它是自找麻烦。
    """
    key = "prefix"
    if key in _PS_PROBE:
        return _PS_PROBE[key]
    result: Tuple[Optional[str], str] = (None, "system:ps")
    exe = shutil.which("ps")
    if exe is None or not _ps_usable(exe):
        built = _ensure_ps_shim()
        if built is not None:
            result = (built, "shim:libproc")
        else:
            result = (None, "unavailable")
    _PS_PROBE[key] = result
    return result


def _ensure_ps_shim() -> Optional[str]:
    """把 `scripts/shims/ps_shim.c` 编译成可执行文件并返回其**目录**。

    ⛔ 编译失败**不降级** —— 返回 None，由调用方把
       `process_table_source` 记成 `unavailable`。静默降级 = 假仪表。
    ⛔ 产物放 `data/rea_shims`（不入备份：它是投影，缺了现编）。
    """
    outdir = paths(_data_root())["rea_shims"]
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    target = outdir / "ps"
    cc = shutil.which("cc") or shutil.which("clang") or "/usr/bin/cc"
    try:
        proc = subprocess.run(
            [cc, "-O2", "-o", str(target), str(SHIM_SRC)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=120, shell=False)
    except Exception:                                      # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    #: ⛔ 自检不过就当没编出来（宁可报不可用，也不要一张错表）
    try:
        chk = subprocess.run([str(target), "--selftest"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=30, shell=False)
    except Exception:                                      # noqa: BLE001
        return None
    if chk.returncode != 0:
        try:
            target.unlink()
        except OSError:
            pass
        return None
    return str(outdir)


#: 内部超时默认（毫秒）。⛔ 必须**小于**能力清单里的 `timeout_s`，
#:    否则外层先杀，桥连"部分 Evidence"都收不到。清单里写 330s，
#:    这里 300s ⇒ 内部先触发，能拿到 REA 的超时降级结果并记账。
DEFAULT_TIMEOUT_MS = 300_000
MAX_TIMEOUT_MS = 3_600_000


class BridgeError(Exception):
    """输入不合法或环境不齐。**退出码 2**，stderr 带回原因。"""


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _engine_env() -> Dict[str, str]:
    """只带**确实存在**的引擎变量。

    ⛔⛔ `SLEIGHHOME` 是**必需**的第三条，不是可选项（2026-10-15 实测）：
       Ghidra 的原生反编译器（`decompile`）**不看** `GHIDRA_INSTALL_DIR`，
       它从**自己的 argv[0]** 逐级上溯、找名为 `Ghidra` 的目录来定 root，
       找不到就取 `SLEIGHHOME` 兜底。
       当它被 `analyzeHeadless` 从**项目临时目录**里拉起时，cwd 不在安装树下
       ⇒ 上溯必然失败 ⇒ 打印 "Could not discover root of Ghidra installation"
       并 exit 1 ⇒ Java 侧把它读成
           "Decompiler executable may not be compatible ... may need to be rebuilt"
       ⇒ 症状看着像"架构不对要重编"，其实是**root 发现路径不对**。
       装上 `SLEIGHHOME` 后立即通过（实测：空 stdin 时报的是
       "Interface error: Unknown error with input file stream"，
       说明已经过了 root 发现，进入读输入阶段）。
    """
    out: Dict[str, str] = {}
    for k, v in _discover_engine_env().items():
        if os.path.exists(v):
            out[k] = v
    if "GHIDRA_INSTALL_DIR" in out:
        out["SLEIGHHOME"] = out["GHIDRA_INSTALL_DIR"]
    return out


def pinned_command() -> Optional[str]:
    """从**自己的 argv** 里读 `--command`（由能力清单钉死）。

    ⛔ 为什么不让调用方在 stdin 里带 command：那样"能调哪个 REA 命令"
       就由调用方决定了，白名单退化成摆设 —— 清单写了 `analyze`，
       调用方一个 stdin 就能换成 `decompile`。
       钉在 argv 里 ⇒ 换命令必须改清单，而改清单是要过 review 的。

    ⛔⛔ 为什么**不认识的参数必须报错而不是忽略**（2026-10-15 实测）：
       本桥的输入**只有** stdin，argv 里除了 `--command` 什么都没有。
       但 `--params '{...}'` 这种形状看起来完全合理，而旧实现只在
       argv 里找 `--command`、其余**一律静默丢弃** ⇒ 调用方以为参数
       传进去了，实际脚本卡在 `sys.stdin.read()` 等 EOF，
       **表现为"桥挂住了"**，日志里却看不出任何异常。
       ⇒ 与 r36「漏了要抛错不是静默跳过」同一条纪律：
         静默忽略 = 把"参数没生效"伪装成"命令在跑"。
    """
    rest = sys.argv[1:]
    if "--command" in rest:
        i = rest.index("--command")
        if i + 1 >= len(rest):
            raise BridgeError("--command 后面缺值")
        #: ⛔ 严格形态：argv **只能**是 `--command <name>`，多一个位置都不行。
        #:    按**位置**判定而非按 token 内容 —— 值恰好叫 `--command` 也合法。
        expected = {i, i + 1}
        extra = [t for j, t in enumerate(rest) if j not in expected]
        if extra:
            raise BridgeError(
                f"不认识的 argv 参数 {extra}（本桥的输入**只在 stdin**，"
                f"argv 只能是 --command <name>；静默忽略会让调用方误以为参数生效）")
        return rest[i + 1]
    if rest:
        raise BridgeError(
            f"不认识的 argv 参数 {rest}（本桥的输入**只在 stdin**，"
            f"argv 只能是 --command <name>）")
    return None


def build_argv(req: Dict[str, Any]) -> Tuple[List[str], str, Dict[str, Any]]:
    """stdin JSON → REA 的 argv。**任何不合法都抛 BridgeError**（不降级）。"""
    if not isinstance(req, dict):
        raise BridgeError("stdin 必须是一个 JSON 对象")
    cmd = pinned_command() or req.get("command")
    if not isinstance(cmd, str) or not cmd:
        raise BridgeError("缺 command（应由能力清单的 argv 钉死）")
    #: ⛔ 两边都给了却不一致 ⇒ 立刻报，不"以某个为准"静默选一个。
    if req.get("command") and req["command"] != cmd:
        raise BridgeError(
            f"stdin 的 command={req['command']!r} 与清单钉死的 "
            f"{cmd!r} 不一致（以清单为准，需改清单才能换命令）")
    if cmd not in COMMANDS:
        raise BridgeError(
            f"command={cmd!r} 不在白名单里（可选："
            f"{', '.join(sorted(COMMANDS))}）")
    cli_cmd, pos_name, opts = COMMANDS[cmd]

    argv: List[str] = [NODE, REA, cli_cmd]
    target: Dict[str, Any] = {}

    if pos_name:
        raw = req.get(pos_name)
        if not isinstance(raw, str) or not raw:
            raise BridgeError(f"{cmd} 需要 {pos_name}（字符串）")
        if not os.path.isabs(raw):
            #: ⛔ 不接受相对路径：会随 cwd 漂移（见文件头注释）
            raise BridgeError(
                f"{pos_name} 必须是绝对路径，实得 {raw!r}")
        if not os.path.exists(raw):
            raise BridgeError(f"{pos_name} 不存在：{raw}")
        argv.append(raw)
        target = {"path": raw, "kind": "dir" if os.path.isdir(raw) else "file"}

    for key, (flag, allowed) in opts.items():
        if key not in req or req[key] is None:
            continue
        val = str(req[key])
        if allowed is not None and val not in allowed:
            raise BridgeError(
                f"{key}={val!r} 非法（可选：{', '.join(allowed)}）")
        argv += [flag, val]

    #: ⛔ `--format json` 而不是 `--json`：前者是 `rea <cmd> --help` 里
    #:    写明的全局选项，后者只是恰好可用的别名。按**文档写明**的用。
    argv += ["--format", "json"]
    return argv, cmd, target


def _run(argv: List[str], timeout_ms: int
         ) -> Tuple[int, str, str, bool, str]:
    """跑 REA。

    ⛔⛔ **不要** `start_new_session=True`（实测踩过）：REA 的 JavaScript
       分析器是常驻 worker，它靠进程关系回收；脱离进程组后清理无法确认
       ⇒ REA 直接报 `cleanup_incomplete`（execution_failure，rc=1），
       一次成功的分析变成失败。**为了"杀得干净"把功能弄坏，是倒过来的。**

    ⛔ 也因此**不能** `killpg`：本脚本与宿主（V6 进程）同组，
       `killpg(getpgid(proc.pid))` 会把宿主一起杀掉 —— 那是事故不是清理。
       超时时先 SIGTERM 让 REA 自己收 worker，等 5s 再 SIGKILL。
    """
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")
        if k in os.environ
    }
    env.update(_engine_env())
    #: ⛔ 系统 `ps` 不可用 ⇒ 前置我们的兼容层（见 `SHIM_SRC` 的取证注释）。
    shim_dir, pt_source = _process_table_prefix()
    if shim_dir is not None:
        env["PATH"] = shim_dir + os.pathsep + env.get("PATH", "")

    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, shell=False, cwd=str(ROOT),
    )
    timed_out = False
    try:
        out_b, err_b = proc.communicate(timeout=timeout_ms / 1000.0)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()                    #: 给 REA 一个收尾的机会
        try:
            out_b, err_b = proc.communicate(timeout=5)
        except Exception:                                  # noqa: BLE001
            proc.kill()
            try:
                out_b, err_b = proc.communicate(timeout=10)
            except Exception:                              # noqa: BLE001
                out_b, err_b = b"", b""
    return (proc.returncode,
            out_b.decode("utf-8", "replace"),
            err_b.decode("utf-8", "replace"),
            timed_out,
            pt_source)


def _parse_rea_json(txt: str) -> Tuple[Optional[Any], Optional[str]]:
    """REA 的 stdout 可能是"一行巨 JSON"，也可能混了进度行。

    ⛔ 不猜形态：先整体解析，失败则从**最后一个**可解析行取
       （进度行走 stderr，所以正常情况整体就能解析；这里只是兜底）。
    """
    txt = txt.strip()
    if not txt:
        return None, "stdout 为空"
    try:
        return json.loads(txt), None
    except Exception:                                      # noqa: BLE001
        pass
    for line in reversed(txt.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line), None
        except Exception:                                  # noqa: BLE001
            continue
    return None, "stdout 里没有可解析的 JSON 行"


def _is_error_envelope(data: Dict[str, Any]) -> bool:
    """这是 REA 的**错误信封**吗？

    ⛔⛔ 判据不能只认 `error` 键（2026-10-15 实测取证）。REA 在同一条
       CLI 路径上会吐两种形态的错误信封：

        ① ``{"error":"Analysis failed","code":"capability_unavailable",
             "category":"unsupported_provider","details":{...}}``
        ② ``{"code":"cleanup_incomplete","category":"execution_failure",
             "details":{...}}``            ← **没有 `error` 键**

      形态 ② 出现在"分析跑完了但收尾确认不了"时（本机 `ps` 被环境拒绝）。
      只认 `error` 键 ⇒ 形态 ② 被当成成功结果 ⇒ 顶层 `ok=true` 而分析
      实际上失败了 ⇒ 观测侧恒亮绿灯，正是宪法禁止的假仪表。

    ⇒ 统一判据：**信封里没有 `evidence_id` 且带 `code`/`category` ⇒ 错误**。
      成功的结果信封**一定**带 `evidence_id`（REA 的 Evidence 契约）。
      这个判据与形态无关，REA 再换信封包法也不会失效。
    """
    if "evidence_id" in data:
        return False
    return "error" in data or "code" in data or "category" in data


def _extract_result(data: Any) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """从 REA 的输出里取"结果信封"，并如实记下降级状态。

    ⛔ 为什么要容忍 `cleanup_incomplete`（2026-10-14 实测取证）：
       REA 在分析**成功**的情况下仍可能返回 rc=1 的错误信封，错误码是
       `cleanup_incomplete`，而信封的 `details.partial_observation` 里带着
       **完整的 Evidence** —— 与同一目标成功那次的信封逐字段对拍一致
       （evidence_id / confidence / authority / predicate_type / operation /
       summary / statistics 全等）。

       两种处理都是错的：
         · 丢掉 ⇒ 把一份有效 Evidence 扔了，能力永远"失败"；
         · 说成完全成功 ⇒ 骗人（REA 确实报了 execution_failure）。
       ⇒ 取出来用，并在 `degraded` 里把码、原文、残留资源原样记下。

    ⛔ 只对 `partial_observation` 里**有 evidence_id** 的情况放行：
       没有 evidence_id 说明连部分结果都没有，那才是真失败。
    """
    if not isinstance(data, dict):
        return data, None
    #: ⛔⛔ **错误信封有两种形态**，只认 `error` 键会漏掉第二种
    #:    （2026-10-15 对本机 APK 实测取证）：
    #:      ① `{"error":"Analysis failed","code":...,"details":{...}}`
    #:      ② `{"code":"cleanup_incomplete","category":"execution_failure",
    #:          "details":{...}}`   ← **没有 `error` 键**
    #:    旧判据 `if "error" not in data: return data, None` 遇到形态 ②
    #:    会把**失败信封原样当成结果返回** ⇒ 顶层 `ok=true`、且 `degraded`
    #:    为 None ⇒ 绿灯亮着而分析根本没成（宪法禁的假仪表）。
    #:    ⇒ 判据改为"**信封里有没有 evidence_id**"：有 ⇒ 是结果；
    #:      没有且带 `code`/`category` ⇒ 是错误。这个判据对两种形态都成立。
    if not _is_error_envelope(data):
        return data, None
    details = data.get("details") or {}
    po = details.get("partial_observation") or data.get("partial_observation")
    if not isinstance(po, dict) or not po.get("evidence_id"):
        #: ⛔ `reason` 两个分支都**必须**带 —— 宪法第 73 条说的是
        #:    "降级 / 失败读数必须带 reason"，不是"降级成功才带"。
        #:    真失败时调用方最想知道的恰恰是"该怪谁"，这里漏了就等于白修。
        return None, {
            "code": data.get("code"),
            "message": data.get("message"),
            "reason": _find_reason(details),
            "worker_pid": _find_key(details, "worker_pid", max_depth=6),
            "meaning": "REA 报错且没有可用的部分结果 ⇒ 真失败",
        }
    #: ⛔ `reason` 必须带出来（2026-10-14 取证）：REA 给调用方的 `message`
    #:    是**同一句泛泛的话**，真正的机器可读原因在
    #:    `details.diagnostics.reason`（回退 `details.cleanup_reason`）。
    #:    本沙箱里实测到的真因是
    #:        "process table could not be inspected: spawn EPERM"
    #:    即 REA 收尾时靠 `ps` 查进程表确认 worker 已退出，而 `ps`
    #:    在此环境被拒（与 `pgrep` 可用是同一条已知坑）。
    #:    ⇒ 这是**环境限制**，不是桥的缺陷；不带 reason 就没人知道该怪谁。
    return po, {
        "code": data.get("code"),
        "message": data.get("message"),
        "reason": _find_reason(details),
        "worker_pid": _find_key(details, "worker_pid", max_depth=6),
        "resources": details.get("resources"),
        "meaning": ("分析产出了完整 Evidence（已逐字段对拍确认），"
                    "但 REA 未能确认 worker 清理 ⇒ 结果可用，收尾失败"),
    }


def _walk(node: Any, depth: int, max_depth: int):
    """有限深度遍历（⛔ 只走 dict/list，且**有界**，防止病态信封）。"""
    if depth > max_depth:
        return
    if isinstance(node, dict):
        for v in node.values():
            yield v
            yield from _walk(v, depth + 1, max_depth)
    elif isinstance(node, list):
        for v in node:
            yield v
            yield from _walk(v, depth + 1, max_depth)


def _find_key(data: Any, key: str, max_depth: int = 6) -> Any:
    """在信封里找**第一个**非空同名键 —— 嵌套位置会随 REA 版本漂移，
       而"有没有这个读数"才是要守的不变量。"""
    if isinstance(data, dict) and data.get(key) not in (None, "", [], {}):
        return data[key]
    for value in _walk(data, 1, max_depth):
        if isinstance(value, dict) and value.get(key) not in (None, "", [], {}):
            return value[key]
    return None


def _collect_reasons(node: Any, depth: int, max_depth: int,
                     out: List[Tuple[int, str]]) -> None:
    """把信封里**所有**非空 `reason` 连深度一起收上来。"""
    if depth > max_depth:
        return
    if isinstance(node, dict):
        val = node.get("reason")
        if isinstance(val, str) and val.strip():
            out.append((depth, val.strip()))
        for child in node.values():
            _collect_reasons(child, depth + 1, max_depth, out)
    elif isinstance(node, list):
        for child in node:
            _collect_reasons(child, depth + 1, max_depth, out)


def _find_reason(details: Dict[str, Any]) -> Optional[str]:
    """取出**最深**的那条 `reason` —— 那才是真因。

    ⛔ REA 会把真因包在好几层泛泛的壳里（2026-10-15 实测）：

        details.diagnostics.reason                 ← 浅，只是壳
          = "Provider jadx adapter failed during close_binary"
        details.diagnostics.provider_cleanup.reason ← 深，真因
          = "process table could not be inspected: spawn EPERM"

      只取第一条 ⇒ 拿到壳 ⇒ 调用方照样不知道该怪谁（等于没修）。
      ⇒ 收集全部 `reason` 取**最深**的一条（同深度取更长者）：
        外层说的是"谁失败了"，内层才是"为什么失败"。
    """
    found: List[Tuple[int, str]] = []
    _collect_reasons(details, 0, 6, found)
    if not found:
        return details.get("cleanup_reason")
    return max(found, key=lambda item: (item[0], len(item[1])))[1]


def _summarize(data: Any) -> Dict[str, Any]:
    """从 REA 的 Evidence 信封抽摘要。

    ⛔ 所有字段**原样取自 REA**，不做推断、不补默认值 —— 桥的职责是搬运，
       不是解释。缺了就是缺了（调用方能从 `result_keys` 看出结构不对）。
    """
    out: Dict[str, Any] = {}
    if not isinstance(data, dict):
        return {"result_type": type(data).__name__}
    for k in ("evidence_id", "provider", "confidence", "authority",
              "limitations", "relationship_coverage"):
        if k in data:
            out[k] = data[k]
    nr = data.get("normalized_result")
    if isinstance(nr, dict):
        for k in ("input_path", "format", "root_artifact_sha256",
                  "summary", "statistics", "integrity_contradictions"):
            if k in nr:
                out[k] = nr[k]
    out["result_keys"] = sorted(data.keys())
    return out


def main() -> int:
    try:
        raw = sys.stdin.read()
        req = json.loads(raw) if raw.strip() else {}
    except Exception as ex:                                # noqa: BLE001
        print(f"rea_bridge: stdin 不是合法 JSON：{ex}", file=sys.stderr)
        return 2

    try:
        argv, cmd, target = build_argv(req)
    except BridgeError as ex:
        print(f"rea_bridge: {ex}", file=sys.stderr)
        return 2

    if not os.path.isfile(REA):
        print(f"rea_bridge: 找不到 REA 入口 {REA or '(未发现)'} —— "
              f"装 rea-agents 或设 V6_REA_MJS", file=sys.stderr)
        return 2
    if not os.path.isfile(NODE):
        print(f"rea_bridge: 找不到 node {NODE or '(未发现)'} —— "
              f"装 node 或设 V6_NODE", file=sys.stderr)
        return 2

    tms = req.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    try:
        tms = int(tms)
    except Exception:                                      # noqa: BLE001
        tms = DEFAULT_TIMEOUT_MS
    tms = max(1000, min(tms, MAX_TIMEOUT_MS))

    t0 = time.time()
    rc, out, err, timed_out, pt_source = _run(argv, tms)
    duration_ms = int((time.time() - t0) * 1000)

    #: 全量落盘（**先写后摘要**）：即使后面解析失败，原始输出也留下了。
    outdir = paths(_data_root())["rea"]
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}__{cmd}__{os.getpid()}.json"
    fpath = outdir / name
    fpath.write_text(out, encoding="utf-8")

    parsed, parse_err = _parse_rea_json(out)
    #: ⛔ `rc` 与 `ok` **分开记**：REA 会在分析成功但收尾失败时返回 rc=1
    #:    且信封里带着完整 Evidence（见 `_extract_result` 的取证）。
    #:    ⇒ `returncode` 原样保留（不美化），`ok` 判"有没有拿到结果"。
    result, degraded = _extract_result(parsed)

    env: Dict[str, Any] = {
        "ok": result is not None and not timed_out,
        "bridge": "rea_bridge/1",
        "command": cmd,
        "argv": argv[2:],                    #: 去掉 node 与 rea.mjs 两个前缀
        "target": target,
        "returncode": rc,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "timeout_ms_effective": tms,
        "full_output": {
            #: ⛔⛔ 相对谁？必须相对**数据根**（`_data_root()`），不是代码根
            #:    （`ROOT`）。此前写死 `relative_to(ROOT)`：数据根与代码根
            #:    一旦分开（`$AETHER_ROOT` / 安装态 / 指定工作区），
            #:    这一行直接抛 `ValueError` ⇒ 整条能力 rc=1，
            #:    而错误信息只说"不在子路径下"，看不出是路径口径错了
            #:    （Y109 第三次换衣服：前两次是"写错位置"，这次是"读错根"）。
            "path": _rel_to_root(fpath),
            "bytes": len(out.encode("utf-8")),
            "sha256": _sha256_file(fpath),
        },
        "engines": {k: os.path.basename(v)
                    for k, v in _engine_env().items()},
        #: ⛔ 声明与实际的落差必须记账：调用方得知道这次"进程表"是谁给的。
        #:    `shim:libproc` ⇒ 系统 ps 被环境拒绝，用的兼容层。
        #:    `unavailable` ⇒ 两者都没有 ⇒ REA 必然 `cleanup_incomplete`。
        "process_table_source": pt_source,
    }
    #: ⛔⛔ `ok=true` **只表示"拿到结果了"，不表示"引擎齐全"**（2026-10-14 实测）。
    #:    `rea doctor` 在 Ghidra / IDA / Hopper 全缺的情况下**照样**返回一份
    #:    结构完整的信封，只是里面 `healthy=false`、`environment_healthy=false`。
    #:    若不把它单独暴露，调用方看到 `ok=true` 就会读成"引擎都在" ⇒ 假仪表。
    #:    ⇒ 有这个字段就**原样**搬出来（只搬 REA 自己给的布尔，不推断、不兜底）。
    if isinstance(result, dict) and "healthy" in result:
        env["rea_self_check"] = {
            "healthy": result.get("healthy"),
            "environment_healthy": result.get("environment_healthy"),
        }
        if result.get("healthy") is False:
            missing = [c.get("name") for c in (result.get("checks") or [])
                       if isinstance(c, dict) and c.get("ok") is False]
            env["rea_self_check"]["failing_checks"] = missing
    if degraded:
        env["degraded"] = degraded
    #: ⛔ 超时**单独开一个读数**，不许混进 `degraded`：
    #:    degraded = REA 跑完了但收尾没确认（结果可用）；
    #:    timed_out = REA 在这个环境里没跑完（结果没有）。
    #:    两者都让 ok=false，但归因完全不同 ⇒ 混在一个字段里，调用方
    #:    会把"我们等不到"当成"REA 判失败"，然后去改根本没错的地方。
    if timed_out:
        env["timeout_reason"] = (f"桥在 {tms} ms 后杀掉 REA，未拿到结果；"
                                 f"这不是 REA 的判定，是我们的等待上限")
    if parse_err:
        env["parse_error"] = parse_err
        env["stderr_tail"] = err[-800:]
    elif result is not None:
        env.update(_summarize(result))

    json.dump(env, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")

    #: ⛔⛔ 失败时**必须**把诊断写进 stderr（2026-10-15 取证）：
    #:    REA 把错误信封吐在 **stdout** 上（实测 stdout 13KB、stderr 0 字节），
    #:    而 `CliAdapter` 只在 rc≠0 时把 **stderr 前 300 字节**带回去
    #:    ⇒ 不写 stderr，调用方拿到的就只有一句 "CLI 退出码 1："，
    #:      `code` 与真因 `spawn EPERM` 全部丢失 ⇒ 「失败必须能分诊」在桥这层是断的。
    #:    ⛔ 且**必须按分诊价值排序**：code / reason 放最前 —— 被 300 字节
    #:      截断时先丢的应是冗余上下文，不是归因。
    if result is None:
        dg = degraded or {}
        diag: Dict[str, Any] = {
            "code": dg.get("code") or parse_err or f"rc={rc}",
            "reason": str(dg.get("reason") or dg.get("message") or "")[:140],
            "cmd": cmd,
            "rea_rc": rc,
            "timed_out": timed_out,
            "full_output": env["full_output"]["path"],
        }
        print("rea_bridge: failed "
              + json.dumps(diag, ensure_ascii=False), file=sys.stderr)

    #: ⛔ 退出码按"**有没有拿到结果**"给，不按 REA 的 rc 原样透传：
    #:    `CliAdapter` 见到 rc≠0 就直接 failed，那会把 cleanup_incomplete
    #:    信封里的完整 Evidence 一起丢掉。降级状态在 `degraded` 里记着，
    #:    一个字都没藏 —— 调用方要看得到。
    return 0 if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
