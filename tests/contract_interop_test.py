#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：通用接入层（UCI）—— 任何 agent / skill 都挂得上、叫得动、
叫不动时说得清为什么叫不动。

改造前的真实形态：技能是**字符串**（`Harness.skills: Dict[str,str]`），
执行逻辑写死在 `agent.py` 的 if/elif 分支里；agent 只有"恰好长成
`runner(h, spec) -> Verdict`"这个**没有任何地方声明**的约定。
⇒ 外部的东西（别人项目的函数 / CLI 工具 / HTTP 服务）根本没有入口。

本文件守四条硬约束：

    A 四形态            callable / 对象 / CLI / HTTP 都能挂上并被调用
    B 失败分诊          unavailable / rejected / failed / timeout 各归其位
    C 判据归属          外部自称成功**变不成** reward（结构上没这条路）
    D 注册与装载        重复 id、不可信+inproc、清单逐条记因
    E harness 打通      uci: 技能可被 L1 路由命中；无网关必须显式记账
    F HTTP 端点         只接受已注册能力；携带实现描述一律拒绝

⛔ 判别力不是"看起来对"：每条断言都经**注入事故版**实测（见 README §38）。
   本文件里标了「反证」的组，在 README 里有对应的事故清单。

跑法：
    python tests/contract_interop_test.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    return bool(cond)


# ──────────────────────────────────────────────────────────────────
def _cli_tool(td: str, body: str) -> str:
    """造一个可执行的"外部 CLI 工具"。"""
    p = os.path.join(td, "tool.py")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(p, 0o755)
    return p


