#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：只读 API 层不得有「前缀判断 / 默认全开 / 无上限读取」。

## 为什么单独一组

`serve.py` 是**唯一对外暴露的内部状态面**（账本全文、harness 状态、记忆
正文、失败轨迹）。它不是"一个只读小服务"那么简单 —— 只读 ≠ 无隐私：
本机账本里躺着的是整个自演化系统的因果证据与记忆内容。

2026-10-13 逐条实测，发现四处全部与实际表现形式一致的问题：

| 项 | 实际表现 |
|---|---|
| 端点匹配 | `path.startswith("/api/status")` ⇒ `/api/status_extra` **也命中** |
| 静态文件  | `str(f).startswith(str(base))` ⇒ `/app/console_bak/x` 被当成 console 子路径 |
| CORS     | 硬编码 `Access-Control-Allow-Origin: *` ⇒ **任意网页**的 JS 能读本机账本 |
| limit    | `int(q["limit"])` 无保护 ⇒ `?limit=abc` 直接 500（异常被兜成 500 而不是 400） |
| `/raw`   | 整个账本 `.read_bytes()` ⇒ 内存峰值随历史线性增长（append-only） |

## 守的纪律

  A 组  端点按**路径边界**匹配（`/api/status_extra` 不得命中 `/api/status`）
  B 组  入参必须有明确失败形态（400，不是 500）
  C 组  静态文件按**路径分量**判断（兄弟前缀目录不得被当成子路径）
  D 组  CORS **默认关闭**；开启时回显 origin 而不是回 `*`
  E 组  `/api/ledger/raw` 有上限，超限明确 413 并给出替代路径
  F 组  两个事件端点共用**同一个**序列化函数（结构不得漂移）
  G 组  `memory_report` 的污染判据是**对称条件**（未声明 ⇒ 不得全算外来）
  反证  每条都要靠"还原旧写法 ⇒ 断言翻转"证明不是恒真

## ⛔ 诚实标注：哪几条其实不具判别力

把本测试对着 `git checkout HEAD -- aether/serve.py` 的旧版跑一遍（2026-10-13
实测），结果分三类：

- **真·会翻转**：A2/A3（200 而非 404）、B1/B2（500 而非 400）、D1/D4（`*`
  而非无）、E2（200 而非 413）。这些是旧代码确实错的地方。
- **假·两边都绿**：F1（两个端点的事件键集合旧版本就相同）、F2、F3
  （`Event` 是无 slots 的 frozen dataclass，slots 分支目前是**前瞻守卫**、
  没抓到过真 bug）、G1/G2 依赖 G3 的反证才成立。
- **因此补了 F5**：直接给 `_event_dict` 打标记，某端若自己抄一份序列化
  ⇒ 必转红。这才是"两端同一事实源"这条契约真正能被证伪的形态。

⛔ 不要把"两边都绿"的断言当成抓到了 bug —— 它是护栏，不是战果。

## 跑法

    python tests/contract_serve_test.py

⛔ 全程用 `http.client` 而**不用** `urllib`：后者会读 `HTTP_PROXY`，
   本机有代理环境变量 ⇒ 请求 127.0.0.1 会被代理劫持成 502（假故障）。
⛔ 全程用**临时目录**做 root 与动态端口：不得读写 V6 的生产状态文件。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aether.serve as S  # noqa: E402

FAILS: list = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL' if not cond else ''} "
          f"{'✓' if cond else '✗'} {name}"
          + ("" if cond else f"  ← {detail}"))
    if not cond:
        FAILS.append(name)


class _Server:
    """临时 root + 动态端口的真起服务（不是 mock）。"""

    def __init__(self, with_ledger: bool = True, cors: tuple = ()):
        self.dir = Path(tempfile.mkdtemp(prefix="v6serve_"))
        (self.dir / "data").mkdir()
        (self.dir / "console").mkdir()
        (self.dir / "console" / "index.html").write_text(
            "<h1>console</h1>", encoding="utf-8")
        #: 兄弟前缀目录：字符串前缀判断会把它当成 console 的子路径
        (self.dir / "console_bak").mkdir()
        (self.dir / "console_bak" / "secret.txt").write_text(
            "TOP-SECRET", encoding="utf-8")
        if with_ledger:
            from aether.ledger import Ledger
            led = Ledger(self.dir / "data" / "ledger.jsonl")
            for i in range(3):
                led.append("harness.gen", {"capsule": f"c{i}", "n": i})
        S.Handler.root = self.dir
        S.Handler.console_dir = self.dir / "console"
        S.Handler.cors_origins = tuple(cors)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever,
                                  daemon=True)
        self.t.start()

    def get(self, path: str, origin: str | None = None
            ) -> tuple[int, dict, dict]:
        """返回 (status, headers(dict, 小写键), parsed_body_or_{})。"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Origin": origin} if origin else {}
        try:
            c.request("GET", path, headers=hdrs)
            r = c.getresponse()
            body = r.read()
            hs = {k.lower(): v for k, v in r.getheaders()}
            try:
                parsed = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                parsed = {"_raw": body[:200].decode("utf-8", "replace")}
            return r.status, hs, parsed
        finally:
            c.close()

    def post(self, path: str, obj: Any) -> tuple[int, dict]:
        """POST JSON，返回 (status, parsed_body)。"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request("POST", path, body=json.dumps(obj).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            body = r.read()
            try:
                parsed = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                parsed = {"_raw": body[:200].decode("utf-8", "replace")}
            return r.status, parsed
        finally:
            c.close()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)


