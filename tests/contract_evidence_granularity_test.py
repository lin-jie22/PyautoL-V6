#!/usr/bin/env python3
"""契约 · 给提议器的证据粒度（r62）。

═══ 守什么 ═══

  A `coarse_dict()` 里**不出现任何检查名**，但保留 topic / 类别 / 数量
  B `to_dict()`（默认 exact 口径）仍然带检查名 ⇒ 默认行为零改动
  C 反证：把 `coarse_dict` 换成 `to_dict`（模拟"漏改"）⇒ A 必须翻红
  D 接线：`--evidence coarse` 真的传到 config；`run_config.evidence` 记 coarse，
    而 `weakness.mined` 账本事件**仍记原文**（判据不粗化）
  E 反证：同一份代码跑 `--evidence exact` ⇒ run_config 记 exact（开关不是恒量）

⛔ 为什么这把尺子重要：`exact` 下模型把 7 个检查名逐字抄进技能正文 ⇒
   dev 0/5→5/5、held-out 0.0→0.0 ⇒ 被判 overfit。检查名是**发展集**的信息，
   等于让模型抄答案。而粗化**只能**粗化给模型的那一份 —— 账本必须留原文，
   否则事后无法回答"它当时到底看到了什么"。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

SUPPORTED_MODES = ("default",)

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILED: list = []
PY = sys.executable

#: 生产上真实出现过的那 7 个检查名（10-20 那次 overfit 的原件）。
NAMES = ["clamp_low", "clamp_high", "clamp_mid", "normalize_basic",
         "normalize_zero_total", "top_n_basic", "hollow_empty"]


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'}" + (f" {name}" if ok else f" **{name}**")
          + (f"  —— {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


from aether.evolution import (EvolutionConfig, Weakness,  # noqa: E402
                             _failure_mode)


def _w() -> Weakness:
    return Weakness(
        kind="missing_skill", topic="textscore",
        task_ids=["textscore::t0", "textscore::t1", "textscore::t2"],
        evidence=["checks_failed:" + ",".join(NAMES[:3]),
                  "checks_failed:" + ",".join(NAMES[3:5]),
                  "checks_failed:" + ",".join(NAMES[5:]),
                  "Traceback (most recent call last): boom"],
        n_failed=4)


def main() -> int:
    w = _w()
    coarse = w.coarse_dict()
    blob = json.dumps(coarse, ensure_ascii=False)

    print("A 组：粗化版里不许出现检查名")
    ck("A1 ⛔ 7 个检查名**一个都不出现**",
       not any(n in blob for n in NAMES), blob[:200])
    ck("A2 也不许出现 `checks_failed:` 这个原始前缀",
       "checks_failed" not in blob, blob[:200])
    ck("A3 但类别与数量必须保留（粗化 ≠ 清空）",
       coarse.get("failure_modes") == {"behavior_mismatch": 3, "crash": 1}
       and coarse.get("n_failing_checks") == 7,
       json.dumps(coarse.get("failure_modes"), ensure_ascii=False))
    ck("A4 topic / 失败题数仍可见（模型得知道在哪个环境、几道题）",
       coarse.get("topic") == "textscore" and coarse.get("n_failed") == 4)
    ck("A5 带显式粒度标记（下游可断言，不靠猜）",
       coarse.get("evidence_granularity") == "coarse")

    print("\nB 组：默认 exact 口径零改动")
    exact = w.to_dict()
    eblob = json.dumps(exact, ensure_ascii=False)
    ck("B1 exact 仍带检查名（历史口径没被偷偷改掉）",
       all(n in eblob for n in NAMES[:3]), eblob[:160])
    ck("B2 exact 没有新的粒度字段（旧读取方不受影响）",
       "evidence_granularity" not in exact)
    ck("B3 默认配置是 exact",
       EvolutionConfig().evidence == "exact", EvolutionConfig().evidence)

    print("\nC 组：反证 —— 把 coarse 换成 exact（模拟漏改）⇒ A 必须翻红")
    orig = Weakness.coarse_dict
    try:
        Weakness.coarse_dict = Weakness.to_dict          # type: ignore[assignment]
        leaked = json.dumps(_w().coarse_dict(), ensure_ascii=False)
        ck("C1 ⛔ 漏改时 A1 会翻红（说明 A1 不是恒真）",
           any(n in leaked for n in NAMES), leaked[:160])
    finally:
        Weakness.coarse_dict = orig                      # type: ignore[assignment]

    print("\nC2 失败模式映射是分类型的（不是把一切都叫 behavior）")
    ck("C2 `checks_failed:` ⇒ behavior_mismatch",
       _failure_mode("checks_failed:a,b") == "behavior_mismatch")
    ck("C3 `ImportError` ⇒ import_failed（配置问题，不是能力缺口）",
       _failure_mode("ModuleNotFoundError: no module named x") == "import_failed")
    ck("C4 `timeout` ⇒ timeout", _failure_mode("timeout 3s") == "timeout")
    ck("C5 认不出的 ⇒ unknown（不许硬塞进某个类别）",
       _failure_mode("something weird") == "unknown")

    print("\nD 组：接线 —— 参数真的传到 config，账本仍记原文")
    #: ⛔ 两次跑的**重量不同**，这是刻意的：coarse 那次要真的挖出弱点
    #:    （D3 要断言 `weakness.mined` 记原文）⇒ 得给足够的题；
    #:    exact 那次只用来证明"开关不是恒量"⇒ 只读 run_config ⇒ 越小越好。
    #:    两份契约一次跑两遍满配 evolve 会顶破 `aether check` 的 600s 预算
    #:    （实测 8m46s）—— 第 119 条：反证也必须便宜。
    PLAN = {"coarse": ("5", "1"), "exact": ("2", "1")}
    outs = {}
    for mode in ("coarse", "exact"):
        mt, gs = PLAN[mode]
        td = tempfile.mkdtemp(prefix=f"v6ev_{mode}_")
        ws = pathlib.Path(td)
        (ws / "tasks").symlink_to(ROOT / "tasks")
        (ws / "data").mkdir(exist_ok=True)
        p = subprocess.run(
            [PY, "-B", "-m", "aether.cli", "evolve", "--root", str(ws),
             "--rounds", "1", "--group", gs, "--max-tasks", mt,
             "--evidence", mode],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        rows = []
        lg = ws / "data" / "ledger.jsonl"
        if lg.exists():
            for line in lg.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        outs[mode] = (p, rows, ws)

    p, rows, ws = outs["coarse"]
    ck("D1 `--evidence coarse` 跑通（rc=0）", p.returncode == 0,
       (p.stderr or p.stdout)[-200:])
    cfg = next((r["payload"] for r in rows
                if r["kind"] == "evolution.run_config"), {})
    ck("D2 run_config 记的是 coarse（不是静默回退成 exact）",
       cfg.get("evidence") == "coarse", str(cfg.get("evidence")))
    mined = [r["payload"] for r in rows if r["kind"] == "weakness.mined"]
    ck("D3 ⛔ `weakness.mined` 账本**仍记原文**（检查名没被一起粗化掉）",
       any("checks_failed:" in str(json.dumps(m, ensure_ascii=False))
           for m in mined) if mined else False,
       f"mined={len(mined)}")
    ck("D4 ho_implication 也进账本（默认 False）",
       cfg.get("ho_implication") is False, str(cfg.get("ho_implication")))

    pe, rows_e, _ws_e = outs["exact"]
    cfg_e = next((r["payload"] for r in rows_e
                  if r["kind"] == "evolution.run_config"), {})
    ck("D5 反证：同代码跑 exact ⇒ run_config 记 exact（开关不是恒量）",
       pe.returncode == 0 and cfg_e.get("evidence") == "exact",
       f"rc={pe.returncode} evidence={cfg_e.get('evidence')}")
    ck("D6 两次跑的账本落点都是**临时** root（生产零写入）",
       (ws / "data" / "ledger.jsonl").exists()
       and (_ws_e / "data" / "ledger.jsonl").exists())

    print("\n" + "=" * 62)
    if FAILED:
        print(f"❌ {len(FAILED)} 项未通过：")
        for f in FAILED:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：模型只看到类别与数量，账本仍留原文，开关可分辨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
