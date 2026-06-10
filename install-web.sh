#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

APP_NAME="host-nginx-manager"
INSTALL_DIR="/opt/host-nginx-manager"
WEB_DIR="$INSTALL_DIR/web"
ENV_DIR="/etc/host-nginx-manager"
ENV_FILE="$ENV_DIR/web.env"
SERVICE_FILE="/etc/systemd/system/host-nginx-manager-web.service"
MANAGER_BIN="/usr/local/sbin/host-nginx-manager"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main}"
BIND_ADDR="${HNG_WEB_BIND:-0.0.0.0}"
PORT="${HNG_WEB_PORT:-8098}"

GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
BLUE=$'\033[0;34m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'

log() { printf "%b[OK]%b %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%b[! ]%b %s\n" "$YELLOW" "$NC" "$*"; }
die() { printf "%b[x ]%b %s\n" "$RED" "$NC" "$*"; exit 1; }
info() { printf "%b[i ]%b %s\n" "$BLUE" "$NC" "$*"; }
section() { printf "\n%b%s%b\n" "$BOLD$CYAN" "$*" "$NC"; }

# 全局变量：用于交互输入的终端设备
TTY_IN=""

require_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "请使用 root 身份运行安装脚本"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

install_packages() {
    if command_exists python3 && command_exists curl; then
        return 0
    fi
    if command_exists apt-get; then
        DEBIAN_FRONTEND=noninteractive apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 curl ca-certificates
    else
        die "未找到 python3/curl，且当前系统不支持自动安装，请手动安装后重试"
    fi
}

