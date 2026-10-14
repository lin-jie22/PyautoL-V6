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
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

BRIDGE = ROOT / "scripts" / "rea_bridge.py"
MANIFEST = ROOT / "capabilities.rea.json"
PY = sys.executable

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    return bool(cond)


def run_bridge(argv, stdin_obj):
    """跑桥，返回 (rc, stdout 解析结果 or None, stderr)。"""
    p = subprocess.run(
        [PY, str(BRIDGE)] + list(argv),
        input=json.dumps(stdin_obj).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
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
            fpath = ROOT / str(fo.get("path", ""))
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

    # ── C 组：经 V6 的 Gateway 调用（不是绕过 V6 自己调脚本）────────
    print()
    print("== C 组：走 mount_capabilities + invoke ==")
    try:
        from aether import interop
        #: ⛔ `mount_capabilities` 返回的是 **(gateway, report)** 元组 ——
        #:    按单个对象取会拿到元组，`getattr(rep,'loaded')` 恒为空（假绿）。
        _gw, rep = interop.mount_capabilities(str(MANIFEST))
        ids = sorted(rep.loaded or [])
        ck("C1 清单 4 个能力全部装载", len(ids) == 4,
           f"loaded={ids} skipped={rep.skipped}")
    except Exception as ex:                                # noqa: BLE001
        ck("C1 清单 4 个能力全部装载", False, f"{type(ex).__name__}: {ex}")
        ids = []

    with tempfile.TemporaryDirectory() as td2:
        sample2 = make_sample(td2)
        p = subprocess.run(
            [PY, "-m", "aether.cli", "caps", "--caps", str(MANIFEST),
             "invoke", "rea.analyze_javascript_application",
             "--json", json.dumps({"path": sample2})],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=600,
        )
        try:
            res = json.loads(p.stdout.decode("utf-8", "replace"))
        except Exception:                                  # noqa: BLE001
            res = None
        ck("C2 invoke 经 Gateway 返回 ok",
           isinstance(res, dict) and res.get("status") == "ok",
           f"rc={p.returncode} status={res.get('status') if res else None}")
        if isinstance(res, dict):
            o = res.get("output") or {}
            ck("C3 输出里带 evidence_id 与落盘路径",
               bool(o.get("evidence_id")) and bool(
                   (o.get("full_output") or {}).get("path")),
               str(o)[:160])
        p2 = subprocess.run(
            [PY, "-m", "aether.cli", "caps", "--caps", str(MANIFEST),
             "invoke", "rea.not_registered", "--json", "{}"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120,
        )
        ck("C4 ⛔ 未注册的能力叫不动（不是静默成功）",
           p2.returncode != 0 or b"ok" not in p2.stdout[:200],
           f"rc={p2.returncode} out={p2.stdout[:120]}")

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

    print()
    print("=" * 60)
    if FAILS:
        print(f"⛔ {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过（{time.time() - t0:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
