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

import http.client
import json
import shutil
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
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
ck("B3 limit=-1 ⇒ 空列表（负数不得变成不封顶）",
   code == 200 and body.get("events") == [], f"{code} {body}")
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
if FAILS:
    print(f"❌ {len(FAILS)} 项未通过：")
    for f in FAILS:
        print("  ✗", f)
else:
    print("✅ 只读 API 层契约全部通过：无前缀判断、无默认全开、无无上限读取")
sys.exit(1 if FAILS else 0)
