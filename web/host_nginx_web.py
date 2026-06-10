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
import tarfile
import time
import urllib.request
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
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --line:#e4e7eb; --text:#17202a; --muted:#667085; --blue:#1b64d8; --blue2:#eaf1ff; --red:#c62828; --green:#157347; --amber:#9a6700; --shadow:0 1px 3px rgba(16,24,40,.1), 0 1px 2px rgba(16,24,40,.06); }
    * { box-sizing: border-box; }
    body { margin:0; font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }
    button,input,select { font:inherit; }
    .shell { min-height:100vh; display:grid; grid-template-columns:260px 1fr; }
    aside { background:#111827; color:#d1d5db; padding:24px 0; display:flex; flex-direction:column; }
    .brand { color:#fff; font-weight:700; font-size:18px; padding:0 24px; margin-bottom:32px; }
    .nav { display:flex; flex-direction:column; gap:2px; padding:0 12px; }
    .nav button { width:100%; text-align:left; background:transparent; color:#d1d5db; border:0; border-radius:8px; padding:12px 14px; cursor:pointer; transition:all 0.15s; font-size:14px; font-weight:500; }
    .nav button.active { background:#1f2937; color:#fff; box-shadow:0 1px 2px rgba(0,0,0,0.1); }
    .nav button:hover:not(.active) { background:#1f2937; color:#e5e7eb; }
    main { padding:32px 40px; max-width:1400px; width:100%; }
    header { display:flex; justify-content:space-between; align-items:flex-start; gap:20px; margin-bottom:28px; padding-bottom:24px; border-bottom:2px solid var(--line); }
    h1 { font-size:28px; margin:0 0 4px; font-weight:700; letter-spacing:-0.02em; }
    h2 { font-size:18px; margin:0 0 16px; font-weight:600; }
    .muted { color:var(--muted); font-size:13px; }
    .grid { display:grid; gap:20px; }
    .stats { grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); margin-bottom:24px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); padding:24px; }
    .stat-label { color:var(--muted); font-size:13px; margin-bottom:10px; font-weight:500; text-transform:uppercase; letter-spacing:0.03em; }
    .stat-value { font-size:24px; font-weight:700; overflow-wrap:anywhere; }
    .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    .spacer { flex:1; }
    .btn { border:1px solid var(--line); background:#fff; color:var(--text); border-radius:8px; padding:10px 16px; cursor:pointer; min-height:40px; font-weight:500; transition:all 0.15s; }
    .btn:hover { border-color:#b8c1cf; background:#f9fafb; transform:translateY(-1px); box-shadow:0 2px 4px rgba(0,0,0,0.08); }
    .btn.primary { background:var(--blue); border-color:var(--blue); color:#fff; }
    .btn.primary:hover { background:#1557c0; border-color:#1557c0; }
    .btn.danger { background:#fff; border-color:#f0b6b6; color:var(--red); }
    .btn.danger:hover { background:#fef5f5; border-color:#e09090; }
    .btn.small { padding:6px 12px; min-height:32px; font-size:13px; }
    .tag { display:inline-flex; align-items:center; height:26px; padding:0 10px; border-radius:6px; background:#f1f3f5; color:#495057; font-size:12px; font-weight:600; white-space:nowrap; }
    .tag.ok { background:#d4f4dd; color:#0f5132; }
    .tag.warn { background:#fff4db; color:#664d03; }
    .tag.bad { background:#fee; color:#c62828; }
    table { width:100%; border-collapse:collapse; }
    th,td { text-align:left; padding:14px 12px; border-bottom:1px solid var(--line); vertical-align:middle; }
    th { color:var(--muted); font-size:12px; font-weight:700; background:#f8f9fa; text-transform:uppercase; letter-spacing:0.03em; }
    td { overflow-wrap:anywhere; }
    td:first-child { font-weight:500; }
    tbody tr:hover { background:#f8f9fb; }
    form { display:grid; gap:16px; }
    .form-grid { display:grid; grid-template-columns:repeat(2,minmax(240px,1fr)); gap:16px; }
    label { display:grid; gap:8px; color:#344054; font-weight:600; font-size:13px; }
    input,select { border:1px solid var(--line); border-radius:8px; padding:10px 12px; background:#fff; min-height:42px; transition:border-color 0.15s; }
    input:focus,select:focus { outline:0; border-color:var(--blue); box-shadow:0 0 0 3px var(--blue2); }
    input[type=checkbox] { min-height:auto; width:18px; height:18px; cursor:pointer; }
    .check { display:flex; align-items:center; gap:10px; font-weight:500; cursor:pointer; }
    .view { display:none; }
    .view.active { display:block; }
    pre { margin:0; white-space:pre-wrap; background:#1e293b; color:#e2e8f0; padding:16px; border-radius:8px; max-height:400px; overflow:auto; font-size:13px; line-height:1.6; }
    .notice { border-left:4px solid var(--blue); background:var(--blue2); padding:14px 16px; border-radius:8px; color:#1e3a8a; line-height:1.6; }
    .dashboard-grid { grid-template-columns:minmax(0,1.2fr) minmax(360px,0.8fr); }
    .toolbar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:16px 0 20px; padding:16px; background:#f8f9fa; border-radius:8px; border:1px solid var(--line); }
    .toolbar input { flex:1 1 300px; }
    .toolbar select { width:min(240px,100%); }
    .list { display:grid; gap:12px; }
    .list-item { border:1px solid var(--line); border-radius:8px; padding:16px; background:#fff; transition:box-shadow 0.15s; }
    .list-item:hover { box-shadow:0 2px 8px rgba(0,0,0,0.08); }
    .list-item .title { font-weight:600; font-size:15px; }
    .list-item .meta { color:var(--muted); font-size:12px; margin-top:6px; }
    .list-item .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .login { min-height:100vh; display:grid; place-items:center; padding:24px; background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
    .login .panel { width:min(440px,100%); box-shadow:0 20px 60px rgba(0,0,0,0.3); }
    .login-message { margin:12px 0; }
    details { margin:16px 0; }
    summary { cursor:pointer; padding:12px 14px; background:#f8f9fa; border-radius:8px; font-weight:600; user-select:none; border:1px solid var(--line); }
    summary:hover { background:#e9ecef; }
    details[open] summary { margin-bottom:16px; }
    .help-content { line-height:1.7; }
    .help-content h3 { margin:20px 0 12px; font-size:16px; font-weight:600; }
    .help-content code { background:#f1f3f5; padding:3px 7px; border-radius:4px; font-size:13px; color:#c7254e; }
    .help-content pre { background:#1e293b; color:#e2e8f0; padding:16px; border-radius:8px; overflow:auto; }
    .modal-overlay { display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.6); z-index:1000; align-items:center; justify-content:center; }
    .modal-overlay.active { display:flex; }
    .modal { background:#fff; border-radius:12px; max-width:90vw; max-height:90vh; overflow:auto; box-shadow:0 20px 60px rgba(0,0,0,0.3); }
    .modal-header { padding:20px 24px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
    .modal-header h3 { margin:0; font-size:20px; font-weight:600; }
    .modal-body { padding:24px; }
    .domain-col { max-width:280px; }
    .domain-col strong { font-size:15px; color:#111827; }
    .domain-col .muted { font-size:12px; margin-top:2px; display:block; }
    .type-col { min-width:160px; }
    .type-col .tag { margin-right:4px; margin-bottom:4px; }
    .source-col { max-width:300px; font-size:13px; color:var(--muted); }
    .actions-col { min-width:200px; white-space:nowrap; }
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
      <button data-view="migrate">证书迁移</button>
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
        <div class="row"><h2>Nginx 站点</h2><span class="spacer"></span><label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:14px;"><input type="checkbox" id="showAllSites" onchange="toggleShowAllSites(this.checked)"><span>显示所有站点</span></label><div id="siteSummary" class="muted"></div><button class="btn primary" data-jump="create">新增</button></div>
        <div class="toolbar">
          <input id="siteSearch" placeholder="搜索域名、后端、来源">
          <select id="siteFilter">
            <option value="all">全部站点</option>
            <option value="problems">问题站点</option>
            <option value="backend_bad">后端异常</option>
            <option value="cert_warn">证书预警</option>
            <option value="dns_bad">DNS 异常</option>
            <option value="managed">受管站点</option>
            <option value="can_manage">可管理站点</option>
            <option value="https">HTTPS</option>
            <option value="http">HTTP</option>
          </select>
          <button class="btn" id="siteSearchClear" type="button">清空筛选</button>
        </div>
        <div style="overflow:auto"><table><thead><tr><th class="domain-col">域名</th><th>监听</th><th class="type-col">类型与状态</th><th>目标/目录</th><th class="source-col">来源</th><th class="actions-col">操作</th></tr></thead><tbody id="siteRows"></tbody></table></div>
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
        <div style="overflow:auto"><table><thead><tr><th>域名</th><th>证书状态</th><th>自动续期</th><th>当前配置</th><th>来源</th><th>操作</th></tr></thead><tbody id="certRows"></tbody></table></div>
      </div>
    </section>
    <section id="migrate" class="view">
      <div class="panel">
        <h2>🔧 证书迁移与修复工具</h2>
        <p class="muted">检测并修复证书权限问题，确保 nginx 可以正常读取证书文件。</p>

        <div class="notice" style="margin:16px 0;">
          <strong>功能说明：</strong><br>
          • 自动检测所有证书的权限问题<br>
          • 修复证书文件所有者和权限<br>
          • 设置自动修复脚本（certbot 续期后自动运行）<br>
          • 不影响 Rathole、stream 等其他配置<br>
          • 安全可靠，支持回滚
        </div>

        <div style="margin:24px 0;">
          <h3>步骤 1：检查证书状态</h3>
          <button class="btn primary" id="checkCertsBtn" type="button">🔍 检查所有证书</button>
          <div id="certCheckResult" style="margin-top:12px;"></div>
        </div>

        <div style="margin:24px 0;">
          <h3>步骤 2：修复证书权限</h3>
          <button class="btn primary" id="fixCertsBtn" type="button" disabled>🔧 一键修复权限</button>
          <button class="btn" id="installHookBtn" type="button" disabled>⚙️ 安装自动修复脚本</button>
          <div id="certFixResult" style="margin-top:12px;"></div>
        </div>

        <div style="margin:24px 0;">
          <h3>步骤 3：验证修复结果</h3>
          <button class="btn" id="verifyCertsBtn" type="button" disabled>✓ 验证修复</button>
          <div id="certVerifyResult" style="margin-top:12px;"></div>
        </div>

        <div style="margin:24px 0;border-top:2px solid var(--line);padding-top:24px;">
          <h3>步骤 4：统一配置格式</h3>
          <p class="muted">检测并迁移使用旧配置的站点（如从 Certbot 或 NPM 接管的站点），统一为本项目的标准格式。</p>
          <button class="btn primary" id="checkLegacyBtn" type="button">🔍 检查旧配置站点</button>
          <button class="btn primary" id="migrateLegacyBtn" type="button" disabled>🔄 一键统一配置</button>
          <div id="legacyCheckResult" style="margin-top:12px;"></div>
        </div>

        <div style="margin:24px 0;border-top:2px solid var(--line);padding-top:24px;">
          <h3>步骤 5：清理重复配置</h3>
          <p class="muted">检测并清理备份配置文件（.bak-*），解决证书中心显示重复站点的问题。</p>
          <button class="btn" id="cleanDuplicatesBtn" type="button">🧹 清理重复配置</button>
          <div id="cleanDuplicatesResult" style="margin-top:12px;"></div>
        </div>

        <details style="margin-top:24px;">
          <summary>高级选项</summary>
          <div style="padding:16px;background:#f8f9fa;border-radius:8px;margin-top:12px;">
            <h4>迁移自定义证书</h4>
            <p class="muted">如果您有从其他工具（如 NPM）创建的证书，可以使用此功能统一管理。</p>
            <label>证书所有者（当前）<input id="certOwner" value="npmbare" placeholder="npmbare"></label>
            <button class="btn" id="migrateCustomBtn" type="button">迁移自定义证书</button>
          </div>
        </details>
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
        <div class="panel">
          <h2>nginx 维护</h2>
          <div class="row">
            <button class="btn" id="testBtn">测试配置</button>
            <button class="btn primary" id="reloadBtn">重载 nginx</button>
          </div>
        </div>
        <div class="panel">
          <h2>配置备份</h2>
          <div class="row">
            <button class="btn primary" onclick="createBackup()">创建备份</button>
            <button class="btn" onclick="showBackupListModal()">恢复备份</button>
          </div>
        </div>
        <div class="panel">
          <h2>健康检查</h2>
          <div class="row">
            <button class="btn primary" onclick="runHealthCheck()">全站检查</button>
          </div>
        </div>
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
let showAllSites = false;
let certQuery = '';
let certFilter = 'all';
const VIEW_TITLES = {dashboard:'概览',issues:'问题',sites:'站点',services:'本机服务',certs:'证书',migrate:'证书迁移',create:'新增反代',tools:'维护',help:'帮助'};
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
    case 'can_manage': return !!site.can_manage;
    case 'https': return !!site.https;
    case 'http': return !site.https;
    default: return true;
  }
}
function getFilteredSites(){
  if(!state){ return []; }
  const query = String(siteQuery || '').trim().toLowerCase();
  return state.sites
    .filter(site => !isSystemSite(site))  // 隐藏系统站点
    .filter(site => siteMatchesFilter(site) && (!query || siteSearchText(site).includes(query)));
}
function isSystemSite(site){
  const domain = String(site.domain || '');
  // 隐藏默认站点
  if (domain === '(默认站点)' || domain === 'default_server') return true;
  // 隐藏只监听localhost的nginx服务（非反向代理）
  if (site.kind === 'Nginx 服务' && !site.managed && !site.can_manage) return true;
  return false;
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
    case 'needs_https': return !site.https && !!site.managed;
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
function toggleShowAllSites(checked){
  showAllSites = checked;
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
    if (site.cert_status === 'critical') {
      issues.push({kind:'cert_critical', severity:'bad', domain, detail: `证书仅剩 ${site.cert_days ?? '-'} 天`, site});
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
  if (issue.kind === 'cert_critical') return '证书紧急';
  if (issue.kind === 'dns') return 'DNS 异常';
  return '问题';
}
function getQuickFixAction(issue){
  const site = issue.site;
  const actionDomain = String(site.managed_domain || site.domain || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");

  // 证书即将过期 - 一键续期
  if (issue.kind === 'cert_warn' && site.managed) {
    return `<button class="btn small primary" onclick="quickFixCertRenew('${actionDomain}')">⚡ 立即续期</button>`;
  }

  // 证书紧急过期 - 一键续期
  if (issue.kind === 'cert_critical' && site.managed) {
    return `<button class="btn small primary" onclick="quickFixCertRenew('${actionDomain}')">⚡ 紧急续期</button>`;
  }

  // 证书异常 - 一键修复（强制重新申请）
  if (issue.kind === 'cert_bad' && site.managed && site.https) {
    return `<button class="btn small primary" onclick="quickFixCertReissue('${actionDomain}')">⚡ 一键修复</button>`;
  }

  // 证书缺失 - 启用HTTPS
  if (issue.kind === 'cert_bad' && site.managed && !site.https) {
    return `<button class="btn small primary" onclick="enableSsl('${actionDomain}')">⚡ 启用HTTPS</button>`;
  }

  return '';
}
async function quickFixCertRenew(domain){
  if(confirm(`⚡ 快速修复\n\n将立即续期证书：${domain}\n\n确认继续？`)){
    await action('/api/certs/renew',{domain});
  }
}
async function quickFixCertReissue(domain){
  if(confirm(`⚡ 快速修复\n\n将强制重新申请证书：${domain}\n\n此操作将：\n✓ 删除损坏的证书\n✓ 重新验证域名\n✓ 申请全新证书\n\n确认继续？`)){
    await action('/api/certs/force-reissue',{domain});
  }
}
function renderIssueRows(){
  const issues = buildIssueItems();
  $('#issueSummary').textContent = `共 ${issues.length} 项`;
  const rows = issues.map(issue => {
    const site = issue.site;
    const focusDomain = String(site.managed_domain || site.domain || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionDomain = String(site.managed_domain || site.domain || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionSource = String(site.source || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");

    // 获取一键修复按钮
    const quickFix = getQuickFixAction(issue);

    let actions = `<button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;

    // 可纳入管理的站点
    if (site.can_manage && (issue.kind === 'dns' || issue.kind === 'backend')) {
      actions = `<button class="btn small primary" onclick="takeOverSite('${actionDomain}', '${actionSource}')">纳入管理</button><button class="btn small danger" onclick="commentOutConfig('${actionDomain}', '${actionSource}')">注释配置</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    }
    // 受管站点的问题
    else if (site.managed && (issue.kind === 'dns' || issue.kind === 'backend')) {
      actions = `<button class="btn small danger" onclick="removeSite('${actionDomain}')">删除站点</button><button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
    }
    // 证书问题 - 添加一键修复
    else if ((issue.kind === 'cert_warn' || issue.kind === 'cert_bad' || issue.kind === 'cert_critical') && site.managed) {
      const otherActions = site.https
        ? `<button class="btn small" onclick="disableSsl('${actionDomain}')">关闭HTTPS</button>`
        : `<button class="btn small primary" onclick="enableSsl('${actionDomain}')">启用HTTPS</button>`;
      actions = quickFix + otherActions + `<button class="btn small" type="button" onclick="focusSite('${focusDomain}')">定位站点</button>`;
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
  // 去重：同一个 managed_domain 只保留一个（优先 HTTPS 块）
  const allCertificates = state.sites.filter(isCertificateSite);
  const seenDomains = new Map();
  allCertificates.forEach(site => {
    const key = site.managed_domain || site.domain;
    if (!seenDomains.has(key)) {
      seenDomains.set(key, site);
    } else {
      // 如果已存在，优先保留 HTTPS 块（监听443）
      const existing = seenDomains.get(key);
      const hasHttps = site.listen && site.listen.some(l => l.includes('443'));
      const existingHasHttps = existing.listen && existing.listen.some(l => l.includes('443'));
      if (hasHttps && !existingHasHttps) {
        seenDomains.set(key, site);
      }
    }
  });
  const uniqueCertificates = Array.from(seenDomains.values());

  const filteredCertificates = uniqueCertificates.filter(s => {
    if (certFilter === 'all') return true;
    if (certFilter === 'issues') return hasAnyIssue(s);
    if (certFilter === 'enabled') return s.https;
    if (certFilter === 'needs_https') return s.managed && !s.imported && !s.https;
    if (certFilter === 'ok') return s.cert_status === 'ok';
    if (certFilter === 'warn') return s.cert_status === 'warn';
    if (certFilter === 'missing') return s.https && !s.cert_status;
    if (certFilter === 'error') return CERT_WARN_STATES.has(s.cert_status);
    if (certFilter === 'dns_bad') return hasDnsIssue(s);
    if (certFilter === 'managed') return s.managed;
    return true;
  }).filter(s => {
    if (!certQuery) return true;
    const q = certQuery.toLowerCase();
    const domain = (s.domain || '').toLowerCase();
    const names = Array.isArray(s.names) ? s.names.join(' ').toLowerCase() : '';
    const status = (s.cert_status || '').toLowerCase();
    const source = (s.source || '').toLowerCase();
    return domain.includes(q) || names.includes(q) || status.includes(q) || source.includes(q);
  });

  $('#certSummary').textContent = `显示 ${filteredCertificates.length} / ${uniqueCertificates.length}`;
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

    // 自动续期开关
    let autoRenewToggle = '-';
    if (s.managed && !s.imported && s.https) {
      const checked = s.auto_renew ? 'checked' : '';
      const labelClass = s.auto_renew ? 'ok' : 'muted';
      autoRenewToggle = `<label style="display:inline-flex;align-items:center;cursor:pointer;user-select:none;"><input type="checkbox" ${checked} onchange="setAutoRenew('${actionDomain}', this.checked)" style="margin-right:6px;"><span class="${labelClass}">${s.auto_renew ? '已启用' : '已禁用'}</span></label>`;
    }

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
    return `<tr><td><strong>${escapeHtml(domain)}</strong><div class="muted">${escapeHtml(names)}</div></td><td>${statusTag}<div class="muted">${escapeHtml(statusDetail)}</div></td><td>${autoRenewToggle}</td><td>${owner} ${s.https ? '<span class="tag ok">HTTPS</span>' : '<span class="tag warn">HTTP</span>'} ${dnsTag}<div class="muted">${escapeHtml(s.kind || 'Nginx 服务')}</div><div class="muted">${escapeHtml(target)}</div></td><td>${escapeHtml(s.source || '-')}${s.cert_info ? `<div class="muted">${escapeHtml(s.cert_info)}</div>` : ''}${s.dns_detail ? `<div class="muted">DNS: ${escapeHtml(s.dns_detail)}</div>` : ''}</td><td class="row">${actions}</td></tr>`;
  }).join('');
  $('#certRows').innerHTML = rows || '<tr><td colspan="6" class="muted">没有匹配当前筛选条件的证书站点</td></tr>';
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

  // 站点去重：同一个 managed_domain 只保留一个（优先 HTTPS 块）
  const allSites = state.sites;
  const seenSiteDomains = new Map();
  allSites.forEach(site => {
    const key = site.managed_domain || site.domain;
    if (!seenSiteDomains.has(key)) {
      seenSiteDomains.set(key, site);
    } else {
      // 优先保留 HTTPS 块
      const existing = seenSiteDomains.get(key);
      const hasHttps = site.listen && site.listen.some(l => l.includes('443'));
      const existingHasHttps = existing.listen && existing.listen.some(l => l.includes('443'));
      if (hasHttps && !existingHasHttps) {
        seenSiteDomains.set(key, site);
      }
    }
  });
  let uniqueSites = Array.from(seenSiteDomains.values());

  // 过滤默认站点和特殊配置（除非开启"显示所有站点"）
  if (!showAllSites) {
    uniqueSites = uniqueSites.filter(s => {
      const domain = s.domain || '';
      const source = s.source || '';
      // 排除：空域名、默认站点、nginx.conf 中的配置
      if (!domain || domain === '(默认站点)') return false;
      if (source.includes('nginx.conf')) return false;
      return true;
    });
  }

  const filteredSites = uniqueSites.filter(s => {
    if (siteFilter === 'all') return true;
    if (siteFilter === 'managed') return s.managed;
    if (siteFilter === 'imported') return s.imported;
    if (siteFilter === 'importable') return s.importable;
    if (siteFilter === 'proxy') return s.kind === '反向代理';
    if (siteFilter === 'static') return s.kind === '静态站点';
    if (siteFilter === 'https') return s.https;
    if (siteFilter === 'http') return !s.https;
    return true;
  }).filter(s => {
    if (!siteQuery) return true;
    const q = siteQuery.toLowerCase();
    const domain = (s.domain || '').toLowerCase();
    const names = Array.isArray(s.names) ? s.names.join(' ').toLowerCase() : '';
    const target = (s.upstream || s.root || '').toLowerCase();
    const source = (s.source || '').toLowerCase();
    return domain.includes(q) || names.includes(q) || target.includes(q) || source.includes(q);
  });

  $('#siteSummary').textContent = `显示 ${filteredSites.length} / ${uniqueSites.length}`;
  const rows = filteredSites.map(s => {
    const domain = s.domain || '(默认站点)';
    const actionDomain = String(s.managed_domain || domain).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionSource = String(s.source || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionTarget = String(s.upstream || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const names = Array.isArray(s.names) && s.names.length ? s.names.join(', ') : domain;
    const listen = Array.isArray(s.listen) && s.listen.length ? s.listen.join(', ') : '-';
    const target = s.upstream || s.root || '-';

    // 状态标签
    const owner = s.managed ? '<span class="tag ok">🟢 受管</span>' : (s.can_manage ? '<span class="tag">🔵 可管理</span>' : '<span class="tag">只读</span>');
    const https = s.https ? '<span class="tag ok">HTTPS</span>' : '<span class="tag warn">HTTP</span>';
    const backendTag = s.backend_status === 'ok' ? '<span class="tag ok">后端正常</span>' : (s.backend_status === 'bad' ? '<span class="tag bad">后端异常</span>' : '');
    const certTag = s.cert_status === 'ok'
      ? `<span class="tag ok">证书${s.cert_days}天</span>`
      : (s.cert_status === 'warn'
        ? `<span class="tag warn">证书${s.cert_days}天</span>`
        : (s.cert_status === 'critical'
          ? `<span class="tag bad">证书${s.cert_days}天</span>`
          : (s.cert_status === 'missing' || s.cert_status === 'error' ? '<span class="tag bad">证书异常</span>' : '')));
    const dnsTag = dnsTagHtml(s);

    // 组合所有状态标签
    const statusTags = [owner, https, backendTag, certTag, dnsTag].filter(t => t).join(' ');
    const kindInfo = s.kind || 'Nginx 服务';

    let actions = '<span class="muted">只读</span>';
    if (s.managed) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><button class="btn small" onclick="renameSite('${actionDomain}')">重命名</button><button class="btn small danger" onclick="removeSite('${actionDomain}')">删除</button>`;
    } else if (s.can_manage) {
      actions = `<button class="btn small primary" onclick="takeOverSite('${actionDomain}', '${actionSource}')">纳入管理</button>`;
    } else if (s.readonly_reason) {
      actions = `<span class="muted">${escapeHtml(s.readonly_reason)}</span>`;
    }

    return `<tr>
      <td class="domain-col"><strong>${escapeHtml(domain)}</strong><div class="muted">${escapeHtml(names)}</div></td>
      <td>${escapeHtml(listen)}</td>
      <td class="type-col">${statusTags}<div class="muted" style="margin-top:6px;">${escapeHtml(kindInfo)}</div></td>
      <td>${escapeHtml(target)}${s.backend_detail ? `<div class="muted">${escapeHtml(s.backend_detail)}</div>` : ''}</td>
      <td class="source-col">${escapeHtml(s.source || '-')}${s.cert_info ? `<div class="muted">${escapeHtml(s.cert_info)}</div>` : ''}${s.dns_detail ? `<div class="muted">DNS: ${escapeHtml(s.dns_detail)}</div>` : ''}</td>
      <td class="actions-col">${actions}</td>
    </tr>`;
  }).join('');
  $('#siteRows').innerHTML = rows || '<tr><td colspan="6" class="muted">没有匹配当前筛选条件的站点</td></tr>';
  const serviceRows = state.services.map(s => {
    const target = `${s.host}:${s.port}`;
    const actionTarget = String(target).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    return `<tr><td>${escapeHtml(s.host)}</td><td>${escapeHtml(String(s.port))}</td><td>${escapeHtml(s.process)}</td><td><span class="tag ${s.exposed ? 'warn' : 'ok'}">${s.exposed ? '公网监听' : '本机监听'}</span></td><td>${escapeHtml(target)}</td><td class="row"><button class="btn small primary" onclick="useService('${actionTarget}')">用于反代</button></td></tr>`;
  }).join('');
  $('#serviceRows').innerHTML = serviceRows || '<tr><td colspan="6" class="muted">当前没有发现适合反代的本机监听服务</td></tr>';
}
async function action(path, body){
  $('#output').textContent='执行中...';
  showMsg('正在执行操作，请稍候...', 'info');

  try {
    const data = await api(path,{method:'POST',body:JSON.stringify(body||{})});
    $('#output').textContent = data.output || '完成';

    // 根据操作类型显示不同的成功消息
    let successMsg = data.message || '操作完成';
    if (path.includes('/certs/')) successMsg = '✓ ' + successMsg;
    if (path.includes('/sites/add')) successMsg = '✓ 站点创建成功';
    if (path.includes('/sites/remove')) successMsg = '✓ 站点删除成功';

    showMsg(successMsg,'ok');
    await load();
  } catch(err) {
    $('#output').textContent = err.message || '操作失败';
    showMsg('❌ ' + err.message, 'bad');
    throw err;
  }
}
async function enableSsl(domain){ const email = prompt('证书邮箱，可留空'); await action('/api/sites/enable-ssl',{domain,email:email||''}); }
async function editSite(domain, currentTarget){ const target = prompt('新的后端地址，例如 127.0.0.1:3002 或 http://127.0.0.1:3002', currentTarget || ''); if(target) await action('/api/sites/update',{domain,target}); }
async function disableSsl(domain){ if(confirm('确认关闭 HTTPS？')) await action('/api/sites/disable-ssl',{domain}); }
async function removeSite(domain){
  showDeleteModal(domain);
}
function showDeleteModal(domain){
  const modal = document.createElement('div');
  modal.className = 'modal-overlay active';
  modal.innerHTML = `
    <div class="modal" style="max-width:520px;">
      <div class="modal-header">
        <h3>删除站点：${escapeHtml(domain)}</h3>
        <span class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</span>
      </div>
      <div class="modal-body">
        <p style="margin-bottom:16px;">请选择删除方式：</p>
        <div style="display:grid;gap:12px;margin-bottom:20px;">
          <label class="delete-option" style="border:2px solid var(--line);border-radius:8px;padding:16px;cursor:pointer;transition:all 0.2s;">
            <input type="radio" name="deleteMode" value="keep" checked style="margin-right:8px;">
            <div style="display:inline-block;">
              <strong style="color:var(--text);">删除站点，保留证书</strong>
              <div class="muted" style="margin-top:4px;font-size:13px;">✓ 删除 nginx 配置<br>✓ 自动创建备份<br>✓ 保留 SSL 证书（推荐）</div>
            </div>
          </label>
          <label class="delete-option" style="border:2px solid var(--line);border-radius:8px;padding:16px;cursor:pointer;transition:all 0.2s;">
            <input type="radio" name="deleteMode" value="delete" style="margin-right:8px;">
            <div style="display:inline-block;">
              <strong style="color:var(--red);">删除站点和证书</strong>
              <div class="muted" style="margin-top:4px;font-size:13px;">✗ 删除 nginx 配置<br>✗ 删除 SSL 证书<br>✗ 清理所有证书文件<br>⚠️ 重新申请需验证域名</div>
            </div>
          </label>
        </div>
        <div class="row" style="gap:12px;">
          <button class="btn" onclick="this.closest('.modal-overlay').remove()">取消</button>
          <span class="spacer"></span>
          <button class="btn danger" onclick="confirmDeleteSite('${escapeHtml(domain)}', this.closest('.modal-overlay'))">确认删除</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.onclick = (e) => { if(e.target === modal) modal.remove(); };
  // 选中效果
  modal.querySelectorAll('.delete-option').forEach(opt => {
    opt.onclick = () => {
      modal.querySelectorAll('.delete-option').forEach(o => o.style.borderColor = 'var(--line)');
      opt.style.borderColor = 'var(--blue)';
    };
  });
}
async function confirmDeleteSite(domain, modalEl){
  const mode = modalEl.querySelector('input[name="deleteMode"]:checked').value;
  modalEl.remove();
  await action('/api/sites/remove',{domain, delete_cert: mode === 'delete'});
}
async function renameSite(domain){
  showRenameModal(domain);
}
function showRenameModal(domain){
  const modal = document.createElement('div');
  modal.className = 'modal-overlay active';
  modal.innerHTML = `
    <div class="modal" style="max-width:520px;">
      <div class="modal-header">
        <h3>重命名站点</h3>
        <span class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</span>
      </div>
      <div class="modal-body">
        <div style="margin-bottom:16px;">
          <label style="display:block;margin-bottom:4px;font-weight:500;">当前域名</label>
          <input type="text" value="${escapeHtml(domain)}" readonly style="background:var(--bg-secondary);cursor:not-allowed;">
        </div>
        <div style="margin-bottom:16px;">
          <label style="display:block;margin-bottom:4px;font-weight:500;">新域名 <span style="color:var(--red);">*</span></label>
          <input type="text" id="newDomain" placeholder="例如: newdomain.example.com" style="width:100%;">
        </div>
        <div style="margin-bottom:16px;">
          <label style="display:block;margin-bottom:4px;font-weight:500;">新后端地址（可选）</label>
          <input type="text" id="newUpstream" placeholder="留空则保持原后端地址不变">
          <div class="muted" style="margin-top:4px;font-size:13px;">如果只需要更换域名，请留空</div>
        </div>
        <div style="margin-bottom:20px;">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
            <input type="checkbox" id="deleteOldCert">
            <span>删除旧证书</span>
          </label>
          <div class="muted" style="margin-top:4px;font-size:13px;">建议保留旧证书，以便回滚</div>
        </div>
        <div class="row" style="gap:12px;">
          <button class="btn" onclick="this.closest('.modal-overlay').remove()">取消</button>
          <span class="spacer"></span>
          <button class="btn primary" onclick="confirmRenameSite('${escapeHtml(domain)}', this.closest('.modal-overlay'))">确认重命名</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.onclick = (e) => { if(e.target === modal) modal.remove(); };
  setTimeout(() => modal.querySelector('#newDomain').focus(), 100);
}
async function confirmRenameSite(domain, modalEl){
  const newDomain = modalEl.querySelector('#newDomain').value.trim();
  const newUpstream = modalEl.querySelector('#newUpstream').value.trim();
  const deleteOldCert = modalEl.querySelector('#deleteOldCert').checked;

  if(!newDomain){
    alert('请输入新域名');
    return;
  }

  modalEl.remove();
  await action('/api/sites/rename', {domain, new_domain: newDomain, new_upstream: newUpstream, delete_old_cert: deleteOldCert});
}
async function createBackup(){
  if(!confirm('确认创建配置备份？\n\n将备份以下内容：\n• 所有站点配置\n• Nginx 配置文件\n• SSL 证书')) return;
  await action('/api/backup/create', {});
}
async function showBackupListModal(){
  const res = await fetch('/api/backup/list', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  const data = await res.json();

  if(data.code !== 0){
    alert('获取备份列表失败：\n' + (data.output || data.error));
    return;
  }

  // 解析备份列表
  const lines = (data.output || '').split('\n');
  const backups = [];
  let inList = false;

  for(const line of lines){
    if(line.includes('----')) {
      inList = true;
      continue;
    }
    if(inList && line.trim() && !line.includes('备份目录') && !line.includes('总数')){
      const parts = line.trim().split(/\s+/);
      if(parts.length >= 3){
        backups.push({
          file: parts[0],
          size: parts[1],
          time: parts.slice(2).join(' ')
        });
      }
    }
  }

  if(backups.length === 0){
    alert('暂无备份文件');
    return;
  }

  // 创建模态框
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.innerHTML = `
    <div class="modal-content">
      <h2>恢复配置备份</h2>
      <p style="color:#f39c12;margin-bottom:15px">⚠️ 恢复备份将覆盖当前配置，操作前会自动备份当前配置</p>
      <div style="max-height:400px;overflow-y:auto">
        <table style="width:100%;border-collapse:collapse">
          <thead>
            <tr style="background:#34495e;color:white">
              <th style="padding:8px;text-align:left">文件名</th>
              <th style="padding:8px;text-align:left">大小</th>
              <th style="padding:8px;text-align:left">创建时间</th>
              <th style="padding:8px;text-align:center">操作</th>
            </tr>
          </thead>
          <tbody>
            ${backups.map(b => `
              <tr style="border-bottom:1px solid #ddd">
                <td style="padding:8px;font-family:monospace;font-size:12px">${b.file}</td>
                <td style="padding:8px">${b.size}</td>
                <td style="padding:8px">${b.time}</td>
                <td style="padding:8px;text-align:center">
                  <button class="btn primary" style="padding:5px 10px;font-size:12px" onclick="restoreBackup('${b.file}')">恢复</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
      <div class="row" style="margin-top:15px">
        <button class="btn" onclick="this.closest('.modal').remove()">关闭</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
}
async function restoreBackup(filename){
  const backupPath = '/etc/nginx/vps-proxy-manager/backups/' + filename;

  if(!confirm(`确认恢复备份？\n\n备份文件：${filename}\n\n操作将：\n✓ 自动备份当前配置\n✓ 恢复选定的备份\n✓ 测试 nginx 配置\n✓ 失败自动回滚\n\n此操作不可撤销，是否继续？`)){
    return;
  }

  // 关闭模态框
  document.querySelector('.modal')?.remove();

  // 执行恢复
  await action('/api/backup/restore', {backup_file: backupPath});
}
async function runHealthCheck(){
  if(!confirm('确认运行健康检查？\n\n将检查所有站点的：\n• 后端连接\n• DNS 解析\n• 证书有效期\n• Nginx 配置')) return;
  await action('/api/health/check', {domain: ''});
}
async function commentOutConfig(domain, source){ if(confirm(`确认注释掉这个nginx配置？\n\n域名: ${domain}\n配置文件: ${source}\n\n操作将：\n1. 注释掉该server块\n2. 创建备份文件\n3. 重载nginx\n\n该配置不会被删除，只是被注释。`)) await action('/api/nginx/comment-out',{domain, source}); }
async function takeOverSite(domain, source){
  if(confirm(`🎯 确认纳入管理？

域名: ${domain}

操作将自动完成：
✓ 创建标准的受管配置
✓ 复用现有SSL证书（如果有）
✓ 使用统一的ACME验证目录
✓ 注释原始nginx配置（保留备份）
✓ 网站保持正常运行（无缝切换）

纳入后的好处：
✓ 可在界面编辑后端地址
✓ 可管理SSL证书和自动续期
✓ 可随时删除（自动备份恢复）
✓ 配置格式标准化

确认继续？`)) await action('/api/sites/take-over',{domain, source});
}
async function renewCert(domain){
  showRenewModal(domain);
}
function showRenewModal(domain){
  const modal = document.createElement('div');
  modal.className = 'modal-overlay active';
  modal.innerHTML = `
    <div class="modal" style="max-width:520px;">
      <div class="modal-header">
        <h3>续期证书：${escapeHtml(domain)}</h3>
        <span class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</span>
      </div>
      <div class="modal-body">
        <p style="margin-bottom:16px;">请选择续期方式：</p>
        <div style="display:grid;gap:12px;margin-bottom:20px;">
          <label class="renew-option" style="border:2px solid var(--blue);border-radius:8px;padding:16px;cursor:pointer;transition:all 0.2s;">
            <input type="radio" name="renewMode" value="normal" checked style="margin-right:8px;">
            <div style="display:inline-block;">
              <strong style="color:var(--text);">正常续期（推荐）</strong>
              <div class="muted" style="margin-top:4px;font-size:13px;">✓ 使用现有证书续期<br>✓ 快速完成<br>✓ 适用于证书即将过期</div>
            </div>
          </label>
          <label class="renew-option" style="border:2px solid var(--line);border-radius:8px;padding:16px;cursor:pointer;transition:all 0.2s;">
            <input type="radio" name="renewMode" value="force" style="margin-right:8px;">
            <div style="display:inline-block;">
              <strong style="color:var(--amber);">强制重新申请</strong>
              <div class="muted" style="margin-top:4px;font-size:13px;">✓ 删除现有证书<br>✓ 重新验证域名<br>✓ 申请全新证书<br>⚠️ 适用于证书损坏的情况</div>
            </div>
          </label>
        </div>
        <div class="row" style="gap:12px;">
          <button class="btn" onclick="this.closest('.modal-overlay').remove()">取消</button>
          <span class="spacer"></span>
          <button class="btn primary" onclick="confirmRenewCert('${escapeHtml(domain)}', this.closest('.modal-overlay'))">确认续期</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.onclick = (e) => { if(e.target === modal) modal.remove(); };
  // 选中效果
  modal.querySelectorAll('.renew-option').forEach(opt => {
    opt.onclick = () => {
      modal.querySelectorAll('.renew-option').forEach(o => o.style.borderColor = 'var(--line)');
      opt.style.borderColor = 'var(--blue)';
    };
  });
}
async function confirmRenewCert(domain, modalEl){
  const mode = modalEl.querySelector('input[name="renewMode"]:checked').value;
  modalEl.remove();
  if(mode === 'force'){
    await action('/api/certs/force-reissue',{domain});
  } else {
    await action('/api/certs/renew',{domain});
  }
}
async function setAutoRenew(domain, enable) {
  try {
    await action('/api/certs/set-auto-renew', {domain, enable});
    showMsg(enable ? '已启用自动续期' : '已禁用自动续期', 'ok');
    // 刷新数据以更新界面
    await load();
  } catch(err) {
    showMsg(err.message, 'bad');
    // 失败时也刷新，恢复复选框状态
    await load();
  }
}
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

// 证书迁移功能
$('#checkCertsBtn').onclick = async () => {
  const btn = $('#checkCertsBtn');
  btn.disabled = true;
  btn.textContent = '检查中...';
  $('#certCheckResult').innerHTML = '<div style="color:var(--blue);">正在检查证书权限...</div>';
  try {
    const data = await api('/api/migrate/check', {method:'POST', body:'{}'});
    let html = '<div class="panel" style="margin-top:12px;background:#f8f9fa;">';
    html += '<h4>检查结果：</h4>';
    html += `<p><strong>检查了 ${data.total} 个证书目录</strong></p>`;
    if (data.issues.length > 0) {
      html += `<p style="color:var(--red);"><strong>发现 ${data.issues.length} 个权限问题：</strong></p><ul>`;
      data.issues.forEach(issue => {
        html += `<li>${escapeHtml(issue)}</li>`;
      });
      html += '</ul>';
      $('#fixCertsBtn').disabled = false;
      $('#installHookBtn').disabled = false;
    } else {
      html += '<p style="color:var(--green);"><strong>✓ 所有证书权限正常</strong></p>';
    }
    html += '<pre style="max-height:200px;overflow:auto;margin-top:12px;">' + escapeHtml(data.output) + '</pre>';
    html += '</div>';
    $('#certCheckResult').innerHTML = html;
  } catch(err) {
    $('#certCheckResult').innerHTML = `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 检查所有证书';
  }
};

$('#fixCertsBtn').onclick = async () => {
  if (!confirm('确认修复所有证书权限？\n\n此操作将：\n✓ 修改证书文件所有者为 root:root\n✓ 设置正确的文件权限\n✓ 重载 nginx\n\n该操作安全可靠，不影响其他配置。')) return;

  const btn = $('#fixCertsBtn');
  btn.disabled = true;
  btn.textContent = '修复中...';
  $('#certFixResult').innerHTML = '<div style="color:var(--blue);">正在修复证书权限...</div>';
  try {
    const data = await api('/api/migrate/fix', {method:'POST', body:'{}'});
    let html = '<div class="panel" style="margin-top:12px;background:#d4f4dd;color:var(--green);">';
    html += '<h4>✓ 修复完成！</h4>';
    html += '<pre style="max-height:300px;overflow:auto;margin-top:12px;">' + escapeHtml(data.output) + '</pre>';
    html += '</div>';
    $('#certFixResult').innerHTML = html;
    $('#verifyCertsBtn').disabled = false;
    showMsg('✓ 证书权限修复成功', 'ok');
  } catch(err) {
    $('#certFixResult').innerHTML = `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔧 一键修复权限';
  }
};

$('#installHookBtn').onclick = async () => {
  if (!confirm('确认安装自动修复脚本？\n\n此脚本将在 certbot 续期后自动运行，确保证书权限始终正确。\n\n脚本位置：/etc/letsencrypt/renewal-hooks/post/fix-permissions.sh')) return;

  const btn = $('#installHookBtn');
  btn.disabled = true;
  btn.textContent = '安装中...';
  try {
    const data = await api('/api/migrate/install-hook', {method:'POST', body:'{}'});
    $('#certFixResult').innerHTML += `<div class="panel" style="margin-top:12px;background:#d4f4dd;color:var(--green);"><h4>✓ 自动修复脚本已安装</h4><pre>${escapeHtml(data.output)}</pre></div>`;
    showMsg('✓ 自动修复脚本安装成功', 'ok');
  } catch(err) {
    $('#certFixResult').innerHTML += `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '⚙️ 安装自动修复脚本';
  }
};

$('#verifyCertsBtn').onclick = async () => {
  const btn = $('#verifyCertsBtn');
  btn.disabled = true;
  btn.textContent = '验证中...';
  $('#certVerifyResult').innerHTML = '<div style="color:var(--blue);">正在验证修复结果...</div>';
  try {
    const data = await api('/api/migrate/verify', {method:'POST', body:'{}'});
    let html = '<div class="panel" style="margin-top:12px;';
    if (data.success) {
      html += 'background:#d4f4dd;color:var(--green);">';
      html += '<h4>✓ 验证通过！</h4>';
      html += '<p>所有证书均可被 nginx 正常读取。</p>';
    } else {
      html += 'background:#fff4db;color:var(--amber);">';
      html += '<h4>⚠ 部分证书仍有问题</h4>';
    }
    html += '<pre style="max-height:200px;overflow:auto;margin-top:12px;">' + escapeHtml(data.output) + '</pre>';
    html += '</div>';
    $('#certVerifyResult').innerHTML = html;
  } catch(err) {
    $('#certVerifyResult').innerHTML = `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '✓ 验证修复';
  }
};

$('#checkLegacyBtn').onclick = async () => {
  const btn = $('#checkLegacyBtn');
  btn.disabled = true;
  btn.textContent = '检查中...';
  $('#legacyCheckResult').innerHTML = '<div style="color:var(--blue);">正在检查旧配置站点...</div>';
  try {
    const data = await api('/api/migrate/check-legacy', {method:'POST', body:'{}'});
    let html = '<div class="panel" style="margin-top:12px;background:#f8f9fa;">';
    html += '<h4>检查结果：</h4>';
    if (data.legacy_sites.length > 0) {
      html += `<p style="color:var(--amber);"><strong>发现 ${data.legacy_sites.length} 个使用旧配置的站点：</strong></p><ul>`;
      data.legacy_sites.forEach(site => {
        html += `<li><strong>${escapeHtml(site.domain)}</strong><div class="muted">配置文件: ${escapeHtml(site.source)}</div><div class="muted">问题: ${escapeHtml(site.issues.join(', '))}</div></li>`;
      });
      html += '</ul>';
      html += '<p class="muted">建议：点击"一键统一配置"将这些站点迁移到标准格式。</p>';
      $('#migrateLegacyBtn').disabled = false;
    } else {
      html += '<p style="color:var(--green);"><strong>✓ 所有站点均使用标准配置</strong></p>';
    }
    html += '</div>';
    $('#legacyCheckResult').innerHTML = html;
  } catch(err) {
    $('#legacyCheckResult').innerHTML = `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 检查旧配置站点';
  }
};

$('#migrateLegacyBtn').onclick = async () => {
  if (!confirm('⚠️ 确认统一配置格式？\n\n此操作将：\n✓ 将旧配置站点迁移到标准格式\n✓ 保留所有证书和后端设置\n✓ 注释或删除旧配置文件\n✓ 启用 IPv6、HTTP/2 等新特性\n✓ 网站保持正常运行\n\n操作前会自动创建备份，可随时回滚。')) return;

  const btn = $('#migrateLegacyBtn');
  btn.disabled = true;
  btn.textContent = '迁移中...';
  $('#legacyCheckResult').innerHTML = '<div style="color:var(--blue);">正在迁移旧配置站点...</div>';
  try {
    const data = await api('/api/migrate/migrate-legacy', {method:'POST', body:'{}'});
    let html = '<div class="panel" style="margin-top:12px;';
    if (data.success) {
      html += 'background:#d4f4dd;color:var(--green);">';
      html += '<h4>✓ 迁移完成！</h4>';
      html += `<p>成功迁移 ${data.migrated} 个站点</p>`;
    } else {
      html += 'background:#fff4db;color:var(--amber);">';
      html += '<h4>⚠ 部分站点迁移失败</h4>';
      html += `<p>成功: ${data.migrated} 个，失败: ${data.failed} 个</p>`;
    }
    html += '<pre style="max-height:400px;overflow:auto;margin-top:12px;">' + escapeHtml(data.output) + '</pre>';
    html += '</div>';
    $('#legacyCheckResult').innerHTML = html;
    showMsg('✓ 配置统一完成，正在刷新...', 'ok');
    setTimeout(() => load(), 2000);
  } catch(err) {
    $('#legacyCheckResult').innerHTML = `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 一键统一配置';
  }
};

$('#cleanDuplicatesBtn').onclick = async () => {
  if (!confirm('确认清理重复配置？\n\n此操作将：\n✓ 删除所有备份配置文件（.bak-*）\n✓ 只保留标准配置文件\n✓ 解决证书中心重复显示问题\n\n不影响站点正常运行。')) return;

  const btn = $('#cleanDuplicatesBtn');
  btn.disabled = true;
  btn.textContent = '清理中...';
  $('#cleanDuplicatesResult').innerHTML = '<div style="color:var(--blue);">正在清理重复配置...</div>';
  try {
    const data = await api('/api/migrate/clean-duplicates', {method:'POST', body:'{}'});
    let html = '<div class="panel" style="margin-top:12px;';
    if (data.success) {
      html += 'background:#d4f4dd;color:var(--green);">';
      html += '<h4>✓ 清理完成！</h4>';
      html += `<p>清理了 ${data.cleaned} 个备份配置文件</p>`;
    } else {
      html += 'background:#fff4db;color:var(--amber);">';
      html += '<h4>⚠ 部分文件清理失败</h4>';
    }
    html += '<pre style="max-height:300px;overflow:auto;margin-top:12px;">' + escapeHtml(data.output) + '</pre>';
    html += '</div>';
    $('#cleanDuplicatesResult').innerHTML = html;
    showMsg('✓ 清理完成，正在刷新...', 'ok');
    setTimeout(() => load(), 2000);
  } catch(err) {
    $('#cleanDuplicatesResult').innerHTML = `<div class="panel" style="background:#fee;color:var(--red);margin-top:12px;">❌ ${escapeHtml(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🧹 清理重复配置';
  }
};

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

    # 1. 获取本机所有IP (hostname -I)
    result = run_cmd(["hostname", "-I"], timeout=10)
    if result["code"] == 0:
        for part in str(result["output"]).split():
            add_ip(part)

    # 2. 获取公网IP (适配云服务商NAT场景)
    for service in ["https://ifconfig.me", "https://ip.sb", "https://api.ipify.org"]:
        try:
            response = urllib.request.urlopen(service, timeout=3)
            public_ip = response.read().decode('utf-8').strip()
            add_ip(public_ip)
            break  # 成功获取一个即可
        except Exception:
            continue

    # 3. socket备用方案
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

    # 排除的端口：nginx、管理界面、系统服务
    EXCLUDED_PORTS = {
        22,    # SSH
        53,    # DNS (systemd-resolved)
        80,    # HTTP (nginx)
        443,   # HTTPS (nginx)
        8098,  # 管理界面
    }

    # 排除的进程：系统服务
    EXCLUDED_PROCESSES = {
        "systemd-resolve",
        "systemd-network",
        "dnsmasq",
        "sshd",
        "nginx",
    }

    services: list[dict[str, object]] = []
    seen: set[tuple[str, int, str]] = set()
    for line in str(result["output"]).splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[3]
        process_info = parts[5] if len(parts) > 5 else ""

        # 解析地址和端口
        if local_addr.startswith("["):
            host, _, port_text = local_addr.rpartition(":")
            host = host.strip("[]")
        else:
            host, _, port_text = local_addr.rpartition(":")

        if not port_text.isdigit():
            continue
        port = int(port_text)

        # 过滤系统端口
        if port in EXCLUDED_PORTS:
            continue

        proc_match = re.search(r'\("([^\"]+)"', process_info)
        process = proc_match.group(1) if proc_match else (process_info or "unknown")

        # 过滤系统进程
        if process in EXCLUDED_PROCESSES:
            continue

        exposed = host in {"*", "0.0.0.0", "::"}
        normalized_host = "127.0.0.1" if host in {"*", "0.0.0.0", "::", "::1", "[::]"} else host

        # 过滤特殊地址（如127.0.0.53%lo）
        if "127.0.0.53" in normalized_host or "%" in normalized_host:
            continue

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

    # 修复 HTTPS 判断：受管站点从状态文件读取，非受管站点从配置判断
    if managed and managed_state:
        https = managed_state.get("ENABLE_SSL") == "1"
    else:
        https = has_ssl_cert or any("ssl" in item or ":443" in item or item.startswith("443") for item in listens)

    import_domain = next((name for name in names if DOMAIN_RE.match(name)), "")
    upstream_scheme, upstream_target = split_proxy_upstream(upstream)

    if proxy_passes:
        kind = "反向代理"
    elif roots:
        kind = "静态站点"
    else:
        kind = "Nginx 服务"

    # 判断是否可以纳入管理：非受管的反向代理站点
    can_manage = bool(
        not managed
        and kind == "反向代理"
        and import_domain
        and upstream_scheme
        and upstream_target
        and "$" not in upstream
    )

    # 只读原因
    if managed or can_manage:
        readonly_reason = ""
    elif kind != "反向代理":
        readonly_reason = "非反向代理"
    elif not import_domain:
        readonly_reason = "缺少域名"
    elif "$" in upstream:
        readonly_reason = "包含变量"
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
        "can_manage": can_manage,
        "readonly_reason": readonly_reason,
        "source": source,
        "managed_domain": managed_domain,
        "upstream_scheme": upstream_scheme,
        "upstream_target": upstream_target,
        "ssl_cert_path": ssl_cert_path,
        "auto_renew": managed_state.get("AUTO_RENEW", "1") == "1",
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
                "can_manage": False,
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
        f"AUTO_RENEW={values.get('AUTO_RENEW', '1')}",
    ]
    lines.append("")
    state_path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(state_path, 0o600)
    return state_path


def take_over_site(domain: str, source: str) -> dict[str, object]:
    """纳入管理：将已有nginx站点纳入工具管理（自动完成迁移）"""
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        return {"code": 2, "output": "域名无效"}

    servers = list_nginx_servers()
    # 同一域名可能有多个server块（如80+443），优先选择可管理的
    matching_servers = [item for item in servers if item.get("domain") == domain and item.get("source") == source]
    if not matching_servers:
        return {"code": 3, "output": "未找到对应 nginx 站点，请刷新后重试"}

    # 优先选择can_manage=True的站点
    server = next((s for s in matching_servers if s.get("can_manage")), matching_servers[0])

    if not server.get("can_manage"):
        return {"code": 4, "output": f"该站点不能纳入管理：{server.get('readonly_reason') or '只读'}"}

    upstream_scheme = str(server.get("upstream_scheme") or "")
    upstream_target = str(server.get("upstream_target") or "")
    if upstream_scheme not in {"http", "https"} or not re.match(r"^[^/:]+:\d+$", upstream_target):
        return {"code": 5, "output": "只支持纳入明确的 http/https HOST:PORT 反向代理"}

    # 检查是否有现有证书
    has_existing_cert = server.get("https") and pathlib.Path(f"/etc/letsencrypt/live/{domain}").exists()

    # 使用管理脚本创建受管配置（先创建HTTP，避免重复申请证书）
    args = [MANAGER_BIN, "add", domain, upstream_target, "--upstream-scheme", upstream_scheme, "--no-ssl"]

    result = run_cmd(args, timeout=120)
    if result["code"] != 0:
        return {"code": 6, "output": f"创建受管配置失败：\n{result['output']}"}

    # 如果有现有证书，启用SSL复用证书
    if has_existing_cert:
        enable_ssl_result = run_cmd([MANAGER_BIN, "enable-ssl", domain], timeout=120)
        if enable_ssl_result["code"] != 0:
            return {"code": 7, "output": f"启用SSL失败：\n{enable_ssl_result['output']}\n\nHTTP配置已创建，可稍后手动启用HTTPS"}

    # 注释原配置
    source_path = pathlib.Path(source)
    comment_status = ""
    if source_path.is_file() and str(source_path).startswith("/etc/nginx/"):
        comment_result = comment_out_nginx_config(domain, source)
        if comment_result["code"] == 0:
            comment_status = f"\n✓ 原配置已注释：{source}"
        else:
            comment_status = f"\n⚠ 原配置注释失败，请手动检查：{source}"

    # 构建成功消息
    output_parts = [f"✓ 已纳入管理：{domain} -> {upstream_scheme}://{upstream_target}"]

    if has_existing_cert:
        output_parts.append("✓ 已复用现有SSL证书")
    elif server.get("https"):
        output_parts.append("⚠ 证书文件缺失，已创建HTTP配置，请手动启用HTTPS")

    if comment_status:
        output_parts.append(comment_status.strip())

    output_parts.append("\n提示：现在可以在界面上编辑、删除此站点")

    return {"code": 0, "output": "\n".join(output_parts)}


def find_imported_server_block(lines: list[str], domain: str) -> tuple[int, int]:

    return {"code": 0, "output": f"已纳入管理：{domain} -> {upstream_scheme}://{upstream_target}"}


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


def comment_out_nginx_config(domain: str, source: str) -> dict[str, object]:
    """注释掉nginx配置文件中的server块"""
    domain = domain.strip().lower()
    source_path = pathlib.Path(source)

    if not source_path.exists():
        return {"code": 1, "output": f"配置文件不存在: {source}"}

    try:
        original = source_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"code": 2, "output": f"读取配置文件失败: {e}"}

    # 查找包含该域名的server块
    lines = original.splitlines()
    in_server = False
    in_target_server = False
    depth = 0
    start_line = -1
    commented_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检测server块开始
        if not in_server and re.match(r'^server\s*\{', stripped):
            in_server = True
            start_line = i
            depth = stripped.count('{') - stripped.count('}')
            # 检查是否包含目标域名
            in_target_server = False
            continue

        if in_server:
            depth += line.count('{') - line.count('}')

            # 检查是否包含目标域名
            if not in_target_server and f'server_name' in line:
                if domain in line:
                    in_target_server = True

            # server块结束
            if depth == 0:
                if in_target_server:
                    # 注释掉整个server块
                    for j in range(start_line, i + 1):
                        if not lines[j].strip().startswith('#'):
                            commented_lines.append(j)
                            lines[j] = '# ' + lines[j]

                in_server = False
                in_target_server = False

    if not commented_lines:
        return {"code": 3, "output": f"未找到域名为 {domain} 的server块"}

    # 写入注释后的配置（不创建备份）
    new_content = "\n".join(lines) + "\n"
    source_path.write_text(new_content, encoding="utf-8")

    # 删除 sites-enabled 中的软链接（如果存在）
    if source_path.parent.name == "sites-available":
        enabled_link = pathlib.Path("/etc/nginx/sites-enabled") / source_path.name
        if enabled_link.exists() or enabled_link.is_symlink():
            try:
                enabled_link.unlink()
            except Exception:
                pass

    # 测试nginx配置
    test = run_cmd(["nginx", "-t"], timeout=20)
    if test["code"] != 0:
        # 回滚
        source_path.write_text(original, encoding="utf-8")
        return {"code": 4, "output": f"注释后nginx配置测试失败，已回滚。\n{test['output']}"}

    # 重载nginx
    reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=20)
    if reload_result["code"] != 0:
        reload_result = run_cmd(["nginx", "-s", "reload"], timeout=20)

    output = f"已注释配置: {domain}\n配置文件: {source}\n注释了 {len(commented_lines)} 行\n{reload_result['output']}".strip()
    return {"code": reload_result["code"], "output": output}


def remove_site_with_backup(domain: str, delete_cert: bool = False) -> dict[str, object]:
    """删除受管站点（直接删除，无备份）"""
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        return {"code": 1, "output": "域名无效"}

    state_path = STATE_DIR / f"{domain}.env"
    if not state_path.exists():
        return {"code": 2, "output": "状态文件不存在，该站点不是受管站点"}

    # 直接使用管理脚本删除
    args = [MANAGER_BIN, "remove", domain, "--yes"]
    if delete_cert:
        args.append("--delete-cert")

    result = run_cmd(args, timeout=90)

    if result["code"] == 0:
        output_parts = [f"✓ 已删除站点：{domain}"]
        if delete_cert:
            output_parts.append("✓ 证书已彻底清理")
        else:
            output_parts.append("✓ 证书已保留")
        return {"code": 0, "output": "\n".join(output_parts)}
    else:
        return {"code": 3, "output": f"删除失败：\n{result['output']}"}


def rename_site(old_domain: str, new_domain: str, new_upstream: str = "", delete_old_cert: bool = False) -> dict[str, object]:
    """重命名站点（修改域名）"""
    old_domain = old_domain.strip().lower()
    new_domain = new_domain.strip().lower()

    if not DOMAIN_RE.match(old_domain):
        return {"code": 1, "output": "旧域名无效"}
    if not DOMAIN_RE.match(new_domain):
        return {"code": 2, "output": "新域名无效"}

    state_path = STATE_DIR / f"{old_domain}.env"
    if not state_path.exists():
        return {"code": 3, "output": "旧域名不存在"}

    new_state_path = STATE_DIR / f"{new_domain}.env"
    if new_state_path.exists():
        return {"code": 4, "output": "新域名已存在，请先删除或选择其他域名"}

    # 使用管理脚本重命名
    args = [MANAGER_BIN, "rename", old_domain, new_domain]
    if new_upstream:
        args.extend(["--upstream", new_upstream])
    if delete_old_cert:
        args.append("--delete-old-cert")

    result = run_cmd(args, timeout=180)

    if result["code"] == 0:
        output_parts = [
            f"✓ 重命名完成",
            f"  旧域名：{old_domain}",
            f"  新域名：{new_domain}"
        ]
        if delete_old_cert:
            output_parts.append("  ✓ 旧证书已删除")
        else:
            output_parts.append("  ✓ 旧证书已保留")
        return {"code": 0, "output": "\n".join(output_parts)}
    else:
        return {"code": 5, "output": f"重命名失败：\n{result['output']}"}


def force_reissue_certificate(domain: str) -> dict[str, object]:
    """强制重新申请证书：彻底删除现有证书后重新申请"""
    domain = domain.strip().lower()
    if not DOMAIN_RE.match(domain):
        return {"code": 1, "output": "域名无效"}

    state_path = STATE_DIR / f"{domain}.env"
    if not state_path.exists():
        return {"code": 2, "output": "状态文件不存在，该站点不是受管站点"}

    # 读取状态
    state = parse_state_file(state_path)
    if state.get("ENABLE_SSL") != "1":
        return {"code": 3, "output": "该站点未启用 HTTPS，无需重新申请证书"}

    output_lines = []

    # 1. 彻底删除现有证书
    output_lines.append("正在清理现有证书...")
    cert_dirs = [
        pathlib.Path(f"/etc/letsencrypt/live/{domain}"),
        pathlib.Path(f"/etc/letsencrypt/archive/{domain}"),
        pathlib.Path(f"/etc/letsencrypt/renewal/{domain}.conf"),
    ]

    # 先尝试使用 certbot delete
    run_cmd(["certbot", "delete", "--cert-name", domain, "--non-interactive"], timeout=30)

    # 强制删除目录
    for cert_path in cert_dirs:
        if cert_path.exists():
            try:
                if cert_path.is_dir():
                    import shutil
                    shutil.rmtree(cert_path)
                else:
                    cert_path.unlink()
                output_lines.append(f"✓ 已删除：{cert_path}")
            except Exception as e:
                output_lines.append(f"⚠ 删除失败：{cert_path} - {e}")

    # 2. 关闭 HTTPS（切换到纯 HTTP）
    output_lines.append("\n暂时切换到 HTTP 模式...")
    disable_result = run_cmd([MANAGER_BIN, "disable-ssl", domain], timeout=60)
    if disable_result["code"] != 0:
        return {"code": 4, "output": "\n".join(output_lines) + f"\n\n关闭 HTTPS 失败：\n{disable_result['output']}"}

    # 3. 重新申请证书并启用 HTTPS
    output_lines.append("\n重新申请证书...")
    enable_result = run_cmd([MANAGER_BIN, "enable-ssl", domain], timeout=180)
    if enable_result["code"] != 0:
        return {"code": 5, "output": "\n".join(output_lines) + f"\n\n证书申请失败：\n{enable_result['output']}"}

    output_lines.append("\n✓ 证书已重新申请并启用")
    return {"code": 0, "output": "\n".join(output_lines)}


def create_backup() -> dict[str, object]:
    """创建配置备份"""
    result = run_cmd([MANAGER_BIN, "backup"], timeout=60)
    if result["code"] == 0:
        return {"code": 0, "output": result["output"]}
    else:
        return {"code": 1, "output": f"备份失败：\n{result['output']}"}


def list_backups() -> dict[str, object]:
    """列出所有备份"""
    result = run_cmd([MANAGER_BIN, "list-backups"], timeout=10)
    if result["code"] == 0:
        return {"code": 0, "output": result["output"]}
    else:
        return {"code": 1, "output": f"获取备份列表失败：\n{result['output']}"}


def restore_backup(backup_file: str) -> dict[str, object]:
    """恢复备份"""
    backup_file = backup_file.strip()
    if not backup_file:
        return {"code": 1, "output": "备份文件路径不能为空"}

    result = run_cmd([MANAGER_BIN, "restore", backup_file], timeout=120)
    if result["code"] == 0:
        return {"code": 0, "output": result["output"]}
    else:
        return {"code": 1, "output": f"恢复失败：\n{result['output']}"}


def health_check(domain: str = "") -> dict[str, object]:
    """健康检查"""
    args = [MANAGER_BIN, "health-check"]
    if domain:
        domain = domain.strip().lower()
        if not DOMAIN_RE.match(domain):
            return {"code": 1, "output": "域名无效"}
        args.append(domain)

    result = run_cmd(args, timeout=60)
    return {"code": result["code"], "output": result["output"]}


def parse_friendly_error(output: str) -> str:
    """将技术错误信息转换为用户友好的提示"""
    output_lower = output.lower()

    # Let's Encrypt 速率限制
    if "too many certificates" in output_lower or "rate limit" in output_lower:
        return """❌ Let's Encrypt 速率限制

您已达到该域名的申请限制（每周最多 5 次）。

解决方案：
1. 等待 7 天后重试
2. 检查是否有重复申请的情况
3. 使用"强制重新申请"功能清理残留证书

详细日志：
""" + output

    # DNS 验证失败
    if "connection timed out" in output_lower and "acme-challenge" in output_lower:
        return """❌ 域名验证失败（80端口不可达）

Let's Encrypt 无法通过 HTTP 验证您的域名。

可能原因：
1. 防火墙未开放 80 端口
2. 云服务商安全组未放行 80 端口
3. DNS 未正确解析到本服务器
4. Nginx 未监听 80 端口

解决步骤：
1. 检查防火墙：sudo ufw allow 80
2. 检查安全组：登录云服务商控制台
3. 检查 DNS：nslookup 您的域名
4. 检查 Nginx：sudo netstat -tlnp | grep :80

详细日志：
""" + output

    # DNS 未解析
    if "no valid a records found" in output_lower or "nxdomain" in output_lower:
        return """❌ DNS 未正确解析

该域名没有有效的 A 记录。

解决步骤：
1. 登录 DNS 服务商（如 Cloudflare、阿里云）
2. 添加 A 记录指向本服务器 IP
3. 等待 5-10 分钟让 DNS 生效
4. 验证：dig 您的域名 或 nslookup 您的域名

详细日志：
""" + output

    # 证书已存在但有问题
    if "certificate already exists" in output_lower or "already exists" in output_lower:
        return """⚠️ 证书文件冲突

检测到证书残留文件，请使用"强制重新申请"功能清理后重试。

详细日志：
""" + output

    # 后端连接失败
    if "connection refused" in output_lower or "failed to connect" in output_lower:
        return """❌ 后端服务连接失败

无法连接到后端服务。

检查步骤：
1. 确认后端服务已启动
2. 检查端口号是否正确
3. 测试连接：curl http://127.0.0.1:端口号

详细日志：
""" + output

    # Nginx 配置错误
    if "nginx: configuration file" in output_lower and "test failed" in output_lower:
        return """❌ Nginx 配置错误

配置文件语法错误，已自动回滚。

详细日志：
""" + output

    # 默认返回原始输出
    return output


def check_certificate_permissions() -> dict[str, object]:
    """检查所有证书的权限问题"""
    if not pathlib.Path("/etc/letsencrypt/live").exists():
        return {"code": 1, "total": 0, "issues": [], "output": "未找到证书目录 /etc/letsencrypt/live"}

    issues = []
    total = 0
    output_lines = []

    try:
        for cert_dir in pathlib.Path("/etc/letsencrypt/live").iterdir():
            if not cert_dir.is_dir() or cert_dir.name == "README":
                continue

            total += 1
            domain = cert_dir.name
            output_lines.append(f"检查证书: {domain}")

            # 检查目录权限
            stat = cert_dir.stat()
            import pwd
            try:
                owner = pwd.getpwuid(stat.st_uid).pw_name
            except KeyError:
                owner = f"uid:{stat.st_uid}"

            if owner != "root":
                issues.append(f"{domain}: 目录所有者是 {owner}，应为 root")
                output_lines.append(f"  ✗ 目录所有者: {owner} (应为 root)")
            else:
                output_lines.append(f"  ✓ 目录所有者: {owner}")

            # 检查证书文件
            for cert_file in ["fullchain.pem", "privkey.pem", "cert.pem", "chain.pem"]:
                file_path = cert_dir / cert_file
                if file_path.exists():
                    file_stat = file_path.stat()
                    try:
                        file_owner = pwd.getpwuid(file_stat.st_uid).pw_name
                    except KeyError:
                        file_owner = f"uid:{file_stat.st_uid}"

                    if file_owner != "root":
                        issues.append(f"{domain}/{cert_file}: 所有者是 {file_owner}，应为 root")
                        output_lines.append(f"  ✗ {cert_file}: {file_owner} (应为 root)")

                    # 检查权限
                    mode = oct(file_stat.st_mode)[-3:]
                    if cert_file == "privkey.pem" and mode != "600":
                        issues.append(f"{domain}/{cert_file}: 权限是 {mode}，应为 600")
                        output_lines.append(f"  ✗ {cert_file}: {mode} (应为 600)")
                    elif cert_file != "privkey.pem" and mode not in ["644", "640"]:
                        issues.append(f"{domain}/{cert_file}: 权限是 {mode}，应为 644")
                        output_lines.append(f"  ✗ {cert_file}: {mode} (应为 644)")

        output_lines.append("")
        output_lines.append(f"总计检查: {total} 个证书")
        output_lines.append(f"发现问题: {len(issues)} 个")

        return {
            "code": 0,
            "total": total,
            "issues": issues,
            "output": "\n".join(output_lines)
        }

    except Exception as e:
        return {
            "code": 2,
            "total": total,
            "issues": issues,
            "output": f"检查失败: {e}\n" + "\n".join(output_lines)
        }


def fix_certificate_permissions() -> dict[str, object]:
    """修复所有证书的权限"""
    output_lines = []

    try:
        output_lines.append("步骤 1/4: 修改证书目录所有者...")
        result = run_cmd(["chown", "-R", "root:root", "/etc/letsencrypt/"], timeout=30)
        if result["code"] != 0:
            return {"code": 1, "output": f"修改所有者失败:\n{result['output']}"}
        output_lines.append("✓ 证书目录所有者已修改为 root:root")

        output_lines.append("\n步骤 2/4: 设置目录权限...")
        # 设置目录权限为 755（可进入）
        result = run_cmd([
            "find", "/etc/letsencrypt/",
            "-type", "d",
            "-exec", "chmod", "755", "{}", "+"
        ], timeout=30)
        if result["code"] == 0:
            output_lines.append("✓ 目录权限已设置为 755")
        else:
            output_lines.append(f"⚠ 设置目录权限时出现问题: {result['output']}")

        output_lines.append("\n步骤 3/4: 设置证书文件权限...")
        # 设置所有 .pem 文件为 644（默认）
        result = run_cmd([
            "find", "/etc/letsencrypt/",
            "-type", "f",
            "-name", "*.pem",
            "-exec", "chmod", "644", "{}", "+"
        ], timeout=30)
        if result["code"] == 0:
            output_lines.append("✓ 证书文件已设置为 644")

        # 设置私钥文件为 600
        result = run_cmd([
            "find", "/etc/letsencrypt/",
            "-type", "f",
            "-name", "privkey*.pem",
            "-exec", "chmod", "600", "{}", "+"
        ], timeout=30)
        if result["code"] == 0:
            output_lines.append("✓ 私钥文件已设置为 600")
        else:
            output_lines.append(f"⚠ 设置私钥权限时出现问题: {result['output']}")

        output_lines.append("\n步骤 4/4: 重载 nginx...")
        result = run_cmd(["systemctl", "reload", "nginx"], timeout=30)
        if result["code"] != 0:
            result = run_cmd(["nginx", "-s", "reload"], timeout=30)
        if result["code"] == 0:
            output_lines.append("✓ Nginx 已重载")
        else:
            output_lines.append(f"⚠ Nginx 重载失败: {result['output']}")

        output_lines.append("\n✓ 证书权限修复完成！")

        return {"code": 0, "output": "\n".join(output_lines)}

    except Exception as e:
        output_lines.append(f"\n✗ 修复失败: {e}")
        return {"code": 2, "output": "\n".join(output_lines)}


def install_permission_fix_hook() -> dict[str, object]:
    """安装自动修复权限的 certbot hook"""
    hook_dir = pathlib.Path("/etc/letsencrypt/renewal-hooks/post")
    hook_file = hook_dir / "fix-permissions.sh"

    try:
        hook_dir.mkdir(parents=True, exist_ok=True)

        script_content = """#!/bin/bash
# 自动修复证书权限
# 在 certbot 续期后自动运行

echo "正在修复证书权限..."

# 修改所有者
chown -R root:root /etc/letsencrypt/

# 设置目录权限（必须可进入）
find /etc/letsencrypt/ -type d -exec chmod 755 {} +

# 设置证书文件权限
find /etc/letsencrypt/ -type f -name "*.pem" -exec chmod 644 {} +

# 设置私钥权限
find /etc/letsencrypt/ -type f -name "privkey*.pem" -exec chmod 600 {} +

echo "正在重载 nginx..."
systemctl reload nginx 2>/dev/null || nginx -s reload

echo "✓ 证书权限修复完成"
"""

        hook_file.write_text(script_content, encoding="utf-8")
        os.chmod(hook_file, 0o755)

        output = f"""✓ 自动修复脚本已安装

脚本位置: {hook_file}

该脚本将在以下情况自动运行：
• certbot 续期证书后
• certbot renew 命令执行后

您也可以手动运行测试：
  sudo {hook_file}
"""
        return {"code": 0, "output": output}

    except Exception as e:
        return {"code": 1, "output": f"安装脚本失败: {e}"}


def verify_certificate_permissions() -> dict[str, object]:
    """验证证书权限修复结果"""
    output_lines = []
    all_ok = True

    try:
        output_lines.append("正在验证证书权限...")

        if not pathlib.Path("/etc/letsencrypt/live").exists():
            return {"code": 1, "success": False, "output": "证书目录不存在"}

        for cert_dir in pathlib.Path("/etc/letsencrypt/live").iterdir():
            if not cert_dir.is_dir() or cert_dir.name == "README":
                continue

            domain = cert_dir.name
            fullchain = cert_dir / "fullchain.pem"

            if not fullchain.exists():
                output_lines.append(f"✗ {domain}: fullchain.pem 不存在")
                all_ok = False
                continue

            # 测试 www-data 用户能否读取
            result = run_cmd(["sudo", "-u", "www-data", "cat", str(fullchain)], timeout=5)
            if result["code"] == 0:
                output_lines.append(f"✓ {domain}: 证书可读取")
            else:
                output_lines.append(f"✗ {domain}: 证书无法读取")
                all_ok = False

        if all_ok:
            output_lines.append("\n✓ 所有证书验证通过！")
        else:
            output_lines.append("\n⚠ 部分证书仍有问题，请重新修复")

        return {
            "code": 0,
            "success": all_ok,
            "output": "\n".join(output_lines)
        }

    except Exception as e:
        return {
            "code": 2,
            "success": False,
            "output": f"验证失败: {e}\n" + "\n".join(output_lines)
        }


def clean_duplicate_configs() -> dict[str, object]:
    """清理重复的备份配置文件"""
    output_lines = []
    cleaned = 0

    try:
        output_lines.append("正在扫描备份配置文件...\n")

        # 扫描 sites-enabled 和 sites-available 目录
        for directory in ["/etc/nginx/sites-enabled", "/etc/nginx/sites-available"]:
            dir_path = pathlib.Path(directory)
            if not dir_path.exists():
                continue

            output_lines.append(f"扫描目录: {directory}")

            for file_path in dir_path.iterdir():
                if not file_path.is_file() and not file_path.is_symlink():
                    continue

                filename = file_path.name

                # 匹配备份文件：.bak-*, .bak, .old, *~
                if any([
                    ".bak-" in filename,
                    filename.endswith(".bak"),
                    filename.endswith(".old"),
                    filename.endswith("~"),
                ]):
                    try:
                        if file_path.is_symlink():
                            file_path.unlink()
                        else:
                            file_path.unlink()
                        output_lines.append(f"  ✓ 已删除: {filename}")
                        cleaned += 1
                    except Exception as e:
                        output_lines.append(f"  ✗ 删除失败: {filename} ({e})")

        output_lines.append(f"\n清理完成：删除了 {cleaned} 个备份配置文件")

        # 重载 nginx
        if cleaned > 0:
            output_lines.append("\n正在重载 nginx...")
            reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=30)
            if reload_result["code"] != 0:
                reload_result = run_cmd(["nginx", "-s", "reload"], timeout=30)

            if reload_result["code"] == 0:
                output_lines.append("✓ nginx 已重载")
            else:
                output_lines.append(f"⚠ nginx 重载失败: {reload_result['output'][:150]}")

        return {
            "code": 0,
            "success": True,
            "cleaned": cleaned,
            "output": "\n".join(output_lines)
        }

    except Exception as e:
        return {
            "code": 1,
            "success": False,
            "cleaned": cleaned,
            "output": f"清理失败: {e}\n" + "\n".join(output_lines)
        }



def check_legacy_sites() -> dict[str, object]:
    """检查使用旧配置的站点"""
    try:
        # 只检查受管站点的状态文件
        managed_sites = list_managed_sites()
        legacy_sites = []

        for site_state in managed_sites:
            domain = site_state.get("DOMAIN", "")
            if not domain:
                continue

            # 已经迁移过的，跳过
            if site_state.get("MIGRATED") == "1":
                continue

            # 检查是否为接管但未迁移的站点
            if site_state.get("IMPORTED") == "1" and site_state.get("MIGRATED") != "1":
                # 查找原始配置文件
                imported_source = site_state.get("IMPORTED_SOURCE", "")

                issues = ["已接管但未迁移到标准配置"]

                # 检查原始配置文件
                if imported_source and pathlib.Path(imported_source).exists():
                    try:
                        content = pathlib.Path(imported_source).read_text(encoding="utf-8", errors="ignore")
                        if "if ($host =" in content or "if($host=" in content:
                            issues.append("使用了 if 条件判断（Certbot风格）")
                        if "managed by Certbot" in content:
                            issues.append("包含 Certbot 标记")
                    except Exception:
                        pass

                legacy_sites.append({
                    "domain": domain,
                    "source": imported_source or f"未知（状态文件：{domain}.env）",
                    "issues": issues
                })

        return {
            "code": 0,
            "legacy_sites": legacy_sites,
            "total": len(legacy_sites)
        }

    except Exception as e:
        return {
            "code": 1,
            "legacy_sites": [],
            "total": 0,
            "error": str(e)
        }


def migrate_legacy_sites() -> dict[str, object]:
    """迁移旧配置站点到标准格式（无备份，直接迁移）"""
    output_lines = []
    migrated = 0
    failed = 0

    try:
        # 检查旧配置站点
        check_result = check_legacy_sites()
        legacy_sites = check_result.get("legacy_sites", [])

        if not legacy_sites:
            return {
                "code": 0,
                "success": True,
                "migrated": 0,
                "failed": 0,
                "output": "✓ 所有站点均使用标准配置，无需迁移"
            }

        output_lines.append(f"发现 {len(legacy_sites)} 个需要迁移的站点\n")

        for site in legacy_sites:
            domain = site["domain"]
            imported_source = site["source"]

            output_lines.append(f"▶ 迁移: {domain}")

            try:
                # 1. 读取状态文件
                state_path = STATE_DIR / f"{domain}.env"
                if not state_path.exists():
                    output_lines.append(f"  ✗ 状态文件不存在\n")
                    failed += 1
                    continue

                state = {}
                for line in state_path.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.startswith("#"):
                        key, value = line.split("=", 1)
                        state[key.strip()] = value.strip()

                # 防止重复迁移
                if state.get("MIGRATED") == "1":
                    output_lines.append(f"  ⊘ 已迁移过，跳过\n")
                    continue

                upstream = state.get("UPSTREAM", "")
                upstream_scheme = state.get("UPSTREAM_SCHEME", "http")
                enable_ssl = state.get("ENABLE_SSL", "0")

                if not upstream:
                    output_lines.append(f"  ✗ 后端配置缺失\n")
                    failed += 1
                    continue

                # 2. 删除旧的管理器配置
                old_conf = pathlib.Path(f"/etc/nginx/sites-available/vpspm-{domain}.conf")
                old_link = pathlib.Path(f"/etc/nginx/sites-enabled/vpspm-{domain}.conf")
                if old_conf.exists():
                    old_conf.unlink()
                if old_link.exists():
                    old_link.unlink()

                # 3. 重建标准配置
                args = [MANAGER_BIN, "add", domain, upstream, "--upstream-scheme", upstream_scheme, "--no-ssl"]
                result = run_cmd(args, timeout=60)
                if result["code"] != 0:
                    output_lines.append(f"  ✗ 配置重建失败")
                    output_lines.append(f"    {result['output'][:150]}\n")
                    failed += 1
                    continue

                output_lines.append(f"  ✓ 标准配置已创建")

                # 4. 启用HTTPS（如果原来启用了）
                if enable_ssl == "1":
                    ssl_result = run_cmd([MANAGER_BIN, "enable-ssl", domain], timeout=60)
                    if ssl_result["code"] == 0:
                        output_lines.append(f"  ✓ HTTPS已启用（复用证书）")
                    else:
                        output_lines.append(f"  ⚠ HTTPS启用失败")
                        output_lines.append(f"    {ssl_result['output'][:150]}")

                # 5. 注释原始配置文件
                if imported_source and imported_source != "未知" and pathlib.Path(imported_source).exists():
                    comment_result = comment_out_nginx_config(domain, imported_source)
                    if comment_result["code"] == 0:
                        output_lines.append(f"  ✓ 原始配置已注释: {imported_source}")
                    else:
                        # 注释失败不影响迁移成功
                        output_lines.append(f"  ⚠ 原始配置注释失败（可能已注释）")

                # 6. 更新状态：标记为已迁移
                state["MIGRATED"] = "1"
                state["IMPORTED"] = "0"
                write_managed_state(domain, state)

                output_lines.append(f"  ✓ 迁移完成\n")
                migrated += 1

            except Exception as e:
                output_lines.append(f"  ✗ 异常: {str(e)[:150]}\n")
                failed += 1

        # 7. 重载 nginx
        output_lines.append("正在重载 nginx...")
        reload_result = run_cmd(["nginx", "-t"], timeout=10)
        if reload_result["code"] != 0:
            output_lines.append(f"⚠ nginx 配置测试失败:")
            output_lines.append(reload_result["output"][:300])
            return {
                "code": 1,
                "success": False,
                "migrated": migrated,
                "failed": failed,
                "output": "\n".join(output_lines)
            }

        reload_result = run_cmd(["systemctl", "reload", "nginx"], timeout=30)
        if reload_result["code"] != 0:
            reload_result = run_cmd(["nginx", "-s", "reload"], timeout=30)

        if reload_result["code"] == 0:
            output_lines.append("✓ nginx 已重载\n")
        else:
            output_lines.append(f"✗ nginx 重载失败: {reload_result['output'][:150]}\n")

        output_lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        output_lines.append(f"成功: {migrated} 个 | 失败: {failed} 个")

        return {
            "code": 0,
            "success": failed == 0,
            "migrated": migrated,
            "failed": failed,
            "output": "\n".join(output_lines)
        }

    except Exception as e:
        import traceback
        output_lines.append(f"\n✗ 迁移过程异常: {str(e)}")
        output_lines.append(traceback.format_exc()[:300])
        return {
            "code": 2,
            "success": False,
            "migrated": migrated,
            "failed": failed,
            "output": "\n".join(output_lines)
        }


def run_cmd(args: list[str], timeout: int = 90) -> dict[str, object]:
    try:
        proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        output = proc.stdout.strip()
        # 对失败的命令使用友好错误提示
        if proc.returncode != 0:
            output = parse_friendly_error(output)
        return {"code": proc.returncode, "output": output}
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return {"code": 124, "output": f"❌ 命令执行超时（{timeout}秒）\n\n可能原因：\n1. 网络连接缓慢\n2. Let's Encrypt 服务响应慢\n3. 服务器负载过高\n\n部分输出：\n{output}".strip()}
    except OSError as exc:
        return {"code": 127, "output": f"❌ 命令执行失败\n\n错误：{exc}\n\n请确认：\n1. 相关命令已安装（nginx、certbot）\n2. 具有执行权限\n3. 系统资源充足"}


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
                    from urllib.parse import unquote
                    domain = unquote(part.split("=", 1)[1])
            if not domain:
                self.send_json({"error": "缺少 domain 参数"}, 400)
                return

            # 查找证书路径
            try:
                servers = list_nginx_servers()
                server = next((s for s in servers if s.get("domain") == domain or s.get("managed_domain") == domain), None)

                if not server:
                    # 调试：列出所有可用域名
                    all_domains = [s.get("domain") for s in servers]
                    self.send_json({
                        "error": f"未找到该站点: {domain}",
                        "debug": f"可用域名: {', '.join(all_domains)}"
                    }, 404)
                    return

                # 检查站点是否启用了HTTPS
                if not server.get("https"):
                    self.send_json({
                        "error": "该站点未启用 HTTPS，无证书信息",
                        "status": "none",
                        "debug": f"server keys: {list(server.keys())}"
                    }, 400)
                    return

                cert_path = str(server.get("ssl_cert_path") or "")
                if not cert_path:
                    cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"

                detail = read_certificate_detail(cert_path)
                self.send_json({"domain": domain, "cert_path": cert_path, **detail})
                return
            except Exception as e:
                import traceback
                self.send_json({
                    "error": f"处理请求时出错: {str(e)}",
                    "traceback": traceback.format_exc()
                }, 500)
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

        # 不依赖 domain 的路由
        if path == "/api/backup/create":
            result = create_backup()
            self.send_json({"message": "备份已创建", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/backup/list":
            result = list_backups()
            self.send_json({"message": "备份列表", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/backup/restore":
            backup_file = str(data.get("backup_file", "")).strip()
            if not backup_file:
                self.send_json({"error": "缺少backup_file参数"}, 400)
                return
            result = restore_backup(backup_file)
            self.send_json({"message": "备份已恢复", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/health/check":
            check_domain = str(data.get("domain", "")).strip()
            result = health_check(check_domain)
            self.send_json({"message": "健康检查完成", **result}, 200 if result["code"] == 0 else 500)
            return

        domain = str(data.get("domain", "")).strip()
        if path == "/api/sites/take-over":
            result = take_over_site(domain, str(data.get("source", "")).strip())
            self.send_json({"message": "站点已纳入管理", **result}, 200 if result["code"] == 0 else 400)
            return

        if path == "/api/sites/update":
            scheme, upstream = parse_edit_target(str(data.get("target", "")))
            if not scheme or not upstream:
                self.send_json({"error": "后端地址必须是 HOST:PORT 或 http(s)://HOST:PORT"}, 400)
                return
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
            delete_cert = data.get("delete_cert", False)
            result = remove_site_with_backup(domain, delete_cert)
            self.send_json({"message": "站点已删除", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/sites/rename":
            new_domain = str(data.get("new_domain", "")).strip()
            new_upstream = str(data.get("new_upstream", "")).strip()
            delete_old_cert = data.get("delete_old_cert", False)
            if not new_domain:
                self.send_json({"error": "缺少new_domain参数"}, 400)
                return
            result = rename_site(domain, new_domain, new_upstream, delete_old_cert)
            self.send_json({"message": "站点已重命名", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/nginx/comment-out":
            source = str(data.get("source", "")).strip()
            if not source:
                self.send_json({"error": "缺少source参数"}, 400)
                return
            result = comment_out_nginx_config(domain, source)
            self.send_json({"message": "已注释nginx配置", **result}, 200 if result["code"] == 0 else 400)
            return

        if path == "/api/certs/renew":
            result = run_cmd([MANAGER_BIN, "renew", domain], timeout=180)
            self.send_json({"message": "证书续期完成", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/certs/force-reissue":
            # 强制重新申请证书：先彻底删除，再重新申请
            result = force_reissue_certificate(domain)
            self.send_json({"message": "证书已重新申请", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/certs/set-auto-renew":
            enable = "1" if data.get("enable", True) else "0"
            result = run_cmd([MANAGER_BIN, "set-auto-renew", domain, enable], timeout=30)
            self.send_json({"message": "自动续期设置已更新", **result}, 200 if result["code"] == 0 else 500)
            return

        # 证书迁移相关 API
        if path == "/api/migrate/check":
            result = check_certificate_permissions()
            self.send_json(result, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/migrate/fix":
            result = fix_certificate_permissions()
            self.send_json({"message": "证书权限修复完成", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/migrate/install-hook":
            result = install_permission_fix_hook()
            self.send_json({"message": "自动修复脚本已安装", **result}, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/migrate/verify":
            result = verify_certificate_permissions()
            self.send_json(result, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/migrate/check-legacy":
            result = check_legacy_sites()
            self.send_json(result, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/migrate/migrate-legacy":
            result = migrate_legacy_sites()
            self.send_json(result, 200 if result["code"] == 0 else 500)
            return

        if path == "/api/migrate/clean-duplicates":
            result = clean_duplicate_configs()
            self.send_json(result, 200 if result["code"] == 0 else 500)
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