print("=" * 68)
print("A 组 · 端点按路径边界匹配（startswith 会把 _extra 也算命中）")

srv = _Server()
code, _, body = srv.get("/api/status")
ck("A1 /api/status 正常返回", code == 200 and "ledger" in body, f"{code}")
code, _, _ = srv.get("/api/status_extra")
ck("A2 /api/status_extra ⇒ 404（前缀判断会误命中 status）",
   code == 404, f"实际 {code}")
code, _, _ = srv.get("/api/events_backup")
ck("A3 /api/events_backup ⇒ 404（前缀判断会误命中 events）",
   code == 404, f"实际 {code}")
code, _, body = srv.get("/api/events?limit=2")
ck("A4 /api/events 正常返回且受 limit 约束",
   code == 200 and len(body.get("events", [])) == 2,
   f"{code} n={len(body.get('events', []))}")

#: 反证：把端点匹配退化成旧写法 ⇒ A2 必须从 404 翻成别的状态
_old_endpoint_is = S._endpoint_is
S._endpoint_is = lambda path, prefix: path.startswith(prefix)  # type: ignore
try:
    code2, _, _ = srv.get("/api/status_extra")
    ck("A5 反证：还原 startswith 写法 ⇒ /api/status_extra 不再是 404"
       "（否则 A2 是恒真断言）",
       code2 != 404, f"还原后仍 {code2}")
finally:
    S._endpoint_is = _old_endpoint_is
code, _, _ = srv.get("/api/status_extra")
ck("A6 反证已还原 ⇒ 恢复 404", code == 404, f"实际 {code}")
srv.close()

print()
print("B 组 · 入参有明确失败形态（400，不是被兜成 500）")

srv = _Server()
code, _, body = srv.get("/api/status?limit=abc")
ck("B1 limit=abc ⇒ 400（不是 500）", code == 400, f"实际 {code} {body}")
code, _, body = srv.get("/api/events?limit=abc")
ck("B2 /api/events?limit=abc ⇒ 400（不是 500）", code == 400, f"实际 {code}")
code, _, body = srv.get("/api/events?limit=-1")
#: ⛔⛔ 期望已改（原为"200 + 空列表"）：静默返回空列表是**把危险输入伪装
#:    成正常响应**。实测 `LIMIT -1` 在 SQLite 里是**无限制**（H6 反证），
#:    于是同一份响应里 `events=[]`（deque maxlen=0）而 `runs` 却是**全表**
#:    —— 两个列表语义相反，正是"同一份响应自相矛盾"的同形态问题。
#:    明确 400 才是对的：既拒绝，也说清为什么拒绝。
ck("B3 limit=-1 ⇒ 400 并说明原因（不是静默空列表）",
   code == 400 and "无限制" in json.dumps(body, ensure_ascii=False),
   f"{code} {body}")
srv.close()

print()
print("C 组 · 静态文件按路径分量判断（兄弟前缀目录不是子路径）")

srv = _Server()
code, _, body = srv.get("/console/index.html")
ck("C1 正常 console 文件能服务（修穿越不得修成全 404）",
   code == 200 and "console" in str(body), f"{code}")
code, _, _ = srv.get("/console/../console_bak/secret.txt")
ck("C2 兄弟前缀目录 secret.txt ⇒ 404"
   "（字符串前缀判断会把它当 console 子路径）",
   code == 404, f"实际 {code}")
code, _, _ = srv.get("/console/../../../etc/passwd")
ck("C3 向上穿越到 /etc/passwd ⇒ 404",
   code == 404, f"实际 {code}")

#: 反证：还原字符串前缀写法 ⇒ C2 必须能读到 secret
_old_is_within = S._is_within
S._is_within = (  # type: ignore
    lambda child, parent: str(child).startswith(str(parent)))
try:
    code2, _, body2 = srv.get("/console/../console_bak/secret.txt")
    leaked = "TOP-SECRET" in json.dumps(body2, ensure_ascii=False)
    ck("C4 反证：还原字符串前缀写法 ⇒ secret.txt 被读出"
       "（否则 C2 是恒真断言）",
       code2 == 200 or leaked, f"还原后 {code2}")
finally:
    S._is_within = _old_is_within
