#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约 · 仓库里不许有「某台机器」的绝对路径（r67）。

═══ 起因：把仓库推到 GitHub 时，第一眼看到的是别人的家目录 ═══

上传后清点：**44 行**绝对路径散在 23 个跟踪文件里，其中 10 行是
`capabilities.rea.json` 的 `argv`（解释器 + 桥脚本）、10 行是
`tasks/*/tests.json` 的 `source_origin`、其余在 README / docs / 脚本常量里。

⛔ 分三类，性质完全不同 —— 混为一谈就会改错地方：

  · **运行时必需**（清单 argv、桥的 node/REA/ghidra 路径）：换个账号
    clone 下来，`caps invoke` 每一条 cli 能力都指向不存在的可执行文件，
    而报错是 `unavailable: 可执行文件不存在` ⇒ 看起来像"这个能力没装"。
    这是**最贵**的一种：能力是真验证过的（REA 契约 A–J 全绿），
    但它只在这台机器上成立。
  · **溯源元数据**（`source_origin`）：全仓 `grep` 只有一个写入点、
    **零个消费者** ⇒ 它不影响运行，但让 clone 下来的人以为这库带着私人路径。
  · **文档示例**（README / USAGE）：`cd /Users/<某人>/V6` 对任何人都没用。

═══ 本契约守什么 ═══

1. **静态闸**：所有 `git ls-files` 里的文本文件，不许出现真实家目录
   （`/Users/<名>` `/home/<名>` `C:\\Users\\<名>`）。脱敏形式
   （`/Users/…/V6`，取的是省略号不是用户名）**允许**，因为历史读数要保真。
2. **占位符闸**：`impl.argv` 只认 `{python}` / `{repo}`，未知占位符
   **fail-closed**（`rejected`）—— 留成字面量会让"清单写错了"表现成
   "能力坏了"（第 107 条）。展开后**每个 argv 指向的文件必须真的存在**，
   这才是"可移植"的判据：不是"没有绝对路径"，而是"在新机器上解析得出"。
3. **发现逻辑闸**：桥的 node / REA / ghidra 一律靠发现（env → PATH →
   受管目录 → 常见前缀），且**不许猜**：显式给了不存在的路径 ⇒ 返回空、
   由调用方报错，不许 fallback 到别的路径凑合用。
4. **溯源闸**：`source_origin` 不许是绝对路径，且必须还是可读的标签
   （`stdlib/core.py`），不是把字段删掉。

⛔ **反证是必需的**（A2/B6/E1/E2）：这份契约的核心断言是"零命中"，
   而"零命中"有两种含义 —— 真的干净，或者扫描器根本没在工作
   （本机 `grep` 是 toybox，正则退化成恒 0 命中，第 133 条踩过两次）。
   ⇒ 每条闸都配一条**必须命中**的注入项，红了才算闸有牙。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.interop import CapabilityError, expand_argv  # noqa: E402

FAILED: list[str] = []

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)


def ck(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILED.append(name)


#: ⛔ 三条前缀**拼**出来，不写字面量：本文件自己也被扫描，
#:    写死就等于给自己签发一张豁免（而豁免迟早会被抄到别处）。
_PREFIXES = ("/" + "Users" + "/",
             "/" + "home" + "/",
             "C:" + chr(92) + "Users" + chr(92))
#: 只认**像真用户名**的那一段：`[A-Za-z0-9._-]`。
#: ⇒ 脱敏形式 `/Users/…/V6`（省略号 U+2026）与 `/Users/<你>/V6` 天然不命中。
_PATS = [re.compile(re.escape(p) + r"[A-Za-z0-9._-]{1,32}(?:[/\\\"'\s,)]|$)")
         for p in _PREFIXES]


def scan_text(text: str) -> list[int]:
    """返回命中的行号（1 起）。**只认**上面那三条前缀。"""
    hits: list[int] = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in _PATS):
            hits.append(i)
    return hits


def tracked_files() -> list[str] | None:
    """`git ls-files` 的清单；不是 git 检出 ⇒ None（"跑不了"≠"通过了"）。"""
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT),
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x]


