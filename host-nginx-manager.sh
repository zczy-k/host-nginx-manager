#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

SCRIPT_VERSION="1.0.0"
APP_LABEL="VPS Nginx 代理管理器"
MANAGER_ROOT="/etc/nginx/vps-proxy-manager"
SITE_STATE_DIR="$MANAGER_ROOT/sites"
ACME_ROOT="$MANAGER_ROOT/acme"
BACKUP_DIR="$MANAGER_ROOT/backups"
SITE_PREFIX="vpspm"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

COMMAND="${1:-help}"
shift || true

YES_MODE=0
DELETE_CERT=0
UPSTREAM_SCHEME="http"
CLIENT_MAX_BODY_SIZE="64m"
PROXY_READ_TIMEOUT="300s"
PROXY_SEND_TIMEOUT="300s"
WEBSOCKET=1
ENABLE_SSL=1
CERTBOT_EMAIL=""
BACKEND_INSECURE=0
AUTO_RENEW=1
DOMAIN=""
UPSTREAM=""

log()   { printf "%b[OK]%b %s\n" "$GREEN" "$NC" "$*"; }
info()  { printf "%b[i ]%b %s\n" "$BLUE" "$NC" "$*"; }
warn()  { printf "%b[! ]%b %s\n" "$YELLOW" "$NC" "$*"; }
error() { printf "%b[x ]%b %s\n" "$RED" "$NC" "$*"; }
die()   { error "$*"; exit 1; }
section() { printf "\n%b%s%b\n" "$BOLD$CYAN" "$*" "$NC"; }

usage() {
    cat <<EOF
$APP_LABEL v$SCRIPT_VERSION

用法:
  host-nginx-manager.sh add DOMAIN UPSTREAM [选项]
  host-nginx-manager.sh update DOMAIN UPSTREAM [选项]
  host-nginx-manager.sh enable-ssl DOMAIN [--email EMAIL]
  host-nginx-manager.sh renew DOMAIN
  host-nginx-manager.sh disable-ssl DOMAIN
  host-nginx-manager.sh rename OLD_DOMAIN NEW_DOMAIN [--upstream UPSTREAM] [--delete-old-cert]
  host-nginx-manager.sh set-auto-renew DOMAIN [0|1]
  host-nginx-manager.sh remove DOMAIN [--yes] [--delete-cert]
  host-nginx-manager.sh diagnose DOMAIN
  host-nginx-manager.sh health-check [DOMAIN]
  host-nginx-manager.sh backup [--output FILE]
  host-nginx-manager.sh restore BACKUP_FILE
  host-nginx-manager.sh list-backups
  host-nginx-manager.sh list
  host-nginx-manager.sh show DOMAIN
  host-nginx-manager.sh test
  host-nginx-manager.sh reload
  host-nginx-manager.sh help

说明:
  - 此脚本只管理它自己创建的标准 HTTP/HTTPS 反向代理站点。
  - 不会自动修改你当前 nginx.conf 里的 stream、Rathole、假证书拦截等手写配置。
  - 适合把普通 Web/API 服务统一挂到不同子域名下，由宿主 nginx 复用 80/443。

参数:
  --upstream-scheme http|https   后端协议，默认 http
  --email EMAIL                  申请 Let's Encrypt 证书时使用的邮箱
  --no-ssl                       新增站点时先只创建 HTTP 反代，不立即启用 HTTPS
  --client-max-body-size SIZE    如 64m / 512m / 0，默认 64m
  --proxy-read-timeout TIME      默认 300s
  --proxy-send-timeout TIME      默认 300s
  --backend-insecure             当后端是自签 HTTPS 时关闭证书校验
  --yes                          跳过确认
  --delete-cert                  remove 时一并删除 certbot 证书

示例:
  host-nginx-manager.sh add api.example.com 127.0.0.1:3001 --email you@example.com
  host-nginx-manager.sh update api.example.com 127.0.0.1:3002 --upstream-scheme http
  host-nginx-manager.sh add metapi.cni.de5.net 127.0.0.1:3001 --upstream-scheme http --no-ssl
  host-nginx-manager.sh enable-ssl metapi.cni.de5.net --email you@example.com
  host-nginx-manager.sh renew metapi.cni.de5.net
  host-nginx-manager.sh set-auto-renew metapi.cni.de5.net 1
  host-nginx-manager.sh set-auto-renew metapi.cni.de5.net 0
  host-nginx-manager.sh remove api.example.com --delete-cert --yes
EOF
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

require_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "请使用 root 身份运行此脚本"
}

require_linux() {
    [[ "$(uname -s)" == "Linux" ]] || die "此脚本仅支持 Linux"
}

require_nginx() {
    command_exists nginx || die "未检测到 nginx，请先安装 nginx"
}

require_certbot() {
    command_exists certbot || die "未检测到 certbot，请先安装 certbot"
}

confirm() {
    local prompt="$1"
    local reply=""
    if [[ $YES_MODE -eq 1 ]]; then
        return 0
    fi
    read -r -p "$(printf '%b?%b %s [y/N]: ' "$YELLOW" "$NC" "$prompt")" reply || true
    [[ "$reply" =~ ^[Yy]$ ]]
}

normalize_domain() {
    local raw="$1"
    raw="${raw,,}"
    raw="${raw#http://}"
    raw="${raw#https://}"
    raw="${raw%%/*}"
    printf '%s\n' "$raw"
}

validate_domain() {
    [[ "$1" =~ ^[a-z0-9.-]+$ ]] || return 1
    [[ "$1" == *.* ]]
}

validate_upstream() {
    [[ "$1" =~ ^[^:]+:[0-9]+$ ]]
}

