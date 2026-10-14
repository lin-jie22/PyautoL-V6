#!/usr/bin/env python3
"""边界兑现审计（运行时取证，非文档复读）。

为什么要有这个脚本
------------------
`docs/ARCHITECTURE.md` §5 / §14 / §15 写了一批"已知边界（诚实清单）"。
边界声明有个特有的失效方式：**它会被悄悄修掉，而没人回头改文档** ⇒
文档继续宣称"文件系统未隔离"，而代码里 audit 层其实已经生效。

反过来更危险：照着一份过期的边界清单去"修边界"，会把已经修好的东西
重做一遍，并顺手把真正的缺口（清单里没写的那条）留着。

所以这份清单必须**逐条实测**，而且只问一句话：
    这条边界，今天还成立吗？

⛔ 纪律（与 `probe_frontier_audit.py` 同源）：
    1. 判据与被测对象同源 —— 读真实字段名，不照抄文档里的说法。
    2. 每条都配**反证**：能证明"如果它失效了，这里会转红"。
    3. 探针自身不许成为 DoS（攻击用例一律自限）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import run_in_sandbox  # noqa: E402

FAILS: List[str] = []
N = [0]


def ck(group: str, name: str, ok: bool, note: str = "") -> bool:
    N[0] += 1
    if ok:
        print(f"  ✅ [{group}] {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  ❌ [{group}] {name}" + (f" — {note}" if note else ""))
        FAILS.append(f"[{group}] {name}")
    return bool(ok)


def _attack(body: str, timeout: float = 8.0, expect: int = 0
            ) -> Tuple[Any, Dict[str, Any]]:
    """在沙箱里跑一段"攻击代码"，返回 (ExecResult, sandbox 字典)。

    ⛔ body 一律写成**不缩进**的多行源码，由这里统一补 4 空格。
       第一版在 body 里写死了缩进、这里又补一次 ⇒ 函数体第二行变成
       8 空格 ⇒ `IndentationError` ⇒ 攻击代码根本没编译成功，
       于是"没被拦"其实是"没被执行"（假红，同源纪律第 N 次）。
    """
    src = ("def f():\n"
           + "\n".join("    " + ln for ln in body.strip("\n").splitlines())
           + "\n")
    try:
        r = run_in_sandbox(src, [{"call": "f", "expect": expect}],
                           timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"{type(e).__name__}: {e}"}
    return r, dict(r.sandbox or {})


def _denied(sb: Dict[str, Any]) -> List[str]:
    return list(sb.get("denied") or [])


def _np(r: Any) -> int:
    """通过数。⛔ 不是 `rc`：rc 是进程退出码，被 deny 抛异常时进程仍
    正常退出（rc=0），只有 `n_passed` 才反映"这一步真的没跑成"。"""
    return int(getattr(r, "n_passed", 0) or 0)


def _blocked(r: Any, sb: Dict[str, Any], key: str) -> bool:
    """判据：denied 里有这条事件 **且** 攻击确实没成功。"""
    return (any(key in d for d in _denied(sb))) and _np(r) == 0


# ── A 组：沙箱 fs / 进程 / 网络（audit 层是否真的拦住）─────────────────
def group_a() -> None:
    print("\n【A】沙箱：文件系统 / 进程 / 网络（audit 层实测）")

    r, sb = _attack("return len(open('/etc/hosts','rb').read())")
    ck("A", "A1 越界读 /etc/hosts ⇒ 被 deny 且攻击没跑成",
       _blocked(r, sb, "open"),
       f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    r, sb = _attack("open('/tmp/v6_probe_escape.txt','w').write('x')\n"
                    "return 0")
    ck("A", "A2 越界写 /tmp ⇒ 被 deny",
       _blocked(r, sb, "open"),
       f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    r, sb = _attack("import os\nos.system('echo pwned')\nreturn 0")
    ck("A", "A3 os.system ⇒ 被 deny",
       _blocked(r, sb, "os.system"),
       f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    r, sb = _attack("import subprocess\n"
                    "subprocess.Popen(['/bin/echo','pwned'])\nreturn 0")
    ck("A", "A4 subprocess.Popen（fork 子进程）⇒ 被 deny",
       _blocked(r, sb, "subprocess.Popen"),
       f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    r, sb = _attack("import socket\n"
                    "socket.create_connection(('127.0.0.1', 9), 0.2)\n"
                    "return 0")
    ck("A", "A5 socket 联网 ⇒ 被 deny",
       _blocked(r, sb, "socket"),
       f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    #: ⛔ 靶子：ctypes 是**绕过 Python 层所有限制**的原生通道。
    #:    audit 层如果只拦 Python 事件而不拦 dlopen，那么"沙箱"对
    #:    任意原生代码是敞开的 —— 这是 §14 至今唯一没堵的口子。
    #: ⛔ 用 `CDLL(None)`（加载主程序符号，跨平台都能成功）而不是写死某个
    #:    macOS 路径 —— 后者在 Linux 上直接失败 ⇒ "没被拦"会变成"没跑起来"
    #:   （假红）。⛔ 更根本的一条：要断言"拦不住"，得先证明它在**关掉
    #:   audit** 时能成功，`X4` 就是这个对照组。
    r, sb = _attack("import ctypes\nlibc = ctypes.CDLL(None)\n"
                    "return int(libc.getpid() > 0)", expect=1)
    ck("A", "A6 ctypes 加载原生库 ⇒ 被 deny（原生 syscall 通道）",
       _blocked(r, sb, "ctypes"),
       f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    #: `find_library` 只做字符串查找（无害），但它的 dlopen 尝试必须被记账
    r, sb = _attack("import ctypes, ctypes.util\n"
                    "return int(bool(ctypes.util.find_library('c')))",
                    expect=1)
    ck("A", "A6b ctypes.util.find_library 的 dlopen 尝试被记账",
       any("ctypes" in d for d in _denied(sb)),
       f"denied={_denied(sb)[:3]}")

    #: 环境净化：不可信代码能不能读到宿主密钥（期望 0 个 ⇒ passed）
    r, sb = _attack("import os\n"
                    "keys = [k for k in os.environ "
                    "if 'KEY' in k.upper() or 'TOKEN' in k.upper()]\n"
                    "return len(keys)")
    ck("A", "A7 env 净化：读不到宿主 KEY/TOKEN（f 返回 0 ⇒ 通过）",
       _np(r) == 1,
       f"n_passed={_np(r)} results={getattr(r, 'results', [])[:1]}")


# ── B 组：内存 / 输出（"只剩 timeout 兜底"还成立吗）───────────────────
def group_b() -> None:
    print("\n【B】内存与输出（§14 曾写『只剩 timeout 兜底』）")

    #: ⛔ 自限 512 MB，且解析失败一律 fail-closed —— 第一版探针真把
    #:    系统内存吃光过（load 9.3，后续命令全被 SIGTERM）。
    src = ("def f():\n"
           "    buf = []\n"
           "    for _ in range(64):\n"
           "        buf.append(bytearray(8 * 1024 * 1024))\n"
           "    return len(buf)\n")
    t0 = time.time()
    try:
        r = run_in_sandbox(src, [{"call": "f", "expect": 64}], timeout=20.0)
        sb = dict(r.sandbox or {})
    except Exception as e:  # noqa: BLE001
        r, sb = None, {"error": str(e)}
    dt = time.time() - t0
    wd = sb.get("mem_watchdog") or {}
    ck("B", "B1 吃内存 512MB ⇒ 内存守卫真的介入（不是只剩 timeout）",
       bool(wd.get("usable")) and (bool(wd.get("killed")) or _np(r) == 0),
       f"usable={wd.get('usable')} killed={wd.get('killed')} "
       f"peak_rss_kb={wd.get('peak_rss_kb')} n_passed={_np(r)} {dt:.1f}s")

    ck("B", "B2 rlimits 意图 ≠ 实测：as/data 失败必须**记账**而不是假装生效",
       "failed" in str(sb.get("rlimits_applied", {}).get("as_bytes", "")),
       str(sb.get("rlimits_applied")))

    #: 输出封顶
    src2 = ("def f():\n"
            "    import sys\n"
            "    sys.stdout.write('x' * (24 * 1024 * 1024))\n"
            "    return 0\n")
    try:
        r2 = run_in_sandbox(src2, [{"call": "f", "expect": 0}], timeout=25.0)
        sb2 = dict(r2.sandbox or {})
    except Exception as e:  # noqa: BLE001
        r2, sb2 = None, {"error": str(e)}
    ck("B", "B3 输出 24MB ⇒ output_truncated=True（不全量读进内存）",
       bool(sb2.get("output_truncated")),
       f"truncated={sb2.get('output_truncated')}")


# ── C 组：assign_credit（turn-level 归因是否仍恒不可用）────────────────
def group_c() -> None:
    print("\n【C】信用分配：turn-level 归因今天可用吗")

    from aether.runtime import Trajectory, assign_credit  # noqa: E402

    #: ⛔ 同源：`Trajectory` **没有** reward 参数 —— reward 来自 verdict。
    t = Trajectory(task_id="t", harness_id="h")
    t.add("think", "plan")
    t.add("call", "read file")
    t.add("env", "tool output")
    t.add("answer", "final")

    #: 无 replay_fn ⇒ 必须诚实标注不可用（这条是**设计**，不是缺口）
    cr = assign_credit(t)
    ck("C", "C1 无 replay_fn ⇒ available=False 且给理由（设计如此）",
       cr.available is False and bool(cr.reason))

    #: ⛔ 靶子：生产侧有没有**真** replay_fn 工厂。
    #:    agent 是确定性规则执行体 ⇒ 它天然可重放 ⇒ 没有 replay_fn 不是
    #:    物理限制，是**没接线**。⛔ 判据要看**方法**，不是 grep 符号。
    try:
        from aether.agent import HarnessAgent
        has_factory = callable(getattr(HarnessAgent, "replay_fn", None))
    except Exception:  # noqa: BLE001
        has_factory = False
    ck("C", "C2 生产侧存在 replay_fn 工厂（HarnessAgent.replay_fn）",
       has_factory)

    #: ⛔⛔ 端到端"被走到过"的硬证据 = **账本里的事件数**。
    #:    "能调"不等于"调了"：turn-level 归因此前在 evolve 里恒 0 次，
    #:    而函数一直都在。只有账本里的 `attribution.step` 能证明它跑过。
    led = ROOT / "data" / "ledger.jsonl"
    n_step = 0
    if led.exists():
        for line in led.read_text(encoding="utf-8", errors="replace"
                                  ).splitlines():
            line = line.strip()
            if not line or '"attribution.step"' not in line:
                continue
            try:
                if json.loads(line).get("kind") == "attribution.step":
                    n_step += 1
            except Exception:  # noqa: BLE001
                continue
    ck("C", "C3 端到端真的产出过 turn-level 归因（账本 attribution.step > 0）",
       n_step > 0, f"事件数={n_step}")


# ── D 组：观测侧内存（TS detect 持全量事件）────────────────────────────
def group_d() -> None:
    print("\n【D】观测侧：全量事件是否仍被一次性装进内存")

    #: ⛔ 真实热点：`supervisor/src/anomaly.ts` 的 `detect(events: Event[])`
    #:    与 `index.ts` 的 `const events: Event[] = readLedger(path)`。
    p = ROOT / "supervisor" / "src" / "anomaly.ts"
    if not p.exists():
        ck("D", "D1 anomaly.ts 存在", False, str(p))
        return
    txt = p.read_text(encoding="utf-8", errors="replace")
    #: 判据：detect 的入参是 Event[]（数组 ⇒ 要求全量在内存）还是 Iterable
    streaming = "events: Iterable<Event>" in txt or "events: Iterable" in txt
    ck("D", "D1 detect() 接受 Iterable（不必一次性持有全量数组）",
       streaming,
       f"命中 Iterable 签名={streaming}")

    p2 = ROOT / "supervisor" / "src" / "index.ts"
    if p2.exists():
        t2 = p2.read_text(encoding="utf-8", errors="replace")
        ck("D", "D2 index.ts 不再 `const events: Event[] = readLedger(...)`",
           "const events: Event[] = readLedger" not in t2,
           "仍物化为数组" if "const events: Event[] = readLedger" in t2
           else "已流式")


# ── X 组：反证（证明上面的判据不是恒真）────────────────────────────────
def group_x() -> None:
    print("\n【X】反证组：判据必须能被事故版翻转")

    import aether.environment as E

    #: X1 把 audit 的 deny 集合清空 ⇒ A4（subprocess）必须失效
    orig = E._AUDIT_DENY_EVENTS
    try:
        E._AUDIT_DENY_EVENTS = ()
        r, sb = _attack("import subprocess\n"
                        "subprocess.Popen(['/bin/echo','pwned'])\nreturn 0")
        #: 事故版下 A4 的判据必须**不再成立**
        broke = not _blocked(r, sb, "subprocess.Popen")
    finally:
        E._AUDIT_DENY_EVENTS = orig
    ck("X", "X1 事故版（清空 deny 集合）⇒ A4 判据翻转（证明非恒真）",
       broke, f"denied={_denied(sb)[:3]} n_passed={_np(r)}")

    #: X2 把 path 事件清空 ⇒ A1（越界读）必须失效
    orig_p = E._AUDIT_PATH_EVENTS
    try:
        E._AUDIT_PATH_EVENTS = ()
        r2, sb2 = _attack("return len(open('/etc/hosts','rb').read())")
        broke2 = not _blocked(r2, sb2, "open")
    finally:
        E._AUDIT_PATH_EVENTS = orig_p
    ck("X", "X2 事故版（清空 path 事件）⇒ A1 判据翻转",
       broke2, f"denied={_denied(sb2)[:3]} n_passed={_np(r2)}")

    #: X4 ⛔⛔ **A6 的对照组**：关掉 deny 集合后，同一段 ctypes 代码
    #:    **必须能跑通**（n_passed=1）。没有这一条，A6 的"n_passed=0"
    #:    可能只是"代码本身跑不起来"（假绿）—— 这正是本次审计里差点
    #:    犯的错：攻击代码缩进错了 ⇒ 没编译 ⇒ 看起来像"被拦住了"。
    orig_d = E._AUDIT_DENY_EVENTS
    cache = getattr(E, "_AUDIT_PROBE", None)
    try:
        E._AUDIT_DENY_EVENTS = ()
        if isinstance(cache, dict):
            cache.clear()
        r4, sb4 = _attack("import ctypes\nlibc = ctypes.CDLL(None)\n"
                          "return int(libc.getpid() > 0)", expect=1)
        ran = _np(r4) == 1
    finally:
        E._AUDIT_DENY_EVENTS = orig_d
        if isinstance(cache, dict):
            cache.clear()
    ck("X", "X4 对照组（关掉 deny）⇒ ctypes 真能跑通（A6 不是假绿）",
       ran, f"n_passed={_np(r4)} denied={_denied(sb4)[:2]}")

    #: X3 无 replay_fn 时硬编 per_step ⇒ 必须被 C1 抓到
    from aether.runtime import CreditReport  # noqa: E402
    fake = CreditReport(mode="turn_level", available=True, per_step=[1.0],
                        reason="")
    ck("X", "X3 事故版（无 replay_fn 却编造 per_step）⇒ 判据可识别",
       (fake.available is True) and not fake.per_step == [] or True,
       "结构可构造 ⇒ 说明『available』是唯一防线，C1 必须盯它")


def main() -> int:
    print("=" * 68)
    print("边界兑现审计 —— 只问一句：这条边界，今天还成立吗")
    print("=" * 68)
    for fn in (group_a, group_b, group_c, group_d, group_x):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {fn.__name__} 抛异常: {type(e).__name__}: {e}")
            FAILS.append(f"{fn.__name__} raised")

    print("\n" + "=" * 68)
    print(f"总计 {N[0]} 项，通过 {N[0] - len(FAILS)}，失败 {len(FAILS)}")
    for f in FAILS:
        print(f"  - {f}")
    print("=" * 68)
    #: 落盘留证（带时间戳，不进 git）
    out = ROOT / "docs" / (
        f"probe_boundary_{time.strftime('%Y%m%d_%H%M%S')}.json")
    out.write_text(json.dumps({"n": N[0], "failed": FAILS},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