code, _, _ = srv.get("/console/../console_bak/secret.txt")
ck("C5 反证已还原 ⇒ 恢复 404", code == 404, f"实际 {code}")
srv.close()

print()
print("D 组 · CORS 默认关闭（只读 ≠ 无隐私）")

srv = _Server(cors=())
code, hs, _ = srv.get("/api/status", origin="https://evil.example")
ck("D1 默认配置 + 带 Origin ⇒ **不发** Access-Control-Allow-Origin",
   code == 200 and "access-control-allow-origin" not in hs,
   f"{code} acao={hs.get('access-control-allow-origin')}")
srv.close()

srv = _Server(cors=("https://good.example",))
code, hs, _ = srv.get("/api/status", origin="https://good.example")
ck("D2 白名单内 ⇒ 回显该 origin（不是回 *）",
   hs.get("access-control-allow-origin") == "https://good.example",
   str(hs.get("access-control-allow-origin")))
ck("D3 白名单命中时带 Vary: Origin（避免缓存串味）",
   "vary" in hs and "Origin" in hs["vary"], str(hs.get("vary")))
code, hs, _ = srv.get("/api/status", origin="https://evil.example")
ck("D4 白名单外 ⇒ 不发 ACAO",
   "access-control-allow-origin" not in hs,
   str(hs.get("access-control-allow-origin")))
code, hs, _ = srv.get("/api/status")
ck("D5 无 Origin 头 ⇒ 不发 ACAO",
   "access-control-allow-origin" not in hs,
   str(hs.get("access-control-allow-origin")))

#: 反证：伪造一个恒返回 * 的实现 ⇒ D1/D4 必须翻转
_old_cors = S.Handler._cors_headers
S.Handler._cors_headers = (  # type: ignore
    lambda self: {"Access-Control-Allow-Origin": "*"})
try:
    code2, hs2, _ = srv.get("/api/status", origin="https://evil.example")
    ck("D6 反证：还原旧写法（硬编码 *）⇒ 任意 origin 都拿到 ACAO"
       "（否则 D4 是恒真断言）",
       hs2.get("access-control-allow-origin") == "*",
       str(hs2.get("access-control-allow-origin")))
finally:
    S.Handler._cors_headers = _old_cors
code, hs, _ = srv.get("/api/status", origin="https://evil.example")
ck("D7 反证已还原 ⇒ 恢复不发送",
   "access-control-allow-origin" not in hs,
   str(hs.get("access-control-allow-origin")))
srv.close()

print()
print("E 组 · /api/ledger/raw 有上限（append-only ⇒ 无上限是线性增长）")

srv = _Server()
code, hs, body = srv.get("/api/ledger/raw")
ck("E1 小账本 raw 正常返回 ndjson",
   code == 200 and "x-ndjson" in (hs.get("content-type") or ""),
   f"{code} {hs.get('content-type')}")
old_max = S.MAX_RAW_BYTES
S.MAX_RAW_BYTES = 8  # 让任何非空账本都超限
try:
    code, _, body = srv.get("/api/ledger/raw")
    ck("E2 超过上限 ⇒ 413（不是把整个账本读进内存）",
       code == 413, f"实际 {code}")
    ck("E3 413 响应给出替代路径（否则调用方无从自救）",
       "limit" in str(body) and "hint" in str(body), str(body)[:120])
finally:
    S.MAX_RAW_BYTES = old_max
code, _, _ = srv.get("/api/ledger/raw")
ck("E4 上限复原 ⇒ 恢复 200", code == 200, f"实际 {code}")
srv.close()

print()
print("F 组 · 两个事件端点共用同一个序列化函数（结构不得漂移）")

srv = _Server()
_, _, status_body = srv.get("/api/status?limit=3")
_, _, events_body = srv.get("/api/events?limit=3")
keys_direct = sorted((status_body.get("events") or [{}])[0].keys())
keys_events = sorted((events_body.get("events") or [{}])[0].keys())
ck("F1 /api/status.events 与 /api/events.events **键集合相同**",
   bool(keys_direct) and keys_direct == keys_events,
   f"status={keys_direct} events={keys_events}")
ck("F2 事件里带 kind / payload（TS 侧监督器靠这两个字段）",
   {"kind", "payload"} <= set(keys_direct), str(keys_direct))


class _SlotsEvent:
    """`@dataclass(slots=True)` 的实例：没有 `__dict__`。"""

    __slots__ = ("kind", "payload")

    def __init__(self, kind: str, payload: dict) -> None:
        self.kind = kind
        self.payload = payload

    def to_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload}


d = S._event_dict(_SlotsEvent("x.y", {"a": 1}))
ck("F3 `_event_dict` 能吃下 slots dataclass（不会被 __dict__ 打 500）",
   d == {"kind": "x.y", "payload": {"a": 1}}, str(d)[:120])


class _NoToDict:
    def __init__(self) -> None:
        self.kind = "q.r"
        self.payload = {"b": 2}


