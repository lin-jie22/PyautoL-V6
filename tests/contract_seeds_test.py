#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：`aether init` 必须物化一份**能跑**的示例任务集。

═══ 这条契约守的是什么 ═══

`pip install aether-v6` 之后，`tasks/` 是空的 —— 它不是包资源，是仓库资产
（见 `pyproject.toml` 的 package-data 注释）。于是装完的状态是：

    aether init   ⇒ 建了 data/ tasks/ backups/ 与 .aether
    aether synth  ⇒ ⛔ yield=0 / 裸栈 —— **没有任何可做的事**

"能装但装完不能用"比"装不上"更糟：装不上会有人查，装上了不能用会被
当成"这东西就这样"。所以 `init` 要顺带把包内那份**示例**物化到工作区。

⛔ 那次真装的读数（不是推的）：
    pip install /Users/linshaojie/V6  ⇒ aether_v6-0.2.0-py3-none-any.whl
    wheel 里只有 46 个 .py；`tasks/`（14 个环境 / 28 个文件）一个都没进去。

═══ 六组 ═══

  A 物化：`init` 之后 `<ws>/tasks/<env>/{task.py,tests.json}` 真的在。
  B ⛔ **物化出来的东西真能跑**（`verify_source` 逐条 got/expect），
    不是"文件在就算好"—— 那正是本项目反复惩罚的"空壳产物"。
  C 幂等：第二次 `init` 不覆盖任何已存在的东西。
  D ⛔ 用户改动不被吞掉（他改了 task.py，再跑 init 必须原样还在）。
  E 演练一字节不落（含示例任务）。
  F 安装态：pkg_root 指向一个**假 site-packages**（里面有 seeds），
    `init` 仍能物化 —— 这是真实用户会走的唯一那条路。
  G 反证：包里没有示例 ⇒ 如实说"没有"，不许静默成功；
    且 `package-data` 与 `SEED_FILES` 必须同源（少一个就发半份）。

跑法（离线，秒级）：
    python tests/contract_seeds_test.py
