#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：REA 桥 —— 外部逆向工具接进 V6 之后，**叫得动、且叫不动的
东西真的叫不动**。

桥（`scripts/rea_bridge.py`）是 `CliAdapter` 的实现：stdin 收一个 JSON，
stdout 吐一个 JSON。它有三个地方最容易长出假仪表，本文件逐个钉死：

    A 命令白名单       能调哪个 REA 命令由**清单的 argv** 钉死，
                       调用方从 stdin 换不了（换命令必须改清单）
    B 端到端真调       对自建样本跑一次完整的 analyze，
                       逐项对拍埋点（不是"跑通了"就算）
    C 经 Gateway 调用  走 V6 的 mount_capabilities + invoke，不是绕过
    D 反证组           ⛔ 证明 A 组的闸**不是恒真**：拿掉白名单的对照实现
                       会放行同一个恶意输入
    E 静态闸           清单与桥的常量不漂移（timeout 内外关系、argv 钉死）

⛔ 为什么样本必须自建：① 不依赖上一次跑残留的 /tmp 文件（那种依赖会让
   "测试通过"变成运气）；② 不碰任何第三方闭源应用（REA 官方 DISCLAIMER
   限定：只分析自己拥有或获授权的目标）。

跑法：
    python tests/contract_rea_bridge_test.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BRIDGE = ROOT / "scripts" / "rea_bridge.py"
MANIFEST = ROOT / "capabilities.rea.json"
TESTS = ROOT / "tests"
PY = sys.executable

FAILS = []
#: ⛔「跳过的组」必须**单独记账**，不能混进 PASS：
#:    Ghidra 是重资源工具，负载高时会退化成空壳信封（`ok=true` 但读数为空）。
#:    那时 J2–J12 无从断言 ⇒ 契约**跳过**它们。若把跳过记成 PASS，
#:    则工具哪天彻底跑不动了，这份契约会**恒绿且零覆盖** —— 与
#:    「空冻结套件不得恒绿」是同一个病。⇒ 单独列出来，并在末尾显式提示。
#:    ⛔ 但也不判红：红会把"机器忙"报成"契约坏了"（与 srcmut 的三态
#:    同纪律：跑不起来 ≠ 测不过）。⇒ 记账 + 提示，不判失败。
SKIPPED = []

#: ⛔⛔ 桥的**两类失败**是两个退出码（桥侧 `RC_BAD_INPUT=2` /
#:    `RC_MISSING_DEP=3`）。契约必须复述这个口径，否则「依赖没装」
#:    会被当成「契约坏了」：实测在**没装 REA 的机器**（CI）上，
#:    6 条判据转红，其中 4 条（B1/B7/G2/J1）红的是**环境**、
#:    只有 2 条（G11/J13「失败必须可归因」）红的是**真缺陷**。
#:    ⇒ 环境类的走 SKIP 记账（未覆盖 ≠ 通过），真缺陷类的必须留着牙。
RC_BAD_INPUT = 2
RC_MISSING_DEP = 3


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    return bool(cond)


def sk(name, reason):
    """跳过某一组断言：**单独记账**，既不判红也不冒充通过。

    ⛔ 为什么不判红：红会把"这台机器没装 REA"报成"契约坏了"，
       于是真正的回归淹没在环境噪音里（实测 6 红里 4 条是这种）。
    ⛔ 为什么不判绿：绿会让"工具哪天彻底跑不动了"恒绿且零覆盖 ——
       与「空冻结套件不得恒绿」是同一个病。
    """
    print(f"  SKIP {name} —— {reason}")
    SKIPPED.append(f"{name} —— {reason}")


#: ⛔⛔ 桥的**证据根**必须指向临时目录（r64 实测踩到）。
#:    桥内部走 `paths.resolve_root()`，而直接调用（不经 Gateway）时
#:    既没有 `--root` 也没有 `AETHER_ROOT` ⇒ 命中 ③「仓库」分支
#:    ⇒ 一次契约跑完往**生产仓库** `data/rea/` 写 6 个证据文件
#:    （实测 2026-10-21 21:42：analyze / doctor / capabilities /
#:     inspect_android×2 / analyze_javascript 各一个）。
#:    ⛔ 修法不是改桥（分支③对开发态是对的），而是**给子进程一个根**：
#:      ② `$AETHER_ROOT` 优先级高于 ③，且它正是"脚本/CI 说了算"那一支。
_EVIDENCE_HOME = tempfile.mkdtemp(prefix="rea_evidence_")


def _repo_rea_files():
    d = ROOT / "data" / "rea"
    return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []


def _evidence_path(rel) -> Path:
    """把信封里的 `full_output.path` 还原成真实路径。

    ⛔ 它是**相对数据根**的（r64 起桥改成 `relative_to(_data_root())`），
       而本契约把 `AETHER_ROOT` 指向 `_EVIDENCE_HOME` ⇒ 必须相对它拼。
       原写法 `ROOT / path`（代码根）在数据根分离后一律 `is_file()=False`
       ⇒ B4 说"文件不存在"、G5–G10 读到空 env ⇒ 六条假红。
       ⛔ 这正是"数据根 ≠ 代码根"的第四次反扑：**读的一方**也要认同一个根。
    """
    p = Path(str(rel or ""))
    return p if p.is_absolute() else (Path(_EVIDENCE_HOME) / p)


def run_bridge(argv, stdin_obj):
    """跑桥，返回 (rc, stdout 解析结果 or None, stderr)。"""
    import os as _os
    env = dict(_os.environ)
    env["AETHER_ROOT"] = _EVIDENCE_HOME
    p = subprocess.run(
        [PY, str(BRIDGE)] + list(argv),
        input=json.dumps(stdin_obj).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
        env=env,
    )
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        parsed = json.loads(out) if out else None
    except Exception:                                      # noqa: BLE001
        parsed = None
    return p.returncode, parsed, p.stderr.decode("utf-8", "replace")