ck("F4 `_event_dict` 退路能吃下普通对象",
   S._event_dict(_NoToDict()).get("kind") == "q.r",
   str(S._event_dict(_NoToDict()))[:120])

#: ⛔⛔ F1 是**不具判别力**的断言：实测把它对着 HEAD 旧版跑也绿（旧版
#:    `/api/status` 解 JSONL、`/api/events` 取 `__dict__`，键集合恰好相同）。
#:    它守的是"未来别漂"，守不住"现在漂了"。真正有判别力的是 F5 ——
#:    直接给序列化函数打标记，看两个端点会不会同时带上。
_marker = {"__v6_serve_probe__": 1}
_old_ed = S._event_dict
S._event_dict = lambda ev: dict(_marker)  # type: ignore
try:
    _, _, sb = srv.get("/api/status?limit=2")
    _, _, eb = srv.get("/api/events?limit=2")
    ns, ne = len(sb.get("events") or []), len(eb.get("events") or [])
    both_marked = (ns and ne
                   and all(e == _marker for e in sb["events"])
                   and all(e == _marker for e in eb["events"]))
    ck("F5 反证：给 `_event_dict` 打标记 ⇒ **两个端点同时**带上该标记"
       "（某端自己抄了一份序列化 ⇒ 这里必然转红）",
       both_marked,
       f"status={sb.get('events')} events={eb.get('events')}")
finally:
    S._event_dict = _old_ed
_, _, sb = srv.get("/api/status?limit=1")
ck("F6 反证已还原 ⇒ 恢复真实事件结构",
   set((sb.get("events") or [{}])[0]) == set(keys_direct),
   str(sb.get("events"))[:120])
srv.close()

print()
print("G 组 · 污染判据是对称条件（老快照未声明 ⇒ 不得全算外来）")

with tempfile.TemporaryDirectory() as td:
    old_snap = {"dim": 0, "embedder_id": "",
                "notes": [{"note_id": "n1", "tier": "L0",
                            "embedding": [0.1, 0.2],
                            "embedder": "legacy-x",
                            "content": "一段足够长的正常记忆正文，不含任何"
                                       "未验证标记，用来验证对称条件。"}]}
    p = Path(td) / "memory.json"
    p.write_text(json.dumps(old_snap, ensure_ascii=False), encoding="utf-8")
    rep = S.memory_report(p, ledger_events=None)
    ck("G1 未声明 embedder_id 的老快照 ⇒ foreign_embedder=0"
       "（不得反过来把所有条目算成外来）",
       rep.get("foreign_embedder") == 0, str(rep.get("foreign_embedder")))
    ck("G2 同上：dim 未声明 ⇒ wrong_dim=0",
       rep.get("wrong_dim") == 0, str(rep.get("wrong_dim")))
    #: 反证：缺了对称条件就会把 1 条正常条目算成外来
    asym = sum(1 for n in old_snap["notes"]
               if n["embedder"] != (rep.get("embedder_id") or ""))
    ck("G3 反证：非对称写法会把这条算成外来"
       "（证明 G1 拦住的是一个真会触发的分支）",
       asym == 1, f"非对称计数={asym}")

print("=" * 68)
print("H 组 · 列表端点不得有第二种无上限读取（limit 是第二个人口）")

#: ⛔ 自带实例：G 组末尾已经 `srv.close()`，复用会 ConnectionRefused
srv = _Server()

#: ⛔ E 组修的是 `/api/ledger/raw` 无上限。但**同一个洞换个入口还开着两次**：
#:    `/api/status?limit=N` 与 `/api/events?limit=N` 的 `N` 此前完全没有
#:    校验。更需要命中的是**负数**：SQLite 里 `LIMIT -1` == 无限制，
#:    所以 `limit=-1` 不是"取 0 条"，是"把整张表吐出来"（H6 实测反证）。
for ep in ("/api/status", "/api/events"):
    code, _, b = srv.get(f"{ep}?limit=-1")
    ck(f"H1 {ep}?limit=-1 ⇒ 400（负 limit 在 SQLite 里是**无限制**）",
       code == 400, f"{code} {b}")
    code, _, b = srv.get(f"{ep}?limit=99999")
    ck(f"H2 {ep}?limit=99999 ⇒ 400（超过 MAX_LIST_LIMIT="
       f"{S.MAX_LIST_LIMIT}）", code == 400, f"{code} {b}")
    code, _, b = srv.get(f"{ep}?limit=abc")
    ck(f"H3 {ep}?limit=abc ⇒ 400（不是 500）", code == 400, f"{code} {b}")

code, _, b = srv.get("/api/status?limit=2")
ck("H4 /api/status?limit=2 ⇒ 200 且 events ≤ 2",
   code == 200 and len(b.get("events", [])) <= 2,
   f"{code} {len(b.get('events', []))}")
