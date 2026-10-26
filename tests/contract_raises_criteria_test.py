#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：判据形态必须有【期望异常】这一支（`raises` 槽位）。

═══ 存在的理由（2026-10-26 实测取证）═══

README 153 记了三道串联的门，真实 bug 要进评测得全过：形态门 / 判据门 /
上下文门。其中**判据门**的具体缺口是：F2P 大量用异常表达，而判据只有两支
（返回值相等 `expect`、truthy），没有"期望抛出指定异常"这一支。

实测 boltons `c1c25da`（`Bits` 长度检查 off-by-one，`val > 2**len_` →
`val >= 2**len_`，**纯算子级、形态门过得去**）：

    加槽位前：有鉴别力的判据 **0 条** ⇒ 这个真实 bug **完全不可解**
    加槽位后：有鉴别力的判据 2 条（`Bits(4, 2)` / `Bits(1, 0)` ⇒ raises ValueError）
              ⇒ V6 第 1 轮 `fix_control_flow` reward=1.0 提交 ⇒ 25/25 修好

⇒ 这是"打开一道门直接多出一个可解的真实 bug"的实测证据，不是形态推断。

═══ ⛔ 为什么 `why` 的归属是本契约的核心（不是实现细节）═══

`classify_failure` 把失败分成三类，而注释明写：**crash 类（raise / import）
会把题源强度读数吹胖** —— 崩溃题任何"把源码改回基线"的修法都能顺手修好，
reward 里完全看不见。所以 `raises` 判据失败时归哪一类，决定了它是**强题**
还是**吹胖读数的弱题**：

    抛对了         ⇒ behavior（通过）
    **该抛没抛**   ⇒ mismatch ⇒ 归 **behavior** ⇒ 强题 ✅
    抛了别种类型   ⇒ raise    ⇒ 归 crash    ⇒ 弱题

⛔ 不新增第四类 `why`：三类口径是**尺子**，为迁就新功能改尺子会让既有读数
不可比（第 83 条）。⇒ B 组专门钉这个归属。

═══ 组 ═══

  A 三情形：抛对 / 该抛没抛 / 抛错类型，passed 与 why 各自正确
  B ⛔ why 归属："该抛没抛"必须是 behavior（强题），不是 crash
  C ⛔ 受控词表：异常名必须是内建异常；任意表达式**不许被执行**
  D ⛔ 反证：没有 raises 这一支时，同一个判据在 fixed 版上**恒失败**
    ⇒ 证明"加这个槽位"不是锦上添花，是那道门本身
  E 回归：既有两支（expect / truthy）不受影响

跑法（离线、秒级）：
    python tests/contract_raises_criteria_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.environment import (Environment, classify_failure,  # noqa: E402
                                verify_source)

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: 沙箱 runner 在 3.9 上同样可用（本契约 3.9 实测全绿）。
MIN_PYTHON = (3, 9)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: 三种源码：正好抛 / 该抛没抛 / 抛了别的类型
SRC_RAISES = "def f(x):\n    if x < 0:\n        raise ValueError('neg')\n    return x\n"
SRC_SILENT = "def f(x):\n    return x\n"
SRC_OTHER = "def f(x):\n    raise TypeError('oops')\n"

#: 判据：期望 f(-1) 抛 ValueError
CRIT = {"name": "neg", "call": "f", "args": [-1], "raises": "ValueError"}


def run(src: str, crit, timeout: float = 10.0):
    env = Environment(env_id="t", source=src, tests=(crit,))
    v = verify_source(env, source=src, timeout=timeout)
    why = (v.evidence.get("why") or {}).get(crit.get("name", "?"))
    ev = ""
    for c in v.checks:
        if c.name == crit.get("name", "?"):
            ev = c.evidence
    return bool(v.checks and v.checks[0].passed), why, ev


