# Host Nginx Manager

轻量级 Nginx 反向代理管理工具，专为已有系统 Nginx 的 VPS 场景设计。

> 从 Nginx Proxy Manager 迁移？这是你的最佳选择。

---

## ✨ 核心特性

- 🎯 **Web 管理界面** - 直观的单页应用，无需 Docker
- 🔐 **Let's Encrypt 集成** - 自动申请和续期 SSL 证书
- 📊 **证书监控告警** - 支持 Webhook（飞书/钉钉/企业微信）和邮件通知
- 🔒 **企业级安全** - PBKDF2 密码哈希、双因素认证、登录限流、审计日志
- 💾 **SQLite 持久化** - 会话、配置、审计日志本地存储
- ⚡ **极低资源占用** - 单进程 ~30MB 内存（远低于 Docker 方案）
- 🛠️ **CLI + Web 双模式** - 命令行和界面任选

---

## 📋 系统要求

### 必需组件

- **操作系统**：Linux（Debian/Ubuntu/CentOS 等）
- **Nginx**：系统 nginx（非 Docker）
- **Python**：3.7+
- **Certbot**：Let's Encrypt 工具
- **权限**：root 或 sudo

### 快速检查

```bash
nginx -v && python3 --version && certbot --version
```

### 安装依赖（如缺失）

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install -y nginx python3 certbot python3-certbot-nginx

# CentOS/RHEL
sudo yum install -y nginx python3 certbot python3-certbot-nginx
```

### 资源占用

- **Web 面板**：~30MB 内存
- **Nginx**：~10-20MB 内存
- **磁盘**：<100MB

---

## 🚀 快速开始

### 一键安装 Web 面板

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
```

安装完成后：

1. 记下管理地址和密码（也可查看 `/etc/host-nginx-manager/web.env`）
2. 开放防火墙端口 `8098/tcp`
3. 浏览器访问 `http://服务器IP:8098`

### 升级到最新版

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
# 选择 1) 升级到最新版本
```

---

## 🎨 Web 功能

### 站点管理
- ✅ 查看所有站点、健康度、证书状态
- ✅ 新增 HTTP/HTTPS 反向代理
- ✅ 批量操作（启用/禁用/删除）
- ✅ 导入现有 Nginx 配置

### 证书管理
- ✅ 一键申请 Let's Encrypt 证书
- ✅ 自动续期钩子配置
- ✅ 手动续期
- ✅ 查看证书详情（颁发者、有效期、SAN）
- ✅ **证书到期监控**（提前 7 天通知）
- ✅ **多渠道告警**（Webhook/邮件）

### 维护工具
- ✅ 测试 Nginx 配置
- ✅ 重载 Nginx
- ✅ 配置备份与恢复
- ✅ 健康检查
- ✅ 证书权限修复工具

### 安全特性
- ✅ 强密码策略（12位+复杂度）
- ✅ 双因素认证（TOTP）
- ✅ 登录限流（5次失败锁定5分钟）
- ✅ API 速率限制
- ✅ 审计日志（所有操作可追溯）
- ✅ 自动检测 HTTPS（Cookie Secure）

### 通知设置（新增）

**在"维护"→"通知设置"配置证书到期告警**：

1. **Webhook 通知**（推荐）
   - 支持飞书、钉钉、企业微信机器人
   - 一键测试发送

2. **邮件通知**
   - SMTP 配置
   - 支持 Gmail、腾讯企业邮箱等

3. **监控设置**
   - 每天凌晨 3 点自动检查
   - 提前 N 天提醒（可配置）
   - 通知历史记录

---

## 🔧 CLI 使用

### 安装 CLI

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/host-nginx-manager.sh \
  -o /usr/local/sbin/host-nginx-manager
sudo chmod +x /usr/local/sbin/host-nginx-manager
```

### 常用命令

