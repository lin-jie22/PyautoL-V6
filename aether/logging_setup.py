#!/usr/bin/env python3
"""统一日志（r35）。

为什么需要它（2026-10-14 实测取证）：
    收口前 `aether/` 里 `import logging` **零次**，诊断信息全靠 `print(...,
    file=sys.stderr)`（46 处，集中在 cli/serve/ui 三个入口）。后果有三：

      1. **不可分级** —— 想只看警告只能 `2>&1 | grep`，而 grep 关键词会漏
         （本项目已多次栽在「探针另抄一份词表」上）。
      2. **不可机器读** —— 没有 ts / level / logger 字段，多进程下（serve
         常驻 + CLI 调用 + UI 子进程）无法区分哪行是谁打的。
      3. **入口层各自 print ⇒ 口径漂移** —— 有的带 ❌ 有的不带，脚本判成败
         只能靠猜。

设计约束（与本项目其它模块同一套纪律）：

  · ⛔ **库层默认不装 handler**。与 `checkpoint_path` 默认 None 同理：
    装 handler 是进程级副作用，不该由 import 触发。只有入口
    （cli / serve / ui）显式调 `setup()`。
  · ⛔ **日志一律走 stderr**，结果输出走 stdout。两者混在一起 = 管道消费
    方（`$PY -m aether.cli status | jq`）拿到的不是 JSON。由
    `tests/contract_logging_test.py` 的 B 组守住。
  · ⛔ **`setup()` 幂等**：重复调用只覆盖自己的 handler，不叠加（否则
    `--log-json` 调两次会输出两遍）。
  · ⛔ **extra 字段不许覆盖保留键**：`msg`/`args`/`levelname` 等被
    `logging` 占用，覆盖会抛 `KeyError` ⇒ 冲突字段改名加 `x_` 前缀，
    而不是静默丢掉（静默丢掉 = 读数消失）。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

#: 保留键冲突时加的前缀（见模块 docstring 第 4 条）。
_RESERVED = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: 本次 `setup()` 的形态（读数：让「当前日志长什么样」可被查询，而不是
#: 靠猜 —— 与 `serve` 暴露 `source/hot_reload` 同一思路）。
_current: Dict[str, Any] = {"configured": False, "level": None,
                            "json": False, "stream": None}


class JsonFormatter(logging.Formatter):
    """每行一个 JSON 对象。⛔ 日志里**不许**出现不可序列化的值 —— 那会让
    整条日志抛 `TypeError` 而被 `logging` 内部吞掉（`handleError`），
    表现为「这条日志凭空消失」 ⇒ 用 `default=str` 兜底，宁可看到
    `<object at 0x...>` 也不要看不到。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S",
                                time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in _RESERVED or k.startswith("_"):
                continue
            payload[f"x_{k}" if k in _RESERVED else k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    #: 带 logger 名 —— 多进程下「哪行是谁打的」必须能一眼看出来。
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s │ %(message)s",
                         datefmt="%H:%M:%S")


def setup(level: str = "INFO", json_output: bool = False,
          stream: Optional[Any] = None) -> Dict[str, Any]:
    """配置根 logger。**幂等**：重复调用替换掉上一次的 handler，不叠加。

    返回当前形态快照（供 `--log-json` 之类读自己被配成了什么样）。
    """
    lv = str(level or "INFO").upper()
    if lv not in LEVELS:
        raise ValueError(f"未知日志级别 {lv!r}，可选 {LEVELS}")
    st = stream if stream is not None else sys.stderr

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_aether_owned", False):
            root.removeHandler(h)
    h = logging.StreamHandler(st)
    h._aether_owned = True                                   # noqa: SLF001
    h.setFormatter(JsonFormatter() if json_output else TextFormatter())
    root.addHandler(h)
    root.setLevel(getattr(logging, lv))

    _current.update({"configured": True, "level": lv,
                     "json": bool(json_output),
                     "stream": getattr(st, "name", repr(st))})
    return dict(_current)


def get_logger(name: str) -> logging.Logger:
    """库层/入口层取 logger。**不装 handler** —— 是否装由 `setup()` 决定。"""
    return logging.getLogger(name)


def current() -> Dict[str, Any]:
    """当前日志形态（未 `setup()` 时 `configured=False`）。"""
    return dict(_current)
