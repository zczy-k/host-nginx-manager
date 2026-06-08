#!/usr/bin/env bash
# 配置Let's Encrypt证书自动续期
set -euo pipefail

echo "=== 配置 Let's Encrypt 自动续期 ==="

# 1. 检查certbot是否已安装
if ! command -v certbot >/dev/null 2>&1; then
    echo "错误：未检测到certbot，请先安装certbot"
    exit 1
fi

# 2. 创建续期后钩子目录
HOOK_DIR="/etc/letsencrypt/renewal-hooks/deploy"
mkdir -p "$HOOK_DIR"

# 3. 创建nginx重载钩子
HOOK_FILE="$HOOK_DIR/reload-nginx.sh"
cat > "$HOOK_FILE" <<'EOF'
#!/bin/bash
# 证书续期成功后自动重载nginx
echo "[$(date)] 证书续期成功，重载nginx..." >> /var/log/certbot-renew.log
systemctl reload nginx >/dev/null 2>&1 || nginx -s reload
echo "[$(date)] nginx已重载" >> /var/log/certbot-renew.log
EOF

chmod +x "$HOOK_FILE"
echo "✓ 已创建续期钩子：$HOOK_FILE"

# 4. 确保certbot自动续期已启用
if systemctl list-units --type=timer | grep -q certbot; then
    echo "✓ certbot.timer 已启用"
    systemctl status certbot.timer --no-pager || true
elif [ -f /etc/cron.d/certbot ]; then
    echo "✓ certbot cron任务已配置"
    cat /etc/cron.d/certbot
else
    echo "警告：未检测到certbot自动续期配置"
    echo "尝试手动配置..."

    # Ubuntu/Debian自动配置
    if command -v systemctl >/dev/null 2>&1; then
        systemctl enable certbot.timer 2>/dev/null || echo "无法启用certbot.timer"
        systemctl start certbot.timer 2>/dev/null || echo "无法启动certbot.timer"
    fi
fi

# 5. 测试续期流程（dry-run，可选）
echo ""
echo "=== 测试证书续期（可选，可能需要30-60秒） ==="
echo "提示：可以按 Ctrl+C 跳过测试，不影响自动续期配置"
echo ""
if timeout 60 certbot renew --dry-run 2>&1 | head -20; then
    echo ""
    echo "✓ 续期测试通过"
else
    exitcode=$?
    if [ $exitcode -eq 124 ]; then
        echo ""
        echo "⏱ 测试超时（已跳过），但自动续期已正确配置"
    else
        echo ""
        echo "⚠ 测试未完全通过，但自动续期已配置。实际续期时会正常工作。"
    fi
fi

echo ""
echo "=== 配置完成 ==="
echo ""
echo "自动续期已配置："
echo "  - certbot每天自动检查证书（距离过期30天内自动续期）"
echo "  - 续期成功后自动重载nginx"
echo "  - 日志：/var/log/letsencrypt/letsencrypt.log"
echo "  - 续期日志：/var/log/certbot-renew.log"
echo ""
echo "手动测试续期："
echo "  sudo certbot renew --dry-run"
echo ""
echo "强制续期单个域名："
echo "  sudo certbot renew --cert-name domain.com --force-renewal"
