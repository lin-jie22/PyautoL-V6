#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：真实代码世界 + `string` 变异维度（r48）。

## 为什么要有这份契约

r47 的系统级判决给出一个一票否决级事实：这台 20793 行的引擎，**历史上从未
走到第 3 代**血缘。修完 resume 死锁后剩下的唯一真问题是能力饱和 ——
`tasks/` 只有 4 个手工 toy，两个胶囊就把 dev 集打满。

于是 r48 铺了管道 `scripts/worldgen.py`：把**真实 Python 源码**（默认 Python
标准库）切成自包含的 `task.py + tests.json`，判据由执行发现、并经 F2P 验证。

本契约不保证"进化一定会更深"（它**没有**—— 见契约末尾的诚实边界），
它保证的是这条管道**每一道闸门都有牙**，以及新加的 `string` 维度
**可逆且正交**。

  A 组：`string` 算子默认关闭 ⇒ `which` 口径不漂移（历史账本可比）
  B 组：`string` 造的题**必须可逆**（`revert_strings` 能修回）
  C 组：`string` 与另外三个维度**正交**（只有它自己修得好）
  D 组：反证 —— 把 `revert_strings` 换成恒等 ⇒ B 组必须转红
  E 组：worldgen 的判据必须 JSON round-trip 无损、且 F2P 打不穿的要剔除
  F 组：环境发现顺序稳定、默认口径仍是四个

用法：
    python tests/contract_worldgen_test.py