def main() -> int:
    print("=" * 74)
    print("契约：判据形态必须有【期望异常】这一支（raises 槽位）")
    print("=" * 74)

    print("\n== A 组：三情形的 passed 与 why ==")
    ok_hit, why_hit, ev_hit = run(SRC_RAISES, CRIT)
    ck("A1 抛对了期望的异常 ⇒ passed=True", ok_hit, f"why={why_hit} ev={ev_hit}")
    ck("A2 抛对了 ⇒ why=behavior", why_hit == "behavior", f"why={why_hit}")

    ok_miss, why_miss, ev_miss = run(SRC_SILENT, CRIT)
    ck("A3 该抛没抛 ⇒ passed=False", not ok_miss, f"why={why_miss}")
    ck("A4 该抛没抛 ⇒ why=mismatch", why_miss == "mismatch", f"why={why_miss}")
    ck("A5 evidence 说清是 'no raise'", "no raise" in ev_miss, f"ev={ev_miss}")

    ok_oth, why_oth, ev_oth = run(SRC_OTHER, CRIT)
    ck("A6 抛了别种类型 ⇒ passed=False", not ok_oth, f"why={why_oth}")
    ck("A7 抛了别种类型 ⇒ why=raise", why_oth == "raise", f"why={why_oth}")
    ck("A8 evidence 带上实际抛的类型", "TypeError" in ev_oth, f"ev={ev_oth}")

    print("\n== B 组：⛔ why 归属决定这道题是强题还是弱题 ==")
    ck("B1 '该抛没抛' ⇒ classify 为 behavior（**强题**）",
       classify_failure(why_miss) == "behavior",
       f"classify({why_miss})={classify_failure(why_miss)}")
    ck("B2 '抛了别种类型' ⇒ classify 为 crash（弱题，会被强度读数区别对待）",
       classify_failure(why_oth) == "crash",
       f"classify({why_oth})={classify_failure(why_oth)}")
    #: ⛔ 这是本契约最要紧的一条：若"该抛没抛"被归成 crash，那么用 raises
    #:    造出来的题全都是会吹胖读数的弱题 —— 那还不如不加这一支。
    ck("B3 ⛔ 边界类 bug 造出来的是**强题**不是崩溃题",
       classify_failure(why_miss) != "crash",
       "该抛没抛被归成 crash ⇒ raises 只会制造弱题")

    print("\n== C 组：⛔ 受控词表 —— 异常名不许是任意表达式 ==")
    for bad in ("eval", "__import__('os').system", "NoSuchError", 123, None):
        ok_b, why_b, ev_b = run(SRC_SILENT,
                                {"name": "neg", "call": "f", "args": [-1],
                                 "raises": bad})
        ck(f"C ⛔ raises={bad!r} ⇒ 判失败（不许静默降级成 truthy）",
           not ok_b, f"passed={ok_b} why={why_b}")
        ck(f"C ⛔ raises={bad!r} ⇒ evidence 说清是规格错",
           "bad raises spec" in ev_b, f"ev={ev_b}")
    #: ⛔ 安全：任意表达式**没有被执行**（只是查表失败）
    ok_s, _, ev_s = run(SRC_SILENT, {"name": "neg", "call": "f", "args": [-1],
                                     "raises": "__import__('os').system"})
    ck("C-安全 任意表达式没有被执行（否则这里会是 system 的返回值语义）",
       "bad raises spec" in ev_s, f"ev={ev_s}")

    print("\n== D 组：⛔ 反证 —— 没有这一支时，同一个判据不可用 ==")
    #: 旧行为下 `raises` 键被忽略 ⇒ 落进 truthy 分支：`bool(f(-1))`
    #:  · fixed 版抛 ValueError ⇒ 被 except 抓 ⇒ passed=False（**恒失败**）
    #:  · buggy 版正常返回 ⇒ truthy ⇒ passed=True
    #: ⇒ 判据方向是反的，且 fixed 版恒挂 ⇒ 这个真实 bug 根本进不了评测。
    truthy_crit = {"name": "neg", "call": "f", "args": [-1]}
    ok_t_fixed, why_t_fixed, _ = run(SRC_RAISES, truthy_crit)
    ok_t_buggy, why_t_buggy, _ = run(SRC_SILENT, truthy_crit)
    ck("D1 旧形态（truthy）在**修复版**上失败 ⇒ 修好了反而挂",
       not ok_t_fixed, f"passed={ok_t_fixed} why={why_t_fixed}")
    ck("D2 旧形态（truthy）在**缺陷版**上通过 ⇒ 判据方向是反的",
       ok_t_buggy, f"passed={ok_t_buggy} why={why_t_buggy}")
    #: ⛔ 这两条合起来就是"加 raises 之前这个 bug 不可解"的机制说明，
    #:    它让 A 组不是恒真：若哪天 raises 被删掉，D1/D2 会说明为什么不行。

    print("\n== E 组：回归 —— 既有两支不受影响 ==")
    ok_e, why_e, ev_e = run(SRC_SILENT, {"name": "p", "call": "f", "args": [3],
                                         "expect": 3})
    ck("E1 expect 相等 ⇒ 通过", ok_e, f"why={why_e} ev={ev_e}")
    ok_e2, why_e2, _ = run(SRC_SILENT, {"name": "p", "call": "f", "args": [5],
                                        "expect": 3})
    ck("E2 expect 不等 ⇒ mismatch", (not ok_e2) and why_e2 == "mismatch",
       f"why={why_e2}")
    ok_t, why_t, _ = run(SRC_SILENT, {"name": "p", "call": "f", "args": [7]})
    ck("E3 无 expect ⇒ truthy 分支仍在", ok_t, f"why={why_t}")
    ok_t0, _, _ = run(SRC_SILENT, {"name": "p", "call": "f", "args": [0]})
    ck("E4 truthy 分支：0 ⇒ 不通过", not ok_t0, f"passed={ok_t0}")

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print("  ✗", f)
        return 1
    print("✅ 判据有【期望异常】这一支，且失败归类是强题、异常名受控")
    return 0


if __name__ == "__main__":
    sys.exit(main())
