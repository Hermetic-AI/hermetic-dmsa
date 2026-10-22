# PMD 源码发行包

`pmd-0.1.0.tar.gz` 是本仓库随附、固定 SHA-256 的 Apache-2.0 源码发行包。
来源：本研究工作区 `analysis/pmd_skeleton`，2026-10-22 快照，版本 0.1.0，
作者为其 pyproject 声明的 PMD Maintainers。该源目录没有独立 Git 历史，
因此不声称一个不存在的上游 commit；本文件与 `SHA256SUMS` 共同标识此快照。

归档保存全部 30 个原始 Python 库源文件、原 README、原 pyproject，以及构建元数据。
原 README 和元数据均声明 Apache 2.0；原目录没有 LICENSE 文件，打包时加入了
本仓库的完整 Apache-2.0 文本，除此之外没有修改 PMD 源码。
归档本身提供完整可修改源码，不是一个不可审计的二进制 wheel。

安装：`python -m pip install ./vendor/pmd-0.1.0.tar.gz`，再安装 DMSA。
禁止用未经核实的同名 PyPI 包替换。本仓库所有 CI 和 Docker 构建都从此归档安装。
DMSA wheel 保持标准独立依赖声明；单独发布 DMSA wheel 并不自动发布 PMD。
源码发行包包含此目录；离线安装还须预先下载其他第三方依赖。

更新流程：先在 PMD 的权威来源修改与测试，不在此归档维护第二个分叉；
将该来源的 pmd/、README.md、pyproject.toml 复制到干净临时目录，补全 Apache
许可证文本，再用 `python -m build --sdist --outdir vendor <临时目录>` 重建。
核对归档中每个 `.py` 与来源字节相同，更新 SHA256SUMS、来源日期、依赖版本和
deploy/requirements.lock，一并审查提交。若上游将来有固定 Git/tag 或正式发行，
优先迁移到该发行并保留本版本的归档与出处。不可静默覆盖一个已发布快照。
