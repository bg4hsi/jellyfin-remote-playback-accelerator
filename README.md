# Jellyfin Edge Cache

一个面向家庭 Jellyfin 的边缘缓存与 HLS 分片预取参考实现。它适用于这样的场景：家庭宽带下载正常，但公网远程访问时上行吞吐不稳定，导致起播慢、拖动等待或长时间播放缓冲。

本项目把公网入口和缓存放在 VPS，家庭端主动建立到 VPS 的反向隧道。播放器正在观看时，VPS 上的 worker 会提前拉取后续 HLS 分片；家庭端转码完成后，只要播放会话仍活跃，worker 会继续把已生成的剩余分片搬入 VPS 缓存。

> 这是经过脱敏的参考实现，不包含任何真实 IP、域名、账号、密钥、媒体 ID 或服务器信息。示例地址使用 RFC 5737 文档网段和 `example.com`。

## 适用范围

- 自己拥有或获授权管理的 Jellyfin、NAS 与 VPS；
- Jellyfin 远程播放使用 HLS 分片（示例为 `.ts`）；
- VPS 与家庭端之间可以建立 SSH 反向隧道；
- 家庭端可以运行只监听回环地址的只读分片辅助服务。

不适用于绕过计费、认证、访问控制或第三方网络安全策略。使用前请确认符合运营商条款和当地法律。

## 为什么隧道连接到 TCP 443

这个项目来自一个上海电信家庭宽带的现场观察：普通目标端口上的长期公网上传吞吐明显下降，而连接到 TCP 443 的加密隧道仍能保持更高、更稳定的吞吐。因此示例让家庭端主动连接 VPS 的 SSH 服务端口 `443`。

这只是特定线路、特定时间的实测现象，不代表所有上海电信线路，也不保证 443 永远满速。应先用相同协议、相同目的地、不同端口做对照测试。详见 [为什么使用 443 反向隧道](docs/why-443.md)。

这里的 `443` 是家庭端连接 VPS SSH 服务的**目的端口**，不是要求把明文 Jellyfin 暴露在 443。一个 IP 的同一个 TCP 443 不能同时直接交给 OpenSSH 和 Nginx；如还需要公网 HTTPS 443，请使用独立 IP、四层分流器，或把 SSH 和 HTTPS 分开到不同端口。

## 架构

```text
远程播放器
    |
    | HTTPS（示例公网入口 8443）
    v
VPS Nginx ──────> VPS HLS 缓存
    |  ^               ^
    |  |               |
    |  +── 预取 worker ┘
    |
    | 127.0.0.1:18096  播放源站反向转发
    | 127.0.0.1:18097  分片辅助服务反向转发
    |
    +========= 一条 SSH 连接，目的端口 TCP 443 =========+
                                                           |
                                                     家庭 NAS
                                               Jellyfin + 辅助服务
```

播放入口和预取入口使用同一个 Nginx cache zone 和完全相同的 cache key。预取请求在内部改写到家庭端的分片辅助服务，但保存时仍使用播放器原始 URI 作为 key，之后播放器才能命中。

完整说明见 [架构文档](docs/architecture.md)。

## 项目结构

```text
home/       家庭端只读分片辅助服务
njs/        记录播放器当前分片并输出本地状态
nginx/      VPS 缓存、播放入口、内部预取入口示例
worker/     VPS 预取 worker
systemd/    两端服务单元示例
tunnel/     SSH 反向隧道配置示例
scripts/    安装前检查和脱敏扫描
docs/       架构、443 原因、安装与排错说明
tests/      无网络单元测试
```

## 快速开始

这不是“一键覆盖生产环境”的安装器。先在测试机按 [安装示例](docs/install.md) 完成以下步骤：

1. 家庭端安装 `home/jellyfin_prefetch_origin.py`，把 `TRANSCODE_DIR` 指向 Jellyfin 转码目录；
2. 在 VPS 配置 Nginx cache、njs、播放入口和仅监听回环地址的预取入口；
3. 家庭端通过一条 SSH 连接建立 `18096`、`18097` 两个反向转发；
4. 在 VPS 启动 `worker/jellyfin_prefetch_worker.py`；
5. 用 `/__prefetch_status`、`X-Cache-Status` 和日志确认预取与播放使用同一缓存。

所有可调参数集中在 [.env.example](.env.example)。复制为 `/etc/jellyfin-edge-cache/worker.env` 或 `origin.env` 后再填写；不要提交真实配置。

只准备发布源码时，请按 [GitHub 发布步骤](docs/publish-github.md) 操作；这些步骤不会连接或修改 VPS。

## 安全边界

- 家庭端辅助服务与 VPS 预取入口默认只监听 `127.0.0.1`；
- 分片即使缓存 HIT，也通过 `auth_request` 向 Jellyfin 校验本次请求凭据，避免缓存绕过源站鉴权；
- 辅助服务只允许严格格式的 hash、序号和扩展名，拒绝路径穿越；
- 不在 URL、日志或仓库中保存 Jellyfin API key、SSH 私钥；
- 示例仅支持一个活跃播放流；多用户/多流需要把 njs 状态改成按会话保存；
- “暂停”和“关闭”不能只靠 HLS 请求间隔可靠区分。本版不会声称自动精确删除某个已关闭会话的 Nginx 缓存，而是使用 `inactive`、`max_size` 和缓存管理器淘汰。若要求关闭即删，应接入 Jellyfin 的明确 PlaybackStop 事件，并使用支持精确 purge 的缓存方案。

更多限制见 [架构文档](docs/architecture.md#已知限制)。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile worker/jellyfin_prefetch_worker.py home/jellyfin_prefetch_origin.py
./scripts/scan-secrets.sh
```

## 许可证

[MIT](LICENSE)
