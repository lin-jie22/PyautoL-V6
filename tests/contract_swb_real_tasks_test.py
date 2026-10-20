#!/usr/bin/env python3
"""契约 · 把 SWE-bench Verified 的真实题物化成 V6 环境（r62）。

═══ 守什么 ═══

  A `apply_hunk`：单行替换 / 纯插入 / **匹配到多处必须抛错**（不许猜）
  B 真值校准：`expect` 来自 **gold 版真跑**，不是手写常量
  C 鉴别力：gold 与 patch 前结果相同的探针**必须被丢弃**（否则是恒真断言）
  D 双向校验：修复版全绿 **且** patch 前全红
  E ⛔⛔ **物化进 `task.py` 的必须是修复版** —— 这是本契约最重要的一条
  F 反证：把 `task.py` 换成 patch 前版本 ⇒ E 必须红

═══ E 组为什么单独立组（r62 实测事故）═══

第一版把 patch **前**的源码写进 `task.py`（想的是"让 agent 去修 bug"），
判据却来自 patch **后**的行为 ⇒ 判据在物化环境上 **100% 失败**。
后果不是报错，而是三个环境全部：

    attempted=12  valid=0  yield=0.0  behavior=0

—— 合成器要的是 F2P（变异**前**通过、变异**后**失败），基线本来就红
就没有"可失去的东西" ⇒ 一切改动都测不出差别。这与"发展集基线 0/5
导致 advantage 恒 0"是**同一个病**，只是发生在题源层。

⛔ 所以 E 组必须**同时**断言两件事：修复版 yield > 0、patch 前 yield == 0。
   只断言前者 ⇒ 换回 bug 版也可能因为"变异点少了"碰巧过。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

SUPPORTED_MODES = ("default",)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED: list = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def _load_swb():
    p = ROOT / "scripts" / "swb_import.py"
    spec = importlib.util.spec_from_file_location("swb_import", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


swb = _load_swb()

# ── 同构小样本（形态与真实题一致，但不带 django 的 22K）──────────
PRE = '''FILE_UPLOAD_PERMISSIONS = None


class Box:
    def __init__(self, items):
        self._d = dict.fromkeys(items)

    def __iter__(self):
        return iter(self._d)

    def __contains__(self, k):
        return k in self._d
'''

FIXED = '''FILE_UPLOAD_PERMISSIONS = 0o644


class Box:
    def __init__(self, items):
        self._d = dict.fromkeys(items)

    def __iter__(self):
        return iter(self._d)

    def __reversed__(self):
        return reversed(self._d)

    def __contains__(self, k):
        return k in self._d
'''

PATCH_REPLACE = """diff --git a/task.py b/task.py
--- a/task.py
+++ b/task.py
@@ -1,2 +1,2 @@
-FILE_UPLOAD_PERMISSIONS = None
+FILE_UPLOAD_PERMISSIONS = 0o644
"""

PATCH_INSERT = """diff --git a/task.py b/task.py
--- a/task.py
+++ b/task.py
@@ -10,2 +10,3 @@
     def __iter__(self):
         return iter(self._d)
