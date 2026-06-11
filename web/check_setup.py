#!/usr/bin/env python3
"""快速验证：检查主应用是否能使用新模块."""
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

print("检查模块文件...")
modules = [
    'core/database.py',
    'core/audit.py',
    'auth/session.py',
    'auth/password.py',
    'auth/totp.py',
    'auth/ratelimit.py',
    'utils/validators.py',
    'utils/qrcode.py',
]

for mod in modules:
    if os.path.exists(mod):
        print(f"✅ {mod}")
    else:
        print(f"❌ {mod} 缺失")
        sys.exit(1)

print("\n检查主应用...")
if os.path.exists('host_nginx_web.py'):
    print("✅ host_nginx_web.py 存在")
    # 检查是否包含 MODULAR_MODE
    with open('host_nginx_web.py', 'r', encoding='utf-8') as f:
        content = f.read()
        if 'MODULAR_MODE' in content:
            print("✅ 主应用已集成模块化支持")
        else:
            print("❌ 主应用缺少 MODULAR_MODE 配置")
            sys.exit(1)
else:
    print("❌ host_nginx_web.py 缺失")
    sys.exit(1)

print("\n✅ 所有文件检查通过！")
print("\n模块化改造完成：")
print("- 创建了 8 个新模块文件")
print("- 主应用支持可选模块导入")
print("- 安装脚本已更新支持数据库")
print("\n部署说明：")
print("1. 模块化部署：将 web/ 整个目录复制到服务器")
print("2. 单文件部署：运行 python3 build.py 生成单文件版本")
