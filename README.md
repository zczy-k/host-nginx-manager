# Host Nginx Manager

Host Nginx Manager 是给“已有宿主 nginx，且不再使用 Nginx Proxy Manager”的 VPS 场景准备的轻量管理工具。

它有两部分：

- `host-nginx-manager.sh`：命令行管理器
- `web/host_nginx_web.py`：低资源 Web 管理面板

它只管理标准 HTTP/HTTPS 反向代理站点，不会自动改动你当前已经手写的：

- `stream` / `ssl_preread`
- Rathole SNI 转发
- 假证书拦截
- 直接 IP 访问 `444` 拦截
- 其他手工放在 `/etc/nginx/nginx.conf` 里的全局规则

## 适合的 VPS 架构

从当前 VPS 审计结果看，这台机器适合继续使用宿主 nginx：

- `80` 和 `443` 已由系统 nginx 监听
- `8443`、`54443` 当前用于 `stream` / Rathole SNI 转发
- `3001` 当前有 Node 服务监听，适合通过 nginx 子域名反代
- Certbot 已存在，并已有 `metapi.cni.de5.net` 证书
- 防火墙当前未启用，安全边界主要依赖云厂商安全组和 nginx 规则

普通 Web/API 服务建议统一挂到 `443`，例如：

- `metapi.cni.de5.net -> 127.0.0.1:3001`
- `api.example.com -> 127.0.0.1:3002`

特殊的 `stream` / Rathole 入口继续手工维护，不建议交给这个工具管理。

## 安装 Web 管理面板

推荐安装 Web 面板。它默认监听 `0.0.0.0:8098`，可通过服务器公网 IP 访问。

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
```

安装完成后脚本会输出：

- 管理地址
- 管理密码
- 公网访问地址

确认云安全组/防火墙放行 `8098/tcp` 后，浏览器打开：

```text
http://你的服务器IP:8098
```

如果你想改回只允许 SSH 隧道访问，可以在安装时设置 `HNG_WEB_BIND=127.0.0.1`。

管理密码保存在 VPS：

```bash
sudo cat /etc/host-nginx-manager/web.env
```

## Web 面板功能

当前 Web 面板支持：

- 查看 nginx 状态和站点统计
- 查看受管站点、问题汇总
- 新增标准 HTTP/HTTPS 反向代理
- 申请并启用 Let's Encrypt HTTPS
- **查看证书详情**（颁发者、有效期、SAN等）
- **手动续期证书**
- 关闭站点 HTTPS
- 删除受管站点
- 导入和迁移现有配置
- 测试 nginx 配置
- 重载 nginx
- **应用内帮助文档**
- **密码管理**（修改密码、重置密码）
- **双因素认证（2FA）**
- **Cookie Secure 模式**（HTTPS 环境）

Web 面板本身不管理 `stream`、Rathole、`ssl_preread`。

## 安全特性

- ✅ 强密码策略（12位+复杂度要求）
- ✅ PBKDF2 密码哈希存储
- ✅ 双因素认证（TOTP）
- ✅ 会话管理（30分钟超时）
- ✅ 登录限流（5次失败锁定5分钟）
- ✅ API 速率限制（每分钟60次）
- ✅ 输入验证（防命令注入、路径遍历）
- ✅ 安全 Cookie（HttpOnly、SameSite）

### HTTPS 部署（推荐）

如果通过 HTTPS 反向代理访问管理界面，可启用 Cookie Secure 模式：

**安装时选择**：安装过程中会询问是否启用

**后续切换**：
```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | bash
# 选择 3) 切换 Cookie Secure 模式
```

### 密码管理

**修改密码**：登录后在"账户设置"中修改

**忘记密码重置**：
```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | bash
# 选择 2) 重置登录密码
```

## 证书管理

### 自动续期（推荐配置）

Let's Encrypt 证书有效期90天，需要自动续期。

**1. 配置自动续期（一次性设置）**

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/setup-auto-renew.sh | sudo bash
```

