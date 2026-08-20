#!/bin/bash
# 增强版部署检查清单

echo "=========================================="
echo "SuperTrend 增强版部署检查"
echo "=========================================="
echo ""

# 1. 检查增强模块文件
echo "1️⃣  检查增强模块文件..."
files=(
    "backend/regime_enhanced.py"
    "backend/position_enhanced.py"
    "backend/integration.py"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "   ✅ $file"
    else
        echo "   ❌ $file (缺失)"
    fi
done
echo ""

# 2. 检查代码修改
echo "2️⃣  检查代码修改..."

if grep -q "enhanced_signal_handler" backend/feed.py; then
    echo "   ✅ backend/feed.py 已修改（使用增强版信号处理）"
else
    echo "   ❌ backend/feed.py 未修改"
fi

if grep -q "create_enhanced_executor" backend/main.py; then
    echo "   ✅ backend/main.py 已修改（使用增强版执行器）"
else
    echo "   ❌ backend/main.py 未修改"
fi

if grep -q "gate_reasons" frontend/src/components/CandleChart.jsx; then
    echo "   ✅ frontend/src/components/CandleChart.jsx 已修改（显示拦截原因）"
else
    echo "   ❌ frontend/src/components/CandleChart.jsx 未修改"
fi

if grep -q "reasonBox" frontend/src/components/SignalList.jsx; then
    echo "   ✅ frontend/src/components/SignalList.jsx 已修改（显示详细原因）"
else
    echo "   ❌ frontend/src/components/SignalList.jsx 未修改"
fi
echo ""

# 3. 检查环境配置
echo "3️⃣  检查环境配置..."

if [ -f ".env" ]; then
    if grep -q "OKX_SIMULATED=1" .env; then
        echo "   ✅ 模拟盘模式（OKX_SIMULATED=1）"
    elif grep -q "OKX_SIMULATED=0" .env; then
        echo "   ⚠️  实盘模式（OKX_SIMULATED=0）- 建议先用模拟盘测试"
    else
        echo "   ⚠️  未设置 OKX_SIMULATED，默认为模拟盘"
    fi
else
    echo "   ⚠️  .env 文件不存在"
fi
echo ""

# 4. Python依赖检查
echo "4️⃣  检查Python环境..."
python3 --version 2>/dev/null || python --version 2>/dev/null
echo ""

# 5. 运行测试
echo "5️⃣  运行功能测试..."
if [ -f "backend/test_enhancements.py" ]; then
    echo "   可以运行: python backend/test_enhancements.py"
    read -p "   现在运行测试？(y/N): " run_test
    if [ "$run_test" = "y" ] || [ "$run_test" = "Y" ]; then
        cd backend && python test_enhancements.py
        cd ..
    fi
else
    echo "   ⚠️  测试文件不存在"
fi
echo ""

# 6. 启动指南
echo "=========================================="
echo "📋 下一步操作："
echo "=========================================="
echo ""
echo "1. 启动后端服务："
echo "   cd backend"
echo "   uvicorn main:app --port 8000 --reload"
echo ""
echo "2. 启动前端（另一个终端）："
echo "   cd frontend"
echo "   npm run dev"
echo ""
echo "3. 观察日志中的关键词："
echo "   - '动量突破' - 表示识别到大行情启动"
echo "   - '假突破' - 表示过滤了震荡陷阱"
echo "   - '仅提醒' - 表示信号被拦截"
echo ""
echo "4. 前端检查："
echo "   - 图表上未下单信号应该显示 ❌"
echo "   - 信号列表显示拦截原因"
echo ""
echo "⚠️  重要提醒："
echo "   - 确认是模拟盘模式"
echo "   - 观察2-4周再考虑切换实盘"
echo "   - 随时查看 DEPLOYMENT_COMPLETE.md 文档"
echo ""
echo "=========================================="
