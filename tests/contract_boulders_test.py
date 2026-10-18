#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：架构基线闸（arch_baseline）——「巨石在恶化」必须是一条有牙的闸。

## 为什么需要这份契约

r58 的处置是「`cmd_evolve` 807 行不拆，用 F 段读数盯着」，F 段末尾写着
「真正要防的是**恶化**：新增一条就说明有人又在入口里写编排」。

⛔ 但那句话当时是**空话**：F 段只打印数字、不落基线 ⇒ 「新增一条」只有
   人眼 diff 才看得出来。r59 补的就是这块：落基线、每次比。

## 本文件守四件事（缺一就是假闸）

  1. **不恒红**：现状 = 基线 ⇒ ok。恒红的闸三天内会被人无视（S1）。
  2. **有牙**：新增巨函数 / 模块暴涨 ⇒ worse，且指名道姓（S2）。
  3. **不装绿**：没有基线 ⇒ `unknown`，**绝不**因为「没看到新增」就报 ok
     （第 57 条：空集合当分母最会撒谎）（S3）。
  4. **洗白留痕**：更新基线吃掉恶化 ⇒ 进 `acknowledged`，只增不删（S4）。

外加三条同源 / 口径守护：
  S5 量具只有一份（arch_scan 与 doctor 同一个 `measure`）；
  S6 余量的边界是真的（+249 不红 / +251 红）；
  S7 doctor 三态与闸同源，且无基线时是 skipped 不是 ok。

跑法（纯内存 runner，秒级）：
    python tests/contract_boulders_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

SUPPORTED_MODES = ("default",)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def ck(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name} {detail if not cond else ''}")
    if not cond:
        FAILS.append(name)


def _mk_pkg(tmp: Path, pkg: str = "pkg"):
    """造一个人造包（含 __init__ 与一个 200 行巨函数）。"""
    d = tmp / pkg
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "big.py").write_text(
        "def huge(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(200))
        + "    return x0\n", encoding="utf-8")
    return d


def _measure(tmp: Path, pkg: str = "pkg"):
    from aether import arch_baseline as ab
    return ab.measure_package(tmp / pkg, pkg=pkg)


def _root_with(tmp: Path):
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    return tmp


