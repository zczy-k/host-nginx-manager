# 完整生命周期测试文档

## 场景：管理一个网站从申请域名到移除的完整流程

### 前提条件
- 服务器上运行了一个服务（如 `127.0.0.1:3000`）
- 域名已经 DNS 解析到服务器 IP
- 已安装本项目

---

## 阶段 1：初始部署 - 申请域名和证书

### 1.1 通过 Web 界面操作

**步骤：**
1. 访问管理界面 `http://服务器IP:8098`
2. 点击左侧"新增反代"
3. 填写表单：
   - 域名：`api.example.com`
   - 后端地址：`127.0.0.1:3000`
   - 后端协议：`http`
   - 邮箱：`admin@example.com`
   - ✅ 勾选"立即申请证书并启用 HTTPS"
4. 点击"创建站点"

**系统自动完成：**
```
✓ 创建 HTTP 配置
✓ 向 Let's Encrypt 申请证书
✓ 启用 HTTPS 并配置自动跳转
✓ 设置自动续期（默认开启）
✓ 重载 nginx
```

**结果：**
- 网站可通过 `https://api.example.com` 访问
- HTTP 自动跳转到 HTTPS
- 证书 90 天有效期
- 自动续期已启用 ✅

### 1.2 通过命令行操作

```bash
sudo host-nginx-manager add api.example.com 127.0.0.1:3000 \
  --upstream-scheme http \
  --email admin@example.com
```

**效果相同。**

---

## 阶段 2：日常运维 - 自动续期

### 2.1 自动续期机制

**Certbot 自动续期：**
- Certbot 通过 systemd timer 自动运行
- 默认每天检查 2 次
- 证书剩余 30 天时自动续期

**检查自动续期配置：**
```bash
# 查看 certbot timer 状态
sudo systemctl status certbot.timer

# 手动测试续期（dry-run）
sudo certbot renew --dry-run
```

### 2.2 通过管理界面查看证书状态

**步骤：**
1. 访问管理界面
2. 点击"证书"视图
3. 查看证书状态：
   - 🟢 **证书 60 天**（正常）
   - 🟡 **证书 20 天**（预警，30天内）
   - 🔴 **证书 5 天**（紧急，7天内）

### 2.3 手动续期

**场景：**证书快过期，想立即续期

**Web 界面操作：**
1. 找到域名 `api.example.com`
2. 点击"续期"
3. 确认续期

**命令行操作：**
```bash
sudo host-nginx-manager renew api.example.com
```

**结果：**
- 证书立即续期
- 有效期延长 90 天
- nginx 自动重载 ✅

---

## 阶段 3：变更需求 - 更换域名

### 场景 A：更换到新域名（旧域名废弃）

**需求：**从 `api.example.com` 换到 `api.newdomain.com`

**操作步骤：**

#### 1. 添加新域名
```bash
# Web 界面：新增反代
# 或命令行：
sudo host-nginx-manager add api.newdomain.com 127.0.0.1:3000 \
  --email admin@example.com
```

#### 2. 测试新域名是否正常
```bash
curl https://api.newdomain.com
```

#### 3. 删除旧域名
```bash
# Web 界面：删除站点 → 取消 → 彻底删除证书
# 或命令行：
sudo host-nginx-manager remove api.example.com --yes --delete-cert
```

**结果：**
- ✅ 新域名正常工作
- ✅ 旧域名已删除
- ✅ 旧证书已清理

### 场景 B：同时支持多个域名（不删除旧域名）

**需求：**同时支持 `api.example.com` 和 `api2.example.com`

**操作：**
```bash
# 添加第二个域名
sudo host-nginx-manager add api2.example.com 127.0.0.1:3000 \
  --email admin@example.com
```

**结果：**
- 两个域名都可访问同一后端
- 两个域名独立管理证书
- 可以单独删除任意一个 ✅

---

## 阶段 4：证书问题处理

### 场景 A：证书申请失败或损坏

**问题表现：**
- 网站无法通过 HTTPS 访问
- 管理界面显示"证书异常"

**解决方案 1：强制重新申请（Web 界面）**
1. 找到域名
2. 点击"续期"
3. 点击"取消"
4. 点击"确定"选择"强制重新申请"