check_backend_health() {
    local upstream="$1"
    local scheme="${2:-http}"
    local host="${upstream%:*}"
    local port="${upstream##*:}"

    info "正在检查后端连通性..."

    # 1. 检查端口是否监听
    if ! timeout 3 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null; then
        warn "后端连接失败"
        echo ""
        echo "━━━ 诊断信息 ━━━"
        echo "后端地址：$scheme://$upstream"
        echo "状态：端口无响应"
        echo ""
        echo "━━━ 可能原因 ━━━"
        echo "1. 后端服务未启动"
        echo "2. 端口号配置错误"
        echo "3. 防火墙阻止连接"
        echo "4. 服务启动中（尚未监听端口）"
        echo ""
        echo "━━━ 排查命令 ━━━"
        echo "检查端口监听：sudo ss -tlnp | grep :$port"
        echo "检查进程状态：sudo systemctl status <服务名>"
        echo "查看服务日志：sudo journalctl -u <服务名> -n 50"
        echo "手动测试：curl -v $scheme://$upstream/"
        echo ""
        read -p "是否继续配置？(yes/no): " continue_config
        if [[ "$continue_config" != "yes" ]]; then
            return 1
        fi
        return 0
    fi

    # 2. 尝试 HTTP 请求
    local http_code=$(curl -o /dev/null -s -w "%{http_code}" --max-time 5 "$scheme://$upstream/" 2>/dev/null || echo "000")

    if [[ "$http_code" == "000" ]]; then
        warn "后端 HTTP 请求失败"
        echo ""
        echo "━━━ 诊断信息 ━━━"
        echo "后端地址：$scheme://$upstream"
        echo "TCP 连接：✓ 成功"
        echo "HTTP 请求：✗ 失败"
        echo ""
        echo "━━━ 可能原因 ━━━"
        echo "1. 服务未正确处理 HTTP 请求"
        echo "2. 服务启动中（端口监听但未就绪）"
        echo "3. SSL/TLS 配置错误（如使用了 https://）"
        echo "4. 需要特定的请求头或路径"
        echo ""
        read -p "是否继续配置？(yes/no): " continue_config
        if [[ "$continue_config" != "yes" ]]; then
            return 1
        fi
    elif [[ "$http_code" =~ ^[45] ]]; then
        warn "后端返回错误状态码：$http_code"
        info "这可能是正常的（取决于你的应用）"
    else
        log "后端健康检查通过（HTTP $http_code）"
    fi

    return 0
}

ensure_manager_dirs() {
    mkdir -p "$SITE_STATE_DIR" "$ACME_ROOT"
    chmod 755 "$MANAGER_ROOT" "$ACME_ROOT"
}

state_file() {
    printf '%s/%s.env\n' "$SITE_STATE_DIR" "$1"
}

site_conf_available() {
    printf '/etc/nginx/sites-available/%s-%s.conf\n' "$SITE_PREFIX" "$1"
}

site_conf_enabled() {
    printf '/etc/nginx/sites-enabled/%s-%s.conf\n' "$SITE_PREFIX" "$1"
}

load_state() {
    local file
    file="$(state_file "$1")"
    [[ -f "$file" ]] || die "未找到站点状态文件：$file"
    # shellcheck disable=SC1090
    . "$file"

    # 为旧状态文件设置默认值（兼容性）
    AUTO_RENEW="${AUTO_RENEW:-1}"
}

save_state() {
    local file
    file="$(state_file "$DOMAIN")"
    cat > "$file" <<EOF
DOMAIN=$DOMAIN
UPSTREAM=$UPSTREAM
UPSTREAM_SCHEME=$UPSTREAM_SCHEME
ENABLE_SSL=$ENABLE_SSL
CERTBOT_EMAIL=$CERTBOT_EMAIL
CLIENT_MAX_BODY_SIZE=$CLIENT_MAX_BODY_SIZE
PROXY_READ_TIMEOUT=$PROXY_READ_TIMEOUT
PROXY_SEND_TIMEOUT=$PROXY_SEND_TIMEOUT
WEBSOCKET=$WEBSOCKET
BACKEND_INSECURE=$BACKEND_INSECURE
AUTO_RENEW=$AUTO_RENEW
EOF
    chmod 600 "$file"
}

render_site_config() {
    local conf=""
    local backend_tls_block=""
    local websocket_block=""

    if [[ "$UPSTREAM_SCHEME" == "https" ]]; then
        backend_tls_block+="        proxy_ssl_server_name on;\n"
        if [[ "$BACKEND_INSECURE" == "1" ]]; then
            backend_tls_block+="        proxy_ssl_verify off;\n"
        fi
    fi

    if [[ "$WEBSOCKET" == "1" ]]; then
        websocket_block+="        proxy_set_header Upgrade \$http_upgrade;\n"
        websocket_block+="        proxy_set_header Connection \"upgrade\";\n"
    fi

    conf+="# Managed by $APP_LABEL\n"
    conf+="server {\n"
    conf+="    listen 80;\n"
    conf+="    listen [::]:80;\n"
    conf+="    server_name $DOMAIN;\n\n"
    conf+="    location ^~ /.well-known/acme-challenge/ {\n"
    conf+="        root $ACME_ROOT;\n"
    conf+="        default_type text/plain;\n"
    conf+="        try_files \$uri =404;\n"
    conf+="    }\n\n"

    if [[ "$ENABLE_SSL" == "1" ]]; then
        conf+="    location / {\n"
        conf+="        return 301 https://\$host\$request_uri;\n"
        conf+="    }\n"
        conf+="}\n\n"
        conf+="server {\n"
        conf+="    listen 443 ssl http2;\n"
        conf+="    listen [::]:443 ssl http2;\n"
        conf+="    server_name $DOMAIN;\n\n"
        conf+="    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;\n"
        conf+="    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;\n"
        conf+="    ssl_protocols TLSv1.2 TLSv1.3;\n"
        conf+="    ssl_prefer_server_ciphers on;\n\n"
        conf+="    location / {\n"
    else
        conf+="    location / {\n"
    fi

    conf+="        proxy_pass $UPSTREAM_SCHEME://$UPSTREAM;\n"
    conf+="        proxy_http_version 1.1;\n"
    conf+="$websocket_block"
    conf+="        proxy_set_header Host \$host;\n"
    conf+="        proxy_set_header X-Real-IP \$remote_addr;\n"
    conf+="        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;\n"
    conf+="        proxy_set_header X-Forwarded-Proto \$scheme;\n"
    conf+="        proxy_set_header X-Forwarded-Host \$host;\n"
    conf+="        proxy_set_header X-Forwarded-Port \$server_port;\n"
    conf+="        proxy_read_timeout $PROXY_READ_TIMEOUT;\n"
    conf+="        proxy_send_timeout $PROXY_SEND_TIMEOUT;\n"
    conf+="        client_max_body_size $CLIENT_MAX_BODY_SIZE;\n"
    conf+="$backend_tls_block"
    conf+="    }\n"
    conf+="}\n"

    printf '%b' "$conf"
}

