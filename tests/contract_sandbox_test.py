#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：沙箱必须是**实测过的隔离**，不是"用了 subprocess 就叫沙箱"。

## 为什么要有这一组

2026-10-13 实测（`scripts/probe_sandbox_audit.py`）原实现的 7 项里 5 项未拦住：

    · 宿主 **181 个环境变量全被继承**，含 PYAUTOL_VAULT_KEY / TAVILY_API_KEY
      / WORKBUDDY_PAC_RPC_TOKEN 等**真实密钥**；
    · 能读 /etc/hosts、能写 /tmp、能 fork 子进程；
    · 20 MB 输出被 capture_output **全量读进内存**。

而这里执行的是**合成器与模型产出的不可信代码**。只有 timeout 生效 ——
那是"超时兜底"，不是隔离。

## 这一组守的三件事

1. **密钥不许递到子进程手上**（白名单，不是黑名单 —— 黑名单拦不住
   没见过的名字，实测 `WORKBUDDY_PAC_RPC_TOKEN` 这种前缀根本猜不到）。
2. **声明的限制必须能被验证**：`rlimits`（意图）与 `rlimits_applied`
   （实测）分开显示 —— 实测 macOS 上 `RLIMIT_AS` 直接
   `ValueError: current limit exceeds maximum limit`，若只记意图就是假仪表。
3. **OS 层拿不到就降级，但降级不许静默**：`tier` / `fs_isolated` 必须
   如实反映（实测 macOS `sandbox-exec` 的 deny-default 一律
   `Operation not permitted` ⇒ 本环境只能用进程层）。

跑法（离线，秒级）：
    python tests/contract_sandbox_test.py
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether import environment as E  # noqa: E402
from aether.environment import (MAX_OUTPUT_BYTES, build_child_env,  # noqa: E402
                                run_in_sandbox, verify_source)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _payload(ex):
    """取探针源码自己打印的那一行（带 PROBE: 前缀）。"""
    for line in (ex.stdout or "").splitlines():
        s = line.strip()
        if s.startswith("PROBE:"):
            try:
                return json.loads(s[6:])
            except Exception:  # noqa: BLE001
                return {}
    return {}


# ══ A 组：环境变量净化（最严重的一条：真密钥会泄漏）══════════════
print("== A 组：宿主环境变量不许递给不可信代码 ==")

#: 造一个**可判定**的敏感变量（不碰真实密钥，只验证机制）
os.environ["V6_SANDBOX_CANARY_KEY"] = "canary-secret-value"
try:
    ex_env = run_in_sandbox(
        "import os, json\n"
        "keys = sorted(k for k in os.environ if 'KEY' in k.upper()"
        " or 'TOKEN' in k.upper() or 'SECRET' in k.upper())\n"
        "print('PROBE:' + json.dumps({'canary': os.environ.get("
        "'V6_SANDBOX_CANARY_KEY'), 'sensitive': keys, "
        "'n_env': len(os.environ)}))\n",
        [], timeout=5)
finally:
    os.environ.pop("V6_SANDBOX_CANARY_KEY", None)

d = _payload(ex_env)
ck("A1 端到端：子进程**读不到**注入的密钥（canary）",
   d.get("canary") is None, str(d))
ck("A2 端到端：子进程 env 里没有别的敏感项",
   d.get("sensitive") == [], str(d.get("sensitive")))
ck("A3 env 变量被压到最小（不是 181 个全继承）",
   0 < int(d.get("n_env", 999)) <= 12, str(d.get("n_env")))

env_map = build_child_env("/tmp/whatever")
ck("A4 build_child_env 用**白名单**：非白名单的宿主变量一律不在",
   "V6_SANDBOX_CANARY_KEY" not in env_map and set(env_map) <= {
       "PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR", "PYTHONHASHSEED",
       "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE"},
   str(sorted(env_map)))
ck("A5 ⛔ PYTHONPATH 被掐掉（路径注入的口子）",
   "PYTHONPATH" not in env_map, str(sorted(env_map)))
