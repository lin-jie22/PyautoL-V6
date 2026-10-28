#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**装载门**（`run_in_sandbox(pkg=...)`）的方向、边界与隔离不变性。

═══ 存在的理由（README 164 的取证结论）═══

163 在 boltons 上量出「覆盖率 5%」，用户拍板换第二个仓库验证。结果**根本
读不到**：

| 仓库 | 取到源码 | 装得起 | 抽得到判据 |
|---|---|---|---|
| boltons（对照） | 30/30 | 29/30 (97%) | 27/30 (90%) |
| more-itertools | 25/25 | **2/25 (8%)** | **0/25** |
| attrs | 25/25 | **2/25 (8%)** | 2/25 |

Root cause 只有一处、与能力无关：

    ImportError: attempted relative import with no known parent package

沙箱把被测模块当 `task.py` **单独 exec** ⇒ 没有父包 ⇒ 任何包内相对导入都炸。
boltons 恰好不用相对导入（当初选它的理由正是「113 文件零依赖」）⇒ **5% 是
选择偏差的产物**。⇒ 「最大的那道门」被重新排序：在抽取/鉴别力/形态**之前**
还站着一道**装载门**，它拦掉 92%。

═══ ⛔⛔ 本契约最要紧的一条：**隔离不变性** ═══

沙箱是 V6 的**安全边界**。本轮扩大了它的**装载面**（同包模块能互相 import），
但**不许**削弱它的隔离面。C 组盯的就是这条：传了 `pkg` 之后，越界读、越界写、
deny-modules、fs_audit 记账都**必须照常生效**。谁为了让装载率好看去放宽任何
一条，C 组会红。

═══ 组 ═══

  A ⛔⛔ 向后兼容：不传 pkg ⇒ 与加这个参数之前**一字不差**
  B 整包装载：相对导入可用 + `__init__` 能 import 被测模块（顺序不变量）
  C ⛔⛔ 隔离不变性：越界读/写、deny-modules、audit 记账都照常
  D ⛔ 失败不静默：同包模块装不起来 ⇒ 如实报 import_error
  E ⛔⛔ 依赖裁剪（`needed_modules`）：**无关模块连坐**这条必须有反证
  F `pkg_deps` 覆盖相对/绝对两种形态
  G 依托真实仓库的读数（缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）

跑法（离线、秒级）：
    python tests/contract_package_load_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

from aether.environment import run_in_sandbox                   # noqa: E402
from aether.extract import needed_modules, pkg_deps             # noqa: E402

SKIPS: list = []
FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


#: 被测模块（包内相对导入）
REL_SRC = "from .helper import f\n\ndef g(x):\n    return f(x) + 1\n"
HELPER = "def f(x):\n    return x * 2\n"
T_OK = [{"name": "t0", "call": "g(1)", "expect": 3}]


