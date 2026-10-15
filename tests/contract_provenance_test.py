#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：账本的**出处**——"这份数据是谁写的、按哪版格式写的"。

═══ 为什么这是一条真缺口，不是锦上添花 ═══

V6 的第一条命题是"事实只有一份 = 账本"。但收口前账本里**没有任何出处**：

  · `Event.identity()` 只有 seq/ts/kind/actor/payload/prev_hash；
  · `LEDGER_VERSION` **只参与算 hash**，从不落盘（全仓只有"定义一处、
    写入一处"两次命中，**没有任何读取**）；
  · 账本里查不到"这份数据是哪个代码版本写的"。

⇒ 两种事故，而报错都指向错误的方向：

  1. **格式漂移 ⇒ 报"链断了"**。读者按新规则复算 hash 与旧 hash 对不上
     ⇒ `verify_chain` 说 `hash 不一致`。现场看起来像"账本被篡改"，
     真相是"代码换了、数据没迁移"。
  2. **跨版本比读数**。`status` 的投影对账、`evolution` 的历史对照、
     监督器的跨轮判据，都会拿"不同代码版本产出的事件"当同一口径比 ——
     而没有任何一处能发现这件事。

本文件守：
  A 写入侧：每个写入者的**第一条**事件盖章，后续事件不重复盖；
  B 读取侧：三态（ok / unknown / incompatible）判据正确；
  C 反证：把判据拆掉 ⇒ 上面的断言必须转红（否则是恒真断言）；
  D 接线：`doctor` 真的会读它（"有机制"≠"这条路被走到"）。

跑法（离线，秒级）：
    python tests/contract_provenance_test.py