def read_text(p: Path) -> str | None:
    """读文本；二进制 / 超大 / 读不了 ⇒ None（跳过要**计数**，见 A3）。"""
    try:
        b = p.read_bytes()
    except OSError:
        return None
    if b"\0" in b[:8000] or len(b) > 4 * 1024 * 1024:
        return None
    return b.decode("utf-8", "replace")


def main() -> None:
    print("== A 组：静态闸 —— 跟踪文件里不许有真实家目录 ==")
    files = tracked_files()
    ck("A0 这是 git 检出（否则本组**没跑**，不是通过）", files is not None,
       "git ls-files 失败 —— 从 tarball 解出来的目录里本组无从断言")
    if files is None:
        files = []

    hits: list[str] = []
    n_scanned = 0
    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            continue
        text = read_text(p)
        if text is None:
            continue
        n_scanned += 1
        for ln in scan_text(text):
            hits.append(f"{rel}:{ln}")

    ck("A1 ⛔ 跟踪文件里零真实家目录（44 → 0）", not hits,
       f"命中 {len(hits)} 处：{hits[:6]}")
    #: 扫描面必须够大：只扫到 3 个文件也会"零命中"。
    ck("A2 扫描面覆盖足够（≥200 个文本文件）", n_scanned >= 200,
       f"只扫了 {n_scanned} 个")
    #: ⛔ 反证：注入三条**必定命中**的样本 ⇒ 扫描器必须报出来。
    #:    没有这一条，"零命中"可能只是正则写错了（第 133 条）。
    injected = ("x = \"" + _PREFIXES[0] + "alice" + "/repo/y.py\"\n"
                + "y = \"" + _PREFIXES[1] + "bob" + "/repo\"\n"
                + "z = '" + _PREFIXES[2] + "carol" + "\\repo'\n")
    ck("A2b ⛔ 反证：注入三条真实样式的路径 ⇒ 必须命中 3 行",
       scan_text(injected) == [1, 2, 3], f"得到 {scan_text(injected)}")
    #: 脱敏形式不许误伤：否则历史读数只能被删除，不能保留。
    redacted = ("a = \"/" + "Users/…/V6\"\n"
                "b = \"/" + "Users/<你>/V6\"\n")
    ck("A2c 脱敏形式（省略号 / 尖括号占位）**不**命中 —— 历史读数可以保留",
       scan_text(redacted) == [], f"得到 {scan_text(redacted)}")

    print("== B 组：清单 argv 的占位符（可移植性的真正判据：解析得出）==")
    ck("B1 `{python}` 展开成当前解释器",
       expand_argv(["{python}"], "t") == [sys.executable],
       str(expand_argv(["{python}"], "t")))
    ck("B2 `{repo}` 展开成代码根，且桥脚本真的在那儿",
       Path(expand_argv(["{repo}/scripts/rea_bridge.py"], "t")[0]).is_file(),
       expand_argv(["{repo}/scripts/rea_bridge.py"], "t")[0])
    ck("B3 ⛔ 无占位符 ⇒ 逐字不变（不许顺手 normalize / expanduser）",
       expand_argv(["/bin/sh", "-c", "echo hi"], "t") == ["/bin/sh", "-c", "echo hi"],
       str(expand_argv(["/bin/sh", "-c", "echo hi"], "t")))
    try:
        expand_argv(["{nope}"], "t")
        unknown_ok, unknown_msg = False, "(没抛)"
    except CapabilityError as ex:
        unknown_ok, unknown_msg = ex.status == "rejected", f"{ex.status}: {ex}"
    ck("B4 ⛔ 未知占位符 ⇒ rejected（不许留字面量，否则'清单写错'长成'能力坏了'）",
       unknown_ok, unknown_msg)

    manifest_path = ROOT / "capabilities.rea.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cli = [c for c in (manifest.get("capabilities") or [])
           if (c.get("impl") or {}).get("transport") == "cli"]

    def missing_targets(caps: list[dict]) -> list[str]:
        """展开后仍不存在的 argv **路径项**（"在新机器上跑得起来"的判据）。

        ⛔ 只查**像路径**的项：`--command analyze` 的值是命令名，不是文件
           （第一版把它也当路径查 ⇒ B6 假红 5 条，正好演示"判据写宽了"）。
        """
        bad: list[str] = []
        for c in caps:
            argv = expand_argv((c.get("impl") or {}).get("argv") or [],
                               str(c.get("id", "")))
            for i, a in enumerate(argv):
                if a.startswith("-") or (i and argv[i - 1] == "--command"):
                    continue
                if "/" in a and not Path(a).exists():
                    bad.append(f"{c.get('id')}:{a}")
        return bad

    ck("B5 清单里确实有 cli 能力（否则本组空转）", len(cli) > 0, f"n={len(cli)}")
    ck("B6 ⛔ 每个 cli 能力展开后，argv 指向的文件**全都存在**",
       not missing_targets(cli), f"缺 {missing_targets(cli)[:5]}")
    ck("B7 清单原文零绝对家目录（占位符替换完整）",
       not scan_text(manifest_path.read_text(encoding="utf-8")),
       str(scan_text(manifest_path.read_text(encoding="utf-8")))[:120])
    #: ⛔ 反证：把一条 argv 改成一个必然不存在的路径 ⇒ B6 的检查函数必须报出来。
    #:    否则 B6 可能只是个恒真断言（`Path(...).exists()` 写错就恒真）。
    doctored = [dict(cli[0], impl=dict(cli[0]["impl"],
                  argv=["{repo}/scripts/__no_such_bridge__.py"]))] if cli else []
    ck("B8 ⛔ 反证：argv 指向不存在的文件 ⇒ 检查函数必须报出来",
       bool(missing_targets(doctored)), "反证没报 ⇒ B6 恒真")

    print("== C 组：路径**发现**（node / REA / 引擎），不许写死也不许猜 ==")
    probe = (
        "import json, importlib.util, os, sys\n"
        "spec = importlib.util.spec_from_file_location('rb', %r)\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(json.dumps({'node': m.NODE, 'rea': m.REA,\n"
        "                  'engines': m._engine_env()}))\n"
    ) % str(ROOT / "scripts" / "rea_bridge.py")

    def run_probe(env_extra: dict[str, str]) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k not in ("V6_NODE", "V6_REA_MJS",
                            "GHIDRA_INSTALL_DIR", "REA_JADX_MCP_JAR")}
        env.update(env_extra)
        r = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120, env=env)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:                                        # noqa: BLE001
            return {"_err": (r.stderr or r.stdout)[-400:]}

    base = run_probe({})
    ck("C1 无 env 时 node 仍能发现（env → PATH → 受管目录）",
       bool(base.get("node")) and Path(str(base.get("node"))).is_file(),
       str(base.get("node") or base.get("_err")))
    #: ⛔ REA 是**可选**外部能力：CI 的干净机器上没装它。
    #:    所以断言不是"一定能发现"，而是"**发现的若存在就真的存在**" ——
    #:    发现逻辑不许返回一个不存在的路径（那才会把"没装"伪装成"能跑"）。
    #:    第一版写成"必须发现" ⇒ 在 Linux CI 上恒红，属于把本机状态当世界状态。
    rea_found = str(base.get("rea") or "")
    ck("C2 REA 未装 ⇒ 返回空串（不是瞎猜一个路径）；发现到 ⇒ 文件真的在",
       (rea_found == "") or Path(rea_found).is_file(),
       f"rea={rea_found!r}")
    ck("C2b 本机装没装 REA 都要能说清（空串与'找到了'二选一，不许第三种）",
       rea_found == "" or Path(rea_found).is_file(), f"rea={rea_found!r}")
    ck("C3 引擎变量只加**存在**的（不存在就静默留空，不造假仪表）",
       all(Path(v).exists() for v in (base.get("engines") or {}).values()),
       str(base.get("engines")))
    ck("C4 ghidra 的 SLEIGHHOME 与 GHIDRA_INSTALL_DIR 同步（有则同值，无则都没有）",
       (base.get("engines") or {}).get("SLEIGHHOME")
       == (base.get("engines") or {}).get("GHIDRA_INSTALL_DIR"),
       str(base.get("engines")))

    #: ⛔ 显式覆盖必须**生效**；指向不存在的路径必须**返回空**，不许猜下一个。
    #:    ⛔ 假文件建在**临时目录**：建在 `tests/` 里会被并行跑的其他契约
    #:    看到（`aether check -j N` 同时在扫 tests/）。
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / "fake_node"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        got = run_probe({"V6_NODE": str(fake)})
        ck("C5 `V6_NODE` 显式覆盖生效（不许被 PATH 抢走）",
           got.get("node") == str(fake), str(got.get("node")))
        gone = run_probe({"V6_NODE": str(ROOT / "__no_such_node__")})
        ck("C6 ⛔ 显式给了不存在的路径 ⇒ 返回空并报错，不许静默 fallback",
           gone.get("node") == "", f"得到 {gone.get('node')!r}")

    #: ⛔ 本次真踩的坑：`~/tools/ghidra_*` 同时命中目录与 `.zip`，
    #:    排序时 `.zip` 更大 ⇒ 不过滤类型就把压缩包当安装目录 ⇒ 引擎静默消失。
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "ghidra_1.0_PUBLIC").mkdir()
        (d / "ghidra_1.0_PUBLIC.zip").write_bytes(b"PK")
        code = (
            "import importlib.util, json\n"
            "spec = importlib.util.spec_from_file_location('rb', %r)\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print(json.dumps([m._glob_latest(%r, want='dir'),\n"
            "                  m._glob_latest(%r)]))\n"
        ) % (str(ROOT / "scripts" / "rea_bridge.py"), str(d / "ghidra_*"),
             str(d / "ghidra_*"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60, cwd=str(ROOT))
        try:
            as_dir, as_file = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:                                    # noqa: BLE001
            as_dir, as_file = "", ""
        ck("C7 ⛔ 通配同时命中目录与 .zip ⇒ `want='dir'` 必须给目录（不是压缩包）",
           as_dir == str(d / "ghidra_1.0_PUBLIC") and as_file.endswith(".zip"),
           f"dir={as_dir} file={as_file}")

    print("== D 组：`source_origin` 是可移植标签，不是某人的家目录 ==")
    prov = sorted((ROOT / "tasks").glob("*/tests.json"))
    ck("D0 有溯源文件可查（否则本组空转）", len(prov) >= 5, f"n={len(prov)}")
    origins = {}
    for p in prov:
        try:
            origins[p.parent.name] = str(
                json.loads(p.read_text(encoding="utf-8")).get("source_origin", ""))
        except Exception:                                        # noqa: BLE001
            origins[p.parent.name] = "<读不了>"
    ck("D1 ⛔ 没有一个是绝对路径", not [k for k, v in origins.items()
                                    if v.startswith("/")], str(origins)[:200])
    ck("D2 仍是**可读标签**（`stdlib/…`），不是把字段清空",
       sum(1 for v in origins.values() if v.startswith("stdlib/")) >= 5,
       str(origins)[:200])
    wg = (ROOT / "scripts" / "worldgen.py").read_text(encoding="utf-8")
    ck("D3 生成侧也换了口径（worldgen 用 `_origin_label`，旧写法已删）",
       "_origin_label(" in wg and 'str(src_root / f"{mod}.py")' not in wg,
       "worldgen 仍会把绝对路径写进溯源字段")

    print("== E 组：写死路径的**三个出口**都堵住了 ==")
    for rel in ("scripts/rea_bridge.py", "scripts/demo_evolve.py",
                "capabilities.rea.json", "supervisor/src/selfcheck.ts"):
        p = ROOT / rel
        text = read_text(p) or ""
        ck(f"E1 {rel} 零真实家目录", not scan_text(text), str(scan_text(text))[:80])
    #: ⛔ 反证：`supervisor/src/selfcheck.ts` 曾经写死 `ROOT`，而它的失败
    #:    表现是"跨语言一致性那组 import 失败"。这里钉住它按位置推导。
    ts = read_text(ROOT / "supervisor/src/selfcheck.ts") or ""
    ck("E2 selfcheck.ts 的 ROOT 从 import.meta.dirname 推导（不是写死）",
       "import.meta.dirname" in ts and 'const ROOT = "/' not in ts,
       "仍是写死路径")


if __name__ == "__main__":
    main()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{FAILED}")
        sys.exit(1)
    print("✅ 全部通过")
