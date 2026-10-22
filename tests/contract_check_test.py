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

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约，
#:    写死 ⇒ 契约永远 import 生产那份 ⇒ 删前删后跑的是同一份代码 ⇒
#:    对拍恒绿 ⇒ 那道闸是空的（实测：删掉 `_SLUG_RE` 定义后契约仍 rc=0）。
ROOT = Path(__file__).resolve().parents[1]
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
    #: ⛔⛔ 这里原来写的是 `run_checks(ROOT, only="contract_check")` ——
    #:    `contract_check` 正好匹配**本契约自己** ⇒ 它会跑一份自己的副本，
    #:    而那份副本的 D1 又跑自己 ⇒ 递归链只能靠 `timeout_s=60` 断掉。
    #:    后果实测：整份契约 **136s**，而且真实耗时 = 超时值 × 链长，
    #:    改一次 timeout 就换一个读数（同族：拿超时当终止条件）。
    #:    ⇒ 子集语义根本不需要拿真契约演示，临时目录里放两份假契约、
    #:      只筛其中一份，语义完全等价且毫秒级。
    tmp4 = Path(tempfile.mkdtemp(prefix="v6_check_subset_"))
    (tmp4 / "tests").mkdir()
    (tmp4 / "tests" / "contract_good_test.py").write_text(GOOD, encoding="utf-8")
    (tmp4 / "tests" / "contract_bad_test.py").write_text(BAD, encoding="utf-8")
    rep_sub = C.run_checks(tmp4, only="good", timeout_s=10.0)
    ck("D0 ⛔ 筛子真的只放行一份（子集 ≠ 全量跑一遍再标记）",
       rep_sub["n_scripts"] == 1, f"n_scripts={rep_sub['n_scripts']}")
    ck("D1 子集报告标了 subset=True", rep_sub["subset"] is True)
    text = C.summarize(rep_sub)
    ck("D2 摘要里**明说**这是子集（不能只靠 --only 参数自己记得）",
       "子集" in text, text[:80])
    ck("D3 全量报告不标子集",
       C.run_checks(tmp2, timeout_s=10.0)["subset"] is False)
    _full4 = C.run_checks(tmp4, timeout_s=10.0)
    ck("D4 ⛔ 子集必须真的少于全量（此前 D1 跑向自己 ⇒ 整份契约 136s）",
       rep_sub["n_scripts"] < _full4["n_scripts"] and _full4["ok"] is False,
       f"subset={rep_sub['n_scripts']} full={_full4['n_scripts']}")

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

    # ── F 组：测量型契约必须声明独占（并发下读数不可信）─────────────
    print("F 组：测量型契约必须声明 EXCLUSIVE")
    _excl = [p.name for p in real if C.is_exclusive(p)]
    #: ⛔⛔ **把事故钉死**：这两份一旦被摘掉 EXCLUSIVE ⇒ 回到 4 路并发
    #:    ⇒ 墙钟被放大 ⇒ 稳定超时（实测两次独立运行均 `timeout=2`，
    #:      单独跑 60–113s，套件里 >600s）。摘声明看着像"清理无用配置"，
    #:    实际是拆掉承重结构 ⇒ 必须有断言拦住。
    for _m in ("contract_ops_test.py", "contract_streaming_test.py"):
        ck(f"F1 ⛔⛔ {_m} **必须**声明 EXCLUSIVE"
           f"（摘掉 ⇒ 并发失真 ⇒ 超时）",
           _m in _excl, f"exclusive={_excl}")
    ck("F2 ⛔ 普通契约**不**被误判为独占（否则全体串行 ⇒ 套件无谓变慢）",
       "contract_ledger_test.py" not in _excl
       and "contract_serve_test.py" not in _excl, str(_excl))
    #: ⛔ **反证**（第 8 条纪律）：把 `is_exclusive` 换成恒 False
    #:    ⇒ F1 的两条断言必须**转红**，否则 F1 是恒真断言。
    _orig = C.is_exclusive
    try:
        C.is_exclusive = lambda _p: False
        _red = sum(1 for _m in ("contract_ops_test.py",
                                "contract_streaming_test.py")
                   if not C.is_exclusive(ROOT / "tests" / _m))
        ck("F3 反证：is_exclusive 换成恒 False ⇒ F1 两条都要转红"
           "（证明 F1 有牙）",
           _red == 2, f"转红 {_red}/2")
    finally:
        C.is_exclusive = _orig

    # ── G 组：失败诊断尾巴必须带**信号**，不能只剩名字清单 ──────────
    #: ⛔⛔ 事故形态（真实发生过，不是假设）：CI 首跑 9 份红，报告里每份
    #:    只留下最后 4 行 —— 而本仓契约的结尾恰好都是「❌ N 项未通过：」
    #:    加名字清单。于是远端看到的是"哪几条断言名叫什么"，
    #:    看不到"实测读数是多少"⇒ 排查只能靠猜、或者把整个 CI 拉下来重跑。
    #:    ⇒ 判据必须是：**detail 要活着到报告里**，且省略要可见。
    print("G 组：失败尾巴必须带信号（旧口径只留末 4 行 ⇒ 只剩名字清单）")

    #: 夹具刻意二分：`FAIL` 那行带 detail（`rc=0 j=4`），
    #: 而结尾的名字清单**只印断言名**（不带 detail）。
    #: 这样"留末 4 行"与"留信号行"才能被区分开 —— 若两处都带 detail，
    #: 这个夹具就是恒真的（拿同一份信息当两个判据）。
    _DETAIL = "rc=0 j=4"
    _FAKE = "\n".join(
        [f"  PASS R{i} 普通断言 {i}" for i in range(1, 21)]
        + [f"  FAIL C2 并发墙钟必须收敛 · detail: {_DETAIL}"]
        + [f"  PASS S{i} 收尾断言 {i}" for i in range(1, 21)]
        + ["=" * 60, "❌ 1 项未通过：", "   - C2 并发墙钟必须收敛"])
    _OLD = "\n".join(_FAKE.strip().splitlines()[-4:])[:400]

    #: ⛔ 走 `_one` 而不是直接调 `_summarize_out`：**零件绿 ≠ 装配绿**。
    #:    直接调函数只能证明"这个函数会挑信号行"，证明不了 `_one` 真的
    #:    接了它（接线漏掉时函数照样绿，报告照样只剩名字清单）。
    _FIXTURE = '''#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sys
SUPPORTED_MODES = ("default",)
for i in range(1, 21):
    print(f"  PASS R{i} 普通断言 {i}")
print("  FAIL C2 并发墙钟必须收敛 · detail: rc=0 j=4")
for i in range(1, 21):
    print(f"  PASS S{i} 收尾断言 {i}")
print("=" * 60)
print("\u274c 1 项未通过：")
print("   - C2 并发墙钟必须收敛")
sys.exit(1)
'''
    _OK_FIXTURE = _FIXTURE.replace("sys.exit(1)", "sys.exit(0)").replace(
        "  FAIL C2", "  PASS C2")
    with tempfile.TemporaryDirectory() as _td:
        _bad = Path(_td) / "contract_detail_tail_test.py"
        _bad.write_text(_FIXTURE, encoding="utf-8")
        _good = Path(_td) / "contract_detail_ok_test.py"
        _good.write_text(_OK_FIXTURE, encoding="utf-8")

        _r = C._one(_bad, "default", ROOT, 120)
        ck("G1 ⛔ 端到端：失败契约的报告尾巴**必须**带断言 detail"
           "（旧口径只剩名字清单）",
           _r.status == C.FAIL and _DETAIL in _r.tail,
           f"status={_r.status} tail={_r.tail[-200:]!r}")
        ck("G2 ⛔ 夹具自证：同一份输出按**旧口径**取末 4 行 ⇒ detail 必丢"
           "（否则 G1 是恒真断言）",
           _DETAIL not in _OLD, f"old={_OLD[-120:]!r}")
        ck("G3 省略必须可见（写清省略多少行，不许静默丢）",
           "省略" in _r.tail, _r.tail[:120])

        _ro = C._one(_good, "default", ROOT, 120)
        ck("G4 成功时保持短尾巴（≤4 行、≤400 字）⇒ 真红不会被绿淹没",
           _ro.status == C.PASS and len(_ro.tail.splitlines()) <= 4
           and len(_ro.tail) <= 400, f"{len(_ro.tail.splitlines())} 行")

        _orig_s = C._summarize_out
        try:
            C._summarize_out = lambda out, ok, **k: (
                "\n".join(out.strip().splitlines()[-4:])[:400])
            _rb = C._one(_bad, "default", ROOT, 120)
            ck("G5 ⛔⛔ 反证：把汇总换回「末 4 行」⇒ G1 的判据必须转红"
               "（证明 G1 测的是这条接线，不是巧合）",
               _DETAIL not in _rb.tail, f"tail={_rb.tail[-120:]!r}")
        finally:
            C._summarize_out = _orig_s

    #: ⛔⛔ `✗` 必须算信号行：`contract_serve_test.py` 全部判据都用 `✗`。
    #:    漏了它 ⇒ 那份契约红了以后尾部**一条判据文字都没有**，只剩"退出码 1"
    #:    —— CI 3.9 上就是这样：看得到 I7/I8/I9/I11 四个名字，却看不到
    #:    任何一个 detail，四个红点等于零诊断。
    #:    夹具用 40 行填充，确保命中不是"恰好落在末几行"。
    #:    夹具必须把 `✗` 行放在**中段**：`_summarize_out` 失败时无条件保留
    #:    头 6 行 + 尾 12 行，放在那两段里的话"命中"跟信号检测毫无关系
    #:    —— 第一版我就写在第二行，注入反证时正则删掉了 `✗` 它**照样绿**，
    #:    是个恒真断言（第 9 条的老形状）。
    _noisy = ("  ✓ 前 18 行都正常\n" * 18
              + "  ✗ I7 ⛔ 装载 0 条 ⇒ 409\n"
              + "  .. filler ..\n" * 24
              + "  ✓ I12 结尾\n")
    ck("G6 ⛔ `✗` 也算信号行（serve 契约用它 ⇒ 漏了就没有任何 detail）",
       "I7" in C._summarize_out(_noisy, False),
       C._summarize_out(_noisy, False)[-160:])
    ck("G7 对照：同一段输出把 `✗` 换成 `✓` ⇒ 中段那行必须被省略"
       "（否则 G6 是恒真）",
       "I7" not in C._summarize_out(_noisy.replace("✗", "✓"), False),
       C._summarize_out(_noisy.replace("✗", "✓"), False)[-160:])

    # ── H 组：MIN_PYTHON 版本闸 ────────────────────────────────────
    print("H 组：MIN_PYTHON 版本闸（低于声明 ⇒ SKIP，且不许变成假绿）")
    #: ⛔ 夹具契约在超版本时**一被执行就会红**（打印 FAIL 且 rc=1）。
    #:    这样"闸坏了"立刻可见 —— 若它被跳过，报告里必须是 skip 而不是 pass，
    #:    也不能是 fail（exit 1 会伪装成"契约本身有缺陷"）。
    _OVER = (
        'MIN_PYTHON = (99, 0)\n'
        'SUPPORTED_MODES = ("default",)\n'
        'import sys\n'
        'print("  FAIL 这份契约根本不该被执行（MIN_PYTHON 闸失效了）")\n'
        'sys.exit(1)\n')
    _PLAIN = (
        'SUPPORTED_MODES = ("default",)\n'
        'import sys\n'
        'print("  PASS 正常的契约")\n'
        'sys.exit(0)\n')
    with tempfile.TemporaryDirectory() as _td2:
        _root = Path(_td2)
        (_root / "tests").mkdir()
        (_root / "tests" / "contract_overpy_test.py").write_text(
            _OVER, encoding="utf-8")
        (_root / "tests" / "contract_plain_test.py").write_text(
            _PLAIN, encoding="utf-8")

        _h = C.run_checks(_root, timeout_s=60.0, cross_lang=False)
        _by = {r["script"]: r for r in _h["runs"]}
        _o = _by.get("contract_overpy_test.py", {})
        _p = _by.get("contract_plain_test.py", {})
        ck("H1 超过声明版本的契约记 SKIP（不记 fail —— 版本老不是代码缺陷）",
           _o.get("status") == C.SKIP, f"status={_o.get('status')}")
        ck("H2 而且**根本没执行**（rc 为 None）；跑了就会是 rc=1 的假红",
           _o.get("rc") is None, f"rc={_o.get('rc')!r}")
        ck("H3 SKIP 的原因必须点名版本差（不能只留一个数字）",
           "MIN_PYTHON" in str(_o.get("reason", "")),
           str(_o.get("reason", ""))[:120])
        ck("H4 未声明版本的契约照常跑（闸不许误伤）",
           _p.get("status") == C.PASS, f"status={_p.get('status')}")
        ck("H5 部分跳过不拦整轮（skip 不算通过，但也不该让命令红）",
           _h["ok"] is True and _h["exit_code"] == 0, str(_h["counts"]))
        ck("H6 摘要里必须印出被跳过的那份与原因（不许只有 skip=1）",
           "⏭" in C.summarize(_h) and "contract_overpy_test.py"
           in C.summarize(_h), C.summarize(_h)[:160])

    #: ⛔⛔ 全跳过 = 一个断言都没执行。它与"0 份契约"是**同一类假绿**，
    #:     必须同样 fail-closed；否则某个比所有下限都老的解释器上会看到
    #:     `✅ 全部通过` 而实际上什么都没验。
    with tempfile.TemporaryDirectory() as _td3:
        _root = Path(_td3)
        (_root / "tests").mkdir()
        (_root / "tests" / "contract_overpy_test.py").write_text(
            _OVER, encoding="utf-8")
        _h2 = C.run_checks(_root, timeout_s=60.0, cross_lang=False)
        ck("H7 ⛔ 全部契约都被跳过 ⇒ 必须 fail-closed（假绿的另一扇门）",
           _h2["ok"] is False and _h2["exit_code"] == 1
           and _h2.get("all_skipped") is True,
           f"ok={_h2['ok']} rc={_h2['exit_code']} "
           f"all_skipped={_h2.get('all_skipped')}")
        ck("H8 而且摘要的点名是「全部被跳过」，不是「全部通过」",
           "全部契约都被跳过" in C.summarize(_h2), C.summarize(_h2)[:120])

    #: ⛔ 反证：把同一份夹具的 MIN_PYTHON 去掉 ⇒ 它必须被真执行并红。
    #:    否则 H1 只是"扫描器对任何契约都记 skip"，等于恒真。
    with tempfile.TemporaryDirectory() as _td4:
        _root = Path(_td4)
        (_root / "tests").mkdir()
        (_root / "tests" / "contract_overpy_test.py").write_text(
            _OVER.replace('MIN_PYTHON = (99, 0)\n', ''), encoding="utf-8")
        _h3 = C.run_checks(_root, timeout_s=60.0, cross_lang=False)
        _o3 = {r["script"]: r for r in _h3["runs"]}.get(
            "contract_overpy_test.py", {})
        ck("H9 ⛔⛔ 反证：去掉 MIN_PYTHON 后它**真被执行**并红（H1 因此不是恒真）",
           _o3.get("status") == C.FAIL and _o3.get("rc") == 1,
           f"status={_o3.get('status')} rc={_o3.get('rc')!r}")

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
