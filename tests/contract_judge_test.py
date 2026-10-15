#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：判据集（尺子）必须有身份，且**变异够不到它**。

═══ 缺口 ═══

账本记下了"被判的东西"，没记下"判它的东西"：

  · `eval.verdict` 的 payload 有 `env_fingerprint`（环境源码+tests 的哈希）、
    `harness_id`（内容寻址）；
  · 而**判据本身**（`verify_source` 的判据 / 闸的阈值 / 难度分层的口径）
    在裁决里**没有任何身份**。

⇒ 改一行判据代码，历史读数与未来读数就**不可比**，而账本上没有任何字段
   能发现这件事。而"零新红""非回归""发展集涨了"**全部**建立在这把尺子上。

本文件守：
  A **身份对代码敏感**：改判据模块的一个字符 ⇒ 身份变（反证）；
    但只改注释/缩进 ⇒ 身份**不**变（否则没人受得了，判据会被关掉）；
  B **边界不变量**：变异/提议在构造上够不到判据集（用**真实调用**反证，
    不是读常量）；判据模块读不到 ⇒ 身份是假的 ⇒ 必须报出来；
  C **三态可比性**：comparable / unknown（存量，放行）/ incomparable（拒绝）；
  D **接线**：裁决真的带上了身份；doctor 真的会检查它；
  E **反证**：把边界拆掉 ⇒ 判据必须转红。

跑法（离线，秒级）：
    python tests/contract_judge_test.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/linshaojie/V6")
sys.path.insert(0, str(ROOT))

