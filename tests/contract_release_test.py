#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：版本号、CHANGELOG、git tag 三者必须**对得上**。

═══ 缺口（实测于 2026-10-15）═══

  · `aether/__init__.py` 与 `serve.py` 各写一份版本号（实测漂移过：
    包报 0.1.0、端点报 0.2.0）—— 已收进 `_version.py`；
  · **`git tag` 为空**：版本号与提交历史**没有任何锚定关系**，
    仓库用的是自造的"r33/r34…"轮次叙事，而 `0.2.0` 不指向任何提交；
  · 没有 CHANGELOG。

一个"版本号"如果既不对应某个提交、也没有变更记录，那它就只是**注释**。
本文件把三者接起来（能离线判的那部分）：

  A 版本号唯一源（`_version.py`）与包、CLI、CHANGELOG 一致；
  B CHANGELOG 里必须有一节标题带当前版本号（否则"发布"没留痕）；
  C **反证**：判据必须会响（故意用错版本号必须被抓）；
  D git 可用时：检查是否存在指向当前版本号的 tag，并如实报告
    "没有 tag"（**不**因此判失败 —— tag 是发布动作，不是代码正确性）。

跑法（离线，秒级）：
    python tests/contract_release_test.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _changelog_versions(text: str):
    """CHANGELOG 里的版本小节标题（`## 0.2.0 — 日期` 这类）。"""
    return re.findall(r"^##\s+([0-9][0-9A-Za-z.\-]*)\s", text, re.MULTILINE)


def main() -> int:
    from aether import _version as V
    import aether

    # ── A 组：版本号唯一源 ───────────────────────────────────────
    print("A 组：版本号只有一个源，且各处一致")
    ck("A1 包版本 == _version.CODE_VERSION",
       aether.__version__ == V.CODE_VERSION,
       f"{aether.__version__} vs {V.CODE_VERSION}")
    #: 静态检查：除 _version.py 外源码里不许出现版本号字面量
    lit = []
    for p in sorted((ROOT / "aether").glob("*.py")):
        if p.name == "_version.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if V.CODE_VERSION in line.split("#", 1)[0]:
                lit.append(f"{p.name}:{i}")
    ck("A2 源码里没有第二份版本号字面量", not lit, str(lit))
    #: pyproject 也不许写第二份（必须 dynamic）
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ck("A3 pyproject 的版本走 dynamic 指向 _version",
       data["project"].get("version") is None
       and "version" in (data["project"].get("dynamic") or [])
       and data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
       == "aether._version.CODE_VERSION",
       str(data["project"].get("version")))

    # ── B 组：CHANGELOG 留痕 ─────────────────────────────────────
    print("B 组：变更记录与版本号对得上")
    cl = ROOT / "CHANGELOG.md"
    ck("B1 CHANGELOG.md 存在", cl.is_file())
    versions = _changelog_versions(cl.read_text(encoding="utf-8"))
    ck(f"B2 CHANGELOG 里有一节是当前版本 {V.CODE_VERSION}",
       V.CODE_VERSION in versions, f"小节：{versions}")
    ck("B3 CHANGELOG 里没有比当前代码更新的版本（防'发布了但代码没跟上'）",
       all(v <= V.CODE_VERSION for v in versions) if versions else True,
       f"{versions} vs {V.CODE_VERSION}")
    #: 如实记录"没做/没验"是小节而非流水账 —— 这一节必须存在
    ck("B4 CHANGELOG 里有'已知边界'一节（没做/没验的事必须写明）",
       "已知边界" in cl.read_text(encoding="utf-8"))

    # ── C 组：反证 ───────────────────────────────────────────────
    print("C 组：反证（判据必须会响）")
    fake = "# 变更记录\n\n## 9.9.9 — 2026-01-01\n\n## 0.0.1 — 2026-01-01\n"
    got = _changelog_versions(fake)
    ck("C1 反证：能解析出小节标题（判据不是恒真）",
       got == ["9.9.9", "0.0.1"], str(got))
    ck("C2 反证：'有比当前更新的版本'会被判出来",
       any(v > V.CODE_VERSION for v in got),
       f"{got} vs {V.CODE_VERSION}")

    # ── D 组：git tag（能判就判，不能判就说清）────────────────────
    print("D 组：版本与提交历史的锚定")
    try:
        tags = subprocess.run(["git", "tag", "--list"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=30)
        have_git = tags.returncode == 0
    except (OSError, subprocess.SubprocessError):
        have_git = False
        tags = None
    if not have_git:
        ck("D1 git 不可用 ⇒ 跳过 tag 检查（不假装通过）",
           True, "（跳过：没有 git，非失败）")
    else:
        names = [t.strip() for t in tags.stdout.splitlines() if t.strip()]
        hit = [t for t in names if V.CODE_VERSION in t]
        #: ⛔ **判失败而不是只警告**（2026-10-15 改）：一条"只报告不拦"的
        #:    检查，在这个仓库里已经被证明会长期是红的而无人理会
        #:    （`git tag` 一直为空，版本号与提交历史没有任何锚定关系 ——
        #:    "0.2.0 到底是哪一版代码"没有答案）。
        #:    ⛔ 这是**刻意**把"发布动作"也纳进判据：一个自进化系统的
        #:    版本号如果不能锚定到某个提交，"跨版本比读数"就永远说不清。
        ck("D1 存在指向当前版本号的 tag（版本号必须能锚定到提交）",
           bool(hit),
           f"现有 tag：{names[:5] or '（无）'} ⇒ 版本号 {V.CODE_VERSION} "
           f"不指向任何提交；发布时请 `git tag v{V.CODE_VERSION}`")
    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：版本 {V.CODE_VERSION} 在包/CLI/CHANGELOG 三处一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
