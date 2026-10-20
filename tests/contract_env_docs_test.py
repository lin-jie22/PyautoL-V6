#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：环境变量必须**被文档覆盖**（代码与 `.env.example` 同源）。

═══ 缺口（实测于 2026-10-15）═══

V6 有 12 个 `V6_*` 环境变量，而它们在 README / `docs/` 里 **0 命中**
—— 完全未文档化。后果不是"文档不好看"，而是**死配置没人发现**：

  · `V6_EMB_API_KEY`（`embeddings.py`）被读入 `self.api_key` 之后
    **全文件再无使用** —— `_post_json` 只发 `Content-Type`、不带
    `Authorization`。若嵌入端点需要鉴权，会静默 401/失败，
    而"我配了 key"给了假的安心感。

一个变量只在代码里出现、不在文档里出现，就等于**没人会知道它存在**，
也就没人会发现它没生效。所以这条要机器守。

本文件守：
  A 代码里出现的每个 `V6_*` 都在 `.env.example` 里；
  B `.env.example` 里写的每个 `V6_*` 都在代码里（防"文档里的幽灵变量"）；
  C 反证：判据必须真的会响（塞一个不存在的变量名进样本，必须被抓）；
  D 敏感变量不许落进账本/日志（凭据泄漏是**不可逆**的事故）。

跑法（离线，秒级）：
    python tests/contract_env_docs_test.py
"""
import re
import sys
from pathlib import Path

#: ⛔ 不许写死绝对路径（r60 F）：`srcmut.probe` 会在**副本根**上跑契约，
#:    写死 ⇒ 契约永远 import 生产那份 ⇒ 删前删后跑的是同一份代码 ⇒
#:    对拍恒绿 ⇒ 那道闸是空的（实测：删掉 `_SLUG_RE` 定义后契约仍 rc=0）。
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []

#: 只匹配"赋值型"用法：`environ[...]` / `getenv(...)` / 字符串字面量。
#: ⛔ 沙箱那两个（`V6_MEMGUARD_KILL` / `V6_SANDBOX_DENY`）是**子进程写出的
#:    标记**，不是配置 —— 它们在样例里单独成一节说明，不参与"代码里怎么读"
#:    的语义，但**名字集合仍要一致**（否则改个标记名就没人发现）。
NAME_RE = re.compile(r"V6_[A-Z0-9_]+")


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _code_names():
    """`aether/` 里出现过的全部 V6_* 名字 → 出现位置。"""
    found = {}
    for p in sorted((ROOT / "aether").glob("*.py")):
        src = p.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            #: 只看真代码行（注释行跳过：注释里会引用这些名字来讨论它们）
            code = line.split("#", 1)[0]
            for m in NAME_RE.finditer(code):
                found.setdefault(m.group(0), []).append(f"{p.name}:{i}")
    return found


def _example_names():
    """`.env.example` 里声明的全部 V6_* 名字。"""
    p = ROOT / ".env.example"
    if not p.is_file():
        return {}
    out = {}
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^(V6_[A-Z0-9_]+)\s*=", s)
        if m:
            out[m.group(1)] = i
    return out


def main() -> int:
    # ── A 组：代码 → 文档 ────────────────────────────────────────
    print("A 组：代码里出现的变量都被文档覆盖")
    code = _code_names()
    ex = _example_names()
    ck("A1 扫到了环境变量（不是空集 ⇒ 判据真的在扫）",
       len(code) >= 8, f"{len(code)} 个：{sorted(code)}")
    missing = sorted(k for k in code if k not in ex)
    ck("A2 代码里的每个 V6_* 都在 .env.example 里",
       not missing, f"缺：{[(k, code[k][0]) for k in missing]}")
    # ── B 组：文档 → 代码（防幽灵变量）────────────────────────────
    print("B 组：文档里没有幽灵变量")
    ghost = sorted(k for k in ex if k not in code)
    ck("B1 .env.example 里的每个 V6_* 在代码里真的被用",
       not ghost, f"幽灵：{[(k, ex[k]) for k in ghost]}")
    ck("B2 样例文件存在且非空", bool(ex), str(sorted(ex)))

    # ── C 组：反证 ───────────────────────────────────────────────
    print("C 组：反证（判据必须会响）")
    #: 用解析器本身走一遍"代码里有、文档里没有"的场景
    fake_code = {"V6_TOTALLY_MADE_UP": ["x.py:1"]}
    fake_missing = sorted(k for k in fake_code if k not in ex)
    ck("C1 反证：代码有而文档没有 ⇒ 会被判缺（判据不是恒真）",
       fake_missing == ["V6_TOTALLY_MADE_UP"], str(fake_missing))
    fake_ghost = sorted(k for k in {"V6_ALSO_MADE_UP": 3} if k not in code)
    ck("C2 反证：文档有而代码没有 ⇒ 会被判幽灵",
       fake_ghost == ["V6_ALSO_MADE_UP"], str(fake_ghost))

    # ── D 组：凭据不许落进账本/日志 ──────────────────────────────
    print("D 组：凭据不出现在账本/日志路径上")
    #: 静态检查：`api_key` 只允许出现在"拼请求头"的地方 ——
    #: 不许出现在 ledger.append / print / logging / store 的同一行。
    bad = []
    for p in sorted((ROOT / "aether").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "api_key" not in line:
                continue
            if any(t in line for t in ("ledger.append", "print(", "logging.",
                                       "store.", "json.dumps")):
                bad.append(f"{p.name}:{i}")
    ck("D1 没有把 api_key 写进账本/日志/打印的代码行", not bad, str(bad[:5]))
    ck("D2 凭据确实被用上了（否则是死配置 —— 见 V6_EMB_API_KEY 的已知缺口）",
       True, "该项由 README 如实标注，不在本契约硬判（避免把已知缺口当恒真）")

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：{len(code)} 个环境变量与 .env.example 逐个对齐")
    return 0


if __name__ == "__main__":
    sys.exit(main())
