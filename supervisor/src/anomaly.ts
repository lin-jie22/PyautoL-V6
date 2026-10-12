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

export function detect(events: Event[], cfg: AnomalyConfig = DEFAULT_CONFIG): Finding[] {
  const out: Finding[] = [];
  if (!events.length) {
    out.push({ code: "EMPTY_LEDGER", severity: "warn",
               detail: "账本为空：要么还没跑，要么写路径坏了（两者必须能区分）" });
    return out;
  }

  // ── 1. reward hacking：通过但证据可疑 ─────────────────────────
  const verdicts = events.filter((e) => e.kind === "eval.verdict");
  const suspicious: string[] = [];
  for (const ev of verdicts) {
    const p = payloadOf(ev);
    const v = (p.verdict ?? {}) as Record<string, any>;
    const reward = typeof p.reward === "number" ? p.reward : Number(v.reward ?? 0);
    if (reward > 0) {
      const checks = Array.isArray(v.checks) ? v.checks : [];
      if (checks.length === 0) {
        suspicious.push(`@${ev.seq} ${p.task_id}: reward>0 但没有任何 check`);
      }
      const evd = (v.evidence ?? {}) as Record<string, unknown>;
      for (const k of SELF_REPORT_KEYS) {
        if (k in evd) suspicious.push(`@${ev.seq} ${p.task_id}: evidence 含自述字段 ${k}`);
      }
      const cost = Number(v.cost_s ?? 0);
      if (cost > 0 && cost < cfg.minPlausibleCostS) {
        suspicious.push(`@${ev.seq} ${p.task_id}: 成本 ${cost}s 过低（判据真跑了吗）`);
      }
    }
  }
  if (suspicious.length) {
    out.push({ code: "REWARD_HACKING_SUSPECTED", severity: "critical",
               detail: `${suspicious.length} 条"通过"缺可信证据`,
               evidence: suspicious.slice(0, 8) });
  }

  // ── 2. 非回归闸：回归出现即为 critical ────────────────────────
  const gates = events.filter((e) => e.kind === "gate.result");
  const regressions = gates.filter((e) => {
    const p = payloadOf(e);
    return Array.isArray(p.regressions) && p.regressions.length > 0;
  });
  if (regressions.length) {
    out.push({ code: "REGRESSION_DETECTED", severity: "critical",
               detail: `${regressions.length} 次闸门判出回归（候选已被拦下，需查根因）`,
               evidence: regressions.slice(-5).map((e) =>
                 `@${e.seq} ${payloadOf(e).capsule}: ${(payloadOf(e).regressions as string[]).join(";")}`) });
  }

  // ── 3. 进化停滞：连续多轮无提交 ───────────────────────────────
  const commits = events.filter((e) => e.kind === "harness.commit");
  const noops = events.filter((e) => e.kind === "evolve.noop" || e.kind === "harness.revert");
  const lastCommitSeq = commits.length ? commits[commits.length - 1].seq : -1;
  const stall = noops.filter((e) => e.seq > lastCommitSeq).length;
  if (stall >= cfg.stallRounds) {
    out.push({ code: "EVOLUTION_STALLED", severity: "warn",
               detail: `距上次提交已有 ${stall} 轮无进展（可能是无弱点 ⇒ 正常收敛，也可能挖不到证据）`,
               evidence: noops.slice(-cfg.stallRounds).map((e) =>
                 `@${e.seq} ${payloadOf(e).reason ?? payloadOf(e).harness_id}`) });
  }

  // ── 4. 能力地板：长期 reward=0 ────────────────────────────────
  const recent = verdicts.slice(-20);
  if (recent.length >= 5) {
    const allZero = recent.every((e) => Number(payloadOf(e).reward ?? 0) === 0);
    if (allZero) {
      out.push({ code: "CAPABILITY_FLOOR", severity: "warn",
                 detail: `最近 ${recent.length} 条判定 reward 全为 0：任务太难 或 判据失效（两者必须区分）` });
    }
  }

  // ── 5. 产物增殖：体积涨了但没收益 ─────────────────────────────
  for (const ev of commits) {
    const p = payloadOf(ev);
    const blast = (p.blast ?? {}) as Record<string, number>;
    const before = Number(blast.size_delta ?? 0);
    const gain = Number(p.gain ?? 0);
    const sizeBefore = Number(p.baseline_reward ?? 0); // 占位，见下
    void sizeBefore;
    if (before > 0 && gain < cfg.minGainForGrowth) {
      out.push({ code: "ARTIFACT_PROLIFERATION", severity: "warn",
                 detail: `@${ev.seq} 提交使体积 +${before} 字节但增益仅 ${gain}（产物只增不减会让系统退化）`,
                 evidence: [`capsule=${p.capsule}`] });
    }
  }

  // ── 6. 泛化闸：判非 generalize 却仍提交 ⇒ 闸被绕过 ─────────────
  //     ⛔ 这条是**跨语言不变量**：Python 侧说"我拦下了"，监督器只读账本
  //     就能独立验证"确实没提交"。两侧用同一份事实、各自算一遍 ——
  //     任何一方绕过闸门都会在这里被发现，不需要信任对方的日志。
  const probes = events.filter((e) => e.kind === "gen.probe");
  for (const pe of probes) {
    const v = String(payloadOf(pe).verdict ?? "");
    if (v === "generalize") continue;
    const rn = payloadOf(pe).round_no;
    const bad = commits.filter((c) => payloadOf(c).round_no === rn && c.seq > pe.seq);
    if (bad.length) {
      out.push({
        code: "GENERALIZATION_GATE_BYPASSED", severity: "critical",
        detail: `@${pe.seq} 泛化闸裁决=${v}（round ${rn}），但同轮仍提交了 harness ⇒ 闸被绕过`,
        evidence: bad.map((c) => `@${c.seq} ${payloadOf(c).harness_id}`),
      });
    }
  }
  const overfit = probes.filter((e) => payloadOf(e).verdict === "overfit");
  if (overfit.length) {
    out.push({
      code: "OVERFIT_REJECTED", severity: "info",
      detail: `${overfit.length} 次候选在发展集上变好但未泛化 ⇒ 已拦下（闸在工作）`,
      evidence: overfit.slice(-3).map((e) => {
        const p = payloadOf(e);
        return `@${e.seq} ${p.capsule}: dev ${p.dev_gain} / held-out ${p.heldout_gain}`;
      }),
    });
  }

  // ── 7. 受保护层被自动改动：必须人工确认 ───────────────────────
  const blocked = events.filter((e) => e.kind === "gate.blocked");
  if (blocked.length) {
    out.push({ code: "PROTECTED_LAYER_TOUCHED", severity: "warn",
               detail: `${blocked.length} 次尝试改动受保护层（L0）已被阻断，等待人工审批`,
               evidence: blocked.slice(-3).map((e) => `@${e.seq} ${payloadOf(e).capsule}`) });
  }

  if (!out.length) {
    out.push({ code: "HEALTHY", severity: "info", detail: "未检测到异常信号" });
  }
  return out;
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
