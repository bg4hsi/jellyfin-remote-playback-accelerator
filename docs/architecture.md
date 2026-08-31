# 架构说明

## 数据路径

播放请求先到 VPS Nginx。HLS 分片命中 `jellyfin_media` cache zone 时直接由 VPS 返回；MISS 时通过 `127.0.0.1:18096` 的 SSH 反向转发访问家庭 Jellyfin。

njs 从成功经过播放 location 的分片 URI 中提取：

- 分片前缀，例如 `/videos/00000000-0000-0000-0000-000000000000/hls1/main/`；
- 当前序号；
- 扩展名；
- 最后一次请求时间。

worker 同时读取玩家状态和家庭辅助服务状态。家庭端仍在转码时，源端上限为
`safe_prefetch_max`；转码结束后源端上限切换为 `last_generated`。无论哪种状态，
实际搬运上限始终为 `min(current + PREFETCH_WINDOW, 源端上限)`，默认窗口为 500。

## 为什么需要两个反向转发

- `18096 -> 家庭 Jellyfin:8096`：正常播放 MISS 回源；
- `18097 -> 家庭辅助服务:18097`：读取转码状态并按 hash 安全读取已生成分片。

两个远端转发可以复用同一条 SSH TCP 连接；家庭到 VPS 的外层连接目的端口是 443。

## 缓存键必须一致

播放和预取 location 均使用：

```nginx
proxy_cache_key "hls|$uri|$http_range";
```

预取 URL 可以带内部参数 `jfhash`，但参数不能进入 cache key，也不能转发给播放器。否则会出现“worker 返回 200，播放仍然 MISS”。

## 状态机

```text
最近有分片请求 -> PLAYING -> live 到 current+500
NAS 转码结束   -> PLAYING -> 滚动 drain 到 current+500
窗口已经完整   -> HOLD    -> 等待 current 前进
长时间无请求   -> STALE   -> 停止新增预取
新分片请求     -> PLAYING -> 恢复
```

## 已知限制

1. 示例状态区只保存一个活跃流，多用户同时播放会互相覆盖。
2. HLS 无请求既可能是暂停，也可能是关闭。没有 Jellyfin PlaybackStop 事件时无法可靠区分。
3. 开源版不执行“猜测关闭后精确删除”。压力感知 cleaner 只在磁盘不足且播放器位置新鲜时，按 `[current, current+500]` 回收；它不能替代可靠的 PlaybackStop 事件。
4. 辅助服务假设转码文件命名为 `<hash><序号>.<扩展名>`；不同 Jellyfin 版本或自定义转码器需要调整。
5. 示例仅演示 `.ts`。如使用 fMP4，需要同时审查初始化分片和 Range 请求的 cache key。
6. 公网入口、TLS 证书、SSH 守护进程变更均需按自己的系统发行版处理。
