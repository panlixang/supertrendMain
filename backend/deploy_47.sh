#!/bin/bash
# 部署脚本 - 更新47服务器代码

set -e

echo "=== 部署到47服务器 (47.84.106.154) ==="
echo ""

SERVER="root@47.84.106.154"
APP_DIR="/opt/supertrendMain"

echo "1. 备份当前代码..."
ssh $SERVER "cd $APP_DIR && cp -r backend backend_backup_$(date +%Y%m%d_%H%M%S) || true"

echo "2. 拉取最新代码..."
ssh $SERVER "cd $APP_DIR && git fetch origin && git reset --hard origin/master"

echo "3. 检查代码版本..."
ssh $SERVER "cd $APP_DIR && git log --oneline -3"

echo "4. 停止旧服务..."
ssh $SERVER "pkill -f 'python.*main.py' || true"
sleep 2

echo "5. 启动新服务..."
ssh $SERVER "cd $APP_DIR/backend && nohup python main.py > uvicorn.log 2>&1 &"
sleep 3

echo "6. 检查服务状态..."
ssh $SERVER "ps aux | grep 'python.*main.py' | grep -v grep"

echo ""
echo "=== 部署完成 ==="
echo ""
echo "检查服务健康状态:"
echo "  curl http://47.84.106.154:5174/api/trade/config | jq '.\"SNDK-USDT-SWAP\".enabled'"
echo ""
echo "查看日志:"
echo "  ssh $SERVER 'tail -f $APP_DIR/backend/uvicorn.log'"
