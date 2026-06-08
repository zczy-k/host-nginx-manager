#!/usr/bin/env python3
"""Lightweight web UI for host-nginx-manager."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookies
import http.client
import ipaddress
import json
import os
import pathlib
import re
import secrets
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

APP_TITLE = "Host Nginx Manager"
MANAGER_BIN = os.environ.get("HNG_MANAGER_BIN", "/usr/local/sbin/host-nginx-manager")
STATE_DIR = pathlib.Path(os.environ.get("HNG_STATE_DIR", "/etc/nginx/vps-proxy-manager/sites"))
BIND = os.environ.get("HNG_WEB_BIND", "0.0.0.0")
PORT = int(os.environ.get("HNG_WEB_PORT", "8098"))
PASSWORD = os.environ.get("HNG_WEB_PASSWORD", "")
SECRET = os.environ.get("HNG_WEB_SECRET", "") or secrets.token_urlsafe(32)
COOKIE_NAME = "hng_session"
SESSION_TTL = 12 * 60 * 60
DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z0-9.-]+$")
CERT_WARN_DAYS = int(os.environ.get("HNG_CERT_WARN_DAYS", "30"))
CERT_CRITICAL_DAYS = int(os.environ.get("HNG_CERT_CRITICAL_DAYS", "7"))

PAGE_CSS = r'''
  <style>
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --line:#d9dee6; --text:#17202a; --muted:#667085; --blue:#1b64d8; --blue2:#eaf1ff; --red:#c62828; --green:#157347; --amber:#9a6700; --shadow:0 1px 2px rgba(16,24,40,.06); }
    * { box-sizing: border-box; }
    body { margin:0; font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
    button,input,select { font:inherit; }
    .shell { min-height:100vh; display:grid; grid-template-columns:240px 1fr; }
    aside { background:#111827; color:#d1d5db; padding:18px 14px; }
    .brand { color:#fff; font-weight:700; font-size:17px; margin:2px 8px 22px; }
    .nav button { width:100%; text-align:left; background:transparent; color:#d1d5db; border:0; border-radius:6px; padding:10px 12px; cursor:pointer; }
    .nav button.active, .nav button:hover { background:#243044; color:#fff; }
    main { padding:22px; max-width:1280px; width:100%; }
    header { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:18px; }
    h1 { font-size:22px; margin:0; }
    h2 { font-size:16px; margin:0 0 12px; }
    .muted { color:var(--muted); }
    .grid { display:grid; gap:14px; }
    .stats { grid-template-columns:repeat(4,minmax(150px,1fr)); margin-bottom:14px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; box-shadow:var(--shadow); padding:16px; }
    .stat-label { color:var(--muted); font-size:12px; margin-bottom:8px; }
    .stat-value { font-size:18px; font-weight:700; overflow-wrap:anywhere; }
    .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .spacer { flex:1; }
    .btn { border:1px solid var(--line); background:#fff; color:var(--text); border-radius:6px; padding:8px 11px; cursor:pointer; min-height:36px; }
    .btn:hover { border-color:#b8c1cf; background:#f9fafb; }
    .btn.primary { background:var(--blue); border-color:var(--blue); color:#fff; }
    .btn.danger { border-color:#f0b6b6; color:var(--red); }
    .btn.small { padding:5px 8px; min-height:30px; }
    .tag { display:inline-flex; align-items:center; height:24px; padding:0 8px; border-radius:999px; background:#eef2f7; color:#344054; font-size:12px; }
    .tag.ok { background:#e8f5ee; color:var(--green); }
    .tag.warn { background:#fff4db; color:var(--amber); }
    .tag.bad { background:#fdecec; color:var(--red); }
    table { width:100%; border-collapse:collapse; }
    th,td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--line); vertical-align:middle; }
    th { color:var(--muted); font-size:12px; font-weight:600; background:#fbfcfd; }
    td { overflow-wrap:anywhere; }
    form { display:grid; gap:12px; }
    .form-grid { display:grid; grid-template-columns:repeat(2,minmax(220px,1fr)); gap:12px; }
    label { display:grid; gap:6px; color:#344054; font-weight:600; }
    input,select { border:1px solid var(--line); border-radius:6px; padding:9px 10px; background:#fff; min-height:38px; }
    input[type=checkbox] { min-height:auto; width:16px; height:16px; }
    .check { display:flex; align-items:center; gap:8px; font-weight:500; }
    .view { display:none; }
    .view.active { display:block; }
    pre { margin:0; white-space:pre-wrap; background:#101828; color:#e5e7eb; padding:12px; border-radius:8px; max-height:360px; overflow:auto; }
    .notice { border-left:3px solid var(--blue); background:var(--blue2); padding:10px 12px; border-radius:6px; color:#173b70; }
    .dashboard-grid { grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr); }
    .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:12px 0 14px; }
    .toolbar input { flex:1 1 280px; }
    .toolbar select { width:min(220px,100%); }
    .list { display:grid; gap:10px; }
    .list-item { border:1px solid var(--line); border-radius:6px; padding:12px; background:#fff; }
    .list-item .title { font-weight:700; }
    .list-item .meta { color:var(--muted); font-size:12px; margin-top:4px; }
    .list-item .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
    .login { min-height:100vh; display:grid; place-items:center; padding:24px; }
    .login .panel { width:min(420px,100%); }
    .login-message { margin:12px 0; }
    details { margin:12px 0; }
    summary { cursor:pointer; padding:10px; background:#f6f7f9; border-radius:6px; font-weight:600; user-select:none; }
    details[open] summary { margin-bottom:12px; }
    .help-content { line-height:1.7; }
    .help-content h3 { margin:18px 0 10px; font-size:15px; }
    .help-content code { background:#f1f3f5; padding:2px 6px; border-radius:3px; font-size:13px; }
    .help-content pre { background:#101828; color:#e5e7eb; padding:12px; border-radius:6px; overflow:auto; }
    .modal-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.5); z-index:1000; align-items:center; justify-content:center; }
    .modal-overlay.active { display:flex; }
    .modal { background:#fff; border-radius:8px; max-width:90vw; max-height:90vh; overflow:auto; box-shadow:0 10px 40px rgba(0,0,0,0.2); }
    .modal-header { padding:16px 20px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
    .modal-body { padding:20px; }
    .modal-close { cursor:pointer; font-size:24px; color:var(--muted); }
    .cert-detail-grid { display:grid; gap:12px; }
    .cert-detail-row { display:grid; grid-template-columns:140px 1fr; gap:10px; padding:8px 0; border-bottom:1px solid var(--line); }
    .cert-detail-label { font-weight:600; color:var(--muted); }
    @media (max-width:860px) { .shell { grid-template-columns:1fr; } aside { position:sticky; top:0; z-index:5; } .nav { display:flex; overflow:auto; } .nav button { white-space:nowrap; } .stats,.form-grid,.dashboard-grid { grid-template-columns:1fr; } .toolbar input,.toolbar select { width:100%; } main { padding:16px; } }
  </style>
'''

LOGIN_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登录 - Host Nginx Manager</title>
''' + PAGE_CSS + r'''</head>
<body>
<div class="login">
  <section class="panel">
    <h1>Host Nginx Manager</h1>
    <p class="muted">输入安装时生成的管理密码。</p>
    <section id="loginMessage" class="login-message"></section>
    <form id="loginForm">
      <label>管理密码<input id="password" type="password" autocomplete="current-password" required></label>
      <button id="loginBtn" class="btn primary" type="submit">登录</button>
    </form>
  </section>
</div>
<script>
const $ = (s) => document.querySelector(s);
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showMsg(text, type='info'){ $('#loginMessage').innerHTML = text ? `<div class="panel"><span class="tag ${type}">${type}</span> ${escapeHtml(text)}</div>` : ''; }
async function api(path, opts={}){ const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts}); const data = await res.json(); if(!res.ok) throw new Error(data.error || '请求失败'); return data; }
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('#loginBtn');
  btn.disabled = true;
  btn.textContent = '登录中...';
  showMsg('');
  try {
    await api('/api/login',{method:'POST',body:JSON.stringify({password:$('#password').value})});
    window.location.replace('/');
  } catch(err) {
    showMsg(err.message,'bad');
    btn.disabled = false;
    btn.textContent = '登录';
  }
});
</script>
</body>
</html>'''

APP_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Host Nginx Manager</title>
''' + PAGE_CSS + r'''
</head>
<body>
<div class="shell">
  <aside>
    <div class="brand">Host Nginx Manager</div>
    <nav class="nav">
      <button data-view="dashboard" class="active">概览</button>
      <button data-view="issues">问题</button>
      <button data-view="sites">站点</button>
      <button data-view="services">本机服务</button>
      <button data-view="certs">证书</button>
      <button data-view="create">新增反代</button>
      <button data-view="tools">维护</button>
      <button data-view="help">帮助</button>
    </nav>
  </aside>
  <main>
    <header>
      <div><h1 id="title">概览</h1><div class="muted">管理宿主 nginx 的标准 HTTP/HTTPS 反向代理。</div></div>
      <div class="row"><button class="btn" id="refreshBtn">刷新</button><button class="btn" id="logoutBtn">退出</button></div>
    </header>
    <section id="message"></section>
    <section id="dashboard" class="view active">
      <div class="grid stats">
        <div class="panel"><div class="stat-label">nginx</div><div id="nginxStatus" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">Nginx 站点</div><div id="siteCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">监听地址</div><div id="bindInfo" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">管理脚本</div><div id="managerInfo" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">后端异常</div><div id="backendBadCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">证书预警</div><div id="certWarnCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">DNS 异常</div><div id="dnsBadCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">本机服务</div><div id="serviceCount" class="stat-value">-</div></div>
      </div>
      <div class="grid dashboard-grid">
        <div class="panel"><h2>当前建议</h2><div class="notice">普通 Web/API 服务可以统一放到不同子域名的 443；Rathole、stream、ssl_preread 仍建议手工维护。</div></div>
        <div class="panel">
          <div class="row"><h2>待处理站点</h2><span class="spacer"></span><button class="btn small" id="problemJumpBtn" type="button">只看问题</button></div>
          <div id="problemRows" class="list"></div>
        </div>
      </div>
    </section>
    <section id="issues" class="view">
      <div class="panel">
        <div class="row"><h2>问题清单</h2><span class="spacer"></span><div id="issueSummary" class="muted"></div></div>
        <div style="overflow:auto"><table><thead><tr><th>域名</th><th>问题</th><th>当前情况</th><th>操作</th></tr></thead><tbody id="issueRows"></tbody></table></div>
      </div>
    </section>
    <section id="sites" class="view">
      <div class="panel">
        <div class="row"><h2>Nginx 站点</h2><span class="spacer"></span><div id="siteSummary" class="muted"></div><button class="btn primary" data-jump="create">新增</button></div>
        <div class="toolbar">
          <input id="siteSearch" placeholder="搜索域名、后端、来源">
          <select id="siteFilter">
            <option value="all">全部站点</option>
            <option value="problems">问题站点</option>
            <option value="backend_bad">后端异常</option>
            <option value="cert_warn">证书预警</option>
            <option value="dns_bad">DNS 异常</option>
            <option value="managed">受管站点</option>
            <option value="imported">已接管</option>
            <option value="importable">可接管</option>
            <option value="https">HTTPS</option>
            <option value="http">HTTP</option>
          </select>
          <button class="btn" id="siteSearchClear" type="button">清空筛选</button>
        </div>
        <div style="overflow:auto"><table><thead><tr><th>域名</th><th>监听</th><th>类型</th><th>目标/目录</th><th>来源</th><th>操作</th></tr></thead><tbody id="siteRows"></tbody></table></div>
      </div>
    </section>
    <section id="services" class="view">
      <div class="panel">
        <div class="row"><h2>本机监听服务</h2><span class="spacer"></span><button class="btn primary" data-jump="create">新增反代</button></div>
        <div style="overflow:auto"><table><thead><tr><th>地址</th><th>端口</th><th>进程</th><th>状态</th><th>建议后端</th><th>操作</th></tr></thead><tbody id="serviceRows"></tbody></table></div>
      </div>
    </section>
    <section id="certs" class="view">
      <div class="panel">
        <div class="row"><h2>证书中心</h2><span class="spacer"></span><div id="certSummary" class="muted"></div></div>
        <div class="toolbar">
          <input id="certSearch" placeholder="搜索域名、证书状态、来源">
          <select id="certFilter">
            <option value="all">全部证书视图</option>
            <option value="issues">待处理</option>
            <option value="enabled">已启用 HTTPS</option>
            <option value="needs_https">可启用 HTTPS</option>
            <option value="ok">证书正常</option>
            <option value="warn">即将到期</option>
            <option value="missing">证书缺失</option>
            <option value="error">证书异常</option>
            <option value="dns_bad">DNS 异常</option>
            <option value="managed">仅受管站点</option>
          </select>
          <button class="btn" id="certSearchClear" type="button">清空筛选</button>
        </div>
        <div style="overflow:auto"><table><thead><tr><th>域名</th><th>证书状态</th><th>当前配置</th><th>来源</th><th>操作</th></tr></thead><tbody id="certRows"></tbody></table></div>
      </div>
    </section>
    <section id="create" class="view">
      <div class="panel">
        <h2>新增标准反向代理</h2>
        <form id="createForm">
          <div class="form-grid">
            <label>域名<input name="domain" placeholder="api.example.com" required></label>
            <label>后端地址<input name="upstream" placeholder="127.0.0.1:3001" required></label>
            <label>后端协议<select name="scheme"><option value="http">http</option><option value="https">https</option></select></label>
            <label>邮箱<input name="email" placeholder="you@example.com"></label>
            <label>上传大小<input name="body" value="64m"></label>
            <label>读取超时<input name="readTimeout" value="300s"></label>
          </div>
          <label class="check"><input name="ssl" type="checkbox" checked> 立即申请证书并启用 HTTPS</label>
          <label class="check"><input name="backendInsecure" type="checkbox"> 后端是自签 HTTPS，关闭后端证书校验</label>
          <div class="row"><button class="btn primary" type="submit">创建站点</button></div>
        </form>
      </div>
    </section>
    <section id="tools" class="view">
      <div class="grid">
        <div class="panel"><h2>nginx 维护</h2><div class="row"><button class="btn" id="testBtn">测试配置</button><button class="btn primary" id="reloadBtn">重载 nginx</button></div></div>
        <div class="panel"><h2>输出</h2><pre id="output">等待操作...</pre></div>
      </div>
    </section>
    <section id="help" class="view">
      <div class="panel help-content">
        <h1>Host Nginx Manager 使用帮助</h1>
        <p>这是一个轻量级的 Nginx 反向代理管理工具，帮助你快速配置和管理 Let's Encrypt HTTPS 证书。</p>

        <details>
          <summary>📖 快速开始</summary>
          <h3>1. 新增反向代理</h3>
          <p>点击左侧"新增反代"，填写域名和后端地址：</p>
          <ul>
            <li><strong>域名</strong>：如 <code>api.example.com</code></li>
            <li><strong>后端地址</strong>：如 <code>127.0.0.1:3001</code></li>
            <li><strong>后端协议</strong>：通常选择 <code>http</code></li>
            <li><strong>邮箱</strong>：用于 Let's Encrypt 证书申请通知</li>
          </ul>
          <p>勾选"立即申请证书"后点击创建，工具会自动完成：</p>
          <ol>
            <li>创建 nginx 配置文件</li>
            <li>申请 Let's Encrypt 证书</li>
            <li>配置 HTTPS 并自动跳转</li>
            <li>测试并重载 nginx</li>
          </ol>

          <h3>2. 管理现有站点</h3>
          <p>在"站点"视图中可以：</p>
          <ul>
            <li>编辑后端地址</li>
            <li>启用/关闭 HTTPS</li>
            <li>删除站点</li>
            <li>导入已有配置</li>
          </ul>
        </details>

        <details>
          <summary>🔒 证书管理</summary>
          <h3>证书状态说明</h3>
          <ul>
            <li><span class="tag ok">证书 N 天</span> - 证书正常，剩余 N 天有效期</li>
            <li><span class="tag warn">证书 N 天</span> - 证书即将过期（30天内）</li>
            <li><span class="tag bad">证书异常</span> - 证书缺失或读取失败</li>
          </ul>

          <h3>证书续期</h3>
          <p>证书快要过期时，在"证书"视图中找到对应域名，点击"查看详情"可以看到：</p>
          <ul>
            <li>证书颁发者</li>
            <li>有效期</li>
            <li>SAN（备用域名）</li>
          </ul>
          <p>点击"续期"按钮即可手动续期证书。Let's Encrypt 证书也会在到期前自动续期。</p>

          <h3>DNS 配置</h3>
          <p>申请证书前，请确保域名的 DNS 记录已指向本服务器 IP：</p>
          <ul>
            <li><span class="tag ok">DNS 正常</span> - 域名已正确解析到本机</li>
            <li><span class="tag bad">DNS 异常</span> - 域名未指向本机或解析失败</li>
          </ul>
        </details>

        <details>
          <summary>🔧 功能说明</summary>
          <h3>概览</h3>
          <p>显示 nginx 状态、站点统计、问题汇总。快速发现需要处理的异常。</p>

          <h3>问题</h3>
          <p>集中显示所有需要处理的问题：</p>
          <ul>
            <li>后端服务连接失败</li>
            <li>证书即将过期或缺失</li>
            <li>DNS 未正确解析</li>
          </ul>

          <h3>站点</h3>
          <p>管理所有 nginx 站点配置。支持筛选和搜索。分为：</p>
          <ul>
            <li><span class="tag ok">受管</span> - 由本工具创建和管理</li>
            <li><span class="tag ok">已接管</span> - 从现有配置导入，可编辑</li>
            <li><span class="tag">已有</span> - 现有 nginx 配置，只读</li>
          </ul>

          <h3>本机服务</h3>
          <p>自动发现本机监听的端口，快速为其创建反向代理。</p>

          <h3>证书</h3>
          <p>专注于 HTTPS 证书管理，查看所有证书状态、有效期。</p>

          <h3>维护</h3>
          <p>测试 nginx 配置、重载服务。所有修改操作都会自动测试配置并在失败时回滚。</p>
        </details>

        <details>
          <summary>⚠️ 故障排除</summary>
          <h3>证书申请失败</h3>
          <p><strong>常见原因：</strong></p>
          <ul>
            <li>DNS 未指向本服务器</li>
            <li>80 端口未开放或被防火墙拦截</li>
            <li>Let's Encrypt 速率限制（每域名每周 5 次）</li>
          </ul>
          <p><strong>解决方法：</strong></p>
          <ol>
            <li>在"站点"或"证书"视图检查 DNS 状态</li>
            <li>确认云厂商安全组开放 80 和 443 端口</li>
            <li>如触发速率限制，等待一周后重试</li>
          </ol>

          <h3>后端连接失败</h3>
          <p><strong>可能原因：</strong></p>
          <ul>
            <li>后端服务未启动</li>
            <li>端口号错误</li>
            <li>防火墙阻止本地连接</li>
          </ul>
          <p><strong>检查方法：</strong></p>
          <pre>ss -lntp | grep :端口号
curl http://127.0.0.1:端口号</pre>

          <h3>nginx 重载失败</h3>
          <p>工具会自动回滚到上一个有效配置。查看"维护"视图的输出了解具体错误。</p>

          <h3>删除失效配置</h3>
          <p>如果域名已过期或服务已停止，在"问题"视图中点击"删除失效配置"可以安全清理。操作会：</p>
          <ul>
            <li>删除状态文件</li>
            <li>注释原始 nginx 配置</li>
            <li>创建备份文件</li>
          </ul>
        </details>

        <details>
          <summary>📚 API 文档</summary>
          <h3>站点管理</h3>
          <ul>
            <li><code>POST /api/sites/add</code> - 新增站点</li>
            <li><code>POST /api/sites/update</code> - 更新站点后端</li>
            <li><code>POST /api/sites/remove</code> - 删除站点</li>
            <li><code>POST /api/sites/import</code> - 导入现有站点</li>
            <li><code>POST /api/sites/migrate</code> - 迁移为受管站点</li>
            <li><code>POST /api/sites/remove-imported</code> - 删除导入的站点</li>
          </ul>

          <h3>证书管理</h3>
          <ul>
            <li><code>POST /api/sites/enable-ssl</code> - 启用 HTTPS</li>
            <li><code>POST /api/sites/disable-ssl</code> - 关闭 HTTPS</li>
            <li><code>POST /api/certs/renew</code> - 续期证书</li>
            <li><code>GET /api/certs/detail?domain=xxx</code> - 查看证书详情</li>
          </ul>

          <h3>系统</h3>
          <ul>
            <li><code>GET /api/status</code> - 获取系统状态</li>
            <li><code>POST /api/nginx/test</code> - 测试 nginx 配置</li>
            <li><code>POST /api/nginx/reload</code> - 重载 nginx</li>
          </ul>
        </details>

        <details>
          <summary>❓ 常见问题</summary>
          <h3>Q: 工具会修改我现有的 nginx 配置吗？</h3>
          <p>A: 不会。工具只管理它自己创建的站点（文件名包含 <code>vpspm-</code>）。现有的 stream、ssl_preread 等手写配置不会被修改。</p>

          <h3>Q: 可以管理非 HTTP 协议吗？</h3>
          <p>A: 不建议。工具专注于标准的 HTTP/HTTPS 反向代理。TCP/UDP 转发、Rathole 等建议手动维护。</p>

          <h3>Q: 证书会自动续期吗？</h3>
          <p>A: 是的。Let's Encrypt 证书通常由 certbot 的 systemd timer 自动续期。你也可以手动续期。</p>

          <h3>Q: 删除站点会删除证书吗？</h3>
          <p>A: 默认不会。如需同时删除证书，使用"删除"时会提示选项。</p>

          <h3>Q: 支持自定义证书吗？</h3>
          <p>A: 当前版本专注于 Let's Encrypt。如需自定义证书，建议手动配置 nginx。</p>

          <h3>Q: 如何备份配置？</h3>
          <p>A: 所有状态保存在 <code>/etc/nginx/vps-proxy-manager/sites/</code>。定期备份该目录和 nginx 配置即可。</p>
        </details>

        <div style="margin-top:24px; padding-top:24px; border-top:1px solid var(--line); color:var(--muted);">
          <p>需要帮助？查看 <a href="https://github.com/zczy-k/host-nginx-manager" target="_blank" style="color:var(--blue);">GitHub 项目</a> 或提交 Issue。</p>
        </div>
      </div>
    </section>
  </main>
</div>
<div id="certModal" class="modal-overlay">
  <div class="modal">
    <div class="modal-header">
      <h2 id="certModalTitle">证书详情</h2>
      <span class="modal-close" onclick="closeCertModal()">&times;</span>
    </div>
    <div class="modal-body">
      <div id="certModalContent">加载中...</div>
    </div>
  </div>
</div>
<script>
let state = null;
let siteQuery = '';
let siteFilter = 'all';
let certQuery = '';
let certFilter = 'all';
const VIEW_TITLES = {dashboard:'概览',issues:'问题',sites:'站点',services:'本机服务',certs:'证书',create:'新增反代',tools:'维护',help:'帮助'};
const CERT_WARN_STATES = new Set(['warn','missing','error','critical']);
const $ = (s) => document.querySelector(s);
function showMsg(text, type='info'){
  $('#message').innerHTML = text ? `<div class="panel"><span class="tag ${type}">${type}</span> ${escapeHtml(text)}</div>` : '';
}
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function api(path, opts={}){ const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts}); if(res.status===401){ window.location.replace('/login'); throw new Error('未登录'); } const data = await res.json(); if(!res.ok) throw new Error(data.error || data.output || '请求失败'); return data; }
async function load(){ state = await api('/api/status'); render(); }
function switchView(view){
  document.querySelectorAll('.view').forEach(x => x.classList.toggle('active', x.id === view));
  document.querySelectorAll('.nav button').forEach(x => x.classList.toggle('active', x.dataset.view === view));
  $('#title').textContent = VIEW_TITLES[view] || VIEW_TITLES.dashboard;
}
function hasDnsIssue(site){
  return site.dns_status === 'bad' || site.dns_status === 'error';
}
function dnsTagHtml(site){
  if (site.dns_status === 'ok') return '<span class="tag ok">DNS 正常</span>';
  if (site.dns_status === 'warn') return '<span class="tag warn">DNS 待确认</span>';
  if (site.dns_status === 'bad') return '<span class="tag bad">DNS 未指向</span>';
  if (site.dns_status === 'error') return '<span class="tag bad">DNS 查询失败</span>';
  return '';
}
function isProblemSite(site){
  return site.backend_status === 'bad' || CERT_WARN_STATES.has(site.cert_status) || hasDnsIssue(site);
}
function siteSearchText(site){
  return [
    site.domain,
    Array.isArray(site.names) ? site.names.join(' ') : '',
    site.upstream || site.root || '',
    site.source || '',
    site.kind || '',
    site.backend_detail || '',
    site.cert_info || '',
    site.dns_detail || ''
  ].join(' ').toLowerCase();
}
function siteMatchesFilter(site){
  switch (siteFilter) {
    case 'problems': return isProblemSite(site);
    case 'backend_bad': return site.backend_status === 'bad';
    case 'cert_warn': return CERT_WARN_STATES.has(site.cert_status);
    case 'dns_bad': return hasDnsIssue(site);
    case 'managed': return !!site.managed;
    case 'imported': return !!site.imported;
    case 'importable': return !!site.importable;
    case 'https': return !!site.https;
    case 'http': return !site.https;
    default: return true;
  }
}
function getFilteredSites(){
  if(!state){ return []; }
  const query = String(siteQuery || '').trim().toLowerCase();
  return state.sites.filter(site => siteMatchesFilter(site) && (!query || siteSearchText(site).includes(query)));
}
function isCertificateSite(site){
  const domain = String(site.domain || '');
  return domain.includes('.') && (site.kind === '反向代理' || !!site.https || site.cert_status !== 'none');
}
function certificateSearchText(site){
  return [
    site.domain,
    Array.isArray(site.names) ? site.names.join(' ') : '',
    site.cert_info || '',
    site.source || '',
    site.kind || '',
    site.upstream || site.root || '',
    site.readonly_reason || '',
    site.dns_detail || ''
  ].join(' ').toLowerCase();
}
function certificateMatchesFilter(site){
  switch (certFilter) {
    case 'issues': return !site.https || CERT_WARN_STATES.has(site.cert_status) || hasDnsIssue(site);
    case 'enabled': return !!site.https;
    case 'needs_https': return !site.https && !!site.managed && !site.imported;
    case 'ok': return site.cert_status === 'ok';
    case 'warn': return site.cert_status === 'warn';
    case 'missing': return site.cert_status === 'missing';
    case 'error': return site.cert_status === 'error';
    case 'dns_bad': return hasDnsIssue(site);
    case 'managed': return !!site.managed;
    default: return true;
  }
}
function getFilteredCertificates(){
  if(!state){ return []; }
  const query = String(certQuery || '').trim().toLowerCase();
  return state.sites.filter(site => isCertificateSite(site) && certificateMatchesFilter(site) && (!query || certificateSearchText(site).includes(query)));
}
function focusProblemSites(){
  siteFilter = 'problems';
  siteQuery = '';
  $('#siteFilter').value = 'problems';
  $('#siteSearch').value = '';
  switchView('sites');
  render();
}
function focusSite(domain){
  siteFilter = 'all';
  siteQuery = domain || '';
  $('#siteFilter').value = 'all';
  $('#siteSearch').value = siteQuery;
  switchView('sites');
  render();
}
function buildIssueItems(){
  const issues = [];
  for (const site of state.sites) {
    const domain = site.domain || '(默认站点)';
    if (site.backend_status === 'bad') {
      issues.push({kind:'backend', severity:'bad', domain, detail: site.backend_detail || '后端连接失败', site});
    }
    if (site.cert_status === 'warn') {
      issues.push({kind:'cert_warn', severity:'warn', domain, detail: site.cert_info || `证书剩余 ${site.cert_days ?? '-'} 天`, site});
    }
    if (site.cert_status === 'missing' || site.cert_status === 'error') {
      issues.push({kind:'cert_bad', severity:'bad', domain, detail: site.cert_info || '证书不可用', site});
    }
    if (hasDnsIssue(site)) {
      issues.push({kind:'dns', severity:'bad', domain, detail: site.dns_detail || 'DNS 未指向本机', site});
    }
  }
  return issues;
}
function issueLabel(issue){
  if (issue.kind === 'backend') return '后端异常';
  if (issue.kind === 'cert_warn') return '证书预警';
  if (issue.kind === 'cert_bad') return '证书异常';
  if (issue.kind === 'dns') return 'DNS 异常';
  return '问题';
}
function renderIssueRows(){
  const issues = buildIssueItems();
  $('#issueSummary').textContent = `共 ${issues.length} 项`;
  const rows = issues.map(issue => {
    const site = issue.site;
    const focusDomain = String(site.managed_domain || site.domain || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionDomain = String(site.managed_domain || site.domain || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    let actions = `<button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    if (issue.kind === 'dns' && site.importable) {
      const actionSource = String(site.source || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
      actions = `<button class="btn small primary" onclick="importSite('${actionDomain}', '${actionSource}')">先接管</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    } else if (issue.kind === 'dns' && (site.imported || site.migrated)) {
      actions = `<button class="btn small danger" onclick="removeImportedSite('${actionDomain}')">删除失效配置</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    } else if ((issue.kind === 'cert_warn' || issue.kind === 'cert_bad') && site.managed && !site.imported) {
      actions = site.https
        ? `<button class="btn small" onclick="disableSsl('${actionDomain}')">关闭HTTPS</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`
        : `<button class="btn small primary" onclick="enableSsl('${actionDomain}')">启用HTTPS</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    } else if (issue.kind === 'backend' && (site.imported || site.migrated)) {
      actions = `<button class="btn small danger" onclick="removeImportedSite('${actionDomain}')">删除失效配置</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    }
    const tagClass = issue.severity === 'warn' ? 'warn' : 'bad';
    return `<tr><td><strong>${escapeHtml(issue.domain)}</strong></td><td><span class="tag ${tagClass}">${issueLabel(issue)}</span></td><td>${escapeHtml(issue.detail)}<div class="muted">${escapeHtml(site.source || '-')}</div></td><td class="row">${actions}</td></tr>`;
  }).join('');
  $('#issueRows').innerHTML = rows || '<tr><td colspan="4" class="muted">当前没有需要处理的问题</td></tr>';
}
function renderProblemRows(){
  const problems = state.sites.filter(isProblemSite);
  $('#problemRows').innerHTML = problems.length ? problems.slice(0, 6).map(site => {
    const domain = site.domain || '(默认站点)';
    const target = site.upstream || site.root || '-';
    const jumpTarget = String(domain).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const tags = [];
    if(site.backend_status === 'bad') tags.push('<span class="tag bad">后端异常</span>');
    if(CERT_WARN_STATES.has(site.cert_status)) tags.push(`<span class="tag ${site.cert_status === 'warn' ? 'warn' : 'bad'}">证书异常</span>`);
    if(hasDnsIssue(site)) tags.push(dnsTagHtml(site));
    return `<div class="list-item"><div class="row"><div class="title">${escapeHtml(domain)}</div><span class="spacer"></span>${tags.join(' ')}</div><div class="meta">${escapeHtml(target)}</div><div class="actions"><button class="btn small" type="button" onclick="focusSite('${jumpTarget}')">定位到站点</button></div></div>`;
  }).join('') : '<div class="muted">当前没有需要优先处理的站点。</div>';
}
function renderCertificateRows(){
  const allCertificates = state.sites.filter(isCertificateSite);
  const filteredCertificates = getFilteredCertificates();
  $('#certSummary').textContent = `显示 ${filteredCertificates.length} / ${allCertificates.length}`;
  const rows = filteredCertificates.map(s => {
    const domain = s.domain || '(默认站点)';
    const actionDomain = String(s.managed_domain || domain).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionSource = String(s.source || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const focusDomain = String(s.managed_domain || domain).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const names = Array.isArray(s.names) && s.names.length ? s.names.join(', ') : domain;
    const target = s.upstream || s.root || '-';
    const owner = s.managed ? `<span class="tag ok">${s.migrated || !s.imported ? '受管' : '已接管'}</span>` : '<span class="tag">现有</span>';
    const dnsTag = dnsTagHtml(s);
    const statusTag = !s.https
      ? '<span class="tag">未启用HTTPS</span>'
      : (s.cert_status === 'ok'
        ? `<span class="tag ok">证书${s.cert_days ?? '-'}天</span>`
        : (s.cert_status === 'warn'
          ? `<span class="tag warn">证书${s.cert_days ?? '-'}天</span>`
          : (s.cert_status === 'critical'
            ? `<span class="tag bad">证书${s.cert_days ?? '-'}天</span>`
            : '<span class="tag bad">证书异常</span>')));
    const statusDetail = (s.https ? (s.cert_info || '已启用 HTTPS') : (s.managed && !s.imported ? '可直接申请证书' : '当前仅 HTTP')) + (s.dns_detail ? ` | DNS: ${s.dns_detail}` : '');
    let actions = `<button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    if (s.managed && !s.imported) {
      actions = s.https
        ? `<button class="btn small" onclick="viewCert('${actionDomain}')">查看详情</button><button class="btn small" onclick="renewCert('${actionDomain}')">续期</button><button class="btn small" onclick="disableSsl('${actionDomain}')">关闭HTTPS</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`
        : `<button class="btn small primary" onclick="enableSsl('${actionDomain}')">启用HTTPS</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    } else if (s.importable) {
      actions = `<button class="btn small primary" onclick="importSite('${actionDomain}', '${actionSource}')">先接管</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    } else if (s.imported) {
      actions = `<button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button><span class="muted">迁移后再改证书</span>`;
    }
    return `<tr><td><strong>${escapeHtml(domain)}</strong><div class="muted">${escapeHtml(names)}</div></td><td>${statusTag}<div class="muted">${escapeHtml(statusDetail)}</div></td><td>${owner} ${s.https ? '<span class="tag ok">HTTPS</span>' : '<span class="tag warn">HTTP</span>'} ${dnsTag}<div class="muted">${escapeHtml(s.kind || 'Nginx 服务')}</div><div class="muted">${escapeHtml(target)}</div></td><td>${escapeHtml(s.source || '-')}${s.cert_info ? `<div class="muted">${escapeHtml(s.cert_info)}</div>` : ''}${s.dns_detail ? `<div class="muted">DNS: ${escapeHtml(s.dns_detail)}</div>` : ''}</td><td class="row">${actions}</td></tr>`;
  }).join('');
  $('#certRows').innerHTML = rows || '<tr><td colspan="5" class="muted">没有匹配当前筛选条件的证书站点</td></tr>';
}
function render(){
  $('#nginxStatus').innerHTML = `<span class="tag ${state.nginx_active==='active'?'ok':'bad'}">${escapeHtml(state.nginx_active)}</span>`;
  $('#siteCount').textContent = state.sites.length;
  $('#bindInfo').textContent = state.bind + ':' + state.port;
  $('#managerInfo').textContent = state.manager_exists ? '已安装' : '缺失';
  $('#backendBadCount').textContent = state.sites.filter(s => s.backend_status === 'bad').length;
  $('#certWarnCount').textContent = state.sites.filter(s => CERT_WARN_STATES.has(s.cert_status)).length;
  $('#dnsBadCount').textContent = state.sites.filter(s => hasDnsIssue(s)).length;
  $('#serviceCount').textContent = state.services.length;
  renderProblemRows();
  renderIssueRows();
  renderCertificateRows();
  const filteredSites = getFilteredSites();
  $('#siteSummary').textContent = `显示 ${filteredSites.length} / ${state.sites.length}`;
  const rows = filteredSites.map(s => {
    const domain = s.domain || '(默认站点)';
    const actionDomain = String(s.managed_domain || domain).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionSource = String(s.source || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionTarget = String(s.upstream || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const names = Array.isArray(s.names) && s.names.length ? s.names.join(', ') : domain;
    const listen = Array.isArray(s.listen) && s.listen.length ? s.listen.join(', ') : '-';
    const target = s.upstream || s.root || '-';
    const owner = s.managed ? `<span class="tag ok">${s.migrated || !s.imported ? '受管' : '已接管'}</span>` : '<span class="tag">已有</span>';
    const https = s.https ? '<span class="tag ok">HTTPS</span>' : '<span class="tag warn">HTTP</span>';
    const backendTag = s.backend_status === 'ok' ? '<span class="tag ok">后端正常</span>' : (s.backend_status === 'bad' ? '<span class="tag bad">后端异常</span>' : '');
    const certTag = s.cert_status === 'ok'
      ? `<span class="tag ok">证书${s.cert_days ?? '-'}天</span>`
      : (s.cert_status === 'warn'
        ? `<span class="tag warn">证书${s.cert_days ?? '-'}天</span>`
        : (s.cert_status === 'critical'
          ? `<span class="tag bad">证书${s.cert_days ?? '-'}天</span>`
          : (s.cert_status === 'missing' || s.cert_status === 'error' ? '<span class="tag bad">证书异常</span>' : '')));
    const dnsTag = dnsTagHtml(s);
    let actions = '<span class="muted">只读</span>';
    if (s.migrated) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><button class="btn small danger" onclick="removeImportedSite('${actionDomain}')">删除</button>`;
    } else if (s.managed && !s.imported) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><button class="btn small" onclick="enableSsl('${actionDomain}')">启用HTTPS</button><button class="btn small" onclick="disableSsl('${actionDomain}')">关闭HTTPS</button><button class="btn small danger" onclick="removeSite('${actionDomain}')">删除</button>`;
    } else if (s.imported) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><button class="btn small" onclick="migrateSite('${actionDomain}')">迁移为受管</button><button class="btn small danger" onclick="removeImportedSite('${actionDomain}')">删除</button>`;
    } else if (s.importable) {
      actions = `<button class="btn small primary" onclick="importSite('${actionDomain}', '${actionSource}')">导入/接管</button>`;
    } else if (s.readonly_reason) {
      actions = `<span class="muted">${escapeHtml(s.readonly_reason)}</span>`;
    }
    return `<tr><td><strong>${escapeHtml(domain)}</strong><div class="muted">${escapeHtml(names)}</div></td><td>${escapeHtml(listen)}</td><td>${owner} ${https} ${backendTag} ${certTag} ${dnsTag}<div class="muted">${escapeHtml(s.kind || 'Nginx 服务')}</div></td><td>${escapeHtml(target)}${s.backend_detail ? `<div class="muted">${escapeHtml(s.backend_detail)}</div>` : ''}</td><td>${escapeHtml(s.source || '-')}${s.cert_info ? `<div class="muted">${escapeHtml(s.cert_info)}</div>` : ''}${s.dns_detail ? `<div class="muted">DNS: ${escapeHtml(s.dns_detail)}</div>` : ''}</td><td class="row">${actions}</td></tr>`;
  }).join('');
  $('#siteRows').innerHTML = rows || '<tr><td colspan="6" class="muted">没有匹配当前筛选条件的站点</td></tr>';
  const serviceRows = state.services.map(s => {
    const target = `${s.host}:${s.port}`;
    const actionTarget = String(target).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return `<tr><td>${escapeHtml(s.host)}</td><td>${escapeHtml(String(s.port))}</td><td>${escapeHtml(s.process)}</td><td><span class="tag ${s.exposed ? 'warn' : 'ok'}">${s.exposed ? '公网监听' : '本机监听'}</span></td><td>${escapeHtml(target)}</td><td class="row"><button class="btn small primary" onclick="useService('${actionTarget}')">用于反代</button></td></tr>`;
  }).join('');
  $('#serviceRows').innerHTML = serviceRows || '<tr><td colspan="6" class="muted">当前没有发现适合反代的本机监听服务</td></tr>';
}
async function action(path, body){ $('#output').textContent='执行中...'; const data = await api(path,{method:'POST',body:JSON.stringify(body||{})}); $('#output').textContent = data.output || '完成'; showMsg(data.message || '操作完成','ok'); await load(); }
async function enableSsl(domain){ const email = prompt('证书邮箱，可留空'); await action('/api/sites/enable-ssl',{domain,email:email||''}); }
async function editSite(domain, currentTarget){ const target = prompt('新的后端地址，例如 127.0.0.1:3002 或 http://127.0.0.1:3002', currentTarget || ''); if(target) await action('/api/sites/update',{domain,target}); }
async function disableSsl(domain){ if(confirm('确认关闭 HTTPS？')) await action('/api/sites/disable-ssl',{domain}); }
async function removeSite(domain){ if(confirm('确认删除站点？')) await action('/api/sites/remove',{domain, delete_cert:false}); }
async function removeImportedSite(domain){ if(confirm('确认删除这个导入/迁移的站点？\n\n操作将：\n1. 删除状态文件\n2. 自动注释原配置（如果可以定位）\n3. 创建备份文件\n\n建议删除前先检查该站点是否还在使用。')) await action('/api/sites/remove-imported',{domain, comment_out:true}); }
async function importSite(domain, source){ if(confirm('确认导入这个已有反向代理站点？导入不会删除原 nginx 配置。')) await action('/api/sites/import',{domain, source}); }
async function migrateSite(domain){ if(confirm('确认将这个已接管站点迁移为工具受管配置？会备份并注释原始配置块。')) await action('/api/sites/migrate',{domain}); }
async function renewCert(domain){ if(confirm('确认续期该域名的证书？\n\n这将重新向 Let\'s Encrypt 申请证书，通常在证书即将过期时使用。')) await action('/api/certs/renew',{domain}); }
async function viewCert(domain){
  try {
    const data = await api('/api/certs/detail?domain=' + encodeURIComponent(domain));
    showCertDetail(data);
  } catch(err) {
    showMsg(err.message, 'bad');
  }
}
function showCertDetail(data) {
  const modal = document.getElementById('certModal');
  const title = document.getElementById('certModalTitle');
  const content = document.getElementById('certModalContent');

  title.textContent = `证书详情 - ${data.domain}`;

  if (data.status === 'error' || data.status === 'missing') {
    content.innerHTML = `<div class="notice" style="border-left-color:var(--red);background:#fdecec;color:var(--red);">${escapeHtml(data.error || '证书不可用')}</div>`;
  } else {
    const statusColors = {ok:'var(--green)', warn:'var(--amber)', critical:'var(--red)'};
    const statusLabels = {ok:'正常', warn:'即将过期', critical:'紧急'};
    content.innerHTML = `
      <div class="cert-detail-grid">
        <div class="cert-detail-row"><div class="cert-detail-label">状态</div><div><span class="tag ${data.status}">${statusLabels[data.status] || data.status}</span> 剩余 ${data.days_left} 天</div></div>
        <div class="cert-detail-row"><div class="cert-detail-label">有效期</div><div>${escapeHtml(data.not_before)} 至 ${escapeHtml(data.not_after)}</div></div>
        <div class="cert-detail-row"><div class="cert-detail-label">颁发者</div><div style="word-break:break-all;">${escapeHtml(data.issuer)}</div></div>
        <div class="cert-detail-row"><div class="cert-detail-label">使用者</div><div style="word-break:break-all;">${escapeHtml(data.subject)}</div></div>
        <div class="cert-detail-row"><div class="cert-detail-label">序列号</div><div style="font-family:monospace;">${escapeHtml(data.serial)}</div></div>
        <div class="cert-detail-row"><div class="cert-detail-label">SAN</div><div>${data.san && data.san.length ? data.san.map(escapeHtml).join(', ') : '-'}</div></div>
        <div class="cert-detail-row"><div class="cert-detail-label">证书路径</div><div style="word-break:break-all;font-family:monospace;font-size:12px;">${escapeHtml(data.cert_path)}</div></div>
      </div>
    `;
  }

  modal.classList.add('active');
}
function closeCertModal() {
  document.getElementById('certModal').classList.remove('active');
}
function useService(target){ document.querySelector('#createForm [name="upstream"]').value = target; document.querySelector('#createForm [name="scheme"]').value = 'http'; switchView('create'); }
$('#logoutBtn').onclick = async()=>{ await api('/api/logout',{method:'POST',body:'{}'}); window.location.replace('/login'); };
$('#refreshBtn').onclick = ()=>load().catch(e=>showMsg(e.message,'bad'));
$('#testBtn').onclick = ()=>action('/api/nginx/test',{}).catch(e=>showMsg(e.message,'bad'));
$('#reloadBtn').onclick = ()=>action('/api/nginx/reload',{}).catch(e=>showMsg(e.message,'bad'));
$('#problemJumpBtn').onclick = ()=>focusProblemSites();
$('#siteSearch').addEventListener('input', e => { siteQuery = e.target.value; render(); });
$('#siteFilter').addEventListener('change', e => { siteFilter = e.target.value; render(); });
$('#siteSearchClear').onclick = () => { siteQuery = ''; siteFilter = 'all'; $('#siteSearch').value = ''; $('#siteFilter').value = 'all'; render(); };
$('#certSearch').addEventListener('input', e => { certQuery = e.target.value; render(); });
$('#certFilter').addEventListener('change', e => { certFilter = e.target.value; render(); });
$('#certSearchClear').onclick = () => { certQuery = ''; certFilter = 'all'; $('#certSearch').value = ''; $('#certFilter').value = 'all'; render(); };
$('#createForm').addEventListener('submit', async e => { e.preventDefault(); const f = new FormData(e.target); const body = {domain:f.get('domain'), upstream:f.get('upstream'), scheme:f.get('scheme'), email:f.get('email'), ssl:f.has('ssl'), body:f.get('body'), readTimeout:f.get('readTimeout'), backendInsecure:f.has('backendInsecure')}; try { await action('/api/sites/add', body); e.target.reset(); } catch(err){ showMsg(err.message,'bad'); $('#output').textContent = err.message; } });
document.querySelectorAll('.nav button,[data-jump]').forEach(b => b.onclick = () => switchView(b.dataset.view||b.dataset.jump));
document.getElementById('certModal').onclick = (e) => { if(e.target.id === 'certModal') closeCertModal(); };
load().catch(e=>showMsg(e.message,'bad'));
</script>
</body>
</html>'''


def sign_session(ts: str) -> str:
    return hmac.new(SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()


def parse_state_file(path: pathlib.Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value
    except OSError:
        pass
    return data


def list_managed_sites() -> list[dict[str, str]]:
    if not STATE_DIR.exists():
        return []
    sites = [parse_state_file(p) for p in sorted(STATE_DIR.glob("*.env"))]
    return [s for s in sites if s.get("DOMAIN")]


def split_directive_values(value: str) -> list[str]:
    return [part for part in value.strip().split() if part and part != "_"]


def split_proxy_upstream(upstream: str) -> tuple[str, str]:
    match = re.match(r"^(https?)://([^/]+)$", upstream.strip())
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def parse_edit_target(target: str) -> tuple[str, str]:
    target = target.strip()
    if target.startswith(("http://", "https://")):
        return split_proxy_upstream(target)
    if re.match(r"^[^/:]+:\d+$", target):
        return "http", target
    return "", ""


def check_backend_target(target: str) -> tuple[str, str]:
    if not target or "$" in target:
        return "unknown", "特殊目标"
    match = re.match(r"^([^:]+):(\d+)$", target)
    if not match:
        return "unknown", "格式未知"
    host = match.group(1)
    port = int(match.group(2))
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return "ok", f"{host}:{port}"
    except OSError as exc:
        return "bad", str(exc)


def read_certificate_detail(cert_path: str) -> dict[str, object]:
    """读取证书的详细信息，包括颁发者、使用者、SAN等"""
    if not cert_path:
        return {"status": "none", "error": "未提供证书路径"}

    path = pathlib.Path(cert_path)
    if not path.is_file():
        return {"status": "missing", "error": f"证书文件不存在: {cert_path}"}

    try:
        info = ssl._ssl._test_decode_cert(str(path))
        expires = datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        not_before = datetime.strptime(info["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = max(0, int((expires - datetime.now(timezone.utc)).total_seconds() // 86400))

        # 解析 SAN (Subject Alternative Names)
        san_list = []
        if "subjectAltName" in info:
            for item in info["subjectAltName"]:
                if item[0] == "DNS":
                    san_list.append(item[1])

        # 解析颁发者
        issuer_parts = []
        if "issuer" in info:
            for item in info["issuer"]:
                for part in item:
                    issuer_parts.append(f"{part[0]}={part[1]}")
        issuer = ", ".join(issuer_parts) if issuer_parts else "Unknown"

        # 解析使用者
        subject_parts = []
        if "subject" in info:
            for item in info["subject"]:
                for part in item:
                    subject_parts.append(f"{part[0]}={part[1]}")
        subject = ", ".join(subject_parts) if subject_parts else "Unknown"

        # 证书状态
        if days_left <= CERT_CRITICAL_DAYS:
            cert_status = "critical"
        elif days_left <= CERT_WARN_DAYS:
            cert_status = "warn"
        else:
            cert_status = "ok"

        return {
            "status": cert_status,
            "days_left": days_left,
            "not_before": not_before.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "not_after": expires.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "issuer": issuer,
            "subject": subject,
            "san": san_list,
            "serial": info.get("serialNumber", "Unknown"),
            "version": info.get("version", "Unknown"),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def read_certificate_status(cert_path: str) -> tuple[str, int | None, str]:
    if not cert_path:
        return "none", None, ""
    path = pathlib.Path(cert_path)
    if not path.is_file():
        return "missing", None, cert_path
    try:
        info = ssl._ssl._test_decode_cert(str(path))
        expires = datetime.strptime(info["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = max(0, int((expires - datetime.now(timezone.utc)).total_seconds() // 86400))

        # 使用新的阈值判断
        if days_left <= CERT_CRITICAL_DAYS:
            status = "critical"
        elif days_left <= CERT_WARN_DAYS:
            status = "warn"
        else:
            status = "ok"

        return status, days_left, expires.strftime("%Y-%m-%d")
    except Exception:
        return "error", None, cert_path


def normalize_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return ""


def list_local_ip_addresses() -> list[str]:
    local_ips: list[str] = []
    seen: set[str] = set()

    def add_ip(candidate: str) -> None:
        ip = normalize_ip(candidate)
        if not ip or ip in {"127.0.0.1", "::1"} or ip in seen:
            return
        seen.add(ip)
        local_ips.append(ip)

    result = run_cmd(["hostname", "-I"], timeout=10)
    if result["code"] == 0:
        for part in str(result["output"]).split():
            add_ip(part)

    if not local_ips:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, type=socket.SOCK_STREAM):
                sockaddr = info[4]
                if sockaddr:
                    add_ip(str(sockaddr[0]))
        except OSError:
            pass

    return local_ips


def check_domain_dns(domain: str, local_ips: Optional[list[str]] = None) -> tuple[str, list[str], str]:
    if not DOMAIN_RE.match(domain):
        return "none", [], ""

    local_ips = list(local_ips or list_local_ip_addresses())
    resolved: list[str] = []
    seen: set[str] = set()
    try:
        for info in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM):
            sockaddr = info[4]
            if not sockaddr:
                continue
            ip = normalize_ip(str(sockaddr[0]))
            if not ip or ip in seen:
                continue
            seen.add(ip)
            resolved.append(ip)
    except OSError as exc:
        return "error", [], str(exc)

    if not resolved:
        return "bad", [], "未解析到 A/AAAA 记录"
    if local_ips and any(ip in local_ips for ip in resolved):
        return "ok", resolved, ", ".join(resolved)
    if local_ips:
        return "bad", resolved, f"当前解析: {', '.join(resolved)}; 本机: {', '.join(local_ips)}"
    return "warn", resolved, ", ".join(resolved)


def enrich_server_runtime(server: dict[str, object], local_ips: Optional[list[str]] = None) -> dict[str, object]:
    backend_status = "unknown"
    backend_detail = ""
    if server.get("kind") == "反向代理":
        target = str(server.get("upstream_target") or "")
        if not target:
            _, target = split_proxy_upstream(str(server.get("upstream") or ""))
        backend_status, backend_detail = check_backend_target(target)

    if local_ips is None:
        local_ips = list_local_ip_addresses()

    cert_path = str(server.get("ssl_cert_path") or "")
    if server.get("https") and not cert_path and DOMAIN_RE.match(str(server.get("domain") or "")):
        cert_path = f"/etc/letsencrypt/live/{server['domain']}/fullchain.pem"
    cert_status, cert_days, cert_info = read_certificate_status(cert_path) if server.get("https") else ("none", None, "")
    dns_status, dns_ips, dns_detail = check_domain_dns(str(server.get("domain") or ""), local_ips)

    return {
        **server,
        "backend_status": backend_status,
        "backend_detail": backend_detail,
        "cert_status": cert_status,
        "cert_days": cert_days,
        "cert_info": cert_info,
        "dns_status": dns_status,
        "dns_ips": dns_ips,
        "dns_detail": dns_detail,
    }


def list_local_services() -> list[dict[str, object]]:
    result = run_cmd(["ss", "-lntpH"], timeout=10)
    if result["code"] != 0:
        return []

    services: list[dict[str, object]] = []
    seen: set[tuple[str, int, str]] = set()
    for line in str(result["output"]).splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[3]
        process_info = parts[5] if len(parts) > 5 else ""
        if local_addr.startswith("["):
            host, _, port_text = local_addr.rpartition(":")
            host = host.strip("[]")
        else:
            host, _, port_text = local_addr.rpartition(":")
        if not port_text.isdigit():
            continue
        port = int(port_text)
        if port in {80, 443, 8098}:
            continue
        proc_match = re.search(r'\("([^\"]+)"', process_info)
        process = proc_match.group(1) if proc_match else (process_info or "unknown")
        exposed = host in {"*", "0.0.0.0", "::"}
        normalized_host = "127.0.0.1" if host in {"*", "0.0.0.0", "::", "::1", "[::]"} else host
        key = (normalized_host, port, process)
        if key in seen:
            continue
        seen.add(key)
        services.append({
            "host": normalized_host,
            "port": port,
            "process": process,
            "exposed": exposed,
        })

    services.sort(key=lambda item: (item["host"] != "127.0.0.1", item["port"], item["process"]))
    return services


def parse_server_block(block: list[str], source: str, managed_by_domain: dict[str, dict[str, str]]) -> dict[str, object]:
    names: list[str] = []
    listens: list[str] = []
    proxy_passes: list[str] = []
    roots: list[str] = []
    has_ssl_cert = False
    ssl_cert_path = ""

    for raw in block:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.match(r"^server_name\s+(.+?);", line)
        if match:
            names.extend(split_directive_values(match.group(1)))
            continue
        match = re.match(r"^listen\s+(.+?);", line)
        if match:
            listens.append(match.group(1).strip())
            continue
        match = re.match(r"^proxy_pass\s+(.+?);", line)
        if match:
            proxy_passes.append(match.group(1).strip())
            continue
        match = re.match(r"^root\s+(.+?);", line)
        if match:
            roots.append(match.group(1).strip())
            continue
        match = re.match(r"^ssl_certificate\s+(.+?);", line)
        if match:
            has_ssl_cert = True
            ssl_cert_path = match.group(1).strip()

    managed_domain = next((name for name in names if name in managed_by_domain), "")
    managed_state = managed_by_domain.get(managed_domain, {}) if managed_domain else {}
    managed = bool(managed_domain or re.search(r"/vpspm-[^/]+\.conf$", source))
    display_name = managed_domain or (names[0] if names else "(默认站点)")
    upstream = proxy_passes[0] if proxy_passes else ""
    https = has_ssl_cert or any("ssl" in item or ":443" in item or item.startswith("443") for item in listens)
    import_domain = next((name for name in names if DOMAIN_RE.match(name)), "")
    upstream_scheme, upstream_target = split_proxy_upstream(upstream)

    if proxy_passes:
        kind = "反向代理"
    elif roots:
        kind = "静态站点"
    else:
        kind = "Nginx 服务"

    importable = bool(
        not managed
        and kind == "反向代理"
        and import_domain
        and upstream_scheme
        and upstream_target
        and "$" not in upstream
    )
    if importable:
        readonly_reason = ""
    elif managed:
        readonly_reason = ""
    elif kind != "反向代理":
        readonly_reason = "特殊配置"
    elif not import_domain:
        readonly_reason = "特殊配置"
    elif "$" in upstream:
        readonly_reason = "变量代理"
    else:
        readonly_reason = "只读"

    return {
        "domain": display_name,
        "names": names,
        "listen": listens,
        "upstream": upstream,
        "root": roots[0] if roots else "",
        "kind": kind,
        "https": https,
        "managed": managed,
        "imported": managed_state.get("IMPORTED") == "1",
        "migrated": managed_state.get("MIGRATED") == "1",
        "importable": importable,
        "readonly_reason": readonly_reason,
        "source": source,
        "managed_domain": managed_domain,
        "upstream_scheme": upstream_scheme,
        "upstream_target": upstream_target,
        "ssl_cert_path": ssl_cert_path,
    }


def list_nginx_servers() -> list[dict[str, object]]:
    managed_sites = list_managed_sites()
    managed_by_domain = {str(site.get("DOMAIN", "")): site for site in managed_sites}
    local_ips = list_local_ip_addresses()
    dump = run_cmd(["nginx", "-T"], timeout=20)
    if dump["code"] != 0:
        return [enrich_server_runtime({
            "domain": site.get("DOMAIN", ""),
            "names": [site.get("DOMAIN", "")],
            "listen": [],
            "upstream": f"{site.get('UPSTREAM_SCHEME', 'http')}://{site.get('UPSTREAM', '')}",
            "root": "",
            "kind": "反向代理",
            "https": site.get("ENABLE_SSL") == "1",
            "managed": True,
            "imported": site.get("IMPORTED") == "1",
            "migrated": site.get("MIGRATED") == "1",
            "importable": False,
            "readonly_reason": "",
            "source": "状态文件，nginx -T 读取失败",
            "managed_domain": site.get("DOMAIN", ""),
            "upstream_scheme": site.get("UPSTREAM_SCHEME", "http"),
            "upstream_target": site.get("UPSTREAM", ""),
            "ssl_cert_path": f"/etc/letsencrypt/live/{site.get('DOMAIN', '')}/fullchain.pem" if site.get("ENABLE_SSL") == "1" else "",
        }, local_ips) for site in managed_sites]

    servers: list[dict[str, object]] = []
    current_file = "nginx -T"
    block: list[str] = []
    depth = 0

    for line in str(dump["output"]).splitlines():
        if line.startswith("# configuration file "):
            current_file = line[len("# configuration file "):].rstrip(":")
            continue
        if depth == 0 and re.match(r"^\s*server\s*\{", line):
            block = [line]
            depth = line.count("{") - line.count("}")
            if depth == 0:
                servers.append(parse_server_block(block, current_file, managed_by_domain))
                block = []
            continue
        if depth > 0:
            block.append(line)
            depth += line.count("{") - line.count("}")
            if depth == 0:
                servers.append(parse_server_block(block, current_file, managed_by_domain))
                block = []

    seen_managed = {str(server.get("managed_domain")) for server in servers if server.get("managed_domain")}
    for site in managed_sites:
        domain = site.get("DOMAIN", "")
        if domain and domain not in seen_managed:
            servers.append(enrich_server_runtime({
                "domain": domain,
                "names": [domain],
                "listen": [],
                "upstream": f"{site.get('UPSTREAM_SCHEME', 'http')}://{site.get('UPSTREAM', '')}",
                "root": "",
                "kind": "反向代理",
                "https": site.get("ENABLE_SSL") == "1",
                "managed": True,
                "imported": site.get("IMPORTED") == "1",
                "migrated": site.get("MIGRATED") == "1",
                "importable": False,
                "readonly_reason": "",
                "source": "状态文件，当前 nginx 配置未发现",
                "managed_domain": domain,
                "upstream_scheme": site.get("UPSTREAM_SCHEME", "http"),
                "upstream_target": site.get("UPSTREAM", ""),
                "ssl_cert_path": f"/etc/letsencrypt/live/{domain}/fullchain.pem" if site.get("ENABLE_SSL") == "1" else "",
            }, local_ips))

    return [enrich_server_runtime(server, local_ips) for server in servers]


def write_managed_state(domain: str, values: dict[str, str]) -> pathlib.Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / f"{domain}.env"
    lines = [
        f"DOMAIN={domain}",
        f"UPSTREAM={values.get('UPSTREAM', '')}",
        f"UPSTREAM_SCHEME={values.get('UPSTREAM_SCHEME', 'http')}",
        f"ENABLE_SSL={values.get('ENABLE_SSL', '0')}",
        f"CERTBOT_EMAIL={values.get('CERTBOT_EMAIL', '')}",
        f"CLIENT_MAX_BODY_SIZE={values.get('CLIENT_MAX_BODY_SIZE', '64m')}",
        f"PROXY_READ_TIMEOUT={values.get('PROXY_READ_TIMEOUT', '300s')}",
        f"PROXY_SEND_TIMEOUT={values.get('PROXY_SEND_TIMEOUT', '300s')}",
        f"WEBSOCKET={values.get('WEBSOCKET', '1')}",
        f"BACKEND_INSECURE={values.get('BACKEND_INSECURE', '0')}",
        f"IMPORTED={values.get('IMPORTED', '0')}",
        f"MIGRATED={values.get('MIGRATED', '0')}",
    ]
    if values.get("IMPORTED_SOURCE"):
        lines.append(f"IMPORTED_SOURCE={values['IMPORTED_SOURCE']}")
    if values.get("MIGRATED_FROM"):
        lines.append(f"MIGRATED_FROM={values['MIGRATED_FROM']}")
    lines.append("")
    state_path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(state_path, 0o600)
    return state_path


def import_existing_site(domain: str, source: str) -> dict[str, object]:
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        return {"code": 2, "output": "域名无效，不能导入"}

    servers = list_nginx_servers()
    server = next((item for item in servers if item.get("domain") == domain and item.get("source") == source), None)
    if not server:
        return {"code": 3, "output": "未找到对应 nginx 站点，请刷新后重试"}
    if not server.get("importable"):
        return {"code": 4, "output": f"该站点属于特殊配置或已受管，不能自动接管：{server.get('readonly_reason') or '只读'}"}

    upstream_scheme = str(server.get("upstream_scheme") or "")
    upstream_target = str(server.get("upstream_target") or "")
    if upstream_scheme not in {"http", "https"} or not re.match(r"^[^/:]+:\d+$", upstream_target):
        return {"code": 5, "output": "只支持导入明确的 http/https HOST:PORT 反向代理"}

    write_managed_state(domain, {
        "UPSTREAM": upstream_target,
        "UPSTREAM_SCHEME": upstream_scheme,
        "ENABLE_SSL": "1" if server.get("https") else "0",
        "CERTBOT_EMAIL": "",
        "CLIENT_MAX_BODY_SIZE": "64m",
        "PROXY_READ_TIMEOUT": "300s",
        "PROXY_SEND_TIMEOUT": "300s",
        "WEBSOCKET": "1",
        "BACKEND_INSECURE": "0",
        "IMPORTED": "1",
        "MIGRATED": "0",
        "IMPORTED_SOURCE": source,
    })
    return {"code": 0, "output": f"已导入：{domain} -> {upstream_scheme}://{upstream_target}"}


def find_imported_server_block(lines: list[str], domain: str) -> tuple[int, int]:
    block_start = -1
    depth = 0
    block: list[str] = []
    for index, line in enumerate(lines):
        if depth == 0 and re.match(r"^\s*server\s*\{", line):
            block_start = index
            block = [line]
            depth = line.count("{") - line.count("}")
            continue
        if depth > 0:
            block.append(line)
            depth += line.count("{") - line.count("}")
            if depth == 0:
                names = []
                proxy_passes = []
                for raw in block:
                    stripped = raw.split("#", 1)[0].strip()
                    name_match = re.match(r"^server_name\s+(.+?);", stripped)
                    if name_match:
                        names.extend(split_directive_values(name_match.group(1)))
                    proxy_match = re.match(r"^proxy_pass\s+(.+?);", stripped)
                    if proxy_match:
                        proxy_passes.append(proxy_match.group(1).strip())
                if domain in names and len(proxy_passes) == 1 and split_proxy_upstream(proxy_passes[0])[0]:
                    return block_start, index
                block = []
                block_start = -1
    return -1, -1


def update_imported_site(domain: str, target: str) -> dict[str, object]:
    domain = domain.strip().lower()
    scheme, upstream = parse_edit_target(target)
    if not DOMAIN_RE.match(domain) or not scheme or not upstream:
        return {"code": 2, "output": "域名或后端地址无效"}

    state_path = STATE_DIR / f"{domain}.env"
    state = parse_state_file(state_path)
    if state.get("IMPORTED") != "1" and state.get("MIGRATED") != "1":
        return {"code": 3, "output": "该站点不是导入/迁移站点"}

    source = state.get("IMPORTED_SOURCE", "")
    if not source:
        server = next((item for item in list_nginx_servers() if item.get("domain") == domain), None)
        source = str(server.get("source", "")) if server else ""
    source_path = pathlib.Path(source)
    if not source_path.is_file() or not str(source_path).startswith("/etc/nginx/"):
        return {"code": 4, "output": "找不到可编辑的原始 nginx 配置文件"}

    lines = source_path.read_text(encoding="utf-8").splitlines()
    start, end = find_imported_server_block(lines, domain)
    if start < 0:
        return {"code": 5, "output": "未能在原配置里定位到可安全编辑的反向代理块"}

    changed = False
    for index in range(start, end + 1):
        match = re.match(r"^(\s*)proxy_pass\s+.+?;(\s*)$", lines[index])
        if match:
            lines[index] = f"{match.group(1)}proxy_pass {scheme}://{upstream};{match.group(2)}"
            changed = True
            break
    if not changed:
        return {"code": 6, "output": "未找到 proxy_pass，无法编辑"}

    backup_path = source_path.with_name(f"{source_path.name}.bak-{int(time.time())}")
    original = source_path.read_text(encoding="utf-8")
    backup_path.write_text(original, encoding="utf-8")
    source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    test = run_cmd(["nginx", "-t"], timeout=20)
    if test["code"] != 0:
        source_path.write_text(original, encoding="utf-8")
        return {"code": 7, "output": f"nginx 配置校验失败，已回滚。\n{test['output']}"}
    reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=20)
    if reload_result["code"] != 0:
        reload_result = run_cmd(["nginx", "-s", "reload"], timeout=20)

    write_managed_state(domain, {
        **state,
        "UPSTREAM": upstream,
        "UPSTREAM_SCHEME": scheme,
        "IMPORTED_SOURCE": str(source_path),
    })
    output = f"已更新原 nginx 配置：{domain} -> {scheme}://{upstream}\n备份：{backup_path}\n{reload_result['output']}".strip()
    return {"code": reload_result["code"], "output": output}


def migrate_imported_site(domain: str) -> dict[str, object]:
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        return {"code": 2, "output": "域名无效"}

    state_path = STATE_DIR / f"{domain}.env"
    state = parse_state_file(state_path)
    if state.get("IMPORTED") != "1":
        return {"code": 3, "output": "该站点不是已导入状态，不能迁移"}

    source = state.get("IMPORTED_SOURCE", "")
    source_path = pathlib.Path(source)
    if not source_path.is_file() or not str(source_path).startswith("/etc/nginx/"):
        return {"code": 4, "output": "找不到可迁移的原始 nginx 配置文件"}

    lines = source_path.read_text(encoding="utf-8").splitlines()
    start, end = find_imported_server_block(lines, domain)
    if start < 0:
        return {"code": 5, "output": "未能在原配置里定位到可迁移的反向代理块"}

    available_path = pathlib.Path(f"/etc/nginx/sites-available/vpspm-{domain}.conf")
    enabled_path = pathlib.Path(f"/etc/nginx/sites-enabled/vpspm-{domain}.conf")
    if available_path.exists() or enabled_path.exists():
        return {"code": 6, "output": "目标受管配置已存在，请先检查是否已经迁移过"}

    block_lines = lines[start:end + 1]
    migrated_lines = lines[:]
    for index in range(start, end + 1):
        migrated_lines[index] = f"# migrated by host-nginx-manager: {lines[index]}"

    original = source_path.read_text(encoding="utf-8")
    backup_path = source_path.with_name(f"{source_path.name}.bak-{int(time.time())}")
    backup_path.write_text(original, encoding="utf-8")
    source_path.write_text("\n".join(migrated_lines) + "\n", encoding="utf-8")

    available_path.parent.mkdir(parents=True, exist_ok=True)
    enabled_path.parent.mkdir(parents=True, exist_ok=True)
    available_path.write_text("\n".join(block_lines) + "\n", encoding="utf-8")
    os.chmod(available_path, 0o644)
    if enabled_path.exists() or enabled_path.is_symlink():
        enabled_path.unlink()
    enabled_path.symlink_to(available_path)

    test = run_cmd(["nginx", "-t"], timeout=20)
    if test["code"] != 0:
        source_path.write_text(original, encoding="utf-8")
        if enabled_path.exists() or enabled_path.is_symlink():
            enabled_path.unlink()
        if available_path.exists():
            available_path.unlink()
        return {"code": 7, "output": f"迁移后的 nginx 配置校验失败，已回滚。\n{test['output']}"}

    reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=20)
    if reload_result["code"] != 0:
        reload_result = run_cmd(["nginx", "-s", "reload"], timeout=20)

    write_managed_state(domain, {
        **state,
        "IMPORTED": "0",
        "MIGRATED": "1",
        "IMPORTED_SOURCE": str(available_path),
        "MIGRATED_FROM": str(source_path),
    })
    output = f"已迁移为受管站点：{domain}\n原配置备份：{backup_path}\n新配置：{available_path}\n{reload_result['output']}".strip()
    return {"code": reload_result["code"], "output": output}


def remove_imported_site(domain: str, comment_out: bool = True) -> dict[str, object]:
    """删除已导入的站点配置，可选择注释或完全删除原配置"""
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        return {"code": 2, "output": "域名无效"}

    state_path = STATE_DIR / f"{domain}.env"
    if not state_path.exists():
        return {"code": 3, "output": "状态文件不存在"}

    state = parse_state_file(state_path)
    is_imported = state.get("IMPORTED") == "1"
    is_migrated = state.get("MIGRATED") == "1"

    if not is_imported and not is_migrated:
        return {"code": 4, "output": "该站点不是导入/迁移站点，请使用标准删除功能"}

    # 删除状态文件
    state_path.unlink()

    source = state.get("IMPORTED_SOURCE", "")
    if not source:
        return {"code": 0, "output": f"已删除状态文件：{domain}\n原始配置文件未知，请手动检查 nginx 配置"}

    source_path = pathlib.Path(source)

    # 如果是迁移后的受管配置，直接删除
    if is_migrated and str(source_path).startswith("/etc/nginx/sites-"):
        try:
            available_path = pathlib.Path(f"/etc/nginx/sites-available/vpspm-{domain}.conf")
            enabled_path = pathlib.Path(f"/etc/nginx/sites-enabled/vpspm-{domain}.conf")

            if enabled_path.exists() or enabled_path.is_symlink():
                enabled_path.unlink()
            if available_path.exists():
                available_path.unlink()

            test = run_cmd(["nginx", "-t"], timeout=20)
            if test["code"] != 0:
                return {"code": 5, "output": f"删除受管配置后 nginx 校验失败。\n{test['output']}"}

            reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=20)
            if reload_result["code"] != 0:
                reload_result = run_cmd(["nginx", "-s", "reload"], timeout=20)

            return {"code": 0, "output": f"已删除站点：{domain}\n已删除：{available_path}\n{reload_result['output']}".strip()}
        except Exception as exc:
            return {"code": 6, "output": f"删除受管配置时出错：{exc}"}

    # 对于导入的配置，尝试注释掉原始配置
    if not source_path.is_file():
        return {"code": 0, "output": f"已删除状态文件：{domain}\n原配置文件不存在：{source}"}

    if not str(source_path).startswith("/etc/nginx/"):
        return {"code": 7, "output": f"原配置文件不在 /etc/nginx/ 目录，不自动修改：{source}"}

    # 尝试找到并注释配置块
    lines = source_path.read_text(encoding="utf-8").splitlines()
    start, end = find_imported_server_block(lines, domain)

    if start < 0:
        # 可能是在 stream 块或其他位置，尝试简单搜索
        found_lines = []
        for idx, line in enumerate(lines):
            if domain in line:
                found_lines.append(f"行 {idx + 1}: {line.strip()}")

        hint = "\n".join(found_lines[:5]) if found_lines else "未找到"
        return {"code": 0, "output": f"已删除状态文件：{domain}\n\n无法自动定位配置块，请手动编辑：{source}\n可能的位置：\n{hint}"}

    if comment_out:
        # 注释掉配置块
        commented_lines = lines[:]
        for index in range(start, end + 1):
            if not lines[index].strip().startswith("#"):
                commented_lines[index] = f"# REMOVED: {lines[index]}"

        backup_path = source_path.with_name(f"{source_path.name}.bak-{int(time.time())}")
        original = source_path.read_text(encoding="utf-8")
        backup_path.write_text(original, encoding="utf-8")
        source_path.write_text("\n".join(commented_lines) + "\n", encoding="utf-8")

        test = run_cmd(["nginx", "-t"], timeout=20)
        if test["code"] != 0:
            source_path.write_text(original, encoding="utf-8")
            return {"code": 8, "output": f"注释配置后 nginx 校验失败，已回滚。\n{test['output']}"}

        reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=20)
        if reload_result["code"] != 0:
            reload_result = run_cmd(["nginx", "-s", "reload"], timeout=20)

        return {"code": 0, "output": f"已删除站点：{domain}\n已注释原配置：{source} (行 {start+1}-{end+1})\n备份：{backup_path}\n{reload_result['output']}".strip()}
    else:
        return {"code": 0, "output": f"已删除状态文件：{domain}\n请手动编辑配置文件删除相关配置：{source} (行 {start+1}-{end+1})"}


def run_cmd(args: list[str], timeout: int = 90) -> dict[str, object]:
    try:
        proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return {"code": proc.returncode, "output": proc.stdout.strip()}
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return {"code": 124, "output": f"命令执行超时。\n{output}".strip()}
    except OSError as exc:
        return {"code": 127, "output": f"命令执行失败：{exc}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "HostNginxWeb/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def send_json(self, data: object, status: int = 200, headers: Optional[dict[str, str]] = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def authenticated(self) -> bool:
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        if not morsel:
            return False
        try:
            ts, sig = morsel.value.split(":", 1)
            if time.time() - int(ts) > SESSION_TTL:
                return False
            return hmac.compare_digest(sig, sign_session(ts))
        except Exception:
            return False

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_json({"error": "未登录"}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/login":
            if self.authenticated():
                self.redirect("/")
                return
            self.send_html(LOGIN_HTML)
            return
        if path == "/":
            if not self.authenticated():
                self.redirect("/login")
                return
            self.send_html(APP_HTML)
            return
        if path == "/api/status":
            if not self.require_auth():
                return
            active = run_cmd(["systemctl", "is-active", "nginx"], timeout=10)
            self.send_json({
                "sites": list_nginx_servers(),
                "services": list_local_services(),
                "nginx_active": (active["output"] or "unknown"),
                "manager_exists": pathlib.Path(MANAGER_BIN).exists(),
                "bind": BIND,
                "port": PORT,
            })
            return

        if path.startswith("/api/certs/detail"):
            if not self.require_auth():
                return
            query = urlparse(self.path).query
            domain = ""
            for part in query.split("&"):
                if part.startswith("domain="):
                    domain = part.split("=", 1)[1]
            if not domain:
                self.send_json({"error": "缺少 domain 参数"}, 400)
                return

            # 查找证书路径
            servers = list_nginx_servers()
            server = next((s for s in servers if s.get("domain") == domain or s.get("managed_domain") == domain), None)
            if not server:
                self.send_json({"error": "未找到该站点"}, 404)
                return

            cert_path = str(server.get("ssl_cert_path") or "")
            if not cert_path and server.get("https"):
                cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"

            if not cert_path:
                self.send_json({"error": "该站点未配置证书"}, 400)
                return

            detail = read_certificate_detail(cert_path)
            self.send_json({"domain": domain, "cert_path": cert_path, **detail})
            return

        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self.read_json()
        except Exception:
            self.send_json({"error": "JSON 格式错误"}, 400)
            return

        if path == "/api/login":
            if not PASSWORD:
                self.send_json({"error": "服务未设置 HNG_WEB_PASSWORD"}, 500)
                return
            if str(data.get("password", "")) != PASSWORD:
                self.send_json({"error": "密码错误"}, 403)
                return
            ts = str(int(time.time()))
            cookie = f"{COOKIE_NAME}={ts}:{sign_session(ts)}; HttpOnly; SameSite=Strict; Path=/"
            self.send_json({"ok": True}, headers={"Set-Cookie": cookie})
            return

        if path == "/api/logout":
            self.send_json({"ok": True}, headers={"Set-Cookie": f"{COOKIE_NAME}=; Max-Age=0; Path=/"})
            return

        if not self.require_auth():
            return

        routes = {
            "/api/nginx/test": [MANAGER_BIN, "test"],
            "/api/nginx/reload": [MANAGER_BIN, "reload"],
        }
        if path in routes:
            result = run_cmd(routes[path], timeout=60)
            self.send_json({"message": "完成", **result}, 200 if result["code"] == 0 else 500)
            return

        domain = str(data.get("domain", "")).strip()
        if path == "/api/sites/import":
            result = import_existing_site(domain, str(data.get("source", "")).strip())
            self.send_json({"message": "站点已导入", **result}, 200 if result["code"] == 0 else 400)
            return

        if path == "/api/sites/migrate":
            result = migrate_imported_site(domain)
            self.send_json({"message": "站点已迁移", **result}, 200 if result["code"] == 0 else 400)
            return

        if path == "/api/sites/update":
            scheme, upstream = parse_edit_target(str(data.get("target", "")))
            if not scheme or not upstream:
                self.send_json({"error": "后端地址必须是 HOST:PORT 或 http(s)://HOST:PORT"}, 400)
                return
            state = parse_state_file(STATE_DIR / f"{domain.strip().lower()}.env")
            if state.get("IMPORTED") == "1" or state.get("MIGRATED") == "1":
                result = update_imported_site(domain, f"{scheme}://{upstream}")
            else:
                result = run_cmd([MANAGER_BIN, "update", domain, upstream, "--upstream-scheme", scheme], timeout=120)
            self.send_json({"message": "站点已更新", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/sites/add":
            args = [MANAGER_BIN, "add", domain, str(data.get("upstream", "")).strip(), "--upstream-scheme", str(data.get("scheme", "http"))]
            if not data.get("ssl", True):
                args.append("--no-ssl")
            if data.get("email"):
                args += ["--email", str(data.get("email"))]
            if data.get("body"):
                args += ["--client-max-body-size", str(data.get("body"))]
            if data.get("readTimeout"):
                args += ["--proxy-read-timeout", str(data.get("readTimeout"))]
            if data.get("backendInsecure"):
                args.append("--backend-insecure")
            result = run_cmd(args, timeout=180)
            self.send_json({"message": "站点创建完成", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/sites/enable-ssl":
            args = [MANAGER_BIN, "enable-ssl", domain]
            if data.get("email"):
                args += ["--email", str(data.get("email"))]
            result = run_cmd(args, timeout=180)
            self.send_json({"message": "HTTPS 已启用", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/sites/disable-ssl":
            result = run_cmd([MANAGER_BIN, "disable-ssl", domain], timeout=90)
            self.send_json({"message": "HTTPS 已关闭", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/sites/remove":
            args = [MANAGER_BIN, "remove", domain, "--yes"]
            if data.get("delete_cert"):
                args.append("--delete-cert")
            result = run_cmd(args, timeout=90)
            self.send_json({"message": "站点已删除", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/sites/remove-imported":
            comment_out = data.get("comment_out", True)
            result = remove_imported_site(domain, comment_out)
            self.send_json({"message": "已删除导入站点", **result}, 200 if result["code"] == 0 else 400)
            return

        if path == "/api/certs/renew":
            result = run_cmd([MANAGER_BIN, "renew", domain], timeout=180)
            self.send_json({"message": "证书续期完成", **result}, 200 if result["code"] == 0 else 500)
            return

        self.send_error(404)


def main() -> None:
    if not PASSWORD:
        print("警告：未设置 HNG_WEB_PASSWORD，登录将不可用。")
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"{APP_TITLE} listening on http://{BIND}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