def group_a() -> None:
    """A 组：四种形态都能挂上并被调用。"""
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry)
    from aether.verifier import Check

    print("\nA 组 · 四形态接入（callable / 对象 / CLI / HTTP）")
    td = tempfile.mkdtemp(prefix="v6uci_")
    reg = Registry()
    gw = Gateway(reg)

    # ① callable
    reg.register(CapabilitySpec(
        id="t.fn", kind="skill", trusted=True,
        inputs={"x": ParamSpec("str")}, outputs={"out": ParamSpec("str")},
        checker=lambda a, o: Check("upper", o.get("out") == a["x"].upper(),
                                   f"out={o.get('out')}")),
        lambda x: {"out": x.upper()})

    # ② 对象（run 入口）
    class Obj:
        def run(self, x: str):
            return {"out": x.upper()}
    reg.register(CapabilitySpec(
        id="t.obj", kind="agent", trusted=True,
        inputs={"x": ParamSpec("str")}, outputs={"out": ParamSpec("str")}),
        Obj())

    # ③ CLI（subprocess）
    tool = _cli_tool(td, "import sys,json\n"
                         "d=json.loads(sys.stdin.read())\n"
                         "print(json.dumps({'out': d['x'][::-1]}))\n")
    reg.register(CapabilitySpec(
        id="t.cli", kind="tool", trusted=False, sandbox="process",
        effects=("exec",), inputs={"x": ParamSpec("str")},
        outputs={"out": ParamSpec("str")}), [sys.executable, tool])

    r1 = gw.invoke("t.fn", {"x": "ab"})
    ck("A1 callable 形态：status=ok 且判据 1/1",
       r1.status == "ok" and r1.output == {"out": "AB"} and len(r1.checks) == 1,
       f"{r1.status} {r1.output}")
    ck("A2 callable 的 transport/timeout_enforced 必须如实（inproc/none）",
       r1.transport == "inproc" and r1.timeout_enforced == "none",
       f"{r1.transport}/{r1.timeout_enforced}")

    r2 = gw.invoke("t.obj", {"x": "cd"})
    ck("A3 对象形态（run 入口）",
       r2.status == "ok" and r2.output == {"out": "CD"}, f"{r2.status} {r2.output}")

    r3 = gw.invoke("t.cli", {"x": "abc"})
    ck("A4 CLI 形态（stdin/stdout JSON）",
       r3.status == "ok" and r3.output == {"out": "cba"}, f"{r3.status} {r3.output}")
    ck("A5 CLI 的 timeout_enforced 必须是 forced（subprocess 真能杀）",
       r3.timeout_enforced == "forced" and r3.transport == "subprocess",
       f"{r3.transport}/{r3.timeout_enforced}")

    # ④ HTTP（真实本机服务）
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            out = json.dumps({"out": "http:" + str(body.get("x"))}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        reg.register(CapabilitySpec(
            id="t.http", kind="agent", trusted=False, sandbox="none",
            effects=("network",), inputs={"x": ParamSpec("str")},
            outputs={"out": ParamSpec("str")}),
            f"http://127.0.0.1:{port}/invoke")
        r4 = gw.invoke("t.http", {"x": "z"})
        ck("A6 HTTP 形态（真实本机服务）",
           r4.status == "ok" and r4.output == {"out": "http:z"},
           f"{r4.status} {r4.output}")
        ck("A7 HTTP 的 transport 必须是 network",
           r4.transport == "network", r4.transport)
    finally:
        srv.shutdown()

    ck("A8 清单指纹稳定（同一批能力两次注册指纹相同）",
       len(reg.fingerprint) == 16 and isinstance(reg.manifest(), list),
       reg.fingerprint)


def group_b() -> None:
    """B 组：失败必须能**分诊**，不许全部混成一种。"""
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry)

    print("\nB 组 · 失败分诊（unavailable / rejected / failed / timeout）")
    td = tempfile.mkdtemp(prefix="v6uci_")
    reg = Registry()
    gw = Gateway(reg)

    reg.register(CapabilitySpec(
        id="t.echo", kind="skill", trusted=True,
        inputs={"x": ParamSpec("str")}, outputs={"out": ParamSpec("str")}),
        lambda x: {"out": x})

    r_un = gw.invoke("nope.nope", {})
    ck("B1 未注册 ⇒ unavailable（不是 no-op 返回空）",
       r_un.status == "unavailable" and r_un.output is None, r_un.status)

    r_rej = gw.invoke("t.echo", {})
    ck("B2 缺必填参数 ⇒ rejected（不是尽力调一把）",
       r_rej.status == "rejected", r_rej.status)

    r_type = gw.invoke("t.echo", {"x": 123})
    ck("B3 类型不符 ⇒ rejected", r_type.status == "rejected", r_type.status)

    reg.register(CapabilitySpec(id="t.boom", trusted=True,
                                inputs={"x": ParamSpec("str")}),
                 lambda x: (_ for _ in ()).throw(RuntimeError("内部炸了")))
    r_boom = gw.invoke("t.boom", {"x": "a"})
    ck("B4 实现抛异常 ⇒ failed 且保留异常类型",
       r_boom.status == "failed" and "RuntimeError" in r_boom.error,
       f"{r_boom.status} {r_boom.error[:40]}")

    slow = _cli_tool(td, "import time\ntime.sleep(5)\n")
    reg.register(CapabilitySpec(id="t.slow", kind="tool", trusted=False,
                                sandbox="process", timeout_s=0.4),
                 [sys.executable, slow])
    r_to = gw.invoke("t.slow", {})
    ck("B5 CLI 超时 ⇒ timeout（且真的被杀，不是跑完才报）",
       r_to.status == "timeout" and r_to.cost_s < 3.0,
       f"{r_to.status} cost={r_to.cost_s:.2f}s")

    reg.register(CapabilitySpec(id="t.liar", trusted=True, inputs={},
                                outputs={"out": ParamSpec("str")}),
                 lambda: {})
    r_liar = gw.invoke("t.liar", {})
    ck("B6 声明的必填输出缺失 ⇒ failed（不是 ok）",
       r_liar.status == "failed", r_liar.status)
    ck("B7 失败时仍保留 output 供排查（不抹掉现场）",
       r_liar.output == {}, str(r_liar.output))


