#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：控制台**渲染**——面板得真的画出来，而不是"写了就当有了"。

═══ 这条契约是被一次实测事故逼出来的 ═══

2026-10-14 用页面自己的脚本 + 真实 `/api/status` 数据跑了一遍，结果是：

    ReferenceError: Cannot access 'evs' before initialization

原因：`const evs = data.events ?? []` 声明在「系统状态」面板**之后**，而
状态面板的 IIFE 里引用了它 ⇒ TDZ。`load()` 是 async ⇒ 抛错变成
**unhandled rejection** ⇒ 浏览器控制台之外**毫无迹象**：卡片标题是静态
HTML、占位符是 "…"，页面看起来完全正常，而 9 个数据面板**从未渲染过**。

⇒ 后面几轮新加的面板（泛化闸、记忆溯源、沙箱 tier）写了、提交了、
   **从来没有人看见过**。

本文件守三条硬约束：

    A 静态闸      TDZ 顺序 / 中断不静默 / id 拼错 / 锚点 / XSS / 详略三档
    B 运行时取证  用**页面自己的脚本**跑一遍，15 个面板逐个断言"写到了内容"
    C 反证组      注入事故版 ⇒ A/B 的判据必须**转红**（不转红就是恒真闸）

⛔ 为什么静态闸不够、非要有 B 组：静态闸只能查"代码长什么样"，查不出
   "这段渲染代码跑不跑得到"。TDZ 那次，静态看每一行都对。
⛔ 为什么 B 组必须用页面自己的脚本：另写一份简化渲染逻辑 ⇒ 页面改了探针
   没改 ⇒ 闸恒绿。探针跑的是从 `console/index.html` **原样抽出**的脚本。
⛔ 为什么 B 组用合成数据而不是打生产服务：测试不得读写生产状态。

跑法：
    python tests/contract_console_render_test.py
    AETHER_CONSOLE_NODE=/path/to/node python tests/contract_console_render_test.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HTML = ROOT / "console" / "index.html"
PROBE = ROOT / "tests" / "_console_probe.mjs"

FAILS: list[str] = []
SKIPS: list[str] = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    return bool(cond)


def skip(name, why):
    """⛔ 跳过 ≠ 通过。必须单独记账并在末尾汇总里报出来。"""
    print(f"  SKIP {name} —— {why}")
    SKIPS.append(name)


# ── 脚本抽取 ─────────────────────────────────────────────────────
def script_text(html: str) -> str:
    m = re.search(r'<script type="module">(.*?)</script>', html, re.S)
    if not m:
        raise AssertionError("console/index.html 里找不到 <script type=module>")
    return m.group(1)