code, _, b = srv.get("/api/events?limit=2")
ck("H5 /api/events?limit=2 ⇒ 200 且 events ≤ 2",
   code == 200 and len(b.get("events", [])) <= 2,
   f"{code} {len(b.get('events', []))}")

#: 反证：负 limit 在 SQLite 里真的是"全表"而不是"0 条" —— 否则 H1 是恒真
from aether.store import Store  # noqa: E402
with tempfile.TemporaryDirectory() as td2:
    st = Store(Path(td2) / "x.db")
    try:
        for i in range(3):
            st.save_snapshot(f"h{i}", {"i": i}, accepted=True, round_no=i)
        #: ⛔ 直接打原生 SQL：证明这个危险**真实存在**（不是想象出来的），
        #:    这也是"数据层必须也夹一道"的理由 —— HTTP 层的闸可以被绕过，
        #:    `LIMIT -1` 的语义不会。
        n_neg = len(st.conn.execute(
            "SELECT harness_id FROM harness_snapshots LIMIT -1").fetchall())
        ck("H6 反证：SQLite 原生 `LIMIT -1` 返回**全表**"
           "（证明 H1 拦的是真会触发的分支，不是恒真）",
           n_neg == 3 and len(st.snapshots(limit=1)) == 1,
           f"LIMIT -1 → {n_neg} 条, limit=1 → "
           f"{len(st.snapshots(limit=1))} 条")
        #: 深层防御：数据层入口自己也不许把负数传下去
        ck("H6b 深层防御：Store 的每个 limit 入口都夹住负数",
           len(st.snapshots(limit=-1)) == 0
           and len(st.runs(limit=-1)) == 0
           and len(st.run_ids(limit=-1)) == 0
           and len(st.run_digests(limit=-1)) == 0,
           f"snapshots={len(st.snapshots(limit=-1))} "
           f"runs={len(st.runs(limit=-1))} "
           f"ids={len(st.run_ids(limit=-1))} "
           f"digests={len(st.run_digests(limit=-1))}")
        #: 截断必须是"最后 N 条"，且**总数/是否截断**要如实报出去
        for i in range(3, 12):
            st.save_snapshot(f"h{i}", {"i": i}, accepted=True, round_no=i)
        got = st.snapshots(limit=3)
        ck("H7 snapshots(limit=3) 取**最后** 3 条",
           [g["harness_id"] for g in got] == ["h9", "h10", "h11"],
           str([g["harness_id"] for g in got]))
        ck("H8 判据不受响应截断影响：snapshot_ids() 仍是**全量**",
           len(st.snapshot_ids()) == 12, str(len(st.snapshot_ids())))
    finally:
        st.close()

#: 响应里必须带"截断了吗"，否则"谱系只有 7 个"会被当成全部
code, _, b = srv.get("/api/status?limit=200")
ck("H9 /api/status 带 snapshots_total 与 snapshots_truncated"
   "（切了就要说切了）",
   code == 200 and "snapshots_total" in b and "snapshots_truncated" in b,
   f"{code} {sorted(b)[:4]}")

srv.close()

print("=" * 68)
print("I 组 · 能力热装载：默认关闭 / 只收路径 / 换空不替换（r30）")

#: ⛔ 自带实例：H 组末尾已 `srv.close()`；且 I 组要动 `Handler.caps_hot`
srv = _Server()

import tempfile  # noqa: E402
_td = Path(tempfile.mkdtemp(prefix="v6hot_"))

def _mf(name: str, items: list) -> str:
    f = _td / name
    f.write_text(json.dumps({"capabilities": items}), encoding="utf-8")
    return str(f)

_GOOD = {"id": "i.only", "kind": "skill", "trusted": True,
         "inputs": {"text": {"type": "str", "required": True}},
         "outputs": {"out": {"type": "str", "required": True}},
         "impl": {"transport": "python",
                  "target": "scripts.ext_upper:upper"}}
_BAD = {"id": "i.bad", "kind": "nonsense", "inputs": {}, "outputs": {},
        "impl": {"transport": "python", "target": "x:y"}}

m_good = _mf("good.json", [_GOOD])
m_bad = _mf("bad.json", [_BAD])
m_partial = _mf("partial.json", [_GOOD, _BAD])

#: ⛔ 初始能力集必须**非空且不含 i.only**：`gateway=None` 时 invoke 返回的是
#:    503（配置问题，既有正确行为），而 I3 要证明的是"已装载但没这个能力
#:    ⇒ 422 unavailable"。若初始为空，I3 会假红而 I5 变成恒真。
from aether.interop import mount_capabilities  # noqa: E402
_INIT = {"id": "i.other", "kind": "skill", "trusted": True,
         "inputs": {"text": {"type": "str", "required": True}},
         "outputs": {"out": {"type": "str", "required": True}},
         "impl": {"transport": "python",
                  "target": "scripts.ext_upper:upper"}}
m_init = _mf("init.json", [_INIT])

