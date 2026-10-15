#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：`aether check` 自己必须是个**真闸**。

═══ 为什么这条要单独守 ═══

`check` 是"全量验收"的判据本身。判据自己错了，比被测对象错了更危险：
它会把"红"报成"绿"，而所有下游（CI、人、README 的"零新红"承诺）
都靠它。而收口前这件事的形态恰好就是坏的：

    for t in tests/contract_*_test.py; do $PY $t; $PY $t --e2e; done

· 没有失败聚合 ⇒ shell 退出码 = **最后一次迭代**的退出码 ⇒ 中间任何
  一份契约变红都被吞掉；
· `--e2e` 传给不支持的契约 ⇒ 几十次纯重复，报告上看起来"两种模式都验了"。

本文件守：
  A 发现与模式声明：`SUPPORTED_MODES` 被读懂，未声明 ≠ 只有一种模式；
  B 五态分类：pass / fail / timeout / error / skip 不合并；
  C **反证（本组的重点）**：往临时目录塞一份必红的契约 ⇒ 退出码必须非 0
    —— 若 `check` 只是"跑完就算过"，这条会转红；
  D 子集必须自我标注：只跑一份契约的报告**不许**与"全绿"同形。

跑法（离线，秒级）：
    python tests/contract_check_test.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。本契约只跑默认模式。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: 临时"事故版"契约：故意必红。⛔ 它必须**不进** tests/ 目录（否则会污染
#: 全量验收，而且会被别的静态闸当成真契约检查 —— 实测踩过）。
BAD = '''#!/usr/bin/env python
import sys
SUPPORTED_MODES = ("default",)
print("  故意失败")
sys.exit(1)
'''
GOOD = '''#!/usr/bin/env python
import sys
SUPPORTED_MODES = ("default",)
print("  故意通过")
sys.exit(0)
'''
TIMEOUT = '''#!/usr/bin/env python
import time
SUPPORTED_MODES = ("default",)
time.sleep(60)
'''
E2E = '''#!/usr/bin/env python
import sys
SUPPORTED_MODES = ("default", "e2e")
print("  e2e" if "--e2e" in sys.argv else "  default")
sys.exit(0)
'''
UNDECLARED = '''#!/usr/bin/env python
import sys
print("  没声明模式")
sys.exit(0)
'''


