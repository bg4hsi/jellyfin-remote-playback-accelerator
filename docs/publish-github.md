# GitHub 发布步骤

以下步骤只操作本地目录和 GitHub，不登录或部署到 VPS。

## 1. 发布前检查

```bash
cd jellyfin-edge-cache
./scripts/check.sh
git status --short
```

人工再检查一次：

- 没有真实 IP、域名、用户名和主机名；
- 没有 `.env`、SSH 私钥、证书、token 或日志；
- Git 历史里也没有曾经提交过的秘密；
- README 中的特定运营商表现被写成现场观察，而非普遍保证。

## 2. 创建本地提交

```bash
git init
git add .
git commit -m "Initial open-source release"
git branch -M main
```

## 3. 使用 GitHub CLI 创建公开仓库

先登录：

```bash
gh auth login -h github.com
```

确认当前账号后创建并推送：

```bash
gh auth status
gh repo create jellyfin-edge-cache \
  --public \
  --description "Jellyfin 边缘缓存与 HLS 预取参考实现" \
  --source . \
  --remote origin \
  --push
```

如果同名仓库已经存在，改用：

```bash
git remote add origin https://github.com/YOUR_ACCOUNT/jellyfin-edge-cache.git
git push -u origin main
```

## 4. GitHub 页面设置

建议添加 topics：

```text
jellyfin, nginx, njs, hls, cache, prefetch, reverse-tunnel
```

然后启用 GitHub 的 Secret scanning、Push protection 和 Dependabot alerts（如账号套餐与仓库设置支持）。不要上传生产压缩包或现场日志作为 Release 附件。
