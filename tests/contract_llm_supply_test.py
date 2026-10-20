#!/usr/bin/env python3
"""契约 · **LLM 供给端的读数**（r62）—— 用了哪个模型必须能从账本查出来。

═══ 为什么需要这份契约 ═══

全量账本（71 次 `evolution.run_config`）里只有 `proposer="template"` 一个
字符串。于是 71 次跑完之后，**「用的是哪个模型」在账本里根本不存在** ——
更糟的是它长得像"provider=None×71"：读取侧写 `d.get("provider")` 拿到
`None`，而 `None` 在日志里既可能是"值是 None"也可能是"没有这个键"。
实测：`"provider"` 这个键命中 **0 次**（第 24 条：某维度恒为 0 先怀疑工具）。

⇒ 本契约守的不是"能跑"，而是**事后能分辨四种状态**：

    ┌─────────────────┬──────────┬───────────────────────────────┐
    │ 状态            │ kind     │ 运行后 llm_result.error       │
    ├─────────────────┼──────────┼───────────────────────────────┤
    │ 本来就用模板    │ template │ ""                            │
    │ 真模型跑通了    │ llm      │ ""（且 raw_len > 0）          │
    │ 要了模型但连不上│ llm      │ "URLError: ..."               │
    └─────────────────┴──────────┴───────────────────────────────┘

⛔ 为什么必须是**两条**事件：`run_config` 写在跑之前，只能记意图 ——
端点连不上时它照样写 `kind="llm"` 且 `fallback=""`（构造 `OllamaProvider`
不校验可达性，第一次真发请求才失败）⇒ 只靠 run_config，
「接了模型、跑了、失败了」与「接了模型、跑了、成功了」**逐字相同**。
本契约的 C 组就是靠这条事件才第一次能分辨这两件事。

⛔ 假端点而不是真 Ollama：真 Ollama 的可用性取决于本机装没装（r60 G 的
   教训：拿"本机没装某工具"当夹具，装了就全红）。这里起一个**进程内**
   假端点返回固定 capsule ⇒ 断言与机器状态无关，且能证明"候选确实来自
   这条链路"（端点返回的胶囊名会出现在 `experience.propose` 里）。
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

SUPPORTED_MODES = ("default",)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED: list = []
PY = sys.executable
KIND = "evolution.run_config"

#: 假端点返回的胶囊名 —— 它只会来自这个假端点（模板提议器的候选名是
#: `fix_control_flow` 一类的固定清单），因此"账本里出现它"就证明候选
#: 确实走了 LLM 链路，而不是回退到了模板还报告说 llm。
FAKE_CAPSULE = "probe_from_fake_endpoint"
FAKE_BODY = json.dumps({
    "capsules": [{
        "name": FAKE_CAPSULE,
        "rationale": "契约用假端点返回的固定胶囊",
        "genes": [{"op": "set", "target": "L3.skills.probe_from_fake_endpoint",
                   "value": "契约占位技能体",
                   "rationale": "只为证明链路被走到"}],
    }]
}, ensure_ascii=False)


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


class _Handler(http.server.BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        type(self).calls += 1
        body = json.dumps({"message": {"role": "assistant", "content": FAKE_BODY},
                           "done": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静默
        pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_fake() -> tuple:
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}"


def evolve(root: str, *extra: str, env: dict = None, cwd: str = None,
           fast: bool = False):
    e = dict(os.environ)
    #: ⛔ 清掉可能影响判定的外部赋值（否则跑的人环境不同 ⇒ 结果不同）
    for k in ("V6_OLLAMA_BASE_URL", "V6_OLLAMA_MODEL", "V6_LLM_BASE_URL"):
        e.pop(k, None)
    e.update(env or {})
    #: ⛔ `fast=True`：A 组只需要"账本里出现 run_config.llm"这一个事实，
    #:    不需要真挖出弱点/候选 ⇒ 用最小世界（1 题 1 候选）。B/C 组要
    #:    真调到 propose，必须给足题量否则 proposal 根本不会被调用
    #:    （r62 实测：`--max-tasks 2` 时 B5/B6/B8/B9 全红）。
    #:    ⛔ 这样拆是为了把这份契约压在 `aether check` 的单份预算内 ——
    #:    超时一次就等于这份契约在验收里"不存在"。
    sizing = (["--group", "1", "--max-tasks", "1"] if fast
              else ["--group", "2", "--max-tasks", "4"])
    p = subprocess.run([PY, "-m", "aether.cli", "--root", root,
                        "evolve", "--rounds", "1", *sizing, *extra],
                       capture_output=True, text=True, cwd=cwd or str(ROOT),
                       timeout=300, env=e)
    return p.returncode, p.stdout, p.stderr


def parse_out(out: str):
    try:
        return json.loads(out[out.find("{"):])
    except Exception:  # noqa: BLE001
        return {}


def ledger_payload(root: str, kind: str):
    sys.path.insert(0, str(ROOT))
    from aether.ledger import Ledger
    led = Ledger(str(pathlib.Path(root) / "data" / "ledger.jsonl"))
    return [getattr(e, "payload", {}) or {} for e in led.events()
            if getattr(e, "kind", "") == kind]


def fresh_ws(prefix: str) -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copytree(ROOT / "tasks", tmp / "tasks")
    return tmp


print("=" * 62)
print("契约 · LLM 供给端读数（用了哪个模型必须可查）")
print("=" * 62)

# ── A 组：默认 template 也要留痕 ───────────────────────────────
print("\n[A] 默认（不复用外部环境）跑一轮 ⇒ llm 字段必须存在且标 template")
ws_a = fresh_ws("v6_llmsup_a_")
rc, out, err = evolve(str(ws_a), fast=True)
ra = parse_out(out)
cfg_a = ra.get("run_config") or {}
llm_a = cfg_a.get("llm")
ck("A0 evolve 正常结束（否则后面都在空数据上比）", rc == 0,
   f"rc={rc} · {err[-200:]}" if rc else "rc=0")
ck("A1 ⛔ run_config 里有 llm 字段（此前完全没有 ⇒ 读不出用的什么模型）",
   isinstance(llm_a, dict), f"{type(llm_a).__name__}")
ck("A2 默认路径 kind=template", (llm_a or {}).get("kind") == "template",
   f"{llm_a!r}")
ck("A3 默认路径 model 为空（没接模型就别假装有）",
   (llm_a or {}).get("model") == "", f"{llm_a!r}")
ck("A4 默认路径 fallback 为空（空 = 没发生回退，不是「没检查」）",
   (llm_a or {}).get("fallback") == "", f"{llm_a!r}")
evs_a = ledger_payload(str(ws_a), KIND)
ck("A5 账本里同一份（不是只打 stdout）",
   bool(evs_a) and "llm" in (evs_a[-1] or {}), f"{len(evs_a)} 条")
ck("A6 账本与 stdout 同源（model 一致）",
   (evs_a[-1] or {}).get("llm", {}).get("kind") == (llm_a or {}).get("kind"),
   f"账本={(evs_a[-1] or {}).get('llm')!r}")

# ── B 组：真链路跑通 ⇒ kind=llm + 模型名 + 候选确实来自端点 ─────
print("\n[B] 假端点 + --proposer llm ⇒ 必须记成 llm，且候选确实来自该端点")
srv, base = start_fake()
llm_b: dict = {}
try:
    ws_b = fresh_ws("v6_llmsup_b_")
    before = _Handler.calls
    rc, out, err = evolve(str(ws_b), "--proposer", "llm",
                          "--llm-model", "contract-fake-model",
                          env={"V6_OLLAMA_BASE_URL": base})
    rb = parse_out(out)
    llm_b = (rb.get("run_config") or {}).get("llm") or {}
    ck("B0 evolve 正常结束", rc == 0, f"rc={rc} · {err[-200:]}" if rc else "rc=0")
    ck("B1 kind=llm（要了模型且连上了）", llm_b.get("kind") == "llm", f"{llm_b!r}")
    ck("B2 ⛔ 记的是**实际生效的模型名**，不是空串（空串会掩盖「其实跑了默认模型」）",
       llm_b.get("model") == "contract-fake-model", f"{llm_b!r}")
    ck("B3 provider 记成 ollama", llm_b.get("provider") == "ollama", f"{llm_b!r}")
    ck("B4 连上了 ⇒ fallback 必须为空", llm_b.get("fallback") == "", f"{llm_b!r}")
    ck("B5 ⛔ 端点真的被调到了（假端点计数增加）",
       _Handler.calls > before, f"calls {before}→{_Handler.calls}")
    props = ledger_payload(str(ws_b), "experience.propose")
    names = [n for p in props for n in (p.get("proposed") or [])]
    ck(f"B6 ⛔ 候选名里出现假端点专属的 {FAKE_CAPSULE}（证明候选确实来自 LLM 链路，"
       f"不是回退模板后仍报 llm）", FAKE_CAPSULE in names,
       f"proposed={names[:6]}")
    res_b = ledger_payload(str(ws_b), "evolution.llm_result")
    rb1 = res_b[-1] if res_b else {}
    ck("B7 连通时 llm_result.error 为空（与 C 组构成对照 ⇒ error 有牙）",
       res_b and not rb1.get("error"), f"{rb1!r}")
    ck("B8 连通时 raw_len > 0（真拿到了模型输出）",
       int(rb1.get("raw_len") or 0) > 0, f"raw_len={rb1.get('raw_len')!r}")
    ck("B9 连通时 propose_calls >= 1（端点被调用过）",
       int(rb1.get("propose_calls") or 0) >= 1,
       f"propose_calls={rb1.get('propose_calls')!r}")
finally:
    srv.shutdown()

# ── C 组：要了模型但连不上 ⇒ 不许静默回退 ───────────────────────
print("\n[C] 端点不可达 + --proposer llm ⇒ fallback 必须写明原因（不许静默）")
ws_c = fresh_ws("v6_llmsup_c_")
dead = f"http://127.0.0.1:{_free_port()}"      # 没人监听
rc, out, err = evolve(str(ws_c), "--proposer", "llm",
                      "--llm-model", "contract-dead-model",
                      env={"V6_OLLAMA_BASE_URL": dead})
rcp = parse_out(out)
llm_c = (rcp.get("run_config") or {}).get("llm") or {}
res_c = ledger_payload(str(ws_c), "evolution.llm_result")
ck("C0 端点不可达时 evolve 仍然跑完（回退而不是崩）", rc == 0,
   f"rc={rc} · {err[-200:]}" if rc else "rc=0")
ck("C1 ⛔ 有运行后的 `evolution.llm_result` 事件（run_config 在跑之前写，"
   "只能记意图 ⇒ 没有这条，「跑了但失败」不可见）",
   len(res_c) == 1, f"{len(res_c)} 条")
r1 = res_c[-1] if res_c else {}
ck("C2 ⛔ error 非空（这是「链路走到了但失败了」唯一的账本痕迹）",
   bool(str(r1.get("error") or "")), f"{r1!r}")
ck("C3 error 带异常类型（不是一句「失败」—— 否则无法归因）",
   any(t in str(r1.get("error")) for t in
       ("Error", "Exception", "ValueError", "URLError", "超时")),
   f"{r1.get('error')!r}")
ck("C4 run_config 里 kind 仍记 llm（要的是什么，与成没成是两件事）",
   llm_c.get("kind") == "llm", f"{llm_c!r}")
ck("C5 跑失败 ⇒ raw_len 为 0（没拿到任何模型输出）",
   int(r1.get("raw_len") or 0) == 0, f"{r1.get('raw_len')!r}")

# ── D 组：反证 —— A2 不是恒真 ─────────────────────────────────
print("\n[D] ⛔ 反证：kind 必须随输入变（不是写死的常量）")
#: ⛔ 反证用**跨组对照**而不是复制整个仓库再改源码跑一遍
#:    （r62 实测：copytree + 第 4 次 evolve 在 `-j 4` 并发下把这份契约
#:    顶到 600s 上限 ⇒ 超时。`aether check` 有硬预算，反证也必须便宜）。
#:    论证强度不变：同一份代码在两组不同输入下产出**两种** kind
#:    ⇒ kind 不是常量。
ck("D1 ⛔ 同一份代码在两组输入下产出两种 kind ⇒ A2 不是写死的常量",
   (llm_a or {}).get("kind") == "template" and llm_b.get("kind") == "llm",
   f"A组={((llm_a or {}).get('kind'))!r} B组={llm_b.get('kind')!r}")
#: ⛔ 源码级守卫：初值必须仍是 template。防"有人把默认改成 llm"
#:    而 A2 因为别的原因照样绿（那时 D1 会红，但 A2 看起来还是对的）。
_cli = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
ck("D2 初值仍是 template（默认不许悄悄变成 llm）",
   '_llm_desc: Dict[str, Any] = {"kind": "template"' in _cli, "")
ck("D3 run_config 里的 llm 来自 `_llm_desc`，不是字面量",
   '"llm": dict(_llm_desc)' in _cli, "")

print("\n" + "=" * 62)
if FAILED:
    print(f"❌ {len(FAILED)} 项未过：")
    for n in FAILED:
        print(f"   · {n}")
    sys.exit(1)
print("✅ 全过")
