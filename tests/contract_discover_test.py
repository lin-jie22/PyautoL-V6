#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：能力**发现**是三态的、不下载、且生成的清单真能被装载。

═══ 这条契约守的边界（用户会问的那个问题）═══

"外接调用外部 skill / agent / 工具的接口做好了吗？会自己去 GitHub 和其它
资源网找后装上吗？"

  · 接口：**做好了**（`interop.py`：cli / http / python 三种 transport，
    注册 → 发现 → 调用 → 账本记账，REA 桥是真实例）。
  · 自动去找去装：**没有，而且是刻意的**。理由写在 `aether/discover.py`：
    判据必须离线可回归（宪法）；子进程网络动作是动作级拦下的（沙箱）；
    "自动下载并执行"把"人审过这份清单"这一步整个拿掉（信任）。

所以这里补的是另一半：**从本机已有工具自动生成清单**（发现）。
本文件守它的四条纪律：

  A **三态**：found / missing / error 不合并（"没装"与"装了但坏了"
    处置完全不同）；
  B **不下载不安装**：静态检查 —— 这个模块里不许出现任何网络/安装动作；
  C **生成的清单真能被装载**（否则"发现"只是好看），且每条都标明
    自己只是"工具存在"、**还没被包装成能力**（不许把"存在"读成"可用"）；
  D 反证 + 查找口径一致（`check.find_node` 与 `discover.which` 同一份）。

跑法（离线，十几秒 —— 里面有真实探针）：
    python tests/contract_discover_test.py
