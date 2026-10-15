#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：入口参数与数据布局**同源**（r36）。

═══ 收口前实测到的四处漂移 ═══

1. **布局有 4 份**：`data/ledger.jsonl` 这组路径在 cli._paths / doctor._paths /
   serve（3 处手拼）/ backup.BACKUP_TARGETS 各写一遍。改布局要改四处，
   漏一处 ⇒ `status` 读的文件与 `backup` 带走的文件**不是同一个**。
2. **`--log-level` 的 choices 有 2 份**：cli/serve 用 `LEVELS`，ui 硬编码
   一个列表 ⇒ 级别表改了 ui 不跟着改，同一份配置在 cli 合法、在 ui 报 rc=2。
3. **⚠️ `aether ui --log-level DEBUG` 静默无效**：ui 的转发清单是手写的，
   漏了 log-level / log-json / api-token。命令行开关丢了却一切正常。
4. **`--port` 默认值 8611 三处各写一遍**（cli / serve / ui）。

═══ 本契约钉的三条纪律 ═══

· **透传清单不手写** —— 由 `serve.build_parser()` 动态导出；ui 缺参数时
  `build_forward()` **抛错**，不静默跳过（静默跳过 = 开关失效却无人知晓）。
· **子 parser 的默认值会盖掉主 parser 已解析的值** —— 这就是纪律 3 的
  另一半：`aether --log-level DEBUG ui` 的 DEBUG 会被 ui 子命令的 INFO 悄悄
  盖掉。用 `SUPPRESS` 修，本契约 D5 用"事故版"证明这个坑真实存在。
· **每项断言都配反证** —— 静态闸（E4）与透传闸（D2/D3）都注入事故版，
  确认原判据能翻转；不翻转的说明断言恒真。

跑法（离线，秒级；G 组会起一个本地子进程再关掉）：
    python tests/contract_entrypoints_test.py
