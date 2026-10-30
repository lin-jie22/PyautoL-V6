#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：**上界实验**（README 171）。

═══ 为什么有这份契约 ═══

170 收口时说「判据侧已到顶 ⇒ 只剩换架构做代码生成这一条路」。这句话隐含一个
未经检验的前提：**把判据给足之后，形态可达的题还是修不好**（如果是 ⟹ bug 在
下游合成层 ⟹ 换架构才有意义）。

171 用一个便宜的实验（3 个案例）去证它，结果把结论**反过来**了：

    A-93185b2  oracle 判据 ⇒ **exact_file**（机器补丁与人类逐字节相同）
    B-64e6430  oracle 判据 ⇒ elsewhere（full_pass=True 但改在别处）
    C-f5f5cd8  **oracle 判据根本写不出来** ⇒ no_f2p_under_oracle

⇒ 合成层在 L3 上**一步命中**；真正卡住的是判据（写不出 / 写出来不够强）。
⇒ **171 否掉了 170 的那个大扩张计划**，而整个221行数全部建立在「先不敢靠猜」
这件事上。所以这份契约要钉死的不只是读数，还有**读数是怎么取出来的**。

═══ 四组 ═══

  · A ⛔ **存档读数灶**：三个案例的 stage / reach / gate 写死对拍
        （落盘缺失 ⇒ **跳过并披露**，未覆盖 ≠ 通过 —— 第 149 条）
  · B ⛔⛔ **oracle 闸自证**：期望值写错 ⇒ 必须拦在 `oracle_bad`，
        **不许**带着错判据进合成层。这是 171 当场拦下我 2/5 写错判据的
        那个闸本身的自证 —— 没有它，我就会把「我判据写错」归因成
        「合成层修不好」（第 24 条）。
        ⛔ **注入反证**：把这一步去掉 ⇒ 错判据变成 F2P ⇒ 读数从
        `oracle_bad` 掉到 `no_f2p`，归因静默错位。
  · C ⛔ **因果谓词**：A 的 oracle 判据里拿掉唯一那条 F2P ⇒ 必须是
        `no_f2p_under_oracle`。它证明「exact_file」是**那条判据换来的**，
        不是撞出来的（第 122 条：构造必然 ≠ 证据）。需要 `/tmp/realrepo`。
  · D ⛔ **plausible patch 是可防卫的读数**：B 的机器改动必须落在
        `_cull()` 的分支条件上（绕开 bug 的那条路），**不是**修对了位置。
        需要 `/tmp/realrepo`。

跑法（A/B 组秒级；C/D 组需真实仓库，缺失则 SKIP）：
    python tests/contract_upper_bound_test.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUPPORTED_MODES = ("default",)
MIN_PYTHON = (3, 9)

CENSUS = ROOT / "scripts" / "upper_bound_3cases.json"

SKIPS: list = []
FAILS: list = []
PASSES: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)
    else:
        #: ⛔ 放在**后**（让 `FAILS` 成为 ck 里第一个 `.append`）：
        #:    `contract_assert_wiring_test` 的 B2 用「ck 里第一个 append 的
        #:    变量名」推断失败列表是谁。把 PASSES 写在前面会让它把失败列表
        #:    认成 `PASSES` ⇒ 误报「失败没传到退出码」。
        PASSES.append(name)


def skip(name, why):
    print(f"  SKIP {name}  ⛔ {why}")
    SKIPS.append(name)


def _realrepo_ok(repo: str) -> bool:
    p = Path(repo)
    return (p / ".git").exists() and (p / ".git" / "HEAD").exists()


# ═══════════════ A 组：存档读数灶 ═══════════════
def group_a():
    print("\n══ A 组：171 上界实验的存档读数 ══")
    if not CENSUS.exists():
        skip("A1 三个案例的 stage", f"落盘缺失 {CENSUS.name}")
        return None
    rows = json.loads(CENSUS.read_text())
    by = {r["id"]: r for r in rows}
    ck("A1 存档含全部三案例",
       {"A-93185b2", "B-64e6430", "C-f5f5cd8"} <= set(by),
       f"实得 {sorted(by)}")
    if len(by) < 3:
        return None
    #: ⛔⛔ 这三个数是 171 的结论本身，改一个字等于改结论。
    ck("A2 A-93185b2 ⇒ exact_file（oracle 判据下真修好了）",
       by["A-93185b2"]["stage"] == "exact_file",
       by["A-93185b2"]["stage"])
    ck("A3 A-93185b2 走到底 S5_full 且 full_pass",
       by["A-93185b2"].get("gate") == "S5_full"
       and by["A-93185b2"].get("full_pass") is True)
    ck("A4 B-64e6430 ⇒ elsewhere（full_pass 也不算修好，161 口径）",
       by["B-64e6430"]["stage"] == "elsewhere",
       by["B-64e6430"]["stage"])
    ck("A5 ⛔ B-64e6430 的 full_pass 却为 True（过点≠修好）",
       by["B-64e6430"].get("gate") == "S5_full"
       and by["B-64e6430"].get("full_pass") is True)
    ck("A6 C-f5f5cd8 ⇒ no_f2p_under_oracle（判据写不出来）",
       by["C-f5f5cd8"]["stage"] == "no_f2p_under_oracle",
       by["C-f5f5cd8"]["stage"])
    ck("A7 三个 oracle 判据在参照实现上必须全绿（否则本轮读数全部作废）",
       all(r.get("fixed_ok") and r["fixed_ok"].split("/")[0]
           == r["fixed_ok"].split("/")[1] for r in by.values()),
       str([r.get("fixed_ok") for r in by.values()]))
    ck("A8 L2/L3 上 reach 记下来了（这是选Case 的依据）",
       by["A-93185b2"].get("reach") == "L3"
       and by["B-64e6430"].get("reach") == "L3"
       and by["C-f5f5cd8"].get("reach") == "L2")
    return by


