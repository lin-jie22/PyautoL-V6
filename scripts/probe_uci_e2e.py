#!/usr/bin/env python3
"""UCI 接口端到端探针：SKILL / AGENT / TOOL 三类能力**是否真能被调用**。

⛔ 为什么不是看 `tests/contract_interop_test.py` 绿了就算数：单测里注册的
是**内存里的 lambda**，而真实链路是「JSON 清单 → 适配器 → transport →
进程/网络」。两者之间隔着一整个装载层 —— 实测过"单测全绿、清单里那条
因为 trusted 没声明而被 skip"的落差。所以这里全程走**真实清单 + 真实
transport**，并对每条能力做**可达性计数**（调用了几次、各自分诊为什么）。

五组：
    A 装载     —— 清单逐条 loaded/skipped，skip 必须有原因
    B CLI 入口 —— `python -m aether.cli caps invoke`（skill/tool/agent）
    C HTTP 入口 —— `/api/capabilities` + `/api/invoke`（含拒绝面）
    D harness   —— bind_capability ⇒ L1 路由 ⇒ L3 真调用外部 skill
    E 反证      —— 服务不在 / 缺参 / 未注册 / 携带实现描述，必须分诊出来

跑法：python scripts/probe_uci_e2e.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list = []
N_CK = [0]


def ck(name: str, cond: bool, detail: str = "") -> None:
    N_CK[0] += 1
    if cond:
        print(f"  ✅ {name}")
    else:
        FAILS.append(name)
        print(f"  ❌ {name}  ← {detail}")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = int(s.getsockname()[1])
    s.close()
    return p


def _opener():
    """⛔ 环境里有 HTTP_PROXY：`urllib` 默认会走代理 ⇒ 访问 localhost
    变成访问代理 ⇒ 502 会被误读成"接口坏了"。显式关掉。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ── 模拟外部 agent 服务（http transport 的对端）────────────────────
