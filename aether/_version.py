#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本号与格式号的**唯一事实源**。

⛔ 为什么单独一个文件：`aether/__init__.py` 会 import 一堆核心模块
（ledger / verifier / …），而 `ledger` 需要知道格式版本 —— 如果版本号
住在 `__init__` 里，就形成 `__init__ → ledger → __init__` 的循环 import。
把两个常量放到最底层的这里，依赖方向就永远是单向的：

    _version.py  ←  provenance.py  ←  ledger.py / cli.py / serve.py …
                 ←  __init__.py

⛔ 任何地方都不许再写版本号字面量（同族教训：`_ID_PREFIX` 曾在 5 个文件
   里各写一份，漂移了没人发现）。要读就读这里的常量。
"""

#: Python 包（代码）版本。
#: ⛔ 只有在**账本/读数语义**发生可感知变化时才递增 —— 它参与
#:    `provenance.is_compatible` 的判据（0.x 期按次版本比），
#:    随手改会让"跨版本比读数"这件事悄悄复活。
CODE_VERSION = "0.2.0"

#: 账本文件格式版本。⛔ 改它 = 让全部历史 hash 失效（它参与
#:    `Event.identity()`），只能走"迁移 + 留痕"。
LEDGER_FORMAT = 1

__all__ = ["CODE_VERSION", "LEDGER_FORMAT"]
