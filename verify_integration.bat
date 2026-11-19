@echo off
REM 平衡型方案 - 快速验证脚本 (Windows)

echo ==========================================
echo 🚀 平衡型方案 - 快速验证
echo ==========================================
echo.

REM 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

echo ✓ Python环境检测通过
echo.

REM 进入后端目录
cd backend
if errorlevel 1 (
    echo ❌ backend目录不存在
    pause
    exit /b 1
)

echo ==========================================
echo 📝 步骤1：运行集成测试
echo ==========================================
echo.

python test_balanced_integration.py

if errorlevel 1 (
    echo.
    echo ❌ 测试失败，请检查错误信息
    pause
    exit /b 1
)

echo.
echo ==========================================
echo 📊 步骤2：运行方案对比测试（可选）
echo ==========================================
echo.
echo 这将对比原方案、打分制、动态阈值在历史数据上的表现
set /p choice="是否运行？(y/n): "

if /i "%choice%"=="y" (
    python test_filter_methods.py
)

echo.
echo ==========================================
echo ✅ 验证完成！
echo ==========================================
echo.
echo 📖 下一步：
echo.
echo 1. 启动后端：
echo    cd backend
echo    python main.py
echo.
echo 2. 启动前端（新终端）：
echo    cd frontend
echo    npm run dev
echo.
echo 3. 配置品种：
echo    - 打开交易面板
echo    - 展开任一品种
echo    - 找到「▸ 平衡型方案」配置块
echo    - 开启打分制和动态阈值
echo    - 保存配置
echo.
echo 4. 查看效果：
echo    - 信号列表会显示置信度评分
echo    - 中等置信度信号会自动半仓下单
echo.
echo 📚 详细文档：
echo    - BALANCED_SOLUTION_GUIDE.md   - 使用指南
echo    - INTEGRATION_SUMMARY.md       - 集成总结
echo    - FILTER_OPTIMIZATION_GUIDE.md - 方案详解
echo.
echo ==========================================

pause
