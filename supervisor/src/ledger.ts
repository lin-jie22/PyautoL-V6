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
import { openSync, readSync, closeSync, existsSync } from "node:fs";
import { StringDecoder } from "node:string_decoder";

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

/** 分块读取的块大小（默认 64 KiB）。测试会故意调小来打跨块边界。 */
export const READ_CHUNK_BYTES = 1 << 16;

/** 可选的读取统计：坏行数**必须能被看见**，不能被静默吞掉。 */
export interface ReadStats {
  lines: number;
  bad: number;
}

/**
 * **流式**逐条读账本：内存里同时只留一个块 + 当前行。
 *
 * ⛔⛔ 为什么不再 `readFileSync` + `split("\n")`：那样会同时持有
 *     **整份文本**和**整份行数组**，而它们只是为了 `JSON.parse` 一下就丢。
 *     2026-10-13 实测：1.72 MB 账本 ⇒ 堆增量 **5.57 MB**（≈3.2×）。
 *
 * ⛔⛔ 两个**必须**处理的流式的坑（不是"理论上"，是实测踩过的）：
 *   1. **跨块的多字节字符**：块边界可能落在某个 UTF-8 中文字符**中间**，
 *      直接 `buf.toString("utf8")` 会把它变成 U+FFFD ⇒ 那一行的内容被
 *      改写 ⇒ hash 校验把一条好事件判成"被篡改"。⇒ 必须用
 *      `StringDecoder`，它会在块之间保留不完整的字节序列。
 *   2. **跨块的行**：一块的末尾通常不是行尾 ⇒ 残留必须留到下一块再拼，
 *      不能当成一整行 parse。
 *
 * ⛔ 坏行（解析失败）**计入 `stats.bad` 并跳过**：账本里出现坏行意味着
 *     写入端有问题（半行 / 截断），监督器必须能报出来 —— 旧注释写着
 *     "计入统计，由调用方决定是否告警"，但旧代码只是 `continue`
 *     （注释与实现矛盾，比没注释更危险，与 `run_digests` 默认 limit 同形态）。
 */
export function* readLedgerIter(path: string,
                                chunkBytes: number = READ_CHUNK_BYTES,
                                stats?: ReadStats): Generator<Event> {
  if (!existsSync(path)) return;
  let fd: number;
  try {
    fd = openSync(path, "r");
  } catch {
    return;
  }
  try {
    // ⛔ 下限只防 0/负数（否则 `readSync` 退化为死循环），**不要**钳到
    //    1024：测试正是靠 7 字节块来把每一行切成十几段、把多字节字符
    //    切在中间 —— 钳大了那个测试就悄悄失效（实测踩过）。
    const buf = Buffer.allocUnsafe(Math.max(16, chunkBytes | 0));
    const dec = new StringDecoder("utf8");
    let pending = "";
    // ⛔ 必须是 `function*` 声明：箭头函数**不能**是 generator（会报
    //    ERR_INVALID_TYPESCRIPT_SYNTAX，看上去像 TS 的错，其实是 JS 语法）。
    function* emit(raw: string): Generator<Event> {
      const t = raw.trim();
      if (!t) return;
      if (stats) stats.lines++;
      try {
        // ⛔ 也不写 `yield JSON.parse(t) as Event`：Node 的 type-stripping
        //    在 yield 表达式里的 `as` 上同样报错（实测）。先落变量。
        const ev: Event = JSON.parse(t);
        yield ev;
      } catch {
        if (stats) stats.bad++;
      }
    }
    for (;;) {
      let n = 0;
      try {
        n = readSync(fd, buf, 0, buf.length, null);
      } catch {
        break;
      }
      if (n <= 0) break;
      pending += dec.write(buf.subarray(0, n));
      let idx = pending.indexOf("\n");
      while (idx >= 0) {
        const line = pending.slice(0, idx);
        pending = pending.slice(idx + 1);
        yield* emit(line);
        idx = pending.indexOf("\n");
      }
    }
    pending += dec.end();       // 冲刷 StringDecoder 里可能残留的字节
    yield* emit(pending);       // 最后一行（可能没有换行符）
  } finally {
    try {
      closeSync(fd);
    } catch {
      /* 忽略：读失败了也别把监督器打挂 */
    }
  }
}

/**
 * 读整份账本（**数组**形式，给需要多次 filter 的 `detect` 用）。
 *
 * ⛔ 它就是 `Array.from(readLedgerIter(...))` —— 不写第二份解析逻辑。
 * ⛔ 想省掉这份数组的人要注意：`anomaly.detect` 对同一批事件做**多遍**
 *    filter，所以它确实需要数组；真正被省掉的是"整份文本 + 整份行数组"。
 */
export function readLedger(path: string, stats?: ReadStats): Event[] {
  return Array.from(readLedgerIter(path, READ_CHUNK_BYTES, stats));
}

export interface ChainResult {
  ok: boolean;
  firstBadSeq: number | null;
  reason: string;
  count: number;
}

/**
 * 与 Python `Ledger.verify_chain` 同一条判据。
 *
 * ⛔ 参数是 `Iterable` 而不是 `Event[]`：校验只需要**顺序走一遍**，
 *    不该要求调用方先物化一份数组（数组是 `detect` 要的，不是这里要的）。
 *
 * ⛔ `count` 的语义随参数一起变了：**已校验的条数**，不再是"总条数"
 *    —— 对 `Iterable` 而言总数在读完前不可知，谎称总数就得先物化。
 *    ⇒ 校验中断时 `count` = 断在那一条之前校验过的条数（不是数组长度）。
 */
export function verifyChain(events: Iterable<Event>): ChainResult {
  let prev = GENESIS_HASH;
  let count = 0;
  for (const ev of events) {
    count++;
    if (ev.prev_hash !== prev) {
      return {
        ok: false, firstBadSeq: ev.seq, count,
        reason: `prev_hash 不连续 @${ev.seq}（期望 ${prev.slice(0, 12)}… 实得 ${ev.prev_hash.slice(0, 12)}…）`,
      };
    }
    const want = contentHash(identity(ev));
    if (ev.hash !== want) {
      return {
        ok: false, firstBadSeq: ev.seq, count,
        reason: `hash 不匹配 @${ev.seq}（payload 被改过？实得 ${ev.hash.slice(0, 12)}… 应为 ${want.slice(0, 12)}…）`,
      };
    }
    prev = ev.hash;
  }
  return { ok: true, firstBadSeq: null, reason: "", count };
}
