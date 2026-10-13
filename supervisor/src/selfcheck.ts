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
import { readLedger, verifyChain, canonicalJson, contentHash,
         identity } from "./ledger.ts";
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

    // ── A8/A9：半整数毫秒边界（真实账本撞出来的漂移）──────────────
    //    ⛔ 合成用例的 ts 都是整数 ⇒ 永远撞不到 `ts*1000` 恰好 .5。
    //      真实账本 1028 条里命中 1 条（seq=788，ts=1791871960.7365）⇒
    //      Python 银行家舍入 vs JS half-up ⇒ 两端 hash 不同 ⇒ 监督器把
    //      完好的链判成"被篡改"。这条必须**用真实值**对拍，不能靠整数。
    const halfPath = join(dir, "half.jsonl");
    const halfTs = 1791871960.7365;      // *1000 = 1791871960736.5
    let pyHalf = "";
    try {
      pyHalf = execFileSync(PY, ["-c", `
import sys; sys.path.insert(0, ${JSON.stringify(ROOT)})
from aether.ledger import Ledger
L = Ledger(${JSON.stringify(halfPath)})
ev = L.append("half.test", {"i": 1}, actor="py", ts=${halfTs})
print(ev.hash)
`], { encoding: "utf8", cwd: ROOT }).trim();
    } catch (e) {
      ck("A8a Python 端可写半整数 ts", false, String(e).slice(0, 200));
    }
    if (pyHalf) {
      const hev = readLedger(halfPath);
      ck("A8 半整数毫秒 ⇒ TS 与 Python 算出同一个 hash（不是各算各的）",
         contentHash(identity(hev[0])) === pyHalf,
         `TS=${contentHash(identity(hev[0])).slice(0, 16)} PY=${pyHalf.slice(0, 16)}`);
      ck("A9 半整数毫秒 ⇒ 链仍完整（不误报篡改）",
         verifyChain(hev).ok, verifyChain(hev).reason);
      ck("A10 舍入取偶：...736.5 ⇒ ...736（不是 half-up 的 ...737）",
         identity(hev[0]).ts === 1791871960736,
         String(identity(hev[0]).ts));
    }
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

  // ── C7/C8：泛化闸的跨语言不变量 ───────────────────────────────
  //     Python 侧说"我拦下了 overfit 候选" ⇒ 账本里同轮**不该**有提交。
  //     监督器不读 Python 的任何日志，只看账本就能验证这件事。
  const ev = (seq: number, kind: string, payload: Record<string, unknown>) =>
    ({ seq, ts: seq, kind, actor: "t", payload, prev_hash: "", hash: "" });

  // ⛔ 构造必须与**真实 payload 同源**：生产里 probe 与 commit 都带
  //    `capsule`，事件都带 `sid`（会话归属）。少了任何一项，这里的
  //    "真绕过"用例测的就不是真实形态（实测踩过：缺 capsule 的用例
  //    把不同候选的合法提交判成绕过，而测试还全绿）。
  const bypassed = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "narrow", verdict: "overfit",
                         dev_gain: 1, heldout_gain: 0, sid: "s1" }),
    ev(2, "harness.commit", { round_no: 1, capsule: "narrow",
                              harness_id: "h1", gain: 1, sid: "s1" }),
  ]);
  ck("C7 同会话同候选：判 overfit 却仍提交 ⇒ critical（闸被绕过）",
     bypassed.some((f) => f.code === "GENERALIZATION_GATE_BYPASSED"
                          && f.severity === "critical"),
     JSON.stringify(bypassed.map((f) => f.code)));
  ck("C7b 真绕过 ⇒ 退出码 2（CI 能吃到）", exitCode(bypassed) === 2,
     String(exitCode(bypassed)));

  const rejected = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "narrow", verdict: "overfit",
                         dev_gain: 1, heldout_gain: 0, sid: "s1" }),
    ev(2, "harness.revert", { round_no: 1, reason: "generalization_gate",
                              sid: "s1" }),
  ]);
  ck("C8 拦下即无提交 ⇒ 不报 bypass（只有真绕过才 critical）",
     !rejected.some((f) => f.code === "GENERALIZATION_GATE_BYPASSED")
     && rejected.some((f) => f.code === "OVERFIT_REJECTED"),
     JSON.stringify(rejected.map((f) => f.code)));

  // ── C9–C11：误报形态（实测在真实账本上抓到的三种）──────────────
  //    这三组**必须不报 critical**，否则这条规则就是狼来了。
  const otherCap = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "narrow", verdict: "overfit",
                         dev_gain: 1, heldout_gain: 0, sid: "s1" }),
    ev(2, "harness.commit", { round_no: 1, capsule: "broad",
                              harness_id: "h1", gain: 1, sid: "s1" }),
  ]);
  ck("C9 反证：被拦的是 A、提交的是 B ⇒ 不判绕过（闸在工作）",
     !otherCap.some((f) => f.code === "GENERALIZATION_GATE_BYPASSED"),
     JSON.stringify(otherCap.map((f) => f.code)));

  const crossRun = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "narrow", verdict: "overfit",
                         dev_gain: 1, heldout_gain: 0, sid: "s1" }),
    ev(2, "harness.commit", { round_no: 1, capsule: "narrow",
                              harness_id: "h9", gain: 1, sid: "s2" }),
  ]);
  ck("C10 反证：不同会话的同号 round ⇒ 不判绕过（round_no 会重置）",
     !crossRun.some((f) => f.code === "GENERALIZATION_GATE_BYPASSED"),
     JSON.stringify(crossRun.map((f) => f.code)));

  const legacy = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "narrow", verdict: "overfit",
                         dev_gain: 1, heldout_gain: 0 }),
    ev(2, "harness.commit", { round_no: 1, capsule: "narrow",
                              harness_id: "h1", gain: 1 }),
  ]);
  ck("C11 反证：旧账本缺 sid ⇒ 不硬判绕过，但必须报不可判定（warn）",
     !legacy.some((f) => f.code === "GENERALIZATION_GATE_BYPASSED")
     && legacy.some((f) => f.code === "GATE_BYPASS_UNVERIFIABLE"
                           && f.severity === "warn"),
     JSON.stringify(legacy.map((f) => f.code)));

  const sameRunOtherRound = detect([
    ev(1, "gen.probe", { round_no: 2, capsule: "narrow", verdict: "overfit",
                         dev_gain: 1, heldout_gain: 0, sid: "s1" }),
    ev(2, "harness.commit", { round_no: 1, capsule: "narrow",
                              harness_id: "h1", gain: 1, sid: "s1" }),
  ]);
  ck("C12 反证：同会话但不同轮 ⇒ 不判绕过",
     !sameRunOtherRound.some((f) => f.code === "GENERALIZATION_GATE_BYPASSED"),
     JSON.stringify(sameRunOtherRound.map((f) => f.code)));

  // ── C14/C15：闸的闸 —— 无判别力的 held-out 不许放行 ─────────────
  //     `discriminative === false` = 这套 held-out 只有单一难度层，它无法
  //     区分「真泛化」与「只会修一类题」。此时任何 `generalize` 都是实现
  //     自相矛盾（Python 侧 `_downgrade_if_flat` 保证不输出）。
  //     ⛔ 用 `=== false` 而非 falsy：`null`/undefined 是合法的"未打标签"。
  const flatPass = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "narrow", verdict: "generalize",
                         dev_gain: 1, heldout_gain: 1,
                         discriminative: false, sid: "s1" }),
  ]);
  ck("C14 discriminative=false 却判 generalize ⇒ critical（闸自身失效）",
     flatPass.some((f) => f.code === "PASS_WITHOUT_DISCRIMINATION"
                          && f.severity === "critical"),
     JSON.stringify(flatPass.map((f) => f.code)));
  ck("C14b 闸失效 ⇒ 退出码 2（CI 能吃到）", exitCode(flatPass) === 2,
     String(exitCode(flatPass)));

  // ⛔ 反证：三态不能被合并。`generalize` + `discriminative: true` 是
  //    **正常放行**；`null`（没打标签）是"未测"，两者都**不许**报。
  //    不写这条，把判据放宽成 `!discriminative` 也能全绿 ⇒ 反证失效。
  const goodPass = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "broad", verdict: "generalize",
                         dev_gain: 1, heldout_gain: 1,
                         discriminative: true, sid: "s1" }),
  ]);
  ck("C15 反证：有判别力（true）的放行 ⇒ 不报（不是闸失效）",
     !goodPass.some((f) => f.code === "PASS_WITHOUT_DISCRIMINATION"),
     JSON.stringify(goodPass.map((f) => f.code)));
  const unlabeled = detect([
    ev(1, "gen.probe", { round_no: 1, capsule: "broad", verdict: "generalize",
                         dev_gain: 1, heldout_gain: 1,
                         discriminative: null, sid: "s1" }),
  ]);
  ck("C15b 反证：未打标签（null）= 没测过 ⇒ 不报（不可判别 ≠ 有罪）",
     !unlabeled.some((f) => f.code === "PASS_WITHOUT_DISCRIMINATION"),
     JSON.stringify(unlabeled.map((f) => f.code)));

  // ── C13：Python 侧写出的事件必须带会话归属（判据的数据来源）──────
  let sidOk = false, sidDetail = "";
  try {
    const p = join(dir, "sid.jsonl");
    execFileSync(PY, ["-c", `
import sys; sys.path.insert(0, ${JSON.stringify(ROOT)})
from aether.ledger import Ledger
L = Ledger(${JSON.stringify(p)}, session="sess-abc")
L.append("t.a", {"x": 1}); L.append("t.b", {"x": 2})
print(L.session)
`], { encoding: "utf8", cwd: ROOT });
    const es = readLedger(p);
    sidOk = es.length === 2 && es.every((e) => (e.payload as any)?.sid === "sess-abc");
    sidDetail = JSON.stringify(es.map((e) => (e.payload as any)?.sid));
  } catch (e) {
    sidDetail = String(e).slice(0, 200);
  }
  ck("C13 反证：Python 写入的事件带 sid（否则跨运行判据无数据来源）",
     sidOk, sidDetail);

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
