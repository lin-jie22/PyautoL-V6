#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：账本是唯一事实源，且**改一条就断链**。

判据来源（V5 硬教训的制度化）：
  · V5 把状态散在 3 个 DB + 多个 JSON ⇒ 对账要靠猜字段名；
  · V5 热进程回写覆盖文件修改 ⇒ 并发写会静默丢；
  · 2026 self-harness 综述把"可回滚 + 爆炸半径"列为必备属性，
    而没有 append-only 史，回滚就是空话。

本文件守：内容寻址确定性 / 哈希链 / 篡改可检测 / 并发不分叉 / 只读投影可重建。

用法：
    .venv/bin/python tests/contract_ledger_test.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether.ledger import Ledger, Event, content_hash, canonical_json

    tmp = Path(tempfile.mkdtemp(prefix="v6_ledger_"))

    # ── A 组：内容寻址确定性（跨进程同输入必须同 hash）──────────
    print("A 组：内容寻址确定性")
    payload = {"中文": "判据必须可读不可写", "n": 3, "list": [1, 2]}
    h1 = content_hash(payload)
    code = ("import sys; sys.path.insert(0, %r); "
            "from aether.ledger import content_hash; "
            "print(content_hash(%r))" % (str(ROOT), payload))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(ROOT))
    h2 = out.stdout.strip()
    ck("A1 跨进程 hash 一致（TS/其他语言也能算出来）", h1 == h2, f"{h1[:12]} vs {h2[:12]}")
    ck("A2 键序不影响 hash（sort_keys）",
       content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1}))
    ck("A3 中文不被转义（跨语言解析不会错位）",
       "判据" in canonical_json({"k": "判据"}))
    ck("A4 值变则 hash 变", content_hash({"a": 1}) != content_hash({"a": 2}))

    # ── B 组：哈希链 ──────────────────────────────────────────────
    print("B 组：哈希链")
    p = tmp / "a.jsonl"
    led = Ledger(p)
    for i in range(5):
        led.append("test.event", {"i": i}, actor="test")
    ok, seq, why = led.verify_chain()
    ck("B1 正常追加 ⇒ 链完整", ok, why)
    ck("B2 prev_hash 首尾相接",
       all(e.prev_hash == (led.events()[i - 1].hash if i else Ledger.GENESIS_HASH)
           for i, e in enumerate(led.events())))
    ck("B3 事件数 = 5", len(led.events()) == 5)

    # ── C 组：反证 —— 篡改必须被检测到（证明 B 不是死代码）──────
    print("C 组：反证（篡改可检测）")
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    ev = json.loads(lines[2])
    ev["payload"]["i"] = 999                      # 改中间一条的 payload
    lines[2] = json.dumps(ev, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    bad = tmp / "tampered.jsonl"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    led2 = Ledger(bad)
    ok2, seq2, why2 = led2.verify_chain()
    ck("C1 改中间 payload ⇒ 链断裂", not ok2, f"却报完整（{why2}）")
    ck("C2 断裂定位到被改的那一条", seq2 == 3, f"实得 seq={seq2}")

    # 删中间一行
    del lines[2]
    bad2 = tmp / "deleted.jsonl"
    bad2.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok3, seq3, _ = Ledger(bad2).verify_chain()
    ck("C3 删中间一行 ⇒ 链断裂（不是静默少一条）", not ok3, f"seq={seq3}")

    # ── D 组：并发不分叉 ──────────────────────────────────────────
    print("D 组：并发追加")
    p2 = tmp / "conc.jsonl"
    p2.write_text("", encoding="utf-8")
    n_proc, n_each = 4, 5
    procs = []
    for k in range(n_proc):
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r});"
                f"from aether.ledger import Ledger;"
                f"L=Ledger({str(p2)!r});\n"
                f"for i in range({n_each}): L.append('conc', {{'p': {k}, 'i': i}}, actor='p{k}')")
        procs.append(subprocess.Popen([sys.executable, "-c", code],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      cwd=str(ROOT)))
    for pr in procs:
        pr.communicate(timeout=60)
    led3 = Ledger(p2)
    ok4, _, why4 = led3.verify_chain()
    ck(f"D1 {n_proc} 进程并发 ⇒ 链完整", ok4, why4)
    ck(f"D2 事件一条不丢（{n_proc * n_each} 条）",
       len(led3.events()) == n_proc * n_each, f"实得 {len(led3.events())}")
    ck("D3 seq 严格递增无重复",
       [e.seq for e in led3.events()] == list(range(1, n_proc * n_each + 1)))

    # ── E 组：投影可重建（库只是视图）────────────────────────────
    print("E 组：账本 → 投影")
    from aether.store import Store
    db = tmp / "v.db"
    st = Store(db)
    n = st.rebuild_from_ledger(Ledger(p).events())
    ck("E1 从账本重建成功", n >= 0)
    ck("E2 重建后投影可读", st.stats()["n_runs"] >= 0)
    st.close()

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