def main() -> int:
    print("=" * 74)
    print("契约：装载门（`run_in_sandbox(pkg=...)`）")
    print("=" * 74)

    # ── A 组：⛔⛔ 向后兼容 ──────────────────────────────────────────
    print("\n== A 组：⛔⛔ 不传 pkg ⇒ 行为一字不差 ==")
    plain = "def f(x):\n    return x * 2\n"
    a0 = run_in_sandbox(plain, [{"name": "a", "call": "f(3)", "expect": 6}])
    ck("A1 单文件仍装得起、判据可跑",
       (not a0.import_error) and a0.results and a0.results[0]["passed"],
       str(a0.results))

    a1 = run_in_sandbox(REL_SRC, T_OK)
    ck("A2 相对导入·无 pkg ⇒ **必须** import 失败", bool(a1.import_error),
       str(a1.import_error)[:80])
    ck("A3 且错误信息就是那一条（否则本轮读数恒真）",
       "no known parent package" in (a1.import_error or ""),
       (a1.import_error or "")[-90:])
    ck("A4 失败时每条判据都记 import（不许记成 behavior）",
       all(r.get("why") == "import" for r in a1.results), str(a1.results))

    # ── B 组：整包装载 ──────────────────────────────────────────────
    print("\n== B 组：整包装载（相对导入可用 + 顺序不变量）==")
    pkg = {"name": "tp", "module": "core", "modules": {"helper": HELPER}}
    b1 = run_in_sandbox(REL_SRC, T_OK, pkg=pkg)
    ck("B1 相对导入·有 pkg ⇒ 装得起", b1.import_error == "",
       (b1.import_error or "")[-120:])
    ck("B2 且语义正确（g(1) == f(1)+1 == 3）",
       bool(b1.results) and b1.results[0]["passed"], str(b1.results))

    #: ⛔ 顺序不变量：`__init__` 常写 `from .<被测> import *`，而被测模块正
    #:   是要被替换的那一份 ⇒ 必须**先注册后 exec**，否则永远找不到。
    pkg_i = {"name": "tp2", "module": "core",
             "modules": {"": "from .core import g\n", "helper": HELPER}}
    b2 = run_in_sandbox(REL_SRC, T_OK, pkg=pkg_i)
    ck("B3 ⛔ `__init__` 能 import 被测模块（先注册后 exec）",
       b2.import_error == "", (b2.import_error or "")[-120:])

    #: 反证：被测模块依赖链两层（core → helper → base）。
    #: ⛔ 顺序**必须**走 `needed_modules`（规则只有一份），且这里故意把
    #:    pool 写成"依赖在后"的顺序 —— 手排的顺序是**错**的，正是这条
    #:    契约要验的：靠工具排，不靠人排。
    _pool = {"helper": "from .base import b\n\ndef f(x):\n    return b(x)\n",
             "base": "def b(x):\n    return x * 2\n"}
    _main = "from .helper import f\n\ndef g(x):\n    return f(x) + 1\n"
    ck("B4a 手排顺序（helper 在前）确实是错的 —— 反证",
       bool(run_in_sandbox(_main, [{"name": "t", "call": "g(2)", "expect": 5}],
                           pkg={"name": "tp3", "module": "core",
                                "modules": dict(_pool)}).import_error))
    b3 = run_in_sandbox(_main, [{"name": "t", "call": "g(2)", "expect": 5}],
                        pkg={"name": "tp3", "module": "core",
                             "modules": dict(needed_modules(_main, "tp3",
                                                            _pool))})
    ck("B4 两层依赖链（走 needed_modules 排序 ⇒ 装得起且语义对）",
       b3.import_error == "" and b3.results and b3.results[0]["passed"],
       (b3.import_error or "")[-120:])

    # ── C 组：⛔⛔ 隔离不变性（本契约最要紧的一组）──────────────────
    print("\n== C 组：⛔⛔ 传了 pkg 之后，隔离**一个都不许少** ==")
    esc_src = "s = open('/etc/hosts').read(4)\n"
    c0 = run_in_sandbox(esc_src, [], pkg=pkg)
    ck("C1 ⛔ 越界读仍被拒",
       bool(c0.import_error) and "PermissionError" in c0.import_error,
       (c0.import_error or "")[-100:])

    c1 = run_in_sandbox("open('/tmp/v6_escape_probe.txt', 'w').write('x')\n",
                        [], pkg=pkg)
    ck("C2 ⛔ 越界写仍被拒", bool(c1.import_error), (c1.import_error or "")[-100:])

    c2 = run_in_sandbox("import termios\nx = termios\n", [], pkg=pkg)
    ck("C3 ⛔ deny-modules 仍生效（termios）", bool(c2.import_error),
       (c2.import_error or "")[-100:])

    #: 沙箱自检字段照常记账（传 pkg 不许让 fs_audit 消失）
    c3 = run_in_sandbox(plain, [], pkg=pkg)
    sb = c3.sandbox or {}
    ck("C4 沙箱记账字段齐全（tier / fs_audit / rlimits）",
       {"tier", "fs_audit", "rlimits"} <= set(sb), str(sorted(sb))[:120])
    ck("C5 fs_audit 真的生效了（不是空声明）",
       bool(sb.get("fs_audit_applied")) or not sb.get("fs_audit"),
       str({k: sb.get(k) for k in ("fs_audit", "fs_audit_applied")}))

    #: ⛔ runner 不读文件系统：modules 里没有的东西，**不许**去磁盘找
    c4 = run_in_sandbox("import tp5.ghost_module\nx = tp5.ghost_module\n", [],
                        pkg={"name": "tp5", "module": "core", "modules": {}})
    ck("C6 ⛔ 未在 modules 里的同包模块 ⇒ ImportError（不去磁盘找）",
       bool(c4.import_error), (c4.import_error or "")[-110:])

    # ── D 组：⛔ 失败不静默 ─────────────────────────────────────────
    print("\n== D 组：⛔ 装载失败不许静默（跳过 ≠ 通过，第 149 条）==")
    d0 = run_in_sandbox(REL_SRC, T_OK,
                        pkg={"name": "tp6", "module": "core",
                             "modules": {"helper": "def f(:\n"}})
    ck("D1 同包模块语法错误 ⇒ import_error 非空", bool(d0.import_error),
       str(d0.results)[:90])
    ck("D2 且判据被记成 import（不是「通过」）",
       all(r.get("why") == "import" for r in d0.results), str(d0.results)[:90])

    d1 = run_in_sandbox(REL_SRC, T_OK,
                        pkg={"name": "tp7", "module": "core",
                             "modules": {"helper": "import termios\n"}})
    ck("D3 同包模块触发 deny ⇒ 照样如实报（不降级成「跳过」）",
       bool(d1.import_error), str(d1.results)[:90])

    # ── E 组：⛔⛔ 依赖裁剪（无关模块连坐的反证）─────────────────────
    print("\n== E 组：⛔⛔ 依赖裁剪（`needed_modules`）==")
    pool = {"a": "from .b import z\n\ndef f():\n    return z\n",
            "b": "z = 1\n",
            "junk": "import termios\n"}
    got = [k for k, _ in needed_modules("from .a import x\n", "p", pool)]
    ck("E1 ⛔ 无关模块被裁掉（junk 不许进沙箱）",
       "junk" not in got and set(got) == {"a", "b"}, str(got))
    ck("E2 依赖排在被依赖者之后（b 在 a 前）",
       got.index("b") < got.index("a"), str(got))

    #: ⛔⛔ 反证：**不裁剪**的后果必须能被复现（这就是 boltons 100%→0% 的成因）
    _m = "from .a import f\n\ndef g():\n    return f()\n"
    e_bad = run_in_sandbox(_m, [{"name": "t", "call": "g()", "expect": 1}],
                           pkg={"name": "pz", "module": "core",
                                "modules": dict(pool)})
    e_ok = run_in_sandbox(_m, [{"name": "t", "call": "g()", "expect": 1}],
                          pkg={"name": "pz", "module": "core",
                               "modules": dict(needed_modules(_m, "pz",
                                                              pool))})
    ck("E3 ⛔ 不裁剪 ⇒ 被无关模块连坐至失败（反证）",
       bool(e_bad.import_error), str(e_bad.import_error)[-90:])
    ck("E4 裁剪后 ⇒ 装得起（同一份源码，唯一变量是裁剪）",
       e_ok.import_error == "", (e_ok.import_error or "")[-90:])

    #: ⛔⛔ 顺序不变量（实踩的 bug，attrs `validators.py`）：
    #:    主干同时依赖 setters 与 _config，而 setters 又依赖 _config /
    #:    exceptions ⇒ **不能**因为"_config 也在待办里"就把它算作已满足。
    _p2 = {"setters": "from . import _config\nfrom .exceptions import E\n",
           "_config": "X = 1\n", "exceptions": "class E(Exception):\n    pass\n"}
    _m2 = "from . import setters, _config\nQ = setters\n"
    _ord = [k for k, _ in needed_modules(_m2, "p2", _p2)]
    ck("E5 ⛔ 兄弟节点不算「已满足」：依赖必须排在被依赖者之前",
       _ord.index("_config") < _ord.index("setters")
       and _ord.index("exceptions") < _ord.index("setters"), str(_ord))
    _r5 = run_in_sandbox(_m2, [{"name": "t", "call": "Q.X", "expect": 1}],
                         pkg={"name": "p2", "module": "core",
                              "modules": dict(needed_modules(_m2, "p2", _p2))})
    ck("E5b 该形状在沙箱里真能装起来", _r5.import_error == "",
       (_r5.import_error or "")[-120:])

    ck("E6 循环依赖不死循环（路径判定兜底）",
       len(needed_modules("from .a import x\n", "p",
                          {"a": "from .b import y\n", "b": "from .a import x\n"}))
       == 2)
    ck("E7 语法错误的源码 ⇒ 依赖集为空集、不 raise",
       pkg_deps("def f(:\n", "p") == set())

    # ── F 组：`pkg_deps` 形态覆盖 ───────────────────────────────────
    print("\n== F 组：`pkg_deps` 覆盖相对 / 绝对两种形态 ==")
    src_f = ("from .a import x\n"
             "from .b.c import y\n"
             "from boltons.d import z\n"
             "import boltons.e.f\n"
             "import os\n"
             "from other.g import h\n")
    deps = pkg_deps(src_f, "boltons")
    ck("F1 相对导入（含两级）", {"a", "b"} <= deps, str(sorted(deps)))
    ck("F2 绝对包内导入（import + from）", {"d", "e"} <= deps, str(sorted(deps)))
    ck("F3 ⛔ 他包导入不算（os / other.g）",
       not ({"os", "other", "g"} & deps), str(sorted(deps)))

    # ── G 组：真实仓库读数（缺失则跳过并披露）────────────────────────
    print("\n== G 组：真实仓库读数（缺失 ⇒ 跳过并披露）==")
    probe = ROOT / "scripts" / "probe_package_load.py"
    if not probe.exists():
        skip("G1 装载门普查脚本存在", f"缺 {probe}")
    else:
        ck("G1 装载门普查脚本存在", True)
    for name in ("boltons", "more-itertools", "attrs"):
        if not Path(f"/tmp/realrepo/{name}").exists():
            skip(f"G2 真实仓库 {name}", f"/tmp/realrepo/{name} 不在本机")
        else:
            ck(f"G2 真实仓库 {name} 在位", True)

    print("\n" + "=" * 74)
    if FAILS:
        print(f"⛔ 契约失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print(f"✅ 全部通过（跳过 {len(SKIPS)} 项：{SKIPS or '无'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