**系统自动执行：**
```
✓ 删除现有证书目录
✓ 删除 certbot 记录
✓ 切换到 HTTP 模式
✓ 重新验证域名
✓ 申请新证书
✓ 启用 HTTPS
```

**解决方案 2：命令行强制重新申请**
```bash
# 先删除证书
sudo host-nginx-manager remove api.example.com --yes --delete-cert

# 重新添加
sudo host-nginx-manager add api.example.com 127.0.0.1:3000 \
  --email admin@example.com
```

**结果：**
- ✅ 证书重新申请成功
- ✅ HTTPS 恢复正常

### 场景 B：只想临时关闭 HTTPS

**操作：**
```bash
# Web 界面：找到域名 → 关闭 HTTPS
# 或命令行：
sudo host-nginx-manager disable-ssl api.example.com
```

**结果：**
- 网站切换到 HTTP 模式
- 证书保留（不删除）
- 可随时重新启用 ✅

**重新启用 HTTPS：**
```bash
sudo host-nginx-manager enable-ssl api.example.com
```

---

## 阶段 5：更换证书

### 场景：想更换证书（如更换邮箱、重新申请）

**方法 1：强制重新申请（推荐）**

**Web 界面：**
1. 找到域名
2. 点击"续期" → "取消" → "强制重新申请"

**命令行：**
```bash
# 关闭 HTTPS
sudo host-nginx-manager disable-ssl api.example.com

# 删除证书
sudo certbot delete --cert-name api.example.com

# 重新启用 HTTPS（使用新邮箱）
sudo host-nginx-manager enable-ssl api.example.com --email newemail@example.com
```

**方法 2：完全重建（彻底清理）**

```bash
# 1. 删除站点和证书
sudo host-nginx-manager remove api.example.com --yes --delete-cert

# 2. 重新添加（使用新邮箱）
sudo host-nginx-manager add api.example.com 127.0.0.1:3000 \
  --email newemail@example.com
```

**结果：**
- ✅ 证书已更换
- ✅ 使用新的联系邮箱
- ✅ 配置完全刷新

---

## 阶段 6：项目下线 - 移除域名和证书

### 场景 A：项目下线，彻底清理

**需求：**
- 删除网站配置
- 删除证书
- 不留任何残留

**Web 界面操作：**
1. 找到域名 `api.example.com`
2. 点击"删除"
3. 点击"取消"（不保留证书）
4. 点击"确定"（彻底删除证书）

**命令行操作：**
```bash
sudo host-nginx-manager remove api.example.com --yes --delete-cert
```

**系统清理内容：**
```
✓ 删除 nginx 配置文件
  - /etc/nginx/sites-available/vpspm-api.example.com.conf
  - /etc/nginx/sites-enabled/vpspm-api.example.com.conf

✓ 删除状态文件
  - /etc/nginx/vps-proxy-manager/sites/api.example.com.env

✓ 删除证书文件
  - /etc/letsencrypt/live/api.example.com/
  - /etc/letsencrypt/archive/api.example.com/
  - /etc/letsencrypt/renewal/api.example.com.conf

✓ 删除 certbot 记录

✓ 创建备份
  - /opt/host-nginx-manager/backups/api.example.com-1234567890.tar.gz
```

**验证清理结果：**
```bash
# 检查配置文件（应该不存在）
ls /etc/nginx/sites-available/vpspm-api.example.com.conf

# 检查证书（应该不存在）
ls /etc/letsencrypt/live/api.example.com/

# 检查状态文件（应该不存在）
ls /etc/nginx/vps-proxy-manager/sites/api.example.com.env
```

**结果：**
- ✅ 所有配置已删除
- ✅ 所有证书已清理
- ✅ 无任何残留
- ✅ 自动备份已保存（可恢复）

### 场景 B：暂时下线，保留证书

**需求：**
- 删除网站配置
- 保留证书（将来可能恢复）

**Web 界面操作：**
1. 找到域名 `api.example.com`
2. 点击"删除"
3. 点击"确定"（保留证书）

**命令行操作：**
```bash
sudo host-nginx-manager remove api.example.com --yes
# 不加 --delete-cert 参数
```

**结果：**
- ✅ nginx 配置已删除
- ✅ 状态文件已删除
- ✅ 证书保留（/etc/letsencrypt/live/api.example.com/）
- ✅ 将来恢复时可以复用证书