#: ⛔ I1 是**反证组的核心**：默认必须关。若这条绿而开启后也能装载，
#:    才证明开关真在起作用（而不是"端点根本没接上"）。
S.Handler.caps_hot = False
S.Handler.caps_src = m_init
S.Handler.gateway, _ = mount_capabilities(m_init)
S.Handler.caps_reload_count = 0
code, b = srv.post("/api/capabilities/reload", {"manifest": m_good})
ck("I1 ⛔ 默认关闭 ⇒ 403（热装载=运行时改变能力集，不是默认能力）",
   code == 403 and b.get("hot_reload") is False, f"{code} {b}")

#: I2：不带 manifest 的行为必须**显式**，不能静默装个空表
S.Handler.caps_hot = True
_saved_src, S.Handler.caps_src = S.Handler.caps_src, ""
code, b = srv.post("/api/capabilities/reload", {})
ck("I2a 开闸后缺 manifest 且**无启动清单** ⇒ 400（不是静默装载空表）",
   code == 400, f"{code} {b}")
S.Handler.caps_src = _saved_src
code, b = srv.post("/api/capabilities/reload", {})
ck("I2b 缺 manifest 但**有启动清单** ⇒ 回退重载那一份（changed=false，"
   "不是偷偷换成别的）",
   code == 200 and b.get("path") == m_init and b.get("changed") is False,
   f"{code} {b}")

#: I3：热装载前能力不可达 —— 否则 I4 的"装载后可达"是恒真
code, b = srv.post("/api/invoke",
                   {"capability": "i.only", "args": {"text": "x"}})
ck("I3 对照组：装载前 i.only ⇒ unavailable（不是 ok）",
   code == 422 and b.get("status") == "unavailable", f"{code} {b.get('status')}")

#: I4：真装载 ⇒ 200，且 fingerprint 变了、记账 +1
#:    ⛔ 用**相对计数**（c0+1）而不是写死 1：I2b 也会 +1，写死会让后面
#:    每加一条用例就漂移一次 —— 断言要钉语义，不是钉当前序号。
c0 = S.Handler.caps_reload_count
code, b = srv.post("/api/capabilities/reload", {"manifest": m_good})
fp_after = b.get("fingerprint_after", "")
ck("I4 热装载 ⇒ 200 且能力集真的换了（changed=true, reload_count+1）",
   code == 200 and b.get("changed") is True
   and b.get("reload_count") == c0 + 1 and b.get("n_after") == 1,
   f"{code} {b}")

#: I5：装载后**真的能调**（可达性取证，不只是"报告说装载了"）
code, b = srv.post("/api/invoke",
                   {"capability": "i.only", "args": {"text": "hot"}})
ck("I5 装载后 i.only 真能调通（out=HOT）—— 不是只有装载报告说成功",
   code == 200 and (b.get("output") or {}).get("out") == "HOT",
   f"{code} {b.get('output')}")

#: I6：远程注入闸 —— 与 /api/invoke 拒带 impl 同一条纪律
code, b = srv.post("/api/capabilities/reload",
                   {"capabilities": [_GOOD], "impl": "os:system"})
ck("I6 ⛔ 内联能力描述 ⇒ 400（否则=远程注入实现进本进程）",
   code == 400 and "impl" in (b.get("rejected_keys") or []), f"{code} {b}")

#: I7：装载 0 条 ⇒ **不替换**（换空 = 能力凭空消失，比失败更难查）
code, b = srv.post("/api/capabilities/reload", {"manifest": m_bad})
ck("I7 ⛔ 装载 0 条 ⇒ 409 且保持原能力集（kept=装载前的 fingerprint）",
   code == 409 and b.get("kept") == fp_after, f"{code} {b}")
code, b = srv.post("/api/invoke",
                   {"capability": "i.only", "args": {"text": "still"}})
ck("I8 换空失败后 i.only 仍可调 ⇒ 证明真没被换成空表",
   code == 200 and (b.get("output") or {}).get("out") == "STILL",
   f"{code} {b.get('output')}")

#: I9：部分成功 ⇒ 207（只回 200 会让只看 HTTP 码的人漏掉 skipped）
code, b = srv.post("/api/capabilities/reload", {"manifest": m_partial})
ck("I9 ⛔ 部分成功 ⇒ 207（不是 200）—— 与「status=ok 掩盖落差」同源",
   code == 207 and b.get("status") == "partial"
   and (b.get("report") or {}).get("n_skipped") == 1, f"{code} {b}")

#: I10：路径不存在 ⇒ 404（不是"装载了个空表还报 200"）
code, b = srv.post("/api/capabilities/reload", {"manifest": str(_td / "x.json")})
ck("I10 manifest 不是文件 ⇒ 404", code == 404, f"{code} {b}")