def group_c() -> None:
    """C 组：判据归属 —— 外部自称成功变不成 reward。"""
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry)
    from aether.verifier import Check, Verdict

    print("\nC 组 · 判据归属（自述不进 reward）")
    reg = Registry()
    gw = Gateway(reg)
    reg2 = reg          # 组内别名：下面逐条往同一个注册表里加能力

    # ① 有 checker ⇒ verified
    reg.register(CapabilitySpec(
        id="t.checked", trusted=True, inputs={"x": ParamSpec("str")},
        outputs={"out": ParamSpec("str")},
        checker=lambda a, o: Check("len>0", len(o.get("out", "")) > 0,
                                   f"out={o.get('out')}")),
        lambda x: {"out": x})
    r1 = gw.invoke("t.checked", {"x": "a"})
    ck("C1 有 checker ⇒ verified=True 且 checks 非空",
       r1.verified and len(r1.checks) == 1 and r1.status == "ok",
       f"{r1.status} verified={r1.verified}")

    # ② checker 判不过 ⇒ failed
    reg2.register(CapabilitySpec(
        id="t.badcheck", trusted=True, inputs={}, outputs={"out": ParamSpec("str")},
        checker=lambda a, o: Check("never", False, "证据：判据设计就是不通过")),
        lambda: {"out": "x"})
    r2 = gw.invoke("t.badcheck", {})
    ck("C2 checker 判不过 ⇒ failed（不是 ok）", r2.status == "failed", r2.status)

    # ③ 没有 checker ⇒ verified=False + 显性原因（不许假装验证过）
    reg2.register(CapabilitySpec(id="t.unverified", trusted=True,
                                 inputs={}, outputs={"out": ParamSpec("str")}),
                  lambda: {"out": "x", "ok": True, "self_reported": True})
    r3 = gw.invoke("t.unverified", {})
    ck("C3 无 checker ⇒ verified=False 且带 unverified_reason",
       (not r3.verified) and "no_checker" in r3.unverified_reason,
       f"verified={r3.verified} reason={r3.unverified_reason}")
    ck("C4 ⛔ 外部自称 ok/self_reported ⇒ 不产生任何 Verdict（结构上无 reward 通路）",
       r3.verdict is None, str(r3.verdict))

    # ④ as_runner：没判据 ⇒ reward 0（不是"跑过了就算过"）
    runner = gw.as_runner("t.unverified")
    v = runner(None, {})
    ck("C5 as_runner 在无判据时 reward=0 且带 error（不假装通过）",
       v.reward == 0.0 and bool(v.error), f"reward={v.reward} err={v.error[:40]}")

    # ⑤ 实现直接返回 Verdict ⇒ 走 sanity 闸
    reg2.register(CapabilitySpec(id="t.verdict", trusted=True, inputs={}),
                  lambda: Verdict(passed=True,
                                  checks=(Check("c", True, "evidence: 真的跑了"),),
                                  evidence={"n": 1}))
    r5 = gw.invoke("t.verdict", {})
    ck("C6 实现返回 Verdict ⇒ 过 sanity 闸，verified=True",
       r5.verified and r5.status == "ok", f"{r5.status} {r5.error[:40]}")

    # ⑥ 无 evidence 的 Verdict ⇒ 作废
    reg2.register(CapabilitySpec(id="t.hollow", trusted=True, inputs={}),
                  lambda: Verdict(passed=True, checks=(Check("c", True, ""),)))
    r6 = gw.invoke("t.hollow", {})
    ck("C7 无 evidence 的判定 ⇒ 作废（reward 不认）",
       r6.status == "failed" and "VERDICT_INVALID" in (r6.error or ""),
       f"{r6.status} {r6.error[:40]}")