install_conf_file() {
    local available enabled tmp
    available="$(site_conf_available "$DOMAIN")"
    enabled="$(site_conf_enabled "$DOMAIN")"
    tmp="$(mktemp /tmp/vpspm.XXXXXX.conf)"

    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
    render_site_config > "$tmp"
    install -m 0644 "$tmp" "$available"
    rm -f "$tmp"

    ln -sfn "$available" "$enabled"
}

validate_and_reload() {
    nginx -t >/dev/null 2>&1 || {
        nginx -t
        return 1
    }
    systemctl reload nginx >/dev/null 2>&1 || nginx -s reload
}

apply_site() {
    local available enabled backup_available="" backup_enabled_target=""
    available="$(site_conf_available "$DOMAIN")"
    enabled="$(site_conf_enabled "$DOMAIN")"

    if [[ -f "$available" ]]; then
        backup_available="$(mktemp /tmp/vpspm-backup.XXXXXX.conf)"
        cp "$available" "$backup_available"
    fi
    if [[ -L "$enabled" ]]; then
        backup_enabled_target="$(readlink "$enabled")"
    fi

    install_conf_file

    if validate_and_reload; then
        [[ -n "$backup_available" ]] && rm -f "$backup_available"
        log "nginx 配置已应用：$DOMAIN"
        return 0
    fi

    warn "新配置未通过校验，正在回滚：$DOMAIN"
    if [[ -n "$backup_available" ]]; then
        cp "$backup_available" "$available"
        rm -f "$backup_available"
    else
        rm -f "$available"
    fi

    if [[ -n "$backup_enabled_target" ]]; then
        ln -sfn "$backup_enabled_target" "$enabled"
    else
        rm -f "$enabled"
    fi

    validate_and_reload || true
    return 1
}

issue_cert() {
    local email_args=()
    local force_renew=0
    require_certbot

    # 完整的证书健康检查
    if [[ -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
        local cert_file="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
        local key_file="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
        local cert_issue=""

        # 1. 检查证书文件是否存在
        if [[ ! -f "$cert_file" ]]; then
            cert_issue="证书文件缺失"
        # 2. 检查私钥文件是否存在
        elif [[ ! -f "$key_file" ]]; then
            cert_issue="私钥文件缺失"
        # 3. 检查文件权限
        elif [[ ! -r "$cert_file" ]] || [[ ! -r "$key_file" ]]; then
            cert_issue="证书文件权限不足"
        # 4. 检查证书是否过期
        elif ! openssl x509 -in "$cert_file" -noout -checkend 0 >/dev/null 2>&1; then
            cert_issue="证书已过期或损坏"
        # 5. 检查证书域名是否匹配
        elif ! openssl x509 -in "$cert_file" -noout -text 2>/dev/null | grep -qE "(DNS:|CN=).*$DOMAIN"; then
            cert_issue="证书域名不匹配"
        # 6. 检查证书和私钥是否匹配
        else
            local cert_modulus=$(openssl x509 -noout -modulus -in "$cert_file" 2>/dev/null | openssl md5)
            local key_modulus=$(openssl rsa -noout -modulus -in "$key_file" 2>/dev/null | openssl md5)
            if [[ -n "$cert_modulus" ]] && [[ -n "$key_modulus" ]] && [[ "$cert_modulus" != "$key_modulus" ]]; then
                cert_issue="证书和私钥不匹配"
            fi
        fi

        if [[ -n "$cert_issue" ]]; then
            warn "证书检查失败：$cert_issue，将强制重新申请"
            force_renew=1
        else
            info "证书已存在且健康，跳过申请"
            return 0
        fi
    fi

    if [[ -n "$CERTBOT_EMAIL" ]]; then
        email_args=(--email "$CERTBOT_EMAIL")
    else
        email_args=(--register-unsafely-without-email)
    fi

    # 如果需要强制续期，添加 --force-renewal 参数
    local extra_args=()
    if [[ $force_renew -eq 1 ]]; then
        extra_args=(--force-renewal)
        warn "使用 --force-renewal 强制重新申请证书"
    fi

    # DNS 预检查
    info "正在检查 DNS 解析..."
    local server_ip=$(curl -s ifconfig.me 2>/dev/null || curl -s icanhazip.com 2>/dev/null || echo "未知")
    local dns_result=$(dig +short "$DOMAIN" @8.8.8.8 2>/dev/null | tail -1)

    if [[ -z "$dns_result" ]]; then
        error "DNS 解析失败"
        echo ""
        echo "━━━ 诊断信息 ━━━"
        echo "域名：$DOMAIN"
        echo "DNS 查询：无解析结果"
        echo ""
        echo "━━━ 可能原因 ━━━"
        echo "1. 域名 DNS 记录未配置"
        echo "2. DNS 记录刚添加，尚未生效（需等待 10-30 分钟）"
        echo "3. 域名服务商 DNS 服务器故障"
        echo ""
        echo "━━━ 解决方案 ━━━"
        echo "1. 检查域名服务商控制台的 DNS 记录"
        echo "2. 确认 A 记录指向：$server_ip"
        echo "3. 使用在线工具检查 DNS 传播：https://dnschecker.org"
        echo "4. 等待 DNS 生效后重试：host-nginx-manager enable-ssl $DOMAIN"
        echo ""
        return 1
    elif [[ "$dns_result" != "$server_ip" ]]; then
        warn "DNS 解析不匹配"
        echo ""
        echo "━━━ 诊断信息 ━━━"
        echo "域名：$DOMAIN"
        echo "期望 IP：$server_ip（本服务器）"
        echo "实际 IP：$dns_result"
        echo ""
        echo "━━━ 可能原因 ━━━"
        echo "1. DNS 记录指向了错误的服务器"
        echo "2. DNS 记录正在传播中（新旧值混合）"
        echo "3. 使用了 CDN 或负载均衡（可能正常）"
        echo ""
        echo "━━━ 解决方案 ━━━"
        echo "1. 如果使用 CDN，请忽略此警告"
        echo "2. 否则，请修改 DNS A 记录指向：$server_ip"
        echo "3. 等待 DNS 生效后重试"
        echo ""
        read -p "是否继续申请证书？(yes/no): " continue_cert
        if [[ "$continue_cert" != "yes" ]]; then
            info "已取消"
            return 1
        fi
    else
        log "DNS 解析正确：$DOMAIN → $dns_result"
    fi

    # 执行证书申请
    info "正在申请证书..."
    if ! certbot certonly \
        --webroot \
        -w "$ACME_ROOT" \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        "${email_args[@]}" \
        "${extra_args[@]}" 2>&1 | tee /tmp/certbot-error.log; then

        error "证书申请失败"
        echo ""
        echo "━━━ 错误详情 ━━━"
        grep -E "(Error|Failed|Unable|Invalid)" /tmp/certbot-error.log | head -5 || echo "查看完整日志：/tmp/certbot-error.log"
        echo ""
        echo "━━━ 常见原因与解决方案 ━━━"
        echo "1. 端口 80 未开放"
        echo "   解决：sudo ufw allow 80 或在云服务商安全组开放"
        echo ""
        echo "2. ACME 验证目录无权限"
        echo "   解决：sudo chmod 755 $ACME_ROOT"
        echo ""
        echo "3. 域名已有证书正在生效"
        echo "   解决：使用 --force-renewal 强制续期"
        echo ""
        echo "4. Let's Encrypt 速率限制"
        echo "   解决：每个域名每周最多 5 次失败，等待或更换域名"
        echo ""
        echo "5. DNS CAA 记录阻止"
        echo "   解决：检查 DNS CAA 记录是否允许 letsencrypt.org"
        echo ""
        echo "━━━ 排查命令 ━━━"
        echo "诊断站点：sudo host-nginx-manager diagnose $DOMAIN"
        echo "测试 HTTP：curl -I http://$DOMAIN/.well-known/acme-challenge/test"
        echo "查看日志：sudo tail -50 /var/log/letsencrypt/letsencrypt.log"
        echo ""
        rm -f /tmp/certbot-error.log
        return 1
    fi

    log "证书申请成功"
    return 0
}

parse_add_options() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --upstream-scheme)
                UPSTREAM_SCHEME="${2:-}"
                shift 2
                ;;
            --email)
                CERTBOT_EMAIL="${2:-}"
                shift 2
                ;;
            --no-ssl)
                ENABLE_SSL=0
                shift
                ;;
            --client-max-body-size)
                CLIENT_MAX_BODY_SIZE="${2:-}"
                shift 2
                ;;
            --proxy-read-timeout)
                PROXY_READ_TIMEOUT="${2:-}"
                shift 2
                ;;
            --proxy-send-timeout)
                PROXY_SEND_TIMEOUT="${2:-}"
                shift 2
                ;;
            --backend-insecure)
                BACKEND_INSECURE=1
                shift
                ;;
            --yes)
                YES_MODE=1
                shift
                ;;
            *)
                die "未知参数：$1"
                ;;
        esac
    done

    [[ "$UPSTREAM_SCHEME" == "http" || "$UPSTREAM_SCHEME" == "https" ]] || die "--upstream-scheme 只能是 http 或 https"
}