这会：
- 创建证书续期钩子，续期成功后自动重载nginx
- 确保certbot定时任务已启用（每天检查2次）
- 测试续期流程

**2. 验证自动续期**

```bash
# 检查定时任务
sudo systemctl list-timers | grep certbot

# 测试续期（不会真正续期）
sudo certbot renew --dry-run
```

**3. 查看续期日志**

```bash
sudo tail -f /var/log/letsencrypt/letsencrypt.log
```

### 手动续期

**Web界面：**
1. 进入"证书"视图
2. 找到需要续期的域名
3. 点击"查看详情"查看证书信息
4. 点击"续期"按钮手动续期

**命令行：**
```bash
# 续期单个域名
sudo host-nginx-manager renew domain.com

# 强制续期所有证书
sudo certbot renew --force-renewal
```

### 证书健康监控

Web面板会自动检测证书状态：
- 🟢 **正常**：剩余30天以上
- 🟡 **预警**：剩余7-30天
- 🔴 **紧急**：剩余7天以内

在"问题"视图中会显示所有证书异常。

## 安装 CLI 管理器

如果只想使用命令行：

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/host-nginx-manager.sh -o /usr/local/sbin/host-nginx-manager
sudo chmod +x /usr/local/sbin/host-nginx-manager
```

查看帮助：

```bash
host-nginx-manager help
```

## 新增普通反向代理

如果后端是本机 `3001` 端口：

```bash
sudo host-nginx-manager add metapi.cni.de5.net 127.0.0.1:3001 --email you@example.com
```

这会：

- 写入 `/etc/nginx/sites-available/vpspm-metapi.cni.de5.net.conf`
- 创建 `/etc/nginx/sites-enabled/vpspm-metapi.cni.de5.net.conf` 链接
- 先创建 HTTP 站点用于 ACME 验证
- 使用 `certbot certonly --webroot` 申请证书
- 启用 HTTPS 并将 HTTP 跳转到 HTTPS
- `nginx -t` 通过后才 reload nginx
- 如果新配置失败，会回滚旧配置

## 只创建 HTTP，不立即申请证书

```bash
sudo host-nginx-manager add metapi.cni.de5.net 127.0.0.1:3001 --no-ssl
```

稍后再启用 HTTPS：

```bash
sudo host-nginx-manager enable-ssl metapi.cni.de5.net --email you@example.com
```

## 后端是自签 HTTPS

例如后端是 `https://127.0.0.1:58000`，且证书不可被公网 CA 验证：

```bash
sudo host-nginx-manager add nas.example.com 127.0.0.1:58000 \
  --upstream-scheme https \
  --backend-insecure \
  --client-max-body-size 0 \
  --email you@example.com
```

## 查看和维护

```bash
sudo host-nginx-manager list
sudo host-nginx-manager show metapi.cni.de5.net
sudo host-nginx-manager test
sudo host-nginx-manager reload
```

删除站点：

```bash
sudo host-nginx-manager remove metapi.cni.de5.net --yes
```

删除站点并尝试删除证书：

```bash
sudo host-nginx-manager remove metapi.cni.de5.net --delete-cert --yes
```

## 卸载 Web 面板

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/uninstall-web.sh | sudo bash
```

它会移除 Web 面板服务和 `/opt/host-nginx-manager`，但默认保留：

- `/usr/local/sbin/host-nginx-manager`
- `/etc/host-nginx-manager`
- 已创建的 nginx 站点配置

## 重要边界

这个工具不要用来管理：

- `8443` / `54443` 这类 `stream` 入口
- Rathole 的 SNI 透传
- 非 HTTP 协议
- 已经手工写在 `/etc/nginx/nginx.conf` 里的复杂规则

这些继续手工维护更稳。

如果需要把现有手工规则迁移成工具管理，建议逐条迁移：先加一个新子域名验证，再替换旧配置，不要一次性改公网入口。
