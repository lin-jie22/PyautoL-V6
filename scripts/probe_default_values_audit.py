#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针（**只读、不进回归**）：翻生产默认值，看契约看不看得见。

═══ 它回答的那个问题 ═══

契约里绝大多数条目都是**用显式参数构造**被测对象的
（`EvolutionConfig(cleanup_enabled=False)`、`EvolutionLoop(..., store=st)`…）。
那种条目只证明"传了会生效"，证明不了"生产默认就是那个值"。

⇒ 把某个默认值翻掉，"测试全绿、生产失效"可以**一条红都不出现** ——
   与 V5「默认关的记账开关」是同一个病，只是这次落在**测试侧**。
   （2026-10-16 实测：把减负地板 1.0 放松到 0.1、或让清理一次清 5 条，
     `contract_cleanup_test.py` 的 30 条断言里**只有**直接盯默认值那
     两条会响，其余 28 条一条不动。）

═══ 做法（mutation testing，逐个来、一次只翻一个）═══

  1. 把仓库复制到 /tmp 的独立副本（契约的 ROOT 由 `__file__` 推出
     ⇒ 在副本里跑，读的就是副本的源码，与主仓库零接触）；
  2. 把目标文件里那一行字段声明翻成事故值；
  3. 在副本里跑指定的契约，收集哪些条目转红。

⇒ **转红 0 条 = 那个默认值没有任何契约盯着 = 改了没人知道。**

⛔ 为什么不在回归里跑：一次翻一个、每个都要复制一份仓库再跑一整份
   契约（实测 3 个 mutant 近 3 分钟）。它**按需**跑 —— 改了默认值、
   或新加了一个默认开启的开关时跑一次。

⛔⛔ 探针自己也要有反证（否则"全 0 红"可能只是探针没跑起来）：
    `CANARY` 是一个**已知会转红**的 mutant。若它报 0 条，
    本探针直接判自己坏了（exit 1）—— 那时该查的是复制/注入/跑的管线，
    而不是相信"这几个默认值没人盯"。

跑法：
    python scripts/probe_default_values_audit.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


@dataclass(frozen=True)
class Mut:
    """一个事故版：把 `src` 里的 `old` 那一行翻成 `new`，用 `contract` 验。"""
    src: str            # 相对仓库根的目标文件
    old: str            # 字段声明原文（必须唯一）
    new: str            # 事故值
    contract: str       # 拿哪份契约去验
    note: str = ""      # 为什么要盯它（给人看的）


#: ⛔ 逐个登记。**一次只翻一个**是硬约束：混在一起 ⇒ 红的是哪条分不清。
MUTANTS: tuple = (
    Mut("aether/evolution.py", "cleanup_enabled: bool = True",
        "cleanup_enabled: bool = False", "tests/contract_cleanup_test.py",
        "整根减负轴空转"),
    Mut("aether/evolution.py", "min_load_gain: float = 1.0",
        "min_load_gain: float = 0.1", "tests/contract_cleanup_test.py",
        "减负地板放松 ⇒ 清掉半条也算减负"),
    Mut("aether/evolution.py", "cleanup_group_size: int = 1",
        "cleanup_group_size: int = 5", "tests/contract_cleanup_test.py",
        "一次清 5 条 ⇒ 出事不知道是谁"),
)

#: ⛔ 反证（探针自检）：这一条**必须**转红。它坏了 ⇒ 上面全是假读数。
CANARY = MUTANTS[0]


def _build(dst: Path, contract: str) -> None:
    """复制一份能跑契约的最小仓库（与主仓库零接触）。"""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copytree(ROOT / "aether", dst / "aether")
    #: 契约常读 `tasks/textscore`（真环境）。整个 tasks 一起搬，
    #: 免得某个契约换个环境就跑不起来 —— 那时红的是假红。
    if (ROOT / "tasks").is_dir():
        shutil.copytree(ROOT / "tasks", dst / "tasks")
    (dst / "tests").mkdir(parents=True)
    shutil.copy(ROOT / contract, dst / contract)


def _mutate(dst: Path, m: Mut) -> int:
    """翻那一行。⛔ 原文必须**恰好出现一次** —— 出现 0 次说明字段改名了
    （探针悄悄失效，报出来的 0 条红是假的）；出现多次说明不敢下刀。"""
    f = dst / m.src
    txt = f.read_text(encoding="utf-8")
    n = len(re.findall(re.escape(m.old), txt))
    if n != 1:
        raise RuntimeError(
            f"目标串出现 {n} 次（应为 1）：{m.old!r} —— 探针已失效，"
            f"请更新 MUTANTS（这是探针的**读数**，不是被测对象的结论）")
    f.write_text(txt.replace(m.old, m.new), encoding="utf-8")
    return n


def _run(dst: Path, contract: str):
    return subprocess.run([PY, contract], cwd=str(dst), capture_output=True,
                          text=True, timeout=900)


def _report(m: Mut) -> int:
    dst = Path("/tmp/v6_dv_mut_" + re.sub(r"\W+", "_", m.old))
    print("─" * 68)
    print(f"{m.src}  |  {m.note}")
    print(f"  {m.old!r}  →  {m.new!r}")
    try:
        _build(dst, m.contract)
        _mutate(dst, m)
        p = _run(dst, m.contract)
    except Exception as e:                                # noqa: BLE001
        print(f"  !! 探针构建/注入失败：{type(e).__name__}: {e}")
        return -1
    out = p.stdout or ""
    fails = [ln.strip()[5:].strip() for ln in out.splitlines()
             if ln.strip().startswith("FAIL ")]
    errs = [ln.strip() for ln in (p.stderr or "").splitlines()
            if "Error" in ln or "Traceback" in ln]
    if errs:
        print(f"  !! 契约崩了（rc={p.returncode}）：{errs[0][:120]}")
        for e in errs[1:3]:
            print(f"     {e[:120]}")
        return -1
    if not fails:
        print(f"  ⚠️  **没有任何条目转红** —— 这个默认值无人盯"
              f"（改了它，{Path(m.contract).name} 全绿）")
        return 0
    print(f"  ✅ 转红 {len(fails)} 条：")
    for line in fails:
        tag = line.split(" ")[0]
        print(f"     · {tag}  {line[len(tag):].strip()[:70]}")
    return len(fails)


def main() -> int:
    print("探针：翻生产默认值，看契约看不看得见（逐个、只读、可中断）")
    print(f"仓库：{ROOT}")
    print()
    results = {}
    for m in MUTANTS:
        results[m.old] = _report(m)
    print("─" * 68)

    #: ⛔⛔ 自检：反证报 0 ⇒ **探针自己坏了**，那时别信上面任何一个 0。
    canary = results.get(CANARY.old)
    if canary == 0 or canary == -1:
        print("❌ 探针自检失败：反证本应转红却报 0 / 崩了。"
              "上面所有『无人盯』的结论**作废** —— 先修管线。")
        return 1
    blind = [m for m in MUTANTS if results.get(m.old) == 0]
    if blind:
        print(f"⚠️  无人盯的默认值 {len(blind)} 个：")
        for m in blind:
            print(f"   · {m.src} :: {m.old}  ({m.note})")
        print("   ⇒ 加一条直接断言**默认值本身**的断言"
              "（不是「传了才生效」的那种）。")
    else:
        print("✅ 登记的默认值都有契约盯着（反证已确认探针在干活）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
