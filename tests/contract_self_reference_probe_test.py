#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：自指负载探针必须是**只读**的、且它的判据不许是恒真的。

═══ 为什么这条要守 ═══

`scripts/probe_self_reference.py` 回答的是"这套系统有多少产出在给自己造
机器"。这类探针有个特殊风险：**它的读数会被当成结论引用**。所以：

  · 扫错了（假阳）⇒ 会把正常接口报成死代码，人会去删有用的东西；
  · 扫空了（假阴）⇒ 会给出"很健康"的假读数，比没有读数更坏；
  · 它自己**不许**改仓库 —— 探针改状态就不再是取证工具。

本文件守四条：
  A **只读**：探针的源码里没有任何写仓库/写账本/跑进化的动作；
  B **判据会响**：注入一个人造的死接口 ⇒ 必须被抓到（反证）；
  C **判据不冤报**：被真实调用的接口 / 协议方法 / CLI 入口 ⇒ 不许报死；
  D 账本的机制/增益分类**与账本的真实事件名一致**（实测踩过两次：
    先写裸名而账本是 `eval.verdict`、改成全名后归一化又把它去掉了
    ⇒ 增益恒 0；一个"不报错但永远指向同一结论"的假仪表）。

跑法（离线，秒级）：
    python tests/contract_self_reference_probe_test.py
"""
import importlib.util
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


def _load_probe():
    """把探针当模块加载（它是脚本，没有包路径）。"""
    spec = importlib.util.spec_from_file_location(
        "probe_self_reference", ROOT / "scripts" / "probe_self_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    P = _load_probe()
    src = (ROOT / "scripts" / "probe_self_reference.py").read_text(encoding="utf-8")

    # ── A 组：只读 ───────────────────────────────────────────────
    print("A 组：探针只读（不改仓库、不写账本、不跑进化）")
    banned = ["ledger.append", ".write_text(", "os.replace", "shutil.move",
              "cli_main(", "cmd_evolve", "subprocess.run", "unlink(",
              "mkdir(", "store.save_run"]
    hit = [b for b in banned if b in src.split("def _main")[0]]
    ck("A1 探针主体没有任何写动作（append/write/replace/mkdir/subprocess…）",
       not hit, f"命中：{hit}")
    ck("A2 它读账本用的是**只读**文本读（不是 Ledger 对象）",
       "read_text(encoding=\"utf-8\"" in src and "Ledger(" not in src,
       "出现 Ledger( 说明可能顺手写了账")

    # ── B 组：判据会响（反证）────────────────────────────────────
    print("B 组：反证 —— 注入人造死接口必须被抓到")
    with tempfile.TemporaryDirectory() as td:
        fake_root = Path(td)
        (fake_root / "aether").mkdir()
        (fake_root / "tests").mkdir()
        (fake_root / "scripts").mkdir()
        #: 一个**确定零调用方**的公开函数 + 一个被调用的对照
        (fake_root / "aether" / "mod.py").write_text(
            "def totally_uncalled_api():\n"
            "    return 1\n"
            "\n"
            "def called_api():\n"
            "    return 2\n"
            "\n"
            "def _private_helper():\n"
            "    return 3\n", encoding="utf-8")
        (fake_root / "aether" / "user.py").write_text(
            "from .mod import called_api\n"
            "\n"
            "def go():\n"
            "    return called_api()\n", encoding="utf-8")
        old_root = P.ROOT
        old_dirs = (P.SOURCE_DIRS, P.CALL_SITE_DIRS)
        try:
            P.ROOT = fake_root
            P.SOURCE_DIRS = ("aether",)
            P.CALL_SITE_DIRS = ("aether", "tests", "scripts")
            got = P.scan_dead_api()
        finally:
            P.ROOT = old_root
            P.SOURCE_DIRS, P.CALL_SITE_DIRS = old_dirs
        names = {d["name"] for d in got["dead_api"]}
        ck("B1 ⛔ 零调用方的公开函数被抓到（判据不是恒真）",
           "totally_uncalled_api" in names, str(sorted(names)))
        ck("B2 ⛔ 被真实调用的函数不冤报",
           "called_api" not in names, str(sorted(names)))
        ck("B3 下划线私有函数不进这份报告（口径是「公开接口」）",
           "_private_helper" not in names, str(sorted(names)))

    # ── C 组：真实仓库上不许冤报关键入口 ─────────────────────────
    print("C 组：真实仓库上，已知被调用/协议的东西不许被报为死")
    real = P.scan_dead_api()
    real_names = {d["name"] for d in real["dead_api"]}
    for must_not in ("to_dict", "from_dict", "main", "identity", "fingerprint",
                     "probe", "run", "report"):
        ck(f"C 不冤报 {must_not}()", must_not not in real_names)
    ck("C5 报告给出了「这是下界」的口径说明（读数不许被当成全量）",
       "下界" in real["note"], real["note"][:60])

    # ── D 组：账本分类与真实事件名一致 ───────────────────────────
    print("D 组：机制/增益分类与账本真实事件名一致（假仪表的守门）")
    ck("D1 `_bare()` 会去掉前缀", P._bare("eval.verdict") == "verdict",
       P._bare("eval.verdict"))
    ck("D2 增益表认得 `eval.verdict`",
       P._bare("eval.verdict") in P.GAIN_KINDS)
    ck("D3 机制表认得 `harness.commit` / `cap.candidate`",
       P._bare("harness.commit") in P.MECHANISM_KINDS
       and P._bare("cap.candidate") in P.MECHANISM_KINDS)
    lg = P.mechanism_vs_gain(ROOT / "data" / "ledger.jsonl")
    if lg.get("present"):
        ck(f"D4 真实账本上有增益事件（增益数 > 0 ⇒ 判据没被架空）",
           (lg["gain_total"] or 0) > 0, f"gain_total={lg['gain_total']}")
        ck("D5 ⛔ `commit_yield` 被标记为**废读数**"
           "（revert 无 gain 键 ⇒ 它恒等于 1.0，是循环论证）",
           lg.get("commit_yield_is_vacuous") is True,
           str(lg.get("commit_yield")))
        ck("D6 ⛔ 账本上无法把增益与机制对齐到同一轮次 ⇒ 必须**如实说**",
           lg.get("verdict_has_round_no") is False
           and "无法回答" in lg.get("join_note", ""),
           lg.get("join_note", "")[:60])
        ck("D7 按会话分组的比值有读数（能算的那种近似）",
           len(lg.get("by_sid") or []) >= 3, str(len(lg.get("by_sid") or [])))
    else:
        ck("D4 账本不存在 ⇒ 跳过（不假装通过）", True, lg.get("note", ""))

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：探针只读、判据会响、不冤报、分类与账本同源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
