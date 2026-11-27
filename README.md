# 美国50州本地新闻简报 | US Local News Daily Briefing

每日聚合全美50州最重要的政治、经济、社会和科技本地新闻，标题自动翻译为中文。

## 快速开始

**方式一：** 双击 `start.bat`

**方式二：**
```bash
pip install -r requirements.txt
python server.py
```

启动后在浏览器打开：
- **电脑**: http://localhost:3847
- **手机**: 页面底部会显示局域网链接，例如 `http://192.168.x.x:3847`

## 手机每日访问

1. 确保手机和电脑在同一 WiFi 网络
2. 启动服务后，用手机浏览器打开页面底部显示的链接
3. **iPhone**: 点击 Safari 分享按钮 →「添加到主屏幕」
4. **Android**: 点击 Chrome 菜单 →「添加到主屏幕」

这样每天只需点击主屏幕图标即可打开最新简报。

## 功能

- 覆盖全美50州本地新闻（States Newsroom 及合作媒体 RSS）
- 自动分类：政治 / 经济 / 社会 / 科技
- 英文标题下方显示中文翻译
- 点击标题查看摘要，可跳转阅读原文
- 「更新每日简报」按钮采集过去24小时新闻
- 每天 GMT 22:00 自动更新（含中文翻译）

## 每日自动更新（GMT 22:00）

- **服务器内置定时任务**：`server.py` 每天 22:00 UTC 自动采集并翻译新闻；若服务器在该时间处于休眠，下次启动时会自动补更新。可通过环境变量 `DAILY_UPDATE_HOUR_UTC` / `DAILY_UPDATE_MINUTE_UTC` 调整时间，`DAILY_UPDATE_ENABLED=false` 关闭。
- **GitHub Actions**：`.github/workflows/daily-update.yml` 每天 22:00 UTC 唤醒 Render 免费实例并触发更新（也可在 Actions 页面手动运行）。GitHub 定时任务可能延迟或被跳过，因此 22:20、22:45、23:15、01:00 UTC 设有备用触发，若当天已更新则自动跳过。每次运行会自动重新启用该定时任务，避免 GitHub 因仓库 60 天无活动而停用。`/api/status` 中的 `commit` 字段显示当前部署的版本。如网址不同，在仓库 Settings → Variables 中设置 `SITE_URL`。

## 数据来源

各州非营利新闻机构，包括 Alabama Reflector、Texas Tribune、CalMatters 等。

## 永久部署到 Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/CherieYingXue/us-local-news-briefing)

**一键部署：** 打开 [Render 部署链接](https://render.com/deploy?repo=https://github.com/CherieYingXue/us-local-news-briefing)，登录 Render，点击 **Apply** 即可。

部署完成后永久地址：**https://us-local-news-briefing.onrender.com**

GitHub 仓库：https://github.com/CherieYingXue/us-local-news-briefing

免费版 Render 在无访问时会休眠，首次打开需等待约 30 秒唤醒。
