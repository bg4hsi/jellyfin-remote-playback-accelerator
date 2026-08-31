# 安装示例

以下命令只说明文件位置和依赖关系，不应直接覆盖现有生产配置。先备份并在测试环境验证。

## 1. 家庭端辅助服务

```bash
sudo install -d -m 0750 /opt/jellyfin-edge-cache /etc/jellyfin-edge-cache
sudo install -m 0755 home/jellyfin_prefetch_origin.py /opt/jellyfin-edge-cache/
sudo install -m 0644 systemd/jellyfin-prefetch-origin.service.example /etc/systemd/system/jellyfin-prefetch-origin.service
sudo cp .env.example /etc/jellyfin-edge-cache/origin.env
```

编辑 `origin.env`，至少确认 `TRANSCODE_DIR`。然后：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jellyfin-prefetch-origin
curl http://127.0.0.1:18097/status
```

## 2. VPS Nginx

Nginx 需要 njs http 模块、`ngx_http_auth_request_module` 和支持 `js_shared_dict_zone` 的版本。按发行版方式在 `nginx.conf` 的 main 上下文加载动态模块；不要把 `load_module` 放进 `http {}` 内。把示例文件复制到发行版对应位置，并替换 `include` 路径；不要直接假设 `/etc/nginx/conf.d` 适用于所有系统。

```bash
sudo install -m 0644 njs/jellyfin_prefetch.js /etc/nginx/njs/
sudo install -m 0644 nginx/00-cache.conf.example /etc/nginx/conf.d/
sudo install -m 0644 nginx/playback.conf.example /etc/nginx/conf.d/
sudo install -m 0644 nginx/prefetch.conf.example /etc/nginx/conf.d/
sudo nginx -t
```

确认没有和现有端口、cache zone、日志格式冲突后再 reload。

## 3. SSH 反向隧道

先阅读 [tunnel/ssh-config.example](../tunnel/ssh-config.example)。家庭端的一条 SSH 连接创建两个远端转发：

```text
VPS 127.0.0.1:18096 -> 家庭 127.0.0.1:8096
VPS 127.0.0.1:18097 -> 家庭 127.0.0.1:18097
```

服务端应保持 `GatewayPorts no`，并用 `PermitListen` 仅允许这两个回环监听。

## 4. VPS worker

```bash
sudo install -d -m 0750 /opt/jellyfin-edge-cache /etc/jellyfin-edge-cache
sudo install -m 0755 worker/jellyfin_prefetch_worker.py /opt/jellyfin-edge-cache/
sudo install -m 0644 systemd/jellyfin-prefetch-worker.service.example /etc/systemd/system/jellyfin-prefetch-worker.service
sudo cp .env.example /etc/jellyfin-edge-cache/worker.env
sudo systemctl daemon-reload
sudo systemctl enable --now jellyfin-prefetch-worker
```

## 5. VPS 磁盘压力清理器（可选但推荐）

不要使用“NAS inactive 就删除整个 cache 目录”的脚本。转码结束不等于播放结束，
而且在 nginx 运行时整目录删除会让磁盘文件和共享缓存索引失去同步。

压力感知 cleaner 只在可用空间低于阈值时工作：删除其他旧会话缓存，以及当前
会话中窗口外的分片；默认只保留 `[current, current+500]`。
没有新鲜、有效的播放器位置时不会清理。

```bash
sudo install -m 0755 scripts/jellyfin_cache_cleaner.py /opt/jellyfin-edge-cache/
sudo install -m 0644 systemd/jellyfin-cache-cleaner.service.example \
  /etc/systemd/system/jellyfin-cache-cleaner.service
sudo install -m 0644 systemd/jellyfin-cache-cleaner.timer.example \
  /etc/systemd/system/jellyfin-cache-cleaner.timer
sudo cp .env.example /etc/jellyfin-edge-cache/cleaner.env
sudo systemctl daemon-reload

# 首次先检查计划，不删除文件
sudo /opt/jellyfin-edge-cache/jellyfin_cache_cleaner.py --force --dry-run

sudo systemctl enable --now jellyfin-cache-cleaner.timer
```

`JELLYFIN_CACHE_TRIGGER_FREE_GIB` 必须高于 nginx 的 `min_free`，示例分别为
3 GiB 和 2 GiB。实际删除后 cleaner 会检查配置并重启一次 nginx，以重置共享
缓存索引；正常空间检查不会重启 nginx。

## 6. 验证

开始播放后在 VPS 检查：

```bash
curl http://127.0.0.1:8098/__prefetch_status
curl http://127.0.0.1:18097/status
journalctl -u jellyfin-prefetch-worker -f
journalctl -u jellyfin-cache-cleaner -f
```

播放器后续分片应从 `X-Cache-Status: MISS` 逐步变成 `HIT`。如果预取接口返回 200 但播放器仍 MISS，首先比较两个 location 的 cache zone、cache key、URI 和 Range。

再用无凭据请求相同分片，确认返回 `401` 或 `403`，不能因为已有缓存而返回媒体内容。