def main() -> int:
    from aether import check as C

    # ── A 组：发现与模式声明 ─────────────────────────────────────
    print("A 组：发现契约 + 读懂模式声明")
    real = C.discover(ROOT)
    ck("A1 发现到全部契约（>10 份，不是空集）", len(real) > 10, f"{len(real)} 份")
    #: ⛔ 静态读取，不许 import（import 契约 = 在 check 进程里执行它的断言）
    src = (ROOT / "tests" / "contract_check_test.py").read_text(encoding="utf-8")
    ck("A2 读取模式声明用的是**静态**读源码", "SUPPORTED_MODES" in src)
    ck("A3 本契约被读成只支持默认模式",
       C.declared_modes(ROOT / "tests" / "contract_check_test.py")
       == ("default",),
       str(C.declared_modes(ROOT / "tests" / "contract_check_test.py")))
    ck("A4 至少有一份契约声明了 e2e（否则'两种模式'是空话）",
       any(C.supports_e2e(p) for p in real),
       str([p.name for p in real if C.supports_e2e(p)]))

    tmp = Path(tempfile.mkdtemp(prefix="v6_check_"))
    tests = tmp / "tests"
    tests.mkdir()

    def write(name, body):
        (tests / name).write_text(body, encoding="utf-8")

    # ── B 组：五态分类 ───────────────────────────────────────────
    print("B 组：五态分类（不合并）")
    write("contract_good_test.py", GOOD)
    write("contract_bad_test.py", BAD)
    write("contract_slow_test.py", TIMEOUT)
    write("contract_undeclared_test.py", UNDECLARED)
    write("contract_e2e_test.py", E2E)
    rep = C.run_checks(tmp, timeout_s=3.0)
    got = {(r["script"], r["mode"]): r["status"] for r in rep["runs"]}
    ck("B1 通过 ⇒ pass", got.get(("contract_good_test.py", "default")) == C.PASS,
       str(rep["counts"]))
    ck("B2 非零退出 ⇒ fail", got.get(("contract_bad_test.py", "default")) == C.FAIL)
    ck("B3 超时 ⇒ timeout（不是 fail，也不是 pass）",
       got.get(("contract_slow_test.py", "default")) == C.TIMEOUT)
    ck("B4 未声明模式的契约也会被跑（默认模式兜底）",
       got.get(("contract_undeclared_test.py", "default")) == C.PASS)
    ck("B5 未声明模式被**点名**（未声明 ≠ 只有一种模式）",
       "contract_undeclared_test.py" in rep["undeclared_modes"],
       str(rep["undeclared_modes"]))
    ck("B6 声明 e2e 的契约跑了两遍（default + e2e）",
       got.get(("contract_e2e_test.py", "default")) == C.PASS
       and got.get(("contract_e2e_test.py", "e2e")) == C.PASS)
    ck("B7 未声明 e2e 的契约**不跑**第二遍（省下的不是覆盖，是重复）",
       ("contract_good_test.py", "e2e") not in got
       and "contract_good_test.py" in rep["e2e_not_supported"])
    ck("B8 五态计数与实际一致",
       rep["counts"][C.PASS] == 4 and rep["counts"][C.FAIL] == 1
       and rep["counts"][C.TIMEOUT] == 1,
       str(rep["counts"]))

    # ── C 组：反证 —— 它必须是个**闸** ───────────────────────────
    print("C 组：反证（有红的就必须红）")
    ck("C1 目录里有必红契约 ⇒ ok=False", rep["ok"] is False)
    ck("C2 且退出码非 0（否则 CI 拿到 0 会把红当绿）",
       rep["exit_code"] != 0, str(rep["exit_code"]))
    ck("C3 失败项被点名（能说清是哪一份）",
       any(r["script"] == "contract_bad_test.py"
           for r in rep["runs"] if r["status"] == C.FAIL))
    ck("C4 失败项带了退出码与输出尾巴（可诊断，不是只有一句红）",
       all(r["rc"] not in (None, 0) and r["tail"]
           for r in rep["runs"] if r["status"] == C.FAIL))
    #: 全绿目录 ⇒ 必须放行（闸不是恒红）
    tmp2 = Path(tempfile.mkdtemp(prefix="v6_check_ok_"))
    (tmp2 / "tests").mkdir()
    (tmp2 / "tests" / "contract_good_test.py").write_text(GOOD, encoding="utf-8")
    rep2 = C.run_checks(tmp2, timeout_s=10.0)
    ck("C5 反证的另一半：全绿 ⇒ ok=True 且退出码 0（不是恒红）",
       rep2["ok"] is True and rep2["exit_code"] == 0, str(rep2["counts"]))
    #: **空集**不许被当成通过（同 doctor 的"空套件 fail-closed"）
    tmp3 = Path(tempfile.mkdtemp(prefix="v6_check_empty_"))
    (tmp3 / "tests").mkdir()
    rep3 = C.run_checks(tmp3)
    #: ⛔ **0 份契约 = 无从判别 ⇒ fail-closed**（与 doctor 的空套件同源）。
    #:    这条断言最初写的是"n_runs == 0" —— 后来 check 增加了跨语言那一步
    #:    （0 份契约时仍会跑），于是它转红。但转红暴露的是**真问题**：
    #:    当时的报告是 `✅ 全部通过：0 份契约` —— 一个"什么都没验"的绿色结论。
    #:    判据本身为空时必须当失败处理，而不是数一数跑了几个子步骤。
    ck("C6 ⛔ 一份契约都没发现 ⇒ 判失败（判据为空 ≠ 全绿）",
       rep3["n_scripts"] == 0 and rep3["empty"] is True
       and rep3["ok"] is False and rep3["exit_code"] != 0,
       f"scripts={rep3['n_scripts']} empty={rep3.get('empty')} "
       f"ok={rep3['ok']} rc={rep3['exit_code']}")
    ck("C7 ⛔ 且摘要里**明说**是'没发现契约'，不与'全绿'同形",
       "一份契约都没发现" in C.summarize(rep3),
       C.summarize(rep3).splitlines()[0])

    # ── D 组：子集必须自我标注 ───────────────────────────────────
    print("D 组：子集不许与全量同形")
    rep_sub = C.run_checks(ROOT, only="contract_check", timeout_s=60.0)
    ck("D1 子集报告标了 subset=True", rep_sub["subset"] is True)
    text = C.summarize(rep_sub)
    ck("D2 摘要里**明说**这是子集（不能只靠 --only 参数自己记得）",
       "子集" in text, text[:80])
    ck("D3 全量报告不标子集",
       C.run_checks(tmp2, timeout_s=10.0)["subset"] is False)

    # ── E 组：CLI 真接上了（有模块 ≠ 有入口）─────────────────────
    print("E 组：CLI 入口真的接了")
    #: ⛔⛔ 这里**不能**用 `--only contract_check`：那会让 CLI 跑到本文件，
    #:    而本文件又来起 CLI ⇒ 无限递归（实测直接把测试挂到超时）。
    #:    静态闸的契约必须用一个**匹配不到任何契约**的筛子。
    r = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "check",
                        "--only", "contract_zzz_matches_nothing", "--json"],
                       cwd=str(ROOT), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=300)
    out = (r.stdout or b"").decode("utf-8", "replace")
    try:
        parsed = json.loads(out)
    except Exception:                                      # noqa: BLE001
        parsed = None
    ck("E1 `aether check --json` 输出可解析的结构化报告",
       isinstance(parsed, dict) and "counts" in parsed, out[-200:])
    ck("E2 CLI 退出码与报告里的 exit_code 一致（不是各算一份）",
       parsed is not None and r.returncode == parsed["exit_code"],
       f"rc={r.returncode} rep={parsed and parsed['exit_code']}")
    ck("E3 `--only` 真的被透传（筛子生效 ⇒ 0 份契约）",
       parsed is not None and parsed["n_scripts"] == 0
       and parsed["only"] == "contract_zzz_matches_nothing",
       str(parsed and {"n": parsed["n_scripts"], "only": parsed["only"]}))
    ck("E4 ⛔ 筛子匹配不到任何契约 ⇒ 报告必须**看得出来**（n_runs=0），"
       "不能与'全绿'同形",
       parsed is not None and parsed["n_runs"] == 0
       and parsed["subset"] is True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：check 会聚合退出码、分五态、并标注子集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
