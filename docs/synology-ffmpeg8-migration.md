# 群晖旧版预取检测器：FFmpeg 8 迁移

## 故障原因

旧版 `/usr/local/bin/jf-segment-status.py` 使用固定路径识别转码进程：

```python
FFMPEG = "/var/packages/ffmpeg7/target/bin/ffmpeg"
```

Jellyfin 改为使用 FFmpeg 8 后，真实 HLS 转码进程会被忽略。NAS 状态显示
`active_jobs=0`、`available=false`；VPS 虽然能跟踪播放位置，但拿不到转码 hash
和可用分片范围，预取 Worker 因此停留在 IDLE。

这是旧版进程检测器的迁移问题。本仓库 `home/jellyfin_prefetch_origin.py`
按转码目录检测任务，不使用上述固定路径，不需要执行此脚本。

## 适用范围与执行

仅适用于已安装上述旧版检测器、正在运行一个该检测器进程、且 Jellyfin
实际使用 `/var/packages/ffmpeg8/target/bin/ffmpeg` 的群晖。先用 `ps` 确认
真实 HLS 转码命令；仅安装了 FFmpeg 8 并不代表 Jellyfin 已切换到它。

将仓库脚本复制到 NAS 后，在 NAS 上运行：

```sh
sudo python3 scripts/fix_synology_ffmpeg8.py
```

脚本检查原设置与 FFmpeg 8 文件，保存带时间戳的原文件备份，再把固定路径
从 `ffmpeg7` 改为 `ffmpeg8`，只重启检测器。不会重启 Jellyfin、FFmpeg、
隧道或 Nginx。条件不匹配时会退出；检测器启动失败时尝试恢复原文件并重启。
重复执行已修复的文件会退出，不会再次修改。

脚本针对独立运行的检测器进程；如果检测器由服务管理器监管，应使用相应
服务管理器完成重启，不要直接运行此迁移脚本。修复保留了固定版本识别方式，
以后再次更换 FFmpeg 主版本时仍需检查路径。

## 验证

1. 播放一段需要 HLS 转码的视频，确认 NAS 状态恢复为 `active_jobs=1`，
   且返回有效 hash、`last_generated` 和 `safe_prefetch_max`。
2. 确认 VPS Worker 日志出现 `prefetched ... verified=1`。
3. 确认播放器前方分片开始命中 Nginx 缓存，而非只看预取 HTTP 200。

本次现场验证：修复前分片请求约 20–28 秒；修复后连续分片命中缓存，
所检查请求耗时均不足 1 秒。预取从当前第 361 片推进到第 443 片，
按每片约 3 秒计算，提前约 4 分钟。这是单次环境观测，不是速度保证。
跨境链路本身仍可能波动，预取通过提前缓存降低其对连续播放的影响。

## 回滚

迁移脚本会输出原文件备份路径。确认需要回滚后，用该备份恢复
`/usr/local/bin/jf-segment-status.py`，再重启检测器。旧设置只识别 FFmpeg 7，
如果 Jellyfin 仍使用 FFmpeg 8，回滚也会恢复原来的漏检行为。
