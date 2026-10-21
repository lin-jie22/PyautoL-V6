#!/usr/bin/env python3
"""契约：纯类库模块的「模板问法」—— 让覆盖率探针**自动**长到类上。

═══ 为什么需要它（r62 读数，README 123）═══

`auto_probes` 只认两类：模块级常量、模块级无参函数。
真实 SWE-bench 里纯类库模块（`OrderedSet` 那种）两类都没有 ⇒
自动探针 0 条 ⇒ 7 条判据 / 22K 源码 ⇒ `yield=0.0`。
r62 的结论是「只能人给问法」——人手写了 5 条同类探针才把覆盖率拉起来。

本契约检验：这套「人给问法」能被**模板**代替，且不违反既有纪律。

═══ 不猜输入 ≠ 不生成候选（关键区分）═══

`auto_probes` 只**枚举候选表达式**，真值一律由 `calibrate_coverage` 拿
gold 源码真跑得出；求值失败的候选当场丢弃。
⇒ 模板给出的"输入夹具"即使不合适，也只会被丢掉，**不会留下假判据**。
这正是既有 `calibrate_coverage` 的设计意图（只收"当前版本为绿"）。

⛔ 所以本契约不断言"生成了 N 条"，而是断言**自动生成的探针真能撞到变异**
（E 组：同一表达式在 bug 版 / gold 版给出不同结果）。只数条数就是
规则 25 的形态 —— 计数器涨了，判据没牙。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS = []

#: ⛔ 必须声明：不声明 ⇒ `check` 会报「未声明支持的模式」并只按默认模式跑一次。
#:    本契约不跑 `aether` 子进程、不依赖 `--e2e` 开关，故只有默认模式。
SUPPORTED_MODES = ("default",)


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def _load_swb():
    spec = importlib.util.spec_from_file_location(
        "swb_import_under_test", ROOT / "scripts" / "swb_import.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load_swb()

# ═══ 私有夹具（第 114 条：不许依赖"本机恰好装了某个包"）═══
# 逻辑与 django 的 OrderedSet 同形，代码自造 ⇒ 夹具坏了是夹具的事，
# 不会因为上游改版而无声漂移。
_SET_BODY = '''
class OrderedSet:
    """插入序集合（最小实现，只留本契约要测的成员）。"""

    def __init__(self, iterable=()):
        self._d = {}
        for x in iterable:
            self._d[x] = None

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)

    def __contains__(self, x):
        return x in self._d

    def add(self, x):
        self._d[x] = None

    def discard(self, x):
        self._d.pop(x, None)

    def __eq__(self, other):
        return list(self) == list(other)
'''

BUG_SRC = _SET_BODY                       # 缺 __reversed__ ⇒ reversed() 抛 TypeError
FIXED_SRC = _SET_BODY + '''
    def __reversed__(self):
        return iter(list(self._d)[::-1])
'''

#: 第二个变异：`__eq__` 里的 `==` 反转。
#: ⛔ 为什么必须有这一个（本轮实测）：196 行的夹具上，`__reversed__` 一类被
#:    撞到了、`__eq__` 那类**一条都没撞到** ⇒ 22 条判据的 yield 与 2 条
#:    完全相同（0.1 vs 0.1）。只测一个变异种类会把"缺一整个维度"读成"够用"。
BUG2_SRC = FIXED_SRC.replace(
    "return list(self) == list(other)",
    "return list(self) != list(other)", 1)
assert BUG2_SRC != FIXED_SRC, "BUG2 夹具替换没命中"


def main() -> int:
    print("=" * 62)
    print("A 组 · 缺口本身要被记录：**旧两条路径**在纯类库模块上是 0 条")
    print("=" * 62)
    a = M.auto_probes(FIXED_SRC)
    legacy = [p for p in a if p["name"].startswith(("const_", "fn_"))]
    #: ⛔ 断言的是「旧路径」而不是「全部」：本契约正是来补类的，
    #:    把全部写成 0 就等于断言"补的东西不存在"（改前绿、改后必红）。
    ck("A1 旧两条路径（常量 / 无参函数）在纯类库模块上仍是 0 条",
       legacy == [] and "class " in FIXED_SRC,
       f"旧路径 {len(legacy)} 条，类模板补了 {len(a) - len(legacy)} 条")
    #: ⛔ 反证：如果夹具里塞一个常量，旧路径**必须**有产出 ——
    #:    否则 A1 的「0 条」可能只是因为夹具读错了，不是因为缺口。
    a_ctl = M.auto_probes(FIXED_SRC + "\nSENTINEL = 7\n")
    ctl = [p["expr"] for p in a_ctl if p["name"].startswith("const_")]
    ck("A2 对照：同一个夹具加一个模块级常量 ⇒ 旧路径立刻有 1 条（证明 A1 不是读歪）",
       ctl == ["SENTINEL"], f"{ctl}")

    print("\n" + "=" * 62)
    print("B 组 · 模板问法：类 ⇒ 构造表达式 + 单目观察表达式")
    print("=" * 62)
    exprs = {p["expr"] for p in M.auto_probes(FIXED_SRC)}
    ck("B1 无参构造（`__init__` 全默认 ⇒ 零参调用是确定的，不是猜）",
       "OrderedSet()" in exprs, f"exprs={len(exprs)} 条")
    ck("B2 夹具构造（标准夹具枚举，合适与否交给 calibrate 用 gold 真值筛）",
       "OrderedSet([1, 2, 3])" in exprs, "")
    ck("B3 单目观察（len / list / bool 这类不引入新输入的纯观察）",
       any(e.startswith("len(OrderedSet") for e in exprs)
       and any(e.startswith("list(OrderedSet") for e in exprs), "")
    #: ⛔ 生成器**不许**把「每次求值都不同」的表达式塞进来（id / 默认 repr）：
    #:    那样的"判据"在两次求值间就自相矛盾 ⇒ 变异体永远被判失败。
    ck("B4 ⛔ 不生成非确定表达式（repr / str / id 这类带地址的）",
       not any(("repr(" in e or "id(" in e or e.startswith("str("))
               for e in exprs), "")
    #: ⛔ 索引是**协议**（`__getitem__`），模板够得着；而 `last()`/`snapshot()`
    #:    这类普通方法够不着（调用要传参 ⇒ 那是猜）。这条断言把边界钉在
    #:    "协议方法可以、普通方法不行"，防止有人后来往模板里塞 `obj.some_method()`。
    ck("B6 索引协议（`x[0]` / `x[-1]`）在模板里 —— 它们是 `__getitem__`，不是猜",
       any(e.endswith("[0]") for e in exprs)
       and any(e.endswith("[-1]") for e in exprs), "")

    #: ⛔⛔ 判据的 expect 要**落进 tests.json**，而 JSON 没有 tuple：
    #:    `tuple([1,2,3])` 存成 `[1,2,3]` ⇒ 执行器求回 tuple ⇒ 恒不相等。
    #:    实测代价：25 条自动探针里 5 条基线就红（`got=(1, 2, 3) expect=[1, 2, 3]`）。
    #:    ⇒ 判据不是"生成出来就算数"，值必须在 **JSON 往返后逐字相等且同类型**。
    #: ⛔ 只检查**会被收下**的（可序列化的）那些：返回对象的构造表达式
    #:    本来就会被 `calibrate_coverage` 丢弃，不该在这里算违规 ——
    #:    否则 B5 与生产验收标准不一致，变成我自己的尺子。
    unstable_json = []
    for p in M.auto_probes(FIXED_SRC):
        ok, v, _ = M.eval_in_ns(FIXED_SRC, p["expr"])
        if not ok:
            continue
        try:
            back = json.loads(json.dumps(v))
        except Exception:                                # noqa: BLE001
            continue            # 不可序列化 ⇒ 落盘前就被丢，不是本组的事
        if type(back) is not type(v) or back != v:
            unstable_json.append((p["expr"], v, back))
    ck("B5 ⛔⛔ 会落盘的表达式其值要经得起 JSON 往返（类型与值都不许变）",
       not unstable_json, f"{unstable_json[:3]}")

    print("\n" + "=" * 62)
    print("C 组 · 稳定性：同一条模板表达式求值两次必须逐字相同")
    print("=" * 62)
    unstable = []
    for p in M.auto_probes(FIXED_SRC):
        o1, v1, _ = M.eval_in_ns(FIXED_SRC, p["expr"])
        o2, v2, _ = M.eval_in_ns(FIXED_SRC, p["expr"])
        if not o1 or not o2:
            continue            # 求值失败的由 calibrate 丢弃，不是本组的事
        if v1 != v2:
            unstable.append((p["expr"], v1, v2))
    ck("C1 ⛔ 没有一条表达式在两次求值间给出不同值", not unstable,
       f"{unstable[:3]}")

    print("\n" + "=" * 62)
    print("D 组 · 反证：把模板关掉，B 组必须全红（断言不是死代码）")
    print("=" * 62)
    keep = M._CLASS_TEMPLATES if hasattr(M, "_CLASS_TEMPLATES") else None
    ck("D1 模板是**具名的**（关得掉才证明它真在起作用）", keep is not None,
       f"{'有' if keep is not None else '没有 _CLASS_TEMPLATES'}")
    if keep is not None:
        M._CLASS_TEMPLATES = ()           # 关掉
        try:
            off = {p["expr"] for p in M.auto_probes(FIXED_SRC)}
        finally:
            M._CLASS_TEMPLATES = keep     # 还原
        ck("D2 关掉模板后只剩旧路径的 0 条 ⇒ B 组的三条断言确实由它提供",
           off == set() or all(not e.startswith(("len(OrderedSet",
                                                "list(OrderedSet"))
                               for e in off),
           f"关掉后 {len(off)} 条：{sorted(off)[:5]}")

    print("\n" + "=" * 62)
    print("E 组 · 有牙：自动探针必须真能撞到**每一种**变异（不是「多生成了几条」）")
    print("=" * 62)
    #: 用 calibrate_coverage 走**生产同一条路径**（只收 gold 为绿的），
    #: 再拿收下来的探针去 bug 版求值 —— 有鉴别力的会给出不同结果。
    kept, dropped = M.calibrate_coverage(FIXED_SRC, M.auto_probes(FIXED_SRC))

    def _killers(bug_src: str):
        out = []
        for t in kept:
            ob, vb, _ = M.eval_in_ns(bug_src, t["call"])
            og, vg, _ = M.eval_in_ns(FIXED_SRC, t["call"])
            if (not ob or vb != vg) and og:
                out.append(t["call"])
        return out

    for tag, bug in (("缺 __reversed__", BUG_SRC), ("__eq__ 反转", BUG2_SRC)):
        k = _killers(bug)
        ck(f"E1[{tag}] 至少一条自动探针撞到它 ⇒ 覆盖率真覆盖到这个维度",
           bool(k), f"撞到的：{k[:2]}")
    ck("E2 覆盖率探针的验收标准是「gold 为绿」，不是「必须区分两版」",
       len(kept) > 0, f"收 {len(kept)} / 丢 {len(dropped)}")

    print("\n" + "=" * 62)
    print("F 组 · 端到端：物化成真环境后，基线必须 **100% 绿**")
    print("=" * 62)
    #: ⛔⛔ 为什么必须有这一组（本轮实测踩到）：B5 之前，`eval_in_ns` 里
    #:    25 条全绿，但物化落盘再经**生产执行器**跑，只有 20/25 ——
    #:    两个执行器对 tuple/list 的处理不同。只测自己那条路径
    #:    就等于"用我写的校验器验我写的生成器"。
    import tempfile
    sys.path.insert(0, str(ROOT))
    try:
        from aether.environment import Environment, verify_source
    except Exception as e:                               # noqa: BLE001
        ck("F1 能加载生产执行器", False, f"{type(e).__name__}: {e}")
        verify_source = None
    if verify_source is not None:
        kept, _ = M.calibrate_coverage(FIXED_SRC, M.auto_probes(FIXED_SRC))
        tests = [{"name": t["name"], "call": t["call"], "expect": t["expect"]}
                 for t in kept]
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "probe_env"
            d.mkdir()
            (d / "task.py").write_text(FIXED_SRC, encoding="utf-8")
            (d / "tests.json").write_text(json.dumps(
                {"env_id": "probe_env", "tests": tests}, ensure_ascii=False),
                encoding="utf-8")
            rep = verify_source(Environment.from_dir(d))
        bad = [c.name + " | " + c.evidence for c in rep.checks if not c.passed]
        ck("F1 ⛔ 落盘后的基线全绿（不是「我这条路径全绿」）",
           rep.n_passed == len(rep.checks) and len(rep.checks) == len(tests),
           f"{rep.n_passed}/{len(rep.checks)}；红：{bad[:2]}")

    print("\n" + "=" * 62)
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败：{FAILS}")
        return 1
    print("✅ 全部通过：纯类库模块的覆盖率可以靠模板自动拉起，"
          "且自动探针真能撞到变异")
    return 0


if __name__ == "__main__":
    sys.exit(main())
