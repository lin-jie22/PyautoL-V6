#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：变异点的**采样口径**（r51）。

## 为什么要有这份契约

`which` 是 `_Mutator` 的**全局 DFS 序号**：遍历时遇到的第 i 个可变异点
（**不分种类**）就是 `which=i`。于是 AST 里数量最多的那种算子会把前 N 个
序号**全部占满**，后加的维度一条题都进不来。

r50 撞到的就是它：第五维 `unaryop` 已经修好、能单独造出 14 条题，但在
`max_n=12` 的实际采样里是 **0 条**。账面结论会是「这个世界里没有 unaryop
题」—— 而真相是**采样轮不到它**。

2026-10-17 实测（14 环境 / 全算子 / max_n=12，尝试次数相同）：

    顺序采样   59 条题   string=19 constant=17 compare=12
                         binop=8   boolop=2   unaryop=1
    均衡采样   81 条题   binop=20  compare=18 constant=16
                         unaryop=11 boolop=9  string=7

⇒ 总题量 +37%，最稀缺的两个维度翻了 5–11 倍；dev 环境（textscore）从
「3 条全是 compare」变成「6 条 / 3 个维度」。

⛔ 这份契约守三件事，**缺一件就会有人把口径改动读成世界变化**：

  S1 组：点位枚举与真实变异**同源**（另写一份枚举必错位 —— 这个病在本仓库
         出现过三次：BFS/DFS 错位 ×2、`unaryop` 字段改名 ×1）
  S2 组：均衡序列的定义（长度不超、各维度差 ≤1、确定性）
  S3 组：**默认口径零影响**（`balanced=False` 时绝不走新代码路径）
  S4 组：真实环境上均衡采样确实出更多维度，且**反证有牙**
  S5 组：采样口径进报告（不记 ⇒ 题量涨了不知道是换口径还是换世界）

用法：
    python tests/contract_sampling_test.py