# ═══════════════ B 组：oracle 闸自证 ═══════════════
def group_b():
    """: ⛔ 这一组不碰真实仓库 —— 它验的是**取读数的手续**本身对不对。"""
    print("\n══ B 组：⛔⛔ oracle 判据必须先在参照实现上验过 ══")
    from scripts.probe_upper_bound import run_case

    src = "def f(x):\n    return x + 1\n"
    #: 拿错老在地expected ⇒ 借 r91 那次我写错两条的真实形状
    bad = [{"name": "e1", "call": "f(1)", "expect": 99}]
    case = {"id": "probe-bad", "repo": "", "sha": "", "src": "t.py",
            "reach": "L3", "shape": "probe", "tests": bad}
    r = run_case(case, timeout=10.0, buggy_src=src, fixed_src=src)
    ck("B1 ⛔⛔ 期望值写错 ⇒ 拦在 oracle_bad",
       r.get("stage") == "oracle_bad", str(r.get("stage")))
    ck("B2 ⛔ 拦下了就**不许**进合成层（没有 gate/found 字段）",
       "gate" not in r and "found" not in r,
       f"gate={r.get('gate')} found={r.get('found')}")
    ck("B3 必须在读数里**说出哪条错、为什么**（不许静默丢）",
       bool(r.get("bad")), str(r.get("bad"))[:80])

    #: ── ⛔⛔ 注入反证：把闸拿掉，看会不会红 ──
    #:    没有这一步，B1 就是一句恒真的口号。
    from aether.environment import run_in_sandbox
    rb = run_in_sandbox(src, bad, timeout=10.0)
    n_fail = sum(1 for x in rb.results if not x.get("passed"))
    ck("B4 ⛔⛔ 注入反证：跳过这道闸 ⇒ 错判据会被当成 **1 条 F2P**"
       " ⇒ 归因从「我写错了」静默错位到「合成层修不好」",
       n_fail == 1, f"实得 n_fail={n_fail}")

    #: 正例：写得对 ⇒ 不该被拦（真 F2P 才能往下走）
    good = [{"name": "e1", "call": "f(1)", "expect": 2}]
    case2 = dict(case, id="probe-ok", tests=good)
    r2 = run_case(case2, timeout=10.0, buggy_src=src,
                  fixed_src="def f(x):\n    return x + 1\n")
    ck("B5 期望值写对 ⇒ 在参照实现上全绿、不被拦",
       r2.get("fixed_ok") == "1/1", str(r2.get("fixed_ok")))


# ═══════════════ C/D 组：需要真实仓库 ═══════════════
def group_cd():
    print("\n══ C/D 组：因果关系与 plausible patch（需 /tmp/realrepo）══")
    repo = "/tmp/realrepo/boltons"
    if not _realrepo_ok(repo):
        skip("C/D 全组", "取证基线 /tmp/realrepo/boltons 不存在或未鉴定"
                        "（169 教训：基线没了 ⇒ 跳过并披露，不许当成通过）")
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pub", ROOT / "scripts" / "probe_upper_bound.py")
    pub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pub)
    cases = {c["id"]: c for c in pub.CASES}

    #: ── C 组：A 的成功是不是那条 F2P 换来的 ──
    base = cases["A-93185b2"]
    no_a1 = dict(base, id="probe-no-a1",
                 tests=[t for t in base["tests"] if t["name"] != "a1"])
    #: ⛔ 用量具自己的 runner，不许自带第二套口径（103 条）
    r = pub.run_case(no_a1, timeout=25.0)
    ck("C1 ⛔⛔ 注入反证：拿掉唯一那条 F2P ⇒ A 立刻变成走不下去"
       " ⇒ exact_file 是那条判据换来的，不是撞出来的",
       r.get("stage") == "no_f2p_under_oracle", str(r.get("stage")))

    with_a1 = dict(base, id="probe-with-a1")
    r2 = pub.run_case(with_a1, timeout=25.0)
    ck("C2 同一Token 判据留下 ⇒ 必须还是修好（可复现）",
       r2.get("stage") == "exact_file", str(r2.get("stage")))

    #: ── D 组：B 的 elsewhere 是「绕开」不是「修对」 ──
    rb = pub.run_case(cases["B-64e6430"], timeout=25.0)
    md = " ".join(rb.get("machine_diff", []))
    hd = " ".join(rb.get("human_diff", []))
    ck("D1 ⛔ B-64e6430 仍是 elsewhere（重跑可复现）",
       rb.get("stage") == "elsewhere", str(rb.get("stage")))
    ck("D2 ⛔⛔ 机器改在 `_cull()` 的分支条件上 ⇒ 它把**有 bug 的路径绕开**"
       "（走 `_compact()` 重建索引），而不是修好 `_get_apparent_index`",
       "len(ded)" in md and "384" in md, md[:100])
    ck("D3 人类改的那行（`d_stop + d_start`）与机器改的行**不相交**",
       "d_stop" in hd and "d_stop" not in md, f"human={hd[:60]}")


def main() -> int:
    print(f"{'=' * 76}\n契约：上界实验（README 171）\n{'=' * 76}")
    group_a()
    group_b()
    group_cd()
    print(f"\n{'=' * 76}")
    print(f"  pass={len(PASSES)}  fail={len(FAILS)}  skip={len(SKIPS)}")
    if FAILS:
        print("  ⛔ 失败项：" + ", ".join(FAILS))
    if SKIPS:
        print("  ⛔ 跳过项（未覆盖 ≠ 通过）：" + ", ".join(SKIPS))
    print(f"{'=' * 76}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
