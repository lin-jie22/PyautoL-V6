#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：运维面（doctor 体检 + /api/health 探活）。

这两项此前都没有：

  · **doctor** —— 诊断能力散在 `status` / `verify-chain` / `rebuild --check`
    / `memory ...` 四个入口，没有一条命令能回答「现在健康吗"；而唯一的
    自检入口在 TS 侧，只跑 Python 的人拿不到一次成型的结论。
  · **/api/health** —— 最轻的端点 `/api/status` 也要全量扫账本（4 个
    聚合器）⇒ 拿它当 liveness，账本一大就把探活拖成超时 ⇒ 编排层
    （docker/k8s healthcheck）会重启一个其实是健康的服务。

本契约钉的核心是一条纪律：**"没查过" ≠ "查过没问题"**。
聚合器天然倾向于「没有坏消息就是好消息"，所以 skipped 必须显式、
必须带原因、且**不得计入通过**。

跑法（离线，秒级）：
    python tests/contract_ops_test.py
"""
import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)

#: ⛔⛔ **必须独占执行**：这份契约测的是**墙钟时间**（E6 比 health 与
#:    status 的耗时）。跟别的契约并发 ⇒ CPU 时间片被瓜分 ⇒ 量到的是
#:    **别人的负载**，判据失真；实测 4 路并发下由 113s 放大到 >600s 超时。
#:    ⇒ `aether check` 会把它排进串行段，不与任何契约同时跑。
EXCLUSIVE = True

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main():
    from aether import doctor as D
    from aether.ledger import Ledger
    from aether.serve import Handler

    tmp = Path(tempfile.mkdtemp(prefix="v6_ops_"))

    # ── A 组：空目录 —— 该 skipped 的必须 skipped，不冒充通过 ──────
    print("== A 组：没数据 ⇒ 记 skipped（未查 ≠ 通过）==")
    empty = tmp / "empty"
    (empty / "data").mkdir(parents=True)
    rep0 = D.run(empty)
    by = {c["name"]: c for c in rep0["checks"]}
    ck("A1 账本不存在 ⇒ skipped 而不是 ok",
       by["ledger.chain"]["status"] == D.SKIPPED,
       by["ledger.chain"]["status"])
    ck("A1b skipped 带原因（不然和「查过没问题」无从区分）",
       bool(by["ledger.chain"]["skipped_reason"]),
       str(by["ledger.chain"]))
    ck("A2 skipped 项也给 action（缺什么、该跑哪条命令）",
       bool(by["ledger.chain"]["action"]))
    ck("A3 ⛔ 没有备份 ⇒ 判 **fail**（唯一事实源无退路是坏消息，不是「没查」）",
       by["backup.present"]["status"] == D.FAIL,
       by["backup.present"]["status"])
    ck("A4 healthy=False（有 fail 就不许报健康）", rep0["healthy"] is False)
    ck("A5 报告里显式点名被跳过的项",
       "skipped" in rep0["note"] or "跳过" in rep0["note"], rep0["note"])

    # ── B 组：健康态 —— 造好数据后转 ok ──────────────────────────
    print()
    print("== B 组：数据齐了 ⇒ 转 ok，且备份项随备份动作翻转 ==")
    good = tmp / "good"
    (good / "data").mkdir(parents=True)
    led = Ledger(good / "data" / "ledger.jsonl")
    for i in range(3):
        led.append("t.e", {"i": i}, actor="t")
    rep1 = D.run(good)
    by1 = {c["name"]: c for c in rep1["checks"]}
    ck("B1 账本链完整 ⇒ ok", by1["ledger.chain"]["status"] == D.OK,
       str(by1["ledger.chain"]))
    ck("B2 仍因无备份判 fail（体检不因为「别的都好」就放过它）",
       by1["backup.present"]["status"] == D.FAIL)
    from aether import backup as B
    B.create(good, good / "backups")
    rep2 = D.run(good)
    by2 = {c["name"]: c for c in rep2["checks"]}
    ck("B3 备份之后 ⇒ 同一项转 ok（证明它不是恒红）",
       by2["backup.present"]["status"] == D.OK, str(by2["backup.present"]))
    ck("B4 此时 healthy=True", rep2["healthy"] is True, str(rep2["n_fail"]))
    ck("B5 exit_code 与 healthy 一致（脚本靠它判成败）",
       rep2["exit_code"] == 0 and rep1["exit_code"] == 1)

    # ── C 组：doctor 只读 + 一项崩了不连坐 ────────────────────────
    print()
    print("== C 组：体检不得改数据，也不得因一项崩掉而中断 ==")
    before = (good / "data" / "ledger.jsonl").read_text(encoding="utf-8")
    D.run(good)
    ck("C1 跑完账本逐字未变（doctor 只读，不修任何东西）",
       (good / "data" / "ledger.jsonl").read_text(encoding="utf-8") == before)

    def _boom(_root, _ctx):
        raise RuntimeError("检查自己炸了")
    rep3 = D.run(good, checks=[_boom, D._check_ledger])
    ck("C2 一项抛异常 ⇒ 记 fail 而不是让体检崩掉",
       rep3["checks"][0]["status"] == D.FAIL, str(rep3["checks"][0]))
    ck("C3 后续项照常跑完（崩的那项恰恰最可疑，但不能连坐别人）",
       rep3["checks"][1]["status"] == D.OK, str(rep3["checks"][1]))

    # ── D 组：反证 —— 把检查短路成恒 ok，坏数据也必须变绿 ──────────
    print()
    print("== D 组：反证 —— 短路后，坏数据必须被判健康（证明 B 组有判别力）==")
    bad = tmp / "bad"
    (bad / "data").mkdir(parents=True)
    (bad / "data" / "ledger.jsonl").write_text(
        '{"seq":1,"ts":1.0,"kind":"x","actor":"a","payload":{},'
        '"prev_hash":"cafe","hash":"dead"}\n', encoding="utf-8")
    rep_bad = D.run(bad)
    byb = {c["name"]: c for c in rep_bad["checks"]}
    ck("D0 前置：坏账本 ⇒ ledger.chain 判 fail（否则后面测不了）",
       byb["ledger.chain"]["status"] == D.FAIL, str(byb["ledger.chain"]))

    def _always_ok(_root, _ctx):
        return D.Check("ledger.chain", D.OK, "假装查过了")
    rep_ok = D.run(bad, checks=[_always_ok])
    ck("D1 ⛔ 反证：检查短路 ⇒ 坏数据被判健康"
       "（证明 B1/B2 拦的是检查，不是别的）",
       rep_ok["healthy"] is True and rep_ok["n_fail"] == 0)
    #: 还原：真检查回来必须重新转红
    rep_back = D.run(bad)
    ck("D2 换回真检查 ⇒ 重新判 fail（事故版没残留）",
       rep_back["checks"][0]["status"] == D.FAIL)

    # ── E 组：/api/health 必须轻 ─────────────────────────────────
    print()
    print("== E 组：探活端点必须 O(1)，不能拿 status 顶替 ==")
    Handler.api_token = ""
    Handler.root = good
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        r = _opener.open(base + "/api/health", timeout=5)
        h = json.loads(r.read().decode("utf-8"))
        ck("E1 /api/health 返回 200 且 ok", r.status == 200 and h.get("ok"))
        ck("E2 带 uptime（编排层要看它判要不要重启）",
           isinstance(h.get("uptime_s"), (int, float)))
        ck("E3 带鉴权状态（事后能查证服务上没上锁）",
           "auth_required" in h)
        ck("E4 ⛔ 不返回事件列表（返回了就不是 O(1)，探活会随账本变大变慢）",
           "events" not in h and "rounds" not in h, str(list(h.keys())))
        ck("E5 只做 stat：报告账本在不在、多大",
           h.get("checks", {}).get("ledger_present") is True,
           str(h.get("checks")))
        #: 轻的**证据**：health 比 status 快一个量级（同一服务、同一时刻）
        #: ⛔⛔ 账本必须**先放大**再比 —— 用 3 条事件的账本，两者都是
        #:    亚毫秒级（实测 1.8ms vs 0.9ms），差异完全淹没在冷启动噪声里
        #:    ⇒ 断言时红时绿。放大到 3000 条后实测：status 单次 115.9ms、
        #:    health 单次 0.3ms（385x）⇒ 差异才**可观测**。
        #:    （不是放宽判据：判据方向没变，变的是让信噪比足够。）
        import time as _time
        from aether.ledger import Ledger as _Ledger
        _led = _Ledger(str(Path(good) / "data" / "ledger.jsonl"))
        #: ⛔⛔ **必须用批量接口**：逐条 `append()` 每条都 fsync，
        #:    实测单次 34.5ms ⇒ 3000 条 = **103s**；套件 4 路并发下
        #:    多进程同时 fsync ⇒ 稳定放大到 >600s 超时（两次独立运行均
        #:    `timeout=2`）。`append_many()` 整批只同步一次，
        #:    落盘文件与逐条 append **逐字节相同**（hash 链不变），
        #:    只是中间态不保证 —— 这里是"构造基准账本"，可重放，安全。
        _led.append_many(
            [("probe.bench", {"i": _i, "pad": "x" * 80}) for _i in range(3000)],
            actor="ops-contract")
        _opener.open(base + "/api/health", timeout=20).read()      # 预热
        _opener.open(base + "/api/status?limit=5", timeout=30).read()

        t0 = _time.time()
        for _ in range(5):
            _opener.open(base + "/api/health", timeout=5).read()
        t_h = _time.time() - t0
        t1 = _time.time()
        _opener.open(base + "/api/status?limit=5", timeout=30).read()
        t_s = _time.time() - t1
        ck("E6 ⛔ 实测：5 次探活 < 1 次 status（探活不该随账本变慢）",
           t_h < t_s, f"health5={t_h*1000:.1f}ms status1={t_s*1000:.1f}ms")
        ck("E6b ⛔ 放大后差距要够大（>10x），否则信噪比不足 ⇒ E6 会时红时绿",
           t_s > t_h * 2 and t_s * 1000 > 10,
           f"status/health = {t_s/max(t_h, 1e-6):.1f}x")
    finally:
        srv.shutdown()

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   ✗ {f}")
        return 1
    print("✅ 运维契约全部通过：skipped 不是通过、doctor 只读、探活 O(1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
