#!/usr/bin/env python3
"""示例外部能力（python 形态）：被 capabilities.example.json 用
`scripts.ext_upper:upper` 引用。

⛔ 这种形态会**导入进本进程** ⇒ 清单里必须显式 `trusted:true`，
否则注册时被拒（不可信实现不许 inproc —— 杀不掉）。
"""
#: ⛔⛔ 这行不是风格问题，是**能不能在 3.9 上导入**的问题。
#:    `spec: dict | None` 是可求值的注解（没有这行的话），而 `dict | None`
#:    在 3.9/3.10 上会抛 `TypeError: unsupported operand type(s) for |:
#:    'type' and 'NoneType'` —— 于是这个模块**导入即崩**。
#:    而 `pyproject.toml` 声明 `requires-python = ">=3.9"`，`contract_serve_test`
#:    又通过 `scripts.ext_upper:upper` 热装载它 ⇒ 3.9 上 I 组 6 条全红，
#:    报的却是"清单没装载上任何能力"（第 144 条：真因藏在下游的形状）。
from __future__ import annotations


def upper(text: str) -> dict:
    return {"out": str(text).upper()}


def repair(source: str, baseline: str = "", spec: dict | None = None) -> dict:
    """示例修复技能：把 `>=` 改回 `<=`（保守、通用，不针对具体环境）。

    UCI 技能契约：输入 {"source","baseline","spec"}，输出 {"source", ...}。
    ⛔ 自称修好了不算修好 —— 判据由 `verify_source` 执行给出。
    """
    return {"source": str(source).replace(">=", "<="), "changed": " >= " in source}