"""
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


#: ⛔ 维度归类必须与 `_Mutator` 写的 desc **逐字对应**。
#:    改了描述文字而这里没同步 ⇒ S1 会转红（这是刻意的：量具与被测同源）。
_PREFIX = (
    ("比较符反转", "compare"),
    ("运算符替换", "binop"),
    ("布尔运算", "boolop"),
    ("取反 删除 not", "unaryop"),
    ("字符串常量", "string"),
    ("常量 ", "constant"),
)


def kind_of(desc: str) -> str:
    for pref, name in _PREFIX:
        if desc.startswith(pref):
            return name
    return "?"


def main() -> int:
    import aether.environment as E
    from aether.environment import (ALL_MUTATION_KINDS, Environment,
                                    balanced_which_order, mutate_source,
                                    mutation_sites, synthesize_tasks)

    tasks_dir = ROOT / "tasks"

    #: ── S1 组：点位枚举与真实变异同源 ──────────────────────────
    print("S1 组：点位表必须与真实变异**同源**（另写一份枚举必错位）")
    for name in ("textscore", "eventflow", "chaos"):
        env = Environment.from_dir(tasks_dir / name, env_id=name)
        sites = mutation_sites(env.source, ALL_MUTATION_KINDS)
        checked = 0
        bad = []
        unavailable = 0
        for i, k in enumerate(sites[:24]):
            try:
                _, desc = mutate_source(env.source, which=i,
                                        kinds=ALL_MUTATION_KINDS)
            except Exception:                                # noqa: BLE001
                #: 点位存在但**变不动**（空串无变体 / 比较符不在 _FLIP_OPS）
                #: ⇒ 这不是不一致，是两件事。⛔ 绝不能把它读成"点位不存在"。
                unavailable += 1
                continue
            kk = kind_of(desc)
            checked += 1
            if kk != k:
                bad.append(f"which={i} 枚举={k} 实际={kk}")
        ck(f"S1.{name} 前 24 个点位逐个反查一致（同源）",
           not bad and checked >= 8,
           f"校验 {checked} 个 / 变不动 {unavailable} 个；不一致 {bad[:3]}")

    #: ⛔ 探针模式不许产生任何变异：`which=-1` 遍历完整棵树但 `done` 恒假
    e_demo = Environment.from_dir(tasks_dir / "textscore", env_id="textscore")
    try:
        mutate_source(e_demo.source, which=-1, kinds=ALL_MUTATION_KINDS)
        ck("S1.4 ⛔ `which=-1` 是探针模式：**必须**抛异常（不许偷偷变异）",
           False, "它居然返回成功了")
    except Exception:                                        # noqa: BLE001
        ck("S1.4 ⛔ `which=-1` 是探针模式：**必须**抛异常（不许偷偷变异）",
           True)

    #: ── S2 组：均衡序列的定义 ──────────────────────────────────
    print("S2 组：均衡序列的定义（长度不超 / 各维度差 ≤1 / 确定性）")
    sites_t = mutation_sites(e_demo.source, ALL_MUTATION_KINDS)
    n_sites = len(sites_t)
    for max_n in (6, 12, 24, 4096):
        order = balanced_which_order(sites_t, max_n)
        ck(f"S2.{max_n} 长度 = min(max_n, 点位数)（⛔ 尝试次数不变）",
           len(order) == min(max_n, n_sites),
           f"实得 {len(order)} 期望 {min(max_n, n_sites)}")
        ck(f"S2.{max_n}b 无重复点位",
           len(set(order)) == len(order))
    order12 = balanced_which_order(sites_t, 12)
    by = {}
    for w in order12:
        k = sites_t[w]
        by[k] = by.get(k, 0) + 1
    if by:
        #: ⛔ 判据**不能**写成"各维度被采次数差 ≤ 1" —— 那只在"每个维度
        #:    点位数都够"时才成立。点位不足的维度第二轮就取不到了，
        #:    配额自然跑到别人头上（实测 textscore：binop/unaryop 各只有
        #:    1 个点位 ⇒ 分布 3/3/2/2/1/1，差是 2，但那是**正确**的）。
        #:    ⇒ 轮流公平的**真判据**是：还剩点位却比最多那个少 ≥2 ⇒ 违规。
        total_by = {}
        for s in sites_t:
            total_by[s] = total_by.get(s, 0) + 1
        maxc = max(by.values())
        viol = [(k, c, maxc) for k, c in by.items()
                if c < total_by[k] and c < maxc - 1]
        ck("S2.5 轮流公平：**还剩点位**却比最多的少 ≥2 ⇒ 违规"
           f"（点位耗尽的不算；实测分布 {by}）",
           not viol, str(viol))
    ck("S2.6 ⛔ 确定性：同输入两次调用结果相同",
       balanced_which_order(sites_t, 12) == order12)
    ck("S2.7 ⛔ 均衡 ≠ 顺序：两者**长度相同但取点不同**（否则是空转）",
       len(order12) == len(list(range(min(12, n_sites))))
       and order12 != list(range(min(12, n_sites))), str(order12))
    #: 全点位时：均衡应当**覆盖**全部点位（一个不漏，只是顺序不同）
    all_order = balanced_which_order(sites_t, n_sites)
    ck("S2.8 配额给足时覆盖全部点位（只是换顺序，不丢点位）",
       sorted(all_order) == list(range(n_sites)))

    #: ── S3 组：默认口径零影响 ──────────────────────────────────
    print("S3 组：默认（balanced=False）**绝不**走新代码路径")
    _real = E.mutation_sites

    def _explode(*a, **k):                                   # noqa: ANN001
        raise AssertionError("默认口径不该调用 mutation_sites")

    E.mutation_sites = _explode
    try:
        rep_def = synthesize_tasks(e_demo, max_n=6,
                                   kinds=ALL_MUTATION_KINDS)
        ok = True
        err = ""
    except AssertionError as ex:
        ok, err = False, str(ex)
    finally:
        E.mutation_sites = _real
    ck("S3.1 ⛔ 默认路径不调用 `mutation_sites`（装了炸弹也没炸到）", ok, err)
    ck("S3.2 默认报告的 `sampling` 记 sequential",
       rep_def.sampling == "sequential", rep_def.sampling)
    ck("S3.3 默认报告**不**写维度账（None，不是空 dict —— 空 dict 会被读成"
       "「枚举过但没点位」）",
       rep_def.sites_by_kind is None and rep_def.chosen_by_kind is None)
    #: 默认任务集：与"逐个 which 顺序采样"的题一致（口径未漂）
    seq_ids = [t.task_id for t in rep_def.tasks]
    ck("S3.4 默认任务 id 仍是 `m{i}` 顺序形态（口径未漂）",
       all(tid.split("::")[-1].startswith("m") for tid in seq_ids)
       or not seq_ids, str(seq_ids[:3]))

    #: ── S4 组：真实环境上均衡采样确实带来新维度 ────────────────
    print("S4 组：真实环境（dev=textscore）上，均衡采样确实出更多维度")
    rep_seq = synthesize_tasks(e_demo, max_n=12,
                               kinds=ALL_MUTATION_KINDS, balanced=False)
    rep_bal = synthesize_tasks(e_demo, max_n=12,
                               kinds=ALL_MUTATION_KINDS, balanced=True)

    def dims(rep) -> set:
        return {kind_of(t.desc) for t in rep.tasks}

    d_seq, d_bal = dims(rep_seq), dims(rep_bal)
    print(f"     顺序：{len(rep_seq.tasks)} 题 / 维度 {sorted(d_seq)}")
    print(f"     均衡：{len(rep_bal.tasks)} 题 / 维度 {sorted(d_bal)}")
    ck("S4.1 ⛔ 尝试次数相同（这是「只差一个变量」的前提）",
       rep_seq.attempted == rep_bal.attempted,
       f"{rep_seq.attempted} vs {rep_bal.attempted}")
    ck("S4.2 均衡采样的题量 ≥ 顺序",
       len(rep_bal.tasks) >= len(rep_seq.tasks),
       f"{len(rep_bal.tasks)} vs {len(rep_seq.tasks)}")
    ck("S4.3 ⛔ 均衡采样覆盖**更多维度**（dev 环境不再被单一算子垄断）",
       len(d_bal) > len(d_seq), f"{sorted(d_bal)} vs {sorted(d_seq)}")
    ck("S4.4 报告记了口径与维度账",
       rep_bal.sampling == "balanced"
       and isinstance(rep_bal.sites_by_kind, dict)
       and isinstance(rep_bal.chosen_by_kind, dict)
       and rep_bal.sites_by_kind, str(rep_bal.sampling))
    ck("S4.5 ⛔ `chosen_by_kind` 是**采到**的点位账，不是点位总账"
       "（两者不同 ⇒ 说明确实只采了一部分）",
       rep_bal.chosen_by_kind != rep_bal.sites_by_kind,
       f"{rep_bal.chosen_by_kind} / {rep_bal.sites_by_kind}")

    #: ⛔ 反证：把均衡实现退化成顺序 ⇒ S4.3 必须失去依据（断言有牙）
    _real_order = E.balanced_which_order
    E.balanced_which_order = lambda sites, n: list(range(min(n, len(sites))))
    try:
        rep_fake = synthesize_tasks(e_demo, max_n=12,
                                    kinds=ALL_MUTATION_KINDS, balanced=True)
    finally:
        E.balanced_which_order = _real_order
    d_fake = dims(rep_fake)
    ck("S4.6 ⛔ 反证：实现退化成顺序后维度数回落 ⇒ S4.3 有牙（不是恒真）",
       len(d_fake) < len(d_bal), f"退化后 {sorted(d_fake)} vs {sorted(d_bal)}")

    #: 第二个证据：顺序采样**一条题都出不来**的环境
    e_utils = Environment.from_dir(tasks_dir / "utils", env_id="utils")
    if (tasks_dir / "utils" / "task.py").exists():
        u_seq = synthesize_tasks(e_utils, max_n=12,
                                 kinds=ALL_MUTATION_KINDS, balanced=False)
        u_bal = synthesize_tasks(e_utils, max_n=12,
                                 kinds=ALL_MUTATION_KINDS, balanced=True)
        print(f"     utils 顺序 {len(u_seq.tasks)} 题 / "
              f"均衡 {len(u_bal.tasks)} 题")
        ck("S4.7 顺序采样 0 题的环境，均衡后能出题（题源被采样方式浪费掉了）",
           len(u_bal.tasks) > len(u_seq.tasks),
           f"{len(u_bal.tasks)} vs {len(u_seq.tasks)}")

    #: ── S5 组：口径进报告 / 进账本 ─────────────────────────────
    print("S5 组：采样口径必须可观测（报告 + 账本配置）")
    d = rep_bal.to_dict()
    ck("S5.1 `to_dict()` 带 sampling / sites_by_kind / chosen_by_kind",
       "sampling" in d and "sites_by_kind" in d and "chosen_by_kind" in d,
       str(sorted(d)))
    cli_src = (ROOT / "aether" / "cli.py").read_text(encoding="utf-8")
    ck("S5.2 CLI 有 `--balanced` 开关（新口径必须显式开启）",
       '"--balanced"' in cli_src)
    ck("S5.3 ⛔ `balanced` 进账本 run_config（不记 ⇒ 读不出换的是口径还是世界）",
       '"balanced": bool(_balanced)' in cli_src)
    #: ⛔ 用**局部片段**判定而不是全文件计数：全文件计数会被"又加了一处
    #:    合法调用"打破（那不是回归，计数断言却会红 ⇒ 假警报）。
    _i = cli_src.find("FROZEN_SYNTH_N")
    _seg = cli_src[_i:_i + 300] if _i >= 0 else ""
    ck("S5.4 ⛔ 冻结套件的合成调用**不**带 balanced（基线锚点不许随采样"
       "实验漂移）",
       _i >= 0 and "balanced" not in _seg, _seg[:120])

    #: ── S6 组：难度层的降级自愈（r51 撞到的死锁的出口）──────────
    print("S6 组：缺难度层时必须有出口（否则『明明有增益却 0 提交』）")
    ck("S6.1 CLI 有 depth 降级补层分支，且**记账**（不是静默换口径）",
       "depth_fallback" in cli_src and '"n_added"' in cli_src)
    ck("S6.2 ⛔ 降级分支只在 depth≥2 缺层时触发（默认 depth=1 零影响）",
       "if not g[\"covered\"] and _depth >= 2:" in cli_src)
    #: ⛔ 有牙性：降级到单点变异**确实**能采到 easy 层 —— 否则出口是空的
    from aether.evolution import FrozenTask
    from aether.repair import difficulty_report
    rep_d1 = synthesize_tasks(e_demo, max_n=12, kinds=ALL_MUTATION_KINDS,
                              depth=1, balanced=True)
    ft = [FrozenTask(task_id=t.task_id,
                     spec={"env_id": "textscore",
                           "broken_source": t.broken_source,
                           "topic": "textscore", "task_class": "repair"})
          for t in rep_d1.tasks]
    dr = difficulty_report(e_demo, ft)
    print(f"     depth=1 均衡：{len(rep_d1.tasks)} 题 "
          f"easy={dr['n_easy']} hard={dr['n_hard']}")
    ck("S6.3 ⛔ 降级到 depth=1 确实能采到 easy 层（出口不是空的）",
       dr["n_easy"] >= 1, f"easy={dr['n_easy']} hard={dr['n_hard']}")

    print(f"\n{'=' * 60}")
    print(f"结果：{len(FAILS)} 条失败" if FAILS else "结果：全部通过")
    for f in FAILS:
        print(f"  ✗ {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
