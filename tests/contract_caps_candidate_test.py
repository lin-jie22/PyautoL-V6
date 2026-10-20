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
import os
import shutil
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
    #: ⛔ 全程改用**临时数据根**（r60 B）：原写法直接在真仓库上跑，
    #:    每跑一次契约就往生产账本里塞好几条 `cap.candidate`
    #:    （实测：账本 170 条里署名 tester 的那 53 条就是这么来的），
    #:    收尾还会把生产 `data/candidates.json` 删掉。
    #:    ⇒ 测试不该是生产账本的并发写入者。
    r1 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "propose", "--json"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    try:
        cands = json.loads(r1.stdout.decode("utf-8", "replace"))
    except Exception:                                        # noqa: BLE001
        cands = None
    ck("F1 `candidates propose --json` 输出结构化候选",
       isinstance(cands, list), r1.stderr.decode()[-160:])
    #: ⛔ 未加 --yes 时退出码必须非 0（否则脚本会把"没批准"当成功）
    r2 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "approve",
                         "--tool", "radare2", "--by", "tester"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    ck("F2 ⛔ 无 --yes 的 approve 退出码非 0（脚本能判出没批准）",
       r2.returncode != 0, f"rc={r2.returncode}")
    r3 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "approve",
                         "--tool", "radare2", "--by", "tester", "--yes"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    ck("F3 带 --yes 的 approve 成功且**说清仍未安装**",
       r3.returncode == 0 and "仍未安装" in r3.stdout.decode("utf-8", "replace"),
       r3.stdout.decode()[-200:])
    r4 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "hint",
                         "--tool", "radare2"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    out4 = r4.stdout.decode("utf-8", "replace").strip()
    ck("F4 ⛔ 状态跨进程存活：hint 能读出批准后的命令（实测踩过不落盘的坑）",
       r4.returncode == 0 and out4.startswith("brew install"), out4[:80])

    # ── G 组：装好之后要能结案（r60 B）──────────────────────────
    print("G 组：installed 必须可达（装好 ⇒ 结案，不是消失）")
    #: 造一个「工具装好了、且已经包装好」的探针
    fin = D.Probe(tool="ghost", status="found", path="/bin/ghost",
                  version="1.0",
                  capability={"id": "tool.ghost", "kind": "tool",
                              "impl": {"transport": "cli",
                                       "argv": ["/bin/ghost"]}})
    #: ⛔ 前提：装好且已包装 ⇒ `from_probe` 返回 None（A3 已经钉过这是**对的**）
    ck("G1 前提：装好且已包装 ⇒ 候选不产生（这正是它会消失的原因）",
       CC.from_probe(fin) is None)
    c_appr = CC.approve(CC.validate(CC.from_probe(
        D.probe(ghost), install_argv=["brew", "install", "ghost"])),
        by="linshaojie", yes=True)
    sealed = CC.seal({c_appr.tool: c_appr}, {c_appr.tool: fin})
    ck("G2 ⛔ 装好 ⇒ seal 结案为 installed（没它账本上永远 0 次）",
       len(sealed) == 1 and sealed[0].state == CC.INSTALLED,
       str([s.state for s in sealed]))
    ck("G3 结案后 approved_by 仍留着（谁批的不许丢）",
       bool(sealed) and sealed[0].approved_by == "linshaojie")
    ck("G4 结案证据是探针的客观读数（不是「我觉得装好了」）",
       bool(sealed) and "verify_probe" in sealed[0].evidence)
    #: ⛔ 反证：没批准的不许被认领 —— 装了也不等于本系统采纳
    c_pend = CC.from_probe(D.probe(ghost),
                           install_argv=["brew", "install", "ghost"])
    ck("G5 ⛔ 未批准的候选**不许**被结案（装上也不算本系统采纳）",
       CC.seal({c_pend.tool: c_pend}, {c_pend.tool: fin}) == [])
    #: ⛔ 反证：没探到就不许结案 —— 不许把"没装"判成"装了"
    c_appr2 = CC.approve(CC.validate(CC.from_probe(
        D.probe(ghost), install_argv=["brew", "install", "ghost"])),
        by="linshaojie", yes=True)
    miss = D.Probe(tool="ghost", status="missing", path="", detail="找不到")
    ck("G6 ⛔ 没探到 ⇒ 不许结案（approved 保持原样）",
       CC.seal({c_appr2.tool: c_appr2}, {c_appr2.tool: miss}) == []
       and c_appr2.state == CC.APPROVED)
    #: merge 那条：已批准 + 探到 ⇒ installed（按探针读数判）
    c_appr3 = CC.approve(CC.validate(CC.from_probe(
        D.probe(ghost), install_argv=["brew", "install", "ghost"])),
        by="linshaojie", yes=True)
    c_new = CC.from_probe(D.Probe(tool="ghost", status="found",
                                  path="/bin/ghost", version="1.0"),
                          install_argv=["brew", "install", "ghost"])
    ck("G7 merge：已批准 + 探针探到 ⇒ installed（按读数判）",
       CC.merge(c_new, c_appr3,
                probe=D.Probe(tool="ghost", status="found",
                              path="/bin/ghost", version="1.0")).state
       == CC.INSTALLED)
    #: ⛔ 反证：不传探针 ⇒ 不许判 installed
    ck("G8 ⛔ 反证：不传探针 ⇒ 保持 approved"
       "（旧判据 `kind==\"\"` 永远不成立，是条死规则）",
       CC.merge(c_new, c_appr3).state == CC.APPROVED)

    #: ── CLI 层：人装完之后回来结账 ──
    #: 造一个假 r2，让 radare2 在探针里"装好了"
    fbin = tmp / "fakebin"
    fbin.mkdir(exist_ok=True)
    fk = fbin / "r2"
    fk.write_text("#!/bin/sh\necho 'radare2 5.9.8 fake'\n", encoding="utf-8")
    fk.chmod(0o755)
    env_fake = dict(os.environ)
    env_fake["PATH"] = str(fbin) + os.pathsep + env_fake.get("PATH", "")
    #: ⛔ 先测**没装**就 verify：必须拒绝，且**不许**撤销人的批准
    r5 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "verify",
                         "--tool", "radare2"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300)
    o5 = r5.stdout.decode("utf-8", "replace")
    ck("G9 ⛔ 没装就 verify ⇒ 退出码非 0（脚本能判出没装）",
       r5.returncode != 0, f"rc={r5.returncode}")
    ck("G10 ⛔ 复验失败**不许**撤销批准（「没装」≠「装了没用」）",
       "你的批准没有被撤销" in o5, o5[-160:])
    #: 后测**装好**了：必须结案
    r6 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "verify",
                         "--tool", "radare2"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300, env=env_fake)
    o6 = r6.stdout.decode("utf-8", "replace")
    ck("G11 装完 verify ⇒ 结案为 installed（这是「人批准」的后半段）",
       r6.returncode == 0 and "已装（复验过）" in o6, o6[-200:])
    #: ⛔ 结案必须落到档案里（跨进程能读回来），而且账本里要有 installed
    r7 = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                         str(tmp), "caps", "candidates", "propose"],
                        cwd=str(ROOT), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=300, env=env_fake)
    o7 = r7.stdout.decode("utf-8", "replace")
    ck("G12 ⛔ 结案跨进程存活：重跑 propose 仍看得到 installed"
       "（改前这条会直接消失）",
       "已装（复验过）" in o7, o7[-200:])
    #: ⛔ 光看打印不够 —— 打印读的是**内存里**的 sealed，档案才是跨进程的
    #:    真相。少这一条，"propose 改成整表替换"就测不出来（实测：事故版 D
    #:    只让 G12 看的话是绿的，因为输出不受落盘影响）。
    try:
        _sd = json.loads((tmp / "data" / "candidates.json").read_text(
            encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        _sd = {"candidates": []}
    _st = {c["tool"]: c["state"] for c in (_sd.get("candidates") or [])}
    ck("G12b ⛔ 结案真的落进档案（跨进程读回，不是只打印一下）"
       "（整表替换会把刚结案的又冲掉）",
       _st.get("radare2") == "installed", str(_st))
    #: ⛔ 账本路径按 `paths()` 取，不许手拼（`--root` 下它在 data/ 子目录，
    #:    手拼 `tmp/ledger.jsonl` 会读到空账本 ⇒ 断言假红，第 24 条）。
    n_inst = 0
    _lf = tmp / "data" / "ledger.jsonl"
    if _lf.is_file():
        n_inst = sum(1 for e in Ledger(_lf).events()
                     if e.kind == "cap.candidate"
                     and e.payload.get("state") == "installed")
    ck("G13 账本里真的出现了 installed（事实源上不再是 0 次）",
       n_inst >= 1, f"installed={n_inst}")

    # ── H 组：已裁决的状态不许被机器回退（r60 D）──────────────────
    print("H 组：机器不许回退「人的裁决」与「终态」")
    #: ⛔⛔ 本组针对的形态（2026-10-19/20 生产账本实测，不是我推的）：
    #:    一次人批准被两次机器调用抹掉的**全过程** ——
    #:      seq=6955  23:56:18  approved   by='linshaojie'   ← 人批的
    #:      seq=7021  00:52:13  validated                    ← 重跑 validate
    #:      seq=7022  00:52:14  pending                      ← 重跑 propose
    #:    ⇒ 它比"卡在 approved 不动"（r60 B）更隐蔽：那是**停住**，看得见；
    #:      这是**往后退**，而"退回 pending"在界面上与"从没提过"同形。
    def mk_c():
        return CC.from_probe(D.probe(ghost),
                             install_argv=["brew", "install", "ghost"])

    c_appr4 = CC.approve(CC.validate(mk_c()), by="linshaojie", yes=True)
    ck("H1 前提：过闸 + 批准 ⇒ approved（否则下面全是空真）",
       c_appr4.state == CC.APPROVED, c_appr4.state)
    kept = CC.validate(c_appr4)
    ck("H2 ⛔⛔ 重跑 `validate` 不许把 approved 降级（生产事故的正题）",
       kept.state == CC.APPROVED and kept.approved_by == "linshaojie",
       f"{kept.state}")
    #: ⛔ 别看内部记账 —— 看人能不能装（消费侧）
    ck("H3 ⛔ 消费侧证据：批准没丢 ⇒ `install_hint` 仍读得出命令"
       "（state 只是账本，取不出命令才是人感受到的失效）",
       CC.install_hint(kept) == ["brew", "install", "ghost"],
       str(CC.install_hint(kept)))

    #: ⛔⛔ 反证（本组最要紧的一条）：**不许**「保住 state」顺带把读数也保住
    #:    否则「人批了，闸后来变红」就永远看不见（第 49 条）
    c_inst = CC.verify(CC.approve(CC.validate(mk_c()), by="linshaojie",
                                 yes=True), fin)
    red = CC.validate(c_inst, probe=fin)   # 候选说 missing，现在探到 found
    ck("H4 ⛔ 保住 state，但**闸的读数照常刷新**：installed 的候选仍被判红",
       red.state == CC.INSTALLED and not red.gate_passed,
       f"state={red.state} gate={red.gate_passed}")
    ck("H5 ⛔ 原因也留下来了（不是只把 gate_passed 置 False 了事）",
       any(("过期" in r) or ("不一致" in r) for r in red.gate_reasons),
       str(red.gate_reasons))

    c_again = CC.approve(red, by="linshaojie", yes=True)
    ck("H6 ⛔ 已 installed 的候选再批准 ⇒ 不许退回 approved；且要说清为什么"
       "（否则 CLI 会打印一个 `?`）",
       c_again.state == CC.INSTALLED
       and any("终态" in r for r in c_again.gate_reasons),
       f"{c_again.state} {c_again.gate_reasons[-1:]}")

    #: ⛔⛔ 反证：这条**不许**被写成「state 一概不许改」—— 那会让闸失去牙。
    ck("H7 ⛔ 反证：pending 的候选过闸失败 ⇒ 仍变 rejected（闸有牙）",
       CC.validate(mk_c(), non_regression_ok=False).state == CC.REJECTED)
    ck("H8 ⛔ 反证：pending 的候选过闸通过 ⇒ 仍变 validated",
       CC.validate(mk_c()).state == CC.VALIDATED)
    ck("H9 ⛔ 反证：**未裁决**的（validated）复核失败 ⇒ 仍可回退到 rejected"
       "（「不许回退」只从 approved 起算，不是从 validated 起算）",
       CC.validate(CC.validate(mk_c()), probe=fin).state == CC.REJECTED)
    #: ⛔⛔ 边界：复验**不套**这条保护 —— 它的本职就是报"现在还成立吗"，
    #:    "装好的工具后来被卸了"是必须能被看见的坏消息（r60 B 已定：
    #:    复验失败只留痕、不落盘，「撤销批准」这条路本来就不存在）。
    ck("H10 ⛔ 复验不受这条保护约束：installed 的候选探不到 ⇒ 仍报 rejected",
       CC.verify(c_inst, D.Probe(tool="ghost", status="missing", path="",
                                 detail="被卸了")).state == CC.REJECTED)

    #: ── CLI 端到端：把生产事故那串调用**逐条重放**（不是读代码推断）──
    print("H 组（续）：CLI 重放生产事故的那串调用（独立临时根）")
    #: ⛔ 用新的临时根：G 组已经把 radare2 弄成 installed 了，
    #:    复用会让"H2 到底测的是哪一段"说不清（第 35 条：对照组只差一个变量）。
    th = Path(tempfile.mkdtemp(prefix="v6_cand_h_"))

    def cli(*argv):
        r = subprocess.run([sys.executable, "-B", "-m", "aether.cli", "--root",
                            str(th)] + list(argv),
                           cwd=str(ROOT), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=300)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    def hstate():
        #: ⛔ 读**档案**不读打印：打印读的是内存里的对象，档案才是跨进程真相
        #:    （G12b 同一条纪律：只测打印的话，"整表替换"测不出来）。
        try:
            d = json.loads((th / "data" / "candidates.json").read_text(
                encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            return {}
        return {c["tool"]: c["state"] for c in (d.get("candidates") or [])}

    try:
        cli("caps", "candidates", "propose")
        cli("caps", "candidates", "validate", "--tool", "radare2")
        rc_a, _ = cli("caps", "candidates", "approve", "--tool", "radare2",
                      "--by", "linshaojie", "--yes")
        ck("H11 前提：CLI 批准成功（否则下面全是空真）",
           rc_a == 0 and hstate().get("radare2") == "approved", str(hstate()))
        cli("caps", "candidates", "validate", "--tool", "radare2")
        ck("H12 ⛔⛔ 生产事故第 1 步：重跑 `validate` 后档案仍是 approved",
           hstate().get("radare2") == "approved", str(hstate()))
        cli("caps", "candidates", "propose")
        ck("H13 ⛔⛔ 生产事故第 2 步：重跑 `propose` 后档案**仍是** approved"
           "（改前这里是 pending，批准就这么没了）",
           hstate().get("radare2") == "approved", str(hstate()))
        rc_h, out_h = cli("caps", "candidates", "hint", "--tool", "radare2")
        ck("H14 ⛔ 消费侧证据：`hint` 仍读得出安装命令"
           "（改前 rc=1，提示『先 validate + approve --yes』）",
           rc_h == 0 and out_h.strip().startswith("brew install"),
           f"rc={rc_h} {out_h.strip()[:60]}")
    finally:
        shutil.rmtree(th, ignore_errors=True)

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
