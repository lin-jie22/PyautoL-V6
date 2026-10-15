#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""契约：能力候选是**提案**，不是安装 —— 闸、留痕、人批准三样都要在。

═══ 这条契约守的是什么 ═══

用户问的是："会自己去 GitHub 和其它资源网找后装上吗？"

结论：**不做直接自动安装**（会同时踩宪法/沙箱/信任三条，见
`aether/discover.py` 与 `aether/caps_candidate.py` 的说明），
但也不该完全不许 —— 真实需求是"这个工具没装，装上就能干活"。
⇒ 做成 **发现 → 候选 → 过闸 → 账本留痕 → 人批准 → 人装 → 复验**。

本文件守这条链的**每一条纪律**（缺一条，"人批准"就会退化成装饰）：

  A 候选只对**值得提**的情形生成（缺工具 / 工具在但没包装）；
    "装了但探针失败"**不许**生成安装候选（装一遍解决不了它）；
  B 闸是真的：探针复核、启动词白名单、拒绝削弱校验的参数、
    必须有复验方式；四项**逐项记因**（不是只报第一个）；
  C **人批准**：没有 `--yes` 一律不推进；没通过闸的候选不得被批准；
    批准必须留名；
  D **跨进程状态**：`validate` → `approve` → `hint` 是三次独立调用，
    状态必须存活（实测踩过：不落盘时批准状态丢失，`hint` 报 validated）；
  E **留痕**：状态进账本（`cap.candidate`），两种结果都记；
  F 反证：危险参数 / 未批准 / 幽灵工具 都必须被拦住。

