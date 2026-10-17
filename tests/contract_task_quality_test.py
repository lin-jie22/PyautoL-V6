#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：题源的「含金量」—— 打红原因三态分账（r50）。

## 为什么要有这份契约

r49 加了**题源强度闸**（有效变异体 < min-lethal ⇒ 弃），把世界从"看大小"
改成"看题量"。但 `valid`（打红了几条）自己也会撒谎：它数的是"测试变红了"，
不区分**为什么**红。

实测（r50）当场抓到：

    env           valid  behavior  crash  含金量
    eventflow         6         1      5    17%     ← 默认 dev 环境之一
    shlex             1         0      1     0%
    合计              42        34      8    81%

崩溃题（raise / import-failed）与行为变化题（mismatch）在**题面上**长得一样
（都是"测试红了"），但含金量完全不同：崩溃题任何"把源码改回接近基线"的
修法都能顺手修好 —— 甚至恒等变换都能在别的维度上蒙对。⇒ 它们会把题源
强度读数**吹胖**：账面上多了一堆题，reward 里完全看不见。

这跟 r49 里 `editor` 那个虚胖环境是**同一个病**，区别只是：那次是人工发现的，
这次量具自己看得见。

⛔ 立这条契约的直接原因，是要回答一个更硬的问题：
    **r48 跑出的「第 3 代」，是不是建立在崩溃题上？**
    如果崩溃题被过滤掉之后第 3 代消失了，那 r48 的结论就是假的。

  I 组：`why` 由 runner 产出、分类函数归一正确（三态分开，unknown 不合并）
  J 组：`valid` = behavior + crash + unknown（分账必须闭合）
  K 组：`require_behavior` 开启后，**只留**真行为变化的题
  L 组：反证 —— 把分类函数改成恒返回 behavior ⇒ K 组必须转红
  M 组：默认口径不变（不传参 ⇒ 与显式 False 的任务集完全一致）

用法：
    python tests/contract_task_quality_test.py