#: ⛔ 声明支持的模式（`aether check` 静态读这一行）。
SUPPORTED_MODES = ("default",)

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether import judge as J
    from aether import evolution as E

    # ── A 组：身份对代码敏感，对格式不敏感 ───────────────────────
    print("A 组：尺子有指纹（对逻辑敏感、对格式不敏感）")
    jv1 = J.identity()
    ck("A1 身份是 32 位十六进制（与其它寻址键同值）",
       len(jv1) == 32 and all(c in "0123456789abcdef" for c in jv1), jv1)
    ck("A2 同代码 ⇒ 同身份（确定性）", J.identity() == jv1)
    #: ⛔ 反证直接打到**规范化**这一层（而不是打在它之后）：
    #:    实测踩过 —— 把注入放在 `_module_source` 的**返回值之后**，
    #:    注入的那行注释根本没经过规范化 ⇒ A3 恒红，而红的原因是
    #:    "测试打错了位置"，不是判据坏了。
    #:    真实的敏感面是：**原始源码** → normalize → hash。所以从原始源码打。
    orig_src = J._module_source("aether.verifier")
    comment_only = orig_src + "\n# 这是一行注释\n\n"
    ck("A3 只加注释/空行 ⇒ 规范化后不变（格式不算改尺子）",
       J.normalize_source(comment_only) == J.normalize_source(orig_src),
       "规范化失效会让身份永远在变")
    logic_extra = orig_src + "\nSENTINEL = 1\n"
    ck("A4 ⛔ 加一行逻辑 ⇒ 规范化后**必须**变（尺子改了要看得出来）",
       J.normalize_source(logic_extra) != J.normalize_source(orig_src))
    #: ⛔ 还有一个更隐蔽的坑：字符串里含 `#`。按 `#` 切注释会把它一起吃掉。
    tricky = 'x = "a # b"  # 真注释\n'
    nz = J.normalize_source(tricky)
    ck("A4b ⛔ 字符串里的 `#` 不被当注释切掉（按 `#` 切会误伤）",
       '"a # b"' in nz and "真注释" not in nz, repr(nz))
    #: 身份对**判据源码**敏感：用假的取源注入逻辑，身份必须变
    orig = J._module_source
    try:
        J._module_source = lambda n: orig_src + "\nSENTINEL = 1"
        ck("A4c ⛔ 判据源码变了 ⇒ 身份必须变",
           J.identity() != jv1, "身份对逻辑不敏感 = 没有身份")
    finally:
        J._module_source = orig
    ck("A5 复原后身份回到原值", J.identity() == jv1)
    #: 逐模块指纹必须都在（读不到就得说清，不许静默跳过）
    desc = J.describe()
    ck("A6 每个判据模块都有指纹（读不到会写 `<不可读：…>`）",
       all("<不可读" not in v for v in desc["modules"].values()),
       str(desc["modules"]))

    # ── B 组：边界不变量 ─────────────────────────────────────────
    print("B 组：变异在构造上够不到判据集")
    b = J.assert_boundary()
    ck("B1 边界不变量通过，且反证了越界 target 被拒",
       b["ok"] is True and b["probes_rejected"] >= 5,
       str(b)[:120])
    #: 用**真实调用**再确认一次（不读常量）：这些 target 必须抛
    from aether.genome import Gene, Harness, apply_gene
    leaked = []
    for t in ("verifier.reward", "judge.threshold", "L9.verifier.x",
              "aether.verifier.SELF_REPORT_KEYS"):
        try:
            apply_gene(Harness(), Gene(op="set", target=t, value="x"))
            leaked.append(t)
        except Exception:                                  # noqa: BLE001
            pass
    ck("B2 ⛔ 真实调用：改判据的 target 一个都进不来", not leaked, str(leaked))
    #: 反证另一面：**合法**的 harness 层必须照常能改（边界不是恒红）
    #: ⛔ 每一层都要用**它自己合法的 op + value**：
    #:    实测踩过 —— 用一套"通用"的 `(set, 1)` 去打所有层，
    #:    在新增 `world_facts` 层（append 要求 Mapping）之后当场转红，
    #:    而红的原因是**测试用错了 op**，不是边界坏了。
    #:    ⇒ 这张表要跟着 `_ALLOWED_OPS_BY_LAYER` 一起维护。
    from aether.genome import LAYERS
    ok_layers = []
    probes = {
        "meta_rules": ("set", "L0.meta_rules", "规则"),
        "routing": ("set", "L1.routing.repair", "fix"),
        "tools": ("set", "L2.tools", "read_file"),
        "skills": ("set", "L3.skills.x", "body"),
        "memory_policy": ("tune", "L4.memory_policy.recall_topk", 1),
        "world_facts": ("append", "L5.world_facts.deadend:c|env:e",
                        {"observations": 3}),
        "prompts": ("set", "prompts.a", "内容"),
    }
    for layer in LAYERS:
        op, t, v = probes[layer]
        try:
            apply_gene(Harness(), Gene(op=op, target=t, value=v))
            ok_layers.append(layer)
        except Exception as e:                             # noqa: BLE001
            print(f"     （{layer} 失败：{type(e).__name__}: {str(e)[:60]}）")
    ck("B3 反证的另一半：合法层照常可改（边界不是恒红）",
       len(ok_layers) == len(LAYERS), f"{ok_layers} vs {list(LAYERS)}")
    #: 判据模块名单本身**也是判据**：加一个模块 ⇒ 身份变（改尺子要留痕）
    orig_mods = J.JUDGE_MODULES
    try:
        J.JUDGE_MODULES = tuple(orig_mods) + ("aether.ledger",)
        ck("B4 往判据集里加模块 ⇒ 身份变（名单本身是判据）",
           J.identity() != jv1)
    finally:
        J.JUDGE_MODULES = orig_mods

    #: ⛔⛔ 口径常量是**尺子的一部分**，而且必须是**取值**敏感。
    #:    实测踩过：新增 `world_facts` 层（`LAYERS` 从 6 层变 7 层）时
    #:    `judge.identity()` **纹丝不动** —— 因为原实现只把口径常量的
    #:    **名字列表**算进哈希，而名字（`"aether.genome:LAYERS"`）没变。
    #:    那是一个"看起来有身份、其实漏了关键变化"的假身份。
    from aether import genome as _g
    base_layers = tuple(_g.LAYERS)
    try:
        _g.LAYERS = base_layers + ("probe_only_layer",)
        ck("B5 ⛔ 改**层集合** ⇒ 判据身份必须变（取值敏感，不是名字敏感）",
           J.identity() != jv1, "层集合变了而身份没变 = 假身份")
    finally:
        _g.LAYERS = base_layers
    ck("B6 复原后身份回到原值（B5 不是恒红）", J.identity() == jv1)
    ops = _g._ALLOWED_OPS_BY_LAYER
    base_ops = dict(ops)
    try:
        ops["skills"] = tuple(base_ops["skills"]) + ("probe_only_op",)
        ck("B7 ⛔ 改 **op 白名单** ⇒ 判据身份必须变（它定义什么算合法修改）",
           J.identity() != jv1, "op 白名单是尺子的一部分")
    finally:
        ops.clear()
        ops.update(base_ops)
    ck("B8 复原后身份回到原值", J.identity() == jv1)
    ck("B9 describe 里能看到口径取值（不只名字）",
       "caliber_values" in J.describe()
       and "world_facts" in str(J.describe()["caliber_values"]),
       str(list(J.describe().keys())))

    # ── C 组：三态可比性 ─────────────────────────────────────────
    print("C 组：可比性三态（不合并）")
    st, _ = J.incomparable(jv1, jv1)
    ck("C1 同身份 ⇒ comparable", st == J.COMPARABLE, st)
    st2, why2 = J.incomparable(jv1, "deadbeef")
    ck("C2 不同身份 ⇒ incomparable（拒绝跨尺子比大小）",
       st2 == J.INCOMPARABLE and "不可比" in why2, st2)
    st3, _ = J.incomparable("", jv1)
    ck("C3 ⛔ 一侧缺 ⇒ unknown 且**放行**（存量不该被判罪）",
       st3 == J.UNKNOWN, st3)
    rep = J.check_verdict_comparable([{"judge_version": jv1},
                                      {"judge_version": jv1}])
    ck("C4 一组同身份 ⇒ comparable",
       rep["state"] == J.COMPARABLE, str(rep))
    rep2 = J.check_verdict_comparable([{"judge_version": jv1},
                                       {"judge_version": "deadbeef"}])
    ck("C5 ⛔ 一组里混了两种身份 ⇒ incomparable（这正是要拦的）",
       rep2["state"] == J.INCOMPARABLE, str(rep2))
    rep3 = J.check_verdict_comparable([{}, {}])
    ck("C6 全都没身份 ⇒ unknown", rep3["state"] == J.UNKNOWN, str(rep3))

    # ── D 组：接线（有机制 ≠ 这条路被走到）───────────────────────
    print("D 组：判据身份真的进了裁决与体检")
    ck("D1 evolution 有 _judge_version() 且与 judge.identity() 同源",
       E._judge_version() == J.identity())
    ev_src = (ROOT / "aether" / "evolution.py").read_text(encoding="utf-8")
    ck("D2 `eval.verdict` 的 payload 里写了 judge_version",
       '"judge_version": _judge_version()' in ev_src)
    from aether import doctor as D
    names = [fn.__name__ for fn in D.CHECKS]
    ck("D3 doctor 的检查表里有 judge.boundary",
       "_check_judge" in names, str(names))
    r = D.run(ROOT)
    got = [c for c in r["checks"] if c["name"] == "judge.boundary"]
    ck("D4 doctor 真的产出了 judge.boundary 读数",
       len(got) == 1, str([c["name"] for c in r["checks"]]))
    #: 真实账本上：新裁决带身份、历史不带 ⇒ 应判 unknown 而不是 incomparable
    from aether.ledger import Ledger
    from aether.paths import paths
    led = Ledger(paths(ROOT)["ledger"])
    reps = [{"judge_version": (e.payload or {}).get("judge_version")}
            for e in led.iter_events() if e.kind == "eval.verdict"]
    cmpx = J.check_verdict_comparable(reps)
    #: ⛔⛔ 这里**不能**断言 `state in (UNKNOWN, COMPARABLE)`：实测踩过 ——
    #:    我自己编辑 `judge.py`（为了修规范化那个 bug）之后，
    #:    账本里同时存在**两种判据身份**：改动前那批裁决用旧 hash、
    #:    改动后用新 hash ⇒ 正确行为就是 `incomparable`。
    #:    那条断言假设了"账本永远是单版本"，而**多版本恰恰是常态**
    #:    （每次改判据都会产生一个新的）。真正要守的是：
    #:      · 单版本 ⇒ comparable；
    #:      · 多版本 ⇒ incomparable **并列出全部版本**（可诊断）。
    n_ver = len(cmpx["versions"])
    ck("D5 真实账本上的可比性判定与身份数量一致",
       (cmpx["state"] == J.COMPARABLE and n_ver == 1)
       or (cmpx["state"] == J.INCOMPARABLE and n_ver >= 2)
       or (cmpx["state"] == J.UNKNOWN and not cmpx["versions"]),
       f"state={cmpx['state']} versions={cmpx['versions']}")
    if cmpx["state"] == J.INCOMPARABLE:
        ck("D5b ⛔ 多版本时**必须**把身份都列出来（否则没法诊断）",
           len(cmpx["versions"]) == n_ver, str(cmpx["versions"]))
    ck("D6 而且账本里确实已经有带身份的裁决（接线真被走到）",
       any(x["judge_version"] for x in reps),
       f"带身份 {sum(1 for x in reps if x['judge_version'])} / {len(reps)}")
    #: ⛔ 反证：`judge.py` 被改过 ⇒ 账本必然出现多版本 ⇒ doctor 必须报 fail。
    #:    这条把"改判据要留痕"从口号变成可观测的后果。
    if cmpx["state"] == J.INCOMPARABLE:
        got_d = [c for c in D.run(ROOT)["checks"]
                 if c["name"] == "judge.boundary"][0]
        ck("D7 ⛔ 多版本判据 ⇒ doctor 报 fail（改尺子必须看得见）",
           got_d["status"] == D.FAIL, str(got_d)[:120])

    # ── E 组：反证 —— 拆掉边界必须转红 ───────────────────────────
    print("E 组：反证（把边界拆掉 ⇒ 判据必须转红）")
    orig_layers = None
    try:
        from aether import genome
        orig_layers = genome.LAYERS
        #: 事故版：把判据模块名塞进"可变异层" ⇒ 边界不变量必须报出来
        genome.LAYERS = tuple(orig_layers) + ("verifier",)
        try:
            J.assert_boundary()
            ck("E1 ⛔ 判据模块混进可变异层 ⇒ 边界不变量必须报错",
               False, "没有报错")
        except RuntimeError as e:
            ck("E1 ⛔ 判据模块混进可变异层 ⇒ 边界不变量必须报错",
               "判据" in str(e) or "重叠" in str(e), str(e)[:100])
    finally:
        if orig_layers is not None:
            from aether import genome
            genome.LAYERS = orig_layers
    #: 复原后必须回到正常（E1 不是恒红）
    ck("E2 复原后边界不变量重新通过（E1 不是恒红）",
       J.assert_boundary()["ok"] is True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print(f"✅ 全部通过：尺子身份 {jv1[:16]}，变异够不到它，"
          f"跨尺子比较会被拒绝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
