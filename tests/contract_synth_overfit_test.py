#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：⛔⛔ 判据驱动生成层的 **overfit 风险**是**真的**（实测，不是担心）。

═══ 为什么必须有这份契约 ═══

159 条补上合成层时声明了一条**未验证**的风险：

    筛选用的是发展集判据 ⇒ 选出来的必然是"发展集上更绿"的那个
    ⇒ 这就是 overfit 的形状。

而 159 的实验里 `verdict=no_gate`（闸没参与 —— 已确认是**实验装置固有**，
158 的 A 组同样是 no_gate）⇒ 这个风险**一直没被测过**。

本契约把它钉成**可离线回归**的机制事实。

═══ ⛔⛔ 必须先避开的陷阱：恒真验证 ═══

159 里合成补丁与人类修复**逐字一致** ⇒ 拿去 held-out 上跑必然 100% 通过
⇒ 那是**恒真验证，没有信息量**（第 122 条：构造必然 ≠ 证据）。

⇒ 本契约量的是**判据稀疏度**：只给合成层 k 条判据，看它在**没给它看的
那些判据**上还成不成立。判据给少了 ⇒ 它会搜出一个"满足你给的这些、
但不满足其余"的补丁 —— 那不是修复，是**拟合判据**。

═══ 真实 bug 上的实测（`scripts/probe_synth_overfit.py`）═══

| 给的判据数 k | 搜出 | 与人类修复一致 | 未见判据通过率 |
|---|---|---|---|
| 1 | 5/5 | **0/5** | **0.25** ← 全部过拟合 |
| 2 | 5/5 | 4/5 | 0.843 |
| **3** | 5/5 | 5/5 | **1.0** ← 收敛 |
| 5 / 10 / 25 | 5/5 | 5/5 | 1.0 |

⇒ 风险为真：**k=1 时 5/5 全错**（搜出 `cmp@L160`，满足给它那 1 条、
破坏其余 18 条）。也给出了阈值：这条 bug 上 **k≥3 就收敛**。
⛔ 推论（必须一起报）：159 报的"修好"是在 **k=25（最宽松）**下取得的。

═══ 组（玩具场景，秒级，机制与真实 bug 同形）═══

  O1 量具对照：人类修复在未见判据上必须全过（否则 O3 是恒真的空判据）
  O2 判据够密 ⇒ 搜出正确解（与人类修复一致）
  O3 ⛔⛔ 只给 1 条 ⇒ 搜出的补丁**不等于**人类修复，且**在未见判据上挂**
  O4 反证不是空跑：只给 1 条时**确实**搜出了东西（否则 O3 恒真）
  O5 ⛔ 那个凑数补丁**确实满足**给它的那条判据 ⇒ 是"拟合判据"不是"修 bug"
  O6 探针脚本在（真实 bug 上的稀疏度曲线要能复现）
  O7 ⛔ 模块头的 overfit 警示不许被删（文档即契约）

═══ P 组：⛔⛔ `found` 有两个含义，只看它会读错 ═══

真实 bug `64e6430` 那一例（README 161）：只有 2 条 F2P、且其中一条我写成
了 SyntaxError ⇒ **「全绿」根本不可能达到** ⇒ 正确答案（`+` → `-`，L155）
与一个改在不相干位置的凑数解（`len(ded) > 384` → `<`，L122）**并列 6/7**
⇒ 本层按「通过数最多、先到先得」选中了凑数解。`found=True`、`gain>0`，
可它跟那个 bug 毫无关系。

⇒ 加了 `SynthResult.full_pass`：全绿是天然的分水岭，**必须显式可查**。
⛔ 但它只是必要非充分（P3 证明存在"另一种改法也能全绿"）。

跑法（离线、秒级）：
    python tests/contract_synth_overfit_test.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读取，不 import）。
SUPPORTED_MODES = ("default",)

#: 用到 `run_in_sandbox`（子进程沙箱），3.9 起即可跑。
MIN_PYTHON = (3, 9)

