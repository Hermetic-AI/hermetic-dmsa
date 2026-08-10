"""静态版本号文件（手动管理；后续可切 setuptools_scm）。

发布流程：在 pyproject.toml 里 bump ``version`` 字段；CI 自动 publish。
"""
from __future__ import annotations

__version__ = "0.1.0"
