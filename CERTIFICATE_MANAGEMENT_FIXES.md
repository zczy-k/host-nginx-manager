# 证书管理修复说明

## 修复内容

### 1. 脚本修复 (host-nginx-manager.sh)

#### 1.1 改进 `issue_cert` 函数
- **问题**：证书存在时直接跳过，即使证书损坏或过期
- **修复**：
  - 检查证书文件是否真实存在
  - 验证证书是否有效（未过期、未损坏）
  - 如果证书有问题，自动使用 `--force-renewal` 强制重新申请
  - 保留原有逻辑：证书有效时跳过申请

#### 1.2 彻底清理证书 `cmd_remove` 函数
- **问题**：`--delete-cert` 只调用 certbot delete，可能残留证书目录
- **修复**：
  - 先调用 `certbot delete` 删除记录
  - 强制删除以下目录/文件：
    - `/etc/letsencrypt/live/DOMAIN`
    - `/etc/letsencrypt/archive/DOMAIN`
    - `/etc/letsencrypt/renewal/DOMAIN.conf`
  - 确保无残留，避免后续冲突

#### 1.3 改进 `cmd_renew` 函数
- **问题**：续期时直接调用 `issue_cert`，不使用 certbot 的续期机制
- **修复**：
  - 优先使用 `certbot renew --cert-name DOMAIN --force-renewal`
  - 如果证书目录不存在，才调用 `issue_cert` 重新申请
  - 更符合 certbot 最佳实践

### 2. Web 界面修复 (web/host_nginx_web.py)

#### 2.1 改进删除站点确认流程
- **问题**：删除时默认保留证书，无法选择彻底删除
- **修复**：
  - 第一次确认：删除站点并保留证书（推荐）
  - 第二次确认：提供"彻底删除证书"选项
  - 清晰说明每个选项的后果

#### 2.2 新增强制重新申请证书功能
- **新增 API**：`/api/certs/force-reissue`
- **功能**：
  1. 彻底删除现有证书（certbot delete + 删除目录）
  2. 暂时切换到 HTTP 模式
  3. 重新申请证书并启用 HTTPS
- **适用场景**：
  - 证书损坏无法修复
  - 证书申请过程出错导致残留
  - 需要完全重新开始

#### 2.3 新增 `force_reissue_certificate` 函数
```python
def force_reissue_certificate(domain: str) -> dict[str, object]:
    """强制重新申请证书：彻底删除现有证书后重新申请"""
    # 1. 彻底删除现有证书
    # 2. 关闭 HTTPS（切换到纯 HTTP）
    # 3. 重新申请证书并启用 HTTPS
```

#### 2.4 改进续期证书确认流程
- **问题**：续期时没有提供强制重新申请选项
- **修复**：
  - 第一次确认：正常续期（推荐）
  - 第二次确认：提供"强制重新申请"选项
  - 用户可根据实际情况选择

## 使用场景

### 场景 1：正常删除站点（保留证书）
1. 在管理界面点击"删除"
2. 点击"确定"确认删除站点
3. 证书文件保留，可用于其他站点或恢复

### 场景 2：彻底删除站点和证书
1. 在管理界面点击"删除"
2. 点击"取消"
3. 在第二次确认中点击"确定"彻底删除证书
4. 所有证书文件和记录被清理

### 场景 3：证书损坏，需要重新申请
1. 在"证书"视图找到问题域名
2. 点击"续期"
3. 点击"取消"
4. 在第二次确认中点击"确定"强制重新申请
5. 系统自动：删除旧证书 → 切换到HTTP → 重新申请 → 启用HTTPS

### 场景 4：移除管理界面的证书记录
1. 确保域名已经不再使用
2. 使用 SSH 连接服务器
3. 运行命令彻底删除：
   ```bash
   sudo host-nginx-manager remove DOMAIN --yes --delete-cert
   ```
4. 所有配置和证书被清理干净

## 技术细节

### 证书清理范围
删除证书时会清理以下位置：
```bash
/etc/letsencrypt/live/DOMAIN/          # 当前证书符号链接
/etc/letsencrypt/archive/DOMAIN/       # 证书历史版本
/etc/letsencrypt/renewal/DOMAIN.conf   # 自动续期配置
```

### 备份机制
删除站点时自动创建备份：
```
/opt/host-nginx-manager/backups/DOMAIN-TIMESTAMP.tar.gz
```

备份包含：
- 站点状态文件 (DOMAIN.env)
- Nginx 配置文件 (vpspm-DOMAIN.conf)
- SSL 证书目录 (如果有)

## 注意事项

1. **Let's Encrypt 速率限制**
   - 每个域名每周最多申请 5 次证书
   - 强制重新申请会消耗配额
   - 建议先尝试正常续期

2. **删除证书的影响**
   - 彻底删除后无法通过备份恢复证书
   - 重新申请需要重新验证域名所有权
   - 确保 DNS 解析正确指向服务器

3. **操作顺序**
   - 建议先尝试正常续期
   - 如果续期失败，再考虑强制重新申请
   - 最后才考虑彻底删除站点和证书

4. **安全性**
   - 所有操作都需要管理员权限
   - 删除前会创建自动备份
   - Nginx 配置失败会自动回滚

## 测试建议

1. **测试正常续期**
   ```bash
   sudo host-nginx-manager renew test.example.com
   ```

2. **测试删除站点（保留证书）**
   ```bash
   sudo host-nginx-manager remove test.example.com --yes
   # 检查证书是否保留
   ls -la /etc/letsencrypt/live/test.example.com/
   ```

3. **测试彻底删除**
   ```bash
   sudo host-nginx-manager remove test.example.com --yes --delete-cert
   # 检查证书是否被清理
   ls -la /etc/letsencrypt/live/test.example.com/  # 应该不存在
   ```

4. **测试强制重新申请（通过 Web 界面）**
   - 访问管理界面
   - 找到证书页面
   - 点击"续期" → "取消" → "确定"强制重新申请
   - 观察输出日志

## 升级说明

### 已有站点兼容性
- 完全兼容现有站点
- 不需要手动迁移
- 自动使用新的证书管理逻辑

### 配置文件变化
- 无需修改配置文件
- 所有修改在代码层面

### 回滚方案
如果出现问题，可以回滚到之前的版本：
```bash
git checkout HEAD~1 host-nginx-manager.sh web/host_nginx_web.py
```
