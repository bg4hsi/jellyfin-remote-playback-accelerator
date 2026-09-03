# 排错指南

## worker 没有动作

检查 `/__prefetch_status` 是否显示 `tracked: true`，并确认 `track_age_ms` 小于 `PLAYER_STALE_SECONDS`。如果状态始终为空，通常是 njs 变量没有在日志格式中求值，或 URI 不符合分片正则。

## 预取成功但播放 MISS

依次核对：

1. 播放和预取是否使用同一个 `proxy_cache` zone；
2. `proxy_cache_key` 是否逐字一致；
3. 预取内部参数是否意外进入 cache key；
4. 播放是否带不同的 `Range`；
5. `proxy_cache_valid`、`inactive`、`max_size` 是否提前淘汰；
6. 上游是否返回 `Set-Cookie`、`Cache-Control: private/no-store` 或非 200/206。

v18.4.1 会在首次请求后再次确认 `X-Cache-Status: HIT`。如果日志出现
`cache_unverified`，检查内部预取 location 是否保留了：

```nginx
add_header X-Cache-Status $upstream_cache_status always;
```

## NAS 转码完成后突然全部 MISS

`active=false` 表示转码文件已经生成完成，不表示播放器已经关闭。不要使用定时任务在
该状态下执行 `rm -rf`、`find -delete` 或重建 nginx 缓存目录。此类清理会与 nginx
正在进行的写入发生竞争，常见错误包括：

```text
chmod() ... failed (2: No such file or directory)
unlink() ... failed (2: No such file or directory)
```

缓存容量应交给 `proxy_cache_path` 的 `inactive`、`max_size` 等策略管理。若需要按播放
Session 精确回收，应等待可靠的 PlaybackStop 事件，不能用 NAS 转码状态代替。

磁盘空间不足时也不要执行 `rm -rf /var/cache/nginx/.../*`。使用压力感知 cleaner：

- `JELLYFIN_CACHE_TRIGGER_FREE_GIB` 应高于 nginx `min_free`；
- 默认只保留当前会话 `[current, current+500]`，删除旧会话和窗口外分片；
- `current=0`、状态过期或状态接口失败时跳过本轮；
- 先运行 `--force --dry-run` 核对计划删除量；
- 删除后重启 nginx，避免磁盘文件与共享缓存索引不一致。

## 辅助服务返回 404

转码文件可能尚未生成，worker 会在下一轮重试。若状态中的 `last_generated` 已超过目标但仍 404，检查 `TRANSCODE_DIR` 和文件命名是否符合 `<hash><序号>.ts`。

## 同一 443 下其他服务快，Jellyfin 预取慢

共用端口不代表共用 TCP 连接。先从 OpenWrt 直连 NAS 的 `/prefetch` 和一个已生成分片，
再与 VPS 的 `18097` 对照。NAS 局域网快、VPS `18097` 慢，并且预取 SSH 到 Xray 的
本机连接持续积压时，可使用可选看门狗。不要用“流量低”作为唯一重连条件，否则暂停和
HOLD 会被误判；也不要因单次超时重启整个 Xray、DSM、Drive 或 nginx。

## 辅助服务返回 409

通常表示请求的 hash 已不是最新转码任务。停止旧 worker，确认玩家状态和家庭状态属于同一次播放。

## 443 无法监听

检查该 IP 的 443 是否已由 Nginx、Caddy 或其他服务占用。不要让两个服务直接抢同一个 socket；选择独立 IP、分流器或不同端口。
