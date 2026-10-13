"""审计探针：held-out **没有难度跨度**时，泛化闸会不会假放行？

这不是功能测试，是**反证探针**：同一个候选、同一份行为（在 held-out 的
easy 层涨、hard 层**不涨** ⇒ 典型挑食），仅仅因为 held-out 少了 hard 层，
裁决会不会从「拦截」翻转成「放行」。

翻转成立 ⇒ 现在的 `generalize` 结论**依赖采样量**而非依赖候选行为，
即"闸在工作"是巧合（默认 --max-tasks=12 恰好采到了 hard），不是保证。

用法：python3 scripts/probe_spread_audit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether.generalization import GeneralizationGate, make_heldout  # noqa: E402
from aether.genome import Harness  # noqa: E402
from aether.nongression import FrozenTask  # noqa: E402
from aether.verifier import Check, Verdict  # noqa: E402

# 发展集：easy/hard 两层都有（候选在两层都涨 ⇒ 它不是"能力不够"）
DEV = {
    "dev::e1": "easy", "dev::e2": "easy",
    "dev::h1": "hard", "dev::h2": "hard",
}
# held-out 全套（有跨度）：easy 两层 + hard 两层
HO_FULL = {
    "ho::e1": "easy", "ho::e2": "easy",
    "ho::h1": "hard", "ho::h2": "hard",
}
# held-out 缺采样（无跨度）：**只有 easy 两层**，hard 那两层没被采到
HO_FLAT = {"ho::e1": "easy", "ho::e2": "easy"}

#: 候选在 held-out 上的表现（挑食）：easy 涨（0→1），hard 纹丝不动（0→0）
#: 发展集同层两层**都涨** ⇒ 按同层对照它属于"挑食"，不是"能力边界"。
CAND_PASS = {"ho::e1", "ho::e2"}
#: 发展集上候选两层都修好了（⇒ dev 同层增益都是 +1.0 ⇒ 判挑食）
DEV_CAND_PASS = set(DEV)


def _v(name: str, passed: bool) -> Verdict:
    return Verdict(passed=passed,
                   checks=(Check(name=name, passed=passed,
                                 evidence=f"stub {name} passed={passed}"),),
                   evidence={"stub": True})


def _runner(h: Harness, spec: dict) -> Verdict:
    tid = spec.get("task_id", "")
    is_cand = "cand" in h.meta_rules
    if tid.startswith("dev::"):
        # 基线：dev 两层都是 0（seed harness 修不好）；候选：两层都修好
        return _v(tid, is_cand and tid in DEV_CAND_PASS)
    # held-out：基线全 0；候选只在 easy 上修好
    return _v(tid, is_cand and tid in CAND_PASS)


def _tasks(mapping: dict) -> list:
    return [FrozenTask(task_id=t, spec={"task_id": t}) for t in mapping]


def _probe(ho_map: dict, tag: str) -> dict:
    base = Harness(meta_rules="base")
    cand = Harness(meta_rules="cand")
    gate = GeneralizationGate(
        make_heldout("HO", _tasks(ho_map), difficulty=ho_map),
        dev_task_ids=list(DEV))
    r = gate.probe(base, cand, _runner, dev_gain=1.0,
                   dev_by_difficulty={"easy": 1.0, "hard": 1.0})
    return {"tag": tag, "verdict": r.verdict, "blocks": r.blocks,
            "ho_gain": round(r.heldout_gain, 4),
            "by_difficulty": {k: round(v, 4) for k, v in r.by_difficulty.items()},
            "spread": gate.suite.spread, "reason": r.reason[:110]}


def main() -> int:
    full = _probe(HO_FULL, "held-out 有跨度（easy+hard 都采到）")
    flat = _probe(HO_FLAT, "held-out 无跨度（只采到 easy）")
    for r in (full, flat):
        print(f"\n── {r['tag']}")
        print(f"   spread={r['spread']}  verdict={r['verdict']}  "
              f"blocks={r['blocks']}  ho_gain={r['ho_gain']:+}")
        print(f"   by_difficulty={r['by_difficulty']}")
        print(f"   reason={r['reason']}")

    print("\n" + "=" * 68)
    flipped = (full["blocks"] is True) and (flat["blocks"] is False)
    print(f"同一候选 / 同一行为：有跨度={full['verdict']}(拦)  "
          f"无跨度={flat['verdict']}"
          f"({'放行' if not flat['blocks'] else '拦'})")
    print(f"结论：{'❌ 裁决被采样量翻转 ⇒ 假放行成立，缺口真实存在' if flipped else '✅ 裁决一致，缺口不成立'}")
    return 1 if flipped else 0


if __name__ == "__main__":
    sys.exit(main())
