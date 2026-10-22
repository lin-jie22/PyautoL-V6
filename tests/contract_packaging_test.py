#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：这个仓库是一个**可安装的包**，且"零第三方依赖"可被机器验证。

═══ 为什么这两件事要一起守 ═══

收口前：

  · `pyproject.toml` / `setup.py` / `MANIFEST.in` **全不存在** ⇒
    `pip install -e .` 不可能，包不是标准可分发单元；
  · `aether/__main__.py` **不存在** ⇒ `python -m aether` 报
    `No module named aether.__main__`（只有 `.cli` / `.serve` / `.ui` 能用）；
  · 版本号散在 4 处（其中 `serve.py` 是**字面量**，不引用 `__version__`）。

而"零第三方依赖"此前只是 README 里的一句**声明** —— 没有任何东西在守它。
本文件把它变成可执行判据：静态扫 `aether/` 的全部 import，凡是既不在
stdlib、也不在本包内的，一律算第三方依赖 ⇒ 转红。

⛔ 本契约**不**假装验证"wheel 装得上"：那需要构建后端（setuptools），
   而本机是离线的、没装它。判据只能到"元数据自洽 + 入口可解析"这一层，
   剩下的一层如实写在报告与 README 里。
"""
import ast
import json
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约，
#:    写死 ⇒ 契约永远 import 生产那份 ⇒ 删前删后跑的是同一份代码 ⇒
#:    对拍恒绿 ⇒ 那道闸是空的（实测：删掉 `_SLUG_RE` 定义后契约仍 rc=0）。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

#: ⛔⛔ 这份契约**顶层** `import tomllib`（3.11+），在 3.9 上整份
#:    ModuleNotFoundError ⇒ 那台机器上它是零覆盖。`aether check` 静态读这一行，
#:    低于该版本时记 SKIP 并把原因印进报告（不记 fail —— 3.9 上没有 tomllib
#:    不是代码缺陷）。
#:
#: ⛔ 覆盖缺口**如实写在这里**，不假装 3.9 上也验过：A 组（零第三方依赖）
#:    本来不需要 tomllib，但它现在和 B–D 组同住一个文件；拆出去并不划算 ——
#:    `_stdlib_names()` 取的是**当前解释器**的 stdlib 名单，3.9 的名单比 3.13
#:    小，同一个 import 在两个版本上会得出相反结论，那会把"环境差异"读成
#:    "代码缺陷"。要拆就得先把口径改成"在所有支持版本里都存在"，
#:    那是另一件事（见 README 待办），不在这里顺手做。
MIN_PYTHON = (3, 11)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _stdlib_names() -> set:
    """当前解释器认识的 stdlib 顶层模块名（含内置）。"""
    names = set(sys.builtin_module_names)
    names |= set(getattr(sys, "stdlib_module_names", ()))
    #: `sys.stdlib_module_names` 是 3.10+；3.9 上退回"看它住在哪"。
    if not names:
        names |= {p.stem for p in Path(sysconfig.get_paths()["stdlib"]).glob("*.py")}
    return names


def _imports_of(pkg: Path):
    """一个包的全部顶层 import（mod, first_part, 行号, 是否在 try 里）。"""
    out = []
    for f in sorted(pkg.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    out.append((f.name, a.name.split(".")[0], n.lineno))
            elif isinstance(n, ast.ImportFrom):
                if n.level and n.level > 0:
                    continue          # 相对导入 = 本包内
                if n.module:
                    out.append((f.name, n.module.split(".")[0], n.lineno))
    return out


def main() -> int:
    from aether import _version as V

    # ── A 组：零第三方依赖（可机器验证）──────────────────────────
    print("A 组：零第三方依赖（从口头声明变成可执行判据）")
    pkg = ROOT / "aether"
    std = _stdlib_names()
    local = {p.stem for p in pkg.glob("*.py")} | {"aether"}
    bad = [(f, m, ln) for f, m, ln in _imports_of(pkg)
           if m not in std and m not in local]
    ck("A1 aether/ 里没有第三方 import（stdlib + 本包之外的一律不算）",
       not bad, f"发现：{bad[:5]}")
    #: ⛔ 反证：判据必须真的会响。拿一个**已知**是第三方的名字走一遍。
    ck("A2 反证：判据真的会认出第三方（numpy 必须被判为第三方）",
       "numpy" not in std and "numpy" not in local)
    ck("A3 pyproject 里声明的运行期依赖为空（与 A1 同源）",
       _declared_deps() == [], str(_declared_deps()))

    # ── B 组：可安装的包 ─────────────────────────────────────────
    print("B 组：包元数据自洽")
    pp = ROOT / "pyproject.toml"
    ck("B1 pyproject.toml 存在且能被 tomllib 解析", pp.is_file())
    data = tomllib.loads(pp.read_text(encoding="utf-8"))
    proj = data.get("project", {})
    ck("B2 声明了 build-system（否则 pip 不知道该用什么后端）",
       bool(data.get("build-system", {}).get("build-backend")),
       str(data.get("build-system")))
    ck("B3 requires-python 有下限（不再靠'本机恰好是 3.13'）",
       bool(proj.get("requires-python")), str(proj.get("requires-python")))
    #: ⛔ 版本号只能有一处：pyproject 用 dynamic 从包读，不许写第二份字面量
    dyn = proj.get("dynamic", []) or []
    ck("B4 版本号是 dynamic（不写第二份字面量）",
       "version" in dyn and "version" not in proj,
       f"dynamic={dyn} static={proj.get('version')!r}")
    attr = (data.get("tool", {}).get("setuptools", {})
            .get("dynamic", {}).get("version", {}).get("attr", ""))
    ck("B5 dynamic 的 attr 指向 _version.CODE_VERSION（唯一事实源）",
       attr == "aether._version.CODE_VERSION", attr)
    ck("B6 入口声明指向真实模块（不用 import 也能核）",
       data.get("tool", {}).get("setuptools", {}).get("packages") == ["aether"],
       str(data.get("tool", {}).get("setuptools", {}).get("packages")))

    # ── C 组：命令行入口 ─────────────────────────────────────────
    print("C 组：入口可解析、版本同源")
    scripts = proj.get("scripts", {})
    ck("C1 声明了 `aether` 控制台脚本", "aether" in scripts, str(scripts))
    target = scripts.get("aether", "")
    mod_name, _, fn_name = target.partition(":")
    ck("C2 控制台脚本指向的模块存在", (ROOT / (mod_name.replace('.', '/') + '.py')).is_file(),
       target)
    import importlib
    fn = getattr(importlib.import_module(mod_name), fn_name, None)
    ck("C3 控制台脚本指向的目标可调用", callable(fn), target)
    #: `python -m aether` 必须有 `__main__.py` 才成立
    ck("C4 aether/__main__.py 存在（`python -m aether` 的前提）",
       (ROOT / "aether" / "__main__.py").is_file())
    r = subprocess.run([sys.executable, "-B", "-m", "aether", "--version"],
                       cwd=str(ROOT), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=120)
    out = (r.stdout or b"").decode("utf-8", "replace")
    ck("C5 `python -m aether --version` 可跑且报出 CODE_VERSION",
       r.returncode == 0 and V.CODE_VERSION in out, out.strip()[:120])
    #: ⛔ 入口与包版本必须同源：这是"两个入口各写一份"那类漂移的守门
    ck("C6 包版本 == _version.CODE_VERSION（入口读的是同一个源）",
       __import__("aether").__version__ == V.CODE_VERSION)
    #: 三个老入口都得还在（不许因为加了 __main__ 就把它们撞掉）
    for m in ("aether.cli", "aether.serve", "aether.ui"):
        rr = subprocess.run([sys.executable, "-B", "-m", m, "--help"],
                            cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=120)
        ck(f"C7 `python -m {m} --help` 仍然可用", rr.returncode == 0,
           f"rc={rr.returncode}")

    # ── D 组：授权与类型标注的可发现性 ───────────────────────────
    print("D 组：授权文件与类型标注")
    #: ⛔ 授权缺失是**法律层面的不可交付**（任何人都不能合法使用）。
    #:    这里只守"文件存在且与 pyproject 声明同源"，不判断选哪种授权 ——
    #:    那是人的决定（见 LICENSE 里的说明）。
    ck("D0 LICENSE 存在（缺它等于法律上不可交付）", (ROOT / "LICENSE").is_file())
    lic = proj.get("license", {})
    ck("D0b pyproject 的 license 指向该文件（两处不许各说一套）",
       lic.get("file") == "LICENSE", str(lic))
    ck("D1 py.typed 存在（PEP 561：下游才知道这里有注解）",
       (pkg / "py.typed").is_file())
    ck("D2 py.typed 被声明进包数据（否则装完就丢了）",
       "py.typed" in (data.get("tool", {}).get("setuptools", {})
                      .get("package-data", {}).get("aether", [])),
       str(data.get("tool", {}).get("setuptools", {}).get("package-data")))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：可安装、入口同源、零依赖可机器验证")
    return 0


def _declared_deps():
    """pyproject 里声明的运行期依赖（空 = 零依赖）。"""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data.get("project", {}).get("dependencies")
    return list(deps or [])


if __name__ == "__main__":
    sys.exit(main())
