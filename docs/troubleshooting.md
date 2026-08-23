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

## 辅助服务返回 404

转码文件可能尚未生成，worker 会在下一轮重试。若状态中的 `last_generated` 已超过目标但仍 404，检查 `TRANSCODE_DIR` 和文件命名是否符合 `<hash><序号>.ts`。

## 辅助服务返回 409

通常表示请求的 hash 已不是最新转码任务。停止旧 worker，确认玩家状态和家庭状态属于同一次播放。

## 443 无法监听

检查该 IP 的 443 是否已由 Nginx、Caddy 或其他服务占用。不要让两个服务直接抢同一个 socket；选择独立 IP、分流器或不同端口。
