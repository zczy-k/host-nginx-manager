#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

SCRIPT_VERSION="1.0.0"
APP_LABEL="VPS Nginx 代理管理器"
MANAGER_ROOT="/etc/nginx/vps-proxy-manager"
SITE_STATE_DIR="$MANAGER_ROOT/sites"
ACME_ROOT="$MANAGER_ROOT/acme"
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
  host-nginx-manager.sh set-auto-renew DOMAIN [0|1]
  host-nginx-manager.sh remove DOMAIN [--yes] [--delete-cert]
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

    certbot certonly \
        --webroot \
        -w "$ACME_ROOT" \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        "${email_args[@]}" \
        "${extra_args[@]}"
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

        *)
            die "未知命令：$COMMAND"
            ;;
    esac
}

main "$@"
