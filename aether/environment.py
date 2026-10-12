"""环境优先（Environment-First）的任务环境与合成。

2026 的共识（Meta ARE 平台、SWE-smith、R2E-Gym 一致）：
    **agentic 训练/进化的瓶颈不是 GPU，是"环境 + 判据"的供给。**
    SWE-smith 的关键倒置：先建一个**共享**执行环境（O(repos) 而非
    O(instances) 的镜像），再在环境里合成成百上千个任务实例，
    用"是否让既有测试变红"来筛选（execution-based validation）。

V6 照搬这个结构，但缩到可以在本机秒级跑：
    Environment（共享）  ──►  synthesize_task()  ──►  execution 验证（F2P）

四条纪律：
  1. **执行验证优先**：任务是否成立，由"变异后既有测试确实变红"决定，
     不是由"看起来像 bug"决定（SWE-smith 的 Fail-to-Pass 筛选）。
  2. **环境指纹**：每个环境有 fingerprint，判据跨环境不可比
     （V5 教训：口径不同却同表对比）。
  3. **沙箱隔离**：代码执行走 subprocess，超时/崩溃都被记账，
     ⛔ 绝不因"执行器自己挂了"给 reward。
  4. **yield 记账**：合成成功率要统计（合成不出来 = 环境没用），
     不能只看"生成了多少任务"。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ledger import content_hash
from .verifier import Verdict, all_of, error_verdict

PY = sys.executable or "python3"

# 沙箱内执行器：读源码 + 测试 spec，输出 JSON。
# ⛔ 它**不判断**"好不好"，只报"每个断言的实际结果"——判断在 verifier 里。
_RUNNER = r'''
import json, sys, io, traceback, contextlib
src = sys.stdin.read()
payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
tests = payload["tests"]
ns = {"__name__": "task"}
err = None
try:
    exec(compile(src, "task.py", "exec"), ns)
except Exception:
    err = traceback.format_exc()
out = []
for t in tests:
    name = t.get("name", "?")
    if err:
        out.append({"name": name, "passed": False, "evidence": "import-failed: " + err[:200]})
        continue
    try:
        fn = ns.get(t["call"])
        args = t.get("args", [])
        kwargs = t.get("kwargs", {})
        got = fn(*args, **kwargs) if callable(fn) else eval(t["call"], ns)
        if "expect" in t:
            ok = (got == t["expect"])
            ev = f"got={got!r} expect={t['expect']!r}"
        else:
            ok = bool(got)
            ev = f"got={got!r} (truthy check)"
        out.append({"name": name, "passed": bool(ok), "evidence": ev})
    except Exception:
        out.append({"name": name, "passed": False,
                    "evidence": traceback.format_exc().splitlines()[-1][:200]})
print(json.dumps({"results": out, "import_error": err}))
'''


@dataclass(frozen=True)
class Environment:
    """一个共享执行环境 = 源码 + 测试集 + 元信息。"""
    env_id: str
    source: str
    tests: Tuple[Dict[str, Any], ...]
    note: str = ""

    @property
    def fingerprint(self) -> str:
        return content_hash({"env_id": self.env_id, "source": self.source,
                             "tests": list(self.tests)})[:16]

    @staticmethod
    def from_dir(root: str | Path, env_id: str = "") -> "Environment":
        """从目录构建：读 `task.py` + `tests.json`。"""
        root = Path(root)
        src = (root / "task.py").read_text(encoding="utf-8")
        tests = json.loads((root / "tests.json").read_text(encoding="utf-8"))
        if isinstance(tests, dict):
            tests = tests.get("tests", [])
        return Environment(env_id=env_id or root.name, source=src,
                           tests=tuple(tests), note=str(root))

    def to_dict(self, include_source: bool = True) -> Dict[str, Any]:
        return {
            "env_id": self.env_id,
            "fingerprint": self.fingerprint,
            "tests": [dict(t) for t in self.tests],
            "source": self.source if include_source else "",
            "note": self.note,
        }


@dataclass
class ExecResult:
    """一次沙箱执行的原始结果（还不是 Verdict）。"""
    results: List[Dict[str, Any]] = field(default_factory=list)
    import_error: str = ""
    rc: int = -1
    cost_s: float = 0.0
    stdout: str = ""
    stderr: str = ""

    @property
    def n_passed(self) -> int:
        return sum(1 for r in self.results if r.get("passed"))


def run_in_sandbox(source: str, tests: List[Dict[str, Any]],
                   timeout: float = 10.0) -> ExecResult:
    """跑一次测试。**不做判断**，只报事实。

    ⛔ 崩溃/超时都被如实记账（rc / stderr），绝不返回"通过"。
    """
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="v6env_") as td:
        spec = Path(td) / "tests.json"
        runner = Path(td) / "runner.py"
        spec.write_text(json.dumps({"tests": tests}), encoding="utf-8")
        runner.write_text(_RUNNER, encoding="utf-8")
        try:
            p = subprocess.run([PY, str(runner), str(spec)], input=source,
                               capture_output=True, text=True, timeout=timeout,
                               cwd=td)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return ExecResult(rc=124, cost_s=time.time() - t0,
                              stderr=f"timeout {timeout}s")
        except Exception as e:  # noqa: BLE001
            return ExecResult(rc=1, cost_s=time.time() - t0,
                              stderr=f"{type(e).__name__}: {e}")

    res = ExecResult(rc=rc, cost_s=time.time() - t0, stdout=out, stderr=err)
    try:
        data = json.loads((out or "{}").strip().splitlines()[-1])
        res.results = data.get("results", [])
        res.import_error = data.get("import_error") or ""
    except Exception:  # noqa: BLE001
        res.stderr = (res.stderr or "") + " | unparsable-runner-output"
    return res


def verify_source(env: Environment, source: Optional[str] = None,
                  timeout: float = 10.0) -> Verdict:
    """对某个源码版本做**可执行判定** —— 这就是 reward 的来源。

    ⛔ evidence 是真实断言结果（got/expect），不是"跑过了"。
    """
    src = source if source is not None else env.source
    try:
        ex = run_in_sandbox(src, list(env.tests), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return error_verdict(f"env/{env.env_id}", e)

    if ex.rc != 0 or ex.import_error:
        return Verdict(passed=False, checks=(), error=ex.import_error or ex.stderr[:200],
                       evidence={"rc": ex.rc, "stderr": ex.stderr[:200]},
                       env_fingerprint=env.fingerprint, cost_s=ex.cost_s)

    checks = [(r["name"], bool(r["passed"]), str(r.get("evidence", "")))
              for r in ex.results]
    v = all_of(env.env_id, checks)
    return Verdict(passed=v.passed, checks=v.checks,
                   evidence={"n_passed": ex.n_passed, "n_total": len(ex.results),
                             "env_id": env.env_id},
                   env_fingerprint=env.fingerprint, cost_s=ex.cost_s)


# ──────────────────────────────────────────────────────────────────
# 任务合成（SWE-smith 式：变异源码 → 执行验证 → 只留真让测试变红的）
# ──────────────────────────────────────────────────────────────────
_FLIP_OPS = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.GtE: ast.Lt,
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
}


class _Mutator(ast.NodeTransformer):
    """AST 级变异（SWE-smith 的 Procedural Mod 的迷你版）。"""

    def __init__(self, which: int = 0):
        self.which = which
        self.seen = 0
        self.done = False
        self.desc = ""

    def visit_Compare(self, node: ast.Compare):
        self.generic_visit(node)
        if self.done:
            return node
        self.seen += 1
        if self.seen - 1 == self.which and node.ops:
            old = type(node.ops[0])
            new = _FLIP_OPS.get(old)
            if new is not None:
                node.ops[0] = new()
                self.done = True
                self.desc = f"比较符反转 {old.__name__}→{new.__name__}"
        return node

    def visit_BinOp(self, node: ast.BinOp):
        self.generic_visit(node)
        if self.done:
            return node
        self.seen += 1
        if self.seen - 1 == self.which:
            old = type(node.op)
            new = _FLIP_OPS.get(old)
            if new is not None and not isinstance(node.left, ast.Str):
                node.op = new()
                self.done = True
                self.desc = f"运算符替换 {old.__name__}→{new.__name__}"
        return node

    def visit_Constant(self, node: ast.Constant):
        if self.done or not isinstance(node.value, (int, float)):
            return node
        self.seen += 1
        if self.seen - 1 == self.which:
            node.value = node.value + 1
            self.done = True
            self.desc = f"常量 {node.value - 1}→{node.value}"
        return node


def mutate_source(source: str, which: int = 0) -> Tuple[str, str]:
    """对源码做一次 AST 变异，返回 (新源码, 描述)。

    ⛔ 语法错误 ⇒ 抛异常（不静默返回原文）—— 静默返回原文会造出
    "看起来合成成功但什么都没变"的假任务。
    """
    tree = ast.parse(source)
    m = _Mutator(which)
    new_tree = m.visit(tree)
    ast.fix_missing_locations(new_tree)
    if not m.done:
        raise ValueError(f"没有可变异点（which={which}）")
    return ast.unparse(new_tree), m.desc


@dataclass
class TaskInstance:
    """合成出来的任务实例：一个"坏掉"的环境 + 判据。"""
    task_id: str
    env_id: str
    broken_source: str
    desc: str
    fingerprint: str
    fail_to_pass: Tuple[str, ...] = ()     # 变异后变红的测试名

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "env_id": self.env_id, "desc": self.desc,
                "fingerprint": self.fingerprint, "fail_to_pass": list(self.fail_to_pass)}


@dataclass
class SynthesisReport:
    attempted: int = 0
    valid: int = 0
    rejected_no_failure: int = 0
    rejected_error: int = 0
    tasks: List[TaskInstance] = field(default_factory=list)

    @property
    def yield_rate(self) -> float:
        return (self.valid / self.attempted) if self.attempted else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"attempted": self.attempted, "valid": self.valid,
                "rejected_no_failure": self.rejected_no_failure,
                "rejected_error": self.rejected_error,
                "yield_rate": round(self.yield_rate, 4)}


def synthesize_tasks(env: Environment, max_n: int = 8,
                     timeout: float = 10.0) -> SynthesisReport:
    """从一个环境合成任务实例：**只保留真让测试变红的**（Fail-to-Pass）。

    ⛔ yield 必须统计：合成不出任务 = 这个环境没用，得记账，
    不能拿"尝试次数"冒充"任务数"。
    """
    rep = SynthesisReport()
    before = verify_source(env, timeout=timeout)
    before_names = {c.name for c in before.checks if c.passed}

    for i in range(max_n):
        rep.attempted += 1
        try:
            src, desc = mutate_source(env.source, which=i)
        except Exception:  # noqa: BLE001
            rep.rejected_error += 1
            continue
        try:
            after = verify_source(env, source=src, timeout=timeout)
        except Exception:  # noqa: BLE001
            rep.rejected_error += 1
            continue
        after_names = {c.name for c in after.checks if c.passed}
        broken = tuple(sorted(before_names - after_names))
        if not broken:
            rep.rejected_no_failure += 1
            continue
        rep.valid += 1
        rep.tasks.append(TaskInstance(
            task_id=f"{env.env_id}::m{i}",
            env_id=env.env_id,
            broken_source=src,
            desc=desc,
            fingerprint=content_hash(src)[:16],
            fail_to_pass=broken,
        ))
    return rep
