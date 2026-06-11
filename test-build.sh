#!/bin/bash
# 本地测试构建流程

set -e

echo "================================"
echo "本地构建测试"
echo "================================"

# 1. 检查 Python
echo ""
echo "检查 Python..."
python3 --version || { echo "❌ Python 3 未安装"; exit 1; }

# 2. 运行构建
echo ""
echo "运行构建脚本..."
python3 build.py

# 3. 检查输出
echo ""
echo "检查构建结果..."
if [ -f "dist/host_nginx_web.py" ]; then
    SIZE=$(stat -f%z "dist/host_nginx_web.py" 2>/dev/null || stat -c%s "dist/host_nginx_web.py")
    echo "✅ 构建成功: dist/host_nginx_web.py"
    echo "   文件大小: $SIZE 字节"
else
    echo "❌ 构建失败: dist/host_nginx_web.py 不存在"
    exit 1
fi

# 4. 语法检查
echo ""
echo "语法检查..."
python3 -m py_compile dist/host_nginx_web.py && echo "✅ 语法检查通过" || { echo "❌ 语法错误"; exit 1; }

# 5. 检查关键标志
echo ""
echo "检查关键功能..."
if grep -q "MODULAR_MODE" dist/host_nginx_web.py; then
    echo "✅ 包含模块化支持"
else
    echo "⚠️  未找到 MODULAR_MODE 标志"
fi

if grep -q "init_database" dist/host_nginx_web.py; then
    echo "✅ 包含数据库功能"
else
    echo "⚠️  未找到数据库功能"
fi

if grep -q "log_action" dist/host_nginx_web.py; then
    echo "✅ 包含审计日志"
else
    echo "⚠️  未找到审计日志"
fi

echo ""
echo "================================"
echo "✅ 本地构建测试通过"
echo "================================"
echo ""
echo "下一步："
echo "  git add ."
echo "  git commit -m 'refactor: 模块化架构'"
echo "  git push origin main"
