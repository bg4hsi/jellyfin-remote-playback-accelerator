# v18.3 Session 状态设计

v18.3 重点解决 Jellyfin 转码过程中 hash 变化导致预取状态失效的问题。

## 状态流程

```
NEW SESSION
    |
    v
WARMUP
    |
    v
LIVE PREFETCH
    |
    v
DRAIN
    |
    v
HOLD
```

## Session 标识

worker 使用：

- HLS stream prefix
- Jellyfin hash
- 文件扩展名

组成当前预取上下文。

当 hash 变化时：

1. 停止旧预取上下文；
2. 清空旧 segment 状态；
3. 建立新的 session；
4. 重新 warmup。

## DRAIN

当 NAS 转码结束后，不立即停止。

如果播放器仍然活跃：

- 使用 last_generated 作为目标；
- 继续把已生成 segment 搬入 VPS cache。

## HOLD

全部 segment 已进入缓存后，停止继续请求，等待后续 session 管理器处理。
