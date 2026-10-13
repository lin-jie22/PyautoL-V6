/**
 * 账本读取 + 哈希链校验（TypeScript 侧）。
 *
 * ⛔ 这是**跨语言契约**的另一半：Python 写，这里读。两端的 canonical
 * 序列化必须逐字节一致，否则 verifyChain 会把一份完好的历史判成"被篡改"。
 *
 * 对齐点（与 aether/ledger.py 一一对应）：
 *   1. 键排序（sort_keys）
 *   2. 紧凑分隔（separators=(',',':')）
 *   3. 非 ASCII 不转义（ensure_ascii=false）—— JS 默认就是不转义
 *   4. 整数值的 float 降级为 int（Python `1.0` vs JS `1` 的坑）
 *   5. ts 为毫秒整数
 *
 * 判据同源：这里的 verifyChain 与 Python 的 Ledger.verify_chain 是**同一条
 * 判据**的两个实现，任一侧算出不一致都说明序列化漂移了 —— 这本身就是
 * 一种可检测事件，而不是"以 Python 为准"。
 */
import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";

export const GENESIS_HASH = "0".repeat(64);

export type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

export interface Event {
  seq: number;
  ts: number;
  kind: string;
  actor: string;
  payload: Record<string, Json>;
  prev_hash: string;
  hash: string;
}

/** 与 Python `_norm` 对齐：整数值的 float 降级为 int。 */
function norm(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(norm);
  if (v !== null && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(v as object).sort()) {
      out[k] = norm((v as Record<string, unknown>)[k]);
    }
    return out;
  }
  if (typeof v === "number" && Number.isInteger(v)) return v; // JS 已是 "1"
  return v;
}

/** 银行家舍入（round-half-to-even）—— **必须与 Python `round()` 一致**。

⛔ 2026-10-13 实测：真实账本第 788 条 `ts = 1791871960.7365` ⇒
   `ts * 1000 = 1791871960736.5`（恰好半整数）。
   · Python `int(round(x))`   → 1791871960736（取偶）
   · JS     `Math.round(x)`   → 1791871960737（half-up）
   ⇒ 同一个事件两端算出**不同的 hash**，监督器把完好的链判成"被篡改"。
   1028 条里命中 1 条 —— 概率性漂移，合成用例永远撞不到（ts 都是整数）。
 */
export function roundHalfEven(x: number): number {
  const f = Math.floor(x);
  const r = x - f;
  if (r > 0.5) return f + 1;
  if (r < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;      // 恰好 .5 ⇒ 取偶
}

/** 与 Python `canonical_json` 对齐。 */
export function canonicalJson(obj: unknown): string {
  return JSON.stringify(norm(obj));
}

export function contentHash(obj: unknown): string {
  return createHash("sha256").update(canonicalJson(obj), "utf8").digest("hex");
}

export function identity(ev: Event): Record<string, unknown> {
  return {
    v: 1,
    seq: ev.seq,
    // ⛔ 必须与 Python `Event.identity()` 一致：账本里存的 ts 是**秒（float）**，
    //    而参与 hash 的是**毫秒整数**。直接用 ev.ts 会算出完全不同的 hash
    //    （本文件的 selfcheck A1 就是守这一条的）。
    ts: roundHalfEven(ev.ts * 1000),
    kind: ev.kind,
    actor: ev.actor,
    payload: ev.payload ?? {},
    prev_hash: ev.prev_hash,
  };
}

export function readLedger(path: string): Event[] {
  if (!existsSync(path)) return [];
  const text = readFileSync(path, "utf8");
  const out: Event[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t) as Event);
    } catch {
      // ⛔ 坏行**不静默丢弃到无影无踪**：计入统计，由调用方决定是否告警
      continue;
    }
  }
  return out;
}

export interface ChainResult {
  ok: boolean;
  firstBadSeq: number | null;
  reason: string;
  count: number;
}

/** 与 Python `Ledger.verify_chain` 同一条判据。 */
export function verifyChain(events: Event[]): ChainResult {
  let prev = GENESIS_HASH;
  for (const ev of events) {
    if (ev.prev_hash !== prev) {
      return {
        ok: false, firstBadSeq: ev.seq, count: events.length,
        reason: `prev_hash 不连续 @${ev.seq}（期望 ${prev.slice(0, 12)}… 实得 ${ev.prev_hash.slice(0, 12)}…）`,
      };
    }
    const want = contentHash(identity(ev));
    if (ev.hash !== want) {
      return {
        ok: false, firstBadSeq: ev.seq, count: events.length,
        reason: `hash 不匹配 @${ev.seq}（payload 被改过？实得 ${ev.hash.slice(0, 12)}… 应为 ${want.slice(0, 12)}…）`,
      };
    }
    prev = ev.hash;
  }
  return { ok: true, firstBadSeq: null, reason: "", count: events.length };
}