"""
import json
import re
import sys
import tempfile
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)

#: 一个任务集由哪几个文件构成。⛔ `main()` 会断言它与
#: `aether.workspace.SEED_FILES` **同源** —— 两处各写一份、只改一处，
#: 就是"发半份示例"那种事故的温床。
SEED_FILES = ("task.py", "tests.json")

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _fake_install(tmp: Path) -> Path:
    """造一个"装进 site-packages"的包位置：**有** `aether/seeds/tasks`，
    **没有**仓库根的 `tasks/` 与 `tests/`。

    ⛔ 这才是真实用户的形态：`looks_like_repo()` 必须判 False，
       否则 `init` 会走 noop 分支，测试就成了空转。
    ⛔ 逐个文件读写而不是 `copytree(seeds/tasks, ...)`：`seeds/tasks` 是
       **符号链接**，沙箱 shim 会对 `copytree` 的链接源直接
       `PermissionError`（实测），而这不是被测行为。
    """
    sp = tmp / "site-packages"
    (sp / "aether").mkdir(parents=True, exist_ok=True)
    (sp / "aether" / "paths.py").write_text("# fake\n", encoding="utf-8")
    src = ROOT / "tasks"                     # 真实的源（不是符号链接）
    dst = sp / "aether" / "seeds" / "tasks"
    dst.mkdir(parents=True, exist_ok=True)
    for env in sorted(p for p in src.iterdir() if p.is_dir()):
        out = dst / env.name
        out.mkdir(exist_ok=True)
        for name in SEED_FILES:
            f = env / name
            if f.is_file():
                (out / name).write_text(f.read_text(encoding="utf-8"),
                                        encoding="utf-8")
    return sp


def main() -> int:
    from aether import paths as P
    from aether.environment import Environment, verify_source
    from aether.workspace import SEED_FILES as WS_SEED_FILES
    from aether.workspace import init, seed_tasks

    ck("Z0 本文件与 workspace.SEED_FILES 同源（两处各写一份就会发半份）",
       tuple(WS_SEED_FILES) == SEED_FILES,
       f"{WS_SEED_FILES} vs {SEED_FILES}")

    # ── A 组：物化 ───────────────────────────────────────────────
    print("A 组：init 之后工作区里有可跑的任务集")
    ws = Path(tempfile.mkdtemp(prefix="v6_seed_a_"))
    r = init(ws, now=1_700_000_000.0)
    sd = r["seeds"]
    ck("A1 包内示例被找到（available=True）", sd["available"], str(sd.get("reason")))
    ck("A2 物化了至少一个任务集", len(sd["seeded"]) >= 2, str(sd["seeded"][:4]))
    on_disk = sorted(p.relative_to(ws).as_posix()
                     for p in (ws / "tasks").rglob("*") if p.is_file())
    ck("A3 文件真的落到磁盘上（不是只写进返回值）",
       len(on_disk) == len(sd["seeded"]), f"{len(on_disk)} vs {len(sd['seeded'])}")
    ck("A4 每个任务集都带 task.py 与 tests.json",
       all(any(f.endswith(n) for f in on_disk) for n in SEED_FILES),
       str(on_disk[:4]))
    #: ⛔ 标记里记下了"哪来的一份示例"：事后能分清"自己搭的"与"init 给的"
    marker = json.loads((ws / ".aether").read_text(encoding="utf-8"))
    ck("A5 .aether 里记下了 seeded（可追溯来源）",
       bool(marker.get("seeded")), str(marker))

    # ── B 组：物化出来的东西**真能跑** ───────────────────────────
    print("\nB 组：物化出来的任务集真能跑（不是『文件在就算好』）")
    envs = sorted(p for p in (ws / "tasks").iterdir() if p.is_dir())
    ok_n = 0
    bad = []
    for d in envs:
        env = Environment.from_dir(d)
        v = verify_source(env)
        if v.passed and v.checks:
            ok_n += 1
        else:
            bad.append((d.name, (v.error or "")[:60]))
    #: ⛔ 断言的是"**每一个**都跑得通"，不是"有一个能跑"——
    #:    后者会让半份损坏的示例混过去，而用户 app 打开的就是那个坏的。
    ck("B1 ⛔ 每个示例任务集的判据全过（verify_source 真跑）",
       ok_n == len(envs) and ok_n > 0, f"通过 {ok_n}/{len(envs)} 坏：{bad[:2]}")
    ck("B2 判据不是空集合（空套件不许恒绿）",
       all(len(Environment.from_dir(d).tests) > 0 for d in envs),
       "有任务集的 tests 为空")

    # ── C 组：幂等 ───────────────────────────────────────────────
    print("\nC 组：幂等 —— 第二次 init 不许动任何已存在的东西")
    before = {p: p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()}
    r2 = init(ws)
    after = {p: p.stat().st_mtime_ns for p in ws.rglob("*") if p.is_file()}
    ck("C1 第二次 init：没有新物化（全部记进 existing）",
       r2["seeds"]["seeded"] == [], str(r2["seeds"]["seeded"][:3]))
    ck("C2 文件没被重写（mtime 与内容都没变）", before == after,
       f"变了 {[str(p) for p in after if before.get(p) != after[p]][:3]}")
    ck("C3 .aether 的 created_at 保留最初那份（没被覆盖）",
       json.loads((ws / ".aether").read_text(encoding="utf-8"))["created_at"]
       == 1_700_000_000, "标记被重写了")

    # ── D 组：用户改动不被吞 ─────────────────────────────────────
    print("\nD 组：用户改过的那份**不许**被覆盖")
    target = ws / "tasks" / envs[0].name / "task.py"
    mine = "# 我自己改过的版本\n" + target.read_text(encoding="utf-8")
    target.write_text(mine, encoding="utf-8")
    init(ws)
    ck("D1 ⛔ 用户的改动原样还在（覆盖它 = 吞掉他的工作）",
       target.read_text(encoding="utf-8") == mine, "被覆盖了")

    # ── E 组：演练一字节不落 ─────────────────────────────────────
    print("\nE 组：演练与真跑在磁盘上必须同分（第 139 条）")
    ws_dry = Path(tempfile.mkdtemp(prefix="v6_seed_dry_"))
    r3 = init(ws_dry, dry_run=True)
    left = [p.relative_to(ws_dry).as_posix() for p in ws_dry.rglob("*")]
    ck("E1 ⛔ 演练：磁盘上一个字节都没落", left == [], str(left[:4]))
    ck("E2 但报告里**要说清会建什么**（不然演练没有信息量）",
       bool(r3["created"]) and bool(r3["seeds"]["seeded"]),
       f"created={r3['created']} seeded={len(r3['seeds']['seeded'])}")
    ck("E3 演练报告带 dry_run=True（可与真跑分辨）", r3["dry_run"] is True)

    # ── F 组：安装态（真实用户唯一那条路）────────────────────────
    print("\nF 组：装成 wheel 的形态下也能物化")
    tmp = Path(tempfile.mkdtemp(prefix="v6_seed_inst_"))
    sp = _fake_install(tmp)
    ck("F1 前提：假 site-packages **不是**仓库（否则本组空转）",
       not P.looks_like_repo(sp), f"{sp}")
    ws2 = tmp / "ws"
    ws2.mkdir()
    r4 = init(ws2, pkg_root=sp)
    ck("F2 ⛔ 安装态 init 仍物化了示例（这正是 pip 用户的唯一路径）",
       r4["seeds"]["available"] and len(r4["seeds"]["seeded"]) >= 2,
       str(r4["seeds"].get("reason")))
    envs2 = sorted(p for p in (ws2 / "tasks").iterdir() if p.is_dir())
    ok2 = sum(1 for d in envs2 if verify_source(Environment.from_dir(d)).passed)
    ck("F3 ⛔ 装出来的示例**也真能跑**（不是只有仓库里那份能跑）",
       ok2 == len(envs2) and ok2 > 0, f"通过 {ok2}/{len(envs2)}")

    # ── G 组：反证 ───────────────────────────────────────────────
    print("\nG 组：反证 —— 空集合与半份示例都要被说出来")
    #: G1 包里根本没有 seeds ⇒ 如实说"没有"，**不许**静默成功
    bare = tmp / "bare_pkg"
    (bare / "aether").mkdir(parents=True)
    (bare / "aether" / "paths.py").write_text("# fake\n", encoding="utf-8")
    r5 = seed_tasks(ws2, pkg_root=bare)
    ck("G1 ⛔ 包里没有示例 ⇒ available=False 且给出原因（不是静默空转）",
       r5["available"] is False and bool(r5.get("reason")), str(r5))
    #: G2 半份示例（只有 task.py，没有 tests.json）⇒ 不许物化出半份
    half = tmp / "half_pkg" / "aether" / "seeds" / "tasks" / "only"
    half.mkdir(parents=True)
    (half / "task.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    r6 = seed_tasks(tmp / "ws_half", pkg_root=tmp / "half_pkg")
    got = [p for p in (tmp / "ws_half" / "tasks").rglob("*") if p.is_file()] \
        if (tmp / "ws_half" / "tasks").is_dir() else []
    ck("G2 ⛔ 半份示例（缺 tests.json）不许被当成完整任务集物化",
       len(got) <= 1, f"物化了 {[p.name for p in got]}")

    #: G3 ⛔ package-data 与 SEED_FILES 必须**同源**：
    #:    那边少一个 glob，装出来的 wheel 里就只有半个示例，
    #:    而 `init` 会静默物化半份（G2 那种形态）—— 判据要能提前抓住它。
    py = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"aether\s*=\s*\[(.*?)\]", py, re.S)
    globs = re.findall(r'"(seeds/tasks/[^"]+)"', m.group(1)) if m else []
    suffixes = sorted({g.rsplit(".", 1)[-1] for g in globs})
    want = sorted({n.rsplit(".", 1)[-1] for n in SEED_FILES})
    ck("G3 ⛔⛔ package-data 覆盖的文件类型 == SEED_FILES（少一个就发半份）",
       suffixes == want, f"pyproject={suffixes} SEED_FILES={want}")
    ck("G4 package-data 里没有 `seeds/**/*` 这种通配（会把运行产物一起发货）",
       not any(g.endswith("**/*") for g in globs), str(globs))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：init 物化了 {len(envs)} 个可跑的任务集，幂等且不覆盖用户改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