ck("A6 HOME/TMPDIR 指向工作目录（不是宿主的家目录）",
   env_map.get("HOME") == "/tmp/whatever"
   and env_map.get("TMPDIR") == "/tmp/whatever", str(env_map))

# ⛔ 反证：换成"继承宿主 env" ⇒ A1 必须翻转（否则 A1 是恒真的）
_orig_env = E.build_child_env
E.build_child_env = lambda wd: dict(os.environ)
try:
    os.environ["V6_SANDBOX_CANARY_KEY"] = "canary-secret-value"
    ex_leak = run_in_sandbox(
        "import os, json\n"
        "print('PROBE:' + json.dumps({'canary': os.environ.get("
        "'V6_SANDBOX_CANARY_KEY')}))\n", [], timeout=5)
finally:
    os.environ.pop("V6_SANDBOX_CANARY_KEY", None)
    E.build_child_env = _orig_env
ck("A7 ⛔ 反证：恢复成继承宿主 env ⇒ 密钥**泄漏**（A1 必须翻转）",
   _payload(ex_leak).get("canary") == "canary-secret-value",
   str(_payload(ex_leak)))

# ══ B 组：声明的限制必须能被验证（不许假仪表）══════════════════════
print()
print("== B 组：rlimit 的「意图」与「实测」必须分开显示 ==")
ex_rl = run_in_sandbox("print(1)\n", [], timeout=5)
sb = ex_rl.sandbox or {}
applied = sb.get("rlimits_applied")
ck("B1 有 `rlimits_applied`（实测），不是只有 `rlimits`（意图）",
   isinstance(applied, dict) and bool(applied), str(sb))
ck("B2 每一项实测值都必须是 True 或 `failed:` 开头（不许留空/硬编码）",
   all(v is True or (isinstance(v, str) and v.startswith(("failed:",
                                                          "unsupported")))
       for v in (applied or {}).values()),
   str(applied))
ck("B3 `rlimits`（意图）与 `rlimits_applied`（实测）是**两个**字段"
   " ⇒ 不一致时能看出来",
   "rlimits" in sb and "rlimits_applied" in sb
   and sorted(sb["rlimits"]) == sorted(
       [k for k in applied if not k.startswith("_")]),
   f"{sorted(sb.get('rlimits', {}))} vs {sorted(applied or {})}")
ck("B4 超时仍然生效（沙箱加固不得破坏原有行为）",
   run_in_sandbox("while True:\n    pass\n", [], timeout=2).rc == 124)

# ⛔ 反证：让 setrlimit 全部失败 ⇒ applied 不许出现任何 True
import resource as _res  # noqa: E402

_orig_setrlimit = _res.setrlimit


def _boom(*a, **k):
    raise OSError("injected")


_res.setrlimit = _boom
try:
    ex_bad = run_in_sandbox("print(1)\n", [], timeout=5)
finally:
    _res.setrlimit = _orig_setrlimit
bad_applied = (ex_bad.sandbox or {}).get("rlimits_applied") or {}
ck("B5 ⛔ 反证：setrlimit 全失败 ⇒ 实测里**没有** True"
   "（证明 applied 不是硬编码的绿灯）",
   bool(bad_applied) and not any(v is True for v in bad_applied.values()),
   str(bad_applied))
ck("B6 反证：即便限制全设不上，执行**仍然完成**（沙箱不得把系统打挂）",
   ex_bad.rc == 0, str(ex_bad.rc))

# ══ C 组：输出封顶（OOM 的口子）════════════════════════════════════
print()
print("== C 组：输出必须按字节封顶，且截断要可见 ==")
t0 = time.time()
ex_big = run_in_sandbox("print('x' * (20 * 1024 * 1024))\n", [], timeout=20)
ck("C1 请求 20 MB ⇒ 读回不超过 MAX_OUTPUT_BYTES（不再全量进内存）",
   len(ex_big.stdout) <= MAX_OUTPUT_BYTES + 1024,
   f"{len(ex_big.stdout)} > {MAX_OUTPUT_BYTES}")
