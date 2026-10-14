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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aether.paths import paths                                    # noqa: E402

#: ⛔ 绝对路径，不依赖 PATH：`CliAdapter` 会净化环境变量，而 WorkBuddy /
#:    定时任务等宿主环境的 PATH 里**不一定**有受管 node（实测过：
#:    `#!/usr/bin/env node` 那套在 MCP 启动时会失败）。
NODE = "/Users/linshaojie/.workbuddy/binaries/node/versions/22.22.2-6/bin/node"
REA = ("/Users/linshaojie/.workbuddy/binaries/node/workspace"
       "/node_modules/rea-agents/scripts/rea.mjs")

#: REA 的引擎变量。⛔ **存在才加** —— 写死一个不存在的路径 = 声明了
#:    却没有（假仪表）。引擎没装时这里静默留空，由 REA 自己报 missing。
ENGINE_ENV_CANDIDATES: Dict[str, str] = {
    "GHIDRA_INSTALL_DIR": "/Users/linshaojie/tools/ghidra_12.1.4_PUBLIC",
    "REA_JADX_MCP_JAR":
        "/Users/linshaojie/.local/share/rea/android/"
        "jadx-headless-mcp-0.7.1-all.jar",
}

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
    "analyze": (
        "analyze", "path",
        {"provider": ("--provider", None),
         "integrity_policy": ("--integrity-policy",
                              ("fail", "record-and-continue"))},
    ),
    "capabilities": ("capabilities", None, {}),
    "doctor": ("doctor", None, {"target": ("--target", None)}),
}

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
    """只带**确实存在**的引擎变量。"""
    out: Dict[str, str] = {}
    for k, v in ENGINE_ENV_CANDIDATES.items():
        if os.path.exists(v):
            out[k] = v
    return out


def pinned_command() -> Optional[str]:
    """从**自己的 argv** 里读 `--command`（由能力清单钉死）。

    ⛔ 为什么不让调用方在 stdin 里带 command：那样"能调哪个 REA 命令"
       就由调用方决定了，白名单退化成摆设 —— 清单写了 `analyze`，
       调用方一个 stdin 就能换成 `decompile`。
       钉在 argv 里 ⇒ 换命令必须改清单，而改清单是要过 review 的。
    """
    rest = sys.argv[1:]
    if "--command" in rest:
        i = rest.index("--command")
        if i + 1 >= len(rest):
            raise BridgeError("--command 后面缺值")
        return rest[i + 1]
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
         ) -> Tuple[int, str, str, bool]:
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
            timed_out)


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
    if "error" not in data:
        return data, None
    details = data.get("details") or {}
    po = details.get("partial_observation") or data.get("partial_observation")
    if not isinstance(po, dict) or not po.get("evidence_id"):
        return None, {
            "code": data.get("code"),
            "message": data.get("message"),
            "meaning": "REA 报错且没有可用的部分结果 ⇒ 真失败",
        }
    return po, {
        "code": data.get("code"),
        "message": data.get("message"),
        "resources": details.get("resources"),
        "meaning": ("分析产出了完整 Evidence（已逐字段对拍确认），"
                    "但 REA 未能确认 worker 清理 ⇒ 结果可用，收尾失败"),
    }


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
        print(f"rea_bridge: 找不到 REA 入口 {REA}", file=sys.stderr)
        return 2
    if not os.path.isfile(NODE):
        print(f"rea_bridge: 找不到 node {NODE}", file=sys.stderr)
        return 2

    tms = req.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    try:
        tms = int(tms)
    except Exception:                                      # noqa: BLE001
        tms = DEFAULT_TIMEOUT_MS
    tms = max(1000, min(tms, MAX_TIMEOUT_MS))

    t0 = time.time()
    rc, out, err, timed_out = _run(argv, tms)
    duration_ms = int((time.time() - t0) * 1000)

    #: 全量落盘（**先写后摘要**）：即使后面解析失败，原始输出也留下了。
    outdir = paths(ROOT)["rea"]
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
            "path": str(fpath.relative_to(ROOT)),
            "bytes": len(out.encode("utf-8")),
            "sha256": _sha256_file(fpath),
        },
        "engines": {k: os.path.basename(v)
                    for k, v in _engine_env().items()},
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
    if parse_err:
        env["parse_error"] = parse_err
        env["stderr_tail"] = err[-800:]
    elif result is not None:
        env.update(_summarize(result))

    json.dump(env, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    #: ⛔ 退出码按"**有没有拿到结果**"给，不按 REA 的 rc 原样透传：
    #:    `CliAdapter` 见到 rc≠0 就直接 failed，那会把 cleanup_incomplete
    #:    信封里的完整 Evidence 一起丢掉。降级状态在 `degraded` 里记着，
    #:    一个字都没藏 —— 调用方要看得到。
    return 0 if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
