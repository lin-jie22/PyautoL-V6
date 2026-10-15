#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：备份与恢复 —— 保护的对象必须是**不可重建**的那个。

补齐前的形态是反的：`rebuild` 能从账本重建 SQLite 投影，所以投影有
退路；而唯一事实源 `ledger.jsonl` 自己没有任何备份命令 ⇒ 能被保护的
恰好是能重建的，不能重建的恰好没人管。

本契约钉四件事：

  1. **备份自带校验和**，且备份完立刻验副本的链（不然「拷成功了」会被
     当成「备份可用"，半截拷贝要到恢复那一刻才暴露）；
  2. **恢复前先留当前状态**（覆盖写不可逆，而「当前」是还没坏的那份）；
  3. **校验不通过一律拒绝**，且缺文件 / hash 不符分开报（处置不同）；
  4. **读数要说清 source 与 projection**（丢了哪个是真的回不来）。

跑法（离线，秒级）：
    python tests/contract_backup_test.py
"""
import json
import sys
import tarfile
import tempfile
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether import backup as B
    from aether.ledger import Ledger

    tmp = Path(tempfile.mkdtemp(prefix="v6_bk_"))
    root = tmp / "root"
    (root / "data").mkdir(parents=True)
    led = Ledger(root / "data" / "ledger.jsonl")
    for i in range(3):
        led.append("test.event", {"i": i}, actor="test")
    (root / "data" / "memory.json").write_text(
        json.dumps({"notes": [{"id": "n1", "content": "记忆正文"}]}),
        encoding="utf-8")
    original = (root / "data" / "ledger.jsonl").read_text(encoding="utf-8")

    # ── A 组：备份 ────────────────────────────────────────────
    print("== A 组：备份必须自带校验和，且立刻验副本的链 ==")
    m = B.create(root, tmp / "bk")
    ck("A1 备份目录已生成", any((tmp / "bk").iterdir()))
    ck("A2 manifest 记了全部目标文件（含不存在的也记）",
       len(m.files) == len(B.BACKUP_TARGETS), str(len(m.files)))
    ck("A3 存在的文件都拷到了且 hash 记下来了",
       all(f.get("sha256") for f in m.files if not f.get("missing")))
    ck("A4 ⛔ 备份完立刻验链（不验 = 假仪表『拷成功=可用』）",
       m.chain_ok is True, str(m.chain_reason))
    ck("A5 缺失文件被记账而不是省略（否则「本来就 2 个」和「少拷了」一样）",
       any(f.get("missing") for f in m.files),
       str([f["rel"] for f in m.files]))
    bdir = sorted((tmp / "bk").iterdir())[-1]

    # ── B 组：往返 ────────────────────────────────────────────
    print()
    print("== B 组：改坏之后能恢复回来（往返一致 + 链完整）==")
    (root / "data" / "ledger.jsonl").write_text("被写坏了\n", encoding="utf-8")
    rep = B.restore(bdir, root)
    ck("B1 恢复后账本内容与原文件逐字一致",
       (root / "data" / "ledger.jsonl").read_text(encoding="utf-8") == original)
    ck("B2 恢复后账本链完整", Ledger(root / "data" / "ledger.jsonl").verify_chain()[0])
    ck("B3 恢复报告列出恢复了哪些", "data/ledger.jsonl" in rep["restored"],
       str(rep["restored"]))
    ck("B4 ⛔ 恢复前先留了当前状态（覆盖写不可逆）",
       rep["saved_current_to"] and Path(rep["saved_current_to"]).exists(),
       str(rep["saved_current_to"]))
    ck("B5 留的那份正是被写坏的那份（不是空壳）",
       (Path(rep["saved_current_to"]) / "data__ledger.jsonl").read_text(
           encoding="utf-8") == "被写坏了\n")

    # ── C 组：校验不通过 ⇒ 拒绝 ───────────────────────────────
    print()
    print("== C 组：坏备份必须拒绝，且说清坏在哪 ==")
    ok, problems, _ = B.verify(bdir)
    ck("C1 完好的备份 ⇒ 校验通过", ok and not problems, str(problems))
    bad = tmp / "bad_bk"
    bad.mkdir()
    (bad / "manifest.json").write_text("{}", encoding="utf-8")
    ok2, prob2, _ = B.verify(bad)
    ck("C2 manifest 缺 files ⇒ 拒绝（不许跳过缺失项）",
       (not ok2) and prob2, str(prob2))
    # 篡改副本内容
    bdir2 = sorted((tmp / "bk").iterdir())[-1]
    led_copy = bdir2 / "data__ledger.jsonl"
    led_copy.write_text(led_copy.read_text(encoding="utf-8") + "追加了一行\n",
                        encoding="utf-8")
    ok3, prob3, _ = B.verify(bdir2)
    ck("C3 副本被改动 ⇒ hash 不符 ⇒ 拒绝", (not ok3) and prob3, str(prob3))
    ck("C3b 理由点名是哪个文件、哪种不一致",
       any("ledger" in p and ("内容变了" in p or "sha256" in p) for p in prob3),
       str(prob3))
    try:
        B.restore(bdir2, root)
        ck("C4 校验不过 ⇒ restore 抛（不是尽力恢复）", False, "没抛")
    except B.BackupError as e:
        ck("C4 校验不过 ⇒ restore 抛（不是尽力恢复）", True)
        ck("C4b 异常原因逐条列出问题（不是一句「不可用」）",
           "校验不通过" in str(e), str(e)[:120])

    # ── D 组：反证 ────────────────────────────────────────────
    print()
    print("== D 组：反证 —— 短路校验后，坏备份必须被接受 ==")
    saved = B.verify
    try:
        B.verify = lambda p: (True, [], B.Manifest.from_dict(
            json.loads((Path(bdir2) / "manifest.json").read_text("utf-8"))))
        got = B.restore(bdir2, root)
        ck("D1 ⛔ 反证：校验短路 ⇒ 被改坏的备份**被恢复**"
           "（证明 C 组拦的是校验，不是别的）",
           "data/ledger.jsonl" in got["restored"], str(got["restored"]))
    except B.BackupError as e:
        ck("D1 ⛔ 反证：校验短路 ⇒ 被改坏的备份**被恢复**", False,
           f"仍被拒 ⇒ C 组可能是恒真的：{e}")
    finally:
        B.verify = saved
    try:
        B.restore(bdir2, root)
        ck("D2 还原后重新拒绝（事故版没残留）", False, "还原后竟放行")
    except B.BackupError:
        ck("D2 还原后重新拒绝（事故版没残留）", True)

    # ── E 组：可读性读数 ──────────────────────────────────────
    print()
    print("== E 组：可恢复性读数必须分清 source 与 projection ==")
    r = B.report(root)
    kinds = {f["rel"]: f["kind"] for f in r["files"]}
    ck("E1 账本被标成 source（丢了不可重建）",
       kinds.get("data/ledger.jsonl") == "source", str(kinds))
    ck("E2 SQLite 投影被标成 projection（可从账本 rebuild）",
       kinds.get("data/aether.db") == "projection", str(kinds))
    ck("E3 每个文件都报在不在 / 多大（不是一句看起来正常）",
       all(("exists" in f and "size" in f) for f in r["files"]))
    #: 备份目标**不含**通配：临时大文件不该被带走
    (root / "data" / "huge.tmp").write_text("x" * 1000, encoding="utf-8")
    m2 = B.create(root, tmp / "bk2")
    ck("E4 ⛔ 只带已知清单，不做目录通配（临时文件不被带走）",
       all("huge.tmp" not in f["rel"] for f in m2.files),
       str([f["rel"] for f in m2.files]))

    #: ── F 组：单文件归档（r35）────────────────────────────────
    #: ⛔ 刻意**复用** verify，不另造一份校验 —— "打包格式对不对"与
    #:    "备份内容对不对"是两件事，各有一份判据就够了。
    print("\n[F] 单文件归档（--archive）：解出来必须能被同样的闸接住")
    m3 = B.create(root, tmp / "bk3", note="归档测试")
    ck("F1 manifest 记了备份落在哪（path）",
       bool(m3.path) and Path(m3.path).is_dir(), m3.path)
    try:
        tar_p = B.archive(m3.path)
        ck("F2 打成单个 .tar.gz", tar_p.is_file() and tar_p.suffixes[-2:] ==
           [".tar", ".gz"], str(tar_p))
        ck("F2b 归档非空（空包 ⇒ 解压后什么都没有，却报打包成功）",
           tar_p.stat().st_size > 0, f"{tar_p.stat().st_size}B")
    except Exception as e:                                   # noqa: BLE001
        ck("F2 打成单个 .tar.gz", False, f"{type(e).__name__}: {e}")
        tar_p = None

    if tar_p is not None:
        ex_dir = Path(tempfile.mkdtemp(prefix="v6_unarc_"))
        got = B.unarchive(tar_p, ex_dir)
        ck("F3 解出来的目录里有 manifest.json",
           (got / "manifest.json").is_file(), str(got))
        ok_a, probs_a, m_a = B.verify(got)
        ck("F4 ⛔ 解出来的备份**过得了原有的闸**（复用 verify，不另造）",
           ok_a, f"{probs_a[:2]}")
        ck("F5 解压后 manifest 与原始逐字一致（打包没改内容）",
           m_a is not None and m_a.files == m3.files,
           "一致" if m_a and m_a.files == m3.files else "不一致")

        #: ⛔ 反证：改解压后副本里的一个字节 ⇒ verify 必须翻红。
        #:    否则"F4 通过"可能只是 verify 恒真。
        #: ⛔ kind 取值以 `BACKUP_TARGETS` 为准（"source"/"projection"），
        #:    不是 "ledger" —— 照猜写筛选条件会让反证组静默变空集。
        victim = [f for f in m3.files
                  if not f.get("missing") and f["rel"].endswith("ledger.jsonl")]
        if victim:
            vf = got / victim[0]["rel"].replace("/", "__")
            if vf.is_file():
                vf.write_text(vf.read_text(encoding="utf-8") + "\n",
                              encoding="utf-8")
                ok_b, probs_b, _ = B.verify(got)
                ck("F6 ⛔ 反证：动一个字节 ⇒ verify 必须翻红"
                   "（证明 F4 不是恒真）",
                   not ok_b and bool(probs_b), f"{probs_b[:1]}")
            else:
                ck("F6 ⛔ 反证：动一个字节 ⇒ verify 必须翻红", False,
                   f"找不到 {vf}")
        else:
            ck("F6 ⛔ 反证：动一个字节 ⇒ verify 必须翻红", False, "没有账本文件")

    #: ⛔ 路径穿越：归档是**数据**，不该被当成可信代码执行/落盘。
    print("\n[G] ⛔ 归档里的越界路径必须被拒")
    evil_dir = Path(tempfile.mkdtemp(prefix="v6_evil_"))
    evil_tar = evil_dir / "evil.tar.gz"
    payload = evil_dir / "payload.txt"
    payload.write_text("pwned", encoding="utf-8")
    with tarfile.open(evil_tar, "w:gz") as tf:
        tf.add(payload, arcname="../ESCAPED.txt")
    try:
        B.unarchive(evil_tar, evil_dir / "out")
        ck("G1 含 ../ 的归档 ⇒ 拒绝解包（不许落到 dest 之外）", False,
           "居然解包成功了")
    except B.BackupError as e:
        ck("G1 含 ../ 的归档 ⇒ 拒绝解包（不许落到 dest 之外）", True,
           str(e)[:60])
    except Exception as e:                                   # noqa: BLE001
        ck("G1 含 ../ 的归档 ⇒ 拒绝解包", False,
           f"抛的不是 BackupError：{type(e).__name__}: {e}")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   ✗ {f}")
        return 1
    print("✅ 备份契约全部通过：自带校验和、恢复前留当前、坏备份拒绝、"
          "source/projection 分账")
    return 0


if __name__ == "__main__":
    sys.exit(main())