from aether.synthesize import score_source, synthesize_patch  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


#: ⛔ 玩具设计要点：**正确解必须排在枚举顺序的后面**，否则正确的候选会先
#:    命中，overfit 就复现不出来（第一版就是这样，白跑）。
#:    这里正确解是"常量 11→10"（int-1），而凑数解"翻转比较符"（cmp）在前。
BUGGY = ("def f(x):\n"
         "    if x > 11:\n"
         "        return 'bad'\n"
         "    return 'ok'\n")
HUMAN = BUGGY.replace("x > 11", "x > 10")
#: A：在缺陷版上**挂**（10.5 > 11 为假）⇒ 必须给，否则无增益可图
A = {"name": "A", "call": "f(10.5)", "expect": "bad"}
#: C：区分"真正的修复 `> 10`"与"凑数的 `<= 11`" —— 它是**未见判据**
C = {"name": "C", "call": "f(0)", "expect": "ok"}


def group_p() -> None:
    """⛔⛔ `found` 有两个含义 —— 只看它会读错。"""
    print("\n== P 组：⛔⛔ found ≠ 修好了（真实 bug 64e6430 实录）==")
    #: 同形的最小场景：缺陷是 `>` 应为 `>=`（correct），另有一条**恒失败**
    #: 的判据 ⇒ 「全绿」不可能达到 ⇒ 并列 ⇒ 取材历史子集Thompson
    buggy = "def target(x):\n    if x > 10:\n        return 'BIG'\n    return 'small'\n"
    human = buggy.replace("x > 10", "x >= 10")
    ok3 = [{"name": "F2P", "call": "target(10)", "expect": "BIG"},
           {"name": "p1", "call": "target(5)", "expect": "small"},
           {"name": "p2", "call": "target(20)", "expect": "BIG"}]
    #: ⛔ 这条判据的期望不可能被满足（任何源码都不行）⇒ 全绿不可达
    impossible = {"name": "BROKEN", "call": "target(0)", "expect": "NEVER_MATCH"}

    h_ok, _, h_blew = score_source(human, ok3)
    ck("P1-1 对照：人类修复在 3 条正常判据上全过", (not h_blew) and h_ok == 3,
       f"{h_ok}/3")
    r_ok = synthesize_patch(buggy, ok3)
    ck("P1-2 判据干净 ⇒ 全绿（full_pass=True）", r_ok.found and r_ok.full_pass,
       f"{r_ok.base_ok}→{r_ok.new_ok}/{r_ok.n_tests}")

    r_bad = synthesize_patch(buggy, ok3 + [impossible])
    ck("P2-1 ⛔ 掺一条恒失败判据 ⇒ **found 仍为 True**", r_bad.found,
       f"desc={r_bad.desc}")
    ck("P2-2 ⛔⛔ 但 full_pass=False（不可能全绿）", not r_bad.full_pass,
       f"{r_bad.base_ok}→{r_bad.new_ok}/{r_bad.n_tests}")
    ck("P2-3 ⇒ 只看 found 就会把「部分通过」当作「修好了」"
       "（64e6430 实录：改在 `len(ded) > 384`，人类修复在另一处）",
       r_bad.found and not r_bad.full_pass and r_bad.gain > 0)

    #: ⛔ P3：全绿也**不是**充分条件 —— 存在另一种改法，在这套判据上等价
    alt = buggy.replace("x > 10", "x > 9")     # 另一种全绿改法
    alt_ok, _, alt_blew = score_source(alt, ok3)
    ck("P3-1 ⛔ 存在另一种改法也在这套判据上全绿",
       (not alt_blew) and alt_ok == 3 and alt != human, f"{alt_ok}/3")
    ck("P3-2 ⇒ full_pass 只是**必要**条件：全绿后仍要过 held-out 闸"
       "（这套整数判据区分不了 `>= 10` 与 `> 9`）",
       alt_ok == 3 and alt != human)

    from aether.synthesize import SynthResult
    ck("P4 `full_pass` 在实现里（不是文档承诺）",
       hasattr(SynthResult, "full_pass"))


