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
import { readLedger, verifyChain, type Event } from "./ledger.ts";
import { detect, summarize, exitCode, DEFAULT_CONFIG, type Finding } from "./anomaly.ts";
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

function inspect(path: string) {
  const events: Event[] = readLedger(path);
  const chain = verifyChain(events);
  const findings: Finding[] = detect(events, DEFAULT_CONFIG);
  if (!chain.ok) {
    findings.unshift({
      code: "LEDGER_CHAIN_BROKEN",
      severity: "critical",
      detail: `账本哈希链断裂 @seq=${chain.firstBadSeq}：${chain.reason}`,
    });
  }
  return { events, chain, findings, stats: summarize(findings) };
}

function main(): number {
  const args = parseArgs(process.argv.slice(2));
  if (!existsSync(args.ledger)) {
    console.error(`账本不存在：${args.ledger}`);
    return 2;
  }

  const once = (): number => {
    const { events, chain, findings, stats } = inspect(args.ledger);
    const mtime = statSync(args.ledger).mtimeMs;
    const report = {
      ledger: args.ledger,
      mtime: new Date(mtime).toISOString(),
      events: events.length,
      chain_ok: chain.ok,
      chain_reason: chain.reason,
      findings,
      severity_counts: stats,
    };
    if (args.json) {
      console.log(JSON.stringify(report, null, 2));
    } else if (!args.quiet) {
      console.log(`V6 监督器 · 账本 ${args.ledger}`);
      console.log(`  事件 ${events.length} 条 | 哈希链 ${chain.ok ? "✅ 完整" : "❌ 断裂 " + chain.reason}`);
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