warn_if_domain_exists_elsewhere() {
    local existing=""
    existing="$(nginx -T 2>/dev/null | grep -E "server_name[[:space:]].*\b${DOMAIN}\b" | grep -v "$(site_conf_available "$DOMAIN")" || true)"
    if [[ -n "$existing" ]]; then
        warn "nginx 当前配置里已经出现域名 $DOMAIN。请确认没有重复站点。"
        printf '%s\n' "$existing"
    fi
}

cmd_add() {
    DOMAIN="$(normalize_domain "${1:-}")"
    UPSTREAM="${2:-}"
    shift 2 || true
    parse_add_options "$@"

    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    validate_upstream "$UPSTREAM" || die "UPSTREAM 必须是 HOST:PORT 格式，例如 127.0.0.1:3001"

    # 后端健康检查
    check_backend_health "$UPSTREAM" "$UPSTREAM_SCHEME" || die "后端检查失败，已取消"

    ensure_manager_dirs
    warn_if_domain_exists_elsewhere

    section "步骤 1/4: 创建 HTTP 站点配置"
    local initial_ssl="$ENABLE_SSL"
    ENABLE_SSL=0
    save_state
    apply_site || die "写入 HTTP 站点配置失败"
    log "HTTP 配置已创建"

    if [[ "$initial_ssl" == "1" ]]; then
        section "步骤 2/4: 申请 Let's Encrypt 证书"
        info "正在向 Let's Encrypt 申请证书，这可能需要 30-60 秒..."
        issue_cert || die "certbot 证书申请失败"
        log "证书申请成功"

        section "步骤 3/4: 启用 HTTPS 配置"
        ENABLE_SSL=1
        save_state
        apply_site || die "启用 HTTPS 配置失败"
        log "HTTPS 已启用"

        section "步骤 4/4: 配置自动续期"
        log "证书自动续期已启用（由 certbot.timer 管理）"
    else
        log "已跳过证书申请（使用 --no-ssl）"
    fi

    write_summary
}

cmd_update() {
    DOMAIN="$(normalize_domain "${1:-}")"
    local new_upstream="${2:-}"
    shift 2 || true

    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    validate_upstream "$new_upstream" || die "UPSTREAM 必须是 HOST:PORT 格式，例如 127.0.0.1:3001"
    load_state "$DOMAIN"
    [[ "${IMPORTED:-0}" != "1" ]] || die "该站点是从已有 nginx 配置导入的记录，尚未迁移为工具配置，不能直接编辑"

    # 后端健康检查
    check_backend_health "$new_upstream" "$UPSTREAM_SCHEME" || die "后端检查失败，已取消"

    UPSTREAM="$new_upstream"
    parse_add_options "$@"
    save_state
    apply_site || die "更新站点配置失败"
    write_summary
}

write_summary() {
    section "站点已就绪"
    printf '域名         : %s\n' "$DOMAIN"
    printf '后端         : %s://%s\n' "$UPSTREAM_SCHEME" "$UPSTREAM"
    printf 'HTTPS        : %s\n' "$([[ "$ENABLE_SSL" == "1" ]] && echo 已启用 || echo 未启用)"
    if [[ "$ENABLE_SSL" == "1" ]]; then
        printf '访问地址     : https://%s\n' "$DOMAIN"
    else
        printf '访问地址     : http://%s\n' "$DOMAIN"
    fi
    printf '状态文件     : %s\n' "$(state_file "$DOMAIN")"
    printf '配置文件     : %s\n' "$(site_conf_available "$DOMAIN")"
}