"""
import sys
import tempfile
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


#: ⛔ 这段样例**刻意**含一个默认值字符串参数 + 一个参与逻辑的字符串常量：
#:    既能被 `string` 算子击穿，也能验 `ast.literal_eval(default)` 那条路径。
_SRC = '''
def render(items, sep="\\n"):
    head = "ITEMS"
    if len(items) > 2:
        head = head + "(*)"
    return head + sep + sep.join(items)
'''
_TESTS = [
    {"name": "basic", "call": "render", "args": [["a", "b"]],
     "expect": "ITEMS\na\nb"},
    {"name": "many", "call": "render", "args": [["a", "b", "c"]],
     "expect": "ITEMS(*)\na\nb\nc"},
    {"name": "sep", "call": "render", "args": [["x"], ","],
     "expect": "ITEMS,x"},
]


def _env():
    from aether.environment import Environment
    return Environment(env_id="demo_worldgen", source=_SRC,
                       tests=tuple(_TESTS))


def main() -> int:
    from aether.environment import (DEFAULT_MUTATION_KINDS, Environment,
                                    mutate_source, string_literals,
                                    verify_source)
    from aether.repair import (revert_boolops, revert_control_flow,
                               revert_literals, revert_strings)

    env = _env()
    base = verify_source(env, timeout=10.0)
    ck("A0 样例源码基线全绿（否则后面全是假红）", base.reward >= 1.0,
       f"reward={base.reward}")

    # ── A 组：默认口径不漂移 ──────────────────────────────────────
    print("A 组：`string` 默认关闭 ⇒ which 口径不漂移")
    ck("A1 string 不在 DEFAULT_MUTATION_KINDS",
       "string" not in DEFAULT_MUTATION_KINDS,
       str(DEFAULT_MUTATION_KINDS))
    #: ⛔ **A2 曾经是一条过度承诺的断言**，实测把它打回（这是本契约最有价值
    #:    的一条）：原断言是"加 string 前后 `which=0` 必须指向同一个变异点"。
    #:    实测不成立 —— `sep="\n"` 这个字符串常量在 DFS 里排在数值常量 `2`
    #:    之前 ⇒ 启用 string 后**所有 which 编号整体后移**。
    #:
    #:    正确的边界是：**默认口径下 string 分支一次都不触发**，所以历史
    #:    账本与基线不受影响；一旦显式 `--kinds ...,string`，编号含义就变了
    #:    ⇒ 那是**新实验**（与 r44「新口径必须显式开启」同一条纪律），
    #:    不允许拿它和历史基线对比。
    #:    ⇒ 断言改成"默认口径下绝不出现字符串变异"，而不是"编号恒等"。
    descs: list = []
    for i in range(8):
        try:
            _, desc = mutate_source(_SRC, which=i,
                                    kinds=DEFAULT_MUTATION_KINDS)
            descs.append(desc)
        except Exception:                                    # noqa: BLE001
            break
    ck("A2 默认口径下 string 分支一次都不触发（历史编号不漂移）",
       len(descs) >= 1 and all("字符串" not in d for d in descs), str(descs))
    #: 事实记录（不是断言）：显式启用 ⇒ 编号会变，这是新实验的代价。
    try:
        _, d_with = mutate_source(_SRC, which=0,
                                  kinds=tuple(DEFAULT_MUTATION_KINDS)
                                  + ("string",))
        print(f"      事实：启用 string 后 which=0 ⇒ {d_with}（编号已变，"
              f"属新实验口径，不可与历史基线对比）")
    except Exception as e:                                   # noqa: BLE001
        print(f"      事实：启用 string 后无法变异：{type(e).__name__}")

    # ── B 组：string 造的题必须可逆 ────────────────────────────────
    print("B 组：`string` 造的题必须能被 `revert_strings` 修回")
    kinds = ("string",)
    broke = []
    for i in range(8):
        try:
            src, desc = mutate_source(_SRC, which=i, kinds=kinds)
        except Exception:                                    # noqa: BLE001
            continue
        v = verify_source(env, source=src, timeout=10.0)
        if v.reward < 1.0:
            broke.append((src, desc, v.reward))
    ck("B1 string 算子确实能造出真失败的任务（F2P）", len(broke) >= 1,
       f"击穿 {len(broke)} 条")
    ok = 0
    for src, desc, rw in broke:
        fixed = revert_strings(src, env.source)
        rv = verify_source(env, source=fixed, timeout=10.0)
        if rv.reward >= 1.0:
            ok += 1
        else:
            print(f"      修不回：{desc[:40]} reward={rv.reward}")
    ck("B2 每一条都能被 revert_strings 修回（可逆 ⇒ 不是死题）",
       len(broke) > 0 and ok == len(broke), f"{ok}/{len(broke)}")

    # ── C 组：正交（其他三维修不好它）──────────────────────────────
    print("C 组：`string` 与另外三个维度正交")
    others = {"revert_literals": revert_literals,
              "revert_control_flow": revert_control_flow,
              "revert_boolops": revert_boolops}
    bad = []
    for src, desc, _ in broke:
        for nm, fn in others.items():
            rv = verify_source(env, source=fn(src, env.source), timeout=10.0)
            if rv.reward >= 1.0:
                bad.append((nm, desc[:40]))
    ck("C1 其他三个修法都修不好 string 题（⇒ 独立的第四维）",
       not bad, str(bad))

    # ── D 组：反证 ─────────────────────────────────────────────────
    print("D 组：反证 —— 修法被摘掉时 B2 必须转红")
    if broke:
        src = broke[0][0]
        rv = verify_source(env, source=src, timeout=10.0)
        ck("D1 事故版（恒等修法）修不好 ⇒ B2 有牙", rv.reward < 1.0,
           f"reward={rv.reward}")

    # ── E 组：worldgen 的判据纪律 ──────────────────────────────────
    print("E 组：worldgen 判据必须 JSON 无损、F2P 打不穿的要剔除")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "worldgen", ROOT / "scripts" / "worldgen.py")
        wg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wg)
    except Exception as e:                                   # noqa: BLE001
        wg = None
        print(f"      （无法加载 worldgen：{e}）")
    if wg is not None:
        ck("E1 字符串常量收集器与变异器同源（都按 str 判定）",
           len(string_literals(__import__("ast").parse(_SRC))) >= 3,
           f"收集到 {len(string_literals(__import__('ast').parse(_SRC)))} 个")
        ck("E2 expect 必须 JSON round-trip 无损（否则判据不可存账本）",
           wg._jsonable(["a", 1, None]) and not wg._jsonable({1, 2}),
           "set 应判为不可 JSON 化")
        #: ⛔ `f` 也在集合里是对的：函数名本身也是"本模块内绑定"的名字
        #:    （嵌套 def 引用它时不能算外部符号）。断言只看**是否含局部变量**。
        _lb = wg._local_bound(__import__("ast").parse(
            "def f(x):\n    y = 1\n    return y"))
        ck("E3 局部变量必须算内部绑定（曾致 11 个模块全军覆没）",
           {"x", "y"} <= _lb, str(_lb))

    # ── F 组：环境发现 ─────────────────────────────────────────────
    print("F 组：环境发现顺序稳定、默认口径不变")
    from aether.cli import DEFAULT_ENVS, discover_envs
    tasks_root = ROOT / "tasks"
    found = discover_envs(tasks_root)
    ck("F1 发现顺序：DEFAULT_ENVS 在前（历史可比）",
       tuple(found[:len(DEFAULT_ENVS)]) == DEFAULT_ENVS, str(found[:6]))
    ck("F2 自动发现确实扩大了世界（> 4 个）", len(found) > len(DEFAULT_ENVS),
       f"{len(found)} 个：{found}")
    ck("F3 发现的每个目录都真的可用（能构造 Environment）",
       all((tasks_root / n / "task.py").is_file()
           and (tasks_root / n / "tests.json").is_file() for n in found),
       str(found))
    built = 0
    for n in found:
        try:
            Environment.from_dir(tasks_root / n)
            built += 1
        except Exception:                                    # noqa: BLE001
            print(f"      构造失败：{n}")
    ck("F4 每个发现的环境都能被真正加载", built == len(found),
       f"{built}/{len(found)}")

    # ── G 组：题源强度（世界大小会撒谎，它不会）────────────────────
    #
    #   r48 把世界 4 → 10。逐个量下来：
    #       doctest  23 次变异只击穿 1 次    editor  30 次只击穿 1 次
    #   ⇒ 环境数变好看了，题量没涨。本组钉住：**入套件的环境必须有题源强度**。
    print("G 组：题源强度闸门（虚胖环境不许进世界）")
    if wg is not None:
        try:
            e_ho = Environment.from_dir(tasks_root / "textscore")
            n8 = wg.lethal_mutants(e_ho, n_mut=8, timeout=10.0)
            n32 = wg.lethal_mutants(e_ho, n_mut=32, timeout=10.0)
            ck("G1 采样量是主杠杆：which 上限 32 找到的有效变异体 ≥ 上限 8",
               n32 >= n8 >= 1, f"n8={n8} n32={n32}")
            ck("G2 扩大采样真的多找到了（否则 G1 只是恒等断言）",
               n32 > n8 or n8 >= 4, f"n8={n8} n32={n32}（若持平需基线已足够强）")
        except Exception as ex:                              # noqa: BLE001
            ck("G1 题源强度可读（textscore）", False,
               f"{type(ex).__name__}: {ex}")

        #: ⛔ **判据选择**：kill（按变异杀伤率）与 first（取前 N 条 + 按
        #:    当前返回值去重）。同条数下 kill 的杀伤率必须不低于 first。
        try:
            import ast as _ast
            src_ho = (tasks_root / "textscore" / "task.py").read_text(
                encoding="utf-8")
            tree = _ast.parse(src_ho)
            fns = [(n.name, n) for n in tree.body
                   if isinstance(n, _ast.FunctionDef)]
            cases: list = []
            for fn, node in fns:
                cases += wg.gen_cases(fn, node, cap=48)
            res, err = wg.probe_source(src_ho, cases, timeout=18.0)
            ok_map = {r["name"]: r for r in res if r.get("ok")}
            ck("G3 候选判据可探测（否则 G4/G5 无从对照）",
               len(ok_map) >= 4 and not err, f"ok={len(ok_map)} err={err[:60]}")
            if len(ok_map) >= 4:
                kills, n_seen = wg.mutation_kill_matrix(
                    src_ho, cases, ok_map,
                    ("compare", "binop", "constant", "boolop", "unaryop", "string"),
                    24, timeout=18.0)
                ck("G4 杀伤矩阵非退化（至少 1 个变异体被杀死）",
                   n_seen >= 1 and any(kills.values()),
                   f"n_mut_seen={n_seen} kills={len(kills)}")
                names = [nm for nm, _ in fns]
                #: ⛔⛔ **per_call 必须小到"覆盖不全"**，否则两种选法都是满分
                #:    ⇒ G6 变成恒等断言（2026-10-17 实测：per_call=6 时
                #:    textscore 的 4 个变异体被两种选法全部覆盖，4 == 4）。
                #:    ⛔ 同理条数必须**相同**：条数不同 ⇒ 变量不唯一 ⇒
                #:    覆盖差异可能只是"多挑了几条"的功劳。
                pc = 1
                t_first, i_first = wg.pick_tests(cases, ok_map, kills, names,
                                                 mode="first", per_call=pc)
                t_kill, i_kill = wg.pick_tests(cases, ok_map, kills, names,
                                               mode="kill", per_call=pc)
                ck("G5 两种选法条数相同 ⇒ 唯一变量是杀伤力（否则对照不成立）",
                   len(t_first) == len(t_kill) >= 1,
                   f"first={len(t_first)} kill={len(t_kill)}")
                ck("G6 kill 选出的判据集杀伤力 ≥ first（⇒ 这个选择真有用）",
                   i_kill["n_covered"] >= i_first["n_covered"],
                   f"first={i_first['n_covered']} kill={i_kill['n_covered']}")
                #: ⛔ 事实读数必须**无条件**打印：只在失败时打印的话，
                #:    "两边都相等"这种恒等情形永远看不见（第 16 条：不随被测
                #:    对象变化的读数等于没有读数）。
                print(f"      事实：first 覆盖 {i_first['n_covered']} 个变异体"
                      f" / kill 覆盖 {i_kill['n_covered']} 个"
                      f"（总数 {len(set().union(*kills.values())) if kills else 0}）"
                      f"  条数 first={len(t_first)} kill={len(t_kill)}")

                #: ── H 组：反证 ──────────────────────────────────
                print("H 组：反证 —— 拿掉杀伤矩阵，G6 必须失去依据")
                t_kill_empty, i_empty = wg.pick_tests(cases, ok_map, {}, names,
                                                      mode="kill", per_call=pc)
                ck("H1 空矩阵下 kill 仍凑够条数（退化为按参数大小补齐）",
                   len(t_kill_empty) == len(t_first) >= 1,
                   f"empty={len(t_kill_empty)} first={len(t_first)}")
                print(f"      事实：无矩阵 kill 覆盖 {i_empty['n_covered']} 个"
                      f"（有矩阵 {i_kill['n_covered']} 个）")
                ck("H2 ⛔ 反证：有矩阵时的杀伤力 > 无矩阵（否则矩阵是装饰）",
                   i_kill["n_covered"] > i_empty["n_covered"],
                   f"with={i_kill['n_covered']} empty={i_empty['n_covered']}")
        except Exception as ex:                              # noqa: BLE001
            ck("G3 判据选择可对照", False, f"{type(ex).__name__}: {ex}")

    print(f"\n{'=' * 60}")
    print(f"结果：{len(FAILS)} 条失败" if FAILS else "结果：全部通过")
    for f in FAILS:
        print(f"  ✗ {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