def main() -> int:
    print("=" * 74)
    print("契约：判据驱动生成层的 overfit 风险为真（判据稀疏度 → 泛化）")
    print("=" * 74)

    print("\n== O1 量具对照（先证装置没坏，否则 O3 是恒真空判据）==")
    a_bad, _, a_blew = score_source(BUGGY, [A])
    ck("O1-1 缺陷版在 A 上确实挂", (not a_blew) and a_bad == 0, f"{a_bad}/1")
    h_ok, _, h_blew = score_source(HUMAN, [A, C])
    ck("O1-2 ⛔ 人类修复在 A+C 上全过（天花板）", (not h_blew) and h_ok == 2,
       f"{h_ok}/2")

    print("\n== O2 判据够密（2 条）⇒ 必须搜出正确解 ==")
    r2 = synthesize_patch(BUGGY, [A, C])
    ck("O2-1 搜出了补丁", r2.found, f"desc={r2.desc}")
    ck("O2-2 ⛔ 与人类修复逐字一致", r2.found and r2.patch == HUMAN,
       f"{r2.desc}")

    print("\n== O3 ⛔⛔ 只给 1 条 ⇒ overfit（给它的过了，没给的挂了）==")
    r1 = synthesize_patch(BUGGY, [A])
    ck("O3-1 搜出的补丁**不等于**人类修复",
       (not r1.found) or r1.patch != HUMAN, f"desc={r1.desc}")
    hedge_ok, _, hedge_blew = score_source(r1.patch, [C])
    ck("O3-2 ⛔⛔ 在**未见判据** C 上挂", (not hedge_blew) and hedge_ok == 0,
       f"{hedge_ok}/1")
    print(f"     凑数补丁：{[l.strip() for l in r1.patch.splitlines() if l.strip().startswith('if ')]}")
    print(f"     （真实 bug 上同形：k=1 ⇒ 5/5 全错，未见判据通过率 0.25）")

    print("\n== O4 反证不是空跑 ==")
    ck("O4-1 只给 1 条时**确实**搜出了东西（否则 O3 恒真）", r1.found)
    ck("O4-2 两种判据密度下产出不同（证明差异由判据密度造成）",
       r1.patch != r2.patch)

    print("\n== O5 ⛔ 凑数补丁确实满足了给它的那条判据 ==")
    sub_ok, _, sub_blew = score_source(r1.patch, [A])
    ck("O5-1 它在给定的判据 A 上**通过**", (not sub_blew) and sub_ok == 1,
       f"{sub_ok}/1")
    ck("O5-2 ⇒ 它是**拟合判据**，不是修复（过了给的、挂了没给的）",
       sub_ok == 1 and hedge_ok == 0)

    print("\n== O6 真实 bug 上的稀疏度曲线要能复现 ==")
    p = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "probe_synth_overfit.py"),
         "--help"], capture_output=True, text=True, timeout=60)
    ck("O6-1 探针脚本可跑", p.returncode == 0, f"rc={p.returncode}")
    ck("O6-2 ⛔ 有 --ks / --trials / --seed（可复现）",
       all(x in p.stdout for x in ("--ks", "--trials", "--seed")))

    print("\n== O7 ⛔ 模块头的 overfit 警示不许被删 ==")
    txt = (ROOT / "aether" / "synthesize.py").read_text(encoding="utf-8")
    ck("O7-1 模块头含 overfit 警示", "overfit" in txt.lower())
    ck("O7-2 ⛔ 含实测阈值（k=1 ⇒ 全错）",
       "k=1" in txt and "0.25" in txt,
       "（删掉警示 = 删掉这条风险的唯一入口，见 159 残留）")

    group_p()

    print("=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for x in FAILS:
            print("  ✗", x)
        return 1
    print("✅ overfit 风险确认为真：判据稀疏 ⇒ 合成层拟合判据而非修复 bug"
          "（k≥3 才收敛）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