+    def __reversed__(self):
+        return reversed(self._d)
"""

probes = [
    {"name": "perm", "expr": "FILE_UPLOAD_PERMISSIONS"},
    {"name": "rev", "expr": "list(reversed(Box([1, 2, 3])))"},
    #: ⛔ 恒真探针：两版都成立 ⇒ 必须被 C 组丢弃
    {"name": "always_true", "expr": "isinstance(Box, type)"},
]

print("=" * 62)
print("契约 · SWE-bench Verified → V6 环境物化")
print("=" * 62)

# ── A 组：patch 应用 ─────────────────────────────────────────────
print("\n[A] apply_hunk")
out, how = swb.apply_hunk(PRE, PATCH_REPLACE)
ck("A1 单行替换：`None` → `0o644`", "FILE_UPLOAD_PERMISSIONS = 0o644" in out,
   how)
ck("A2 替换后行数不变", len(out.splitlines()) == len(PRE.splitlines()),
   f"{len(out.splitlines())} vs {len(PRE.splitlines())}")
try:
    out2, how2 = swb.apply_hunk(PRE, PATCH_INSERT)
    ck("A3 纯插入：`__reversed__` 被插到 __iter__ 之后",
       "__reversed__" in out2 and out2.index("__reversed__")
       > out2.index("__iter__"), how2)
except Exception as e:                                  # noqa: BLE001
    ck("A3 纯插入", False, f"{type(e).__name__}: {e}")

#: ⛔ 匹配多处必须抛错，不许"替换第一处" —— 那是静默改错位置。
ambiguous = PRE + "\nFILE_UPLOAD_PERMISSIONS = None\n"
try:
    swb.apply_hunk(ambiguous, PATCH_REPLACE)
    ck("A4 ⛔ 上下文匹配到 2 处必须抛错（不许猜哪一处）", False, "居然没抛")
except ValueError as e:
    ck("A4 ⛔ 上下文匹配到 2 处必须抛错（不许猜哪一处）", "2 处" in str(e),
       str(e))
except Exception as e:                                  # noqa: BLE001
    ck("A4 ⛔ 匹配多处必须抛错", False, f"抛了别的：{type(e).__name__}: {e}")

# ── B 组：真值校准 ──────────────────────────────────────────────
print("\n[B] expect 来自 gold 版真跑，不是手写")
tests, dropped = swb.calibrate({"probes": probes}, PRE, FIXED)
by = {t["name"]: t for t in tests}
ck("B1 perm 的 expect 是 gold 版的真实值 420",
   by.get("perm", {}).get("expect") == 420, f"{by.get('perm')}")
ck("B2 rev 的 expect 是 gold 版真的 reversed 结果",
   by.get("rev", {}).get("expect") == [3, 2, 1], f"{by.get('rev')}")
ck("B3 每条都记了 bug 版的读数（可核对「它凭什么有鉴别力」）",
   all(t.get("why_bug") for t in tests), f"{[t['name'] for t in tests]}")
#: ⛔ 核心：改 gold 版的值，expect 必须跟着变 ⇒ 证明它来自源码而非常量。
alt = FIXED.replace("0o644", "0o600")
tests2, _ = swb.calibrate({"probes": probes}, PRE, alt)
by2 = {t["name"]: t for t in tests2}
ck("B4 ⛔ 改 gold 版的常量 ⇒ expect 跟着变（证明真值来自源码）",
   by2.get("perm", {}).get("expect") == 384, f"{by2.get('perm')}")

# ── C 组：鉴别力 ────────────────────────────────────────────────
print("\n[C] 没有鉴别力的探针必须被丢弃")
names = [t["name"] for t in tests]
ck("C1 ⛔ 两版都成立的探针被丢弃", "always_true" not in names, f"{names}")
ck("C2 丢弃原因写清（「没鉴别力」与「探针写错」处置完全不同）",
   any(d["name"] == "always_true" and "鉴别力" in d["reason"] for d in dropped),
   f"{dropped}")
ck("C3 有鉴别力的两条都留着", {"perm", "rev"} <= set(names), f"{names}")
#: ⛔ `True == 1` 在 Python 里成立 ⇒ 类型不同必须判不等
ck("C4 ⛔ 类型不同不算相等（True == 1 的坑）",
   not swb._split_types_eq(True, 1) and swb._split_types_eq(1, 1), "")

# ── D 组：双向校验 ──────────────────────────────────────────────
print("\n[D] 双向校验：修复版全绿 + patch 前全红")
tmp = pathlib.Path(tempfile.mkdtemp(prefix="v6_swb_"))
envd = tmp / "iso_env"
envd.mkdir()
(envd / "task.py").write_text(FIXED, encoding="utf-8")
(envd / "tests.json").write_text(json.dumps(
    {"env_id": "iso_env", "tests": tests, "patch_preimage": PRE},
    ensure_ascii=False), encoding="utf-8")
r = swb.verify_env(envd)
ck("D1 修复版 0 失败", r["fixed_fails"] == 0, f"{r}")
ck("D2 patch 前全部失败", r["pre_fails"] == r["n"], f"{r}")
ck("D3 双向都过才算 ok", r["ok"] is True, f"{r}")

# ── E 组：V6 合成器能吃（最重要）────────────────────────────────
print("\n[E] ⛔ V6 合成器：修复版有 yield，patch 前 yield 必须为 0")
from aether.environment import Environment, synthesize_tasks


def _yield_of(src: str, tag: str) -> dict:
    d = tmp / tag
    d.mkdir(exist_ok=True)
    (d / "task.py").write_text(src, encoding="utf-8")
    (d / "tests.json").write_text(json.dumps(
        {"env_id": tag, "tests": tests}, ensure_ascii=False), encoding="utf-8")
    env = Environment.from_dir(d)
    rep = synthesize_tasks(env, max_n=12)
    return rep.to_dict() if hasattr(rep, "to_dict") else vars(rep)


ok_rep = _yield_of(FIXED, "env_fixed")
bad_rep = _yield_of(PRE, "env_pre")
ck("E1 修复版上能合成出 F2P 题（yield > 0）",
   ok_rep.get("valid", 0) > 0,
   f"attempted={ok_rep.get('attempted')} valid={ok_rep.get('valid')} "
   f"yield={ok_rep.get('yield_rate')}")
ck("E2 ⛔ patch 前版本上 yield 必须为 0（判据本来就红 ⇒ 没有可失去的东西）",
   bad_rep.get("valid", 0) == 0,
   f"attempted={bad_rep.get('attempted')} valid={bad_rep.get('valid')} "
   f"yield={bad_rep.get('yield_rate')}")
ck("E3 ⛔ 两者必须不同（否则 E1 不是靠「修复版」过的，而是靠别的东西）",
   (ok_rep.get("valid", 0) > 0) != (bad_rep.get("valid", 0) > 0),
   f"fixed={ok_rep.get('valid')} pre={bad_rep.get('valid')}")

# ── F 组：反证 ─────────────────────────────────────────────────
print("\n[F] ⛔ 反证：把 task.py 换成 patch 前版本 ⇒ E1 会红")
d2 = tmp / "env_rev"
d2.mkdir()
(envd / "task.py").write_text(PRE, encoding="utf-8")      # 故意换回 bug 版
r2 = swb.verify_env(envd)
ck("F1 ⛔ 换回 bug 版 ⇒ 双向校验立刻报红（证明 D 组不是恒真）",
   r2["ok"] is False, f"{r2}")
ck("F2 报红的具体理由是「修复版也失败」，不是「少读了几条」",
   r2["fixed_fails"] > 0, f"{r2}")

print("\n" + "=" * 62)
if FAILED:
    print(f"❌ {len(FAILED)} 项未过：")
    for n in FAILED:
        print(f"   · {n}")
    sys.exit(1)
print("✅ 全过")
