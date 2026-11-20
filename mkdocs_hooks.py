"""MkDocs hook：让文档站锚点与 GitHub 的锚点算法一致。

Python-Markdown 的默认 slugify 会做 NFKD 归一化再丢弃非 ASCII 字符，中文标题因此
被压成 `3-http`，与 GitHub 生成的 `3-http-接口契约` 对不上。仓库内的锚点链接按
GitHub 规则书写，因此这里改用保留 Unicode 的 slugify，两处锚点才会一致。

用法：在 mkdocs.yml 中通过 `hooks:` 引入本文件。
"""

from __future__ import annotations

from typing import Any, cast

from markdown.extensions.toc import TocExtension
from markdown.extensions.toc import slugify as _default_slugify


def _unicode_slugify(value: str, separator: str) -> str:
    """保留中日韩字符的锚点生成。"""
    return cast(str, _default_slugify(value, separator, unicode=True))


def on_config(config: Any, **kwargs: Any) -> Any:
    """用 Unicode 版 slugify 替换默认 toc 扩展。"""
    extensions = config["markdown_extensions"]
    if "toc" not in extensions:
        return config
    extension = TocExtension()
    extension.setConfig("slugify", _unicode_slugify)
    extensions[extensions.index("toc")] = extension
    return config
