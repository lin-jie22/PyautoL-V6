#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""`python -m aether` 的入口。

⛔ 收口前 `python -m aether` **报错**（`No module named aether.__main__`），
   只有 `python -m aether.cli` / `.serve` / `.ui` 能用 —— 而
   "一个包可以直接跑"是 Python 的基本惯例，缺它就要在文档里教用户记住
   三个不同的 `-m` 路径（少记一个就"这个包跑不起来"）。

⛔ 这里**不实现**任何逻辑，只做转发：判据与命令表在 `aether.cli`，
   多一个入口就多一份会漂移的命令表（同族教训：三个入口的公共参数
   曾经各写一份）。`serve` / `ui` 同理，用 `--help` 看它们各自的参数。
"""
from __future__ import annotations

import sys

from .cli import main as _cli_main


def main(argv=None) -> int:
    """等同 `python -m aether.cli`。"""
    return _cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