def main() -> int:
    from aether import arch_baseline as ab

    # ──────────────────────────────────────────────────────────
    print("== S1 组：不恒红（现状 = 基线 ⇒ ok）==")
    d1 = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s1_")))
    _mk_pkg(d1)
    cur1 = _measure(d1)
    ab.write(ab.baseline_path(d1), cur1, note="S1")
    dd1, _ = ab.compare(d1, pkg_dir=d1 / "pkg", pkg="pkg")
    ck("S1.1 现状 = 基线 ⇒ ok", dd1["state"] == ab.STATE_OK, f"{dd1['state']}")
    ck("S1.2 n_worse = 0", dd1.get("n_worse", 0) == 0, f"{dd1.get('n_worse')}")
    ck("S1.3 有巨石也不许红（判的是恶化，不是「有没有巨石」）",
       len(cur1["big_fn"]) >= 1 and dd1["state"] == ab.STATE_OK,
       f"big_fn={list(cur1['big_fn'])}")

    # ──────────────────────────────────────────────────────────
    print("\n== S2 组：有牙（造恶化必须被抓到，且指名）==")
    (d1 / "pkg" / "newbig.py").write_text(
        "def brand_new(a):\n" + "".join(f"    y{i} = {i}\n" for i in range(260))
        + "    return y0\n", encoding="utf-8")
    cur2 = _measure(d1)
    dd2 = ab.diff(ab.read(ab.baseline_path(d1)), cur2)
    ck("S2.1 新增 262 行函数 ⇒ worse", dd2["state"] == ab.STATE_WORSE,
       f"{dd2['state']}")
    ck("S2.2 指名道姓（new_big_fn 里有它）",
       any("brand_new" in s for s in dd2["new_big_fn"]), f"{dd2['new_big_fn']}")

    #: 模块暴涨（+251 行，超过余量 250）
    p = d1 / "pkg" / "big.py"
    src = p.read_text(encoding="utf-8")
    p.write_text(src + "".join(f"# pad {i}\n" for i in range(251)), encoding="utf-8")
    cur3 = _measure(d1)
    dd3 = ab.diff(ab.read(ab.baseline_path(d1)), cur3)
    ck("S2.3 模块 +251 行 ⇒ worse（grown_mod）",
       dd3["state"] == ab.STATE_WORSE and len(dd3["grown_mod"]) > 0,
       f"{dd3['grown_mod']}")

    #: 变小**不算**恶化，但要看得见（拆成功了不能被读成「没变化」）
    #: ⛔ 两种变小必须分开：还 ≥150（shrunk） vs 彻底拆没了（gone）
    #: ⛔ 先把造恶化用的 newbig.py 撤掉：留着它 ⇒ 下面每一组都带着
    #:    「新增巨函数」这一项 ⇒ 改善被恶化掩盖，读数看不出单变量效果
    #:    （第 35 条：对照要「条数相同只差一个变量」）。
    (d1 / "pkg" / "newbig.py").unlink()
    base1 = ab.read(ab.baseline_path(d1))
    #: ⛔ 余量是 100 ⇒ 必须涨过 100 才算恶化（202→262 只涨 60，不该红）。
    #:    这里用 202→322（+120），刚好跨过余量。
    (d1 / "pkg" / "big.py").write_text(
        "def huge(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(320))
        + "    return x0\n", encoding="utf-8")
    dd4a = ab.diff(base1, _measure(d1))
    ck("S2.4 巨函数涨 120 行（超余量 100）⇒ worse",
       dd4a["state"] == ab.STATE_WORSE and dd4a["grown_fn"],
       f"{dd4a['grown_fn']}")
    (d1 / "pkg" / "big.py").write_text(
        "def huge(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(170))
        + "    return x0\n", encoding="utf-8")
    dd4b = ab.diff(base1, _measure(d1))
    ck("S2.5 仍 ≥150 但变小（202→172）⇒ shrunk 且不红",
       any("huge" in s for s in dd4b["shrunk"])
       and dd4b["state"] == ab.STATE_OK, f"{dd4b['shrunk']}")
    #: ⛔ 最好的结果：拆到 150 以下 ⇒ 从 big_fn 里消失。它既不进 shrunk
    #:    也不进 new_big_fn ⇒ 若没有 gone，「拆成功了」在报告里看不见。
    (d1 / "pkg" / "big.py").write_text(
        "def huge(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(100))
        + "    return x0\n", encoding="utf-8")
    dd4c = ab.diff(base1, _measure(d1))
    ck("S2.6 ⛔ 拆到 150 行以下 ⇒ gone_big_fn 必须被列出（第 49 条）",
       any("huge" in s for s in dd4c["gone_big_fn"]), f"{dd4c['gone_big_fn']}")
    ck("S2.7 拆成功不许被判恶化（否则「去拆」会被惩罚）",
       dd4c["state"] == ab.STATE_OK, f"{dd4c['state']}")
    ck("S2.8 render 里要看得见这个好消息",
       "拆掉" in ab.render(dd4c), f"{ab.render(dd4c)}")

    # ──────────────────────────────────────────────────────────
    print("\n== S3 组：不装绿（没基线 ⇒ unknown，不是 ok）==")
    d3 = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s3_")))
    _mk_pkg(d3)
    cur5 = _measure(d3)
    ck("S3.1 没有基线文件 ⇒ unknown",
       ab.diff(None, cur5)["state"] == ab.STATE_UNKNOWN)
    ck("S3.2 ⛔ 空基线（没有任何模块读数）⇒ unknown，不是 ok",
       ab.diff({"modules": {}, "big_fn": {}, "over": []}, cur5)["state"]
       == ab.STATE_UNKNOWN,
       "空基线若报 ok，这条闸就永远绿着")
    ck("S3.3 unknown 的 reason 不许为空（要说明为什么不可判别）",
       bool(ab.diff(None, cur5)["reason"]))
    dd3b, _ = ab.compare(d3, pkg_dir=d3 / "pkg", pkg="pkg")
    ck("S3.4 compare 在无基线时也报 unknown",
       dd3b["state"] == ab.STATE_UNKNOWN, f"{dd3b['state']}")
    #: 坏基线必须被识别成「坏了」，不是「没有」（第 25 条：一个值两种含义）
    bp3 = ab.baseline_path(d3)
    bp3.write_text("{ this is not json", encoding="utf-8")
    try:
        ab.read(bp3)
        ck("S3.5 坏基线必须抛 BaselineError（不许被读成「没有」）", False, "没抛")
    except ab.BaselineError:
        ck("S3.5 坏基线必须抛 BaselineError（不许被读成「没有」）", True)
    #: schema 不符同理
    bp3.write_text(json.dumps({"schema": 999, "baseline": cur5}), encoding="utf-8")
    try:
        ab.read(bp3)
        ck("S3.6 schema 不符 ⇒ BaselineError", False, "没抛")
    except ab.BaselineError:
        ck("S3.6 schema 不符 ⇒ BaselineError", True)

    # ──────────────────────────────────────────────────────────
    print("\n== S4 组：洗白留痕（更新基线吃掉恶化 ⇒ acknowledged）==")
    d4 = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s4_")))
    _mk_pkg(d4)
    c0 = _measure(d4)
    bp4 = ab.baseline_path(d4)
    ab.write(bp4, c0, note="S4 首次")
    ck("S4.1 首次建基线 ⇒ acknowledged 为空",
       json.loads(bp4.read_text(encoding="utf-8"))["n_acknowledged"] == 0)
    (d4 / "pkg" / "another.py").write_text(
        "def another(a):\n" + "".join(f"    z{i} = {i}\n" for i in range(300))
        + "    return z0\n", encoding="utf-8")
    c1 = _measure(d4)
    obj = ab.write(bp4, c1, note="接受：这是有意的", prev=c0)
    ck("S4.2 吃掉恶化 ⇒ acknowledged 记 1 条", obj["n_acknowledged"] == 1,
       f"{obj['n_acknowledged']}")
    ck("S4.3 留痕里带原因与被吃掉的量",
       obj["acknowledged"][0]["note"] == "接受：这是有意的"
       and obj["acknowledged"][0]["n_worse"] >= 1,
       f"{obj['acknowledged'][0]}")
    #: ⛔ 只增不删：再写一次（没恶化）⇒ 历史留痕不许丢
    obj2 = ab.write(bp4, c1, note="再写一次", prev=c1,
                    acked=json.loads(bp4.read_text(encoding="utf-8"))["acknowledged"])
    ck("S4.4 ⛔ 只增不删：没恶化地重写 ⇒ 留痕仍在",
       obj2["n_acknowledged"] == 1, f"{obj2['n_acknowledged']}")

    # ──────────────────────────────────────────────────────────
    print("\n== S5 组：量具只有一份（第 13 条）==")
    ck("S5.1 arch_scan 的 build_graph 就是 arch_baseline 的那个",
       _scan_build_graph_is_shared())
    ck("S5.2 arch_scan 的 F 段常量取自 arch_baseline（不另写一份）",
       _scan_constants_shared())
    #: 同名同参 ⇒ 两条路径量出来的数必须一样
    d5 = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s5_")))
    _mk_pkg(d5)
    m_direct = ab.measure(sorted((d5 / "pkg").glob("*.py")),
                          ab.build_graph(sorted((d5 / "pkg").glob("*.py")),
                                         d5)[1], pkg="pkg")
    m_pkg = ab.measure_package(d5 / "pkg", pkg="pkg")
    ck("S5.3 measure 与 measure_package 同源（同一批文件 ⇒ 同一读数）",
       m_direct == m_pkg, f"{m_direct} vs {m_pkg}")

    # ──────────────────────────────────────────────────────────
    print("\n== S6 组：余量边界是真的（不是拍脑袋的常数）==")
    d6 = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s6_")))
    (d6 / "pkg").mkdir()
    (d6 / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (d6 / "pkg" / "a.py").write_text("X = 1\n", encoding="utf-8")
    c6 = _measure(d6)
    #: 模块 +249（余量内）⇒ 不红；+251（超余量）⇒ 红
    p6 = d6 / "pkg" / "a.py"
    p6.write_text("X = 1\n" + "".join(f"# p{i}\n" for i in range(249 - 1)),
                  encoding="utf-8")
    ck("S6.1 模块 +248 行（余量 250 内）⇒ ok",
       ab.diff(c6, _measure(d6))["state"] == ab.STATE_OK,
       f"{ab.diff(c6, _measure(d6))['grown_mod']}")
    p6.write_text("X = 1\n" + "".join(f"# p{i}\n" for i in range(251)),
                  encoding="utf-8")
    ck("S6.2 模块 +251 行（超余量）⇒ worse",
       ab.diff(c6, _measure(d6))["state"] == ab.STATE_WORSE,
       f"{ab.diff(c6, _measure(d6))['grown_mod']}")
    #: 巨函数余量 100：+99 不红 / +101 红
    d6b = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s6b_")))
    (d6b / "pkg").mkdir()
    (d6b / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (d6b / "pkg" / "b.py").write_text(
        "def f(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(200))
        + "    return x0\n", encoding="utf-8")
    c6b = _measure(d6b)
    pb = d6b / "pkg" / "b.py"
    pb.write_text("def f(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(200 + 99))
                  + "    return x0\n", encoding="utf-8")
    ck("S6.3 巨函数 +99 行（余量 100 内）⇒ ok",
       ab.diff(c6b, _measure(d6b))["state"] == ab.STATE_OK)
    pb.write_text("def f(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(200 + 101))
                  + "    return x0\n", encoding="utf-8")
    ck("S6.4 巨函数 +101 行（超余量）⇒ worse",
       ab.diff(c6b, _measure(d6b))["state"] == ab.STATE_WORSE,
       f"{ab.diff(c6b, _measure(d6b))['grown_fn']}")

    # ──────────────────────────────────────────────────────────
    print("\n== S7 组：doctor 消费点（三态，无基线 ⇒ skipped 不是 ok）==")
    from aether import doctor as D
    d7 = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_s7_")))
    #: ⛔ doctor 的 `compare(root)` 默认量 `<root>/aether` ⇒ 造包必须叫 aether，
    #:    否则它退回量当前代码的包 ⇒ 造出来的恶化一个都不算（r59 实测踩到）。
    _mk_pkg(d7, pkg="aether")
    (d7 / "data").mkdir(parents=True, exist_ok=True)
    r7 = D._check_boulders(d7, {})
    ck("S7.1 没基线 ⇒ skipped（第 57 条：没量过 ≠ 没恶化）",
       r7.status == D.SKIPPED, f"{r7.status} / {r7.detail}")
    ck("S7.2 skipped 必须带原因", bool(r7.skipped_reason), f"{r7.skipped_reason}")
    ck("S7.3 action 指向真有的命令", "--write-baseline" in (r7.action or ""),
       f"{r7.action}")
    #: 建基线 ⇒ ok
    ab.write(ab.baseline_path(d7), _measure(d7, pkg="aether"), note="S7")
    r7b = D._check_boulders(d7, {})
    ck("S7.4 有基线且没恶化 ⇒ ok", r7b.status == D.OK, f"{r7b.status}")
    ck("S7.5 ok 的 detail 里要写明实测读数（不许只说「没问题」）",
       "模块" in (r7b.detail or ""), f"{r7b.detail}")
    #: 造恶化 ⇒ fail，且 action 里点名
    (d7 / "aether" / "newbig.py").write_text(
        "def oops(a):\n" + "".join(f"    w{i} = {i}\n" for i in range(280))
        + "    return w0\n", encoding="utf-8")
    r7c = D._check_boulders(d7, {})
    ck("S7.6 造恶化 ⇒ fail", r7c.status == D.FAIL, f"{r7c.status}")
    ck("S7.7 fail 的 detail 指名道姓", "oops" in (r7c.detail or ""),
       f"{r7c.detail}")
    ck("S7.8 ⛔ 判据没变松：巨石**变小**不许红（拆成功了是好事）",
       _shrink_is_not_fail(), "变小被判红会让「去拆」这件事被惩罚")

    print()
    print("=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


def _shrink_is_not_fail() -> bool:
    from aether import arch_baseline as ab
    from aether import doctor as D
    tmp = _root_with(Path(tempfile.mkdtemp(prefix="v6_b_shrink_")))
    (tmp / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "aether").mkdir()
    (tmp / "aether" / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "aether" / "c.py").write_text(
        "def f(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(400))
        + "    return x0\n", encoding="utf-8")
    ab.write(ab.baseline_path(tmp), _measure(tmp, pkg="aether"), note="shrink")
    #: 把它拆小（400 → 120 行）
    (tmp / "aether" / "c.py").write_text(
        "def f(a):\n" + "".join(f"    x{i} = {i}\n" for i in range(120))
        + "    return x0\n", encoding="utf-8")
    r = D._check_boulders(tmp, {})
    return r.status == D.OK


def _scan_build_graph_is_shared() -> bool:
    """`scripts/arch_scan.py` 不再拥有自己的 build_graph 实现。"""
    src = (ROOT / "scripts" / "arch_scan.py").read_text(encoding="utf-8")
    return ("_ab.build_graph" in src
            and "def build_graph" in src
            and "edges[(m, tgt)] += 1" not in src)


def _scan_constants_shared() -> bool:
    src = (ROOT / "scripts" / "arch_scan.py").read_text(encoding="utf-8")
    return ("VERDICT_LAYERS = _ab.VERDICT_LAYERS" in src
            and "ENTRY_LAYERS = _ab.ENTRY_LAYERS" in src)


if __name__ == "__main__":
    raise SystemExit(main())