class _AgentH(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            args = json.loads(raw or b"{}")
        except Exception:
            args = {}
        body = json.dumps(
            {"out": str(args.get("text", "")).upper() + "|by-agent"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_agent_service() -> tuple:
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _AgentH)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, port


def wait_port(port: int, deadline: float = 15.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def http_json(url: str, method: str = "GET", payload=None) -> tuple:
    """返回 (http_code, dict)。连不上也如实返回（code=0）。"""
    op = _opener()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=20) as r:
            return int(r.status), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return int(e.code), json.loads(raw or b"{}")
        except Exception:
            return int(e.code), {"_raw": raw[:200].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return 0, {"_err": f"{type(e).__name__}: {e}"}


def cli_caps(args: list, manifest: str) -> tuple:
    """调 CLI 入口，返回 (rc, dict|None)。"""
    cmd = [sys.executable, "-m", "aether.cli", "caps",
           "--caps", manifest] + args
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True,
                       text=True, timeout=120)
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, None


# ──────────────────────────────────────────────────────────────
def main() -> int:
    from aether.interop import Gateway, Registry, load_manifest

    src_manifest = ROOT / "capabilities.example.json"
    agent_srv, agent_port = start_agent_service()
    dead_port = free_port()          #: 没人监听 ⇒ agent 服务"不在"的反证

    tmpdir = tempfile.mkdtemp(prefix="uci_probe_")
    #: 清单副本：只把 ext.service 的 url 指向本探针起的真实服务
    raw = json.loads(src_manifest.read_text(encoding="utf-8"))
    for c in raw["capabilities"]:
        if c["id"] == "ext.service":
            c["impl"]["url"] = f"http://127.0.0.1:{agent_port}/invoke"
    live_manifest = os.path.join(tmpdir, "caps_live.json")
    Path(live_manifest).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    #: 反证清单：服务指向空端口
    raw_dead = json.loads(json.dumps(raw))
    for c in raw_dead["capabilities"]:
        if c["id"] == "ext.service":
            c["impl"]["url"] = f"http://127.0.0.1:{dead_port}/invoke"
    dead_manifest = os.path.join(tmpdir, "caps_dead.json")
    Path(dead_manifest).write_text(
        json.dumps(raw_dead, ensure_ascii=False, indent=2), encoding="utf-8")

    # ═══ A 组：装载 ═══════════════════════════════════════════
    print("\nA 组 · 清单装载（逐条记因）")
    reg = Registry()
    rep = load_manifest(reg, live_manifest)
    print(f"  loaded={rep.loaded} skipped={[s.get('id') for s in rep.skipped]}")
    ck("A1 清单 4 条全部装载（没有静默 skip）",
       len(rep.loaded) == 4 and not rep.skipped, str(rep.skipped))
    kinds = {s.id: s.kind for s in reg.list()}
    ck("A2 三类 kind 齐备：skill×2 / agent / tool",
       kinds.get("ext.upper") == "skill" and kinds.get("ext.repairer") == "skill"
       and kinds.get("ext.service") == "agent"
       and kinds.get("ext.reverse") == "tool", str(kinds))

    reg_chk = Registry()
    rep_chk = load_manifest(reg_chk, {"capabilities": [{
        "id": "chk.x", "kind": "skill", "trusted": True,
        "inputs": {"text": {"type": "str", "required": True}},
        "outputs": {"out": {"type": "str", "required": True}},
        "checker": "some.module:fn",
        "impl": {"transport": "python",
                 "target": "scripts.ext_upper:upper"}}]})
    ck("A3 ⛔ 清单声明 checker ⇒ 注册被拒（不是静默忽略）",
       not rep_chk.loaded and len(rep_chk.skipped) == 1
       and rep_chk.skipped[0].get("status") == "rejected",
       str(rep_chk.skipped)[:120])

    # ═══ B 组：CLI 入口（真实 transport）══════════════════════
    print("\nB 组 · CLI 入口 `caps invoke`（进程内真实调用）")
    cases = [
        ("B1 SKILL inproc  ext.upper", "ext.upper",
         {"text": "hello skill"}, "inproc", "HELLO SKILL"),
        ("B2 SKILL inproc  ext.repairer", "ext.repairer",
         {"source": "return n >= 3"}, "inproc", None),
        ("B3 TOOL cli      ext.reverse", "ext.reverse",
         {"text": "abcdef"}, "subprocess", "fedcba"),
        ("B4 AGENT http    ext.service", "ext.service",
         {"text": "call agent"}, "network", None),
    ]
    for name, cid, args, want_transport, want_out in cases:
        rc, d = cli_caps(["invoke", cid, "--json", json.dumps(args)],
                         live_manifest)
        ok = (d is not None and d.get("status") == "ok" and rc == 0
              and d.get("transport") == want_transport)
        if want_out is not None:
            ok = ok and (d or {}).get("output", {}).get("out") == want_out
        ck(name, ok, f"rc={rc} got={(d or {}).get('status')} "
                     f"transport={(d or {}).get('transport')} "
                     f"out={(d or {}).get('output')}")

    #: 可达性计数：网关真的被走到几次
    gw = Gateway(reg)
    for cid, args in [("ext.upper", {"text": "x"}),
                      ("ext.service", {"text": "y"}),
                      ("ext.reverse", {"text": "z"})]:
        gw.invoke(cid, args, actor="probe")
    ck("B5 可达性计数：3 次调用全部记入网关统计",
       gw.n_invoked == 3 and gw.stats.get("ok", 0) == 3,
       f"n_invoked={gw.n_invoked} stats={gw.stats}")

    # ═══ C 组：HTTP 入口 ═════════════════════════════════════
    print("\nC 组 · HTTP 入口 `/api/capabilities` + `/api/invoke`")
    api_port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "aether.serve", "--caps", live_manifest,
         "--port", str(api_port)], cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait_port(api_port):
            ck("C0 API 服务起得来", False, "端口未就绪")
            return 1
        base = f"http://127.0.0.1:{api_port}"

        code, d = http_json(f"{base}/api/capabilities")
        ids = {c["id"] for c in d.get("capabilities", [])}
        ck("C1 GET /api/capabilities 列出 4 条能力",
           code == 200 and len(d.get("capabilities", [])) == 4,
           f"code={code} {d}")
        code_s, d_skill = http_json(f"{base}/api/capabilities?kind=skill")
        ck("C1b ⛔ 过滤后 n 跟随过滤、n_total 恒为总数",
           d.get("n") == 4 and d.get("n_total") == 4
           and d_skill.get("n") == 2 and d_skill.get("n_total") == 4,
           f"all={d.get('n')}/{d.get('n_total')} "
           f"skill={d_skill.get('n')}/{d_skill.get('n_total')}")
        ck("C2 清单里含 skill 与 agent 两类",
           {"ext.upper", "ext.repairer"} <= ids and "ext.service" in ids,
           str(sorted(ids)))

        for cid, args, want in [("ext.upper", {"text": "http skill"},
                                 "HTTP SKILL"),
                                ("ext.service", {"text": "http agent"},
                                 None),
                                ("ext.reverse", {"text": "httpcli"},
                                 "ilcptth")]:
            code, d = http_json(f"{base}/api/invoke", "POST",
                                {"capability": cid, "args": args})
            ok = code == 200 and d.get("status") == "ok"
            if want is not None:
                ok = ok and d.get("output", {}).get("out") == want
            ck(f"C3 POST /api/invoke {cid}", ok,
               f"code={code} status={d.get('status')} out={d.get('output')}")

        #: 拒绝面：这些**必须**被挡住，否则端点就是远程执行
        code, d = http_json(f"{base}/api/invoke", "POST",
                            {"capability": "ext.upper", "args": {"text": "x"},
                             "impl": {"transport": "python"}})
        ck("C4 ⛔ 请求体携带实现描述 ⇒ 400（不是远程执行任意代码）",
           code == 400, f"code={code} {d}")
        code, d = http_json(f"{base}/api/invoke", "POST",
                            {"capability": "no.such.cap", "args": {}})
        ck("C5 未注册能力 ⇒ 422 且 status=unavailable",
           code == 422 and d.get("status") == "unavailable",
           f"code={code} {d}")
        code, d = http_json(f"{base}/api/invoke", "POST",
                            {"capability": "ext.upper", "args": {}})
        ck("C6 缺必填输入 ⇒ 422 且 status=rejected（fail-closed，不'尽力调'）",
           code == 422 and d.get("status") == "rejected",
           f"code={code} {d}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ═══ D 组：harness 端到端（AGENT 真的用上 SKILL）══════════
    print("\nD 组 · harness：bind_capability ⇒ L1 路由 ⇒ L3 真调用外部 skill")
    from aether.agent import HarnessAgent
    from aether.environment import Environment, mutate_source
    from aether.genome import Harness
    from aether.interop import bind_capability

    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    broken, _ = mutate_source(env.source, which=2)
    envs = {env.env_id: env}
    SPEC = {"env_id": env.env_id, "broken_source": broken,
            "topic": env.env_id, "task_class": "repair"}
    print(f"  env={env.env_id} broken 片段："
          f"{[l for l in broken.splitlines() if '>' in l or '<' in l][:2]}")

    h0 = Harness(meta_rules="V6 元规则", routing={},
                 tools=("read_file", "write_file", "python_exec"),
                 skills={}, memory_policy={})
    h1 = bind_capability(h0, "ext.repairer", "外部修复技能", route_from="repair")
    ck("D1 绑定改写了 skills 与 routing ⇒ harness_id 变（技能进了基因组）",
       h1.harness_id != h0.harness_id
       and "uci:ext.repairer" in h1.skills
       and "uci:ext.repairer" in str(h1.routing.get("repair")),
       f"{h1.skills} routing={h1.routing}")

    r = HarnessAgent(envs, gateway=gw).run(h1, SPEC)
    uci_steps = [s for s in r.steps if s.action == "uci"]
    ck("D2 外部 skill 被 L1 路由命中且真的执行（reward 来自环境判据）",
       r.strategy == "uci:ext.repairer" and r.reward == 1.0
       and any(s.ok for s in uci_steps),
       f"strategy={r.strategy} reward={r.reward} "
       f"steps={[(s.layer, s.action, s.ok) for s in r.steps]}")
    ck("D3 L3 步骤留下 UCI 调用详情（含'未验证'如实标注）",
       any("ext.repairer" in s.detail for s in uci_steps),
       str([s.detail[:90] for s in uci_steps]))

    r_no = HarnessAgent(envs, gateway=None).run(h1, SPEC)
    u2 = [s for s in r_no.steps if s.action == "uci"]
    ck("D4 ⛔ 对照组：无网关 ⇒ 显式记不可达，不悄悄退化成无策略",
       u2 and not u2[0].ok and r_no.reward == 0.0,
       f"{[s.detail[:60] for s in u2]} reward={r_no.reward}")

    # ═══ E 组：反证（服务不在 / 坏清单）════════════════════════
    print("\nE 组 · 反证：失败了必须能被分诊出来，不能混成 ok")
    rc, d = cli_caps(["invoke", "ext.service", "--json", '{"text":"x"}'],
                     dead_manifest)
    ck("E1 agent 服务不在 ⇒ 非 ok 且带 error（不是静默成功）",
       d is not None and d.get("status") != "ok" and bool(d.get("error"))
       and rc != 0,
       f"rc={rc} status={(d or {}).get('status')} "
       f"error={(d or {}).get('error')[:80]}")
    bad = os.path.join(tmpdir, "caps_bad.json")
    Path(bad).write_text(json.dumps({"capabilities": [
        {"id": "bad.untrusted", "kind": "skill", "trusted": False,
         "inputs": {"text": {"type": "str", "required": True}},
         "impl": {"transport": "python", "target": "scripts.ext_upper:upper"}},
    ]}), encoding="utf-8")
    reg2 = Registry()
    rep2 = load_manifest(reg2, bad)
    ck("E2 ⛔ trusted:false + python 形态 ⇒ 注册被拒（且写明原因）",
       not rep2.loaded and rep2.skipped
       and rep2.skipped[0].get("status") == "rejected"
       and bool(rep2.skipped[0].get("reason")),
       str(rep2.skipped))
    rc, d = cli_caps(["invoke", "ext.upper", "--json", '{"text":123}'],
                     live_manifest)
    ck("E3 类型不合规 ⇒ rejected（输入校验在调用之前）",
       d is not None and d.get("status") == "rejected",
       str((d or {}).get("error"))[:80])

    # ═══ F 组：timeout_s 是不是真仪表 ═════════════════════════
    print("\nF 组 · 声明的 timeout_s 能不能真的中断（三种 transport 逐个实测）")
    from aether.interop import load_manifest as _lm  # noqa: F401

    slow_py = os.path.join(tmpdir, "slow_cli.py")
    Path(slow_py).write_text(
        "import json,time\ntime.sleep(5)\nprint(json.dumps({'out':'slow'}))\n",
        encoding="utf-8")
    slow_mod = os.path.join(tmpdir, "uci_slow_mod.py")
    Path(slow_mod).write_text(
        "import time\n\ndef slow(text=''):\n"
        "    time.sleep(1.5)\n    return {'out': 'slow-' + str(text)}\n",
        encoding="utf-8")
    sys.path.insert(0, tmpdir)

    class _SlowH(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            time.sleep(5)
            body = b'{"out":"slow"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    slow_srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowH)
    slow_port = int(slow_srv.server_address[1])
    threading.Thread(target=slow_srv.serve_forever, daemon=True).start()

    reg3 = Registry()
    rep3 = load_manifest(reg3, {"capabilities": [
        {"id": "slow.cli", "kind": "tool", "trusted": False,
         "sandbox": "process", "timeout_s": 1.0,
         "inputs": {"text": {"type": "str", "required": True}},
         "outputs": {"out": {"type": "str", "required": True}},
         "impl": {"transport": "cli", "argv": [sys.executable, slow_py]}},
        {"id": "slow.http", "kind": "agent", "trusted": False,
         "sandbox": "none", "timeout_s": 1.0,
         "inputs": {"text": {"type": "str", "required": True}},
         "outputs": {"out": {"type": "str", "required": False}},
         "impl": {"transport": "http",
                 "url": f"http://127.0.0.1:{slow_port}/invoke"}},
        {"id": "slow.inproc", "kind": "skill", "trusted": True,
         "timeout_s": 0.5,
         "inputs": {"text": {"type": "str", "required": True}},
         "outputs": {"out": {"type": "str", "required": True}},
         "impl": {"transport": "python", "target": "uci_slow_mod:slow"}},
    ]})
    ck("F0 三条慢能力都装载成功", len(rep3.loaded) == 3, str(rep3.skipped))
    gw3 = Gateway(reg3)
    r_cli = gw3.invoke("slow.cli", {"text": "x"}, actor="probe")
    ck("F1 TOOL cli：超时真能中断（status=timeout，且没跑满 5s）",
       r_cli.status == "timeout" and r_cli.cost_s < 3.0
       and r_cli.timeout_enforced == "forced",
       f"status={r_cli.status} cost={r_cli.cost_s:.2f}s "
       f"enforced={r_cli.timeout_enforced}")
    ck("F1c 被中断的那次**不算**落差（兑现了，不是没兑现）",
       abs(r_cli.timeout_declared_s - 1.0) < 1e-6
       and r_cli.timeout_declared_but_unenforced is False,
       f"declared={r_cli.timeout_declared_s} "
       f"flag={r_cli.timeout_declared_but_unenforced}")
    r_http = gw3.invoke("slow.http", {"text": "x"}, actor="probe")
    ck("F2 AGENT http：超时真能中断",
       r_http.status == "timeout" and r_http.cost_s < 3.0
       and r_http.timeout_enforced == "forced",
       f"status={r_http.status} cost={r_http.cost_s:.2f}s")
    r_in = gw3.invoke("slow.inproc", {"text": "x"}, actor="probe")
    ck("F3 SKILL inproc：`timeout_enforced=none` 是**如实**标注（真的杀不掉）",
       r_in.status == "ok" and r_in.cost_s >= 1.4
       and r_in.timeout_enforced == "none",
       f"status={r_in.status} cost={r_in.cost_s:.2f}s "
       f"enforced={r_in.timeout_enforced}")
    ck("F3b ⛔ 声明 0.5s 实跑 1.5s ⇒ 落差**被独立记账**（r29 新增字段）",
       abs(r_in.timeout_declared_s - 0.5) < 1e-6
       and r_in.timeout_declared_but_unenforced is True,
       f"declared={r_in.timeout_declared_s} cost={r_in.cost_s:.2f}s "
       f"flag={r_in.timeout_declared_but_unenforced}")
    #: ⛔ 对照组：**同一条慢能力**把 timeout 放宽到 30s 必须跑满并成功。
    #:    没有这一条，"status=timeout" 也可能只是脚本路径写错 / 子进程崩了
    #:    ⇒ 会把"根本没跑起来"误读成"超时机制生效"。
    reg4 = Registry()
    load_manifest(reg4, {"capabilities": [{
        "id": "slow.cli", "kind": "tool", "trusted": False,
        "sandbox": "process", "timeout_s": 30.0,
        "inputs": {"text": {"type": "str", "required": True}},
        "outputs": {"out": {"type": "str", "required": True}},
        "impl": {"transport": "cli", "argv": [sys.executable, slow_py]}}]})
    r_wide = Gateway(reg4).invoke("slow.cli", {"text": "x"}, actor="probe")
    ck("F1b 对照组：timeout 放宽到 30s ⇒ 同一条能力跑满并成功"
       "（证明 F1 的 timeout 真是超时机制，不是没跑起来）",
       r_wide.status == "ok" and r_wide.cost_s >= 4.5
       and (r_wide.output or {}).get("out") == "slow",
       f"status={r_wide.status} cost={r_wide.cost_s:.2f}s "
       f"out={r_wide.output}")
    ck("F1d 对照组：放宽后没跑超 ⇒ 落差为 False（生效预算取更严的 10.0）",
       abs(r_wide.timeout_declared_s - 10.0) < 1e-6
       and r_wide.timeout_declared_but_unenforced is False,
       f"declared={r_wide.timeout_declared_s} "
       f"flag={r_wide.timeout_declared_but_unenforced}")

    #: ⛔ 这条 ℹ️ 是**修复前**的读数（r29 之前只有 `timeout_enforced` 一处
    #:    形态读数，看不出"这一次"超没超）。现在已有 `timeout_declared_s` +
    #:    `timeout_declared_but_unenforced`，保留这行是为了让回归时能看出
    #:    "落差被记账了"而不是"落差消失了"。
    print(f"  ℹ️  落差读数：slow.inproc 声明 timeout_s=0.5，实跑 "
          f"{r_in.cost_s:.2f}s 未被中断 ⇒ "
          f"timeout_declared_s={r_in.timeout_declared_s}、"
          f"timeout_declared_but_unenforced="
          f"{r_in.timeout_declared_but_unenforced}（落差可见）")
    slow_srv.shutdown()

    # ── 汇总 ──────────────────────────────────────────────────
    print(f"\n{'=' * 62}")
    print(f"UCI 接口探针：{N_CK[0] - len(FAILS)}/{N_CK[0]} 通过")
    if FAILS:
        for f in FAILS:
            print(f"  ❌ {f}")
    agent_srv.shutdown()
    print("=" * 62)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
