#!/usr/bin/env python3
"""嵌入判别力对照：把"该不该换真嵌入"变成可测判据，而不是信仰。

用法：
    python3 scripts/embedding_bench.py                # hashing vs ollama（如可用）
    python3 scripts/embedding_bench.py --no-ollama    # 只跑离线组

输出：每个 embedder 的 recall@1 / MRR / margin，以及选型闸的裁决。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aether.embeddings import (HashingEmbedder, OllamaEmbedder,  # noqa: E402
                               SynonymEmbedder, resolve_embedder)
from aether.retrieval import (BENCH, BENCH_EN, EmbeddingSwitchGate,  # noqa: E402
                              evaluate)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ollama", action="store_true")
    ap.add_argument("--timeout", default="180")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    cands = [HashingEmbedder(dim=256), SynonymEmbedder(dim=64)]
    if not args.no_ollama:
        emb, rep = resolve_embedder("auto", timeout=float(args.timeout))
        print(f"[探测] chosen={rep['chosen']} id={rep['embedder_id']} "
              f"dim={rep['dim']} degraded={rep['degraded']}")
        for p in rep["probes"]:
            print(f"       · {p['kind']}: available={p['available']} {p['reason'][:70]}")
        if rep["chosen"] == "ollama":
            cands.insert(1, emb)

    results: Dict[str, list] = {}
    verdicts: list = []
    for label, cases in (("中文", BENCH), ("英文", BENCH_EN)):
        print(f"\n=== {label}判别力（{len(cases)} 条，候选池=全部正例文档）===")
        rows = []
        for e in cands:
            t0 = time.time()
            r = evaluate(e, cases)
            rows.append(r)
            cost = time.time() - t0
            if r.error:
                print(f"{r.embedder_id:34s} ERROR {r.error}")
                continue
            print(f"{r.embedder_id:34s} recall@1={r.recall_at_1:.2f} "
                  f"MRR={r.mrr:.3f} margin={r.margin_mean:+.4f} "
                  f"(min {r.margin_min:+.4f})  {cost:.1f}s")
        print("逐条 rank（1=正例排第一）：")
        for c in cases:
            line = f"  {c.doc_id:12s}"
            for r in rows:
                cr = next((x for x in r.cases if x.doc_id == c.doc_id), None)
                line += f"  {r.embedder_id.split(':')[0]}=" \
                        f"{str(cr.rank) if cr else '-':>2s}"
            print(line)
        results[label] = rows

        base = rows[0]
        gate = EmbeddingSwitchGate()
        print("选型闸裁决（当前 = %s）：" % base.embedder_id)
        for r in rows[1:]:
            v = gate.decide(base, r)
            verdicts.append({"lang": label, "verdict": v})
            print(f"  → {r.embedder_id:34s} {'允许' if v.allow else '拒绝'}"
                  f"：{v.reason}")

    if args.json:
        out = {lang: {"current": rows[0].to_dict(),
                      "candidates": [r.to_dict() for r in rows[1:]]}
               for lang, rows in results.items()}
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n已写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
