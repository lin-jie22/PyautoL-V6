/**
 * 异常检测：监督器不看"系统说它很好"，只看**账本里的事实**。
 *
 * 2026 的 self-harness 综述点名的失败模式，这里逐条做成可检测信号：
 *   · catastrophic forgetting / regression  → 冻结闸失败率上升
 *   · reward hacking                        → 通过但证据可疑（自述字段、
 *                                             有 reward 没 check、成本异常低）
 *   · capability floor                      → 长期 reward=0，进化原地踏步
 *   · artifact proliferation                → harness 体积只增不减、提交无增益
 *   · 进化停滞                              → 连续多轮 revert/noop
 *
 * ⛔ 设计取舍：检测规则**只吃账本**，不调用 Python、不看模型输出。
 *    监督器必须能独立于被监督系统运行 —— 否则它会被同一批 bug 带偏。
 */
import type { Event } from "./ledger.ts";

export type Severity = "info" | "warn" | "critical";

export interface Finding {
  code: string;
  severity: Severity;
  detail: string;
  evidence?: string[];
}

export interface AnomalyConfig {
  /** 连续多少轮无提交即判停滞 */
  stallRounds: number;
  /** reward 长期为 0 的容忍轮数 */
  zeroRewardRounds: number;
  /** 单次判定成本低于此秒数且通过 ⇒ 可疑（判据可能没真跑） */
  minPlausibleCostS: number;
  /** harness 体积增长超过此比例而收益低于阈值 ⇒ 增殖 */
  growthRatio: number;
  minGainForGrowth: number;
}

export const DEFAULT_CONFIG: AnomalyConfig = {
  stallRounds: 3,
  zeroRewardRounds: 3,
  minPlausibleCostS: 0.001,
  growthRatio: 0.5,
  minGainForGrowth: 0.05,
};

const SELF_REPORT_KEYS = [
  "self_reported", "claimed_success", "agent_says", "self_score",
  "llm_score", "model_judged", "claimed_passed", "self_eval",
];

function payloadOf(ev: Event): Record<string, any> {
  return (ev.payload ?? {}) as Record<string, any>;
}

// ── 流式扫描器 ──────────────────────────────────────────────────
// ⛔⛔ 2026-10-14 边界兑现审计（`scripts/probe_boundary_audit.py` D 组）：
//     旧签名 `detect(events: Event[])` 要求调用方先把**全量**账本物化成
//     数组（实测 1.72 MB 账本 ⇒ 堆增量 5.57 MB），而函数内部又
//     `events.filter(...)` 了 **9 次** ⇒ 9 遍全量扫描 + 9 份中间数组。
//
//     改成 push/finish 的增量扫描器之后：
//       · 账本**不再需要物化**（`readLedgerIter` 边读边喂）；
//       · 只遍历 **1 遍**；
//       · 只留下后面真会用到的那几条（有界窗口）+ 精简记录。
//
//     ⛔ 它**仍然不是 O(1)**，这是问题的固有性质而不是实现偷懒：
//       第 6 段要判断"这个被闸拦下的候选后来是不是又被提交了"，
//       probes 与 commits 两侧都得看完才知道 ⇒ 这部分必须留
//       O(#commits + #probes) 的**精简记录**（不含 payload 全对象）。
//       把这条硬说成 O(1) 就是假仪表 —— 与 `rlimits_applied` 同一个坑。
//
// ⛔⛔ **计数与 evidence 必须分开存**（2026-10-14 对拍抓到的，5 处全部中招）：
//     `detail` 里写的 `${arr.length}` 是**真实总数**（如"37 组不可判定"），
//     而 `evidence` 只展示前/后几条。把两者合成一个"有界数组" ⇒ 计数被
//     窗口截断 ⇒ 文本从 37 变成 5 —— 那不是省内存，是**改了读数**。
//     ⇒ 一律 `xxxCount++`（真计数）+ `xxxEvidence`（有界窗口）。
const RECENT_N = 20;      // 第 4 段只看最近 20 条判定
const EVID_N = 8;         // evidence 上限
const TAIL_N = 5;         // 取末尾的 evidence 上限
const TAIL_3 = 3;