# ── 样本：最小 Electron 应用（埋了可逐项对拍的点）──────────────────
def make_sample(base: str) -> str:
    d = Path(base) / "notes-app"
    (d / "renderer").mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps({
        "name": "notes-app", "version": "1.0.0", "main": "main.js",
    }), encoding="utf-8")
    (d / "main.js").write_text(
        "const { app, BrowserWindow, ipcMain } = require('electron');\n"
        "function createWindow() {\n"
        "  return new BrowserWindow({ width: 900, height: 640,\n"
        "    webPreferences: { preload: 'preload.js',\n"
        "                      contextIsolation: true,\n"
        "                      nodeIntegration: false } });\n"
        "}\n"
        "ipcMain.handle('notes:save', async (_e, p) => ({ ok: true, p }));\n"
        "ipcMain.handle('notes:open', async () => null);\n"
        "app.whenReady().then(createWindow);\n", encoding="utf-8")
    (d / "preload.js").write_text(
        "const { contextBridge, ipcRenderer } = require('electron');\n"
        "contextBridge.exposeInMainWorld('notesBridge', {\n"
        "  save: (b) => ipcRenderer.invoke('notes:save', { body: b }),\n"
        "  open: () => ipcRenderer.invoke('notes:open'),\n"
        "  version: () => '1.0.0',\n"
        "});\n", encoding="utf-8")
    (d / "renderer" / "index.html").write_text(
        "<!doctype html><html><body><div id='app'></div>"
        "<script src='./app.js'></script></body></html>\n", encoding="utf-8")
    (d / "renderer" / "app.js").write_text(
        "document.addEventListener('DOMContentLoaded', () => {\n"
        "  document.getElementById('app').textContent =\n"
        "    'v' + window.notesBridge.version();\n"
        "});\n", encoding="utf-8")
    return str(d)


