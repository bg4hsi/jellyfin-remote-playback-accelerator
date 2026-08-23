# 安全说明

请不要在 Issue、日志或配置片段中提交真实域名、IP、用户名、Jellyfin token、媒体 ID、SSH 私钥或完整请求 URL。

发现安全问题时，请使用 GitHub Security Advisory 私下报告，不要先创建公开 Issue。

部署时至少遵守以下原则：

- `8098`、`18096`、`18097` 只绑定 VPS 回环地址；
- 家庭端辅助服务只绑定回环地址，并仅通过 SSH 反向转发访问；
- 公网播放入口启用 TLS 和正常的 Jellyfin 鉴权；
- SSH 使用专用低权限账号、密钥认证和 `PermitListen` 限制；
- 不要把真实 `.env`、私钥、证书或日志提交到仓库。