"""
import json
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


def main() -> int:
    from aether import provenance as P
    from aether._version import CODE_VERSION, LEDGER_FORMAT
    from aether.ledger import LEDGER_VERSION, Ledger

    tmp = Path(tempfile.mkdtemp(prefix="v6_prov_"))

    # ── A 组：版本的唯一事实源 ────────────────────────────────────
    print("A 组：版本号只有一个来源")
    import aether
    ck("A1 包版本 == _version.CODE_VERSION（不再各写一份）",
       aether.__version__ == CODE_VERSION, f"{aether.__version__}")
    ck("A2 账本格式版本 == _version.LEDGER_FORMAT（不再各写一份）",
       LEDGER_VERSION == LEDGER_FORMAT, f"{LEDGER_VERSION}")
    #: ⛔ 反证式静态检查：源码里不许再出现版本号**字面量**。
    #:    （此前 `__init__.py` 与 `serve.py` 各写一份，实测漂移过：
    #:     包报 0.1.0、端点报 0.2.0。）
    lit = []
    for p in sorted((ROOT / "aether").glob("*.py")):
        if p.name == "_version.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if CODE_VERSION in code:
                lit.append(f"{p.name}:{i}")
    ck("A3 除 _version.py 外没有版本号字面量（防再次漂移）",
       not lit, f"仍有字面量：{lit}")

    # ── B 组：写入侧盖章 ─────────────────────────────────────────
    print("B 组：写入侧只在第一条事件盖章")
    p1 = tmp / "stamped.jsonl"
    L1 = Ledger(p1)
    L1.append("harness.init", {"x": 1})
    L1.append("eval.verdict", {"x": 2})
    evs = L1.events()
    first = evs[0].payload
    second = evs[1].payload
    ck("B1 第一条事件带 src_version / format_version",
       first.get(P.KEY_CODE_VERSION) == CODE_VERSION
       and first.get(P.KEY_FORMAT) == LEDGER_FORMAT, str(first))
    ck("B2 后续事件不重复盖章（账本不被撑大）",
       P.KEY_CODE_VERSION not in second and P.KEY_FORMAT not in second,
       str(second))
    ck("B3 盖章后链仍完整（payload 变了，hash 也要跟着对）",
       L1.verify_chain()[0])
    ck("B4 读取侧能从账本里读出出处", L1.source().declared
       and L1.source().code_version == CODE_VERSION)
    #: ⛔ 反证：调用方**显式**声明的出处不许被覆盖（测试要靠它构造
    #:    "旧版本写的账本"）。`setdefault` 语义。
    p2 = tmp / "explicit.jsonl"
    L2 = Ledger(p2)
    L2.append("harness.init", {P.KEY_CODE_VERSION: "0.0.9"})
    ck("B5 反证：显式声明的出处不被写入侧覆盖（能构造历史数据）",
       L2.source().code_version == "0.0.9", str(L2.source().to_dict()))

    # ── C 组：兼容判据三态 ───────────────────────────────────────
    print("C 组：兼容判据（三态，不合并）")
    cases = [
        ({"src_version": CODE_VERSION, "format_version": LEDGER_FORMAT},
         P.COMPAT_OK, "同版本 ⇒ ok"),
        ({}, P.COMPAT_UNKNOWN, "无出处（升级前存量）⇒ unknown，不拦"),
        ({"format_version": LEDGER_FORMAT + 1}, P.COMPAT_INCOMPATIBLE,
         "格式版本不同 ⇒ incompatible"),
        ({"src_version": "9.9.9", "format_version": LEDGER_FORMAT},
         P.COMPAT_INCOMPATIBLE, "代码版本不兼容 ⇒ incompatible"),
        ({"src_version": "一句话不是版本", "format_version": LEDGER_FORMAT},
         P.COMPAT_INCOMPATIBLE, "版本字段形状非法 ⇒ incompatible（不得判 ok）"),
    ]
    for payload, want, label in cases:
        got = P.compat_report(P.read_from_payload(payload))["state"]
        ck(f"C·{label}", got == want, f"got={got} want={want}")
    ck("C6 unknown 与 incompatible 的处置不同（读数里必须能分辨）",
       P.compat_report(P.read_from_payload({}))["state"]
       != P.compat_report(P.read_from_payload({"format_version": 99}))["state"])
    #: 反证：判据不是恒真 —— 造一份不兼容账本，`source_compat` 必须报出来
    p3 = tmp / "incompat.jsonl"
    L3 = Ledger(p3)
    L3.append("harness.init", {P.KEY_CODE_VERSION: "0.1.0",
                               P.KEY_FORMAT: LEDGER_FORMAT})
    ck("C7 反证：不兼容账本 ⇒ source_compat 报 incompatible",
       L3.source_compat()["state"] == P.COMPAT_INCOMPATIBLE,
       str(L3.source_compat()["reasons"]))
    #: 存量账本（完全没有出处）⇒ unknown 且**放行**（不可判别 ≠ 有罪）
    p4 = tmp / "legacy.jsonl"
    p4.write_text(
        json.dumps({"seq": 1, "ts": 1.0, "kind": "t", "actor": "a",
                    "payload": {}, "prev_hash": "0" * 64, "hash": "x" * 64},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    L4 = Ledger(p4)
    ck("C8 存量账本 ⇒ unknown 而不是崩溃或判罪",
       L4.source_compat()["state"] == P.COMPAT_UNKNOWN)

    # ── D 组：判据进读数（有机制 ≠ 被走到）────────────────────────
    print("D 组：出处进了 doctor / --version（接线，不是只有模块）")
    r = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--version"],
                       capture_output=True, text=True, cwd=str(ROOT))
    ck("D1 CLI --version 能读出代码版本与格式版本",
       r.returncode == 0 and CODE_VERSION in r.stdout
       and f"v{LEDGER_FORMAT}" in r.stdout, repr(r.stdout[:80] + r.stderr[:80]))
    from aether import doctor as D
    names = [fn.__name__ for fn in D.CHECKS]
    ck("D2 doctor 的检查表里有账本出处这一项", "_check_provenance" in names,
       str(names))
    rep = D.run(ROOT)
    got = [c for c in rep["checks"] if c["name"] == "ledger.provenance"]
    ck("D3 doctor 真的产出了 ledger.provenance 读数（不是注册了没跑）",
       len(got) == 1, f"{[c['name'] for c in rep['checks']]}")
    #: ⛔ 反证：把出处检查换成一个"不兼容账本"的 root ⇒ 必须 fail。
    root2 = tmp / "vk"
    (root2 / "data").mkdir(parents=True)
    Lk = Ledger(root2 / "data" / "ledger.jsonl")
    Lk.append("harness.init", {P.KEY_CODE_VERSION: "0.1.0",
                               P.KEY_FORMAT: LEDGER_FORMAT})
    rep2 = D.run(root2, checks=[D._check_provenance])
    ck("D4 反证：不兼容的账本 ⇒ doctor 判 fail 并给出动作",
       rep2["checks"][0]["status"] == D.FAIL
       and bool(rep2["checks"][0]["action"]),
       str(rep2["checks"][0]))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：出处是一次写入、三态判读、并进了体检读数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
