#!/bin/sh
# 命灵插件 (astrbot_plugin_mingbu) — 飞牛一键升级脚本
# 用法：bash /vol1/1000/docker/astrbot/data/plugins/astrbot_plugin_mingbu/update.sh

set -e

PLUGIN_DIR="/vol1/1000/docker/astrbot/data/plugins/astrbot_plugin_mingbu"
ASTRBOT_DIR="/vol1/1000/docker/astrbot"

echo "============================================"
echo " 命灵插件 — 一键升级"
echo "============================================"

if [ ! -d "$PLUGIN_DIR" ]; then
  echo "❌ 插件目录不存在: $PLUGIN_DIR"
  echo "   请先运行 install.sh 首次部署"
  exit 1
fi

cd "$PLUGIN_DIR"

echo ">>> 当前版本: $(git describe --tags 2>/dev/null || git rev-parse --short HEAD)"
echo ">>> 拉取最新代码..."

# 丢弃本地未提交的改动（install.sh/update.sh 等可能已被覆盖）
git stash --include-untracked || true
git fetch origin
git merge --ff-only origin/main

NEW_VERSION=$(git describe --tags 2>/dev/null || git rev-parse --short HEAD)
echo ">>> 更新至: $NEW_VERSION"

# 重启 AstrBot 容器
echo ">>> 重启 AstrBot 容器..."
cd "$ASTRBOT_DIR"
if [ -f "docker-compose.yml" ] || [ -f "compose.yml" ]; then
  docker compose restart
else
  CONTAINER=$(docker ps --filter "name=astrbot" --format "{{.Names}}" | head -1)
  if [ -n "$CONTAINER" ]; then
    docker restart "$CONTAINER"
  else
    echo "⚠️ 未找到 AstrBot 容器，请手动重启"
  fi
fi

echo ""
echo "✅ 升级完成！"
echo "   新版本: $NEW_VERSION"
echo "   查看日志: docker logs -f $(docker ps --filter 'name=astrbot' --format '{{.Names}}' | head -1) 2>&1 | grep -i mingbu"