ck("C2 截断被**标记**（truncated=True），不是静默截断",
   (ex_big.sandbox or {}).get("output_truncated") is True,
   str(ex_big.sandbox))
ck("C3 封顶后耗时可控（20 MB 不会拖死）", time.time() - t0 < 15,
   f"{time.time() - t0:.1f}s")

# ⛔ 截断导致 runner JSON 解析失败 ⇒ **不许**给 reward（fail-closed）
ho = E.Environment.from_dir(ROOT / "tasks" / "textscore", env_id="textscore")
v_big = verify_source(ho, source="print('x' * (20 * 1024 * 1024))\n",
                      timeout=20)
ck("C4 输出被截断 ⇒ 判定为**不通过**（不给 reward，不是静默放行）",
   v_big.passed is False and v_big.reward == 0.0, str(v_big.evidence)[:160])

# ⛔ 反证：把上限放到无穷 ⇒ 截断标记必须消失
_orig_cap = E.MAX_OUTPUT_BYTES
E.MAX_OUTPUT_BYTES = 10 ** 9
try:
    ex_nocap = run_in_sandbox("print('y' * (8 * 1024 * 1024))\n", [],
                              timeout=20)
finally:
    E.MAX_OUTPUT_BYTES = _orig_cap
ck("C5 ⛔ 反证：上限放到无穷 ⇒ truncated 变 False（C2 不是恒真）",
   (ex_nocap.sandbox or {}).get("output_truncated") is False,
   str(ex_nocap.sandbox))

# ══ D 组：OS 层降级不许静默 ════════════════════════════════════════
print()
print("== D 组：OS 层沙箱拿不到 ⇒ 降级，但必须看得见 ==")
ck("D1 结果里带 `tier`（要么是 process 要么是 seatbelt）",
   sb.get("tier") in ("process", "seatbelt"), str(sb.get("tier")))
# ⛔ 2026-10-13 加了 audit 层之后，`fs_isolated` 不再等价于"OS 层是否生效"：
#    audit 层**实测**能拦住越界读写（G2/G3），所以 OS 层拿不到时它仍是真的。
#    ⇒ 原来那条 `fs_isolated == (tier=="seatbelt")` 的断言已经**不能表达**
#      "不许谎报"这个不变量了（它会把真生效的 audit 层判成谎报）。
#    改成两条，一条管 OS 层字段、一条管**行为**（比字段相等更强）。
ck("D2 `os_isolated` 与 `tier` **一致**（OS 层不许谎报）",
   sb.get("os_isolated") is (sb.get("tier") == "seatbelt"),
   f"tier={sb.get('tier')} os_isolated={sb.get('os_isolated')}")
_esc = "/tmp/v6_d2_escape.txt"
if os.path.exists(_esc):
    os.remove(_esc)
_ex_esc = E.run_in_sandbox(
    f"def f(x):\n    open('{_esc}', 'w').write('pwned')\n    return x+1\n",
    [{"name": "t", "call": "f", "args": [1], "expect": 2}], timeout=10)
ck("D2b `fs_isolated=True` 必须是**实测**的（越界写真被拦），"
   "不是字段自述 —— 这条比原来的字段相等更强",
   (not sb.get("fs_isolated")) or (not os.path.exists(_esc)),
   f"fs_isolated={sb.get('fs_isolated')} 越界文件存在={os.path.exists(_esc)} "
   f"denied={_ex_esc.sandbox.get('denied')}")
ck("D3 `env_purged` 如实为 True（净化是进程层，总能生效）",
   sb.get("env_purged") is True, str(sb.get("env_purged")))
# ⛔ 断言不许写成"要么 None 要么 tuple" —— 那是恒真的（什么都能过）。
#    有意义的是：**返回非 None 时，那个命令必须真的能跑通**。
import subprocess as _sp  # noqa: E402
import tempfile as _tf  # noqa: E402

with _tf.TemporaryDirectory() as _td:
    _osb = E._os_sandbox_cmd(_td)
if _osb is None:
    ck("D4 本环境 OS 层不可用 ⇒ 如实返回 None（不伪造一个用不了的沙箱）",
       True)
