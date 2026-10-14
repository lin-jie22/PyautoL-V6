#!/usr/bin/env python3
"""契约 · 实验可复现性：跑的参数必须进账本（r35）。

缺口（2026-10-14 取证）：`evolve` 的命令行参数**只活在终端里**。
账本能回答"改了什么"，却回答不了"是用什么口径跑出来的" ⇒
同一份代码跑出不同结果时，无从分辨是随机性还是参数变了。

本契约守三件事：
    A 组  参数**进账本**：有 `evolution.run_config` 事件，字段齐全，
          且与 stdout 输出**同源**（不是各写一遍）。
    B 组  记的是**实际传入值**，不是默认值常量（改参数 ⇒ 记录跟着变）。
    C 组  ⛔ 反证：查询本身是有效的（换个不存在的 kind 必须查不到）。

⛔ 端到端跑真 CLI + 真账本，不在进程内 mock：
   在进程内断言"写了账本"会连"根本没写"一起放过（假绿）。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED: list = []
PY = sys.executable
KIND = "evolution.run_config"


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def evolve(root: str, *extra: str):
    p = subprocess.run([PY, "-m", "aether.cli", "--root", root,
                        "evolve", "--rounds", "1", "--group", "2", *extra],
                       capture_output=True, text=True, cwd=str(ROOT),
                       timeout=300)
    return p.returncode, p.stdout, p.stderr


def ledger_events(root: str, kind: str):
    """从账本里取某类事件 —— 与生产同一条读取路径（`Ledger.events`）。

    ⛔ `events()` 返回的是 **`Event` dataclass**（字段 `seq/ts/kind/actor/
    payload/prev_hash/hash`），**不是 dict** —— 按 dict 取会抛
    AttributeError 而被 except 吞掉 ⇒ 断言变假绿。
    """
    sys.path.insert(0, str(ROOT))
    from aether.ledger import Ledger
    led = Ledger(str(pathlib.Path(root) / "data" / "ledger.jsonl"))
    return [e for e in led.events() if getattr(e, "kind", "") == kind]


print("=" * 62)
print("契约 · 实验参数进账本（可复现性）")
print("=" * 62)

tmp = pathlib.Path(tempfile.mkdtemp(prefix="v6_repro_"))
root = str(tmp)
shutil.copytree(ROOT / "tasks", tmp / "tasks")

# ── A 组：参数进账本 ────────────────────────────────────────────
print(f"\n[A] 跑一轮 evolve（root={tmp.name}），参数必须落进账本")
rc, out, err = evolve(root, "--seed", "7", "--max-rounds", "3")
try:
    raw = out[out.find("{"):]
    rep = json.loads(raw)
except Exception as e:
    ck("A0 evolve 正常结束（否则后面的断言都在空数据上比）", False,
       f"{type(e).__name__}: {e} · {err[-300:]}")
    print("\n" + "=" * 62)
    sys.exit(1)
ck("A0 evolve 正常结束", rc == 0, f"rc={rc}")

rcfg = rep.get("run_config")
ck("A1 stdout 里有 run_config", isinstance(rcfg, dict), str(type(rcfg)))

evs = ledger_events(root, KIND)
ck("A2 账本里有 `evolution.run_config` 事件", len(evs) >= 1, f"{len(evs)} 条")
if evs:
    body = getattr(evs[-1], "payload", None) or {}
    ck("A3 账本里的参数与 stdout 同源（seed 一致）",
       str(body.get("seed")) == str(rcfg.get("seed")),
       f"账本={body.get('seed')!r} 输出={rcfg.get('seed')!r}")

REQUIRED = ("seed", "rounds", "group", "proposer", "dev_env", "heldout_env",
            "heldout", "parallel", "embedder", "budget", "checkpoint",
            "resumed_from")
missing = [k for k in REQUIRED if k not in (rcfg or {})]
ck("A4 影响结果的关键参数全在（缺一个就无法复现）",
   not missing, f"缺 {missing}" if missing else f"{len(REQUIRED)} 项齐全")

# ── B 组：记的是实际传入值 ──────────────────────────────────────
print("\n[B] 记的是**实际传入值**，不是默认值常量")
ck("B1 seed=7 被如实记录（默认值是 0 ⇒ 记常量就会是 0）",
   str(rcfg.get("seed")) == "7", f"seed={rcfg.get('seed')!r}")
ck("B2 rounds=1 被如实记录（默认是 3）",
   str(rcfg.get("rounds")) == "1", f"rounds={rcfg.get('rounds')!r}")
ck("B3 group=2 被如实记录（默认是 3）",
   str(rcfg.get("group")) == "2", f"group={rcfg.get('group')!r}")
ck("B4 预算被记录（--max-rounds 3，默认空串 ⇒ 记常量就会是空）",
   str((rcfg.get("budget") or {}).get("max_rounds")) == "3",
   str(rcfg.get("budget")))
ck("B5 `resumed_from` 是 0（本轮不是续跑 ⇒ 别记成 None 冒充未确认）",
   rcfg.get("resumed_from") == 0, str(rcfg.get("resumed_from")))

# ── C 组：反证 ──────────────────────────────────────────────────
print("\n[C] ⛔ 反证：查询本身有效（换个不存在的 kind 必须查不到）")
ck("C1 不存在的 kind ⇒ 0 条（证明 A2 不是「查询写错了所以恒真」）",
   len(ledger_events(root, "evolution.run_config_nonexistent")) == 0)
ck("C2 账本里确实有别类事件（证明账本不是空的 ⇒ A2 的 0 条会真红）",
   len(ledger_events(root, "harness.register")) >= 1
   or sum(len(ledger_events(root, k)) for k in
          ("harness.register", "memory.write", "evolution.round")) >= 1)

print("\n" + "=" * 62)
if FAILED:
    print(f"❌ {len(FAILED)} 项未通过：" + "; ".join(FAILED))
    sys.exit(1)
print("✅ 全部通过")
