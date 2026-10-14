/**
 * V6 监督器入口（Node ≥22 原生运行 TypeScript，无需构建）。
 *
 *   node src/index.ts --ledger /path/to/ledger.jsonl
 *   node src/index.ts --ledger ... --json          # 机器可读
 *   node src/index.ts --ledger ... --watch 30      # 每 30s 复查一次
 *
 * 退出码：0 健康 / 1 有 warn / 2 有 critical（CI 可直接吃）。
 *
 * ⛔ 监督器**只读**：它不写账本、不改任何状态。写只有一条路径
 * （Python 侧 ledger.append），否则监督者也会变成被监督对象。
 */
import { readLedgerIter, createChainVerifier, type Event,
         type ReadStats } from "./ledger.ts";
import { createDetector, summarize, exitCode, DEFAULT_CONFIG,
         type Finding } from "./anomaly.ts";
import { existsSync, statSync } from "node:fs";

interface Args {
  ledger: string;
  json: boolean;
  watch: number;
  quiet: boolean;
}

function parseArgs(argv: string[]): Args {
  const a: Args = { ledger: "", json: false, watch: 0, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--ledger") a.ledger = argv[++i] ?? "";
    else if (k === "--json") a.json = true;
    else if (k === "--quiet") a.quiet = true;
    else if (k === "--watch") a.watch = Number(argv[++i] ?? 0);
  }
  if (!a.ledger) {
    throw new Error("需要 --ledger <path>（V6 账本 JSONL 路径）");
  }
  return a;
}

/**
 * ⛔⛔ 2026-10-14：这里原来是 `readLedger(path)` 把**全量**事件读成数组
 * （1.72 MB 账本 ⇒ 堆增量 5.57 MB），再交给链校验与异常检测各扫一遍。
 *
 * 现在**一次遍历同时喂两个消费者**：链校验器与异常扫描器都是增量的
 * ⇒ 内存里同时只有一个块 + 当前行 + 扫描器的有界窗口。
 *
 * ⛔ 为什么不是"读两遍"（各拿一个 generator）：两次读之间账本可能被
 *    Python 侧追加 ⇒ 链校验与异常检测看到的不是同一个快照 ⇒ 会出现
 *    "链说完好、检测却在说另一份数据"这种无法解释的结论。一次遍历
 *    喂两个消费者，两边**必然**看到同一份事实。
 */
function inspect(path: string) {
  const stats: ReadStats = { lines: 0, bad: 0 };
  const chain = createChainVerifier();
  const detector = createDetector(DEFAULT_CONFIG);
  let n = 0;
  for (const ev of readLedgerIter(path, undefined, stats)) {
    n++;
    chain.push(ev);
    detector.push(ev);
  }
  const chainResult = chain.finish();
  const findings: Finding[] = detector.finish();
  if (!chainResult.ok) {
    findings.unshift({
      code: "LEDGER_CHAIN_BROKEN",
      severity: "critical",
      detail: `账本哈希链断裂 @seq=${chainResult.firstBadSeq}：${chainResult.reason}`,
    });
  }
  return { n, chain: chainResult, findings, stats: summarize(findings),
           read: stats };
}

function main(): number {
  const args = parseArgs(process.argv.slice(2));
  if (!existsSync(args.ledger)) {
    console.error(`账本不存在：${args.ledger}`);
    return 2;
  }

  const once = (): number => {
    const { n, chain, findings, stats, read } = inspect(args.ledger);
    const mtime = statSync(args.ledger).mtimeMs;
    const report = {
      ledger: args.ledger,
      mtime: new Date(mtime).toISOString(),
      events: n,
      read,                       // ⛔ 坏行数必须能被看见，不能静默吞掉
      chain_ok: chain.ok,
      chain_reason: chain.reason,
      findings,
      severity_counts: stats,
    };
    if (args.json) {
      console.log(JSON.stringify(report, null, 2));
    } else if (!args.quiet) {
      console.log(`V6 监督器 · 账本 ${args.ledger}`);
      console.log(`  事件 ${n} 条 | 哈希链 ${chain.ok ? "✅ 完整" : "❌ 断裂 " + chain.reason}`);
      for (const f of findings) {
        const tag = f.severity === "critical" ? "❌" : f.severity === "warn" ? "⚠️ " : "ℹ️ ";
        console.log(`  ${tag} [${f.code}] ${f.detail}`);
        for (const e of f.evidence ?? []) console.log(`      · ${e}`);
      }
      console.log(`  严重度统计：${JSON.stringify(stats)}`);
    }
    return exitCode(findings);
  };

  const rc = once();
  if (args.watch > 0) {
    setInterval(() => { once(); }, args.watch * 1000);
    console.log(`(watch 模式，每 ${args.watch}s 复查；Ctrl-C 退出)`);
    return 0;
  }
  return rc;
}

process.exit(main());