cmd_enable_ssl() {
    DOMAIN="$(normalize_domain "${1:-}")"
    shift || true
    local cli_email=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --email)
                cli_email="${2:-}"
                shift 2
                ;;
            --yes)
                YES_MODE=1
                shift
                ;;
            *)
                die "未知参数：$1"
                ;;
        esac
    done

    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    load_state "$DOMAIN"

    if [[ "$ENABLE_SSL" == "1" ]]; then
        warn "站点已启用 HTTPS，将重新确认证书和配置"
    fi

    if [[ -n "$cli_email" ]]; then
        CERTBOT_EMAIL="$cli_email"
    fi

    ENABLE_SSL=0
    save_state
    apply_site || die "更新 HTTP 站点失败"

    # 检查证书是否已存在
    if [[ -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
        info "检测到现有证书，将直接复用"

        # 修复续期配置文件中的 webroot_path（如果存在）
        local renewal_conf="/etc/letsencrypt/renewal/${DOMAIN}.conf"
        if [[ -f "$renewal_conf" ]]; then
            info "检查并修复续期配置..."
            # 替换错误的 webroot_path
            sed -i "s|^webroot_path = /var/www/.*|webroot_path = $ACME_ROOT|g" "$renewal_conf" 2>/dev/null || true
            # 替换 webroot_map 中的路径
            sed -i "s|^${DOMAIN} = /var/www/.*|${DOMAIN} = $ACME_ROOT|g" "$renewal_conf" 2>/dev/null || true
            log "续期配置已更新"
        fi
    else
        section "申请 Let's Encrypt 证书"
        issue_cert || die "certbot 证书申请失败"
    fi

    ENABLE_SSL=1
    save_state
    apply_site || die "启用 HTTPS 配置失败"
    write_summary
}

cmd_renew() {
    DOMAIN="$(normalize_domain "${1:-}")"
    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    load_state "$DOMAIN"
    [[ "$ENABLE_SSL" == "1" ]] || die "该站点当前未启用 HTTPS，无法续期证书"

    section "续期证书"

    # 使用 certbot renew 命令进行续期，而不是重新申请
    if [[ -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
        info "使用 certbot renew 续期现有证书"
        certbot renew --cert-name "$DOMAIN" --force-renewal --non-interactive || die "certbot 证书续期失败"
    else
        warn "证书目录不存在，将重新申请"
        issue_cert || die "certbot 证书申请失败"
    fi

    apply_site || die "续期后重新加载 HTTPS 配置失败"
    write_summary
}

cmd_disable_ssl() {
    DOMAIN="$(normalize_domain "${1:-}")"
    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    load_state "$DOMAIN"
    ENABLE_SSL=0
    save_state
    apply_site || die "关闭 HTTPS 失败"
    write_summary
}

cmd_rename() {
    local old_domain="$(normalize_domain "${1:-}")"
    local new_domain="$(normalize_domain "${2:-}")"
    local new_upstream=""
    local delete_old_cert=0

    shift 2 || die "用法：rename OLD_DOMAIN NEW_DOMAIN [--upstream UPSTREAM] [--delete-old-cert]"

    # 解析可选参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --upstream)
                new_upstream="${2:-}"
                shift 2
                ;;
            --delete-old-cert)
                delete_old_cert=1
                shift
                ;;
            *)
                die "未知参数：$1"
                ;;
        esac
    done

    validate_domain "$old_domain" || die "无效的旧域名：$old_domain"
    validate_domain "$new_domain" || die "无效的新域名：$new_domain"

    # 检查旧域名是否存在
    local old_state_file="$(state_file "$old_domain")"
    [[ -f "$old_state_file" ]] || die "旧域名不存在：$old_domain"

    # 检查新域名是否已存在
    local new_state_file="$(state_file "$new_domain")"
    if [[ -f "$new_state_file" ]]; then
        die "新域名已存在：$new_domain（请先删除或选择其他域名）"
    fi

    section "重命名站点：$old_domain → $new_domain"

    # 1. 读取旧配置
    info "读取旧配置..."
    load_state "$old_domain"
    local old_upstream="$UPSTREAM"
    local old_upstream_scheme="$UPSTREAM_SCHEME"
    local old_enable_ssl="$ENABLE_SSL"
    local old_certbot_email="$CERTBOT_EMAIL"
    local old_client_max_body_size="$CLIENT_MAX_BODY_SIZE"
    local old_proxy_read_timeout="$PROXY_READ_TIMEOUT"
    local old_proxy_send_timeout="$PROXY_SEND_TIMEOUT"
    local old_websocket="$WEBSOCKET"
    local old_backend_insecure="$BACKEND_INSECURE"
    local old_auto_renew="$AUTO_RENEW"

    info "  后端：$old_upstream_scheme://$old_upstream"
    info "  HTTPS：$old_enable_ssl"

    # 2. 使用新域名创建站点（保留所有配置）
    section "创建新站点：$new_domain"
    DOMAIN="$new_domain"
    UPSTREAM="${new_upstream:-$old_upstream}"
    UPSTREAM_SCHEME="$old_upstream_scheme"
    ENABLE_SSL=0  # 先创建 HTTP 站点
    CERTBOT_EMAIL="$old_certbot_email"
    CLIENT_MAX_BODY_SIZE="$old_client_max_body_size"
    PROXY_READ_TIMEOUT="$old_proxy_read_timeout"
    PROXY_SEND_TIMEOUT="$old_proxy_send_timeout"
    WEBSOCKET="$old_websocket"
    BACKEND_INSECURE="$old_backend_insecure"
    AUTO_RENEW="$old_auto_renew"

    save_state
    apply_site || die "创建新站点配置失败"
    log "HTTP 配置已创建"

    # 3. 如果旧站点启用了 HTTPS，为新站点申请证书
    if [[ "$old_enable_ssl" == "1" ]]; then
        section "为新域名申请证书"
        issue_cert || die "证书申请失败"

        ENABLE_SSL=1
        save_state
        apply_site || die "启用 HTTPS 配置失败"
        log "HTTPS 已启用"
    fi

    # 4. 删除旧站点配置
    section "删除旧站点：$old_domain"
    local old_conf="/etc/nginx/sites-available/${SITE_PREFIX}-${old_domain}.conf"
    local old_link="/etc/nginx/sites-enabled/${SITE_PREFIX}-${old_domain}.conf"

    if [[ -L "$old_link" ]]; then
        rm -f "$old_link"
        log "已删除旧软链接"
    fi

    if [[ -f "$old_conf" ]]; then
        rm -f "$old_conf"
        log "已删除旧配置文件"
    fi

    if [[ -f "$old_state_file" ]]; then
        rm -f "$old_state_file"
        log "已删除旧状态文件"
    fi

    # 5. 删除旧证书（可选）
    if [[ "$delete_old_cert" == "1" && "$old_enable_ssl" == "1" ]]; then
        section "删除旧证书：$old_domain"
        if certbot delete --cert-name "$old_domain" --non-interactive 2>/dev/null; then
            log "已删除旧证书"
        else
            warn "删除旧证书失败（可能已不存在）"
        fi
    elif [[ "$old_enable_ssl" == "1" ]]; then
        info "旧证书已保留：/etc/letsencrypt/live/$old_domain"
    fi

    # 6. 重载 nginx
    section "重载 nginx"
    if nginx -t 2>&1 | grep -q "test is successful"; then
        systemctl reload nginx || nginx -s reload
        log "nginx 已重载"
    else
        error "nginx 配置测试失败"
        nginx -t
        return 1
    fi

    section "重命名完成"
    log "旧域名：$old_domain"
    log "新域名：$new_domain"
    log "后端：${new_upstream:-$old_upstream}"
    write_summary
}

cmd_remove() {
    DOMAIN="$(normalize_domain "${1:-}")"
    shift || true
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --yes)
                YES_MODE=1
                shift
                ;;
            --delete-cert)
                DELETE_CERT=1
                shift
                ;;
            *)
                die "未知参数：$1"
                ;;
        esac
    done

    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    load_state "$DOMAIN"

    confirm "确认删除站点 $DOMAIN 吗？" || exit 0

    # 删除 nginx 配置文件和状态文件
    rm -f "$(site_conf_enabled "$DOMAIN")" "$(site_conf_available "$DOMAIN")" "$(state_file "$DOMAIN")"
    validate_and_reload || die "删除站点后 nginx 校验失败，请手动检查"

    # 彻底删除证书（包括 certbot 记录和证书文件）
    if [[ $DELETE_CERT -eq 1 ]] && command_exists certbot; then
        section "彻底清理证书"

        # 1. 删除 certbot 证书记录
        certbot delete --cert-name "$DOMAIN" --non-interactive 2>/dev/null || true

        # 2. 强制删除证书目录（防止残留）
        local cert_dirs=(
            "/etc/letsencrypt/live/$DOMAIN"
            "/etc/letsencrypt/archive/$DOMAIN"
            "/etc/letsencrypt/renewal/$DOMAIN.conf"
        )
        for dir in "${cert_dirs[@]}"; do
            if [[ -e "$dir" ]]; then
                rm -rf "$dir"
                info "已删除：$dir"
            fi
        done

        log "证书已彻底清理：$DOMAIN"
    fi

    log "站点已删除：$DOMAIN"
}

