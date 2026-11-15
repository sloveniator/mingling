#!/bin/sh
# 命灵插件 (astrbot_plugin_mingbu) — 飞牛首次部署脚本
# 用法：在飞牛 NAS shell 下运行
#   git clone https://github.com/sloveniator/astrbot_plugin_mingbu.git /vol1/1000/docker/astrbot/data/plugins/astrbot_plugin_mingbu
#   cd /vol1/1000/docker/astrbot && docker compose restart
#
#   # 或一行搞定：
#   curl -sSL 'https://raw.githubusercontent.com/sloveniator/astrbot_plugin_mingbu/main/install.sh' | sh
#
# 前提：AstrBot 已通过 Docker 部署在 /vol1/1000/docker/astrbot

set -e

REPO="https://github.com/sloveniator/astrbot_plugin_mingbu.git"
PLUGIN_DIR="/vol1/1000/docker/astrbot/data/plugins/astrbot_plugin_mingbu"
ASTRBOT_DIR="/vol1/1000/docker/astrbot"

echo "============================================"
echo " 命灵插件 — 飞牛首次部署"
echo " 分支: main (默认)"
echo "============================================"

# 检查 AstrBot 是否已部署
if [ ! -d "$ASTRBOT_DIR" ]; then
  echo "❌ AstrBot 目录不存在: $ASTRBOT_DIR"
  echo "   请先部署 AstrBot 再安装插件"
  exit 1
fi

# 检查 plugins 目录
PLUGINS_DIR="/vol1/1000/docker/astrbot/data/plugins"
if [ ! -d "$PLUGINS_DIR" ]; then
  echo ">>> 创建 plugins 目录..."
  mkdir -p "$PLUGINS_DIR"
fi

# 克隆插件
if [ -d "$PLUGIN_DIR" ]; then
  echo ">>> 插件目录已存在，执行更新..."
  cd "$PLUGIN_DIR"
  git fetch origin
  git merge --ff-only origin/main
else
  echo ">>> 克隆插件到 $PLUGIN_DIR ..."
  git clone "$REPO" "$PLUGIN_DIR"
fi

# 重启 AstrBot 容器
echo ">>> 重启 AstrBot 容器..."
cd "$ASTRBOT_DIR"
if [ -f "docker-compose.yml" ] || [ -f "compose.yml" ]; then
  docker compose restart
else
  # 尝试通用容器名
  CONTAINER=$(docker ps --filter "name=astrbot" --format "{{.Names}}" | head -1)
  if [ -n "$CONTAINER" ]; then
    docker restart "$CONTAINER"
  else
    echo "⚠️ 未找到 AstrBot 容器，请手动重启"
  fi
fi

echo ""
echo "✅ 命灵插件部署完成！"
echo "   路径: $PLUGIN_DIR"
echo "   升级: cd $PLUGIN_DIR && bash update.sh"
