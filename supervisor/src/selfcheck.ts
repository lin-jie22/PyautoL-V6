/**
 * 监督器自检（含**跨语言一致性**验证）。
 *
 *   node src/selfcheck.ts
 *
 * 关键一项是 A 组：用 Python（`aether.ledger`）写一份账本，再用这里的
 * TypeScript 实现校验哈希链 —— 两端必须都判"完整"。
 * 这是"跨语言契约"是否存在漂移的**唯一**硬证据：只要 canonical 序列化
 * 有一处不一致（sort_keys / 紧凑分隔 / 数字 1.0 vs 1 / ts 精度），
 * 这一项立刻变红。
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { readLedger, verifyChain, canonicalJson, contentHash } from "./ledger.ts";
import { detect, exitCode } from "./anomaly.ts";

const FAILS: string[] = [];
function ck(name: string, cond: boolean, detail = ""): void {
  console.log(`  ${cond ? "PASS" : "FAIL"} ${name} ${cond ? "" : detail}`);
  if (!cond) FAILS.push(name);
}

const PY = process.env.V6_PY || "python3";
const ROOT = "/Users/linshaojie/V6";

function main(): number {
  const dir = mkdtempSync(join(tmpdir(), "v6_sup_"));

  // ── A 组：跨语言一致性（真跑 Python）─────────────────────────
  console.log("A 组：跨语言一致性（Python 写 → TypeScript 校）");
  const ledgerPath = join(dir, "cross.jsonl");
  const pyCode = `
import sys; sys.path.insert(0, ${JSON.stringify(ROOT)})
from aether.ledger import Ledger
L = Ledger(${JSON.stringify(ledgerPath)})
for i in range(6):
    L.append("cross.test", {"i": i, "f": 1.0, "zh": "判据", "nested": {"b": 2, "a": 1}}, actor="py")
print(L.head_hash())
`;
  let pyHash = "";
  try {
    pyHash = execFileSync(PY, ["-c", pyCode], { encoding: "utf8", cwd: ROOT }).trim();
  } catch (e) {
    ck("A0 Python 端可写账本", false, String(e).slice(0, 200));
  }
  if (pyHash) {
    const events = readLedger(ledgerPath);
    const r = verifyChain(events);
    ck("A1 TS 校验 Python 写的账本 ⇒ 链完整", r.ok, r.reason);
    ck("A2 事件数一致", events.length === 6, `${events.length}`);
    ck("A3 头部 hash 与 Python 一致",
       events[events.length - 1].hash === pyHash,
       `${events[events.length - 1].hash?.slice(0, 16)} vs ${pyHash.slice(0, 16)}`);
    ck("A4 整数值 float 两端写法一致（1.0 → 1）",
       canonicalJson({ a: 1.0 }) === '{"a":1}', canonicalJson({ a: 1.0 }));
    ck("A5 键排序一致", canonicalJson({ b: 1, a: 2 }) === '{"a":2,"b":1}');
    ck("A6 中文不转义", canonicalJson({ k: "判据" }).includes("判据"));
    ck("A7 hash 与 Python 同算法（sha256 of canonical）",
       contentHash({ a: 1 }).length === 64);
  }

  // ── B 组：篡改检测 ────────────────────────────────────────────
  console.log("B 组：篡改检测");
  const p2 = join(dir, "tamper.jsonl");
  const lines: string[] = [];
  for (let i = 0; i < 4; i++) {
    lines.push(JSON.stringify({ seq: i + 1, ts: 1700000000000 + i, kind: "k",
                                actor: "a", payload: { i }, prev_hash: "x", hash: "y" }));
  }
  writeFileSync(p2, lines.join("\n") + "\n");
  ck("B1 随手造的假账本 ⇒ 链必断", !verifyChain(readLedger(p2)).ok);

  // ── C 组：异常检测 ────────────────────────────────────────────
  console.log("C 组：异常检测");
  const empty = detect([]);
  ck("C1 空账本 ⇒ 告警（不是静默健康）",
     empty.some((f) => f.code === "EMPTY_LEDGER"));

  const hack = detect([{ seq: 1, ts: 1, kind: "eval.verdict", actor: "e",
                         payload: { task_id: "t1", reward: 1,
                                    verdict: { checks: [], evidence: {} } },
                         prev_hash: "", hash: "" }]);
  ck("C2 reward>0 但无 check ⇒ 判为 reward hacking",
     hack.some((f) => f.code === "REWARD_HACKING_SUSPECTED"),
     JSON.stringify(hack.map((f) => f.code)));

  const selfrep = detect([{ seq: 1, ts: 1, kind: "eval.verdict", actor: "e",
                            payload: { task_id: "t1", reward: 1,
                                       verdict: { checks: [{ name: "c", passed: true, evidence: "x" }],
                                                  evidence: { llm_score: 0.9 } } },
                            prev_hash: "", hash: "" }]);
  ck("C3 evidence 含 llm_score ⇒ 判为 reward hacking",
     selfrep.some((f) => f.code === "REWARD_HACKING_SUSPECTED"));

  const reg = detect([{ seq: 1, ts: 1, kind: "gate.result", actor: "g",
                        payload: { capsule: "c", regressions: ["t2: 1→0"] },
                        prev_hash: "", hash: "" }]);
  ck("C4 闸门判出回归 ⇒ critical", reg.some((f) => f.severity === "critical"));
  ck("C5 critical ⇒ 退出码 2", exitCode(reg) === 2);

  const okEv = detect([{ seq: 1, ts: 1, kind: "harness.commit", actor: "e",
                         payload: { gain: 0.2 }, prev_hash: "", hash: "" }]);
  ck("C6 正常提交 ⇒ 无 critical", !okEv.some((f) => f.severity === "critical"),
     JSON.stringify(okEv.map((f) => f.code)));

  // ── D 组：真实账本（如果 V6 跑过）─────────────────────────────
  console.log("D 组：真实账本");
  const real = join(ROOT, "data", "ledger.jsonl");
  try {
    const evs = readLedger(real);
    if (evs.length) {
      const r = verifyChain(evs);
      ck("D1 真实账本链完整", r.ok, r.reason);
      ck("D2 能读出事件", evs.length > 0);
      console.log(`      （${evs.length} 条事件，最新 kind=${evs[evs.length - 1].kind}）`);
    } else {
      console.log("  (跳过：真实账本为空)");
    }
  } catch {
    console.log("  (跳过：真实账本不存在)");
  }

  console.log("\n" + "=".repeat(60));
  if (FAILS.length) {
    console.log(`❌ ${FAILS.length} 项未通过：`);
    for (const f of FAILS) console.log(`   - ${f}`);
    return 1;
  }
  console.log("✅ 全部通过");
  return 0;
}

process.exit(main());