else:
    _r = _sp.run(_osb[0] + [sys.executable, "-c", "print(1)"],
                 capture_output=True, text=True, timeout=15)
    ck("D4 返回非 None ⇒ 该命令**真的能跑通**（探测式接入，不是声明）",
       _r.returncode == 0 and "1" in (_r.stdout or ""),
       f"rc={_r.returncode} out={(_r.stdout or '')[:60]}")

# ⛔ 反证：伪造 OS 层可用 ⇒ tier 与 fs_isolated 必须跟着翻
_orig_osb = E._os_sandbox_cmd
E._os_sandbox_cmd = lambda wd: (["/bin/echo"], "fake-sandbox")
try:
    ex_fake = run_in_sandbox("print(1)\n", [], timeout=5)
finally:
    E._os_sandbox_cmd = _orig_osb
ck("D5 ⛔ 反证：伪造 OS 层 ⇒ tier/fs_isolated 翻转"
   "（证明字段随被测对象变，不是写死的）",
   (ex_fake.sandbox or {}).get("tier") == "seatbelt"
   and (ex_fake.sandbox or {}).get("fs_isolated") is True,
   str(ex_fake.sandbox))

# ══ E 组：判定要知道自己是在哪一层隔离下产出的 ═════════════════════
print()
print("== E 组：Verdict 的 evidence 必须带沙箱层级 ==")
v_ok = verify_source(ho, timeout=20)
ck("E1 正常判定通过（加固没把执行打坏）", v_ok.passed is True,
   str(v_ok.evidence)[:160])
ck("E2 evidence 带 `sandbox`（判定的可信度依赖它）",
   isinstance(v_ok.evidence.get("sandbox"), dict)
   and "tier" in v_ok.evidence["sandbox"],
   str(v_ok.evidence.get("sandbox")))
ck("E3 evidence 里的 sandbox **不带**逐条 rlimits（太吵，tier 才关键）",
   "rlimits" not in v_ok.evidence.get("sandbox", {}),
   str(v_ok.evidence.get("sandbox")))
ck("E4 加了 sandbox 之后 Verdict 仍通过 validate()"
   "（新字段不许撞上自述/打分黑名单）",
   v_ok.validate()[0] is True, str(v_ok.validate()))

# ══ F 组：fs 未隔离的告警只提示一次 ════════════════════════════════
print()
print("== F 组：环境级告警只打一次（不淹掉真告警）==")
import io  # noqa: E402
from contextlib import redirect_stderr  # noqa: E402

E._FS_WARNED = False
buf1, buf2 = io.StringIO(), io.StringIO()
with redirect_stderr(buf1):
    E._warn_fs_not_isolated_once()
with redirect_stderr(buf2):
    E._warn_fs_not_isolated_once()
ck("F1 第一次提示", "未隔离" in buf1.getvalue(), buf1.getvalue()[:80])
ck("F2 ⛔ 第二次不再提示（环境级属性，不是每次执行的事件）",
   buf2.getvalue() == "", buf2.getvalue()[:80])

# ══ G 组：audit 层（跨平台的文件系统闸门）══════════════════════════
#     ⛔ 为什么必须有这一层：OS 层（seatbelt/bwrap）在部分环境用不了
#        （实测 macOS 宿主里 deny-default 一律 Operation not permitted），
#        而 rlimit 层的内存限制在本机**三条全拒**（AS/DATA/RSS 都抛
#        ValueError）。这两层缺了之后，"隔离"只剩净化 env —— 不够。
print()
print("== G 组：audit hook 层（越界文件系统访问必须被拦）==")

_TESTS = [{"name": "t", "call": "f", "args": [1], "expect": 2}]
_OK_SRC = "def f(x):\n    return x+1\n"

ex0 = E.run_in_sandbox(_OK_SRC, _TESTS, timeout=10)
ck("G1 正常代码**零误伤**（沙箱不许把自己要跑的代码一起拒了）",
   ex0.rc == 0 and ex0.n_passed == 1
   and ex0.sandbox.get("denied_count", 0) == 0,
   f"rc={ex0.rc} passed={ex0.n_passed} "
   f"denied={ex0.sandbox.get('denied')}")

