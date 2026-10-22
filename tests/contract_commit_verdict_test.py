#!/usr/bin/env python3
"""契约：`improved` 只答「有没有提交」，不答「提交的是什么」—— 把裁决拆开数。

═══ 为什么需要它（r63 读数，README 135）═══

`RoundReport.improved` 的定义是 `committed is not None`。r63 加了 `scoped` 裁决
之后，一次「只改定点技能」的提交（实测 `dev=+1.0 ho=0`，held-out 一动没动）
也算 `improved=True`。

于是同一句话在三个地方被读成三种意思：

| 消费方 | 它印出来的 | 数据实际支持的 |
|---|---|---|
| `scripts/demo_evolve.py:121` | 「是否进化：✅ 是」 | 发展集修好了 |
| `scripts/llm_e2e.py:134` | 「赢过 = improved == True（必须同时过泛化闸）」 | 闸放行了，但可能是构造必然的 0 增益 |
| `docs/USAGE.md:61` | 「这一轮有没有候选被接受」 | ✅ 这句是准的 |

⇒ 前两处不是数据错，是**标签承诺了数据没有的东西**。

═══ 本契约钉什么、不钉什么 ═══

⛔ **不改 `improved` 的语义**。它是历史口径（生产账本 120 次提交、110 次
`generalize` / 10 次无裁决），改掉等于让新老读数不可比。改法只有两条：

1. **加一层聚合** `summarize_commits(reports)`：把「提交了几次」按裁决拆开，
   与「有没有提交」分开数；
2. **修标签**，让消费方读的是拆开后的数，而不是二值。

⇒ 所以 B3 会**主动断言 `improved` 仍然 True**（scoped 也算）。这不是在测实现
细节，而是在**保护一个决定**：谁想把它改成「只有 `generalize` 才算」，这条会
先红，逼出一次显式拍板，而不是悄悄改掉历史口径。

═══ 一个已存在的陷阱（C 组）═══

`generalization` 可以是 `None`（`--heldout off` / 没有 held-out）。此时
`committed` 非空但裁决缺失 ⇒ 必须归 `no_gate`，**不能**归空串：空串已经
被「没提交」占用了（README 134 的同族：`str(None)` 会把缺字段变成合法值）。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []

#: ⛔ 必须声明：不声明 ⇒ `check` 会报「未声明支持的模式」。
#:    本契约不起 `aether` 子进程、不依赖 `--e2e`，只有默认模式。
SUPPORTED_MODES = ("default",)

sys.path.insert(0, str(ROOT))

from aether import evolution as EV               # noqa: E402
from aether.evolution import RoundReport         # noqa: E402


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def _round(no: int, committed, verdict=None, scope_envs=None) -> RoundReport:
    """造一轮报告。`verdict=None` ⇒ 有提交但闸没跑（`generalization` 为 None）。"""
    gen = None
    if verdict is not None:
        gen = {"verdict": verdict, "dev_gain": 1.0, "heldout_gain": 0.0}
        if scope_envs:
            gen["scope"] = {"kind": "env", "envs": list(scope_envs)}
    return RoundReport(round_no=no, committed=committed, generalization=gen)


#: 混合样本：一次 generalize、一次 scoped、一次「提交了但没裁决」、一次没提交。
MIXED = [
    _round(1, "h_gen", "generalize"),
    _round(2, "h_scoped", "scoped", ["textscore"]),
    _round(3, "h_nogate", None),
    _round(4, None, None),
]


def main() -> int:
    print("=" * 62)
    print("A 组 · 缺口长什么样：数据在，但没人按裁决聚合")
    print("=" * 62)
    r_scoped = MIXED[1]
    ck("A1 `improved` 对 scoped-only 提交为 True（这是要**保留**的历史口径）",
       r_scoped.improved is True and r_scoped.committed == "h_scoped",
       f"improved={r_scoped.improved}")
    #: ⛔ 反证方向：这条证明「洞不是没数据」——每轮的报告里其实读得出裁决，
    #:    缺的是把 N 轮加起来的那一层。所以修法是加聚合，不是补字段。
    ck("A2 裁决在单轮里读得出（scoped 就在报告里，不需要新数据）",
       (r_scoped.to_dict().get("generalization") or {}).get("verdict") == "scoped",
       f"{r_scoped.to_dict().get('generalization')}")
    ck("A3 但**没有**跨轮聚合 ⇒ 每个消费方只能自己数（本行改前必须红）",
       hasattr(EV, "summarize_commits"),
       "aether.evolution.summarize_commits 不存在" if not hasattr(
           EV, "summarize_commits") else "")
    if not hasattr(EV, "summarize_commits"):
        print("\n" + "=" * 62)
        print(f"❌ 共 {len(FAILS)} 条失败（聚合层未实现，后续组无从判定）")
        return 1

    print("\n" + "=" * 62)
    print("B 组 · 聚合分得开：三种「有提交」互不冒充")
    print("=" * 62)
    s = EV.summarize_commits(MIXED)
    ck("B1 混合四轮 ⇒ total=3（三次提交），generalize/scoped/no_gate 各 1",
       s["total"] == 3 and s["generalized"] == 1 and s["scoped"] == 1
       and s["no_gate"] == 1 and s["by_verdict"].get("generalize") == 1,
       f"{s}")
    ck("B2 ⛔ 反证：`scoped` 不许被算进 `generalized`（两者是不同强度的证据）",
       s["generalized"] == 1 and s["by_verdict"].get("scoped") == 1,
       f"generalized={s['generalized']} scoped={s['scoped']}")
    ck("B3 ⛔ `improved` 语义未被改动：有提交就是 True（scoped 也算）",
       all(r.improved for r in MIXED[:3]) and not MIXED[3].improved,
       f"{[r.improved for r in MIXED]}")

    print("\n" + "=" * 62)
    print("C 组 · 「提交了但没裁决」不许伪装成别的")
    print("=" * 62)
    ck("C1 提交 + `generalization=None` ⇒ commit_verdict == 'no_gate'",
       MIXED[2].commit_verdict == "no_gate", f"{MIXED[2].commit_verdict!r}")
    ck("C2 没提交 ⇒ 空串，且不落进任何计数（空串≠no_gate）",
       MIXED[3].commit_verdict == "" and s["total"] == 3,
       f"{MIXED[3].commit_verdict!r} total={s['total']}")
    #: ⛔ 反证：把 no_gate 归成空串（与「没提交」同形）⇒ C1 必红、total 会变 2。
    ck("C3 ⛔ 反证：若 no_gate 与空串同形，total 会掉到 2 —— 现在不是",
       s["total"] == 3, f"total={s['total']}")

    print("\n" + "=" * 62)
    print("D 组 · 它真的进了输出（不是只在模块里躺着）")
    print("=" * 62)
    src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    hit = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if "improved" in keys:
            hit.append(keys)
    ck("D1 evolve 摘要里那个**含 `improved` 的字典**同时带 `commits`",
       bool(hit) and all("commits" in keys for keys in hit),
       f"含 improved 的字典 {len(hit)} 个，键={hit[0] if hit else None}")
    ck("D2 ⛔ 反证：它必须是**同一份** reports 算出来的（同源，不另算一遍）",
       "summarize_commits(reports)" in src.replace(" ", ""),
       f"命中={src.replace(' ', '').count('summarize_commits(reports)')} 处")
    demo_src = (ROOT / "scripts" / "demo_evolve.py").read_text(encoding="utf-8")
    e2e_src = (ROOT / "scripts" / "llm_e2e.py").read_text(encoding="utf-8")
    #: ⛔⛔ 字面量必须先落到变量里再进 f-string。3.9/3.10/3.11 的 f-string 不允许
    #:    表达式部分出现反斜杠（PEP 701 到 3.12 才放开），写成
    #:    `f"{'get(\"commits\")' in src}"` 在 3.9 上是 **SyntaxError** —— 而且
    #:    `ast.parse(feature_version=(3, 9))` 抓不到它（feature_version 只管
    #:    语法特性清单，覆盖不了 f-string 解析器的差异）⇒ 唯一可靠的判据是
    #:    用真实的最低版本解释器编译（见 contract_pycompat_test.py）。
    _d_key = 'get("commits")'
    _d_old = "- 是否进化"
    _demo_has_k = _d_key in demo_src
    _e2e_has_k = _d_key in e2e_src
    _demo_has_old = _d_old in demo_src
    ck("D3 ⛔ 两个脚本读拆开后的数；「- 是否进化」这一行必须消失"
       "（它把 scoped 提交说成进化）",
       _demo_has_k and _e2e_has_k and not _demo_has_old,
       f"demo读commits={_demo_has_k} e2e读commits={_e2e_has_k} "
       f"demo仍有该行={_demo_has_old}")

    print("\n" + "=" * 62)
    print("E 组 · 生产读数：账本里的提交裁决只有三种形状")
    print("=" * 62)
    led = ROOT / "data" / "ledger.jsonl"
    if not led.exists():
        print("… 无生产账本，跳过（不算失败）")
    else:
        from aether.generalization import VERDICTS
        shapes, bad = {}, []
        for line in led.open(encoding="utf-8"):
            if '"harness.commit"' not in line:
                continue
            try:
                ev = json.loads(line)
            except Exception:                        # noqa: BLE001
                continue
            if ev.get("kind") != "harness.commit":
                continue
            g = (ev.get("payload") or {}).get("generalization")
            v = (g or {}).get("verdict") if isinstance(g, dict) else None
            key = v if v is not None else "<none>"
            shapes[key] = shapes.get(key, 0) + 1
            if v is not None and v not in VERDICTS:
                bad.append(v)
        ck("E1 提交里出现的裁决全在封闭集合内（或为 None = 闸没跑）",
           not bad, f"集合外={bad[:5]}，分布={shapes}")
        ck("E2 ⛔ 反证：生产库里**确实有**提交（否则 E1 是空集合当分母）",
           sum(shapes.values()) > 0, f"harness.commit {sum(shapes.values())} 条")

    print()
    print("=" * 62)
    if FAILS:
        print(f"❌ 共 {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