generate_secret() {
    python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

detect_mode() {
    if [[ -f "$ENV_FILE" ]] && [[ -f "$SERVICE_FILE" ]]; then
        return 0  # 已安装
    else
        return 1  # 未安装
    fi
}

show_menu() {
    clear
    printf "${BOLD}${CYAN}\n"
    printf "╔════════════════════════════════════════════════╗\n"
    printf "║     Host Nginx Manager - 安装/升级工具        ║\n"
    printf "╚════════════════════════════════════════════════╝\n"
    printf "${NC}\n"

    if detect_mode; then
        section "检测到已安装的版本"
        if [[ -f "$ENV_FILE" ]]; then
            # shellcheck disable=SC1090
            . "$ENV_FILE" 2>/dev/null || true
            info "安装目录: $INSTALL_DIR"
            info "管理地址: http://${HNG_WEB_BIND:-未知}:${HNG_WEB_PORT:-未知}"
        fi
        if systemctl is-active host-nginx-manager-web.service >/dev/null 2>&1; then
            printf "%b[✓]%b 服务状态: 运行中\n" "$GREEN" "$NC"
        else
            printf "%b[x]%b 服务状态: 已停止\n" "$RED" "$NC"
        fi

        echo ""
        echo "请选择操作："
        echo "  1) 升级到最新版本（保留配置和密码）"
        echo "  2) 全新安装（重置所有配置）"
        echo "  3) 退出"
        echo ""
    else
        section "未检测到已安装的版本"
        echo ""
        echo "请选择操作："
        echo "  1) 全新安装"
        echo "  2) 退出"
        echo ""
    fi
}

do_upgrade() {
    section "开始升级 Host Nginx Manager"

    # 1. 备份配置
    info "1/5 备份当前配置..."
    local backup_date=$(date +%Y%m%d%H%M%S)
    if [[ -d /etc/nginx/vps-proxy-manager ]]; then
        cp -r /etc/nginx/vps-proxy-manager "/etc/nginx/vps-proxy-manager.bak.$backup_date" 2>/dev/null || true
        log "已备份站点配置"
    fi
    if [[ -f "$ENV_FILE" ]]; then
        cp "$ENV_FILE" "$ENV_FILE.bak.$backup_date"
        log "已备份环境配置"
    fi

    # 2. 升级CLI脚本
    info "2/5 升级管理脚本..."
    curl -fsSL "$RAW_BASE/host-nginx-manager.sh" -o "$MANAGER_BIN"
    chmod 0755 "$MANAGER_BIN"
    log "管理脚本已更新"

    # 3. 升级Web界面
    info "3/5 升级Web界面..."
    mkdir -p "$WEB_DIR"
    curl -fsSL "$RAW_BASE/web/host_nginx_web.py" -o "$WEB_DIR/host_nginx_web.py"
    chmod 0755 "$WEB_DIR/host_nginx_web.py"
    log "Web界面已更新"

    # 4. 重启服务
    info "4/5 重启Web服务..."
    systemctl daemon-reload
    systemctl restart host-nginx-manager-web.service
    log "服务已重启"

    # 5. 检查服务状态
    info "5/5 检查服务状态..."
    sleep 2
    if systemctl is-active host-nginx-manager-web.service >/dev/null 2>&1; then
        log "服务运行正常"
    else
        warn "服务启动异常，查看日志："
        systemctl status host-nginx-manager-web.service --no-pager -l
        return 1
    fi

    section "升级完成！"
    echo ""
    log "✓ 所有组件已升级到最新版本"
    log "✓ 配置和密码已保留"
    echo ""

    # 显示访问信息
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        info "管理地址: http://${HNG_WEB_BIND}:${HNG_WEB_PORT}"
        if [[ -n "${HNG_WEB_PASSWORD:-}" ]]; then
            info "管理密码: $HNG_WEB_PASSWORD"
        else
            info "密码已加密存储（查看 $ENV_FILE 中的 HNG_WEB_PASSWORD_HASH）"
        fi
    fi

    echo ""
    printf "%b新功能：%b\n" "$BOLD" "$NC"
    echo "  • 证书详情查看（Web界面 → 证书 → 查看详情）"
    echo "  • 手动续期证书（证书视图 → 续期按钮）"
    echo "  • 应用内帮助（Web界面 → 帮助）"
    echo "  • 失效配置清理（问题视图 → 删除失效配置）"
    echo ""
    info "证书自动续期说明："
    info "  certbot 默认已配置自动续期（每天2次检查）"
    info "  如需配置续期后自动重载nginx，运行："
    info "  curl -fsSL $RAW_BASE/setup-auto-renew.sh | sudo bash"
    echo ""
}

clean_before_install() {
    section "清理旧配置和数据"

    # 停止并禁用服务
    if systemctl is-active host-nginx-manager-web.service >/dev/null 2>&1; then
        info "停止服务..."
        systemctl stop host-nginx-manager-web.service || true
    fi

    if systemctl is-enabled host-nginx-manager-web.service >/dev/null 2>&1; then
        info "禁用服务..."
        systemctl disable host-nginx-manager-web.service >/dev/null 2>&1 || true
    fi

    # 删除配置文件
    if [[ -f "$ENV_FILE" ]]; then
        info "删除配置文件: $ENV_FILE"
        rm -f "$ENV_FILE"
    fi

    if [[ -d "$ENV_DIR" ]]; then
        info "删除配置目录: $ENV_DIR"
        rm -rf "$ENV_DIR"
    fi

    # 删除服务文件
    if [[ -f "$SERVICE_FILE" ]]; then
        info "删除服务文件: $SERVICE_FILE"
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
    fi

    # 删除安装目录（但保留受管站点和备份）
    if [[ -d "$WEB_DIR" ]]; then
        info "删除Web界面: $WEB_DIR"
        rm -rf "$WEB_DIR"
    fi

    log "旧配置已清理完成"
}

do_install() {
    section "开始安装 Host Nginx Manager"

    install_packages

    info "创建安装目录..."
    mkdir -p "$WEB_DIR" "$ENV_DIR"

    info "下载管理脚本..."
    curl -fsSL "$RAW_BASE/host-nginx-manager.sh" -o "$MANAGER_BIN"
    chmod 0755 "$MANAGER_BIN"

    info "下载Web界面..."
    curl -fsSL "$RAW_BASE/web/host_nginx_web.py" -o "$WEB_DIR/host_nginx_web.py"
    chmod 0755 "$WEB_DIR/host_nginx_web.py"

    # 生成新的密码和密钥（全新安装）
    info "生成新的密码和密钥..."
    local password secret password_hash
    password="$(generate_secret)"
    secret="$(generate_secret)"

    # 生成密码 hash
    info "生成密码 hash..."
    password_hash=$(python3 -c "
import base64, hashlib, secrets
salt = secrets.token_bytes(32)
pwdhash = hashlib.pbkdf2_hmac('sha256', '$password'.encode(), salt, 100000)
print(base64.b64encode(salt + pwdhash).decode('ascii'))
")

    info "生成配置文件..."
    cat > "$ENV_FILE" <<EOF
HNG_MANAGER_BIN=$MANAGER_BIN
HNG_WEB_BIND=$BIND_ADDR
HNG_WEB_PORT=$PORT
HNG_WEB_PASSWORD_HASH=$password_hash
HNG_WEB_SECRET=$secret
EOF
    chmod 0600 "$ENV_FILE"

    info "创建系统服务..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Host Nginx Manager Web UI
After=network.target nginx.service
Wants=nginx.service

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=$ENV_FILE
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $WEB_DIR/host_nginx_web.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/etc/nginx /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt /var/log/nginx /run /tmp

[Install]
WantedBy=multi-user.target
EOF

    info "启动服务..."
    systemctl daemon-reload
    systemctl enable host-nginx-manager-web.service >/dev/null
    systemctl restart host-nginx-manager-web.service

    section "安装完成！"
    echo ""
    log "Host Nginx Manager Web UI 已安装"
    printf '\n管理地址: http://%s:%s\n' "$BIND_ADDR" "$PORT"
    printf '管理密码: %s\n' "$password"
    if [[ "$BIND_ADDR" == "127.0.0.1" || "$BIND_ADDR" == "localhost" ]]; then
        printf '\n当前只监听本机，请在本地电脑执行 SSH 隧道：\n'
        printf 'ssh -L %s:127.0.0.1:%s root@你的服务器IP\n' "$PORT" "$PORT"
        printf '然后打开: http://127.0.0.1:%s\n' "$PORT"
    else
        printf '\n公网访问地址: http://你的服务器IP:%s\n' "$PORT"
        warn "Web 面板已监听公网地址，请确认云安全组/防火墙仅向可信 IP 放行 $PORT 端口。"
    fi
    warn "请妥善保存管理密码。密码保存在 $ENV_FILE"
    echo ""
}

main() {
    require_root

    # 如果有命令行参数，直接执行（脚本化用法）
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --upgrade)
                do_upgrade
                return 0
                ;;
            --install)
                install_packages
                do_install
                return 0
                ;;
            *)
                echo "未知参数: $1"
                echo "用法: $0 [--install|--upgrade]"
                exit 1
                ;;
        esac
    fi

    # 判断是否有可用的交互终端
    # curl | bash 时 stdin 是管道，但 /dev/tty 仍可读取键盘输入
    if [[ -t 0 ]]; then
        TTY_IN="/dev/stdin"
    elif [[ -e /dev/tty ]] && (exec </dev/tty) 2>/dev/null; then
        TTY_IN="/dev/tty"
    fi

    # 没有任何可交互的终端：回退到自动模式
    if [[ -z "$TTY_IN" ]]; then
        if detect_mode; then
            section "检测到已安装版本（无交互终端），自动执行升级..."
            do_upgrade
        else
            section "未检测到已安装版本（无交互终端），自动执行安装..."
            install_packages
            do_install
        fi
        return 0
    fi

    # 交互模式：从 tty 读取用户选择
    while true; do
        show_menu
        printf "请输入选项 [1-3]: "
        local choice=""
        read -r choice <"$TTY_IN" || choice="3"

        case "$choice" in
            1)
                if detect_mode; then
                    do_upgrade
                else
                    install_packages
                    do_install
                fi
                echo ""
                printf "按回车键退出..."
                read -r _ <"$TTY_IN" || true
                exit 0
                ;;
            2)
                if detect_mode; then
                    echo ""
                    warn "⚠️  警告：全新安装会清理以下内容"
                    echo ""
                    echo "  将被删除："
                    echo "    • Web管理界面的配置和密码"
                    echo "    • systemd服务文件"
                    echo "    • 登录会话信息"
                    echo ""
                    echo "  不会删除："
                    echo "    ✓ 受管站点配置 (/etc/nginx/vps-proxy-manager/sites/)"
                    echo "    ✓ nginx站点配置 (/etc/nginx/sites-*/)"
                    echo "    ✓ SSL证书 (/etc/letsencrypt/)"
                    echo "    ✓ 备份文件 (/opt/host-nginx-manager/backups/)"
                    echo ""
                    printf "确认要全新安装吗？ [y/N]: "
                    local confirm=""
                    read -r confirm <"$TTY_IN" || confirm="n"
                    if [[ "$confirm" =~ ^[Yy]$ ]]; then
                        clean_before_install
                        install_packages
                        do_install
                        echo ""
                        printf "按回车键退出..."
                        read -r _ <"$TTY_IN" || true
                        exit 0
                    fi
                else
                    echo "退出安装程序"
                    exit 0
                fi
                ;;
            3)
                echo "退出安装程序"
                exit 0
                ;;
            *)
                warn "无效的选项，请重新选择"
                sleep 1
                ;;
        esac
    done
}

main "$@"
