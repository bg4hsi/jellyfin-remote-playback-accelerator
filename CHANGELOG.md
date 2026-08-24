# 更新日志

## v18.4.1

### 起播与续播回归修复

- 忽略新会话建立时短暂出现的 `current=0`，等待播放器报告真实位置后再预取；
- LIVE 单轮最多处理 32 个分片，避免跳转或续播后被过期的大批次任务阻塞；
- 保留 v18.4 的缓存落盘校验、完整 DRAIN 和缓存缺失自动修复能力；
- 新增 `LIVE_BATCH` 参数，默认值为 32。

## v18.4

### 缓存落盘校验与完整 DRAIN

- 预取请求返回 200 后，再用 `X-Cache-Status: HIT` 确认缓存已经真正落盘；
- 缓存对象丢失时自动撤销完成标记，并优先修复播放器前方分片；
- NAS 转码结束后，DRAIN 不再受实时预取窗口限制，分批推进到 `last_generated`；
- DRAIN 期间允许播放器状态暂时过期，避免暂停导致剩余同步中断；
- 新增 `DRAIN_BATCH`、`CACHE_VERIFY_BATCH` 和 `CACHE_VERIFY_RETRIES` 参数；
- 明确禁止在 NAS 变为 inactive 时直接删除正在使用的 nginx 缓存目录。

## v18.3

### Jellyfin 远程播放加速器 Session 管理版

新增：

- 播放 Session 管理；
- Jellyfin hash 变化检测；
- 新播放自动重新初始化；
- WARMUP 预热机制；
- LIVE 实时预取；
- DRAIN 转码完成后继续同步；
- HOLD 保留缓存状态。

## v18.2

- 修复转码完成后状态切换问题；
- 增加 drain 状态管理。

## v18.1

- 增加 NAS 转码完成后的分片同步逻辑。

## v18

- 优化缓存生命周期管理。