cmd_list() {
    ensure_manager_dirs
    section "受管站点列表"
    if ! find "$SITE_STATE_DIR" -maxdepth 1 -name '*.env' | grep -q .; then
        info "暂无受管站点"
        return 0
    fi

    find "$SITE_STATE_DIR" -maxdepth 1 -name '*.env' | sort | while read -r file; do
        # shellcheck disable=SC1090
        . "$file"
        printf '%-30s %-8s %-24s %s\n' "$DOMAIN" "$([[ "$ENABLE_SSL" == "1" ]] && echo HTTPS || echo HTTP)" "$UPSTREAM_SCHEME://$UPSTREAM" "$(basename "$file")"
    done
}

cmd_show() {
    DOMAIN="$(normalize_domain "${1:-}")"
    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    load_state "$DOMAIN"
    section "站点详情"
    printf '域名              : %s\n' "$DOMAIN"
    printf '后端              : %s://%s\n' "$UPSTREAM_SCHEME" "$UPSTREAM"
    printf 'HTTPS             : %s\n' "$([[ "$ENABLE_SSL" == "1" ]] && echo 已启用 || echo 未启用)"
    printf '邮箱              : %s\n' "${CERTBOT_EMAIL:-未设置}"
    printf '上传大小          : %s\n' "$CLIENT_MAX_BODY_SIZE"
    printf '读取超时          : %s\n' "$PROXY_READ_TIMEOUT"
    printf '发送超时          : %s\n' "$PROXY_SEND_TIMEOUT"
    printf '后端证书校验      : %s\n' "$([[ "$BACKEND_INSECURE" == "1" ]] && echo 已关闭 || echo 已开启)"
    printf '状态文件          : %s\n' "$(state_file "$DOMAIN")"
    printf '配置文件          : %s\n' "$(site_conf_available "$DOMAIN")"
}

cmd_test() {
    section "测试 nginx 配置"
    nginx -t
}

cmd_reload() {
    section "重载 nginx"
    validate_and_reload || die "nginx 重载失败"
    log "nginx 已重载"
}

cmd_set_auto_renew() {
    DOMAIN="$(normalize_domain "${1:-}")"
    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"
    local enable="${2:-1}"

    load_state "$DOMAIN"
    AUTO_RENEW="$enable"
    save_state

    if [[ "$enable" == "1" ]]; then
        log "已启用自动续期：$DOMAIN"
    else
        log "已禁用自动续期：$DOMAIN"
    fi
}