**恢复网站：**
```bash
sudo host-nginx-manager add api.example.com 127.0.0.1:3000
# 系统会自动检测并复用现有证书
```

---

## 阶段 7：批量管理

### 场景：管理多个域名

**查看所有站点：**
```bash
sudo host-nginx-manager list
```

**输出示例：**
```
api.example.com               HTTPS    http://127.0.0.1:3000    api.example.com.env
api2.example.com              HTTPS    http://127.0.0.1:3001    api2.example.com.env
web.example.com               HTTP     http://127.0.0.1:8080    web.example.com.env
```

**查看单个站点详情：**
```bash
sudo host-nginx-manager show api.example.com
```

**输出示例：**
```
站点详情
域名              : api.example.com
后端              : http://127.0.0.1:3000
HTTPS             : 已启用
邮箱              : admin@example.com
上传大小          : 64m
读取超时          : 300s
发送超时          : 300s
后端证书校验      : 已开启
状态文件          : /etc/nginx/vps-proxy-manager/sites/api.example.com.env
配置文件          : /etc/nginx/sites-available/vpspm-api.example.com.conf
```

---

## 完整性检查清单

### ✅ 申请域名和证书
- [x] Web 界面一键创建
- [x] 命令行创建
- [x] 自动申请 Let's Encrypt 证书
- [x] 自动配置 HTTPS 跳转
- [x] 自动启用证书续期

### ✅ 自动续期
- [x] Certbot 自动续期（systemd timer）
- [x] 证书状态监控（管理界面）
- [x] 手动触发续期（Web + CLI）
- [x] 续期失败告警（界面显示）

### ✅ 更换域名
- [x] 添加新域名
- [x] 删除旧域名
- [x] 同时支持多个域名
- [x] 无缝切换

### ✅ 更换证书
- [x] 强制重新申请（Web + CLI）
- [x] 更换联系邮箱
- [x] 修复损坏证书
- [x] 临时关闭/启用 HTTPS

### ✅ 移除域名和证书
- [x] 删除站点保留证书
- [x] 彻底删除证书
- [x] 清理所有残留文件
- [x] 自动备份
- [x] 验证清理完整性

### ✅ 故障恢复
- [x] 自动备份机制
- [x] 配置失败自动回滚
- [x] 证书损坏自动修复
- [x] 从备份恢复

---

## 潜在问题和解决方案

### 问题 1：DNS 未解析导致证书申请失败

**表现：**
```
Certbot failed to authenticate some domains (authenticator: webroot)
```

**解决：**
1. 检查 DNS 解析
   ```bash
   dig api.example.com
   nslookup api.example.com
   ```
2. 等待 DNS 生效（最多 48 小时）
3. 确保防火墙开放 80 端口
4. 重新尝试申请

### 问题 2：Let's Encrypt 速率限制

**表现：**
```
too many certificates already issued for exact set of domains
```

**限制：**
- 每个域名每周最多 5 次

**解决：**
1. 等待 7 天后重试
2. 使用 `--dry-run` 测试
3. 避免频繁重新申请

### 问题 3：证书残留导致冲突

**表现：**
- 申请证书时提示"证书已存在"
- 但实际证书损坏或不可用

**解决：**
使用强制重新申请功能（已修复）：
```bash
# Web 界面：续期 → 强制重新申请
# 或命令行：
sudo host-nginx-manager remove api.example.com --yes --delete-cert
sudo host-nginx-manager add api.example.com 127.0.0.1:3000 --email admin@example.com
```

---

## 总结

### 当前项目完全支持以下完整生命周期：

1. **✅ 申请域名证书**
   - Web 界面 + 命令行双支持
   - Let's Encrypt 自动申请
   - 一键启用 HTTPS

2. **✅ 自动续期**
   - Certbot 自动续期
   - 手动续期触发
   - 状态监控预警

3. **✅ 更换域名**
   - 添加新域名
   - 删除旧域名
   - 多域名并存

4. **✅ 更换证书**
   - 强制重新申请
   - 更换邮箱
   - 修复损坏证书

5. **✅ 移除域名和证书**
   - 保留证书删除
   - 彻底清理删除
   - 无残留
   - 自动备份

### 所有功能都已实现且测试通过！🎉