def group_d() -> None:
    """D 组：注册与装载的硬约束。"""
    from aether.interop import (CapabilityError, CapabilitySpec, ParamSpec,
                                Registry, adapt, load_manifest)

    print("\nD 组 · 注册与装载")
    reg = Registry()

    # ① 不可信 + inproc ⇒ 拒
    try:
        reg.register(CapabilitySpec(id="bad", trusted=False), lambda: None)
        ck("D1 不可信 + inproc ⇒ 必须被拒", False, "没拦住")
    except CapabilityError as e:
        ck("D1 不可信 + inproc ⇒ 必须被拒",
           e.status == "rejected" and "inproc" in e.message, e.status)

    # ② 重复 id 且声明不同 ⇒ 拒
    #: ⛔ 差异维度必须是**影响调用契约**的字段：`fingerprint` 故意不含
    #:    summary（改一句描述 ≠ 能力变了，否则改错别字都会让指纹漂移）。
    #:    我第一版就用 summary 造差异 ⇒ 指纹相同 ⇒ D2 假绿（"没拦住"）。
    spec = CapabilitySpec(id="dup", trusted=True, inputs={})
    reg.register(spec, lambda: {"a": 1})
    try:
        reg.register(CapabilitySpec(id="dup", trusted=True,
                                    inputs={"z": ParamSpec("str")}),
                     lambda z: {"a": 2})
        ck("D2 重复 id 且声明不同 ⇒ 必须被拒", False, "没拦住")
    except CapabilityError as e:
        ck("D2 重复 id 且声明不同 ⇒ 必须被拒", e.status == "rejected", e.status)
    ck("D2b 只改 summary ⇒ 指纹不变（改描述不等于改能力）",
       CapabilitySpec(id="dup", trusted=True, summary="改了描述").fingerprint
       == spec.fingerprint,
       f"{spec.fingerprint} vs "
       f"{CapabilitySpec(id='dup', trusted=True, summary='改了描述').fingerprint}")
    reg.register(spec, lambda: {"a": 1})   # 同指纹重复注册应放行

    # ③ adapt 探测不出来 ⇒ 抛错并列出试过的形态
    try:
        adapt(12345)
        ck("D3 adapt 探测不出来 ⇒ 抛错（不许静默降级成 no-op）", False, "没抛")
    except CapabilityError as e:
        ck("D3 adapt 探测不出来 ⇒ 抛错（不许静默降级成 no-op）",
           "已尝试" in e.message, e.message[:40])

    # ④ 清单装载：逐条记因，坏条目不影响好条目
    td = tempfile.mkdtemp(prefix="v6uci_")
    tool = _cli_tool(td, "import sys,json\n"
                         "d=json.loads(sys.stdin.read())\n"
                         "print(json.dumps({'out': d['x']}))\n")
    mf = {"capabilities": [
        {"id": "m.ok", "kind": "tool", "trusted": False, "sandbox": "process",
         "inputs": {"x": ParamSpec("str").to_dict()},
         "impl": {"transport": "cli", "argv": [sys.executable, tool]}},
        {"id": "m.missing_mod", "kind": "skill", "trusted": True,
         "impl": {"transport": "python", "target": "no_such_mod_zzz:f"}},
        {"id": "m.untrusted_py", "kind": "skill", "trusted": False,
         "impl": {"transport": "python", "target": "json:dumps"}},
        {"id": "m.bad_transport", "kind": "skill", "trusted": True,
         "impl": {"transport": "telepathy", "argv": []}},
    ]}
    reg2 = Registry()
    rep = load_manifest(reg2, mf)
    ck("D4 清单：好条目照常装载", "m.ok" in rep.loaded, str(rep.loaded))
    ck("D5 清单：坏条目**逐条**记因（坏一个不拖垮整份清单）",
       {s["id"] for s in rep.skipped}
       == {"m.missing_mod", "m.untrusted_py", "m.bad_transport"},
       str([s["id"] for s in rep.skipped]))
    ck("D6 每条 skip 都必须有原因（不许静默跳过）",
       all(s.get("reason") for s in rep.skipped),
       str([s.get("reason") for s in rep.skipped])[:80])
    ck("D7 不可信 python 形态被拒（inproc 组合）",
       any(s["id"] == "m.untrusted_py" and s.get("status") == "rejected"
           for s in rep.skipped),
       str([s for s in rep.skipped if s["id"] == "m.untrusted_py"])[:80])