ex1 = E.run_in_sandbox(
    "def f(x):\n    d = open('/etc/hosts').read()\n    return x+1\n",
    _TESTS, timeout=10)
ck("G2 越界**读**被拦，且**记账**（不是悄悄失败）",
   ex1.sandbox.get("denied_count", 0) >= 1
   and any("hosts" in d for d in ex1.sandbox.get("denied") or []),
   f"denied={ex1.sandbox.get('denied')}")

_tmp_escape = "/tmp/v6_escape_contract.txt"
if os.path.exists(_tmp_escape):
    os.remove(_tmp_escape)
ex2 = E.run_in_sandbox(
    f"def f(x):\n    open('{_tmp_escape}', 'w').write('pwned')\n"
    f"    return x+1\n", _TESTS, timeout=10)
ck("G3 越界**写**被拦：文件**真的没被创建**（判据只看事实）",
   not os.path.exists(_tmp_escape)
   and ex2.sandbox.get("denied_count", 0) >= 1,
   f"exists={os.path.exists(_tmp_escape)} denied={ex2.sandbox.get('denied')}")

ex3 = E.run_in_sandbox(
    "import math, json, itertools, statistics\n"
    "def f(x):\n    return math.ceil(x + 0.1)\n", _TESTS, timeout=10)
ck("G4 冷 import 不被误伤（stdlib 必须在白名单里，否则沙箱=拒绝执行）",
   ex3.rc == 0 and ex3.n_passed == 1
   and ex3.sandbox.get("denied_count", 0) == 0,
   f"rc={ex3.rc} denied={ex3.sandbox.get('denied')}")

ck("G5 audit 层的**意图与实测分开记**（装没装 ≠ 说没说）",
   ex0.sandbox.get("fs_audit") is True
   and ex0.sandbox.get("fs_audit_applied") is True,
   f"{ex0.sandbox.get('fs_audit')} / {ex0.sandbox.get('fs_audit_applied')}")

ck("G6 `fs_isolated` 现在包含 audit 层（OS 层拿不到也仍为 True）",
   ex0.sandbox.get("fs_isolated") is True,
   f"os_sandbox={ex0.sandbox.get('os_sandbox')} "
   f"fs_isolated={ex0.sandbox.get('fs_isolated')}")

# ══ H 组：内存限制（rlimit 在本机失效后的两条替代路线）═════════════
print()
print("== H 组：内存限制（rlimit 三条全拒 ⇒ 进程内守卫 + 父进程看门狗）==")

_orig_cap = E.MEM_CAP_BYTES
E.MEM_CAP_BYTES = 64 * 1024 * 1024      # ⛔ 压到 64 MB：别真吃 256 MB
try:
    ex4 = E.run_in_sandbox(
        "def f(x):\n    a = []\n    while True:\n"
        "        a.append(bytearray(1024 * 1024))\n    return x\n",
        _TESTS, timeout=20)
    ck("H1 吃内存的代码被**杀掉**（rc=137），不是把机器拖死",
       ex4.rc == 137, f"rc={ex4.rc} stderr={(ex4.stderr or '')[:80]}")
    ck("H2 死法被记账（谁动的手必须看得见，否则无法区分超时/OOM/自杀）",
       ex4.sandbox.get("mem_killed") in ("in_process_guard", "watchdog"),
       f"mem_killed={ex4.sandbox.get('mem_killed')}")
    ck("H3 父进程看门狗的可用性**如实记账**（ps 不可用 ⇒ usable=False）",
       "usable" in (ex4.sandbox.get("mem_watchdog") or {}),
       f"{ex4.sandbox.get('mem_watchdog')}")
finally:
    E.MEM_CAP_BYTES = _orig_cap

print()
print("=" * 60)
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print("  ✗", f)
else:
    print("✅ 沙箱契约全部通过：隔离是实测的，降级是可见的")
sys.exit(1 if FAILS else 0)
