# Host Nginx Manager

Host Nginx Manager 是给”已有宿主 nginx，且不再使用 Nginx Proxy Manager”的 VPS 场景准备的轻量管理工具。

---

## 🚨 安装前必须满足的条件

在安装本项目之前，请确保您的系统已满足以下**所有**条件：

### 必需组件（缺一不可）

- ✅ **操作系统**：Linux（Debian/Ubuntu/CentOS/RHEL 等）
- ✅ **Nginx**：已安装系统 nginx（通过 `apt`/`yum` 安装，非 Docker）
- ✅ **Python 3**：Python 3.7 或更高版本
- ✅ **Certbot**：Let's Encrypt 证书工具
- ✅ **Root 权限**：需要 sudo 或 root 用户权限

### 服务器配置要求

本项目设计用于**低资源消耗**场景，最低配置：

- **CPU**：1 核（双核更佳）
- **内存**：512 MB（1 GB 推荐）
- **磁盘**：1 GB 可用空间
- **网络**：公网 IP（用于 Let's Encrypt 证书验证）

**典型适用服务器**：
- ✅ VPS / 云服务器（阿里云、腾讯云、AWS、Vultr 等）
- ✅ 低配虚拟机（1C1G、2C2G）
- ✅ 个人开发服务器
- ✅ 从 Nginx Proxy Manager 迁移的场景

**资源占用**：
- Web 管理面板：~30-50 MB 内存
- Nginx：~10-20 MB 内存（取决于站点数量）
- 总计：~50-100 MB 内存（远低于 Docker + NPM 方案）

### 快速检查命令

运行以下命令检查是否满足条件：

```bash
# 检查 Nginx
nginx -v

# 检查 Python 3
python3 --version

# 检查 Certbot
certbot --version

# 如果缺少组件，请先安装：
# Debian/Ubuntu:
sudo apt update && sudo apt install -y nginx python3 certbot python3-certbot-nginx

# CentOS/RHEL:
sudo yum install -y nginx python3 certbot python3-certbot-nginx
```

### ⚠️ 如果缺少上述组件

- **没有 Nginx**：先安装 Nginx 后再使用本工具
- **没有 Python 3**：本工具依赖 Python 3，必须先安装
- **没有 Certbot**：无法自动申请 SSL 证书
- **使用 Docker nginx**：本工具不支持，建议使用 Nginx Proxy Manager

---

## ⚠️ 使用场景说明

### ✅ 适用场景

本项目适合以下情况：

- ✅ **已安装 Nginx**（系统 nginx，非 Docker）
- ✅ **主要管理 HTTP/HTTPS 反向代理**
- ✅ **希望自动管理 Let's Encrypt 证书**
- ✅ **需要 Web 界面管理多个域名**
- ✅ **VPS 资源有限**（Python + 系统 nginx，低内存占用）
- ✅ **从 Nginx Proxy Manager 迁移过来**

### ❌ 不适用场景

以下情况**不建议**使用本项目：

- ❌ **没有安装 Nginx** → 建议先用包管理器安装系统 nginx
- ❌ **只用 Docker** → 建议使用 Nginx Proxy Manager 或 Traefik
- ❌ **需要管理 TCP/UDP 流量（stream）** → 本工具只管理 HTTP/HTTPS
- ❌ **需要复杂的负载均衡和高可用** → 建议使用专业方案
- ❌ **Windows 服务器** → 本项目仅支持 Linux

### 🔧 本项目管理什么

**管理范围**（会自动创建和修改）：
- `/etc/nginx/sites-available/vpspm-*.conf` - 站点配置
- `/etc/nginx/sites-enabled/vpspm-*.conf` - 站点链接
- Let's Encrypt 证书申请和续期

**不管理范围**（不会修改，手工维护）：
- `/etc/nginx/nginx.conf` - 全局配置
- `stream` 块配置
- TCP/UDP 端口转发
- Rathole/frp 等隧道工具配置
- 手工创建的其他 nginx 配置

---

## 组成部分

本工具有两部分：

- `host-nginx-manager.sh`：命令行管理器
- `web/host_nginx_web.py`：低资源 Web 管理面板

---

## 安装 Web 管理面板（推荐）

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
sudo host-nginx-manager add api.example.com 127.0.0.1:3001 --email you@example.com
```

这会：

- 写入 `/etc/nginx/sites-available/vpspm-api.example.com.conf`
- 创建 `/etc/nginx/sites-enabled/vpspm-api.example.com.conf` 链接
- 先创建 HTTP 站点用于 ACME 验证
- 使用 `certbot certonly --webroot` 申请证书
- 启用 HTTPS 并将 HTTP 跳转到 HTTPS
- `nginx -t` 通过后才 reload nginx
- 如果新配置失败，会回滚旧配置

## 只创建 HTTP，不立即申请证书

```bash
sudo host-nginx-manager add api.example.com 127.0.0.1:3001 --no-ssl
```

稍后再启用 HTTPS：

```bash
sudo host-nginx-manager enable-ssl api.example.com --email you@example.com
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
sudo host-nginx-manager show api.example.com
sudo host-nginx-manager test
sudo host-nginx-manager reload
```

删除站点：

```bash
sudo host-nginx-manager remove api.example.com --yes
```

删除站点并尝试删除证书：

```bash
sudo host-nginx-manager remove api.example.com --delete-cert --yes
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