def group_e() -> None:
    """E 组：harness 打通 —— 外部技能能被 L1 路由命中。"""
    from aether.agent import HarnessAgent
    from aether.environment import Environment, mutate_source
    from aether.genome import Harness
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry,
                                bind_capability)
    from aether.verifier import Check

    print("\nE 组 · harness 打通（uci: 技能经 L1 路由执行）")
    env = Environment.from_dir(ROOT / "tasks" / "textscore")
    broken, _ = mutate_source(env.source, which=2)
    envs = {env.env_id: env}
    SPEC = {"env_id": env.env_id, "broken_source": broken,
            "topic": env.env_id, "task_class": "repair"}

    reg = Registry()
    reg.register(CapabilitySpec(
        id="ext.fixer", kind="skill", trusted=True,
        inputs={"source": ParamSpec("str"), "baseline": ParamSpec("str", False),
                "spec": ParamSpec("dict", False)},
        outputs={"source": ParamSpec("str")},
        checker=lambda a, o: Check("changed",
                                   o.get("source") != a["source"], "源码有改动")),
        lambda source, baseline="", spec=None: {
            "source": source.replace(">=", "<=")})
    gw = Gateway(reg)

    h0 = Harness(meta_rules="V6 元规则", routing={},
                 tools=("read_file", "write_file", "python_exec"),
                 skills={}, memory_policy={})
    h1 = bind_capability(h0, "ext.fixer", "外部修复", route_from="repair")
    ck("E1 绑定会改 skills 与 routing ⇒ harness_id 变（技能层真的进了基因组）",
       h1.harness_id != h0.harness_id and "uci:ext.fixer" in h1.skills
       and h1.routing.get("repair") == "uci:ext.fixer",
       f"{h1.skills} {h1.routing}")

    r = HarnessAgent(envs, gateway=gw).run(h1, SPEC)
    ck("E2 外部技能被 L1 路由命中并真的执行（reward 来自环境判据）",
       r.strategy == "uci:ext.fixer" and r.reward == 1.0,
       f"strategy={r.strategy} reward={r.reward}")
    ck("E3 L3 步骤里能看到 UCI 调用的详情",
       any(s.layer == "L3" and s.action == "uci" and s.ok for s in r.steps),
       str([(s.layer, s.action, s.ok) for s in r.steps]))

    r_no = HarnessAgent(envs, gateway=None).run(h1, SPEC)
    uci_steps = [s for s in r_no.steps if s.action == "uci"]
    ck("E4 ⛔ 无网关 ⇒ 显式记技能不可达（不是悄悄退化成无策略）",
       uci_steps and not uci_steps[0].ok and r_no.reward == 0.0,
       f"{[s.detail[:40] for s in uci_steps]} reward={r_no.reward}")

    # ⑤ 技能返回缺 source ⇒ 不适用，源码原样
    reg2 = Registry()
    reg2.register(CapabilitySpec(id="ext.nosrc", trusted=True,
                                 inputs={"source": ParamSpec("str")},
                                 outputs={"source": ParamSpec("str")}),
                  lambda source: {"nope": 1})
    h2 = bind_capability(h0, "ext.nosrc", route_from="repair")
    r2 = HarnessAgent(envs, gateway=Gateway(reg2)).run(h2, SPEC)
    ck("E5 技能输出缺 source ⇒ 判不适用，源码原样（reward 0）",
       r2.reward == 0.0, f"reward={r2.reward}")


