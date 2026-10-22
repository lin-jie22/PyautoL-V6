#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：`pyproject.toml` 不许写**第二份**版本号（必须 dynamic 指向 `_version.py`）。

═══ 为什么这条要单独一份文件（2026-10-22）═══

这条判据要**真解析 TOML**，而 `tomllib` 到 3.11 才进 stdlib。它原本住在
`contract_release_test.py` 的 A3 位置，代价是实测出来的：

    ❌ [default] contract_release_test.py 退出码 1
         PASS A1 …  PASS A2 …
         File ".../contract_release_test.py", line 72, in main
           import tomllib
       ModuleNotFoundError: No module named 'tomllib'

⇒ 3.9 上**整份 release 契约（10 条）一条都没跑完**，而报告里只有一个
"退出码 1"，看不出是"版本号漂了"还是"解释器版本不够"——两种后果完全相反。

⛔ 修法不是给整份 release 声明 `MIN_PYTHON = (3, 11)`：那会在 3.9 上把另外
   9 条**根本不需要** 3.11 的判据一起关掉。覆盖缺口应当是**最小必要**的
   （第 145 条的反面用法）：把需要高版本的那一条拆出来，让它自己声明，
   缺口就只剩这一条，且由框架记 SKIP、在报告里**点名**。

═══ 判据 ═══

  A1 真实的 `pyproject.toml` 必须是"dynamic 指向 `_version.py`"的形态；
  A2 正对照：正确形态被判 ok（否则后面三条反证没有意义）；
  A3–A5 **反证**：静态 version / dynamic 里没有 version / attr 指向别处，
        三种错法都必须被判出来 —— 否则 A1 只是"读到文件就算过"的恒真断言。

跑法（离线，毫秒级）：
    python tests/contract_release_toml_test.py
"""
import sys
from pathlib import Path

#: ⛔ 不许写死绝对路径：`srcmut.probe` 会在**副本根**上跑契约（第 30 条同族）。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔⛔ `tomllib` 是 3.11+。低于此版本时由 `aether check` 读这一行、记 **SKIP**
#:    并在报告里点名原因。版本闸**归框架裁定**，不在这份契约里 try/except ——
#:    后者极易写成"悄悄跳过"（用户只看到绿），第 145 条。
MIN_PYTHON = (3, 11)

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def version_is_dynamic(data):
    """判「版本号只有一个源，pyproject 只是引用它」。

    返回 `(ok, detail)`。⛔ 拆成纯函数是为了**能用假 dict 做反证** ——
    只对真实文件跑一次，就分不清"判据在判"还是"文件恰好长这样"。
    """
    proj = data.get("project") or {}
    static = proj.get("version")
    dyn = proj.get("dynamic") or []
    attr = ((((data.get("tool") or {}).get("setuptools") or {})
             .get("dynamic") or {}).get("version") or {}).get("attr")
    ok = (static is None
          and "version" in dyn
          and attr == "aether._version.CODE_VERSION")
    return ok, f"static={static!r} dynamic={dyn!r} attr={attr!r}"


def main() -> int:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    print("A 组：版本号不许有第二份（pyproject 必须引用 _version.py）")
    ok, detail = version_is_dynamic(data)
    ck("A1 真实 pyproject 的版本走 dynamic 指向 _version", ok, detail)

    # ── A2–A4 反证：三种错法都必须判出来 ────────────────────────
    good = {
        "project": {"dynamic": ["version"]},
        "tool": {"setuptools": {"dynamic": {"version": {
            "attr": "aether._version.CODE_VERSION"}}}},
    }
    ok_good, _ = version_is_dynamic(good)
    ck("A2 反证的正对照：正确形态被判 ok（否则 A3–A5 无从谈起）", ok_good,
       "正确形态被判错了 ⇒ 后面三条反证全无意义")

    bad_static = {"project": {"version": "0.2.0", "dynamic": []},
                  "tool": {"setuptools": {"dynamic": {"version": {
                      "attr": "aether._version.CODE_VERSION"}}}}}
    ok_static, d1 = version_is_dynamic(bad_static)
    ck("A3 反证：写了静态 version ⇒ 必须判出来", not ok_static, d1)

    bad_nodynamic = {"project": {}, "tool": {"setuptools": {"dynamic": {
        "version": {"attr": "aether._version.CODE_VERSION"}}}}}
    ok_dyn, d2 = version_is_dynamic(bad_nodynamic)
    ck("A4 反证：dynamic 里没有 version ⇒ 必须判出来", not ok_dyn, d2)

    bad_attr = {"project": {"dynamic": ["version"]},
                "tool": {"setuptools": {"dynamic": {"version": {
                    "attr": "aether.__version__"}}}}}
    ok_attr, d3 = version_is_dynamic(bad_attr)
    ck("A5 反证：attr 指向别处（第二份源）⇒ 必须判出来", not ok_attr, d3)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：版本号在 pyproject 里没有第二份")
    return 0


if __name__ == "__main__":
    sys.exit(main())
