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

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { printf "%b[OK]%b %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%b[! ]%b %s\n" "$YELLOW" "$NC" "$*"; }
die() { printf "%b[x ]%b %s\n" "$RED" "$NC" "$*"; exit 1; }

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

main() {
    require_root
    install_packages

    mkdir -p "$WEB_DIR" "$ENV_DIR"

    curl -fsSL "$RAW_BASE/host-nginx-manager.sh" -o "$MANAGER_BIN"
    chmod 0755 "$MANAGER_BIN"

    curl -fsSL "$RAW_BASE/web/host_nginx_web.py" -o "$WEB_DIR/host_nginx_web.py"
    chmod 0755 "$WEB_DIR/host_nginx_web.py"

    local password secret
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        password="${HNG_WEB_PASSWORD:-}"
        secret="${HNG_WEB_SECRET:-}"
    else
        password=""
        secret=""
    fi
    password="${password:-$(generate_secret)}"
    secret="${secret:-$(generate_secret)}"

    cat > "$ENV_FILE" <<EOF
HNG_MANAGER_BIN=$MANAGER_BIN
HNG_WEB_BIND=$BIND_ADDR
HNG_WEB_PORT=$PORT
HNG_WEB_PASSWORD=$password
HNG_WEB_SECRET=$secret
EOF
    chmod 0600 "$ENV_FILE"

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

    systemctl daemon-reload
    systemctl enable host-nginx-manager-web.service >/dev/null
    systemctl restart host-nginx-manager-web.service

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
}

main "$@"