```bash
# 添加反向代理（自动申请证书）
sudo host-nginx-manager add api.example.com 127.0.0.1:3001 --email you@example.com

# 只创建 HTTP（不申请证书）
sudo host-nginx-manager add blog.example.com 127.0.0.1:8080 --no-ssl

# 后端是自签 HTTPS
sudo host-nginx-manager add nas.example.com 127.0.0.1:5001 \
  --upstream-scheme https --backend-insecure

# 启用 HTTPS
sudo host-nginx-manager enable-ssl blog.example.com --email you@example.com

# 续期证书
sudo host-nginx-manager renew api.example.com

# 查看站点
sudo host-nginx-manager list
sudo host-nginx-manager show api.example.com

# 删除站点
sudo host-nginx-manager remove api.example.com --yes

# 测试和重载
sudo host-nginx-manager test
sudo host-nginx-manager reload
```

---

## 🔐 安全管理

### 修改密码

**Web 界面**：登录后进入"账户设置"

**命令行**：
```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
# 选择 2) 重置登录密码
```

### 启用双因素认证

1. 登录 Web 界面
2. 进入"账户设置"→"双因素认证"
3. 扫描二维码绑定（支持 Google Authenticator、Microsoft Authenticator）
4. 输入验证码启用

### HTTPS 部署（推荐）

如果通过反向代理（如 Nginx）以 HTTPS 访问管理界面：

```bash
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
# 选择 3) 切换 Cookie Secure 模式
```

或者添加环境变量：
```bash
# /etc/host-nginx-manager/web.env
HNG_COOKIE_SECURE=true
```

---

## 📜 证书自动续期

### 配置续期钩子（推荐）

**Web 界面**：进入"维护"→"基础维护"→点击"配置证书续期钩子"

**命令行**：
```bash
# 创建续期钩子
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh > /dev/null <<'EOF'
#!/bin/bash
systemctl reload nginx
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

# 测试续期
sudo certbot renew --dry-run
```

### 验证自动续期

```bash
# 查看 certbot 定时任务
sudo systemctl list-timers | grep certbot

# 查看续期日志
sudo tail -f /var/log/letsencrypt/letsencrypt.log
```

---

## 🎯 适用场景

### ✅ 适合你

- 已安装系统 Nginx（非 Docker）
- 管理多个域名的 HTTP/HTTPS 反向代理
- 需要自动管理 Let's Encrypt 证书
- VPS 资源有限（1C1G 或更低）
- 从 Nginx Proxy Manager 迁移

### ❌ 不适合你

- 没有安装 Nginx → 先安装 nginx
- 只用 Docker → 推荐 Nginx Proxy Manager 或 Traefik
- 需要管理 TCP/UDP stream → 手动配置更合适
- Windows 服务器 → 仅支持 Linux

---

## 📂 项目结构

```
.
├── host-nginx-manager.sh    # CLI 管理脚本
├── install-web.sh            # Web 面板安装脚本
├── build.py                  # 单文件构建脚本
├── web/
│   ├── host_nginx_web.py    # Web 应用主文件
│   ├── core/                 # 核心模块（数据库、审计）
│   ├── auth/                 # 认证模块（会话、密码、2FA）
│   └── utils/                # 工具模块（验证、QR码）
└── dist/
    └── host_nginx_web.py    # 自动构建的单文件版本
```

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

### 开发环境

```bash
# 克隆仓库
git clone https://github.com/zczy-k/host-nginx-manager.git
cd host-nginx-manager

# 模块化模式运行（开发）
cd web && python3 host_nginx_web.py

# 构建单文件版本
python3 build.py
```

### GitHub Actions 自动构建

每次推送到 `main` 分支时，会自动：
- 合并所有模块到 `dist/host_nginx_web.py`
- 运行语法检查
- 提交构建结果

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源。

你可以自由地：
- ✅ 使用（个人或商业）
- ✅ 修改
- ✅ 分发

唯一要求：保留版权声明。

---

## 🙏 致谢

感谢所有贡献者和用户的支持！

如果觉得有用，请给个 ⭐ Star！
