#!/usr/bin/env python3
"""示例外部能力（python 形态）：被 capabilities.example.json 用
`scripts.ext_upper:upper` 引用。

⛔ 这种形态会**导入进本进程** ⇒ 清单里必须显式 `trusted:true`，
否则注册时被拒（不可信实现不许 inproc —— 杀不掉）。
"""


def upper(text: str) -> dict:
    return {"out": str(text).upper()}


def repair(source: str, baseline: str = "", spec: dict | None = None) -> dict:
    """示例修复技能：把 `>=` 改回 `<=`（保守、通用，不针对具体环境）。

    UCI 技能契约：输入 {"source","baseline","spec"}，输出 {"source", ...}。
    ⛔ 自称修好了不算修好 —— 判据由 `verify_source` 执行给出。
    """
    return {"source": str(source).replace(">=", "<="), "changed": " >= " in source}