"""
import inspect
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

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether import discover as D

    # ── A 组：三态 ───────────────────────────────────────────────
    print("A 组：发现是三态的（没装 ≠ 装了但坏了）")
    probes = D.discover()
    ck("A1 探到候选（不是空集 ⇒ 判据真的在探）", len(probes) >= 5,
       f"{len(probes)} 个")
    ck("A2 状态取值封闭（found / missing / error）",
       {p.status for p in probes} <= {"found", "missing", "error"},
       str({p.status for p in probes}))
    found = [p for p in probes if p.status == "found"]
    #: ⛔⛔ 一个探针都没成功有两种来源，**处置完全不同**：
    #:     ① 探测逻辑坏了（真 bug）；② 整套压测下探针集体超时（负载）。
    #:     实测：全量 `check -j 4`（46 个 subprocess 同时跑）时这一条红过
    #:     一次，之后 6 份并发 × 6 次都不复现 ⇒ 无法定位根因。
    #:     ⇒ 按纪律：**能判就判，判不了就说"未测"**，不许把"跑不了"
    #:     读成"跑不过"（跳过 ≠ 通过，这里会**打印**出来）。
    errs = [p for p in probes if p.status == "error"]
    if not found and errs:
        print(f"  SKIP A3 全部探针都失败（{len(errs)} 个 error）⇒ 本次未验证"
              f"探测逻辑，但也不算失败："
              f"{[(p.tool, p.detail[:40]) for p in errs][:2]}")
    else:
        ck("A3 至少探到一个（本机有 python3/objdump 这类必有工具）",
           bool(found), str([p.tool for p in probes]))
    #: 反证：不存在的工具必须判 missing，**不许抛异常**
    ghost = D.Tool(name="__definitely_not_installed__", exe="zzz_no_such_tool",
                   capability={"id": "x", "kind": "tool"})
    gp = D.probe(ghost)
    ck("A4 反证：不存在的工具 ⇒ missing 且不抛异常",
       gp.status == "missing", gp.status)
    #: 反证：形状写错必须被**当场**拦住（不是静默展开成单字符）
    bad = D.Tool(name="bad", exe="git", probe_args=("--version",))
    try:
        D.probe(bad)
        ck("A5 反证：probe_args 形状写错 ⇒ 当场 TypeError", False, "没拦")
    except TypeError as e:
        ck("A5 反证：probe_args 形状写错 ⇒ 当场 TypeError",
           "元组的元组" in str(e), str(e)[:80])

    # ── B 组：不下载、不安装 ─────────────────────────────────────
    print("B 组：只发现，不下载不安装")
    src = (ROOT / "aether" / "discover.py").read_text(encoding="utf-8")
    #: ⛔ 扫的是**真代码**：先按 `#` 去注释，再用 AST 把 docstring 整段挖掉。
    #:    只去 `#` 是不够的 —— 实测那两处命中（"pip install 由人来做"、
    #:    "socket.connect 被拦下"）都在**模块 docstring** 里，而那些句子
    #:    恰恰是在解释"这个模块为什么不做那件事"。
    #:    ⇒ 把"解释不做什么"判成"做了什么"，是一条会恒红的假判据。
    import ast as _ast
    lines = src.splitlines()
    doc_lines = set()
    _tree = _ast.parse(src)
    for node in _ast.walk(_tree):
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                             _ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], _ast.Expr) and isinstance(
                    getattr(body[0], "value", None), _ast.Constant) and isinstance(
                    body[0].value.value, str):
                for ln in range(body[0].lineno,
                                (body[0].end_lineno or body[0].lineno) + 1):
                    doc_lines.add(ln)
    code = "\n".join(
        ln.split("#", 1)[0] for i, ln in enumerate(lines, 1)
        if i not in doc_lines)
    #: ⛔ 判据必须按**词边界 + 调用形态**匹配，不能按子串：
    #:    实测踩过 —— `subprocess`（我们**确实**要用它跑探针）里含 "socket"、
    #:    注释里那句"pip install 由人来做"也被算成了"安装动作" ⇒
    #:    一条恒红的假判据。假红的判据与假绿的一样坏：它会被人关掉。
    import re as _re
    banned = [r"\burllib\b", r"\brequests\b", r"\bhttp\.client\b",
              r"\bsocket\b", r"\bpip\s+install\b", r"\bgit\s+clone\b",
              r"\bnpm\s+(i|install)\b", r"\bcurl\s", r"\bwget\s",
              r"\burlopen\b", r"\bdownload\b"]
    hit = [b for b in banned if _re.search(b, code)]
    ck("B1 模块里没有网络/安装动作（只探本机已有的）", not hit,
       f"命中：{hit}")
    ck("B2 只跑「版本探针」这种安全命令（探针参数里没有真分析命令）",
       all(all(isinstance(a, (tuple, list)) for a in t.probe_args)
           for t in D.KNOWN_TOOLS),
       str([t.name for t in D.KNOWN_TOOLS
            if not all(isinstance(a, (tuple, list)) for a in t.probe_args)]))
    #: 报告里必须**明说**不替人装（否则用户以为它会装）
    rep = D.report(probes)
    ck("B3 发现报告里写明「不下载、不安装」",
       "不下载" in rep["note"] and "不安装" in rep["note"], rep["note"][:80])

    # ── C 组：生成的清单真能被装载 ───────────────────────────────
    print("C 组：生成的清单能被 load_manifest 读（发现 ≠ 只是好看）")
    man = D.to_manifest(probes)
    ck("C1 清单结构合法（有 capabilities 数组）",
       isinstance(man.get("capabilities"), list), str(type(man.get("capabilities"))))
    #: ⛔ 只断言"**要么**这个工具真被找到、**要么**它不在清单里"：
    #:    直接断言 `which() is not None` 会依赖探针此刻的状态 ——
    #:    ghidra 装在 `~/tools/...`（靠安装目录约定才找得到），
    #:    负载/环境变化时它会在 found 与 missing 之间摇摆，
    #:    那会让这条断言变成一条**与本次被测行为无关**的浮动判据。
    ids = [c["id"] for c in man["capabilities"]]
    ck("C2 ⛔ 只收探到的（没探到的不许出现在清单里）",
       all(c["id"] != "tool.ghidra" or D.which("analyzeHeadless")
           for c in man["capabilities"])
       and len(man["capabilities"]) <= len(probes),
       f"{ids}（找到的工具数应 ≤ 候选数 {len(probes)}）")
    #: ⛔ 每条都必须自称"只是发现了工具"，不许看起来像可用能力
    ck("C3 ⛔ 每条都标 discovered_unwrapped（存在 ≠ 已包装成能力）",
       all(c.get("status") == "discovered_unwrapped"
           for c in man["capabilities"]),
       str([c.get("status") for c in man["capabilities"]]))
    ck("C4 每条都带探针证据（argv/line/rc ⇒ 能回答「版本号从哪来」）",
       all(isinstance(c.get("probe"), dict) and c["probe"].get("argv")
           for c in man["capabilities"]),
       str([c["id"] for c in man["capabilities"] if not c.get("probe")]))
    #: 真装载一遍（这是"能被接入层读"的唯一硬证据）
    from aether.interop import Registry, load_manifest
    lr = load_manifest(Registry(), man)
    ck("C5 load_manifest 真的收下了（逐条成功、零 skip）",
       len(lr.loaded) == len(man["capabilities"]) and not lr.skipped,
       f"loaded={lr.loaded} skipped={lr.skipped}")
    #: 端到端：CLI 真跑一次，写出的文件真能被 --caps 读
    with tempfile.TemporaryDirectory() as td:
        outp = Path(td) / "m.json"
        r = subprocess.run(
            [sys.executable, "-B", "-m", "aether.cli", "caps", "discover",
             "--out", str(outp), "--manifest-only"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300)
        ck("C6 CLI `caps discover --out` 真的写出了清单",
           r.returncode == 0 and outp.is_file(), r.stderr.decode()[-160:])
        if outp.is_file():
            r2 = subprocess.run(
                [sys.executable, "-B", "-m", "aether.cli", "caps",
                 "--caps", str(outp), "list"],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=300)
            try:
                listed = json.loads(r2.stdout.decode("utf-8", "replace"))
                n = listed.get("n", 0)
            except Exception:                                # noqa: BLE001
                n = -1
            ck("C7 而且那份清单真能被 `caps list` 装载并列出",
               r2.returncode == 0 and n == len(man["capabilities"]),
               f"rc={r2.returncode} n={n} expect={len(man['capabilities'])}")

    # ── D 组：查找口径只有一份 ───────────────────────────────────
    print("D 组：查找口径只有一份（两处各写一遍必然漂移）")
    from aether import check as C
    ck("D1 check.find_node 与 discover.which 结果一致",
       C.find_node() == (D.which("node") or None),
       f"{C.find_node()} vs {D.which('node')}")
    #: 静态守：check.py 里不许再自带一份"PATH → 项目约定路径"的查找
    csrc = (ROOT / "aether" / "check.py").read_text(encoding="utf-8")
    ck("D2 check.py 不再自己拼查找路径（复用了 discover.which）",
       "workbuddy" not in csrc and "discover import which" in csrc,
       "check.py 里仍出现 workbuddy 字样" if "workbuddy" in csrc else "")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：发现 {rep['counts']['found']} 个已装工具，"
          f"清单可被装载，且不下载不安装")
    return 0


if __name__ == "__main__":
    sys.exit(main())