#: I11：读数必须暴露"跑的是哪份清单"—— 否则热装载后靠猜
#:    ⛔ 计数**不是**"调了几次端点"：I7 的 409 没替换 ⇒ **不计数**
#:    （没换成就不算装载一次，否则读数虚高）。
c_now = S.Handler.caps_reload_count
code, _, b = srv.get("/api/capabilities")
ck("I11 /api/capabilities 暴露 source / hot_reload / reload_count "
   "（磁盘清单 ≠ 进程清单时靠这个读数）",
   code == 200 and b.get("source") == m_partial
   and b.get("hot_reload") is True and b.get("reload_count") == c_now,
   f"{code} src={b.get('source')} n={b.get('reload_count')}")

#: I12：两个入口同源 —— 同一份清单，CLI 与 HTTP 的 fingerprint 必须一致
_gw, _rep = mount_capabilities(m_partial)
code, _, b = srv.get("/api/capabilities")
ck("I12 ⛔ 同源对拍：CLI 与 HTTP 装载同一清单 ⇒ fingerprint 相同"
   "（两个入口各写一份装载 ⇒ 幽灵差异）",
   _gw.registry.fingerprint == b.get("fingerprint"),
   f"cli={_gw.registry.fingerprint} http={b.get('fingerprint')}")

import shutil as _sh  # noqa: E402

#: ⛔ 声明这份契约支持哪些模式（`aether check` 静态读这一行）：
#:    未声明 ≠ 只有一种模式 —— 前者是"还没接线"，报告里会被点名。
SUPPORTED_MODES = ("default",)
_sh.rmtree(_td, ignore_errors=True)
S.Handler.caps_hot = False
S.Handler.gateway = None
S.Handler.caps_src = ""
S.Handler.caps_reload_count = 0
srv.close()

print("=" * 68)
print("J 组 · 文件浏览/预览（`/api/files` `/api/file`）—— 只读，且只许看仓库内")

#: 为什么单独一组：这两个端点是"控制台能看文件"这条需求的实现，它们把
#: **文件系统**接到了 HTTP 上。只读端点的风险不是"被写坏"，是"被读穿"
#: —— 少一条约束就是任意文件读取。
srv = _Server()

_data = srv.dir / "data"          #: `_Server` 已经建好了
(srv.dir / "README.md").write_text("# hello\n", encoding="utf-8")
(_data / "aether.db").write_bytes(b"\x00\x01binary-not-previewable")
(srv.dir / "bad.md").write_bytes(b"\xff\xfe\x00 not utf8")     #: 后缀可预览，内容不是 UTF-8
(srv.dir / "big.md").write_text("x" * (S.MAX_FILE_BYTES + 1024), encoding="utf-8")
#: 三个"整棵跳过"的目录：列根目录时不得出现
for d in (".git", "node_modules", "__pycache__"):
    (srv.dir / d).mkdir(exist_ok=True)
    (srv.dir / d / "junk.txt").write_text("junk", encoding="utf-8")

#: J1 列根目录：200，`n` 跟随实际条目数
code, _, b = srv.get("/api/files?path=.")
ck("J1 /api/files 列根目录 200 且 n == len(entries)",
   code == 200 and b.get("n") == len(b.get("entries", [])), f"{code} {b.get('n')}")
