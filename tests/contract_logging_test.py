#!/usr/bin/env python3
"""契约 · 统一日志（r35）。

收口前：`aether/` 里 `import logging` **零次**，诊断全靠
`print(..., file=sys.stderr)`（46 处）。本契约守四件事：

    A 组  `setup()` 的形态：幂等 / 级别校验 / json 开关 / **库层不装 handler**
    B 组  ⛔ **stdout 不被日志污染**（端到端跑真 CLI，断言 stdout 是纯 JSON）
    C 组  JSON 行可解析且带齐 ts/level/logger/msg
    D 组  `--log-level` 真能过滤（并对拍：降低级别后同一条诊断必须重新出现）
    E 组  ⛔ 反证：短路 formatter ⇒ 输出必须变（证明断言不是恒真）

⛔ 判据纪律：**端到端用 subprocess 跑真 CLI**，不在进程内 mock 日志 ——
   在进程内断言"日志长什么样"会连"根本没接线"一起放过。
"""
from __future__ import annotations

import io
import json
import logging
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aether import logging_setup as LS                            # noqa: E402

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

FAILED: list = []
PY = sys.executable


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def run_cli(*a: str, root: str):
    p = subprocess.run([PY, "-m", "aether.cli", "--root", root, *a],
                       capture_output=True, text=True, cwd=str(ROOT),
                       timeout=90)
    return p.returncode, p.stdout, p.stderr


def parse_json_stream(s: str) -> list:
    """把 stdout 当**JSON 流**解析（status 会打多个 JSON 块）。
    任一段解析失败 ⇒ 说明有非 JSON 文本（多半是日志）混进了产物。"""
    dec = json.JSONDecoder()
    i, out = 0, []
    s = s.strip()
    while i < len(s):
        while i < len(s) and s[i] in " \n\t\r":
            i += 1
        if i >= len(s):
            break
        obj, j = dec.raw_decode(s, i)
        out.append(obj)
        i = j
    return out


def make_root() -> str:
    d = pathlib.Path(tempfile.mkdtemp(prefix="v6_log_"))
    (d / "data").mkdir(exist_ok=True)
    from aether.ledger import Ledger
    led = Ledger(str(d / "data" / "ledger.jsonl"))
    for i in range(3):
        led.append("t.e", {"i": i}, actor="t")
    return str(d)


print("=" * 62)
print("契约 · 统一日志")
print("=" * 62)

# ── A 组：setup 形态 ────────────────────────────────────────────
print("\n[A] setup 的形态")
ck("A1 装完 handler ⇒ configured=True 且级别生效",
   LS.setup(level="WARNING")["configured"] is True
   and logging.getLogger().level == logging.WARNING,
   str(LS.current()))
n1 = len(logging.getLogger().handlers)
LS.setup(level="ERROR", json_output=True)
n2 = len(logging.getLogger().handlers)
ck("A2 ⛔ 幂等：重复 setup 不叠加 handler（否则每条日志打两遍）",
   n1 == n2, f"{n1} → {n2}")
try:
    LS.setup(level="VERBOSE")
    ck("A3 未知级别 ⇒ 抛 ValueError（不是静默降级到 INFO）", False, "没抛")
except ValueError as e:
    ck("A3 未知级别 ⇒ 抛 ValueError（不是静默降级到 INFO）",
       "VERBOSE" in str(e), str(e)[:60])
ck("A4 只收 *_aether_owned 的 handler（不误删别人装的）",
   all(getattr(h, "_aether_owned", False)
       for h in logging.getLogger().handlers)
   or not logging.getLogger().handlers)

# 库层不装 handler：新开一个 logger，父级是 root（有 handler）——
# 真正的断言是 **import 不产生输出**，见 B 组。
ck("A5 `get_logger` 不装 handler（返回普通 Logger，不带 handler）",
   not LS.get_logger("aether.demo.logger").handlers)

# ── B 组：stdout 不被污染（端到端）─────────────────────────────
print("\n[B] ⛔ stdout 不被日志污染（端到端跑真 CLI）")
root = make_root()
rc, out, err = run_cli("status", root=root)
#: ⛔ 判据要拿准：`status` 的 stdout **本来就**混了人类可读摘要
#:    （"账本链：✅ 完整"），那是既有设计、是**产物**，不是日志污染。
#:    真正的判据是：stdout 里**不能出现日志行**（带 ts + 级别 + │ 分隔符，
#:    或 JSON 日志的 "level" 字段）。
LOG_LINE = re.compile(r"\d{2}:\d{2}:\d{2}\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b"
                      r"|\"level\"\s*:")
hits = [l for l in out.splitlines() if LOG_LINE.search(l)]
ck("B1 ⛔ stdout 里一条**日志行**都没有（人类可读摘要不算，那是产物）",
   not hits, f"{hits[:1]}")