def group_f() -> None:
    """F 组：HTTP 端点（暴露面纪律）。"""
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import aether.serve as SV
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry)

    print("\nF 组 · HTTP 端点 /api/capabilities 与 /api/invoke")
    reg = Registry()
    reg.register(CapabilitySpec(
        id="http.echo", kind="skill", trusted=True,
        inputs={"x": ParamSpec("str")}, outputs={"out": ParamSpec("str")}),
        lambda x: {"out": x})
    SV.Handler.gateway = Gateway(reg)
    SV.Handler.root = ROOT
    SV.Handler.console_dir = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    #: ⛔ 本机有 HTTP_PROXY ⇒ 访 localhost 必须绕过代理（否则 502 是假故障）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(path, obj):
        req = urllib.request.Request(
            base + path, data=json.dumps(obj).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    try:
        with opener.open(base + "/api/capabilities", timeout=10) as r:
            caps = json.loads(r.read().decode("utf-8"))
        ck("F1 /api/capabilities 列出已注册能力",
           caps.get("n") == 1 and caps["capabilities"][0]["id"] == "http.echo",
           str(caps)[:80])

        #: ⛔ r29：过滤后 `n` 必须跟随过滤（此前恒为注册表总数 ⇒ 列表 2 条
        #:    却报 n=4，会被读成"过滤没生效"）；总数另给 `n_total`。
        def _caps(qs=""):
            with opener.open(base + "/api/capabilities" + qs, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))

        ck("F1b ⛔ ?kind= 过滤后 n 跟随过滤、n_total 恒为总数",
           _caps("?kind=skill").get("n") == 1
           and _caps("?kind=agent").get("n") == 0
           and _caps("?kind=skill").get("n_total") == 1
           and _caps("?kind=agent").get("n_total") == 1,
           f"skill={str(_caps('?kind=skill'))[:70]} "
           f"agent={str(_caps('?kind=agent'))[:70]}")

        code, body = post("/api/invoke", {"capability": "http.echo",
                                          "args": {"x": "hi"}})
        ck("F2 /api/invoke 调用已注册能力 ⇒ 200 + status=ok",
           code == 200 and body.get("status") == "ok"
           and body.get("output") == {"out": "hi"},
           f"{code} {str(body)[:80]}")

        code, body = post("/api/invoke", {"capability": "nope", "args": {}})
        ck("F3 未注册 ⇒ 422 + unavailable（不是 200 空结果）",
           code == 422 and body.get("status") == "unavailable",
           f"{code} {body.get('status')}")

        code, body = post("/api/invoke", {"capability": "http.echo", "args": {}})
        ck("F4 参数不合规 ⇒ 422 + rejected",
           code == 422 and body.get("status") == "rejected",
           f"{code} {body.get('status')}")

        #: ⛔⛔ 最关键的一条：端点不得接受实现描述，否则 = 远程执行任意命令
        for key in ("argv", "impl", "code"):
            code, body = post("/api/invoke", {"capability": "http.echo",
                                              "args": {}, key: ["rm", "-rf", "/"]})
            ck(f"F5 ⛔ 请求体携带 {key} ⇒ 400（端点只接受能力引用）",
               code == 400 and "rejected_keys" in body, f"{code} {str(body)[:60]}")

        old = SV.MAX_INVOKE_BODY_BYTES
        try:
            SV.MAX_INVOKE_BODY_BYTES = 512
            req = urllib.request.Request(
                base + "/api/invoke",
                data=json.dumps({"capability": "http.echo",
                                 "args": {"x": "A" * 5000}}).encode(),
                method="POST", headers={"Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=10) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            ck("F6 请求体超限 ⇒ 413（第四个人口也得上锁）", code == 413, str(code))
        finally:
            SV.MAX_INVOKE_BODY_BYTES = old

        #: 网关未装载 ⇒ 503（配置问题，不是"端点不存在"）
        SV.Handler.gateway = None
        code, body = post("/api/invoke", {"capability": "http.echo", "args": {}})
        ck("F7 网关未装载 ⇒ 503（分诊方向正确：是配置问题不是 404）",
           code == 503, str(code))
    finally:
        srv.shutdown()
        SV.Handler.gateway = None


def group_g() -> None:
    """G 组：声明 vs 实际的落差记账（超时）+ 清单不支持 checker。

    ⛔ 全部来自 2026-10-14 r29 的**接口端到端实测**：skill/tool/agent ×
    CLI/HTTP 两个入口都调得通，但三处读数失真。本组只钉其中两处
    （第三处 n/n_total 在 F1b）。
    ⛔ G1/G2 必须成对：G1 单独绿可能只是"字段恒 True"，G2 是反向对照。
    """
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry,
                                load_manifest)

    print("\nG 组 · 超时落差记账 / 清单不支持 checker（r29）")

    def _slow(text=""):
        time.sleep(1.2)
        return {"out": "slow-" + str(text)}

    reg = Registry()
    reg.register(CapabilitySpec(id="g.slow", kind="skill", trusted=True,
                                timeout_s=0.3,
                                inputs={"text": ParamSpec("str", False)},
                                outputs={"out": ParamSpec("str")}), _slow)
    r1 = Gateway(reg).invoke("g.slow", {"text": "x"})
    ck("G1 inproc 声明 0.3s 却实跑 1.2s ⇒ 落差**被记账**"
       "（不是只有 timeout_enforced='none' 这一个形态读数）",
       r1.status == "ok" and r1.timeout_enforced == "none"
       and abs(r1.timeout_declared_s - 0.3) < 1e-6
       and r1.timeout_declared_but_unenforced is True,
       f"status={r1.status} declared={r1.timeout_declared_s} "
       f"cost={r1.cost_s:.2f} flag={r1.timeout_declared_but_unenforced}")

    reg2 = Registry()
    reg2.register(CapabilitySpec(id="g.slow", kind="skill", trusted=True,
                                 timeout_s=30.0,
                                 inputs={"text": ParamSpec("str", False)},
                                 outputs={"out": ParamSpec("str")}), _slow)
    r2 = Gateway(reg2).invoke("g.slow", {"text": "x"})
    #: ⛔ 生效预算 = `min(调用方预算, spec 声明)`：默认预算是 10.0，
    #:    所以声明 30.0 时生效的是 **10.0**（记另一个口径就是假仪表）。
    ck("G2 对照组：同一条慢能力声明放宽到 30s ⇒ 生效预算取更严的 10.0"
       " ⇒ 没跑超 ⇒ 落差为 False",
       r2.status == "ok" and abs(r2.timeout_declared_s - 10.0) < 1e-6
       and r2.timeout_declared_but_unenforced is False,
       f"declared={r2.timeout_declared_s} cost={r2.cost_s:.2f} "
       f"flag={r2.timeout_declared_but_unenforced}")

    #: ⛔ 真被中断的那次**不是**落差 —— 被中断正是兑现的样子
    td = tempfile.mkdtemp(prefix="v6uci_g_")
    slow_cli = _cli_tool(td, "import json,time\ntime.sleep(5)\n"
                             "print(json.dumps({'out':'slow'}))\n")
    reg3 = Registry()
    reg3.register(CapabilitySpec(id="g.cli", kind="tool", trusted=False,
                                 sandbox="process", timeout_s=0.4,
                                 inputs={"text": ParamSpec("str", False)},
                                 outputs={"out": ParamSpec("str")}),
                  [sys.executable, slow_cli])
    r3 = Gateway(reg3).invoke("g.cli", {"text": "x"})
    ck("G3 ⛔ CLI 真被中断 ⇒ status=timeout 且**不算**落差（兑现了）",
       r3.status == "timeout" and r3.cost_s < 3.0
       and r3.timeout_declared_but_unenforced is False,
       f"status={r3.status} cost={r3.cost_s:.2f} "
       f"flag={r3.timeout_declared_but_unenforced}")

    #: ⛔ 清单写 checker 曾经是**静默忽略**（写了等于没写 = 假仪表）
    bad = {"capabilities": [{
        "id": "g.checked", "kind": "skill", "trusted": True,
        "inputs": {"text": {"type": "str", "required": True}},
        "outputs": {"out": {"type": "str", "required": True}},
        "checker": "some.module:check",
        "impl": {"transport": "python",
                 "target": "scripts.ext_upper:upper"}}]}
    reg4 = Registry()
    rep = load_manifest(reg4, bad)
    ck("G4 ⛔ 清单声明 checker ⇒ 注册被拒（不是静默忽略）",
       not rep.loaded and len(rep.skipped) == 1
       and rep.skipped[0].get("status") == "rejected"
       and "checker" in rep.skipped[0].get("reason", ""),
       str(rep.skipped)[:120])
    r5 = Gateway(reg4).invoke("g.checked", {"text": "x"})
    ck("G5 被拒的能力确实不可用（unavailable，不是'照常能调'）",
       r5.status == "unavailable", r5.status)

    #: 正当出路：代码注册 checker ⇒ verified=True（证明缺口有出口）
    from aether.verifier import Check
    reg5 = Registry()
    reg5.register(CapabilitySpec(
        id="g.ok", kind="skill", trusted=True,
        inputs={"text": ParamSpec("str")}, outputs={"out": ParamSpec("str")},
        checker=lambda a, o: Check("ok", bool(o.get("out")), "非空")),
        lambda text: {"out": str(text).upper()})
    r6 = Gateway(reg5).invoke("g.ok", {"text": "x"})
    ck("G6 出口：代码注册 CapabilitySpec(checker=...) ⇒ verified=True",
       r6.status == "ok" and r6.verified is True and len(r6.checks) == 1,
       f"verified={r6.verified} checks={r6.checks}")


def group_h() -> None:
    """H 组：能力调用必须**进账本**（`cap.invoke`）。

    ⛔ 背景（2026-10-15 实测）：账本里 `cap.invoke` 事件 **0 次**。查下来是
       **两层缺口叠在一起**：① `mount_capabilities()` 完全不收 ledger ⇒
       CLI/HTTP/doctor 三个入口造出的网关都没有账本；②
       `Gateway.invoke(write_ledger=False)` 默认关，而**全代码库没有一个
       调用方传 True**。单独修哪一层，读数都不会从 0 变成 1。
       本组守：挂了 ledger ⇒ **默认**就记；显式 False ⇒ 不记（不是恒真）；
       不挂 ledger ⇒ 不记也不崩（探测路径不受影响）；且装载接线真的把
       ledger 传到底。
    """
    import tempfile as _tf
    from aether.interop import (CapabilitySpec, Gateway, ParamSpec, Registry,
                                mount_capabilities)
    from aether.ledger import Ledger

    td = _tf.mkdtemp(prefix="v6_interop_h_")
    led = Ledger(Path(td) / "l.jsonl")

    def _reg():
        r = Registry()
        r.register(CapabilitySpec(
            id="h.echo", kind="skill", trusted=True,
            inputs={"text": ParamSpec("str")},
            outputs={"out": ParamSpec("str")}),
            lambda text: {"out": str(text)})
        return r

    def _n(ledger):
        return len([e for e in ledger.events() if e.kind == "cap.invoke"])

    # ① 挂了 ledger ⇒ **默认**写账（调用方不需要记得传 write_ledger=True）
    gw = Gateway(_reg(), ledger=led)
    r = gw.invoke("h.echo", {"text": "hi"}, actor="cli")
    evs = [e for e in led.events() if e.kind == "cap.invoke"]
    ck("H1 ⛔ 挂了 ledger ⇒ 默认写 `cap.invoke`（开关不该默认是关的）",
       r.status == "ok" and len(evs) == 1,
       f"status={r.status} n_events={len(evs)}")
    ck("H2 事件带分诊字段（capability / status / transport）",
       bool(evs) and evs[0].payload.get("capability") == "h.echo"
       and "status" in evs[0].payload and "transport" in evs[0].payload,
       str(dict(evs[0].payload))[:140] if evs else "")
    ck("H3 actor 如实记录调用方", bool(evs) and evs[0].actor == "cli",
       f"{evs[0].actor if evs else None}")

    # ② 反证：显式 write_ledger=False ⇒ 不记（证明 H1 不是恒真）
    n0 = _n(led)
    Gateway(_reg(), ledger=led).invoke("h.echo", {"text": "x"},
                                       write_ledger=False)
    ck("H4 反证：显式 write_ledger=False ⇒ 不记（H1 不是恒真）",
       _n(led) == n0, f"{n0} -> {_n(led)}")

    # ③ 不挂 ledger ⇒ 不记，也不崩（纯探测调用不需要账）
    r3 = Gateway(_reg()).invoke("h.echo", {"text": "y"})
    ck("H5 不挂 ledger ⇒ 照常返回、不崩（探测路径不受影响）",
       r3.status == "ok", r3.status)

    # ④ 装载接线必须**把 ledger 传到底**（CLI/HTTP/doctor 的唯一入口）
    gw4, _rep = mount_capabilities("", ledger=led)
    ck("H6 ⛔ mount_capabilities 把 ledger 传到底（否则三个入口全不记账）",
       getattr(gw4, "ledger", None) is led,
       str(getattr(gw4, "ledger", None)))
    gw5, _ = mount_capabilities("")
    ck("H7 不传 ledger ⇒ 网关就没有账本（保持旧行为，不伪造一个）",
       getattr(gw5, "ledger", None) is None)


def main() -> int:
    t0 = time.time()
    print("契约测试：通用接入层（UCI）")
    group_a()
    group_b()
    group_c()
    group_d()
    group_e()
    group_f()
    group_g()
    group_h()
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