#: J2 ⛔ `n` 不得是"目录里有多少"：超上限时必须 `truncated`，否则前端把
#:    "还有更多"读成"就这些"
many = srv.dir / "many"
many.mkdir()
for i in range(S.MAX_LIST_ENTRIES + 10):
    (many / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
code, _, b = srv.get("/api/files?path=many")
ck("J2 超 500 条 ⇒ truncated=True 且 n 跟随实际（不是目录总数）",
   code == 200 and b.get("truncated") is True
   and b.get("n") == S.MAX_LIST_ENTRIES
   and len(b.get("entries", [])) == S.MAX_LIST_ENTRIES,
   f"{code} n={b.get('n')} truncated={b.get('truncated')}")
#: J3 跳过名单**由服务端给出**（前端不许自己抄一份 ⇒ 改了一处另一处说谎）
code, _, b = srv.get("/api/files?path=.")
ck("J3 响应带 skipped_dirs（前端从这里渲染，不硬编码名单）",
   ".git" in (b.get("skipped_dirs") or [])
   and "node_modules" in (b.get("skipped_dirs") or []),
   f"{b.get('skipped_dirs')}")
names = [e["name"] for e in b.get("entries", [])]
ck("J4 跳过目录不出现在列举里（.git / node_modules / __pycache__）",
   not ({"\.git", "node_modules", "__pycache__"} & set(names)), f"{names}")

#: J5–J7 三条路径闸：**少一条就是任意文件读取**
code, _, b = srv.get("/api/files?path=../../..")
#: ⛔ 必须区分"被闸拦下"与"本来就不存在"：两者都可能是 4xx，但含义相反。
ck("J5 `..` 逃出仓库根 ⇒ 400（且说清是逃出，不是'不存在'）",
   code == 400 and "逃出" in json.dumps(b, ensure_ascii=False), f"{code} {b}")
code, _, _ = srv.get("/api/files?path=/etc")
ck("J6 绝对路径 ⇒ 400", code == 400, f"{code}")
code, _, _ = srv.get("/api/files?path=~/Library")
ck("J7 `~` ⇒ 400", code == 400, f"{code}")
#: J8 缺 path ⇒ 当作仓库根（不是 400）—— 这是**设计**，端点得能用
code, _, b = srv.get("/api/files")
ck("J8 缺 path ⇒ 按仓库根处理（200，不是报错）",
   code == 200 and b.get("path") == ".", f"{code} {b.get('path')}")
#: J9 列**文件**（不是目录）⇒ 400，别拿"空列表"糊过去
code, _, b = srv.get("/api/files?path=README.md")
ck("J9 对文件列目录 ⇒ 400（明确说'不是目录'）",
   code == 400 and "不是目录" in json.dumps(b, ensure_ascii=False), f"{code} {b}")

#: J10 正常预览 + sha256 必须与内容对得上（否则"看的是哪份"说不清）
code, _, b = srv.get("/api/file?path=README.md")
want = hashlib.sha256(b"# hello\n").hexdigest()
ck("J10 /api/file 文本预览 200 且 sha256 与内容一致",
   code == 200 and b.get("sha256") == want and b.get("bytes") == 8,
   f"{code} {b.get('sha256')}")
for label, p, want_code, hint in [
    ("J11 绝对路径 /etc/passwd", "/etc/passwd", 400, ""),
    ("J12 `..` 穿越", "../../etc/passwd", 400, ""),
    ("J13 二进制后缀（.db）", "data/aether.db", 415, "previewable"),
    ("J14 后缀可预览但内容非 UTF-8", "bad.md", 415, ""),
    ("J15 超 512KB", "big.md", 413, "hint"),
]:
    code, _, b = srv.get("/api/file?path=" + p.replace("/", "/"))
    ok = code == want_code and (not hint or hint in b)
    ck(f"{label} ⇒ {want_code}", ok, f"{code} {str(b)[:120]}")
#: J16 415 时必须给出"哪些能预览" —— 只说"不支持"等于让人猜
code, _, b = srv.get("/api/file?path=data/aether.db")
ck("J16 415 时列出可预览后缀（不给名单 = 让人猜）",
   ".md" in (b.get("previewable") or []), f"{b.get('previewable')}")

#: J17 ⛔ **符号链接逃逸**（`..` 与绝对路径都挡住了，软链是第三条路）：
#:    仓库里一个指向外部的软链本身不是漏洞，**跟随它**才是。
_out = Path(tempfile.mkdtemp(prefix="v6outside_"))
(out_file := _out / "secret.md").write_text("OUTSIDE-SECRET", encoding="utf-8")
os.symlink(out_file, srv.dir / "escape.md")
code, _, b = srv.get("/api/file?path=escape.md")
ck("J17 符号链接指向仓库外 ⇒ 400（先 resolve 再判，不是先拼后判）",
   code == 400, f"{code} {str(b)[:120]}")

#: J18 反证：把 `_safe_repo_path` 换成"先拼后判字符串前缀"的旧写法
#:    ⇒ 同一个软链请求必须**放行**（200 且读到仓库外内容）。
#:    ⛔ 没有这条，J17 可能是"请求本身就打不到那个分支"的恒绿。
_orig_safe = S._safe_repo_path


def _naive_safe(root, raw):                       # noqa: ANN001
    """旧写法：拼完直接判字符串前缀，既**不 normalize** 也**不 resolve**
    —— `..` 与软链都能穿过去。

    ⛔ 反证版必须形神兼备：这里若对 root 先 `resolve()`，macOS 上 `/var`
       是 `/private/var` 的软链 ⇒ 前缀判断自己就把请求挡了 ⇒ 反证假红
       （看着像"闸有效"，其实是"反证没造对"）。
    """
    from pathlib import Path as _P
    p = _P(root) / (raw or ".")
    if not str(p).startswith(str(_P(root))):
        return None, "逃出"
    return p, None


S._safe_repo_path = _naive_safe
try:
    code_naive, _, b_naive = srv.get("/api/file?path=escape.md")
finally:
    S._safe_repo_path = _orig_safe
ck("J18 反证：去掉 resolve ⇒ 同一软链请求读到仓库外内容（证明 J17 有牙）",
   code_naive == 200 and "OUTSIDE-SECRET" in b_naive.get("content", ""),
   f"{code_naive} {str(b_naive)[:120]}")

shutil.rmtree(_out, ignore_errors=True)
srv.close()

print("=" * 68)
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print("  ✗", f)
else:
    print("✅ API 层契约全部通过：无前缀判断、无默认全开、无无上限读取、热装载默认关闭")
sys.exit(1 if FAILS else 0)
