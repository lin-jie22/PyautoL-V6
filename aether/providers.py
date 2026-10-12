"""LLM / Embedding 供给：可插拔，**默认离线确定性**。

为什么要一个 stub provider（而不是直接接真模型）：
  1. **自进化系统必须能被离线回归**。依赖网络/模型 ⇒ 每次跑出来的进化
     轨迹都不同 ⇒ "零新红""非回归"这些判据全部失效（V5 的教训：
     链首 zhipu 82% 429 直接让整条供给链崩掉两轮）。
  2. 判据必须与被测对象同源：stub 让"进化循环本身的正确性"和
     "模型好不好"解耦 —— 前者能被确定性测试，后者只能被评测。
  3. 真模型通过同一个 Protocol 注入，**接口不变**。

⛔ 供给失败必须**显式**：stub 不会因为"没配 key"就返回空字符串让下游
   以为成功。真 provider 的任何异常都向上抛并由调用方记账（fail-closed）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from .genome import Capsule, Gene, Harness
from .runtime import Trajectory


class LLMProvider(Protocol):
    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str: ...


class Proposer(Protocol):
    """变异提议器：看弱点 → 产出候选 capsule。"""
    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3) -> List[Capsule]: ...


class StubProvider:
    """确定性 provider：同一输入永远同一输出。

    ⛔ 它**不假装自己是模型**：`complete()` 返回的是结构化的"我不做生成"
    标记，下游 proposer 看到它就必须走模板化路径，而不是把空字符串
    当成"模型说不用改"。
    """
    STUB_MARK = "[[STUB:no-generation]]"

    def __init__(self, echoes: Optional[Dict[str, str]] = None):
        self.echoes = echoes or {}
        self.calls = 0

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        self.calls += 1
        for k, v in self.echoes.items():
            if k in prompt:
                return v
        return f"{self.STUB_MARK} prompt_len={len(prompt)}"


class HTTPProvider:
    """OpenAI 兼容 Chat Completions。**显式失败**，不吞异常。"""

    def __init__(self, base_url: str = "", model: str = "", api_key: str = "",
                 timeout: float = 60.0):
        self.base_url = base_url or os.environ.get("V6_LLM_BASE_URL", "")
        self.model = model or os.environ.get("V6_LLM_MODEL", "")
        self.api_key = api_key or os.environ.get("V6_LLM_API_KEY", "")
        self.timeout = timeout
        if not (self.base_url and self.model):
            raise ValueError("HTTPProvider 需要 base_url 与 model（或环境变量）")

    def complete(self, prompt: str, *, system: str = "",
                 max_tokens: int = 1024, temperature: float = 0.0) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:200]!r}") from e
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM 调用失败：{type(e).__name__}: {e}") from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM 响应结构异常：{data}") from e


@dataclass
class TemplateProposer:
    """模板化变异提议器（stub 的默认搭档）。

    它**不是**"假装是 LLM"，而是把"弱点 → 编辑"的映射显式写成规则：
      · 缺技能 ⇒ 加技能 + 加路由
      · 工具不够 ⇒ 开放工具
      · 记忆召回差 ⇒ 调 memory_policy
    这样进化循环的**机制正确性**可以脱离模型被验证（V5 的纪律：
    先证明机制跑得通，再谈模型好不好）。
    """
    name: str = "template"
    skills: Dict[str, str] = field(default_factory=dict)
    #: 路由键。**默认按任务类别**，不按环境名（topic）。
    #: ⛔ 按环境名路由（`textscore → 修法`）本身就是一种定点化：换一个环境
    #:    这条路由立刻失效，于是"技能加了"却"在别处用不上"。
    #:    按任务类别路由（`repair → 修法`）才是通用写法。
    route_key: str = "repair"

    def propose(self, weakness: Dict[str, Any], harness: Harness,
                n: int = 3) -> List[Capsule]:
        kind = str(weakness.get("kind", ""))
        topic = str(weakness.get("topic", "general"))
        out: List[Capsule] = []

        if kind in ("missing_skill", "unknown"):
            for i, (sname, body) in enumerate(list(self.skills.items())[:n]):
                # ⛔ 胶囊（不是单基因）：技能 + 路由**必须一起改**。
                #    只加技能 ⇒ 路由没接通 ⇒ 技能不可达 ⇒ reward 不变
                #    ⇒ 过不了能力地板。这正是 Capsule 存在的理由，
                #    由 tests/contract_agent_test.py 的 L1 组实测守住。
                out.append(Capsule(
                    name=f"add_skill:{sname}",
                    genes=(Gene(op="set", target=f"L3.skills.{sname}",
                                value=body, rationale=f"弱点={kind}"),
                           Gene(op="set", target=f"L1.routing.{self.route_key}",
                                value=sname,
                                rationale=f"新技能需可达（按任务类别"
                                          f"{self.route_key} 路由，不按环境名）")),
                    rationale=f"补技能 {sname} 并接通路由", origin="mined"))

        if kind == "missing_tool":
            tool = str(weakness.get("tool", "read_file"))
            out.append(Capsule(name=f"tool:{tool}",
                               genes=(Gene(op="append", target="L2.tools",
                                           value=tool, rationale="工具不可达"),),
                               rationale=f"开放工具 {tool}", origin="mined"))

        if kind == "recall_miss":
            out.append(Capsule(
                name="tune:recall",
                genes=(Gene(op="tune", target="L4.memory_policy.recall_topk",
                            value=2, rationale="召回不足 ⇒ 扩大 topk"),
                       Gene(op="tune", target="L4.memory_policy.min_content_len",
                            value=-20, rationale="下限过高挡住了短知识")),
                rationale="调记忆召回参数", origin="mined"))

        if not out:
            out.append(Capsule(name="noop", genes=(), rationale="无可用变异",
                               origin="mined"))
        return out[:max(1, n)]