ck("B1b 对照：同一条命令的诊断确实在 stderr 里（否则 B1 是空集恒真）",
   bool((err or "").strip()) or "账本链" in out, f"stderr={len(err or '')}B")
ck("B2 rc=0（体检类命令成功）", rc == 0, f"rc={rc}")

#: 造一条**必然**产生诊断的场景：清单文件不存在 ⇒ warning 走 stderr
#: ⛔ `file` 是**位置参数**（不是 --file）—— 用错会撞到 argparse usage，
#:    于是"诊断没出现"看着像过滤生效，实际是命令根本没跑（假绿）。
NOPE = str(pathlib.Path(root) / "nope.json")
rc2, out2, err2 = run_cli("memory", "ingest", NOPE, root=root)
ck("B3 诊断真的产生了（否则 B4 是空集恒真）",
   "找不到清单" in (err2 or ""), (err2 or "")[:70].replace("\n", " "))
ck("B4 ⛔ 诊断只出现在 stderr，**stdout 一行都没有**",
   "找不到清单" not in out2 and out2.strip() == "",
   f"stdout={out2[:60]!r}")

# ── C 组：JSON 格式 ─────────────────────────────────────────────
print("\n[C] --log-json：每行一个 JSON 对象")
rc3, out3, err3 = run_cli("--log-json", "memory", "ingest", NOPE, root=root)
lines = [l for l in (err3 or "").splitlines() if l.strip().startswith("{")]
ok_json, parsed = bool(lines), []
for l in lines[:3]:
    try:
        d = json.loads(l)
        parsed.append(d)
        ok_json = ok_json and {"ts", "level", "logger", "msg"} <= set(d)
    except Exception:
        ok_json = False
ck("C1 每行都是合法 JSON 且带齐 ts/level/logger/msg",
   ok_json and bool(parsed), str(parsed[:1])[:110])
ck("C2 logger 名带模块路径（多进程下能分清是谁打的）",
   bool(parsed) and "cli" in parsed[0].get("logger", ""),
   str(parsed[0].get("logger") if parsed else None))
ck("C3 JSON 模式下 msg 内容仍然可读（没把正文丢了）",
   any("找不到清单" in d.get("msg", "") for d in parsed),
   str([d.get("msg") for d in parsed])[:80])

# ── D 组：级别过滤（带反证）────────────────────────────────────
print("\n[D] --log-level 真能过滤（同一条诊断，级别一降必须重新出现）")
rc4, _o4, err4 = run_cli("--log-level", "ERROR", "memory", "ingest", NOPE,
                         root=root)
ck("D1 ERROR 级别 ⇒ warning 级诊断被过滤掉",
   "找不到清单" not in (err4 or ""), (err4 or "")[:60].replace("\n", " "))
rc5, _o5, err5 = run_cli("--log-level", "INFO", "memory", "ingest", NOPE,
                         root=root)
ck("D2 ⛔ 反证：降到 INFO ⇒ 同一条诊断重新出现"
   "（证明 D1 是**过滤**生效，不是这条日志根本没打）",
   "找不到清单" in (err5 or ""), (err5 or "")[:60].replace("\n", " "))
rc6, _o6, err6 = run_cli("--log-level", "BOGUS", "status", root=root)
ck("D3 非法级别 ⇒ rc=2 并说明可选值（不是抛栈）",
   rc6 == 2 and "BOGUS" in (err6 or ""), f"rc={rc6} {(err6 or '')[:60]}")

# ── E 组：反证（断言不是恒真）──────────────────────────────────
print("\n[E] ⛔ 反证：短路 formatter ⇒ 输出必须变")
buf = io.StringIO()
LS.setup(level="INFO", json_output=False, stream=buf)
lg = LS.get_logger("probe.e")
lg.info("原始消息-应当被替换")
plain = buf.getvalue()
ck("E1 基线：普通模式下确实打出了这条消息",
   "原始消息-应当被替换" in plain, plain.strip()[:60])


class _Hijack(logging.Formatter):
    def format(self, record):
        return "HIJACKED-BY-PROBE"


for h in logging.getLogger().handlers:
    if getattr(h, "_aether_owned", False):
        h.setFormatter(_Hijack())
buf2 = io.StringIO()
for h in logging.getLogger().handlers:
    if getattr(h, "_aether_owned", False):
        h.stream = buf2
lg.info("原始消息-应当被替换")
ck("E2 ⛔ 换掉 formatter ⇒ 输出变成探针字样"
   "（证明 E1 命中的是**我们的 handler**，不是别的）",
   "HIJACKED-BY-PROBE" in buf2.getvalue()
   and "原始消息-应当被替换" not in buf2.getvalue(),
   buf2.getvalue().strip()[:60])
LS.setup(level="INFO", json_output=False, stream=sys.stderr)   # 还原

print("\n" + "=" * 62)
if FAILED:
    print(f"❌ {len(FAILED)} 项未通过：" + "; ".join(FAILED))
    sys.exit(1)
print("✅ 全部通过")