def ids_in_html(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


# ── 静态判据（供 A 组与 C 组复用：C 组对事故版跑同一份函数）─────────
def gate_tdz_order(script: str) -> tuple[bool, str]:
    """A1：`EVS` 的声明必须早于它被用到的地方（TDZ 闸）。

    ⛔ 只看"有没有声明"是不够的 —— 事故版里它**有**声明，只是晚了 80 行。
    """
    lines = script.splitlines()
    decl = next((i for i, s in enumerate(lines)
                 if re.search(r"const\s+EVS\s*=\s*data\.events", s)), None)
    use = next((i for i, s in enumerate(lines)
                if "EVS" in s and re.search(r"\[\.\.\.EVS\]", s)), None)
    if decl is None:
        return False, "找不到 EVS 声明"
    if use is None:
        return False, "找不到 EVS 引用（判据失效：脚本改了而闸没改）"
    return decl < use, f"声明在第 {decl + 1} 行，引用在第 {use + 1} 行"


def gate_interrupt_visible(script: str) -> tuple[bool, str]:
    """A2：渲染中断必须写进页面（unhandledrejection / window.onerror）。"""
    has_rj = "unhandledrejection" in script
    has_er = 'addEventListener("error"' in script
    return (has_rj and has_er), f"unhandledrejection={has_rj} window.error={has_er}"


def gate_ids_exist(html: str, script: str) -> tuple[bool, str]:
    """A3：脚本里 `el("x")` 的每个 id 都必须在 HTML 里存在。

    ⛔ 拼错一个 id ⇒ `getElementById` 返回 null ⇒ 后面 `.innerHTML =` 直接
       抛 TypeError ⇒ **整个 load() 断掉**，而页面看起来只是"那个面板空着"。
    """
    declared = ids_in_html(html)
    used = set(re.findall(r'el\("([^"]+)"\)', script))
    missing = sorted(used - declared)
    return (not missing), f"脚本引用了 HTML 里没有的 id: {missing}"


def gate_anchors(html: str) -> tuple[bool, str]:
    """A4：导航锚点的目标必须存在（点了没反应 = 假按钮的一种）。"""
    declared = ids_in_html(html)
    targets = set(re.findall(r'href="#([^"]+)"', html))
    missing = sorted(targets - declared)
    return (not missing), f"锚点指向不存在的 id: {missing}"


def gate_preview_no_html(script: str) -> tuple[bool, str]:
    """A5：文件预览正文必须 `textContent`，不许 `innerHTML`。

    ⛔ 为什么不当"整个文件不许 innerHTML"来查：状态码、徽章这些本来就要
       渲染 HTML。闸要卡的是**文件内容**这一处——它来自仓库，**不可信**。
    """
    m = re.search(r"async function previewFile\(p\)(.*?)\n}\n", script, re.S)
    if not m:
        return False, "找不到 previewFile 函数（判据失效：改名了而闸没改）"
    body = m.group(1)
    # ⛔ 闸要卡的是**接收正文的那个变量**，不是"函数里出现过 innerHTML"
    #    —— `meta.innerHTML` 是给元信息渲染徽章的，正当使用。按"出现过"
    #    来查会误伤，误伤一次就有人加白名单，白名单一开闸就废了。
    mv = re.search(r"(\w+)\.textContent\s*=\s*body\.content\s*;", body)
    if not mv:
        return False, "找不到正文落点（xxx.textContent = body.content）"
    var = mv.group(1)
    hit = re.search(rf"{var}\.innerHTML\s*=", body)
    if hit is None:
        return True, f"{var} 只走 textContent"
    line_no = body[:hit.start()].count("\n") + 1
    return False, f"{var}.innerHTML 赋值出现在 previewFile 第 {line_no} 行"


def gate_detail_levels(script: str, html: str) -> tuple[bool, str]:
    """A6：详略三档的开关与 CSS 隐藏规则都得在。"""
    levels = set(re.findall(r'data-level="([^"]+)"', html))
    ok_levels = levels == {"compact", "standard", "verbose"}
    css_ok = all(
        f'body[data-detail="{d}"]' in html for d in ("compact", "standard")
    ) and ".lv-verb" in html
    return (ok_levels and css_ok), f"levels={sorted(levels)} css={css_ok}"


# ── 合成数据（不打生产服务；形状覆盖真实 /api/status 的分支）─────────
def fixture() -> dict:
    evs = []
    seq = 100
    for i in range(4):                       # eval.verdict：喂 reward 图 + 沙箱读数
        seq += 1
        evs.append({"seq": seq, "kind": "eval.verdict", "ts": "2026-10-14T00:00:00",
                    "payload": {"task_id": f"t{i}", "split": "dev", "reward": 0.25 * i,
                                "harness_id": f"h{i}",
                                "evidence": {"sandbox": {"tier": "process",
                                                         "fs_isolated": False}}}})
    for i in range(3):                       # gate.result：喂非回归闸表
        seq += 1
        evs.append({"seq": seq, "kind": "gate.result", "ts": "2026-10-14T00:00:01",
                    "payload": {"capsule": f"c{i}", "passed": i != 1,
                                "n_tasks": 7, "regressions": [] if i != 1 else ["t1"],
                                "caliber_reason": "口径一致"}})
    # gen.probe：两条，其中一条是"无判别力却放行" ⇒ 触发 ⚠ 闸失效 悖论告警
    seq += 1
    evs.append({"seq": seq, "kind": "gen.probe", "ts": "2026-10-14T00:00:02",
                "payload": {"capsule": "c0", "dev_gain": 0.4, "heldout_gain": 0.3,
                            "verdict": "generalize", "discriminative": True}})
    seq += 1
    evs.append({"seq": seq, "kind": "gen.probe", "ts": "2026-10-14T00:00:02",
                "payload": {"capsule": "c1", "dev_gain": 0.2, "heldout_gain": 0.0,
                            "verdict": "generalize", "discriminative": False}})
    seq += 1
    evs.append({"seq": seq, "kind": "harness.commit", "ts": "2026-10-14T00:00:03",
                "payload": {"capsule": "c0"}})
    seq += 1
    evs.append({"seq": seq, "kind": "evolution.stopped", "ts": "2026-10-14T00:00:04",
                "payload": {"round_no": 3, "rounds_requested": 10,
                            "reason": "连续无事可做（正常收敛）"}})
    seq += 1
    evs.append({"seq": seq, "kind": "memory.write", "ts": "2026-10-14T00:00:05",
                "payload": {"note_id": "n1", "disposition": "written"}})
    seq += 1
    evs.append({"seq": seq, "kind": "memory.write", "ts": "2026-10-14T00:00:05",
                "payload": {"note_id": "n2", "disposition": "written"}})
    seq += 1
    evs.append({"seq": seq, "kind": "harness.init", "ts": "2026-10-14T00:00:06",
                "payload": {"capsule": "c0"}})
    seq += 1
    evs.append({"seq": seq, "kind": "attribution.layer", "ts": "2026-10-14T00:00:07",
                "payload": {"layer": "L2"}})

    return {
        "status": {
            "ledger": {"count": len(evs), "head": "c7ab2c730a0e14e4", "chain_ok": True,
                       "by_kind": {"eval.verdict": 4, "gate.result": 3, "gen.probe": 2,
                                   "harness.commit": 1, "harness.init": 1,
                                   "attribution.layer": 1, "attribution.unavailable": 2,
                                   "memory.write": 2, "evolution.stopped": 1}},
            "events": evs,
            "store": {"n_snapshots": 3, "n_runs": 4, "avg_reward": 0.375},
            # ⛔ ok=False ⇒ 必须触发"读数不可信"告警（B 组断言它会真的出现）
            "consistency": {"ok": False, "ledger_verdicts": 9, "projection_runs": 4,
                            "missing_count": 5, "drift_count": 0,
                            "snapshot_missing": ["s9"], "overwrite_count": 2},
            "runs": [],
            "snapshots": [
                {"harness_id": "h0", "parent_id": "", "accepted": True, "round_no": 1},
                {"harness_id": "h1", "parent_id": "h0", "accepted": True, "round_no": 2},
                {"harness_id": "h2", "parent_id": "h1", "accepted": False, "round_no": 3},
            ],
            "snapshots_total": 3, "snapshots_truncated": False,
            "memory": {"present": True, "total": 11, "ledger_write_events": 0,
                       "ledger_trace": "pre_ledger", "integrity_ok": True,
                       "untraceable_count": 0},
            "version": "0.1", "auth": False,
        },
        "files": {"root": "/tmp/repo", "path": ".", "n": 3, "truncated": False,
                  "skipped_dirs": [".git", ".venv", "node_modules", "__pycache__"],
                  "entries": [
                      {"name": "README.md", "kind": "file", "size": 42, "mtime": 1.0, "ext": ".md"},
                      {"name": "aether", "kind": "dir", "size": 0, "mtime": 1.0, "ext": ""},
                      {"name": "data", "kind": "dir", "size": 0, "mtime": 1.0, "ext": ""}]},
        # ⛔ 文件内容里**故意**带 HTML：探针若用 innerHTML 渲染，A5 就该转红
        "file": {"path": "README.md", "bytes": 42,
                 "sha256": "a" * 64,
                 "content": "# 标题\n<script>alert(1)</script>\n"},
    }


def find_node() -> str | None:
    for c in (os.environ.get("AETHER_CONSOLE_NODE"), shutil.which("node")):
        if c and Path(c).exists():
            return c
    #: ⛔ 兜底也**不写用户名**（r67）：受管目录是 `~/` 相对的通配。
    for pat in ("~/.workbuddy/binaries/node/versions/*/bin/node",
                "/usr/local/bin/node", "/opt/homebrew/bin/node"):
        for c in sorted(glob.glob(os.path.expanduser(pat))):
            if Path(c).exists():
                return c
    return None


def run_probe(script: str, fix: dict) -> dict | None:
    """跑一遍页面脚本，返回探针报告。node 不可用 ⇒ 返回 None（闸失效）。"""
    node = find_node()
    if node is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "script.mjs"
        fp = Path(td) / "fixture.json"
        sp.write_text(script, encoding="utf-8")
        fp.write_text(json.dumps(fix, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([node, str(PROBE), str(sp), str(fp)],
                           capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    if r.returncode != 0 or not r.stdout.strip():
        print(f"    （探针自身失败 rc={r.returncode}）{(r.stderr or '')[:400]}")
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as ex:                                   # noqa: BLE001
        print(f"    （探针输出不是 JSON：{ex}）{r.stdout[:300]}")
        return None


# ══════════════════════════════════════════════════════════════════
def group_a(html: str, script: str) -> None:
    print("A 组：静态闸（结构与 XSS）")
    ok, why = gate_tdz_order(script)
    ck("A1 EVS 声明早于引用（TDZ 闸）", ok, why)
    ok, why = gate_interrupt_visible(script)
    ck("A2 渲染中断写进页面（不静默）", ok, why)
    ok, why = gate_ids_exist(html, script)
    ck("A3 el(id) 全部存在于 HTML", ok, why)
    ok, why = gate_anchors(html)
    ck("A4 导航锚点目标存在", ok, why)
    ok, why = gate_preview_no_html(script)
    ck("A5 文件预览走 textContent（XSS 闸）", ok, why)
    ok, why = gate_detail_levels(script, html)
    ck("A6 详略三档开关与 CSS 齐全", ok, why)


PANELS = [
    ("stats", "系统状态"), ("alert-body", "读数告警"), ("events tbody", "事件流"),
    ("bars", "reward 图"), ("tree", "进化树"), ("gates tbody", "非回归闸"),
    ("gen tbody", "泛化闸"), ("kinds", "kind 分布"), ("ftree", "文件树"),
    ("pv-meta", "文件预览元"), ("pv-body", "文件预览正文"),
    ("ev-meta", "事件详情元"), ("ev-body", "事件详情正文"),
    ("df-meta", "差异元"), ("df-body", "差异正文"), ("render-stamp", "渲染时间戳"),
]


def group_b(script: str, fix: dict) -> None:
    print("B 组：运行时渲染取证（页面自己的脚本 + 合成数据）")
    rep = run_probe(script, fix)
    if rep is None:
        skip("B 组", "无 node 可用 ⇒ 渲染闸这次**没生效**（静态闸查不出跑不到）")
        return
    ck("B0 无未捕获异常", not rep["exceptions"], str(rep["exceptions"])[:300])
    written, texts = rep["written"], rep["texts"]
    bad = [name for pid, name in PANELS if not written.get(pid)]
    ck(f"B1 {len(PANELS)} 个面板全部写入内容", not bad, f"空的：{bad}")

    st = texts.get("stats", "")
    for key in ["账本判定 / 投影记录", "记忆 / 有账本证据", "执行沙箱", "演化状态",
                "投影一致性"]:
        ck(f"B2 状态含「{key}」", key in st)
    ck("B3 演化状态识别到提前终止", "已停 @r3/10" in st, st[:200])

    al = texts.get("alert-body", "")
    ck("B4 投影不一致进告警区", "投影与账本不一致" in al, al[:200])
    ck("B5 沙箱未隔离进告警区", "文件系统**未隔离**" in al, al[:200])
    ck("B6 泛化闸悖论进告警区", "无判别力却放行" in al, al[:200])

    n = lambda pid, pat: len(re.findall(pat, texts.get(pid, "")))     # noqa: E731
    # ⛔ 期望值从 fixture **算出来**，不写死数字：写死的话，改 fixture 就会
    #    红，而红的是"数字对不上"不是"渲染坏了" ⇒ 真事故会被这层噪声盖住。
    n_ev = len(fix["status"]["events"])
    n_ft = len(fix["files"]["entries"])
    ck(f"B7 事件流行数 = {n_ev}", n("events tbody", r"<tr") == n_ev,
       str(n("events tbody", r"<tr")))
    ck("B8 非回归闸 3 行", n("gates tbody", r"<tr") == 3, str(n("gates tbody", r"<tr")))
    ck("B9 泛化闸 2 行", n("gen tbody", r"<tr") == 2, str(n("gen tbody", r"<tr")))
    ck("B10 悖论徽章「⚠ 闸失效」出现", "⚠ 闸失效" in texts.get("gen tbody", ""))
    ck(f"B11 文件树 {n_ft} 条", n("ftree", r'class="fn') == n_ft,
       str(n("ftree", r'class="fn')))
    ck("B12 预览正文是原文（未转义/未执行）",
       "<script>alert(1)</script>" in texts.get("pv-body", ""),
       texts.get("pv-body", "")[:120])
    ck("B13 差异面板给出来源", "#" in texts.get("df-meta", "") and "差异" in texts.get("df-meta", ""),
       texts.get("df-meta", "")[:120])
    # ⛔ 正常版**不许**出现中断横幅：它是"渲染死了"的读数，恒不出现才对。
    #    少了这条，"横幅逻辑坏了"和"一切正常"长得一模一样。
    ck("B14 正常版无中断横幅", "渲染中断" not in texts.get("render-err", ""),
       texts.get("render-err", "")[:200])


def group_c(html: str, script: str, fix: dict) -> None:
    """C 组：反证。⛔ 每条判据都要用**事故版**证明它真的会转红。"""
    print("C 组：反证（注入事故版 ⇒ 判据必须转红）")

    # C1：把 EVS 挪到 stats 之后（复原 2026-10-14 事故）⇒ 运行时必须崩
    moved = re.sub(r"[ \t]*const EVS = data\.events \?\? \[\];\n", "", script, count=1)
    ck("C1a 事故版确实删掉了原声明", "const EVS = data.events ?? [];" not in moved)
    marker = "  // ── 告警：投影与账本对不上"
    ck("C1b 找得到插入点", marker in moved)
    if marker in moved:
        broken = moved.replace(marker,
                               "  const EVS = data.events ?? [];\n" + marker, 1)
        rep = run_probe(broken, fix)
        if rep is None:
            skip("C1", "无 node 可用 ⇒ 运行时反证没跑")
        else:
            # ⛔ 断言的是"事故**被看见**"，不是"事故被抛出"：新版用 try/catch
            #    把它抓进了中断横幅 ⇒ 未捕获异常本来就该是 0。判据跟着改：
            #    横幅必须出现 + stats 必须为空。两条一起才证明安全网真的生效。
            ck("C1c 事故版弹出中断横幅", "渲染中断" in rep["texts"].get("render-err", ""),
               "事故版没有弹横幅 ⇒ 安全网没接住 ⇒ B14 判据恒绿")
            ck("C1d 事故版 stats 面板为空", not rep["written"].get("stats"),
               "stats 竟然渲染出来了 ⇒ 探针没走真实路径")

    # C2：删掉 unhandledrejection 监听 ⇒ A2 转红
    ok, _ = gate_interrupt_visible(script.replace('"unhandledrejection"', '"x"'))
    ck("C2 去掉中断监听 ⇒ A2 转红", not ok)

    # C3：拼错一个 id ⇒ A3 转红
    ok, _ = gate_ids_exist(html, script.replace('el("stats")', 'el("stats_typo")'))
    ck("C3 id 拼错 ⇒ A3 转红", not ok)

    # C4：预览改用 innerHTML ⇒ A5 转红
    ok, _ = gate_preview_no_html(script.replace("out.textContent = body.content;",
                                                "out.innerHTML = body.content;"))
    ck("C4 预览改 innerHTML ⇒ A5 转红", not ok)

    # C5：抽掉详略 CSS ⇒ A6 转红
    ok, _ = gate_detail_levels(script, html.replace('body[data-detail="compact"]', 'body[x]'))
    ck("C5 详略 CSS 缺失 ⇒ A6 转红", not ok)


def main() -> int:
    print("契约：控制台渲染（面板必须真的画出来）")
    if not HTML.exists():
        ck("控制台文件存在", False, str(HTML))
        return 1
    html = HTML.read_text(encoding="utf-8")
    script = script_text(html)
    fix = fixture()

    group_a(html, script)
    print()
    group_b(script, fix)
    print()
    group_c(html, script, fix)

    print()
    if SKIPS:
        print(f"  ⚠ 跳过 {len(SKIPS)} 项（跳过 ≠ 通过）：{SKIPS}")
    print(f"  合计：{len(FAILS)} 失败 / {len(SKIPS)} 跳过")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