"""
import argparse
import ast
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

#: 布局里的**文件/目录名**。出现它们在路径拼接里（`"data" / ...` 形态）
#: 就说明又有人在手拼布局。
_LAYOUT_NAMES = {"data", "ledger.jsonl", "aether.db", "memory.json",
                 "tasks", "console", "backups"}


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _acts(parser):
    """`{dest: action}`，跳过 argparse 自带的 help。"""
    return {a.dest: a for a in parser._actions if a.dest != "help"}


def _sub(parser, name):
    """取子命令的 parser（`aether <name>`）。"""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices.get(name)
    return None


def main():
    from aether import cli, entry, paths, serve, ui
    from aether.logging_setup import LEVELS

    cli_p = cli.build_parser()
    srv_p = serve.build_parser()
    ui_p = ui.build_parser()
    cli_ui_p = _sub(cli_p, "ui")

    # ── A 组：三个入口的公共参数同源 ─────────────────────────────
    print("== A 组：--root / --log-level / --log-json 三入口同口径 ==")
    common = ("root", "log_level", "log_json")
    for p_name, p in (("cli", cli_p), ("serve", srv_p), ("ui", ui_p)):
        a = _acts(p)
        ck(f"A1 {p_name} 三个公共参数都在",
           all(k in a for k in common), str(sorted(a)))
    ck("A2 cli / serve 的 --root 默认 == paths.ROOT（不再各自推导仓库根）",
       _acts(cli_p)["root"].default == _acts(srv_p)["root"].default
       == str(paths.ROOT),
       f"{_acts(cli_p)['root'].default} / {_acts(srv_p)['root'].default}")
    #: ⛔ ui 与其子命令刻意**不给**默认值（SUPPRESS = 由上层决定）：
    #:    给了默认值，子 parser 就会把 `aether --root /x ui` 的 /x 盖掉
    #:    （与 D13 那条反证是同一个 argparse 行为）。
    ck("A2b ui 与 cli 的 ui 子命令：--root 用 SUPPRESS（继承上层）",
       _acts(ui_p)["root"].default is argparse.SUPPRESS
       and _acts(cli_ui_p)["root"].default is argparse.SUPPRESS,
       f"{_acts(ui_p)['root'].default} / {_acts(cli_ui_p)['root'].default}")
    ck("A2c SUPPRESS 之后有兜底 ⇒ 独立跑 ui 时 root 仍是 paths.ROOT",
       entry.normalize_log_args(ui_p.parse_args([])).root == str(paths.ROOT),
       str(entry.normalize_log_args(ui_p.parse_args([])).root))
    for p_name, p in (("cli", cli_p), ("serve", srv_p), ("ui", ui_p)):
        ck(f"A3 {p_name} --log-level 的 choices == LEVELS（唯一事实源）",
           list(_acts(p)["log_level"].choices) == list(LEVELS),
           str(_acts(p)["log_level"].choices))
    for p_name, p in (("cli", cli_p), ("serve", srv_p), ("ui", ui_p)):
        ck(f"A4 {p_name} --log-json 是开关（store_true）",
           isinstance(_acts(p)["log_json"], argparse._StoreTrueAction))

    #: 反证：把 choices 换成硬编码列表 ⇒ A3 必须转红（否则断言恒真）
    bad = argparse.ArgumentParser()
    bad.add_argument("--log-level", default="INFO",
                     choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    ck("A3x ⛔ 反证：硬编码的 choices 与 LEVELS 的判据必须能分辨"
       "（当前恰好相等，仅证明这条判据**现在**不误伤；"
       "真正的反证见 A3y：少一项就必须红）",
       list(_acts(bad)["log_level"].choices) == list(LEVELS))
    worse = argparse.ArgumentParser()
    worse.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    ck("A3y ⛔ 反证：choices 少一项 ⇒ 判据必须转红（证明 A3 不是恒真）",
       list(_acts(worse)["log_level"].choices) != list(LEVELS))

    # ── B 组：cli 的 ui 子命令 与 aether.ui 逐项同源 ──────────────
    print()
    print("== B 组：`aether ui ...` 与 `python -m aether.ui ...` 逐项一致 ==")
    ck("B1 cli 有 ui 子命令", cli_ui_p is not None)
    a_ui, a_cli = _acts(ui_p), _acts(cli_ui_p)
    ck("B2 参数集合完全相同", set(a_ui) == set(a_cli),
       f"ui-only={sorted(set(a_ui) - set(a_cli))} "
       f"cli-only={sorted(set(a_cli) - set(a_ui))}")
    diff = []
    for dest in sorted(set(a_ui) & set(a_cli)):
        x, y = a_ui[dest], a_cli[dest]
        if (x.default, list(x.choices or []), type(x).__name__) != \
           (y.default, list(y.choices or []), type(y).__name__):
            diff.append(f"{dest}: {x.default}/{y.default}")
    ck("B3 逐项 default / choices / action 一致（漏一项 ⇒ 两边分叉）",
       not diff, str(diff))
    ck("B4 ui 默认挂示例清单，而 cli 的 caps/doctor 默认不挂（差异是刻意的）",
       _acts(_sub(cli_p, "caps"))["caps"].default == entry.NO_CAPS
       and _acts(ui_p)["caps"].default == entry.DEFAULT_CAPS,
       f"{_acts(_sub(cli_p, 'caps'))['caps'].default!r} / "
       f"{_acts(ui_p)['caps'].default!r}")

    # ── C 组：serve 与 ui 的「服务参数」同源 ──────────────────────
    print()
    print("== C 组：ui 的 parser 覆盖 serve 的全部服务参数 ==")
    serve_dests = {d for d in _acts(srv_p)
                   if d not in ("root", "log_level", "log_json")}
    ck("C1 ui 覆盖 serve 的每一个服务参数（含 console ⇒ 由 skip 排除）",
       serve_dests <= set(a_ui) | {"console"},
       str(sorted(serve_dests - set(a_ui))))
    ck("C2 --port 默认值来自 entry.DEFAULT_PORT（不再三处各写一遍）",
       _acts(srv_p)["port"].default == _acts(ui_p)["port"].default
       == entry.DEFAULT_PORT)

    # ── D 组：透传闭合（本轮最要紧的一组）────────────────────────
    #    ⛔ 三层包装：cli → ui → serve。**每一层都以接收方的 parser 为准**
    #      导出清单 ⇒ 手写清单的"漏一项"不可能再发生。
    print()
    print("== D 组：ui → serve 的透传清单由 parser 动态导出 ==")
    argv = ["ui", "--host", "0.0.0.0", "--port", "8700", "--cors", "*",
            "--api-token", "tok-123", "--caps", "caps.json", "--caps-hot",
            "--log-level", "DEBUG", "--log-json", "--no-open"]
    ns = entry.normalize_log_args(cli_p.parse_args(argv))
    fwd = entry.build_forward(ns, parser=srv_p, console="CONSOLE_DIR")
    #: ⛔ 第一跳：cli → ui（目标是 **ui** 的 parser，不是 serve 的）。
    #:    收口前这一跳是手写的，且漏了 log/api-token；用 serve 的 parser
    #:    去导出还会多出 `--console` ⇒ ui 直接 rc=2（实测抓到过）。
    hop1 = entry.build_forward(ns, parser=ui_p)
    ck("D0a 第一跳 cli→ui 不含 --console（ui 的 parser 不认它 ⇒ 传了 rc=2）",
       "--console" not in hop1, str(hop1))
    ck("D0b 第一跳把 --no-open 带上（不再手工拼）",
       "--no-open" in hop1, str(hop1))
    ck("D0c 第一跳把 --api-token 带上", "--api-token" in hop1, str(hop1))

    def _val(flag):
        return fwd[fwd.index(flag) + 1] if flag in fwd else None

    ck("D1 --host 透传", _val("--host") == "0.0.0.0", str(fwd))
    ck("D2 --cors 透传", _val("--cors") == "*", str(fwd))
    ck("D3 --api-token 透传（收口前它根本不在转发清单里）",
       _val("--api-token") == "tok-123", str(fwd))
    ck("D4 --caps 透传", _val("--caps") == "caps.json", str(fwd))
    ck("D5 --caps-hot 透传", "--caps-hot" in fwd, str(fwd))
    ck("D6 --log-level 透传（收口前静默失效的那一项）",
       _val("--log-level") == "DEBUG", str(fwd))
    ck("D7 --log-json 透传", "--log-json" in fwd, str(fwd))
    ck("D8 --console 由 ui 自己给（不接受外部值）",
       _val("--console") == "CONSOLE_DIR", str(fwd))
    ck("D9 --port 用的是**实际**端口（可被 ui 顺延覆盖）",
       entry.build_forward(ns, parser=srv_p, port=8800)[3] == "8800",
       str(entry.build_forward(ns, parser=srv_p, port=8800)))
    #: ⛔ 每一个"有默认值的 serve 参数"都要出现在命令里 —— 用 parser 反推，
    #:    以后给 serve 加参数，这里自动跟着长（不用人记得回来改）。
    expected = {a.option_strings[0] for d, a in _acts(srv_p).items()
                if d not in ("root", "port", "console")}
    missing = [f for f in expected if f not in fwd]
    ck("D10 ⛔ 闭合：serve 的每个参数都在转发命令里（漏 ⇒ 红）",
       not missing, str(missing))
    hop1_expected = {a.option_strings[0] for d, a in _acts(ui_p).items()
                     if d not in ("root", "port")}
    hop1_missing = [f for f in hop1_expected if f not in hop1]
    ck("D10b ⛔ 闭合：ui 的每个参数都在第一跳里（漏 ⇒ 红）",
       not hop1_missing, str(hop1_missing))

    #: 反证 1：ui 缺一个参数 ⇒ **抛错**，不许静默跳过
    class _Ns(dict):
        def __getattr__(self, k):
            try:
                return self[k]
            except KeyError:
                raise AttributeError(k) from None

    broken = _Ns({k: v for k, v in vars(ns).items() if k != "api_token"})
    raised = False
    try:
        entry.build_forward(broken, parser=srv_p)
    except ValueError:
        raised = True
    ck("D11 ⛔ 反证：入口缺参数 ⇒ 抛 ValueError（不是静默丢开关）", raised)

    #: 反证 2：skip 真的生效（证明 D10 的"每个参数都在"不是恒真）
    skipped = entry.build_forward(ns, parser=srv_p, skip=("api_token",))
    ck("D12 ⛔ 反证：skip 掉的项确实不在命令里（证明 D3 不是恒真）",
       "--api-token" not in skipped, str(skipped))

    #: 反证 3：**事故版**证明 SUPPRESS 真在防一个坑 ——
    #: 不用 SUPPRESS 时，主 parser 的值会被子 parser 的默认值盖掉。
    bug = argparse.ArgumentParser(prog="bug")
    entry.add_log_args(bug)                    # 主 parser：默认 INFO
    bsub = bug.add_subparsers(dest="cmd")
    bui = bsub.add_parser("ui")
    entry.add_log_args(bui)                    # ⚠️ 事故版：非 SUPPRESS
    bug_ns = bug.parse_args(["--log-level", "DEBUG", "ui"])
    ck("D13 ⛔ 反证：不用 SUPPRESS ⇒ 子命令的默认值盖掉用户填的值"
       "（bug 版得到 INFO）",
       bug_ns.log_level == "INFO", bug_ns.log_level)
    ok_ns = entry.normalize_log_args(
        cli_p.parse_args(["--log-level", "DEBUG", "ui"]))
    ck("D14 生产版：全局位置写的 --log-level **仍然生效**",
       ok_ns.log_level == "DEBUG", ok_ns.log_level)
    ck("D15 生产版：子命令后写的也生效（两者都得能用）",
       entry.normalize_log_args(
           cli_p.parse_args(["ui", "--log-level", "WARNING"])).log_level
       == "WARNING")

    # ── E 组：布局同源（静态闸 + 运行时）────────────────────────
    print()
    print("== E 组：数据布局只有一份（静态闸 + 运行时对拍）==")
    hits = []
    for f in sorted((ROOT / "aether").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            #: ⛔ 只判**路径拼接**形态（`"data" / "ledger.jsonl"`），
            #:    不判所有字符串常量 —— `"data"` 也是 HTTP 响应里的常见
            #:    键名（embeddings.py 就有），按常量判会误伤 ⇒ 误伤一次
            #:    就会有人加白名单，白名单一开这条闸就废了。
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                for side in (n.left, n.right):
                    if isinstance(side, ast.Constant) and \
                            side.value in _LAYOUT_NAMES:
                        hits.append(f"{f.name}:{n.lineno}")
    ck("E1 ⛔ 静态闸：没有人在路径拼接里手拼布局名", not hits, str(hits))

    #: 反证：注入一行手拼 ⇒ E1 必须转红（否则这条闸是恒真的空账）
    evil = ('x = ROOT / "data" / "ledger.jsonl"\n'
            'y = root / "aether.db"\n')
    evil_hits = []
    for n in ast.walk(ast.parse(evil)):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            for side in (n.left, n.right):
                if isinstance(side, ast.Constant) and \
                        side.value in _LAYOUT_NAMES:
                    evil_hits.append(n.lineno)
    #: ⛔ 期望 3 次而不是 2 次：嵌套的 `ROOT / "data" / "ledger.jsonl"` 是
    #:    两层 BinOp，每层各命中一次 —— 判据抓的是"拼接处"，不是"语句"。
    ck("E1x ⛔ 反证：注入手拼的代码能被同一判据抓到（证明 E1 不是恒真）",
       len(evil_hits) == 3, str(evil_hits))

    from aether import doctor
    d_path = doctor._paths(Path("/tmp/x"))
    p_path = paths.paths(Path("/tmp/x"))
    ck("E2 doctor 与 paths 给出同一个账本路径",
       d_path["ledger"] == p_path["ledger"]
       and d_path["db"] == p_path["db"]
       and d_path["memory"] == p_path["memory"], str(d_path))
    ck("E3 cli 与 paths 同一份（含 tasks_root / task）",
       all(paths.paths(Path("/tmp/x"))[k] == _cli_paths(Path("/tmp/x"))[k]
           for k in ("ledger", "db", "memory", "task", "tasks_root")))

    from aether import backup as B
    ck("E4 backup 的清单与 paths 是**同一个对象**（不是抄一份）",
       B.BACKUP_TARGETS is paths.BACKUP_TARGETS)
    ck("E5 备份扁平名由 paths.flat_name 唯一决定",
       all(paths.BACKUP_FLAT[rel] == paths.flat_name(rel)
           for rel, _ in paths.BACKUP_TARGETS))

    #: 运行时对拍：**真**做一份备份，验证"写进去的名字"与
    #: "验链时读的名字"是同一个（这两处曾各拼一次）。
    tmp = Path(tempfile.mkdtemp(prefix="v6_entry_"))
    from aether.ledger import Ledger
    good = tmp / "good"
    paths.data_dir(good).mkdir(parents=True)
    led = Ledger(paths.paths(good)["ledger"])
    for i in range(4):
        led.append("t.e", {"i": i}, actor="t")
    m = B.create(good, good / "backups")
    flat = paths.flat_name(f"{paths.DATA_DIRNAME}/{paths.LEDGER_NAME}")
    ck("E6 备份目录里躺着的是 flat_name 决定的名字",
       (Path(m.path) / flat).is_file(),
       str([p.name for p in Path(m.path).iterdir()]))
    ck("E7 副本账本链被验过且完整（写与读用的是同一个名字）",
       m.chain_ok is True, f"{m.chain_ok} / {m.chain_reason}")
    ok, problems, _ = B.verify(m.path)
    ck("E8 verify 通过（备份↔布局两处对得上）", ok and not problems,
       str(problems))

    # ── F 组：三个入口的 --help 都能跑（防 import 级回归）────────
    print()
    print("== F 组：三个入口 --help 可执行 ==")
    for mod in ("aether.cli", "aether.serve", "aether.ui"):
        r = subprocess.run([sys.executable, "-m", mod, "--help"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           timeout=60)
        ck(f"F1 {mod} --help → rc=0", r.returncode == 0, r.stderr[-300:])
        ck(f"F2 {mod} --help 含 --log-level",
           "--log-level" in r.stdout, r.stdout[:200])

    # ── G 组：端到端 —— 真的起一次服务，验证透传与鉴权都生效 ──────
    print()
    print("== G 组：端到端起服务（透传的 api-token 必须真生效）==")
    port = 8971
    #: ⛔ `start_new_session`：ui 会再起一个 serve **子进程**。收尾时只
    #:    terminate 父进程 ⇒ serve 变孤儿，占着端口 ⇒ 下一次跑这条契约
    #:    "服务起不来"，而真实原因只是上一次没清干净（假红）。整组一起收。
    proc = subprocess.Popen(
        [sys.executable, "-m", "aether.cli", "ui",
         "--port", str(port), "--api-token", "tok-E2E",
         "--log-level", "WARNING", "--no-open"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    up = False
    try:
        for _ in range(60):
            try:
                #: ⛔ 探活**必须带令牌**：服务是开着鉴权起的，无令牌的请求
                #:    一律 401（那正是 G2 要验的）。拿它探活 ⇒ 永远"没起来"，
                #:    看起来像 G1 坏了，实际是 G2 在生效。
                req = urllib.request.Request(
                    base + "/api/health",
                    headers={"X-API-Key": "tok-E2E"})
                _opener.open(req, timeout=1).read()
                up = True
                break
            except Exception:                                  # noqa: BLE001
                time.sleep(0.25)
        ck("G1 服务起来了（透传出来的命令行真的能跑）", up)
        if not up:
            return 1
        try:
            _opener.open(base + "/api/health", timeout=5).read()
            ck("G2 ⛔ 无令牌 ⇒ 被拒（证明 --api-token 透传**生效**）", False,
               "没有令牌却读到了数据 ⇒ 鉴权形同虚设")
        except Exception as e:                                 # noqa: BLE001
            ck("G2 ⛔ 无令牌 ⇒ 被拒（证明 --api-token 透传**生效**）",
               "401" in str(e) or "403" in str(e), str(e))
        req = urllib.request.Request(base + "/api/health",
                                     headers={"X-API-Key": "tok-E2E"})
        body = json.loads(_opener.open(req, timeout=5).read())
        ck("G3 带令牌 ⇒ 200 且 health 结构在",
           body.get("ok") is True and "uptime_s" in body, str(body)[:200])
        ck("G4 ⛔ 鉴权状态是**读数**（auth_required=True）",
           body.get("auth_required") is True, str(body)[:200])
    finally:
        #: 收掉整个进程组（父 ui + 子 serve），不读管道（避免被孙进程占住
        #: 的 stdout 卡死 —— 实测：只 terminate 父进程会让这里挂住）。
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:                                      # noqa: BLE001
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:                      # noqa: BLE001
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:                                  # noqa: BLE001
                proc.kill()

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   ✗ {f}")
        return 1
    print("✅ 入口契约全部通过：参数三处同源、透传自动闭合、布局只有一份")
    return 0


def _cli_paths(root):
    from aether.cli import _paths
    return _paths(root)


if __name__ == "__main__":
    sys.exit(main())
