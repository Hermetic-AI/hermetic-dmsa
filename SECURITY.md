# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a Vulnerability

**请勿在 GitHub Issues 公开安全漏洞。**

通过 [GitHub Security Advisories](https://github.com/anthropic-research/hermetic-dmsa/security/advisories/new) 私下报告。

我们承诺：
- 48 小时内确认收到
- 7 天内评估严重性
- 30 天内发布修复（高危漏洞 7 天内）

## 安全建议

使用 hermetic-dmsa 处理用户数据时：

1. **PII 处理** — 默认不持久化 `feedback_text` 原文；如需保留请先做脱敏
2. **路径安全** — `dmsa-mcp-server --storage` 参数必须验证（库内部已做）
3. **反 DoS** — `feedback_text` 长度上限 100k 字符（库内部已做）
4. **依赖审计** — CI 跑 `pip-audit`；本地 `pip-audit` 也推荐
5. **MCP 协议** — stdio 模式假设 server 与 client 同机；如跨网络需自行 TLS 包装

## 已修复漏洞

暂无。
