# 部署流程说明（方式2：单文件自动构建）

## 工作流程

```
开发者修改代码
    ↓
git push 到 GitHub
    ↓
GitHub Actions 自动运行
    ↓
执行 build.py 构建单文件
    ↓
提交 dist/host_nginx_web.py 到仓库
    ↓
用户运行安装脚本
    ↓
脚本下载 dist/host_nginx_web.py
    ↓
部署到服务器
```

## 已完成的配置

### 1. GitHub Actions 工作流
- **文件**: `.github/workflows/build.yml`
- **触发**: 当 `web/` 或 `build.py` 变更时
- **操作**: 自动运行 `python3 build.py` 生成 `dist/host_nginx_web.py`
- **提交**: 自动提交构建好的文件到仓库

### 2. 安装脚本更新
- **文件**: `install-web.sh`
- **修改**: 优先下载 `dist/host_nginx_web.py`（构建版本）
- **回退**: 如果不存在，下载原始 `web/host_nginx_web.py`

### 3. 构建脚本
- **文件**: `build.py`
- **功能**: 将 8 个模块文件合并为单个 Python 文件
- **输出**: `dist/host_nginx_web.py`

## 使用方式

### 对开发者（你）
```bash
# 1. 修改代码（在 web/ 目录下的模块）
vim web/auth/session.py

# 2. 提交到 GitHub
git add .
git commit -m "feat: 改进会话管理"
git push

# 3. GitHub Actions 自动构建
# 等待几分钟，Actions 会自动生成 dist/host_nginx_web.py 并提交
```

### 对用户
```bash
# 使用原有的一键安装命令（无需改变）
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
```

**用户完全无感知，自动获得优化版本！**

## 第一次设置步骤

### 1. 提交所有新文件到 GitHub
```bash
git add .
git commit -m "refactor: 模块化架构 + SQLite + 审计日志"
git push origin main
```

### 2. 触发首次构建
两种方式：
- **自动触发**: 推送后 GitHub Actions 会自动运行
- **手动触发**: 在 GitHub 仓库页面，Actions → Build Single File → Run workflow

### 3. 验证构建结果
```bash
# 检查 dist/host_nginx_web.py 是否生成
git pull
ls -lh dist/host_nginx_web.py

# 应该看到一个包含所有模块的单文件（约 150-200 KB）
```

### 4. 测试安装
```bash
# 在测试服务器运行
curl -fsSL https://raw.githubusercontent.com/zczy-k/host-nginx-manager/main/install-web.sh | sudo bash
```

## 验证成功标志

### 构建成功
- GitHub Actions 显示绿色勾号 ✅
- `dist/host_nginx_web.py` 文件存在
- 文件大小约 150-200 KB

### 安装成功
```bash
# 查看日志，应该显示：
sudo journalctl -u host-nginx-manager-web -n 20

# 期望输出：
# ✅ 数据库初始化成功（SQLite 持久化）
# 模式: 模块化 + SQLite
# Host Nginx Manager 启动
```

## 优势

✅ **开发便捷**: 模块化开发，清晰易维护
✅ **部署简单**: 用户仍然一行命令部署
✅ **自动化**: GitHub Actions 自动构建，无人工干预
✅ **向后兼容**: 如果构建失败，自动回退到原始文件
✅ **CI/CD**: 每次推送自动验证和构建

## 故障排查

### 如果 GitHub Actions 失败
1. 检查 Actions 日志
2. 本地测试 `python3 build.py`
3. 修复后重新推送

### 如果用户安装失败
- 安装脚本会自动回退到 `web/host_nginx_web.py`
- 用户无感知，仍能使用（功能稍少）

## 下一步

1. **推送代码到 GitHub**
   ```bash
   git add .
   git commit -m "refactor: 添加模块化架构和自动构建"
   git push origin main
   ```

2. **观察 GitHub Actions** 
   - 访问: https://github.com/zczy-k/host-nginx-manager/actions
   - 查看构建状态

3. **测试安装**
   - 在测试服务器运行安装命令
   - 验证新特性是否生效

4. **发布说明**
   - 在 GitHub Releases 发布新版本
   - 说明新增的功能（SQLite、审计日志、安全增强）