"""
import ast
import sys
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS: list = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} "
          f"{detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    from aether.environment import (Environment, classify_failure,
                                    synthesize_tasks, verify_source)

    #: ── I 组：runner 的 `why` 与分类函数 ──────────────────────
    print("I 组：打红原因必须由**产出方**写死，不许靠下游猜文案")

    ck("I1 classify(mismatch)=behavior（真行为变化）",
       classify_failure("mismatch") == "behavior")
    ck("I2 classify(raise)=crash / classify(import)=crash",
       classify_failure("raise") == "crash"
       and classify_failure("import") == "crash")
    ck("I3 ⛔ classify(None)=unknown —— 装置故障不许冒充 behavior",
       classify_failure(None) == "unknown")
    ck("I4 ⛔ 未知字符串也归 unknown（不许静默并入任何一类）",
       classify_failure("got=x expect=y") == "unknown")

    #: 三类都要能**真实产生**（否则分类是死代码）
    def _mk(src, tests, eid):
        return Environment(env_id=eid, source=src, tests=tuple(tests))

    e_raise = _mk("def f(x):\n    return x / 0\n",
                  [{"name": "r", "call": "f", "args": [1], "expect": 1}],
                  "demo_raise")
    e_behav = _mk("def f(x):\n    return x + 1\n",
                  [{"name": "b", "call": "f", "args": [1], "expect": 3}],
                  "demo_behavior")
    e_impor = _mk("raise RuntimeError('boom')\n",
                  [{"name": "i", "call": "f", "args": [], "expect": 1}],
                  "demo_import")

    got = {}
    for tag, e in (("raise", e_raise), ("behavior", e_behav),
                   ("import", e_impor)):
        v = verify_source(e, timeout=10.0)
        why = dict((v.evidence or {}).get("why") or {})
        vals = set(why.values())
        got[tag] = vals
        ck(f"I5 {tag} 场景真能产出对应的 why（不是死代码）",
           len(vals) == 1, f"why={why}")
    ck("I6 raise 场景 ⇒ why=raise", got.get("raise") == {"raise"},
       f"{got.get('raise')}")
    ck("I7 behavior 场景 ⇒ why=mismatch", got.get("behavior") == {"mismatch"},
       f"{got.get('behavior')}")
    ck("I8 import 场景 ⇒ why=import（整段崩也必须有归属，不许 None）",
       got.get("import") == {"import"}, f"{got.get('import')}")

    #: ── J 组：分账闭合 ───────────────────────────────────────
    print("\nJ 组：valid 必须等于三态之和（分账闭合，不许漏记）")
    env_dir = ROOT / "tasks" / "eventflow"
    if (env_dir / "task.py").is_file():
        env = Environment.from_dir(env_dir)
        rep = synthesize_tasks(env, max_n=12, timeout=10.0,
                               kinds=("compare", "binop", "constant", "boolop",
                                      "unaryop", "string"))
        s = rep.n_behavior + rep.n_crash + rep.n_unknown
        #: ⛔ 读数**无条件打印**：只在失败时打印的话"两边相等"永远看不见
        print(f"      事实：eventflow valid={rep.valid} "
              f"behavior={rep.n_behavior} crash={rep.n_crash} "
              f"unknown={rep.n_unknown} 含金量={rep.behavior_rate:.0%}")
        ck("J1 valid == behavior + crash + unknown", rep.valid == s,
           f"{rep.valid} != {s}")
        ck("J2 ⛔ 这个环境确实有崩溃题（否则本契约的判据是空的）",
           rep.n_crash >= 1, f"crash={rep.n_crash}")
        ck("J3 每条任务的 fail_reason 都在三态之内",
           all(t.fail_reason in ("behavior", "crash", "unknown")
               for t in rep.tasks),
           str(sorted({t.fail_reason for t in rep.tasks})))
        ck("J4 ⛔ reason 计数与逐条 fail_reason 一致（不许两本账）",
           sum(1 for t in rep.tasks if t.fail_reason == "behavior")
           == rep.n_behavior)
        #: ── K 组：require_behavior ───────────────────────────
        print("\nK 组：开启 require_behavior 后只留真行为变化的题")
        rep_k = synthesize_tasks(env, max_n=12, timeout=10.0,
                                 kinds=("compare", "binop", "constant",
                                        "boolop", "unaryop", "string"),
                                 require_behavior=True)
        print(f"      事实：开启后 valid {rep.valid} → {rep_k.valid}，"
              f"过滤掉 {rep_k.rejected_not_behavior} 条")
        ck("K1 开启后 valid 严格变少（否则这个开关没起作用）",
           rep_k.valid < rep.valid, f"{rep_k.valid} vs {rep.valid}")
        ck("K2 ⛔ 剩下的题**全部**是 behavior（一条崩溃都不许留）",
           all(t.fail_reason == "behavior" for t in rep_k.tasks),
           str(sorted({t.fail_reason for t in rep_k.tasks})))
        ck("K3 过滤掉的条数 == 原来的非 behavior 条数",
           rep_k.rejected_not_behavior == rep.valid - rep.n_behavior,
           f"{rep_k.rejected_not_behavior} vs {rep.valid - rep.n_behavior}")

        #: ── L 组：反证 ───────────────────────────────────────
        print("\nL 组：反证 —— 分类函数失效 ⇒ K2 必须失去判别力")
        import aether.environment as AE
        orig = AE.classify_failure
        try:
            AE.classify_failure = lambda why: "behavior"     #: 事故版
            rep_bad = synthesize_tasks(env, max_n=12, timeout=10.0,
                                       kinds=("compare", "binop", "constant",
                                              "boolop", "unaryop", "string"),
                                       require_behavior=True)
            leaked = [t.task_id for t in rep_bad.tasks
                      if t.fail_reason == "behavior"]
            #: 事故版下会把崩溃题也放行 ⇒ 放行条数回到"未过滤"的水平
            print(f"      事实（事故版）：放行 {rep_bad.valid} 条 "
                  f"（正常版 {rep_k.valid} 条）")
            ck("L1 ⛔ 反证：分类恒为 behavior ⇒ 闸放行更多题（闸有牙）",
               rep_bad.valid > rep_k.valid,
               f"bad={rep_bad.valid} good={rep_k.valid}")
            ck("L2 反证：事故版放行条数 == 未开启过滤时的条数（全漏）",
               rep_bad.valid == rep.valid,
               f"bad={rep_bad.valid} unfiltered={rep.valid}")
            del leaked
        finally:
            AE.classify_failure = orig

        #: ── M 组：默认口径不变 ───────────────────────────────
        print("\nM 组：默认口径必须与显式 False 完全一致（历史可比）")
        rep_m = synthesize_tasks(env, max_n=12, timeout=10.0,
                                 kinds=("compare", "binop", "constant",
                                        "boolop", "unaryop", "string"),
                                 require_behavior=False)
        ck("M1 默认(False) 与显式 False 的任务集相同",
           sorted(t.task_id for t in rep_m.tasks)
           == sorted(t.task_id for t in rep.tasks))
        ck("M2 默认口径下 rejected_not_behavior 恒为 0",
           rep.rejected_not_behavior == 0)
    else:
        ck("J0 环境目录存在", False, f"缺 {env_dir}")

    #: ── N 组：安静失效的算子（unaryop）必须被钉住 ────────────
    print("\nN 组：钉住「安静失效」这一类 bug —— 工具坏了不许被读成"
          "「世界里没有」")
    from aether.environment import mutate_source
    import ast as _ast
    ck("N0 当前解释器的 UnaryOp 操作数字段名（环境事实，非断言）",
       getattr(_ast.UnaryOp, "_fields", ()) == ("op", "operand"),
       f"_fields={getattr(_ast.UnaryOp, '_fields', None)}")

    _src_u = ("def f(x, y):\n"
              "    if not x:\n"
              "        return 0\n"
              "    if not (x and y):\n"
              "        return 1\n"
              "    return 2\n")
    #: ⛔ 连续多个 which 都要能**真的变异**（不是抛 AttributeError）
    n_ok = n_err = 0
    err_samples = []
    for w in range(3):
        try:
            s, d = mutate_source(_src_u, which=w, kinds=("unaryop",))
            if s and d:
                n_ok += 1
        except ValueError:
            pass                      #: "没有可变异点"是正常的（which 越界）
        except Exception as ex:                              # noqa: BLE001
            n_err += 1
            err_samples.append(f"w{w}:{type(ex).__name__}")
    ck("N1 ⛔ unaryop 算子在本解释器上**不抛异常**（曾因 .value/.operand "
       "改名 100% 失效）", n_err == 0, str(err_samples))
    ck("N2 unaryop 至少能造出一次真变异（不是死算子）", n_ok >= 1, f"ok={n_ok}")

    #: 第五维：可逆 + 正交
    from aether.repair import (revert_unaryops, revert_control_flow,
                               revert_literals, revert_boolops,
                               revert_strings)
    mut_u, _ = mutate_source(_src_u, which=0, kinds=("unaryop",))
    fixed_u = revert_unaryops(mut_u, _src_u)
    ck("N3 第五维可逆：`revert_unaryops` 把 unaryop 题修回基线",
       fixed_u.strip() == _src_u.strip(), fixed_u[:80])
    others_changed = 0
    for fn in (revert_control_flow, revert_literals, revert_boolops,
               revert_strings):
        if fn(mut_u, _src_u).strip() != mut_u.strip():
            others_changed += 1
    ck("N4 ⛔ 第五维正交：其余四个修法**连改动都没有**（各管一类节点）",
       others_changed == 0, f"改动的修法数={others_changed}")
    #: 反证：拿掉第五维 ⇒ N3 必须失去依据
    ck("N5 反证：不用 revert_unaryops 时，其它四维修不好 unaryop 题",
       all(fn(mut_u, _src_u).strip() != _src_u.strip()
           for fn in (revert_control_flow, revert_literals,
                      revert_boolops, revert_strings)))
    ck("N6 `fix_unaryops` 已在技能词汇表里（否则 proposer 提不出来）",
       "fix_unaryops" in __import__("aether.agent", fromlist=["x"]
                                    ).KNOWN_STRATEGIES)

    #: ── N7：递归对齐的 **None 守卫**（2026-10-17 实测回归）────────
    #: `revert_unaryops` 用"沿两棵树同一条路径递归对齐"，而 AST 的**列表
    #: 字段**里合法地含 `None`（`ast.arguments.kw_defaults == [None]`）
    #: ⇒ 原版 `gn._fields` 直接抛 `AttributeError: 'NoneType' object has
    #: no attribute '_fields'` ⇒ **修法抛异常** ⇒ 冻结任务从通过变不通过
    #: （监督器 `REGRESSION_DETECTED` 当场抓到 frozen::_writer 1→0）。
    #: ⛔ 这类写法的通病：假定"列表里每个元素都是节点"，而 AST 字段域是
    #:    Optional[AST]。⇒ fail-closed：任一边缺节点 ⇒ 原样返回。
    _w = Environment.from_dir(ROOT / "tasks" / "_writer", env_id="_writer")
    _tree = ast.parse(_w.source)
    _has_none_in_list = any(
        any(x is None for x in getattr(n, f))
        for n in ast.walk(_tree) if isinstance(n, ast.arguments)
        for f in ("kw_defaults",) if isinstance(getattr(n, f, None), list))
    ck("N7 ⛔ 触发条件**真实存在于源码里**（不是假想）："
       "`ast.arguments.kw_defaults` 含 None",
       _has_none_in_list)
    _rep_w = synthesize_tasks(_w, max_n=8,
                              kinds=("compare", "binop", "constant",
                                     "boolop", "unaryop", "string"))
    _errs = []
    for t in _rep_w.tasks:
        try:
            revert_unaryops(t.broken_source, _w.source)
        except Exception as ex:                              # noqa: BLE001
            _errs.append(f"{t.task_id}: {type(ex).__name__}: {ex}")
    ck("N8 ⛔ 该环境的**每条**破坏题上第五维修法都不抛异常"
       f"（实测 {len(_rep_w.tasks)} 条；曾 2/2 全崩）",
       not _errs and len(_rep_w.tasks) >= 1, str(_errs[:2]))

    print(f"\n{'=' * 60}")
    print(f"结果：{len(FAILS)} 条失败" if FAILS else "结果：全部通过")
    for f in FAILS:
        print(f"  ✗ {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
