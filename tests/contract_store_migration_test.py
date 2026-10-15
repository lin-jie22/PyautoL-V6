#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：投影的 schema 版本要有**真闸**，不能是"伪迁移"。

═══ 缺口（实测于 2026-10-15）═══

`Store._migrate()` 原来只有两件事：

    self.conn.executescript(_SCHEMA)      # 全是 CREATE TABLE IF NOT EXISTS
    if 版本行不存在: 插入 SCHEMA_VERSION

⇒ 它**从不比较**已存版本，**没有**升级/加列分支。后果分两种，都不轻：

  · 库里是旧结构 ⇒ 代码按新结构去读写 ⇒ `sqlite3.OperationalError`
    出现在**业务查询**里（排障方向指向查询本身，而根因是库没迁移）；
  · 更坏的一种：列都在但语义变了 ⇒ **读出一堆看起来正常的错数**。

而 `LEDGER_VERSION` 曾经是同一个病（"写了没人读"）—— 所以这不是孤例，
是同一种形态：**版本号写了，但没有一处拿它做判断**。

本文件守：
  A 打开库时会**比较**版本，而不是只插不管；
  B 三态处置不合并：created / ok / upgrade_needed / refused_newer；
  C 反证：把版本写高 ⇒ 必须**拒绝打开**（fail-closed 的那个方向）；
  D 接线：`doctor` 真的会读它（有问题要报 fail，不是只在模块里有个属性）。

跑法（离线，秒级）：
    python tests/contract_store_migration_test.py
"""
import sqlite3
import sys
import tempfile
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


def _make_db(path: Path, version):
    """手工造一个"指定 schema 版本"的库（模拟别的代码版本写的库）。"""
    from aether.store import SCHEMA_VERSION
    con = sqlite3.connect(str(path))
    #: 借 Store 自己建表，避免把 _SCHEMA 抄第二份（抄一份就会漂移）
    from aether.store import Store
    s = Store(path)
    con2 = s.conn
    con2.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                 (str(version),))
    con2.commit()
    s.close()
    con.close()
    return SCHEMA_VERSION


def main() -> int:
    from aether import doctor as D
    from aether.store import SCHEMA_VERSION, Store

    tmp = Path(tempfile.mkdtemp(prefix="v6_store_"))

    # ── A 组：打开时会比较版本 ───────────────────────────────────
    print("A 组：打开库时比较版本（不是只插不管）")
    s_new = Store(tmp / "new.db")
    ck("A1 新建库 ⇒ created 且版本 == 代码版本",
       s_new.schema_state == "created"
       and s_new.schema_found == SCHEMA_VERSION,
       f"{s_new.schema_state}/{s_new.schema_found}")
    s_new.close()

    #: 同版本的既有库 ⇒ ok（不是每次都被当成"新建"）
    s_ok = Store(tmp / "new.db")
    ck("A2 同版本重开 ⇒ ok（不会被误判成需要迁移）",
       s_ok.schema_state == "ok", s_ok.schema_state)
    s_ok.close()

    #: 版本 0（老库 / 手工建的库）⇒ 放行但**明确标注**需要迁移
    old = tmp / "old.db"
    _make_db(old, 0)
    s_old = Store(old)
    ck("A3 版本低于代码 ⇒ upgrade_needed（放行 + 标注，不假装成功）",
       s_old.schema_state == "upgrade_needed" and s_old.schema_found == 0,
       f"{s_old.schema_state}/{s_old.schema_found}")
    s_old.close()

    # ── B 组：refused_newer —— 唯一必须 fail-closed 的方向 ────────
    print("B 组：库比代码新 ⇒ 必须拒绝")
    newer = tmp / "newer.db"
    _make_db(newer, SCHEMA_VERSION + 5)
    try:
        Store(newer)
        ck("B1 库版本高于代码 ⇒ 打开即拒绝（旧代码读新结构必然读错）",
           False, "没有拒绝")
    except RuntimeError as e:
        ck("B1 库版本高于代码 ⇒ 打开即拒绝（旧代码读新结构必然读错）",
           "高于" in str(e) or "新" in str(e), str(e)[:100])
    #: ⛔ 反证的另一半：被拒是**因为版本**，不是因为文件坏了。
    #:    同一条路径下"版本相同"必须能正常打开（否则 B1 是恒真断言）。
    same = tmp / "same.db"
    _make_db(same, SCHEMA_VERSION)
    s_same = Store(same)
    ck("B2 反证：同版本的库能正常打开（B1 不是恒真）",
       s_same.schema_state == "ok")
    s_same.close()
    #: 垃圾版本值不许崩：形状不对 ⇒ 当成"不可判别"（记 0），不抛异常
    junk = tmp / "junk.db"
    _make_db(junk, "not-a-number")
    s_junk = Store(junk)
    ck("B3 ⛔ 版本字段是垃圾值 ⇒ 不崩，按不可判别处理（记 0）",
       s_junk.schema_state == "upgrade_needed" and s_junk.schema_found == 0,
       f"{s_junk.schema_state}/{s_junk.schema_found}")
    s_junk.close()

    # ── C 组：接线 —— doctor 真的会读 ─────────────────────────────
    print("C 组：doctor 真的会读它（有属性 ≠ 进了读数）")
    src = (ROOT / "aether" / "doctor.py").read_text(encoding="utf-8")
    ck("C1 doctor 的投影检查读了 schema_state",
       "schema_state" in src, "没读到")
    #: 造一个"版本低于代码"的 root ⇒ doctor 必须报 fail 并给出路
    root2 = tmp / "vk"
    (root2 / "data").mkdir(parents=True)
    from aether.ledger import Ledger
    led = Ledger(root2 / "data" / "ledger.jsonl")
    led.append("harness.init", {"x": 1})
    _make_db(root2 / "data" / "aether.db", 0)
    rep = D.run(root2, checks=[D._check_projection])
    got = rep["checks"][0]
    ck("C2 版本落后 ⇒ doctor 判 fail（不是'一致就没事'）",
       got["status"] == D.FAIL, str(got))
    ck("C3 并给出可行动的出路（重建投影）", "rebuild" in got.get("action", ""),
       got.get("action", ""))
    #: 库比代码新 ⇒ doctor 必须报 fail 而不是崩（打开就会抛）
    root3 = tmp / "vk2"
    (root3 / "data").mkdir(parents=True)
    Ledger(root3 / "data" / "ledger.jsonl").append("harness.init", {"x": 1})
    _make_db(root3 / "data" / "aether.db", SCHEMA_VERSION + 5)
    rep3 = D.run(root3, checks=[D._check_projection])
    ck("C4 库比代码新 ⇒ doctor 报 fail（不是抛异常把体检打死）",
       rep3["checks"][0]["status"] == D.FAIL, str(rep3["checks"][0])[:120])

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：投影版本被真比较，新库拒读、老库标注、垃圾值不崩")
    return 0


if __name__ == "__main__":
    sys.exit(main())