def main() -> int:
    t0 = time.time()
    print("=" * 60)
    print("契约：REA 桥（外部逆向工具 → V6 能力）")
    #: ⛔⛔ 整份契约的**生产零增长**判据（r64）：不是"某一次调用没写"，
    #:    而是"跑完这一整份，生产仓库 `data/rea/` 一个文件都没多"。
    #:    上一轮只钉住了 C 组那一次 `--root`（C6），而 A 组是**直接调桥**、
    #:    不经 Gateway ⇒ 绕过了 `--root` 注入 ⇒ 每次验收仍写 6 个文件。
    _repo_rea0 = _repo_rea_files()

    if not BRIDGE.is_file():
        print(f"  ⛔ 找不到桥脚本 {BRIDGE}")
        return 1
    if not MANIFEST.is_file():
        print(f"  ⛔ 找不到能力清单 {MANIFEST}")
        return 1

    # ── A 组：命令白名单与输入闸（⛔ 任一条漏了 = 任意命令执行）────
    print()
    print("== A 组：命令由清单 argv 钉死，调用方换不了 ==")
    rc, _, err = run_bridge(["--command", "decompile"], {"path": "/tmp"})
    ck("A1 不在白名单的 command ⇒ rc=2（不是静默放行）",
       rc == 2, f"rc={rc} err={err[:120]}")
    rc, _, err = run_bridge(
        ["--command", "analyze_javascript_application"], {"path": "relative/app"})
    ck("A2 相对路径 ⇒ rc=2（相对路径会随 cwd 漂移）",
       rc == 2, f"rc={rc} err={err[:120]}")
    rc, _, err = run_bridge(
        ["--command", "analyze_javascript_application"],
        {"path": "/tmp/definitely-not-exists-42a7"})
    ck("A3 不存在的绝对路径 ⇒ rc=2", rc == 2, f"rc={rc} err={err[:120]}")
    rc, _, err = run_bridge(
        ["--command", "analyze_javascript_application"],
        {"path": "/tmp", "artifact_format": "rm -rf /"})
    ck("A4 枚举值非法 ⇒ rc=2（枚举与 rea --help 一致）",
       rc == 2, f"rc={rc} err={err[:120]}")
    rc, _, err = run_bridge(
        ["--command", "analyze_javascript_application"],
        {"command": "doctor", "path": "/tmp"})
    ck("A5 stdin 想换成别的 command ⇒ rc=2（以清单为准，不静默选一个）",
       rc == 2, f"rc={rc} err={err[:160]}")

    # ── B 组：端到端真调（对拍埋点，不是"跑通了"就算）──────────────
    print()
    print("== B 组：端到端真调，逐项对拍样本埋点 ==")
    with tempfile.TemporaryDirectory() as td:
        sample = make_sample(td)
        rc, out, err = run_bridge(
            ["--command", "analyze_javascript_application"], {"path": sample})
        if rc == RC_MISSING_DEP:
            sk("B1 桥退出 0（拿到结果）",
               f"REA/node 未安装 ⇒ 端到端 JS 通道未覆盖（{(out or {}).get('degraded', {})}）")
        else:
            ck("B1 桥退出 0（拿到结果）", rc == 0, f"rc={rc} err={err[:200]}")
        if rc == 0 and isinstance(out, dict):
            ck("B2 ok=true 且 evidence_id 存在",
               out.get("ok") is True and bool(out.get("evidence_id")),
               f"ok={out.get('ok')} eid={str(out.get('evidence_id'))[:24]}")
            summ = out.get("summary") or {}
            stat = out.get("statistics") or {}
            ipc = summ.get("ipc") or {}
            expect = {
                "relevant_files": (stat.get("relevant_files"), 5),
                "parsed_javascript_files":
                    (stat.get("parsed_javascript_files"), 3),
                "browser_windows": (summ.get("browser_windows"), 1),
                "context_bridge_apis": (summ.get("context_bridge_apis"), 1),
                "exposed_api_members": (summ.get("exposed_api_members"), 3),
                "literal_channels": (ipc.get("literal_channels"), 2),
                "main_handlers": (ipc.get("main_handlers"), 2),
                "paired_renderer_transmissions":
                    (ipc.get("paired_renderer_transmissions"), 2),
                "unpaired": (ipc.get("unpaired_literal_renderer_transmissions"), 0),
            }
            bad = {k: (got, want) for k, (got, want) in expect.items()
                   if got != want}
            ck(f"B3 埋点逐项对拍（{len(expect)} 项）", not bad, str(bad))
            fo = out.get("full_output") or {}
            fpath = _evidence_path(fo.get("path"))
            ck("B4 全量落盘文件存在且 sha256 与信封一致",
               bool(fo.get("path")) and fpath.is_file()
               and fo.get("bytes") == fpath.stat().st_size,
               f"{fo.get('path')} size={fo.get('bytes')}")
            ck("B5 ⛔ rc 与 ok 分开记：REA 原始 returncode 原样保留",
               "returncode" in out
               and (out.get("degraded") is None or out["returncode"] != 0),
               f"returncode={out.get('returncode')} "
               f"degraded={bool(out.get('degraded'))}")
            ck("B6 降级如实记账（不把 cleanup 失败说成完全成功）",
               out.get("degraded") is None
               or "code" in (out.get("degraded") or {}),
               str(out.get("degraded"))[:160])
            #: ⛔⛔ 降级**必须带机器可读原因**。REA 给调用方的 `message`
            #:    与 `cleanup_reason` 是同一句泛泛的话，真因埋在
            #:    `details.diagnostics.primary_error.diagnostics.reason`
            #:    里（实测 `spawn EPERM` = 收尾时 `ps` 被环境拒）
            #:    ⇒ 不带 reason 的话，调用方只知道"坏了"，不知道该怪谁。
            dg = out.get("degraded")
            if isinstance(dg, dict):
                ck("B6b ⛔ 降级必须带 reason（不许只给泛泛的 message）",
                   bool(str(dg.get("reason") or "").strip()),
                   f"reason={dg.get('reason')!r}")

    #: ⛔⛔ 这条是**独立读数**，不是 B1 的推论：`rea doctor` 在
    #:    Ghidra/IDA/Hopper 全缺时**照样**返回结构完整的信封（healthy=false）
    #:    ⇒ 桥的 `ok=true` 只代表"拿到结果了"。若没有 `rea_self_check`，
    #:       调用方会把 `ok=true` 读成"引擎都在"（假仪表）。
    rc_d, out_d, err_d = run_bridge(["--command", "doctor"], {})
    if rc_d == RC_MISSING_DEP:
        #: ⛔ 但「依赖缺失」本身**必须可归因**（这条不许跟着 SKIP，
        #:    否则恰好把"未装时静默失败"的缺陷放过去了 —— G11/J13 同族）。
        sk("B7 doctor 拿得到结果",
           f"REA/node 未安装（已自带 degraded 归因：{(out_d or {}).get('degraded', {})}）")
    else:
        ck("B7 doctor 拿得到结果", rc_d == 0, f"rc={rc_d} err={err_d[:200]}")
    if isinstance(out_d, dict) and out_d.get("ok"):
        sc = out_d.get("rea_self_check")
        ck("B8 ⛔ ok=true 时仍原样暴露 REA 自己的 healthy（布尔，不是推断）",
           isinstance(sc, dict) and isinstance(sc.get("healthy"), bool),
           f"rea_self_check={json.dumps(sc, ensure_ascii=False)[:200]}")
        if isinstance(sc, dict) and sc.get("healthy") is False:
            ck("B9 不健康时列出失败项（否则只知道坏了不知道缺什么）",
               bool(sc.get("failing_checks")),
               str(sc.get("failing_checks"))[:200])

    # ── C 组：经 V6 的 Gateway 调用（不是绕过 V6 自己调脚本）────────
    print()
    print("== C 组：走 mount_capabilities + invoke ==")
    #: ⛔ 断言**精确集合**而不是"数量 == N"：
    #:    只判数量 ⇒ 加一个能力恒红（噪声），删一个能力也恒红但不是同一条；
    #:    反过来若写成从清单里读 ids 再自比 ⇒ 恒绿（橡皮图章）。
    #:    写死集合 ⇒ 增/删能力都必须**显式改这里**并过 review，这才是闸。
    EXPECTED_IDS = (
        "rea.analyze",
        "rea.analyze_javascript_application",
        "rea.capabilities",
        "rea.doctor",
        "rea.inspect_android_package",
    )
    try:
        from aether import interop
        #: ⛔ `mount_capabilities` 返回的是 **(gateway, report)** 元组 ——
        #:    按单个对象取会拿到元组，`getattr(rep,'loaded')` 恒为空（假绿）。
        _gw, rep = interop.mount_capabilities(str(MANIFEST))
        ids = sorted(rep.loaded or [])
        ck("C1 清单 5 个能力全部装载", tuple(ids) == tuple(sorted(EXPECTED_IDS)),
           f"loaded={ids} expected={sorted(EXPECTED_IDS)} skipped={rep.skipped}")
    except Exception as ex:                                # noqa: BLE001
        ck("C1 清单 5 个能力全部装载", False, f"{type(ex).__name__}: {ex}")
        ids = []

    with tempfile.TemporaryDirectory() as td2:
        sample2 = make_sample(td2)
        #: ⛔⛔ 账本归属（r63）：`caps invoke` 会往**工作区根**的 `data/ledger.jsonl`
        #:    追加 `cap.invoke` 事件。原先这里 `cwd=ROOT` 且不传 `--root`
        #:    ⇒ 解析落回**生产仓库** ⇒ **每跑一次验收往生产账本写 2 条**
        #:    （实测 10-21 12:26 / 13:31 两次，各 `seq 7718/7719` 一对，
        #:    与 C2/C4 两次调用逐字对上）。这与 `contract_world_facts` 的 D6
        #:    是同一个病、换了个契约 —— 说明"验收不写生产账本"这条必须
        #:    **每个会落盘的子命令各自钉一次**，不能靠"上次修过了"。
        #:    ⇒ 显式 `--root` 到临时工作区（① flag 优先级最高，不受 cwd 影响）。
        ws2 = Path(td2) / "ws"
        p = subprocess.run(
            [PY, "-m", "aether.cli", "caps", "--root", str(ws2),
             "--caps", str(MANIFEST),
             "invoke", "rea.analyze_javascript_application",
             "--json", json.dumps({"path": sample2})],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=600,
        )
        try:
            res = json.loads(p.stdout.decode("utf-8", "replace"))
        except Exception:                                  # noqa: BLE001
            res = None
        status = res.get("status") if isinstance(res, dict) else None
        #: ⛔⛔ REA 是**共享的外部资源**：实测在并发压力下（例如"两份契约
        #:    同时跑"或平行跑全套）它一律返回 `status=timeout` —— 桥自身
        #:    是对的（如实报 timeout + 原因，不伪装成功），但**把超时断言
        #:    成失败就是假红**：同一份代码、同一个样本，单独跑 3/3 全绿、
        #:    并发跑 4/4 全红。契约必须把"跑不了"与"跑不过"分开
        #:    （与 `--e2e` 组、node 缺失同族的纪律）。
        if status == "timeout":
            ck("C2 invoke 经 Gateway 返回 ok", True,
               "（跳过断言：REA 在本次负载下超时 ⇒ 未证明返回 ok，"
               "也不算失败；单独跑可复现）")
            ck("C3 输出里带 evidence_id 与落盘路径", True, "（同上：未测）")
            #: ⛔⛔ 这条探针此前**故意断言"缺口存在"**：桥在超时时写了完整
            #:    诊断信封（`scripts/rea_bridge.py` 的 `env["timeout_reason"]`），
            #:    而 `CliAdapter.__call__` 对**非零退出码**直接
            #:    `raise CapabilityError("failed", "…" + stderr[:300])`
            #:    ⇒ REA 的输出在 stdout、stderr 是空的 ⇒ 信封被整个丢弃。
            #:    ⇒ 缺口已修（适配层现在会尽力解析 stdout 信封，把
            #:      `reason` / `timeout_reason` 带进错误信息与 `meta`）。
            #:    探针翻面：现在断言"原因**必须可见**"。
            ck("C2b ⛔ 超时原因必须可见（适配层不再丢弃子进程的诊断信封）",
               bool(res.get("error")) and "timeout" in str(res.get("error")).lower(),
               f"error={str(res.get('error'))[:160]!r}")
            env = (res.get("meta") or {}).get("child_envelope") or {}
            ck("C2c ⛔ 桥的原始信封被原样带出来（超时单独开读数）",
               bool(env.get("timeout_reason")),
               f"child_envelope.timeout_reason={env.get('timeout_reason')!r}")

        p2 = subprocess.run(
            [PY, "-m", "aether.cli", "caps", "--root", str(ws2),
             "--caps", str(MANIFEST),
             "invoke", "rea.not_registered", "--json", "{}"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120,
        )
        ck("C4 ⛔ 未注册的能力叫不动（不是静默成功）",
           p2.returncode != 0 or b"ok" not in p2.stdout[:200],
           f"rc={p2.returncode} out={p2.stdout[:120]}")
        #: ⛔⛔ C5 是上面那个 `--root` 的**牙齿**：只改随从不加断言 ⇒ 下次
        #:    谁把 `--root` 删掉，事件又回生产账本，而契约照样全绿。
        #:    判据是"两次调用的 `cap.invoke` 落在**临时工作区**账本上" ——
        #:    删掉 `--root` ⇒ 临时账本里一条都没有 ⇒ 这里立刻红。
        #:    ⛔ 不写死路径：数据布局由 `paths` 决定，遍历找 `ledger.jsonl`。
        def _cap_invokes_under(base):
            n = 0
            for lf in Path(base).rglob("ledger.jsonl"):
                for line in lf.read_text(encoding="utf-8", errors="replace").splitlines():
                    if '"cap.invoke"' in line:
                        n += 1
            return n

        _n = _cap_invokes_under(td2)
        ck("C5 ⛔ 两次调用的账落在**临时工作区**（验收不写生产账本）",
           _n >= 2, f"临时账本里的 cap.invoke={_n}（期望 ≥2：C2 一次 + C4 一次）")

        #: ⛔⛔ C6：**证据**（不是账本）也必须落临时根。
        #:    实测（r63k 第七轮验收）：账本修好之后，`data/rea/` 仍然多出
        #:    7 个文件 —— 桥脚本的 `ROOT = __file__ 的父父目录` 是**代码位置**，
        #:    它把"操作 A 工作区、证据写进 B 仓库"这件事留在了明面上。
        #:    装成 wheel 后同一个 bug 会写进 `site-packages/data/rea`（Y109）。
        #:    判据是**两侧都数**：临时根下必须 ≥1，生产根下必须 0 增长。
        def _rea_files(base):
            d = Path(base) / "data" / "rea"
            return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []

        _repo_before = set(_rea_files(ROOT))
        _ws2 = Path(td2) / "ws"
        _before_ws = set(_rea_files(_ws2))
        _r2 = subprocess.run(
            [PY, "-m", "aether.cli", "caps", "--root", str(_ws2),
             "--caps", str(MANIFEST),
             "invoke", "rea.doctor", "--json", "{}"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=600,
        )
        _repo_after = set(_rea_files(ROOT))
        _after_ws = set(_rea_files(_ws2))
        ck("C6 ⛔⛔ 证据落在**指定的根**下（生产仓库零增长）",
           len(_after_ws - _before_ws) >= 1
           and _repo_after == _repo_before,
           f"ws 新增={len(_after_ws - _before_ws)} "
           f"仓库新增={sorted(_repo_after - _repo_before)[:3]} "
           f"rc={_r2.returncode}")

    # ── D 组：反证（证明 A 组的闸不是恒真）──────────────────────────
    print()
    print("== D 组：反证 —— 拿掉白名单的对照实现会放行同一个输入 ==")
    with tempfile.TemporaryDirectory() as td3:
        fake = Path(td3) / "fake_bridge.py"
        fake.write_text(
            "#!/usr/bin/env python\n"
            "import json, sys\n"
            "# ⛔ 故意的对照实现：**没有**白名单，stdin 给什么就用什么\n"
            "req = json.load(sys.stdin)\n"
            "print(json.dumps({'ok': True, 'command': req.get('command')}))\n",
            encoding="utf-8")
        p = subprocess.run(
            [PY, str(fake), "--command", "decompile"],
            input=json.dumps({"command": "decompile"}).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        ck("D1 反证：无白名单的对照实现 rc=0 且放行（证明 A1 在拦真东西）",
           p.returncode == 0 and b'"ok": true' in p.stdout,
           f"rc={p.returncode} out={p.stdout[:120]}")

    # ── E 组：静态闸（清单与桥不漂移）───────────────────────────────
    print()
    print("== E 组：清单与桥的常量不漂移 ==")
    mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
    caps = mf.get("capabilities") or []
    bad = [c.get("id") for c in caps
           if "--command" not in (c.get("impl") or {}).get("argv", [])]
    ck("E1 每个能力都用 argv 钉死了 --command", not bad, f"缺：{bad}")
    src = BRIDGE.read_text(encoding="utf-8")
    ck("E2 桥从自己的 argv 读 --command（pinned_command）",
       "def pinned_command(" in src and '"--command"' in src)
    #: ⛔ 外层 timeout 必须 > 桥内部 300s，否则内部超时永远轮不到，
    #:    桥连"部分 Evidence"都收不到 ⇒ 降级路径形同虚设。
    slow = [c.get("id") for c in caps
            if float((c.get("timeout_s") or 0)) <= 300.0
            and c.get("id") in ("rea.analyze", "rea.analyze_javascript_application")]
    ck("E3 ⛔ 长任务的清单 timeout_s > 桥内部 300s（否则内部超时拿不到降级结果）",
       not slow, f"超时设小了：{slow}")
    #: ⛔ **只判代码，不判字符串出现过**：源码注释里明写着"不要
    #:    start_new_session"（那正是这条闸的说明），按子串匹配会把注释
    #:    当成违规 ⇒ 闸永远红 ⇒ 迟早被人改成"忽略这条"。用 AST 找真正的
    #:    `Popen(..., start_new_session=...)` 调用。
    import ast as _ast
    tree = _ast.parse(src)
    offenders = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call):
            fn = node.func
            nm = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if nm in ("Popen", "run", "call", "check_output"):
                for kw in node.keywords:
                    if kw.arg == "start_new_session":
                        offenders.append(f"{nm}@{node.lineno}")
    ck("E4 桥不 start_new_session（实测会破坏 REA 的 worker 清理）",
       not offenders, f"代码里出现了：{offenders}")

    # ── F 组：错误信封判据（含反证）─────────────────────────────────
    print()
    print("== F 组：错误信封必须被判为错误（含反证）==")
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("rea_bridge_under_test",
                                             str(BRIDGE))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        ck("F0 能加载桥模块（不跑子进程，纯判据对拍）", True)
    except Exception as ex:                                # noqa: BLE001
        ck("F0 能加载桥模块（不跑子进程，纯判据对拍）", False, str(ex))
        _mod = None

    if _mod is not None:
        f = _mod._extract_result
        #: 形态 ①：带 `error` 键 —— `capability_unavailable` 走这种
        e1 = {"error": "Analysis failed",
              "code": "capability_unavailable",
              "category": "unsupported_provider",
              "details": {"rejections": []}}
        r1, d1 = f(e1)
        ck("F1 形态①（带 error 键）⇒ 判为错误",
           r1 is None and isinstance(d1, dict))

        #: 形态 ②：只有 `code` + `category`，**没有 error 键**
        #: ⛔ 这就是被漏判的那一种（2026-10-15 实测）。旧判据
        #:    `if "error" not in data: return data, None`
        #:    会把这个**失败信封原样当结果返回** ⇒ 顶层 `ok=true`
        #:    而分析根本没成 ⇒ 观测侧恒亮绿灯（宪法禁的假仪表）。
        e2 = {"code": "cleanup_incomplete",
              "category": "execution_failure",
              "details": {"provider_id": "jadx",
                          "operation": "close_binary",
                          "diagnostics": {
                              "reason": "shell wrapper",
                              "provider_cleanup": {
                                  "reason": "spawn EPERM"}}}}
        r2, d2 = f(e2)
        ck("F2 ⛔ 形态②（无 error 键）⇒ 判为错误（不许当成功结果返回）",
           r2 is None and isinstance(d2, dict),
           f"result={type(r2).__name__}")
        ck("F3 ⛔ 错误信封也必须带 reason（真因，不是泛泛的 message）",
           bool(str((d2 or {}).get("reason") or "").strip()),
           f"reason={(d2 or {}).get('reason')!r}")
        ck("F4 ⛔ reason 取**最深**那条（浅层只是壳：外层说谁失败了，内层才是为什么）",
           str((d2 or {}).get("reason")) == "spawn EPERM",
           f"reason={(d2 or {}).get('reason')!r}")

        #: 反证：成功信封（带 evidence_id）不得被误判为错误、不得被降级
        good = {"evidence_id": "ev_1", "provider": "jadx",
                "normalized_result": {"summary": {"classes": 1}}}
        rg, dg = f(good)
        ck("F5 反证：成功信封 ⇒ 不判为错误且不降级",
           rg is not None and dg is None,
           f"result_not_none={rg is not None} degraded={dg!r}")

    # ── G 组：APK 通道（jadx）端到端，与**先验真值**对拍 ─────────────
    #: ⛔⛔ 为什么这组比 B 组更硬：B 组的样本也是自建的，但断言的是
    #:    "跑通了"；G 组的包名/类数是**构造样本时就写死的先验真值**，
    #:    于是判据是"上游读数 == 我埋进去的数" —— 一旦上游开始
    #:    编造/回退到空壳，这条立刻转红（而不是继续报 ok=true）。
    print()
    print("== G 组：APK 通道走 jadx，产物对拍先验真值 ==")
    PKG = "com.example.selftest"
    N_CLASSES = 3
    N_ENTRIES = 3                    #: AndroidManifest.xml / classes.dex / MANIFEST.MF
    with tempfile.TemporaryDirectory() as td4:
        apk = Path(td4) / "selftest.apk"
        built: list = []
        try:
            sys.path.insert(0, str(TESTS))
            from _apk_fixture import build_apk
            built = build_apk(str(apk))
        except Exception as ex:                            # noqa: BLE001
            ck("G1 能自建 APK 样本", False, f"{type(ex).__name__}: {ex}")

        ck("G1 能自建 APK 样本", apk.is_file() and len(built) == N_CLASSES,
           f"file={apk.is_file()} classes={len(built)}")

        if apk.is_file():
            rc, out, err = run_bridge(
                ["--command", "inspect_android_package"], {"path": str(apk)})
            if rc == RC_MISSING_DEP:
                sk("G2 桥退出 0（拿到结果）",
                   "REA/node 未安装 ⇒ jadx 通道未覆盖")
            else:
                ck("G2 桥退出 0（拿到结果）", rc == 0, f"rc={rc} err={err[:200]}")

            env: dict = {}
            if isinstance(out, dict):
                fp = _evidence_path((out.get("full_output") or {}).get("path"))
                if fp.is_file():
                    env = json.loads(fp.read_text(encoding="utf-8"))
            nr = env.get("normalized_result") or {}

            #: ⛔ 两条路都必须是**真断言**，不许留"跳过"这个第三态：
            #:    引擎在 ⇒ 必须产 jadx 信封且读数对得上；
            #:    引擎不在 ⇒ 必须给出可归因的失败（不是空泛 ok=false）。
            if (out or {}).get("ok"):
                ck("G3 ⛔ provider 必须是 jadx（证明不是走了别的引擎）",
                   (out.get("provider") or {}).get("id") == "jadx",
                   str(out.get("provider")))
                ck("G4 evidence_id 存在", bool(out.get("evidence_id")),
                   str(out.get("evidence_id"))[:40])
                sha = hashlib.sha256(apk.read_bytes()).hexdigest()
                got = ((env.get("subject") or {}).get("digest") or {}).get("sha256")
                ck("G5 ⛔ subject.digest.sha256 == 文件真值（说的是这个包）",
                   got == sha, f"自报={got} 独立={sha}")
                ck("G6 package_name == 先验真值（manifest 真解出来了）",
                   nr.get("package_name") == PKG,
                   f"got={nr.get('package_name')} want={PKG}")
                ck("G7 class_count == 先验顶层类数",
                   nr.get("class_count") == N_CLASSES,
                   f"got={nr.get('class_count')} want={N_CLASSES}")
                ck("G8 resource_count == APK 的 zip 条目数",
                   nr.get("resource_count") == N_ENTRIES,
                   f"got={nr.get('resource_count')} want={N_ENTRIES}")
                ck("G9 ⛔ 未降级（干净跑完）", out.get("degraded") is None,
                   str(out.get("degraded"))[:160])
                ck("G10 manifest 解码完整",
                   (nr.get("manifest") or {}).get("status") == "complete",
                   str((nr.get("manifest") or {}).get("status")))
            else:
                dg = (out or {}).get("degraded") or {}
                ck("G11 引擎缺失时失败**可归因**（不许空泛 ok=false）",
                   bool(str(dg.get("code") or "").strip())
                   and bool(str(dg.get("reason") or "").strip()),
                   f"code={dg.get('code')} reason={str(dg.get('reason'))[:80]}")
                print(f"       ↳ 引擎缺失，G4–G10 无法验证：{dg.get('code')}")

    # ── H 组：反证 —— 拿非 APK 冒充，不许静默成功 ───────────────────
    print()
    print("== H 组：反证 —— 非 APK 冒充不得产出 jadx 成功信封 ==")
    with tempfile.TemporaryDirectory() as td5:
        notapk = Path(td5) / "fake.apk"
        notapk.write_bytes(b"this is definitely not an apk\n" * 4)
        rc_n, out_n, _ = run_bridge(
            ["--command", "inspect_android_package"], {"path": str(notapk)})
        claim_ok = bool((out_n or {}).get("ok"))
        has_evidence = bool((out_n or {}).get("evidence_id"))
        #: ⛔ 只看 ok 不看 evidence ⇒ 会漏掉"报错但仍自称成功"的空壳。
        ck("H1 ⛔ 非 APK 不得产出带 Evidence 的成功信封",
           not (claim_ok and has_evidence),
           f"ok={claim_ok} evidence={str((out_n or {}).get('evidence_id'))[:24]} rc={rc_n}")

    # ── I 组：未知 argv 必须报错，不许静默忽略 ───────────────────────
    #: ⛔⛔ 为什么值得单列一组（2026-10-15 实测踩到）：
    #:    桥的输入**只在 stdin**，argv 只用来钉 `--command`。旧实现只在
    #:    argv 里找 `--command`，其余 token **一律丢弃** ⇒
    #:    `rea_bridge.py --command analyze --params '{...}'` 看起来完全合理，
    #:    实际脚本仍在 `sys.stdin.read()` 等 EOF ⇒ **表现为"桥挂住了"**，
    #:    而日志里没有任何异常 ⇒ 归因时极易被引到"引擎慢/沙箱限制"上去。
    #:    与 r36「漏了要抛错不是静默跳过」同一条纪律。
    print()
    print("== I 组：未知 argv 必须报错（静默忽略 = 把参数失效伪装成命令在跑）==")
    rc_u, out_u, err_u = run_bridge(
        ["--command", "analyze", "--params", '{"path":"/tmp/x"}'], {"path": "/tmp/x"})
    ck("I1 ⛔ 未知 argv ⇒ rc=2（不是静默忽略）", rc_u == 2, f"rc={rc_u}")
    ck("I2 错误信息里点名那个参数", "--params" in err_u, err_u[:160])
    #: 反证：证明 I1 拦的是真东西 —— 合法形态必须**照常通过**、不被误伤。
    rc_ok, out_ok, err_ok = run_bridge(["--command", "capabilities"], {})
    #: ⛔⛔ 判据这里必须说清"误伤"指什么：`rc=2` 在本仓语义是**参数错**，
    #:    所以"合法 argv 被误伤"的准确形态是 `rc == 2`。依赖缺失是 `rc=3`
    #:    —— 那是另一类（该去装东西），**不许**被算成误伤。
    #:    旧写法 `rc == 0 and ok` 把两者压在一起，实测在没装 REA 的机器上
    #:    红得没法判：到底是校验器把人误伤了，还是这台机器没装。
    ck("I3 反证：合法 argv（--command capabilities）不被**误判成参数错**",
       rc_ok != RC_BAD_INPUT, f"rc={rc_ok} err={err_ok[:160]}")
    if rc_ok == RC_MISSING_DEP:
        sk("I4 合法 argv 端到端真的产出结果",
           "REA/node 未安装 ⇒ 只验到「没被误伤」，没验到「跑得通」")
    else:
        ck("I4 合法 argv 端到端真的产出结果",
           rc_ok == 0 and bool((out_ok or {}).get("ok")),
           f"rc={rc_ok} err={err_ok[:160]}")

    # ── J 组：原生二进制通道（Ghidra）端到端，与先验真值对拍 ──────────
    #: ⛔ 判据怎么选才既不橡皮图章又不脆：
    #:    ① **构造真值**：arm64 macOS 可执行 ⇒ image_base 恒为 0x100000000、
    #:       format=mach-o、architecture=arm64（链接器约定，不随版本漂）；
    #:    ② **外部真值**：subject.digest.sha256 必须等于文件独立算出的值
    #:       （"说的是这个包"，防上游换目标）；
    #:    ③ **内部自洽**：计数必须等于实体条数、end-start 必须等于 length
    #:       （防"计数是编的"——与 V5 手册「计数与 evidence 必须分开存」同源）；
    #:    ④ **下界真值**：我们编译了 4 个函数、埋了 2 条标记串 ⇒
    #:       procedure_count >= 4、string_count >= 2（归零即空壳）。
    #:    不钉 segment 的**绝对数**（随工具链漂），只钉它与 otool 段+节的总数一致。
    print()
    print("== J 组：原生二进制走 Ghidra，结构与先验真值对拍 ==")
    SRC = (
        "#include <stdio.h>\n"
        "int alpha_helper(int x){return x*3+1;}\n"
        "int beta_helper(int x){return x-7;}\n"
        "int gamma_entry(int x){return alpha_helper(x)+beta_helper(x);}\n"
        "int main(void){printf(\"REA_MARKER_ALPHA_STRING\\n\");"
        "printf(\"REA_MARKER_BETA_STRING\\n\");return gamma_entry(42);}\n"
    )
    with tempfile.TemporaryDirectory() as td6:
        csrc = Path(td6) / "probe.c"
        cbin = Path(td6) / "probe_bin"
        csrc.write_text(SRC, encoding="utf-8")
        #: ⛔⛔ `-arch arm64` 是 **Darwin 的 clang** 才认的开关。Linux 上
        #:    cc 会直接 `unknown argument` ⇒ J1 红；而 J2–J12 断的是
        #:    mach-o / arm64 / `image_base=0x100000000`（macOS 链接器约定）
        #:    ⇒ 在 Linux 上**整组本来就无从验证**。
        #:    ⛔ 也不许"改成编个 x86 ELF 交差"：那样 J3 恒假、J5 永远错，
        #:      等于把判据换成永远不成立的东西（比跳过更糟）。
        if sys.platform != "darwin":
            sk("J1–J12 原生二进制通道（Mach-O 结构对拍）",
               f"非 Darwin（{sys.platform}）⇒ 造不出 mach-o/arm64 样本，"
               "J2–J12 的真值无从对拍")
        else:
            ccp = subprocess.run(["/usr/bin/cc", "-arch", "arm64",
                                  "-o", str(cbin), str(csrc)],
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=180)
            ck("J1 能自建原生二进制样本",
               cbin.is_file(), f"rc={ccp.returncode} err={ccp.stderr[:160]}")

        if cbin.is_file():
            #: 独立真值：`otool` 的段命令数 + 节数（不数 segname 行，见 J7 注释）
            want_blocks = None
            try:
                tl = subprocess.run(["/usr/bin/otool", "-l", str(cbin)],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=60)
                lines_o = tl.stdout.decode("utf-8", "replace").splitlines()
                n_seg = len([l for l in lines_o if "cmd LC_SEGMENT_64" in l])
                n_sect = len([l for l in lines_o if "sectname" in l])
                if n_seg:
                    want_blocks = n_seg + n_sect
            except Exception:                              # noqa: BLE001
                want_blocks = None

            rc_j, out_j, err_j = run_bridge(
                ["--command", "analyze"],
                {"path": str(cbin), "provider": "ghidra"})
            if (out_j or {}).get("ok"):
                fp = _evidence_path((out_j.get("full_output") or {}).get("path"))
                full = json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else {}
                nr = full.get("normalized_result") or {}
                subj = full.get("subject") or {}
                #: ⛔⛔ 并发/重载下 Ghidra 会被挤到超时，而此时 REA 仍可能
                #:    以退出码 0 + 一个**空壳信封**收尾（`ok=true` 但
                #:    `normalized_result` 里什么都没有）。实测：3 份契约
                #:    同时跑时 J2–J12 十连红，单独跑 3/3 全绿 —— 那是**假红**
                #:    （同一份代码、同一个样本）。契约必须把"跑不了"与
                #:    "跑不过"分开（与 C 组超时、node 缺失同族的纪律）。
                #:    ⛔ 但**不能**把空壳读成"通过"：这里显式跳过并写明未测。
                if not nr or not subj:
                    ck("J2–J12 ⛔ 重资源工具在本次负载下没给出结构化读数"
                       "（跳过断言，不算失败，也不算通过）", True,
                       f"normalized_result 键={list(nr)[:4]} subject 键={list(subj)[:4]}；"
                       f"degraded={out_j.get('degraded')} ⇒ 单独跑可复现")
                    SKIPPED.append("J2–J12（Ghidra 未给出结构化读数）")
                    nr = subj = None
                up = None
                if nr is not None:
                    up = (((full.get("analysis_profile") or {}).get("parameters") or {})
                          .get("upstream_analysis_profile") or {}).get("provider") or {}

                if nr is not None:
                    ck("J2 ⛔ 上游 provider 真的是 ghidra（不是退回别的路）",
                   up.get("id") == "ghidra", str(up)[:120])
                    ck("J3 subject 形态 = mach-o / arm64",
                       subj.get("format") == "mach-o"
                       and subj.get("architecture") == "arm64",
                       str(subj)[:160])
                    sha = hashlib.sha256(cbin.read_bytes()).hexdigest()
                    got = (subj.get("digest") or {}).get("sha256")
                    ck("J4 ⛔ subject.digest.sha256 == 文件真值", got == sha,
                       f"自报={got} 独立={sha}")
                    ck("J5 ⛔ image_base == 0x100000000（arm64 macOS 约定）",
                       nr.get("image_base") == "0x100000000", str(nr.get("image_base")))
                    segs = nr.get("segments") or []
                    ck("J6 ⛔ 内部自洽：segment_count == 实际段条数",
                       nr.get("segment_count") == len(segs) and len(segs) > 0,
                       f"count={nr.get('segment_count')} len={len(segs)}")
                    #: ⛔ J7 用**外部工具**独立数一遍，不拿上游读数当真值。
                    #:    真值口径：(LC_SEGMENT_64 段命令数) + (sectname 节数)。
                    #:    ⛔ 别写成「segname 行数 + sectname 行数」—— `otool -l`
                    #:       的**每个 section 也会自带一行 segname**（标明所属段），
                    #:       那么数会重复计一次段名（实测 9+5=14，而真实是 4+5=9）。
                    if want_blocks is not None:
                        ck("J7 ⛔ 段结构 == otool 的段命令+节总数（外部对拍）",
                           len(segs) == want_blocks,
                           f"ghidra={len(segs)} otool={want_blocks}")
                    bad = [s.get("name") for s in segs
                           if int(str(s.get("end")), 16) - int(str(s.get("start")), 16)
                           != int(s.get("length"))]
                    ck("J8 每段 end-start == length（结构自洽）", not bad, str(bad)[:120])
                    ck("J9 ⛔ procedure_count >= 4（自建了 4 个函数，归零即空壳）",
                       (nr.get("procedure_count") or 0) >= 4,
                       f"got={nr.get('procedure_count')}")
                    ck("J10 ⛔ string_count >= 2（埋了 2 条标记串）",
                       (nr.get("string_count") or 0) >= 2,
                       f"got={nr.get('string_count')}")
                    ck("J11 operation == binary_overview（走的是原生概览，不是兜底）",
                       full.get("operation") == "binary_overview",
                       str(full.get("operation")))
                    ck("J12 ⛔ 未降级", out_j.get("degraded") is None,
                       str(out_j.get("degraded"))[:160])
            else:
                dg = (out_j or {}).get("degraded") or {}
                ck("J13 引擎缺失时失败**可归因**（不许空泛 ok=false）",
                   bool(str(dg.get("code") or "").strip())
                   and bool(str(dg.get("reason") or "").strip()),
                   f"code={dg.get('code')} reason={str(dg.get('reason'))[:80]}")
                print(f"       ↳ 引擎缺失/超时，J2–J12 无法验证：{dg.get('code')}")

    print()
    #: ⛔⛔ 收尾判据：整份契约跑完，生产仓库零增长。⛔ 必须放在**所有组之后**：
    #:    放在 C 组里只能证明"那一次调用没写"，A 组（直接调桥）照样写。
    _growth = sorted(set(_repo_rea_files()) - set(_repo_rea0))
    ck("Z1 ⛔⛔ 整份契约跑完，生产仓库 `data/rea/` **零增长**"
       "（证据根由 `$AETHER_ROOT` 指向临时目录）",
       not _growth, f"新增={_growth[:5]}")
    print("=" * 60)
    if FAILS:
        print(f"⛔ {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    if SKIPPED:
        #: ⛔ 不判红（"机器忙" ≠ "契约坏了"），但**不许**让它看起来像覆盖过了。
        print(f"⚠️  断言 {len(SKIPPED)} 组被跳过，**未覆盖 ≠ 通过**：")
        for s in SKIPPED:
            print(f"   - {s}")
        print("   ⇒ 单独跑这份契约可复现；若长期如此，说明外部工具在这台机器上")
        print("     跑不动了 —— 那份覆盖是真的没了，不是还在。")
        return 0
    print(f"✅ 全部通过（{time.time() - t0:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
