# 清理失效配置功能使用指南

## 功能概述

新增了在Web管理界面中清理失效、过期或残留配置的功能，特别适用于：
- DNS解析失败的域名
- 后端服务已停止的站点
- 已导入但不再使用的配置
- 需要批量清理的历史配置

## 使用场景

### 场景1：清理DNS失效的域名（如 fnnas.cni.de5.net）

**问题表现：**
- 问题列表中显示"DNS 异常"
- 错误信息：`[Errno -2] Name or service not known`
- 后端服务不可用：`[Errno 111] Connection refused`

**操作步骤：**
1. 访问Web管理界面
2. 进入"问题"视图
3. 找到DNS异常或后端异常的站点
4. 点击 **"删除失效配置"** 按钮
5. 确认删除操作

**执行结果：**
- ✅ 删除状态文件 `/etc/nginx/vps-proxy-manager/sites/域名.env`
- ✅ 自动注释nginx.conf中的相关配置（如果能定位）
- ✅ 创建备份文件 `nginx.conf.bak-时间戳`
- ✅ 重载nginx配置

### 场景2：在站点列表中删除导入/迁移的站点

**操作步骤：**
1. 进入"站点"视图
2. 找到标记为"已接管"或"受管"的站点
3. 点击该站点的 **"删除"** 按钮
4. 确认删除（会提示操作详情）

**适用站点类型：**
- `IMPORTED=1` - 已导入的站点
- `MIGRATED=1` - 已迁移的站点

## 安全机制

### 1. 自动备份
删除前会自动创建备份文件：
```
/etc/nginx/nginx.conf.bak-20260608120000
```

### 2. 配置测试
修改后自动执行 `nginx -t` 测试，失败则回滚。

### 3. 智能注释
对于能定位的配置块，会自动注释而不是删除：
```nginx
# REMOVED: server {
# REMOVED:     listen 443 ssl;
# REMOVED:     server_name fnnas.cni.de5.net;
# REMOVED:     ...
# REMOVED: }
```

### 4. 无法定位时的提示
如果配置在复杂位置（如stream块），会提示手动编辑：
```
已删除状态文件：fnnas.cni.de5.net

无法自动定位配置块，请手动编辑：/etc/nginx/nginx.conf
可能的位置：
行 73: server_name fnnas.cni.de5.net cni.de5.net;
行 133: fnnas.cni.de5.net 127.0.0.1:58000;
```

## API接口

### 删除导入站点
```bash
POST /api/sites/remove-imported
Content-Type: application/json

{
  "domain": "fnnas.cni.de5.net",
  "comment_out": true  # true=注释配置，false=仅删除状态文件
}
```

**响应示例：**
```json
{
  "code": 0,
  "message": "已删除导入站点",
  "output": "已删除站点：fnnas.cni.de5.net\n已注释原配置：/etc/nginx/nginx.conf (行 73-95)\n备份：/etc/nginx/nginx.conf.bak-1717824000"
}
```

## 命令行操作（可选）

如果Web界面删除后仍有残留，可以手动清理：

```bash
# 1. 删除状态文件
rm /etc/nginx/vps-proxy-manager/sites/fnnas.cni.de5.net.env

# 2. 编辑nginx配置，删除相关配置块
nano /etc/nginx/nginx.conf

# 3. 测试配置
nginx -t

# 4. 重载nginx
systemctl reload nginx
```

## 注意事项

1. **删除前确认**：确保该域名/站点确实不再使用
2. **检查依赖**：某些配置可能被其他配置引用（如stream块）
3. **备份重要**：虽然会自动备份，但建议重要操作前手动备份
4. **DNS记录**：删除nginx配置不会删除DNS记录，需要单独处理

## 故障排除

### 问题1：删除后nginx启动失败
**解决方案：**
```bash
# 恢复备份
cp /etc/nginx/nginx.conf.bak-最新时间戳 /etc/nginx/nginx.conf
systemctl reload nginx
```

### 问题2：配置仍然显示在列表中
**原因：** 可能在多个地方引用了该域名

**解决方案：**
```bash
# 搜索所有引用
grep -r "域名" /etc/nginx/

# 手动清理残留
```

### 问题3：无法定位配置块
**原因：** 配置在stream块、map指令或复杂嵌套中

**解决方案：** 根据提示的行号，手动编辑配置文件

## 更新日志

**v1.1.0 (2026-06-08)**
- ✨ 新增删除导入/迁移站点功能
- ✨ 问题列表中添加"删除失效配置"快捷操作
- ✨ 自动注释原配置块，支持回滚
- ✨ 智能识别无法自动处理的配置
- 🔒 增强安全性：自动备份、配置测试、错误回滚
