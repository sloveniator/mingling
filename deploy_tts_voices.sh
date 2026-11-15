#!/bin/bash
# 音色库功能部署脚本
# 用途：在 NAS 容器内拉取最新代码并重载插件

set -e

PLUGIN_DIR="/volume8/AI/astrbot/astrbot_plugin_mingbu"
RELOAD_API="http://127.0.0.1:6185/api/v1/plugins/reload"
PLUGIN_ID="mingbu"

echo "========================================"
echo "  音色库功能部署"
echo "========================================"
echo ""

# 检查目录是否存在
if [ ! -d "$PLUGIN_DIR" ]; then
    echo "错误：插件目录不存在：$PLUGIN_DIR"
    echo "请确认容器已启动且路径正确"
    exit 1
fi

echo "1. 拉取最新代码..."
cd "$PLUGIN_DIR"
git pull origin main

echo ""
echo "2. 验证代码完整性..."
if [ -f "stars/apis/tts_voices.py" ]; then
    echo "   ✓ stars/apis/tts_voices.py 存在"
else
    echo "   ✗ stars/apis/tts_voices.py 缺失"
    exit 1
fi

if [ -f "pages/admin/index.html" ]; then
    echo "   ✓ pages/admin/index.html 存在"
else
    echo "   ✗ pages/admin/index.html 缺失"
    exit 1
fi

echo ""
echo "3. 重载插件..."
curl -s -X POST "$RELOAD_API" \
    -H "Content-Type: application/json" \
    -d "{\"plugin_id\": \"$PLUGIN_ID\"}" \
    -o /dev/null -w "HTTP 状态码：%{http_code}\n"

echo ""
echo "========================================"
echo "  部署完成！"
echo "========================================"
echo ""
echo "下一步操作："
echo "1. 打开管理面板：http://127.0.0.1:6185/admin"
echo "2. 点击「音色库」标签"
echo "3. 确保已填写 DashScope API Key（核心配置 → 外部服务 → TTS）"
echo "4. 点击「拉取音色列表」按钮"
echo "5. 浏览音色并试听"
echo ""
