"""嵌入供给：可插拔，默认确定性，**降级必须显式**。

## 为什么单独成模块

记忆层的检索质量完全由嵌入决定，而嵌入恰好是这类系统里最容易"看起来
成功、实际报废"的一环。V5 出过两次同一形态的事故：

  1. learners 写 float32 binary、retriever 用 json.loads 读 —— 169 条记录
     全部解析失败；**入库条数照常增长**，只有检索端知道它们全废了。
  2. 一条维度不符的坏数据让整次检索抛 ValueError —— 一条坏数据能废掉
     整个库，而不是只废掉它自己。

共同点：**写入端"成功"了**。所以本模块把"成功"重新定义成
「向量非空 + 维度与当前 embedder 一致 + 可复现」，三条缺一条即拒写。

## ⛔⛔ 头号纪律：绝不静默降级

真嵌入（模型/网络）不可用时，最容易的写法是 `except: return hash_embedding(t)`。
**这是本模块明令禁止的**：hashing 是 256 维、nomic 是 768 维，混进同一个库后
余弦相似度在维度不符时静默返回 0.0 —— 于是库越写越大、检索越来越差，
而**没有任何一条日志会报错**（正是 V5 那 169 条的形态）。

正确做法只有两条：
  · **fail-closed**：真嵌入不可用 ⇒ 当次写入被拒并记账，库保持单 dim 纯净；
  · **显式 reindex**：换 embedder 时调用 `MemoryOS.reindex()` **全量重算**，
    新旧向量绝不共存。

## 可复现性

`embedder_id` = 名字 + dim（如 `nomic-embed-text:768`）。它写进每条记忆，
于是"这个库是用什么嵌入建的"是**可查的事实**，不是口头约定。换模型后
旧笔记的 `embedder_id` 不匹配 ⇒ `integrity()` 直接报出来。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

_WORD = re.compile(r"[\u4e00-\u9fff]|[A-Za-z_][A-Za-z0-9_]*|\d+")


class Embedder(Protocol):
    """嵌入器协议。实现者**必须**保证 `dim` 与 `embed()` 返回长度一致。"""

    name: str
    dim: int

    def embed(self, texts: List[str]) -> List[List[float]]: ...


# ──────────────────────────────────────────────────────────────────
# 默认嵌入：hashing trick（确定性、离线、零依赖）
# ──────────────────────────────────────────────────────────────────
def hash_embedding(text: str, dim: int = 256) -> List[float]:
    """词袋 hashing trick。同文本 ⇒ 同向量（跨进程、跨版本确定性）。

    它是**词面**嵌入：能召回字面重叠的内容，召回不了同义改写。
    这是它的能力上限，也是它作为默认值的理由 —— 离线可回归比召回率重要。
    """
    vec = [0.0] * dim
    toks = _WORD.findall(text or "")
    if not toks:
        return vec
    for t in toks:
        h = int(hashlib.blake2b(t.encode("utf-8"), digest_size=8).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class HashingEmbedder:
    """确定性嵌入器。**永远可用** —— 它是把底，不是降级产物。"""

    def __init__(self, dim: int = 256):
        self.dim = dim

    @property
    def name(self) -> str:
        return f"hashing:{self.dim}"

    @property
    def embedder_id(self) -> str:
        return self.name

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [hash_embedding(t, self.dim) for t in texts]

    def probe(self) -> Tuple[bool, str]:
        return True, "always-available"


# ──────────────────────────────────────────────────────────────────
# 真嵌入：Ollama / OpenAI 兼容
# ──────────────────────────────────────────────────────────────────
def _no_proxy_opener():
    """⛔ 本机有 HTTP_PROXY，访问 localhost 必须绕过代理（否则 502 假故障）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_json(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with _no_proxy_opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


@dataclass
class OllamaEmbedder:
    """Ollama `/api/embed`（真模型嵌入）。

    ⛔ `dim` 是**探测出来的**，不是猜的：探测失败 ⇒ `available=False` ⇒
    调用方必须 fail-closed，绝不能拿一个假的 dim 继续跑。
    """
    model: str = ""
    base_url: str = "http://127.0.0.1:11434"
    timeout: float = 30.0
    _dim: int = field(default=0, repr=False)

    def __post_init__(self):
        # ⛔ 模型名必须带 tag：ollama 对裸名（`nomic-embed-text`）会静默挂住
        #    直到超时，而带 tag 的同一模型正常返回 —— 实测 180s 无响应 vs
        #    125s 正常返回（首次加载慢，之后常驻秒回）。
        self.model = self.model or os.environ.get(
            "V6_OLLAMA_EMB_MODEL", "nomic-embed-text:latest")

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"

    @property
    def embedder_id(self) -> str:
        return f"{self.name}:{self.dim or '?'}"

    @property
    def dim(self) -> int:
        return self._dim

    def probe(self) -> Tuple[bool, str]:
        """探测模型是否真能做嵌入，并**实测** dim。"""
        try:
            data = _post_json(self.base_url.rstrip("/") + "/api/embed",
                              {"model": self.model, "input": ["probe"]},
                              self.timeout)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        vecs = data.get("embeddings")
        if not vecs or not isinstance(vecs[0], list) or not vecs[0]:
            return False, f"响应无 embeddings：{str(data)[:160]}"
        self._dim = len(vecs[0])
        # ⛔ 空向量 = "模型返回了但没内容"（V5：入库成功但读不出来）
        if not any(x != 0.0 for x in vecs[0]):
            return False, "嵌入全零（模型不支持 embedding 或输出异常）"
        return True, f"dim={self._dim}"

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self._dim:
            raise RuntimeError("OllamaEmbedder 未探测（先 probe()）—— "
                               "不允许在未确认 dim 的情况下写入")
        data = _post_json(self.base_url.rstrip("/") + "/api/embed",
                          {"model": self.model, "input": texts}, self.timeout)
        vecs = data.get("embeddings")
        if not vecs or len(vecs) != len(texts):
            raise RuntimeError(f"嵌入数量不符：{len(vecs) if vecs else 0}"
                               f" != {len(texts)}")
        out = []
        for v in vecs:
            if len(v) != self._dim:
                raise RuntimeError(f"嵌入维度漂移：{len(v)} != {self._dim}")
            out.append([float(x) for x in v])
        return out


@dataclass
class HTTPEmbedder:
    """OpenAI 兼容 `/embeddings`。同上：dim 必须探测。"""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout: float = 30.0
    _dim: int = field(default=0, repr=False)

    def __post_init__(self):
        self.base_url = self.base_url or os.environ.get("V6_EMB_BASE_URL", "")
        self.model = self.model or os.environ.get("V6_EMB_MODEL", "")
        self.api_key = self.api_key or os.environ.get("V6_EMB_API_KEY", "")

    @property
    def name(self) -> str:
        return f"http:{self.model or 'unset'}"

    @property
    def embedder_id(self) -> str:
        return f"{self.name}:{self.dim or '?'}"

    @property
    def dim(self) -> int:
        return self._dim

    def probe(self) -> Tuple[bool, str]:
        if not (self.base_url and self.model):
            return False, "未配置 V6_EMB_BASE_URL / V6_EMB_MODEL"
        try:
            data = _post_json(
                self.base_url.rstrip("/") + "/embeddings",
                {"model": self.model, "input": ["probe"]}, self.timeout)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        vecs = data.get("data")
        if not vecs or not isinstance(vecs[0].get("embedding"), list):
            return False, f"响应结构异常：{str(data)[:160]}"
        v = vecs[0]["embedding"]
        self._dim = len(v)
        if not any(x != 0.0 for x in v):
            return False, "嵌入全零"
        return True, f"dim={self._dim}"

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self._dim:
            raise RuntimeError("HTTPEmbedder 未探测（先 probe()）")
        data = _post_json(self.base_url.rstrip("/") + "/embeddings",
                          {"model": self.model, "input": texts}, self.timeout)
        rows = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
        if len(rows) != len(texts):
            raise RuntimeError("嵌入数量不符")
        out = []
        for r in rows:
            v = r["embedding"]
            if len(v) != self._dim:
                raise RuntimeError(f"嵌入维度漂移：{len(v)} != {self._dim}")
            out.append([float(x) for x in v])
        return out


# ──────────────────────────────────────────────────────────────────
# 语义测试替身：用于在不联网的情况下验证"换 embedder 真的改变了检索"
# ──────────────────────────────────────────────────────────────────
class SynonymEmbedder:
    """**确定性语义**嵌入：同义词组映射到同一方向。

    它存在的唯一理由：让"真嵌入比词面嵌入强"这件事**可离线验证**。
    hashing trick 是词面的 → "去重" 与 "删除重复数据" 词面不重叠 ⇒ 召回不到；
    语义嵌入把它们放到同一方向 ⇒ 能召回。这个差别就是换 embedder 的全部意义，
    而它必须能被测试，不能只能被感觉。
    """

    #: 同义组。组内共享一个方向向量。
    GROUPS: Tuple[Tuple[str, ...], ...] = (
        ("去重", "删除重复", "消除冗余", "dedupe", "deduplicate"),
        ("排序", "排序稳定性", "次序", "sort", "ordering"),
        ("截断", "超长处理", "截断过长的输入", "truncate"),
        ("限流", "速率限制", "rate limit", "429"),
    )

    def __init__(self, dim: int = 64):
        self.dim = dim
        self._dirs: Dict[str, int] = {}
        for gi, g in enumerate(self.GROUPS):
            for w in g:
                self._dirs[w.lower()] = gi

    @property
    def name(self) -> str:
        return f"synonym:{self.dim}"

    @property
    def embedder_id(self) -> str:
        return self.name

    def _hits(self, text: str) -> List[int]:
        t = (text or "").lower()
        return sorted({gi for w, gi in self._dirs.items() if w in t})

    def embed(self, texts: List[str]) -> List[List[float]]:
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            hits = self._hits(t)
            if hits:
                for gi in hits:
                    vec[gi] += 1.0
            else:
                # 无同义词命中 ⇒ 退化成词面 hashing（保证不会全零）
                for tok in _WORD.findall(t):
                    h = int(hashlib.blake2b(tok.encode("utf-8"),
                                            digest_size=8).hexdigest(), 16)
                    vec[len(self.GROUPS) + h % (self.dim - len(self.GROUPS))] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out

    def probe(self) -> Tuple[bool, str]:
        return True, "deterministic"


# ──────────────────────────────────────────────────────────────────
# 解析：按优先级探测，返回 (embedder, 探测报告)
# ──────────────────────────────────────────────────────────────────
def resolve_embedder(prefer: str = "auto", *,
                     ollama_model: str = "",
                     hashing_dim: int = 256,
                     timeout: float = 30.0) -> Tuple[Any, Dict[str, Any]]:
    """选出嵌入器，并把**为什么选它**完整地记下来。

    ⛔ 返回值里永远有一个可用的 embedder（hashing 兜底），
    但报告里 `chosen != preferred` 时，调用方必须知道这是**降级**，
    而不是"本来就这样"。降级可观测 ≠ 降级可接受。
    """
    cands: List[Tuple[str, Any]] = []
    if prefer in ("auto", "ollama"):
        cands.append(("ollama", OllamaEmbedder(model=ollama_model,
                                               timeout=timeout)))
    if prefer in ("auto", "http"):
        cands.append(("http", HTTPEmbedder(timeout=timeout)))
    if prefer == "synonym":
        cands.append(("synonym", SynonymEmbedder()))

    report: Dict[str, Any] = {"preferred": prefer, "probes": [],
                              "chosen": "", "embedder_id": "", "dim": 0,
                              "degraded": False, "reason": ""}
    for kind, emb in cands:
        try:
            ok, why = emb.probe()
        except Exception as e:  # noqa: BLE001
            ok, why = False, f"{type(e).__name__}: {e}"
        report["probes"].append({"kind": kind, "name": emb.name,
                                 "available": bool(ok), "reason": str(why)})
        if ok:
            report.update(chosen=kind, embedder_id=emb.embedder_id,
                          dim=emb.dim, degraded=False, reason=str(why))
            return emb, report

    fallback = HashingEmbedder(dim=hashing_dim)
    report.update(chosen="hashing", embedder_id=fallback.embedder_id,
                  dim=fallback.dim, degraded=True,
                  reason=f"真嵌入不可用 ⇒ 用确定性 hashing"
                         f"（禁止与真嵌入混库，切回时必须 reindex）")
    return fallback, report


def cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度。⛔ 维度不符 ⇒ 0.0 且**不抛异常**是危险的 —— 它会让
    "库被污染"表现成"没有相关知识"。所以这里显式返回 None 语义由调用方处理：
    见 `cosine_or_none`。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def cosine_or_none(a: List[float], b: List[float]) -> Optional[float]:
    """维度不符 ⇒ 返回 None（**可判别**），而不是 0.0（不可判别）。"""
    if not a or not b or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b))