/** commit 的精简记录：只留判据要读的字段（不用留整个 payload）。 */
interface CommitRec {
  seq: number; harness_id: string; sid: string; capsule: string;
  round_no: unknown; size_delta: number; gain: number;
}
/** probe 的精简记录。 */
interface ProbeRec {
  seq: number; verdict: string; disc: unknown;
  round_no: unknown; capsule: string; sid: string;
  dev_gain: unknown; heldout_gain: unknown;   // OVERFIT_REJECTED 的 evidence 要读
}

function bounded<T>(arr: T[], x: T, n: number): void {
  arr.push(x);
  if (arr.length > n) arr.shift();
}

export interface Detector {
  push(ev: Event): void;
  finish(): Finding[];
}

export function createDetector(cfg: AnomalyConfig = DEFAULT_CONFIG): Detector {
  let n = 0;
  let suspCount = 0;
  const suspicious: string[] = [];      // 有界 EVID_N（evidence 用）
  const recent: Event[] = [];          // 有界 RECENT_N（第 4 段）
  let regCount = 0;
  const regressions: Event[] = [];     // 有界 TAIL_N
  const commits: CommitRec[] = [];
  const noopSeqs: number[] = [];       // 计数用：只需 seq（数字，不存对象）
  const noops: Event[] = [];           // 有界 cfg.stallRounds（evidence 用）
  const probes: ProbeRec[] = [];
  const attrOk = new Set<string>();
  let attrFailedCount = 0;
  const attrFailed: Event[] = [];      // 有界 TAIL_3
  let blockedCount = 0;
  const blocked: Event[] = [];         // 有界 TAIL_3
  let overfitCount = 0;
  const overfit: ProbeRec[] = [];      // 有界 TAIL_3
  let unattributableCount = 0;
  const unattributable: string[] = []; // 有界 TAIL_N（evidence）
  let unverifiableCount = 0;
  const unverifiable: string[] = [];   // 有界 TAIL_N（evidence）
  let flatPassCount = 0;
  const flatPass: string[] = [];       // 有界 TAIL_N（evidence）

  function push(ev: Event): void {
    n++;
    const p = payloadOf(ev);
    switch (ev.kind) {
      case "eval.verdict": {
        bounded(recent, ev, RECENT_N);
        // ── 1. reward hacking（只保留前 EVID_N 条 ⇒ 与原 slice(0,8) 等价）
        const v = (p.verdict ?? {}) as Record<string, any>;
        const reward = typeof p.reward === "number" ? p.reward : Number(v.reward ?? 0);
        if (reward > 0) {
          const note = (s: string): void => {
            suspCount++;                       // ⛔ 真计数（detail 用它）
            if (suspicious.length < EVID_N) suspicious.push(s);
          };
          const checks = Array.isArray(v.checks) ? v.checks : [];
          if (checks.length === 0) {
            note(`@${ev.seq} ${p.task_id}: reward>0 但没有任何 check`);
          }
          const evd = (v.evidence ?? {}) as Record<string, unknown>;
          for (const k of SELF_REPORT_KEYS) {
            if (k in evd) {
              note(`@${ev.seq} ${p.task_id}: evidence 含自述字段 ${k}`);
            }
          }
          const cost = Number(v.cost_s ?? 0);
          if (cost > 0 && cost < cfg.minPlausibleCostS) {
            note(`@${ev.seq} ${p.task_id}: 成本 ${cost}s 过低（判据真跑了吗）`);
          }
        }
        break;
      }
      case "gate.result": {
        if (Array.isArray(p.regressions) && p.regressions.length > 0) {
          regCount++;
          bounded(regressions, ev, TAIL_N);
        }
        break;
      }
      case "harness.commit": {
        const blast = (p.blast ?? {}) as Record<string, number>;
        commits.push({
          seq: ev.seq,
          harness_id: String(p.harness_id ?? ""),
          sid: String(p.sid ?? ""),
          capsule: String(p.capsule ?? ""),
          round_no: p.round_no,
          size_delta: Number(blast.size_delta ?? 0),
          gain: Number(p.gain ?? 0),
        });
        break;
      }
      case "evolve.noop":
      case "harness.revert": {
        noopSeqs.push(ev.seq);
        bounded(noops, ev, Math.max(1, cfg.stallRounds));
        break;
      }
      case "gen.probe": {
        const verdict = String(p.verdict ?? "");
        const rec: ProbeRec = {
          seq: ev.seq, verdict,
          disc: p.discriminative,
          round_no: p.round_no,
          capsule: String(p.capsule ?? ""),
          sid: String(p.sid ?? ""),
          dev_gain: p.dev_gain, heldout_gain: p.heldout_gain,
        };
        probes.push(rec);
        if (verdict === "overfit") {
          overfitCount++;
          bounded(overfit, rec, TAIL_3);
        }
        break;
      }
      case "attribution.layer":
      case "attribution.unavailable": {
        attrOk.add(String(p.harness_id ?? ""));
        if (ev.kind === "attribution.unavailable") {
          attrFailedCount++;
          bounded(attrFailed, ev, TAIL_3);
        }
        break;
      }
      case "gate.blocked": {
        blockedCount++;
        bounded(blocked, ev, TAIL_3);
        break;
      }
      default:
        break;
    }
  }

  function finish(): Finding[] {
    const out: Finding[] = [];
    if (n === 0) {
      out.push({ code: "EMPTY_LEDGER", severity: "warn",
                 detail: "账本为空：要么还没跑，要么写路径坏了（两者必须能区分）" });
      return out;
    }

    // ── 1. reward hacking ──────────────────────────────────────
    if (suspCount) {
      out.push({ code: "REWARD_HACKING_SUSPECTED", severity: "critical",
                 detail: `${suspCount} 条"通过"缺可信证据`,
                 evidence: suspicious.slice(0, EVID_N) });
    }

    // ── 2. 非回归闸：回归出现即为 critical ──────────────────────
    if (regCount) {
      out.push({ code: "REGRESSION_DETECTED", severity: "critical",
                 detail: `${regCount} 次闸门判出回归（候选已被拦下，需查根因）`,
                 evidence: regressions.slice(-TAIL_N).map((e) =>
                   `@${e.seq} ${payloadOf(e).capsule}: ${(payloadOf(e).regressions as string[]).join(";")}`) });
    }

    // ── 3. 进化停滞：连续多轮无提交 ────────────────────────────
    const lastCommitSeq = commits.length ? commits[commits.length - 1].seq : -1;
    const stall = noopSeqs.filter((s) => s > lastCommitSeq).length;
    if (stall >= cfg.stallRounds) {
      out.push({ code: "EVOLUTION_STALLED", severity: "warn",
                 detail: `距上次提交已有 ${stall} 轮无进展（可能是无弱点 ⇒ 正常收敛，也可能挖不到证据）`,
                 evidence: noops.slice(-cfg.stallRounds).map((e) =>
                   `@${e.seq} ${payloadOf(e).reason ?? payloadOf(e).harness_id}`) });
    }

    // ── 4. 能力地板：长期 reward=0 ─────────────────────────────
    if (recent.length >= 5) {
      const allZero = recent.every((e) => Number(payloadOf(e).reward ?? 0) === 0);
      if (allZero) {
        out.push({ code: "CAPABILITY_FLOOR", severity: "warn",
                   detail: `最近 ${recent.length} 条判定 reward 全为 0：任务太难 或 判据失效（两者必须区分）` });
      }
    }

    // ── 5. 产物增殖：体积涨了但没收益 ──────────────────────────
    for (const c of commits) {
      if (c.size_delta > 0 && c.gain < cfg.minGainForGrowth) {
        out.push({ code: "ARTIFACT_PROLIFERATION", severity: "warn",
                   detail: `@${c.seq} 提交使体积 +${c.size_delta} 字节但增益仅 ${c.gain}（产物只增不减会让系统退化）`,
                   evidence: [`capsule=${c.capsule}`] });
      }
    }

    // ── 6. 泛化闸：判非 generalize 却仍提交 ⇒ 闸被绕过 ──────────
    //     ⛔ 这条是**跨语言不变量**：Python 侧说"我拦下了"，监督器只读账本
    //     就能独立验证"确实没提交"。两侧用同一份事实、各自算一遍 ——
    //     任何一方绕过闸门都会在这里被发现，不需要信任对方的日志。
    for (const pe of probes) {
      const v = pe.verdict;
      // ⛔ **闸的闸**：`discriminative === false` 的意思是"这套 held-out 没有
      //    难度跨度 ⇒ 它无法区分真泛化与挑食"。此时**任何** `generalize`
      //    都是实现自相矛盾（Python 侧 `_downgrade_if_flat` 保证不会输出它）。
      //    出现即说明闸被绕过或判据被改坏 ⇒ critical，不能当普通放行读。
      //    注意用 `=== false` 而不是 falsy：`null`（未打标签）是合法的"未测"。
      if (pe.disc === false && v === "generalize") {
        flatPassCount++;
        if (flatPass.length < TAIL_N) {
          flatPass.push(`@${pe.seq} verdict=generalize 但 discriminative=false`
            + `（capsule=${pe.capsule || "?"}）`
            + ` ⇒ 无判别力的 held-out 却放行了，闸自身失效`);
        }
      }
      if (v === "generalize") continue;
      const rn = pe.round_no;
      if (!pe.capsule) {
        unattributableCount++;
        if (unattributable.length < TAIL_N) {
          unattributable.push(`@${pe.seq} probe 未记 capsule ⇒ 无法归因到具体候选`);
        }
      }
      // ① 同一个候选才算：A 被拦、B 被放行并提交 —— 那是闸在工作，不是绕过。
      //    ⛔ 少了这条，实测把一批合法提交判成 critical（狼来了）。
      const matches: CommitRec[] = [];
      for (const c of commits) {
        if (c.seq <= pe.seq || c.round_no !== rn) continue;
        if (pe.capsule && c.capsule && c.capsule !== pe.capsule) continue;
        matches.push(c);
      }
      if (!matches.length) continue;

      // ② 同一次运行才算：`round_no` **每个进程都从 1 重新数**，跨小时的
      //    两次运行会撞出同一个 round 1。⛔ 归属未知时**不许**当成"同一次
      //    运行"硬判 —— 那是把无法判定的东西判成已证明（实测 8 条全假）。
      if (pe.sid) {
        const bad = matches.filter(
          (c) => !c.sid || c.sid === pe.sid);
        if (bad.length) {
          out.push({
            code: "GENERALIZATION_GATE_BYPASSED", severity: "critical",
            detail: `@${pe.seq} 泛化闸裁决=${v}（round ${rn}，capsule=${pe.capsule || "?"}），`
              + `同一次运行内该候选仍被提交 ⇒ 闸被绕过`,
            evidence: bad.map((c) => `@${c.seq} ${c.harness_id}`),
          });
        }
      } else {
        // 旧账本（sid 引入之前）：无法区分"跨运行串味"与"真绕过"。
        // ⛔ 不静默跳过、也不降级成 info —— 规则对它们**失效**必须可见。
        for (const c of matches) {
          unverifiableCount++;
          if (unverifiable.length < TAIL_N) {
            unverifiable.push(`@${pe.seq}(${v},${pe.capsule || "?"}) → @${c.seq} `
              + `${c.harness_id}：缺会话归属，无法判定是否同一运行`);
          }
        }
      }
    }
    if (unverifiableCount) {
      out.push({
        code: "GATE_BYPASS_UNVERIFIABLE", severity: "warn",
        detail: `${unverifiableCount} 组"裁决非 generalize 却见同候选提交"缺会话`
          + `归属 ⇒ 无法判定是跨运行串味还是真绕过（新事件已带 sid，可精判）`,
        evidence: unverifiable.slice(0, TAIL_N),
      });
    }
    if (flatPassCount) {
      out.push({
        code: "PASS_WITHOUT_DISCRIMINATION", severity: "critical",
        detail: `${flatPassCount} 条放行裁决来自**没有判别力**的 held-out`
          + ` ⇒ 闸自身失效（放行结论不可信，与挑食候选被放过完全同形）`,
        evidence: flatPass.slice(0, TAIL_N),
      });
    }
    if (unattributableCount) {
      out.push({
        code: "PROBE_UNATTRIBUTABLE", severity: "warn",
        detail: `${unattributableCount} 条泛化判定缺 capsule ⇒ 绕过检测对它们失效`
          + `（不静默跳过，也不硬凑归因）`,
        evidence: unattributable.slice(0, TAIL_N),
      });
    }
    if (overfitCount) {
      out.push({
        code: "OVERFIT_REJECTED", severity: "info",
        detail: `${overfitCount} 次候选在发展集上变好但未泛化 ⇒ 已拦下（闸在工作）`,
        evidence: overfit.slice(-TAIL_3).map((e) =>
          `@${e.seq} ${e.capsule}: dev ${e.dev_gain} / held-out ${e.heldout_gain}`),
      });
    }

    // ── 6.5 层归因：有没有 / 挂没挂 ─────────────────────────────
    //     ⛔ "哪一层真正起作用"是唯一能回答"这次提升来自哪里"的证据。
    //     它挂了却只写在内存报告里 ⇒ 账本上看不见 ⇒ 下一次仍在盲改。
    const modern = commits.filter((c) => c.sid !== "");
    const noAttr = modern.filter((c) => !attrOk.has(c.harness_id));
    if (noAttr.length) {
      out.push({ code: "ATTRIBUTION_MISSING", severity: "warn",
                 detail: `${noAttr.length} 次提交后没有层归因 ⇒ 这次提升来自`
                   + `哪一层没有证据（只统计带会话归属的新事件）`,
                 evidence: noAttr.slice(-TAIL_3).map(
                   (c) => `@${c.seq} ${c.harness_id}`) });
    }
    if (attrFailedCount) {
      out.push({ code: "ATTRIBUTION_FAILED", severity: "warn",
                 detail: `${attrFailedCount} 次层归因报错 ⇒ 已记账，不再静默`,
                 evidence: attrFailed.slice(-TAIL_3).map(
                   (e) => `@${e.seq} ${String(payloadOf(e).error ?? "").slice(0, 80)}`) });
    }

    // ── 7. 受保护层被自动改动：必须人工确认 ─────────────────────
    if (blockedCount) {
      out.push({ code: "PROTECTED_LAYER_TOUCHED", severity: "warn",
                 detail: `${blockedCount} 次尝试改动受保护层（L0）已被阻断，等待人工审批`,
                 evidence: blocked.slice(-TAIL_3).map((e) => `@${e.seq} ${payloadOf(e).capsule}`) });
    }

    if (!out.length) {
      out.push({ code: "HEALTHY", severity: "info", detail: "未检测到异常信号" });
    }
    return out;
  }

  return { push, finish };
}

export function detect(events: Iterable<Event>,
                       cfg: AnomalyConfig = DEFAULT_CONFIG): Finding[] {
  const d = createDetector(cfg);
  for (const ev of events) d.push(ev);
  return d.finish();
}

export function summarize(findings: Finding[]): Record<string, number> {
  const acc: Record<string, number> = { info: 0, warn: 0, critical: 0 };
  for (const f of findings) acc[f.severity] = (acc[f.severity] ?? 0) + 1;
  return acc;
}

/** 退出码约定：0=健康，1=有 warn，2=有 critical（CI 可直接吃）。 */
export function exitCode(findings: Finding[]): number {
  if (findings.some((f) => f.severity === "critical")) return 2;
  if (findings.some((f) => f.severity === "warn")) return 1;
  return 0;
}