跑法（离线，秒级）：
    python tests/contract_caps_candidate_test.py
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
    from aether import caps_candidate as CC
    from aether import discover as D
    from aether.ledger import Ledger

    tmp = Path(tempfile.mkdtemp(prefix="v6_cand_"))
    led = Ledger(tmp / "ledger.jsonl")

    # ── A 组：候选只对值得提的情形生成 ───────────────────────────
    print("A 组：谁该成为候选（不硬凑提案）")
    ghost = D.Tool(name="ghost", exe="zzz_no_such_binary_xyz",
                   capability={"id": "tool.ghost", "kind": "tool"})
    c_missing = CC.from_probe(D.probe(ghost),
                              install_argv=["brew", "install", "ghost"])
    ck("A1 缺工具 ⇒ missing_tool 候选", c_missing is not None
       and c_missing.kind == "missing_tool", str(c_missing and c_missing.kind))
    #: 装了但探针失败 ⇒ **不许**生成安装候选
    err_probe = D.Probe(tool="broken", status="error", path="/x/y",
                        detail="探针超时")
    ck("A2 ⛔ '装了但探针失败' **不生成**安装候选（装解决不了它）",
       CC.from_probe(err_probe) is None)
    #: 已包装好的工具 ⇒ 也不该生成候选
    ok_probe = D.Probe(tool="fine", status="found", path="/bin/fine",
                       version="1.0",
                       capability={"id": "tool.fine", "kind": "tool",
                                   "impl": {"transport": "cli",
                                            "argv": ["/bin/fine"]}})
    ck("A3 已包装的工具不生成候选（否则清单永远是脏的）",
       CC.from_probe(ok_probe) is None)
    ck("A4 候选 id 是内容寻址（同一提案 ⇒ 同一 id）",
       c_missing.cap_id
       == CC.from_probe(D.probe(ghost),
                        install_argv=["brew", "install", "ghost"]).cap_id)

    # ── B 组：闸是真的 ───────────────────────────────────────────
    print("B 组：过闸（四项，逐项记因）")
    c = CC.from_probe(D.probe(ghost), install_argv=["brew", "install", "ghost"])
    c = CC.validate(c)
    ck("B1 正常提案 ⇒ 过闸", c.gate_passed and c.state == CC.VALIDATED,
       str(c.gate_reasons))
    #: 启动词白名单
    bad1 = CC.validate(CC.from_probe(D.probe(ghost),
                                     install_argv=["totally-unknown-pm", "x"]))
    ck("B2 ⛔ 启动词不在白名单 ⇒ 拒（黑名单拦不住没见过的包管理器）",
       not bad1.gate_passed and "白名单" in " ".join(bad1.gate_reasons),
       str(bad1.gate_reasons))
    #: 削弱校验/提权的参数
    bad2 = CC.validate(CC.from_probe(
        D.probe(ghost),
        install_argv=["pip", "install", "--break-system-packages", "x"]))
    ck("B3 ⛔ 含 --break-system-packages ⇒ 拒",
       not bad2.gate_passed and "削弱校验" in " ".join(bad2.gate_reasons),
       str(bad2.gate_reasons))
    #: 探针复核（提案过期）
    stale = CC.from_probe(D.probe(ghost), install_argv=["brew", "install", "ghost"])
    ok_again = D.Probe(tool="ghost", status="found", path="/bin/ghost",
                       version="1.0",
                       capability={"id": "tool.ghost", "kind": "tool"})
    stale = CC.validate(stale, probe=ok_again)
    ck("B4 ⛔ 探针复核发现提案已过期 ⇒ 拒（别批一张过期的票）",
       not stale.gate_passed and "过期" in " ".join(stale.gate_reasons),
       str(stale.gate_reasons))
    #: 逐项记因（不是只报第一个）
    multi = CC.from_probe(D.probe(ghost), install_argv=["evil-pm", "x"])
    multi.verify_hint = ""      # 再制造一个失败项
    multi = CC.validate(multi, non_regression_ok=False)
    ck("B5 ⛔ 多项失败时**逐项记因**（只报第一个会让人修完一个又冒一个）",
       len(multi.gate_reasons) >= 3, str(multi.gate_reasons))

    # ── C 组：人批准 ─────────────────────────────────────────────
    print("C 组：人批准（没有 --yes 一律不推进）")
    c = CC.validate(CC.from_probe(D.probe(ghost),
                                 install_argv=["brew", "install", "ghost"]))
    c_no = CC.approve(c, by="linshaojie", yes=False)
    ck("C1 ⛔ 没有 --yes ⇒ 不推进（仍是 validated）",
       c_no.state == CC.VALIDATED
       and "缺少显式批准" in " ".join(c_no.gate_reasons), str(c_no.state))
    ck("C2 没批准时不给安装提示（不催办未批准的提案）",
       CC.install_hint(c_no) == [])
    c_ok = CC.approve(c_no, by="linshaojie", yes=True)
    ck("C3 带 --yes 且留名 ⇒ approved", c_ok.state == CC.APPROVED
       and c_ok.approved_by == "linshaojie", str(c_ok.state))
    ck("C4 批准后才给安装命令", CC.install_hint(c_ok) == ["brew", "install", "ghost"],
       str(CC.install_hint(c_ok)))
    #: 不留名不许批准
    c_noname = CC.validate(CC.from_probe(D.probe(ghost),
                                         install_argv=["brew", "install", "ghost"]))
    c_noname = CC.approve(c_noname, by="", yes=True)
    ck("C5 ⛔ 不留名不许批准（谁批的必须可查）",
       c_noname.state != CC.APPROVED, str(c_noname.state))
    #: 未过闸的候选不许被批准（批一条已知不过闸的 = 把闸当摆设）
    c_bad = CC.validate(CC.from_probe(D.probe(ghost),
                                      install_argv=["evil-pm", "x"]))
    c_bad = CC.approve(c_bad, by="linshaojie", yes=True)
    ck("C6 ⛔ 未过闸的候选不得被批准",
       c_bad.state != CC.APPROVED, str(c_bad.state))

    # ── D 组：跨进程状态 ─────────────────────────────────────────
    print("D 组：候选状态跨进程存活（否则流水线是断的）")
    store = tmp / "candidates.json"
    CC.save(store, {"ghost": c_ok})
    back = CC.load(store)
    ck("D1 存盘再读回：状态与批准人都在",
       back.get("ghost") is not None
       and back["ghost"].state == CC.APPROVED
       and back["ghost"].approved_by == "linshaojie",
       str(back.get("ghost") and back["ghost"].state))
    #: ⛔ 关键：新探针**不许**把已批准的打回 pending
    fresh = CC.from_probe(D.probe(ghost), install_argv=["brew", "install", "ghost"])
    merged = CC.merge(fresh, back.get("ghost"))
    ck("D2 ⛔ 重新探针不得把已批准的打回 pending",
       merged.state == CC.APPROVED and merged.approved_by == "linshaojie",
       merged.state)
    ck("D3 坏掉的档案文件 ⇒ 空表，不抛错（读路径要稳）",
       CC.load(tmp / "nope.json") == {})
    (tmp / "broken.json").write_text("{not json", encoding="utf-8")
    ck("D4 半截 JSON ⇒ 空表而不是崩", CC.load(tmp / "broken.json") == {})

    # ── E 组：留痕 ───────────────────────────────────────────────
    print("E 组：状态进账本（两种结果都记）")
    CC.record(led, c_ok)
    CC.record(led, c_bad)
    evs = [e for e in led.events() if e.kind == "cap.candidate"]
    ck("E1 批准与拒绝**都**进账本（宪法第 10 条）",
       len(evs) >= 2 and {e.payload.get("state") for e in evs} >= {"approved",
                                                                  "rejected"},
       str([e.payload.get("state") for e in evs]))
    ck("E2 事件里带 approved_by（谁批的可查）",
       any(e.payload.get("approved_by") == "linshaojie" for e in evs))
    ck("E3 没挂账本 ⇒ 返回 None 而不是假装记过",
       CC.record(None, c_ok) is None)

    # ── F 组：CLI 流水线（三个独立进程）──────────────────────────
    print("F 组：CLI 三次独立调用能串成一条流水线")
    r1 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "caps",
                         "candidates", "propose", "--json"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    try:
        cands = json.loads(r1.stdout.decode("utf-8", "replace"))
    except Exception:                                        # noqa: BLE001
        cands = None
    ck("F1 `candidates propose --json` 输出结构化候选",
       isinstance(cands, list), r1.stderr.decode()[-160:])
    #: ⛔ 未加 --yes 时退出码必须非 0（否则脚本会把"没批准"当成功）
    r2 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "caps",
                         "candidates", "approve", "--tool", "radare2",
                         "--by", "tester"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    ck("F2 ⛔ 无 --yes 的 approve 退出码非 0（脚本能判出没批准）",
       r2.returncode != 0, f"rc={r2.returncode}")
    r3 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "caps",
                         "candidates", "approve", "--tool", "radare2",
                         "--by", "tester", "--yes"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    ck("F3 带 --yes 的 approve 成功且**说清仍未安装**",
       r3.returncode == 0 and "仍未安装" in r3.stdout.decode("utf-8", "replace"),
       r3.stdout.decode()[-200:])
    r4 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "caps",
                         "candidates", "hint", "--tool", "radare2"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    out4 = r4.stdout.decode("utf-8", "replace").strip()
    ck("F4 ⛔ 状态跨进程存活：hint 能读出批准后的命令（实测踩过不落盘的坑）",
       r4.returncode == 0 and out4.startswith("brew install"), out4[:80])
    #: 清理：别把测试造出来的候选留在真仓库里
    (ROOT / "data" / "candidates.json").unlink(missing_ok=True)

    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过：候选是提案、闸会拦、留痕完整、且必须人批准")
    return 0


if __name__ == "__main__":
    sys.exit(main())