cmd_diagnose() {
    DOMAIN="$(normalize_domain "${1:-}")"
    validate_domain "$DOMAIN" || die "无效域名：$DOMAIN"

    section "诊断站点：$DOMAIN"

    # 1. 检查状态文件
    local state_file="$(state_file "$DOMAIN")"
    if [[ -f "$state_file" ]]; then
        log "✓ 状态文件存在"
        load_state "$DOMAIN"
        info "  后端：$UPSTREAM_SCHEME://$UPSTREAM"
        info "  HTTPS：$ENABLE_SSL"
        info "  自动续期：$AUTO_RENEW"
    else
        error "✗ 状态文件不存在：$state_file"
        return 1
    fi

    # 2. 检查配置文件
    local conf_file="/etc/nginx/sites-available/${SITE_PREFIX}-${DOMAIN}.conf"
    local conf_link="/etc/nginx/sites-enabled/${SITE_PREFIX}-${DOMAIN}.conf"

    if [[ -f "$conf_file" ]]; then
        # 检查是否被注释
        local active_lines=$(grep -c "^[^#]*server_name $DOMAIN" "$conf_file" 2>/dev/null || echo "0")
        local commented_lines=$(grep -c "^#.*server_name $DOMAIN" "$conf_file" 2>/dev/null || echo "0")

        if [[ "$active_lines" -gt 0 ]]; then
            log "✓ 配置文件正常（$active_lines 个 server 块）"
        elif [[ "$commented_lines" -gt 0 ]]; then
            warn "⚠ 配置被注释（$commented_lines 个 server 块）"
            warn "  修复：host-nginx-manager update $DOMAIN $UPSTREAM"
        else
            warn "⚠ 配置异常：未找到 server_name"
        fi
    else
        error "✗ 配置文件不存在：$conf_file"
        warn "  修复：host-nginx-manager update $DOMAIN $UPSTREAM"
    fi

    if [[ -L "$conf_link" ]]; then
        log "✓ 软链接正常"
    else
        warn "⚠ 软链接不存在"
    fi

    # 3. 检查证书
    if [[ "$ENABLE_SSL" == "1" ]]; then
        local cert_dir="/etc/letsencrypt/live/$DOMAIN"
        if [[ -d "$cert_dir" ]]; then
            log "✓ 证书目录存在"

            # 检查续期配置
            local renewal_conf="/etc/letsencrypt/renewal/${DOMAIN}.conf"
            if [[ -f "$renewal_conf" ]]; then
                local webroot_path=$(grep "^webroot_path" "$renewal_conf" | cut -d= -f2 | xargs)
                if [[ "$webroot_path" == "$ACME_ROOT" ]]; then
                    log "✓ 续期配置正确"
                else
                    warn "⚠ webroot_path 错误：$webroot_path"
                    warn "  应该是：$ACME_ROOT"
                    warn "  修复：host-nginx-manager enable-ssl $DOMAIN"
                fi
            else
                warn "⚠ 续期配置不存在"
            fi
        else
            warn "⚠ 证书目录不存在"
        fi
    fi

    # 4. 测试 nginx 配置
    if nginx -t 2>&1 | grep -q "test is successful"; then
        log "✓ nginx 配置测试通过"
    else
        error "✗ nginx 配置测试失败"
        nginx -t
        return 1
    fi

    log "诊断完成"
}

