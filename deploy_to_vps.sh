#!/usr/bin/env bash
# 命灵插件（astrbot_plugin_mingbu）一键部署到 VPS 上的 AstrBot 插件库
#
# 用法：
#   1) 把本文件 scp 到 VPS：   scp deploy_to_vps.sh root@你的VPS:/root/
#   2) 编辑下面变量（二选一）：用 Docker 就保持 ASTRBOT_CONTAINER；用原生就填 DATA_PLUGINS
#   3) 在 VPS 上运行：         bash deploy_to_vps.sh
#
set -euo pipefail

PLUGIN_REPO="https://github.com/sloveniator/astrbot_plugin_mingbu.git"
PLUGIN_DIR="astrbot_plugin_mingbu"
BRANCH="main"                          # 当前活跃分支，v4.8.x 全部在 main 上维护

# ===== 二选一：A) Docker 填容器名；B) 原生填 data/plugins 绝对路径 =====
ASTRBOT_CONTAINER="astrbot"          # Docker 模式（容器内插件目录默认 /AStrBot/data/plugins）
DATA_PLUGINS=""                      # 原生模式：如 /opt/astrbot/data/plugins（与上面二选一，留空则走 Docker）

# 命理服务地址（可选）。不填则用插件默认 http://localhost:2963。
# 若命理服务在别的机器/容器，填这里，脚本会提示你在 compose 或 WebUI 配置 MINGBU_API_URL。
MINGBU_API_URL=""

echo "==> 开始部署命灵插件 (main 最新版) 分支: $BRANCH"
if [ -n "$DATA_PLUGINS" ]; then
  echo "==> [原生] clone 到 $DATA_PLUGINS"
  cd "$DATA_PLUGINS"
  if [ -d "$PLUGIN_DIR" ]; then
    echo "    目录已存在，切换到 $BRANCH 并 git pull 更新"
    git -C "$PLUGIN_DIR" fetch origin
    git -C "$PLUGIN_DIR" checkout "$BRANCH" 2>/dev/null || git -C "$PLUGIN_DIR" checkout -b "$BRANCH" "origin/$BRANCH"
    git -C "$PLUGIN_DIR" merge --ff-only "origin/$BRANCH"
  else
    git clone -b "$BRANCH" "$PLUGIN_REPO"
  fi
  echo "==> 请在 AstrBot WebUI 点「重载」，或重启 AstrBot 进程"
else
  echo "==> [Docker] 容器 $ASTRBOT_CONTAINER"
  if docker exec "$ASTRBOT_CONTAINER" test -d "/AStrBot/data/plugins/$PLUGIN_DIR"; then
    echo "    目录已存在，切换到 $BRANCH 并 git pull 更新"
    docker exec "$ASTRBOT_CONTAINER" bash -c "cd /AStrBot/data/plugins/$PLUGIN_DIR && git fetch origin && git checkout $BRANCH 2>/dev/null || git checkout -b $BRANCH origin/$BRANCH && git merge --ff-only origin/$BRANCH"
  else
    docker exec "$ASTRBOT_CONTAINER" bash -c "cd /AStrBot/data/plugins && git clone -b $BRANCH $PLUGIN_REPO"
  fi
  echo "==> 重载容器"
  docker restart "$ASTRBOT_CONTAINER"
fi

if [ -n "$MINGBU_API_URL" ]; then
  echo "==> 请确认已配置 MINGBU_API_URL=$MINGBU_API_URL"
  echo "    Docker: compose 的 environment 加  - MINGBU_API_URL=$MINGBU_API_URL  后 docker compose up -d"
  echo "    原生/WebUI: 插件配置填 mingbu_api_url"
fi
echo "==> 完成。查看日志： docker logs -f $ASTRBOT_CONTAINER 2>&1 | grep -i mingbu"