cmd_backup() {
    local output_file=""

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output)
                output_file="${2:-}"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    # 默认备份文件名
    if [[ -z "$output_file" ]]; then
        mkdir -p "$BACKUP_DIR"
        output_file="$BACKUP_DIR/backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    fi

    section "创建配置备份"

    # 检查要备份的目录
    local items_to_backup=()

    if [[ -d "$SITE_STATE_DIR" ]]; then
        items_to_backup+=("$SITE_STATE_DIR")
        log "✓ 状态文件"
    fi

    if [[ -d "/etc/nginx/sites-available" ]]; then
        items_to_backup+=("/etc/nginx/sites-available")
        log "✓ Nginx 配置"
    fi

    if [[ -d "/etc/letsencrypt" ]]; then
        items_to_backup+=("/etc/letsencrypt")
        log "✓ SSL 证书"
    fi

    if [[ ${#items_to_backup[@]} -eq 0 ]]; then
        die "没有可备份的内容"
    fi

    # 创建备份
    info "正在打包..."
    tar czf "$output_file" "${items_to_backup[@]}" 2>/dev/null || die "备份失败"

    local size=$(du -h "$output_file" | cut -f1)
    log "备份完成"
    log "文件：$output_file"
    log "大小：$size"
}

cmd_restore() {
    local backup_file="${1:-}"
    [[ -n "$backup_file" ]] || die "用法：restore BACKUP_FILE"
    [[ -f "$backup_file" ]] || die "备份文件不存在：$backup_file"

    section "恢复配置备份"

    warn "⚠️  警告：此操作将覆盖当前配置"
    warn "建议先创建当前配置的备份"

    read -p "确认恢复？(yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        info "已取消"
        return 0
    fi

    # 创建当前配置的备份
    local auto_backup="$BACKUP_DIR/auto-backup-before-restore-$(date +%Y%m%d-%H%M%S).tar.gz"
    mkdir -p "$BACKUP_DIR"
    info "正在备份当前配置..."
    tar czf "$auto_backup" "$SITE_STATE_DIR" /etc/nginx/sites-available /etc/letsencrypt 2>/dev/null || true
    log "当前配置已备份到：$auto_backup"

    # 恢复备份
    info "正在恢复备份..."
    tar xzf "$backup_file" -C / 2>/dev/null || die "恢复失败"

    # 重载 nginx
    info "正在重载 nginx..."
    if nginx -t 2>&1 | grep -q "test is successful"; then
        systemctl reload nginx || nginx -s reload
        log "恢复完成"
    else
        error "nginx 配置测试失败"
        warn "正在回滚..."
        tar xzf "$auto_backup" -C / 2>/dev/null
        systemctl reload nginx || nginx -s reload
        die "恢复失败，已回滚到之前的配置"
    fi
}

cmd_list_backups() {
    section "配置备份列表"

    if [[ ! -d "$BACKUP_DIR" ]] || [[ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]]; then
        info "暂无备份"
        return 0
    fi

    local backups=($(ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null))

    printf "%-30s %10s %20s\n" "文件名" "大小" "创建时间"
    printf "%-30s %10s %20s\n" "----" "----" "----"

    for backup in "${backups[@]}"; do
        local name=$(basename "$backup")
        local size=$(du -h "$backup" | cut -f1)
        local time=$(stat -c %y "$backup" 2>/dev/null | cut -d. -f1 || stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$backup")
        printf "%-30s %10s %20s\n" "$name" "$size" "$time"
    done

    echo ""
    info "备份目录：$BACKUP_DIR"
    info "总数：${#backups[@]}"
}

cmd_health_check() {
    local target_domain="${1:-}"

    section "健康检查"

    # 如果指定了域名，只检查单个站点
    if [[ -n "$target_domain" ]]; then
        target_domain="$(normalize_domain "$target_domain")"
        validate_domain "$target_domain" || die "无效域名：$target_domain"

        local state_file="$(state_file "$target_domain")"
        [[ -f "$state_file" ]] || die "站点不存在：$target_domain"

        check_single_site "$target_domain"
        return 0
    fi

    # 检查所有站点
    local state_files=("$SITE_STATE_DIR"/*.env)
    if [[ ! -e "${state_files[0]}" ]]; then
        info "暂无受管站点"
        return 0
    fi

    local total=0
    local healthy=0
    local warnings=0
    local errors=0

    for state_file in "${state_files[@]}"; do
        local domain=$(basename "$state_file" .env)
        ((total++))

        echo ""
        echo "━━━ [$total] $domain ━━━"

        local status=$(check_single_site "$domain" 2>&1)
        echo "$status"

        if echo "$status" | grep -q "✓ 所有检查通过"; then
            ((healthy++))
        elif echo "$status" | grep -q "✗"; then
            ((errors++))
        else
            ((warnings++))
        fi
    done

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "总计：$total 个站点"
    echo "健康：$healthy"
    echo "警告：$warnings"
    echo "错误：$errors"
}

check_single_site() {
    local domain="$1"
    load_state "$domain"

    local has_error=0
    local has_warning=0

    # 1. 检查后端连通性
    local host="${UPSTREAM%:*}"
    local port="${UPSTREAM##*:}"

    if timeout 2 bash -c "echo >/dev/tcp/$host/$port" 2>/dev/null; then
        log "✓ 后端连接正常 ($UPSTREAM_SCHEME://$UPSTREAM)"

        # 尝试 HTTP 请求
        local http_code=$(curl -o /dev/null -s -w "%{http_code}" --max-time 3 "$UPSTREAM_SCHEME://$UPSTREAM/" 2>/dev/null || echo "000")
        if [[ "$http_code" == "000" ]]; then
            warn "⚠ 后端 HTTP 请求失败"
            has_warning=1
        elif [[ "$http_code" =~ ^[45] ]]; then
            info "  HTTP 状态码：$http_code"
        else
            log "  HTTP 状态码：$http_code"
        fi
    else
        error "✗ 后端连接失败 ($UPSTREAM_SCHEME://$UPSTREAM)"
        has_error=1
    fi

    # 2. 检查 DNS 解析
    local dns_result=$(dig +short "$domain" @8.8.8.8 2>/dev/null | tail -1)
    if [[ -z "$dns_result" ]]; then
        error "✗ DNS 解析失败"
        has_error=1
    else
        local server_ip=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || echo "未知")
        if [[ "$dns_result" == "$server_ip" ]]; then
            log "✓ DNS 解析正确 ($domain → $dns_result)"
        else
            warn "⚠ DNS 指向不同 IP：$dns_result（本机：$server_ip）"
            has_warning=1
        fi
    fi

    # 3. 检查证书
    if [[ "$ENABLE_SSL" == "1" ]]; then
        local cert_file="/etc/letsencrypt/live/$domain/fullchain.pem"
        if [[ -f "$cert_file" ]]; then
            if openssl x509 -in "$cert_file" -noout -checkend 0 >/dev/null 2>&1; then
                local expiry=$(openssl x509 -in "$cert_file" -noout -enddate 2>/dev/null | cut -d= -f2)
                local expiry_epoch=$(date -d "$expiry" +%s 2>/dev/null || echo 0)
                local now_epoch=$(date +%s)
                local days_left=$(( ($expiry_epoch - $now_epoch) / 86400 ))

                if [[ $days_left -le 7 ]]; then
                    error "✗ 证书即将过期（剩余 $days_left 天）"
                    has_error=1
                elif [[ $days_left -le 30 ]]; then
                    warn "⚠ 证书将在 $days_left 天后过期"
                    has_warning=1
                else
                    log "✓ 证书有效（剩余 $days_left 天）"
                fi
            else
                error "✗ 证书已过期或损坏"
                has_error=1
            fi
        else
            error "✗ 证书文件不存在"
            has_error=1
        fi
    else
        info "  未启用 HTTPS"
    fi

    # 4. 检查 Nginx 配置
    local conf_file="$(site_conf_available "$domain")"
    if [[ -f "$conf_file" ]]; then
        log "✓ Nginx 配置存在"
    else
        error "✗ Nginx 配置文件丢失"
        has_error=1
    fi

    # 5. 总结
    if [[ $has_error -eq 1 ]]; then
        return 1
    elif [[ $has_warning -eq 1 ]]; then
        return 2
    else
        log "✓ 所有检查通过"
        return 0
    fi
}

main() {
    case "$COMMAND" in
        help|-h|--help)
            usage
            return 0
            ;;
    esac

    require_root
    require_linux
    require_nginx
    ensure_manager_dirs

    case "$COMMAND" in
        add)
            [[ $# -ge 2 ]] || die "用法：add DOMAIN UPSTREAM [选项]"
            cmd_add "$@"
            ;;
        update)
            [[ $# -ge 2 ]] || die "用法：update DOMAIN UPSTREAM [选项]"
            cmd_update "$@"
            ;;
        enable-ssl)
            [[ $# -ge 1 ]] || die "用法：enable-ssl DOMAIN [--email EMAIL]"
            cmd_enable_ssl "$@"
            ;;
        renew)
            [[ $# -eq 1 ]] || die "用法：renew DOMAIN"
            cmd_renew "$1"
            ;;
        disable-ssl)
            [[ $# -eq 1 ]] || die "用法：disable-ssl DOMAIN"
            cmd_disable_ssl "$1"
            ;;
        rename)
            [[ $# -ge 2 ]] || die "用法：rename OLD_DOMAIN NEW_DOMAIN [--upstream UPSTREAM] [--delete-old-cert]"
            cmd_rename "$@"
            ;;
        remove)
            [[ $# -ge 1 ]] || die "用法：remove DOMAIN [--yes] [--delete-cert]"
            cmd_remove "$@"
            ;;
        list)
            cmd_list
            ;;
        show)
            [[ $# -eq 1 ]] || die "用法：show DOMAIN"
            cmd_show "$1"
            ;;
        test)
            cmd_test
            ;;
        reload)
            cmd_reload
            ;;
        set-auto-renew)
            [[ $# -ge 1 ]] || die "用法：set-auto-renew DOMAIN [0|1]"
            cmd_set_auto_renew "$@"
            ;;
        diagnose)
            [[ $# -ge 1 ]] || die "用法：diagnose DOMAIN"
            cmd_diagnose "$1"
            ;;
        health-check)
            cmd_health_check "$@"
            ;;
        backup)
            cmd_backup "$@"
            ;;
        restore)
            [[ $# -ge 1 ]] || die "用法：restore BACKUP_FILE"
            cmd_restore "$1"
            ;;
        list-backups)
            cmd_list_backups
            ;;

        *)
            die "未知命令：$COMMAND"
            ;;
    esac
}

main "$@"
