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
PASSWORD_HASH = os.environ.get("HNG_WEB_PASSWORD_HASH", "")  # 优先使用 hash
TOTP_SECRET = os.environ.get("HNG_WEB_TOTP_SECRET", "")  # 2FA密钥
SECRET = os.environ.get("HNG_WEB_SECRET", "") or secrets.token_urlsafe(32)
COOKIE_NAME = "hng_session"
SESSION_TTL = 30 * 60  # 30分钟超时
SESSION_STORE = {}  # {token: {"expires": timestamp, "last_active": timestamp}}
LOGIN_ATTEMPTS = {}  # {ip: {"count": int, "locked_until": timestamp}}
DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z0-9.-]+$")
CERT_WARN_DAYS = int(os.environ.get("HNG_CERT_WARN_DAYS", "30"))
CERT_CRITICAL_DAYS = int(os.environ.get("HNG_CERT_CRITICAL_DAYS", "7"))

def generate_totp_secret() -> str:
    """生成 TOTP 密钥（Base32）"""
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii')

def generate_totp_code(secret: str) -> str:
    """生成 TOTP 6位数字码"""
    import struct
    key = base64.b32decode(secret)
    timestamp = int(time.time() // 30)
    msg = struct.pack('>Q', timestamp)
    hmac_hash = hmac.new(key, msg, hashlib.sha1).digest()
    offset = hmac_hash[-1] & 0x0F
    code = struct.unpack('>I', hmac_hash[offset:offset+4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)

def verify_totp_code(secret: str, code: str) -> bool:
    """验证 TOTP 码（允许前后30秒误差）"""
    if not secret or not code:
        return False
    try:
        import struct
        key = base64.b32decode(secret)
        timestamp = int(time.time() // 30)
        for offset in [-1, 0, 1]:  # 检查前后30秒
            msg = struct.pack('>Q', timestamp + offset)
            hmac_hash = hmac.new(key, msg, hashlib.sha1).digest()
            pos = hmac_hash[-1] & 0x0F
            expected = struct.unpack('>I', hmac_hash[pos:pos+4])[0] & 0x7FFFFFFF
            expected_code = str(expected % 1000000).zfill(6)
            if hmac.compare_digest(code, expected_code):
                return True
        return False
    except Exception:
        return False

def generate_totp_qr(secret: str, account: str = "admin") -> str:
    """生成 TOTP 二维码（Base64）"""
    try:
        import urllib.parse
        # otpauth://totp/Host%20Nginx%20Manager:admin?secret=xxx&issuer=Host%20Nginx%20Manager
        label = urllib.parse.quote(f"Host Nginx Manager:{account}")
        issuer = urllib.parse.quote("Host Nginx Manager")
        uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"

        # 简单的二维码生成（纯文本，前端可用库生成）
        # 这里返回一个指向在线QR生成器的URL
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(uri)}"
        return qr_url
    except Exception:
        return ""


def hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 哈希密码"""
    salt = secrets.token_bytes(32)
    pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return base64.b64encode(salt + pwdhash).decode('ascii')

def verify_password(password: str, hash_str: str) -> bool:
    """验证密码"""
    try:
        decoded = base64.b64decode(hash_str)
        salt = decoded[:32]
        stored_hash = decoded[32:]
        pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return hmac.compare_digest(pwdhash, stored_hash)
    except Exception:
        return False

def validate_password_strength(password: str) -> tuple[bool, str]:
    """验证密码强度
    返回: (是否通过, 错误信息)
    """
    import re

    if len(password) < 12:
        return False, "密码长度至少12位"

    if not re.search(r'[A-Z]', password):
        return False, "密码必须包含大写字母"

    if not re.search(r'[a-z]', password):
        return False, "密码必须包含小写字母"

    if not re.search(r'[0-9]', password):
        return False, "密码必须包含数字"

    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "密码必须包含特殊字符"

    # 检查连续数字
    sequential_nums = ['012','123','234','345','456','567','678','789','890','987','876','765','654','543','432','321','210']
    if any(seq in password for seq in sequential_nums):
        return False, "密码不能包含连续数字"

    # 检查连续字母
    sequential_letters = ['abc','bcd','cde','def','efg','fgh','ghi','hij','ijk','jkl','klm','lmn','mno','nop','opq','pqr','qrs','rst','stu','tuv','uvw','vwx','wxy','xyz','zyx','yxw','xwv','wvu','vut','uts','tsr','srq','rqp','qpo','pon','onm','nml','mlk','lkj','kji','jih','ihg','hgf','gfe','fed','edc','dcb','cba']
    if any(seq in password.lower() for seq in sequential_letters):
        return False, "密码不能包含连续字母"

    return True, ""

def check_login_attempts(ip: str) -> bool:
    """检查登录限流"""
    now = time.time()
    if ip in LOGIN_ATTEMPTS:
        attempt = LOGIN_ATTEMPTS[ip]
        if attempt.get("locked_until", 0) > now:
            return False  # 仍在锁定期
        if attempt.get("count", 0) >= 5:
            LOGIN_ATTEMPTS[ip] = {"count": 0, "locked_until": now + 300}  # 锁定5分钟
            return False
    return True

def record_failed_login(ip: str):
    """记录登录失败"""
    if ip not in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS[ip] = {"count": 0}
    LOGIN_ATTEMPTS[ip]["count"] = LOGIN_ATTEMPTS[ip].get("count", 0) + 1

def clean_expired_sessions():
    """清理过期 session"""
    now = time.time()
    expired = [token for token, data in SESSION_STORE.items() if data["expires"] < now]
    for token in expired:
        del SESSION_STORE[token]


PAGE_CSS = r'''
  <style>
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --line:#e4e7eb; --text:#17202a; --muted:#667085; --blue:#1b64d8; --blue2:#eaf1ff; --red:#c62828; --green:#157347; --amber:#9a6700; --shadow:0 1px 3px rgba(16,24,40,.1), 0 1px 2px rgba(16,24,40,.06); }
    [data-theme="dark"] { color-scheme: dark; --bg:#18191a; --panel:#242526; --line:#3a3b3c; --text:#e4e6eb; --muted:#b0b3b8; --shadow:0 1px 3px rgba(0,0,0,0.3); }
    [data-theme="dark"] .shell aside { background:#1c1e21; }
    [data-theme="dark"] .btn { background:#3a3b3c; color:#e4e6eb; }
    [data-theme="dark"] .btn:hover { background:#4e4f50; border-color:#5a5b5c; }
    [data-theme="dark"] .btn.primary { background:var(--blue); color:#fff; }
    [data-theme="dark"] th { background:#3a3b3c; }
    [data-theme="dark"] tbody tr:hover { background:#2d2e2f; }
    [data-theme="dark"] input, [data-theme="dark"] select, [data-theme="dark"] textarea { background:#3a3b3c; color:#e4e6eb; border-color:#5a5b5c; }
    [data-theme="dark"] .tag { background:#3a3b3c; color:#b0b3b8; }
    [data-theme="dark"] .notice { background:#2d2e2f; border-color:#5a5b5c; }
    [data-theme="dark"] .modal { background:#242526; color:#e4e6eb; }
    kbd { display:inline-block; padding:3px 6px; font-family:monospace; font-size:12px; background:#f5f7fa; border:1px solid var(--line); border-radius:4px; box-shadow:0 1px 2px rgba(0,0,0,0.1); }
    [data-theme="dark"] kbd { background:#3a3b3c; border-color:#5a5b5c; }
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
      <label id="totpLabel" style="display:none">双因素认证码<input id="totpCode" type="text" pattern="[0-9]{6}" maxlength="6" placeholder="000000" autocomplete="off"></label>
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
    const payload = {password: $('#password').value};
    const totpInput = $('#totpCode');
    if(totpInput.offsetParent !== null){  // 可见
      payload.totpCode = totpInput.value;
    }
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if(!res.ok){
      if(data.require2FA){
        // 需要2FA，显示输入框
        $('#totpLabel').style.display = '';
        $('#totpCode').required = true;
        $('#totpCode').focus();
        showMsg('请输入双因素认证码', 'info');
      }else{
        throw new Error(data.error || '登录失败');
      }
    }else{
      window.location.replace('/');
    }
  } catch(err) {
    showMsg(err.message,'bad');
  } finally {
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
<div id="globalSearch" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;align-items:flex-start;justify-content:center;padding-top:10vh">
  <div style="background:var(--panel);border-radius:12px;width:90%;max-width:600px;box-shadow:0 20px 40px rgba(0,0,0,0.3)">
    <input id="globalSearchInput" type="text" placeholder="搜索站点、证书、操作... (Ctrl+K)" style="width:100%;border:none;padding:20px;font-size:16px;border-radius:12px 12px 0 0;background:var(--panel);color:var(--text);outline:none">
    <div id="globalSearchResults" style="max-height:400px;overflow-y:auto;border-top:1px solid var(--line)"></div>
  </div>
</div>
<div class="shell">
  <aside>
    <div class="brand">Host Nginx Manager</div>
    <nav class="nav">
      <button data-view="dashboard" class="active">概览</button>
      <button data-view="sites">站点</button>
      <button data-view="certs">证书</button>
      <button data-view="create">新增反代</button>
      <button data-view="issues">问题</button>
      <button data-view="services">本机服务</button>
      <button data-view="tools">维护</button>
      <button data-view="account">账户设置</button>
      <button data-view="help">帮助</button>
    </nav>
  </aside>
  <main>
    <header>
      <div><h1 id="title">概览</h1><div class="muted">管理宿主 nginx 的标准 HTTP/HTTPS 反向代理。</div></div>
      <div class="row"><button class="btn" id="themeBtn" onclick="toggleTheme()">🌙 暗色</button><button class="btn" id="refreshBtn">刷新</button><button class="btn" id="logoutBtn">退出</button></div>
    </header>
    <section id="message"></section>
    <section id="dashboard" class="view active">
      <div class="grid stats">
        <div class="panel"><div class="stat-label">站点总数</div><div id="siteCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">异常站点</div><div id="backendBadCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">证书预警</div><div id="certWarnCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">健康度</div><div id="healthPercent" class="stat-value">-</div></div>
      </div>
      <div class="grid dashboard-grid">
        <div class="panel">
          <h2>快速操作</h2>
          <div style="display:flex;flex-direction:column;gap:10px">
            <button class="btn primary" data-jump="create">新增反向代理</button>
            <button class="btn" data-jump="sites">管理站点</button>
            <button class="btn" data-jump="certs">证书管理</button>
            <button class="btn" onclick="runHealthCheck()">全站健康检查</button>
          </div>
        </div>
        <div class="panel">
          <div class="row"><h2>待处理问题</h2><span class="spacer"></span><button class="btn small" id="problemJumpBtn" type="button">查看全部</button></div>
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
        <div id="batchActions" style="display:none;margin:10px 0;padding:10px;background:#e3f2fd;border-radius:4px">
          <span style="margin-right:10px">已选中 <strong id="batchCount">0</strong> 个站点</span>
          <button class="btn small primary" onclick="batchEnableHttps()">批量启用HTTPS</button>
          <button class="btn small" onclick="batchSetAutoRenew(true)">批量开启自动续期</button>
          <button class="btn small danger" onclick="batchDelete()">批量删除</button>
          <button class="btn small" onclick="clearBatchSelection()">取消选择</button>
        </div>
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
          <select id="siteSort">
            <option value="domain">域名 A-Z</option>
            <option value="domain_desc">域名 Z-A</option>
            <option value="status">状态优先</option>
            <option value="https_first">HTTPS优先</option>
          </select>
          <button class="btn" id="siteSearchClear" type="button">清空筛选</button>
        </div>
        <div style="overflow:auto"><table><thead><tr><th style="width:30px"><input type="checkbox" id="selectAllSites" onchange="toggleSelectAll(this.checked)"></th><th class="domain-col">域名</th><th>监听</th><th class="type-col">类型与状态</th><th>目标/目录</th><th class="source-col">来源</th><th class="actions-col">操作</th></tr></thead><tbody id="siteRows"></tbody></table></div>
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
    <section id="create" class="view">
      <div class="panel">
        <h2>新增标准反向代理</h2>
        <div style="margin-bottom:20px">
          <label style="display:block;margin-bottom:8px;font-weight:500">快速配置模板</label>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px">
            <button type="button" class="btn" onclick="applyTemplate('api')">🔌 API 服务</button>
            <button type="button" class="btn" onclick="applyTemplate('web')">🌐 静态网站</button>
            <button type="button" class="btn" onclick="applyTemplate('websocket')">💬 WebSocket</button>
            <button type="button" class="btn" onclick="applyTemplate('upload')">📤 文件上传</button>
            <button type="button" class="btn" onclick="applyTemplate('custom')">⚙️ 自定义</button>
          </div>
        </div>
        <form id="createForm">
          <div class="form-grid">
            <label>域名<input name="domain" placeholder="api.example.com" required></label>
            <label>后端地址<input name="upstream" placeholder="127.0.0.1:3001" required></label>
            <label>后端协议<select name="scheme"><option value="http">http</option><option value="https">https</option></select></label>
            <label>邮箱<input name="email" placeholder="you@example.com"></label>
            <label>上传大小<input name="body" value="64m"></label>
            <label>读取超时<input name="readTimeout" value="300s"></label>
            <label>发送超时<input name="sendTimeout" value="300s"></label>
          </div>
          <label class="check"><input name="ssl" type="checkbox" checked> 立即申请证书并启用 HTTPS</label>
          <label class="check"><input name="backendInsecure" type="checkbox"> 后端是自签 HTTPS，关闭后端证书校验</label>
          <div class="row"><button class="btn primary" type="submit">创建站点</button></div>
        </form>
      </div>
    </section>
    <section id="tools" class="view">
      <div style="margin-bottom:20px">
        <div class="row" style="gap:10px">
          <button class="btn primary" onclick="switchToolsTab('basic')" id="toolsBasicBtn">基础维护</button>
          <button class="btn" onclick="switchToolsTab('advanced')" id="toolsAdvancedBtn">高级工具</button>
        </div>
      </div>

      <div id="toolsBasic">
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
              <button class="btn" onclick="toggleAutoCheck()">
                <span id="autoCheckStatus">开启自动检查</span>
              </button>
            </div>
            <div style="margin-top:10px;font-size:12px;color:var(--muted)" id="autoCheckInfo">定时检查：未启用</div>
          </div>
          <div class="panel"><h2>输出</h2><pre id="output">等待操作...</pre></div>
        </div>
      </div>

      <div id="toolsAdvanced" style="display:none">
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
      </div>
    </section>
    <section id="account" class="view">
      <div class="panel">
        <h2>账户设置</h2>

        <details open>
          <summary>🔑 修改密码</summary>
          <form id="changePasswordForm" style="max-width:500px;margin-top:15px">
            <label>当前密码
              <div style="position:relative">
                <input id="currentPassword" type="password" required autocomplete="current-password" style="padding-right:40px">
                <button type="button" onclick="togglePassword('currentPassword')" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);border:none;background:none;cursor:pointer;padding:4px;color:var(--muted)">👁️</button>
              </div>
            </label>
            <label>新密码
              <div style="position:relative">
                <input id="newPassword" type="password" required autocomplete="new-password" minlength="12" style="padding-right:40px">
                <button type="button" onclick="togglePassword('newPassword')" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);border:none;background:none;cursor:pointer;padding:4px;color:var(--muted)">👁️</button>
              </div>
            </label>
            <label>确认新密码
              <div style="position:relative">
                <input id="confirmPassword" type="password" required autocomplete="new-password" style="padding-right:40px">
                <button type="button" onclick="togglePassword('confirmPassword')" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);border:none;background:none;cursor:pointer;padding:4px;color:var(--muted)">👁️</button>
              </div>
            </label>
            <div class="notice" style="margin:10px 0;font-size:13px">
              <strong>密码要求：</strong>
              <ul style="margin:5px 0 0 20px">
                <li>最少 12 位字符</li>
                <li>必须包含大写字母 (A-Z)</li>
                <li>必须包含小写字母 (a-z)</li>
                <li>必须包含数字 (0-9)</li>
                <li>必须包含特殊字符 (!@#$%^&*)</li>
                <li>不能包含连续数字（如 123、456）</li>
                <li>不能包含连续字母（如 abc、xyz）</li>
              </ul>
            </div>
            <div id="passwordStrength" style="margin:10px 0"></div>
            <button type="submit" class="btn primary">修改密码</button>
          </form>
        </details>

        <details style="margin-top:20px">
          <summary>🔐 双因素认证 (2FA)</summary>
          <div id="twoFactorStatus" style="margin-top:15px"></div>
        </details>

        <details style="margin-top:20px">
          <summary>📊 登录信息</summary>
          <div id="sessionInfo" style="margin-top:15px"></div>
        </details>
      </div>
    </section>

    <section id="help" class="view">
      <div class="panel help-content">
        <h1>Host Nginx Manager 使用帮助</h1>
        <p>轻量级 Nginx 反向代理管理工具，支持批量操作、自动监控、配置模板等现代化功能。</p>

        <details open>
          <summary>⚡ 快捷功能</summary>
          <h3>全局搜索 (Ctrl+K)</h3>
          <p>按 <kbd>Ctrl+K</kbd> 或 <kbd>Cmd+K</kbd> 打开全局搜索，快速查找：</p>
          <ul>
            <li><strong>站点</strong> - 搜索域名、后端地址</li>
            <li><strong>证书</strong> - 搜索证书域名、到期时间</li>
            <li><strong>操作</strong> - 快速导航到功能页面</li>
          </ul>
          <p>按 <kbd>ESC</kbd> 关闭搜索。</p>

          <h3>暗色模式</h3>
          <p>点击右上角 <strong>🌙 暗色</strong> / <strong>☀️ 亮色</strong> 按钮切换主题。设置自动保存。</p>

          <h3>批量操作</h3>
          <p>在"站点"列表中：</p>
          <ol>
            <li>勾选多个站点（表头全选）</li>
            <li>点击底部批量操作按钮：
              <ul>
                <li><strong>批量启用HTTPS</strong> - 为多个站点申请证书</li>
                <li><strong>批量开启自动续期</strong> - 统一启用续期</li>
                <li><strong>批量删除</strong> - 删除多个站点</li>
              </ul>
            </li>
          </ol>

          <h3>站点排序</h3>
          <p>站点列表支持4种排序：</p>
          <ul>
            <li><strong>域名 A-Z / Z-A</strong> - 字母顺序</li>
            <li><strong>状态优先</strong> - 有问题的站点排前面</li>
            <li><strong>HTTPS优先</strong> - 已启用HTTPS的排前面</li>
          </ul>
        </details>

        <details>
          <summary>📖 快速开始</summary>
          <h3>1. 使用配置模板</h3>
          <p>点击"新增反代"，先选择场景模板：</p>
          <ul>
            <li><strong>🔌 API 服务</strong> - RESTful API、GraphQL（64MB/300s）</li>
            <li><strong>🌐 静态网站</strong> - React、Vue、HTML（10MB/60s）</li>
            <li><strong>💬 WebSocket</strong> - 实时通信、聊天（64MB/3600s）</li>
            <li><strong>📤 文件上传</strong> - 图片、视频上传（512MB/600s）</li>
            <li><strong>⚙️ 自定义</strong> - 手动配置所有参数</li>
          </ul>
          <p>模板自动填充最优参数，无需了解技术细节！</p>

          <h3>2. 填写站点信息</h3>
          <ul>
            <li><strong>域名</strong>：如 <code>api.example.com</code></li>
            <li><strong>后端地址</strong>：如 <code>127.0.0.1:3001</code></li>
            <li><strong>邮箱</strong>：用于证书申请通知</li>
          </ul>
          <p>勾选"立即申请证书"后创建，自动完成HTTPS配置！</p>

          <h3>3. 管理现有站点</h3>
          <p>在"站点"页面可以：</p>
          <ul>
            <li>编辑后端地址</li>
            <li>批量启用HTTPS</li>
            <li>删除站点</li>
            <li>接管已有nginx配置</li>
          </ul>
        </details>

        <details>
          <summary>🔍 自动监控</summary>
          <h3>健康检查</h3>
          <p>在"维护"页面可开启自动健康检查：</p>
          <ul>
            <li>每 <strong>5分钟</strong> 自动检查所有站点</li>
            <li>检查后端连接、DNS解析、证书有效期、Nginx配置</li>
            <li>发现问题立即通知</li>
            <li>设置自动保存，刷新后保持开启</li>
          </ul>
          <p><strong>建议：生产环境开启自动检查，及时发现问题！</strong></p>

          <h3>健康度指标</h3>
          <p>概览页显示整体健康度百分比：</p>
          <ul>
            <li><span class="tag ok">90%+</span> - 健康</li>
            <li><span class="tag warn">60-90%</span> - 注意</li>
            <li><span class="tag bad">&lt;60%</span> - 异常</li>
          </ul>
          <p>点击健康度卡片快速跳转到问题列表。</p>
        </details>

        <details>
          <summary>💾 备份与恢复</summary>
          <h3>创建备份</h3>
          <p>在"维护"页面点击"创建备份"，自动备份：</p>
          <ul>
            <li>✅ 站点状态文件（域名、后端配置）</li>
            <li>✅ Nginx 配置文件（反向代理规则）</li>
            <li>✅ SSL 证书和私钥（Let's Encrypt）</li>
            <li>✅ 证书续期配置</li>
          </ul>
          <p><strong>备份完整，可完全恢复服务！</strong></p>

          <h3>查看备份列表</h3>
          <p>点击"恢复备份"查看所有历史备份：</p>
          <ul>
            <li>显示文件名、大小、创建时间</li>
            <li><strong>恢复</strong> - 恢复到指定版本</li>
            <li><strong>删除</strong> - 清理不需要的备份</li>
          </ul>

          <h3>自动保护</h3>
          <p>恢复备份前会：</p>
          <ol>
            <li>自动备份当前配置</li>
            <li>恢复选定的备份</li>
            <li>测试 nginx 配置</li>
            <li>失败自动回滚</li>
          </ol>
          <p><strong>安全可靠，不用担心恢复失败！</strong></p>
        </details>

        <details>
          <summary>🔒 证书管理</summary>
          <h3>证书状态</h3>
          <ul>
            <li><span class="tag ok">证书 N 天</span> - 正常，剩余 N 天</li>
            <li><span class="tag warn">证书 N 天</span> - 即将过期（30天内）</li>
            <li><span class="tag bad">证书异常</span> - 缺失或失败</li>
          </ul>

          <h3>自动续期</h3>
          <p>Let's Encrypt 证书会在到期前自动续期。也可以手动续期。</p>

          <h3>DNS 检查</h3>
          <ul>
            <li><span class="tag ok">DNS 正常</span> - 已正确解析</li>
            <li><span class="tag bad">DNS 异常</span> - 未指向本机</li>
          </ul>
          <p>申请证书前确保DNS已指向本服务器！</p>
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
            <li>检查 DNS 状态标签</li>
            <li>确认云厂商安全组开放 80 和 443 端口</li>
            <li>触发速率限制需等待一周</li>
          </ol>

          <h3>后端连接失败</h3>
          <p><strong>检查方法：</strong></p>
          <pre>ss -lntp | grep :端口号
curl http://127.0.0.1:端口号</pre>
          <p>确认后端服务已启动且端口号正确。</p>

          <h3>批量操作失败</h3>
          <p>批量操作会显示成功/失败统计。失败的站点会在输出中详细说明原因。</p>
        </details>

        <details>
          <summary>❓ 常见问题</summary>
          <h3>Q: 工具会修改现有 nginx 配置吗？</h3>
          <p>A: 不会。只管理自己创建的站点（文件名包含 <code>vpspm-</code>）。</p>

          <h3>Q: 证书会自动续期吗？</h3>
          <p>A: 是的。Let's Encrypt 通过 certbot 的 systemd timer 自动续期。</p>

          <h3>Q: 备份保存在哪里？</h3>
          <p>A: <code>/etc/nginx/vps-proxy-manager/backups/</code></p>

          <h3>Q: 批量操作安全吗？</h3>
          <p>A: 安全。所有操作都有二次确认，失败自动回滚。</p>

          <h3>Q: 自动监控会影响性能吗？</h3>
          <p>A: 不会。每5分钟检查一次，开销极小。</p>

          <h3>Q: 暗色模式如何工作？</h3>
          <p>A: 使用 localStorage 保存设置，刷新后保持。</p>
        </details>

        <details>
          <summary>🎯 最佳实践</summary>
          <h3>新手建议</h3>
          <ul>
            <li>✅ 使用配置模板，避免参数错误</li>
            <li>✅ 创建站点前检查DNS状态</li>
            <li>✅ 开启自动健康检查</li>
            <li>✅ 定期创建备份（重大变更前）</li>
          </ul>

          <h3>运维建议</h3>
          <ul>
            <li>✅ 使用全局搜索提升效率</li>
            <li>✅ 批量操作处理多个站点</li>
            <li>✅ 关注健康度指标</li>
            <li>✅ 站点排序快速定位问题</li>
          </ul>

          <h3>安全建议</h3>
          <ul>
            <li>✅ 所有站点启用HTTPS</li>
            <li>✅ 设置复杂的管理密码</li>
            <li>✅ 定期查看证书到期时间</li>
            <li>✅ 保留多个备份版本</li>
            <li>✅ 启用双因素认证（2FA）增强安全</li>
          </ul>

          <h3>忘记密码？</h3>
          <p>通过 SSH 登录服务器后，使用以下命令重置密码：</p>
          <pre style="background:var(--panel);border:1px solid var(--line);padding:10px;border-radius:4px;overflow-x:auto"><code>python3 /opt/host-nginx-manager/web/host_nginx_web.py reset-password</code></pre>
          <p>按提示输入新密码后，重启服务生效：</p>
          <pre style="background:var(--panel);border:1px solid var(--line);padding:10px;border-radius:4px;overflow-x:auto"><code>systemctl restart host-nginx-manager-web</code></pre>
        </details>
      </div>
    </section>

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
const VIEW_TITLES = {dashboard:'概览',issues:'问题',sites:'站点',services:'本机服务',certs:'证书',create:'新增反代',tools:'维护',account:'账户设置',help:'帮助'};

function toggleTheme(){
  const currentTheme = document.documentElement.getAttribute('data-theme');
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);
  const btn = document.querySelector('#themeBtn');
  if(btn) btn.textContent = newTheme === 'dark' ? '☀️ 亮色' : '🌙 暗色';
}

// 初始化主题（在 DOM 加载前）
const savedTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', savedTheme);

function updateBatchSelection(){
  const checkboxes = document.querySelectorAll('.site-checkbox');
  const checked = Array.from(checkboxes).filter(cb => cb.checked);
  const count = checked.length;
  $('#batchCount').textContent = count;
  $('#batchActions').style.display = count > 0 ? 'block' : 'none';
  $('#selectAllSites').checked = count > 0 && count === checkboxes.length;
}

function toggleSelectAll(checked){
  document.querySelectorAll('.site-checkbox').forEach(cb => cb.checked = checked);
  updateBatchSelection();
}

function clearBatchSelection(){
  document.querySelectorAll('.site-checkbox').forEach(cb => cb.checked = false);
  updateBatchSelection();
}

function getSelectedDomains(){
  return Array.from(document.querySelectorAll('.site-checkbox:checked')).map(cb => cb.dataset.domain);
}

async function batchEnableHttps(){
  const domains = getSelectedDomains();
  if(domains.length === 0) return;
  if(!confirm(`确认为 ${domains.length} 个站点启用HTTPS？\n\n${domains.join('\n')}`)) return;

  let success = 0;
  let failed = 0;
  for(const domain of domains){
    try {
      await fetch('/api/sites/enable-ssl', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({domain})});
      success++;
    } catch(e) {
      failed++;
    }
  }
  alert(`批量操作完成\n成功：${success}\n失败：${failed}`);
  clearBatchSelection();
  refresh();
}

async function batchSetAutoRenew(enable){
  const domains = getSelectedDomains();
  if(domains.length === 0) return;
  if(!confirm(`确认为 ${domains.length} 个站点${enable?'开启':'关闭'}自动续期？\n\n${domains.join('\n')}`)) return;

  for(const domain of domains){
    await fetch('/api/certs/set-auto-renew', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({domain, enable})}).catch(()=>{});
  }
  alert('批量操作完成');
  clearBatchSelection();
  refresh();
}

async function batchDelete(){
  const domains = getSelectedDomains();
  if(domains.length === 0) return;
  if(!confirm(`⚠️ 危险操作：批量删除 ${domains.length} 个站点\n\n${domains.join('\n')}\n\n此操作不可撤销，是否继续？`)) return;

  for(const domain of domains){
    await fetch('/api/sites/remove', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({domain})}).catch(()=>{});
  }
  alert('批量删除完成');
  clearBatchSelection();
  refresh();
}

function switchToolsTab(tab){
  if(tab === 'basic'){
    $('#toolsBasic').style.display = 'block';
    $('#toolsAdvanced').style.display = 'none';
    $('#toolsBasicBtn').classList.add('primary');
    $('#toolsAdvancedBtn').classList.remove('primary');
  } else {
    $('#toolsBasic').style.display = 'none';
    $('#toolsAdvanced').style.display = 'block';
    $('#toolsBasicBtn').classList.remove('primary');
    $('#toolsAdvancedBtn').classList.add('primary');
  }
}
const CERT_WARN_STATES = new Set(['warn','missing','error','critical']);
const $ = (s) => document.querySelector(s);
function showMsg(text, type='info'){
  const msgEl = $('#message');
  if(!text){
    msgEl.innerHTML = '';
    return;
  }
  msgEl.innerHTML = `<div class="panel" style="font-size:16px;font-weight:bold;padding:15px;margin:10px 0"><span class="tag ${type}">${type}</span> ${escapeHtml(text)}</div>`;
  msgEl.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  // 5秒后自动消失
  setTimeout(() => { msgEl.innerHTML = ''; }, 5000);
}
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function api(path, opts={}){ const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts}); if(res.status===401){ window.location.replace('/login'); throw new Error('未登录'); } const data = await res.json(); if(!res.ok) throw new Error(data.error || data.output || '请求失败'); return data; }
async function load(){ state = await api('/api/status'); render(); }

function togglePassword(inputId){
  const input = $('#' + inputId);
  const btn = input.nextElementSibling;
  if(input.type === 'password'){
    input.type = 'text';
    btn.textContent = '🙈';
  }else{
    input.type = 'password';
    btn.textContent = '👁️';
  }
}

function checkPasswordStrength(password){
  // 检查连续数字（如 123, 234, 012）
  const hasSequentialNumbers = /(?:012|123|234|345|456|567|678|789|890|987|876|765|654|543|432|321|210)/i.test(password);

  // 检查连续字母（如 abc, xyz, cba）
  const hasSequentialLetters = /(?:abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|lmn|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz|zyx|yxw|xwv|wvu|vut|uts|tsr|srq|rqp|qpo|pon|onm|nml|mlk|lkj|kji|jih|ihg|hgf|gfe|fed|edc|dcb|cba)/i.test(password);

  const checks = {
    length: password.length >= 12,
    upper: /[A-Z]/.test(password),
    lower: /[a-z]/.test(password),
    number: /[0-9]/.test(password),
    special: /[!@#$%^&*(),.?":{}|<>]/.test(password),
    noSequentialNum: !hasSequentialNumbers,
    noSequentialLetter: !hasSequentialLetters
  };
  const score = Object.values(checks).filter(Boolean).length;
  let strength = '', color = '';
  if(score < 4) { strength = '弱'; color = 'var(--red)'; }
  else if(score < 7) { strength = '中'; color = 'var(--amber)'; }
  else { strength = '强'; color = 'var(--green)'; }
  return {checks, score, strength, color};
}

async function loadAccountInfo(){
  try{
    const data = await api('/api/account/info');
    // 显示2FA状态
    const twoFactorEl = $('#twoFactorStatus');
    if(data.twoFactorEnabled){
      twoFactorEl.innerHTML = `
        <div class="notice" style="background:var(--blue2);border-color:var(--blue)">
          <strong>✓ 已启用双因素认证</strong>
          <p style="margin:5px 0">您的账户受到双因素认证保护</p>
        </div>
        <button class="btn danger" style="margin-top:10px" onclick="disable2FA()">禁用双因素认证</button>
      `;
    }else{
      twoFactorEl.innerHTML = `
        <div class="notice" style="background:#fff3cd;border-color:var(--amber)">
          <strong>⚠️ 未启用双因素认证</strong>
          <p style="margin:5px 0">启用后可大幅提升账户安全性</p>
        </div>
        <button class="btn primary" style="margin-top:10px" onclick="setup2FA()">启用双因素认证</button>
      `;
    }
    // 显示登录信息
    const sessionEl = $('#sessionInfo');
    sessionEl.innerHTML = `
      <table style="width:100%;max-width:500px">
        <tr><td><strong>Session 超时</strong></td><td>30 分钟无操作自动登出</td></tr>
        <tr><td><strong>登录限流</strong></td><td>5次失败锁定5分钟</td></tr>
        <tr><td><strong>密码存储</strong></td><td>PBKDF2-SHA256 (100,000 迭代)</td></tr>
      </table>
    `;
  }catch(e){
    showMsg(e.message,'bad');
  }
}

async function changePassword(e){
  e.preventDefault();
  const current = $('#currentPassword').value;
  const newPass = $('#newPassword').value;
  const confirmPass = $('#confirmPassword').value;

  if(newPass !== confirmPass){
    showMsg('两次输入的新密码不一致','bad');
    return;
  }

  if(current === newPass){
    showMsg('新密码与当前密码相同，无需修改','bad');
    return;
  }

  const {checks,score} = checkPasswordStrength(newPass);
  if(score < 7){
    const missing = [];
    if(!checks.length) missing.push('至少12位');
    if(!checks.upper) missing.push('大写字母');
    if(!checks.lower) missing.push('小写字母');
    if(!checks.number) missing.push('数字');
    if(!checks.special) missing.push('特殊字符');
    if(!checks.noSequentialNum) missing.push('不能有连续数字');
    if(!checks.noSequentialLetter) missing.push('不能有连续字母');
    showMsg(`密码不符合要求，缺少：${missing.join('、')}`, 'bad');
    return;
  }

  if(!window.confirm('确认修改密码？\n\n修改后需要重新登录')) return;

  const btn = e.target.querySelector('button[type="submit"]');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '修改中...';

  try{
    await api('/api/account/change-password', {method:'POST', body:JSON.stringify({currentPassword:current, newPassword:newPass})});
    showMsg('✓ 密码修改成功！正在退出登录...', 'ok');
    await api('/api/logout', {method:'POST', body:'{}'});
    setTimeout(() => { window.location.href = '/login'; }, 1000);
  }catch(e){
    showMsg('✗ 密码修改失败：' + (e.message || '未知错误'), 'bad');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function setup2FA(){
  try{
    const data = await api('/api/account/2fa/setup', {method:'POST', body:'{}'});
    const modal = document.createElement('div');
    modal.className = 'modal-overlay active';
    modal.innerHTML = `
      <div class="modal" style="width:600px;max-width:90vw">
        <div style="padding:24px">
          <h2 style="margin:0 0 15px">设置双因素认证</h2>
          <div style="text-align:center;margin:20px 0">
            <img src="${data.qrCode}" alt="QR Code" style="max-width:250px;border:1px solid var(--line);border-radius:8px">
            <p style="margin:15px 0;color:var(--muted)">使用验证器应用扫描此二维码</p>
            <div style="background:var(--bg);padding:10px;border-radius:6px;font-family:monospace;font-size:14px">${data.secret}</div>
            <p style="margin:10px 0;font-size:13px;color:var(--muted)">如无法扫描，请手动输入上述密钥</p>
          </div>
          <div class="notice" style="margin:15px 0">
            <strong>支持的验证器：</strong>
            <ul style="margin:5px 0 0 20px">
              <li>Google Authenticator</li>
              <li>Microsoft Authenticator</li>
              <li>Authy</li>
              <li>1Password</li>
            </ul>
          </div>
          <label>输入验证器生成的6位数字
            <input id="totpCode" type="text" pattern="[0-9]{6}" maxlength="6" placeholder="000000" required autocomplete="off">
          </label>
          <div class="row" style="margin-top:15px;gap:10px">
            <button class="btn primary" onclick="confirm2FA('${data.secret}')">确认并启用</button>
            <button class="btn" onclick="this.closest('.modal-overlay').remove()">取消</button>
          </div>
        </div>
      </div>
    `;
    modal.onclick = (e) => { if(e.target === modal) modal.remove(); };
    document.body.appendChild(modal);
  }catch(e){
    showMsg(e.message,'bad');
  }
}

async function confirm2FA(secret){
  const code = $('#totpCode').value.trim();
  if(!code || code.length !== 6){
    showMsg('请输入6位验证码','bad');
    return;
  }
  const btn = document.querySelector('.modal-overlay .btn.primary');
  if(!btn) return;
  const modal = btn.closest('.modal-overlay');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '验证中...';
  try{
    const result = await api('/api/account/2fa/confirm', {method:'POST', body:JSON.stringify({secret, code})});
    showMsg(result.message || '双因素认证已启用','ok');
    setTimeout(() => {
      modal?.remove();
      loadAccountInfo();
    }, 500);
  }catch(e){
    showMsg(e.message || '验证失败','bad');
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function disable2FA(){
  if(!confirm('确认禁用双因素认证？\n\n这会降低账户安全性')) return;
  try{
    await api('/api/account/2fa/disable', {method:'POST', body:'{}'});
    showMsg('双因素认证已禁用','ok');
    loadAccountInfo();
  }catch(e){
    showMsg(e.message,'bad');
  }
}

function switchView(view){
  document.querySelectorAll('.view').forEach(x => x.classList.toggle('active', x.id === view));
  document.querySelectorAll('.nav button').forEach(x => x.classList.toggle('active', x.dataset.view === view));
  $('#title').textContent = VIEW_TITLES[view] || VIEW_TITLES.dashboard;
  $('#message').innerHTML = ''; // 切换页面时清空通知
  if(view === 'account') loadAccountInfo();
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
  $('#siteCount').textContent = state.sites.length;
  const badCount = state.sites.filter(s => s.backend_status === 'bad').length;
  $('#backendBadCount').textContent = badCount;
  const warnCount = state.sites.filter(s => CERT_WARN_STATES.has(s.cert_status)).length;
  $('#certWarnCount').textContent = warnCount;

  // 计算健康度
  const totalSites = state.sites.length;
  const healthySites = totalSites - badCount - warnCount;
  const healthPercent = totalSites > 0 ? Math.round((healthySites / totalSites) * 100) : 100;
  $('#healthPercent').innerHTML = `<span class="tag ${healthPercent >= 80 ? 'ok' : healthPercent >= 50 ? 'warn' : 'bad'}">${healthPercent}%</span>`;

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

  // 排序
  const sortBy = $('#siteSort')?.value || 'domain';
  filteredSites.sort((a, b) => {
    if (sortBy === 'domain') {
      return (a.domain || '').localeCompare(b.domain || '');
    } else if (sortBy === 'domain_desc') {
      return (b.domain || '').localeCompare(a.domain || '');
    } else if (sortBy === 'status') {
      // 异常优先
      const aScore = (a.backend_status === 'bad' ? 2 : 0) + (CERT_WARN_STATES.has(a.cert_status) ? 1 : 0);
      const bScore = (b.backend_status === 'bad' ? 2 : 0) + (CERT_WARN_STATES.has(b.cert_status) ? 1 : 0);
      return bScore - aScore;
    } else if (sortBy === 'https_first') {
      return (b.https ? 1 : 0) - (a.https ? 1 : 0);
    }
    return 0;
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
    const checkbox = s.managed ? `<input type="checkbox" class="site-checkbox" data-domain="${actionDomain}" onchange="updateBatchSelection()">` : '';
    if (s.managed) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><button class="btn small" onclick="renameSite('${actionDomain}')">重命名</button><button class="btn small danger" onclick="removeSite('${actionDomain}')">删除</button>`;
    } else if (s.can_manage) {
      actions = `<button class="btn small primary" onclick="takeOverSite('${actionDomain}', '${actionSource}')">纳入管理</button>`;
    } else if (s.readonly_reason) {
      actions = `<span class="muted">${escapeHtml(s.readonly_reason)}</span>`;
    }

    return `<tr>
      <td>${checkbox}</td>
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
  modal.className = 'modal-overlay active';
  modal.innerHTML = `
    <div class="modal" style="width:800px;max-width:90vw">
      <div style="padding:24px">
        <h2 style="margin:0 0 10px">恢复配置备份</h2>
        <p style="color:var(--amber);margin-bottom:15px">⚠️ 恢复备份将覆盖当前配置，操作前会自动备份当前配置</p>
        <div style="max-height:400px;overflow-y:auto;border:1px solid var(--line);border-radius:8px">
          <table style="width:100%">
            <thead>
              <tr>
                <th>文件名</th>
                <th>大小</th>
                <th>创建时间</th>
                <th style="text-align:center">操作</th>
              </tr>
            </thead>
            <tbody>
              ${backups.map(b => `
                <tr>
                  <td style="font-family:monospace;font-size:12px">${escapeHtml(b.file)}</td>
                  <td>${escapeHtml(b.size)}</td>
                  <td>${escapeHtml(b.time)}</td>
                  <td style="text-align:center">
                    <button class="btn small primary" onclick="restoreBackup('${b.file.replace(/'/g, "\\'")}'); this.closest('.modal-overlay').remove()">恢复</button>
                    <button class="btn small danger" onclick="deleteBackup('${b.file.replace(/'/g, "\\'")}'); this.closest('.modal-overlay').remove(); showBackupListModal()">删除</button>
                  </td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
        <div class="row" style="margin-top:15px;justify-content:flex-end">
          <button class="btn" onclick="this.closest('.modal-overlay').remove()">关闭</button>
        </div>
      </div>
    </div>
  `;
  modal.onclick = (e) => { if(e.target === modal) modal.remove(); };
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

async function deleteBackup(filename){
  if(!confirm(`⚠️ 确认删除备份？\n\n文件：${filename}\n\n此操作不可撤销！`)) return;
  const backupPath = '/etc/nginx/vps-proxy-manager/backups/' + filename;
  await action('/api/backup/delete', {backup_file: backupPath});
}

async function runHealthCheck(){
  if(!confirm('确认运行健康检查？\n\n将检查所有站点的：\n• 后端连接\n• DNS 解析\n• 证书有效期\n• Nginx 配置')) return;
  await action('/api/health/check', {domain: ''});
}

let autoCheckInterval = null;
function toggleAutoCheck(){
  if(autoCheckInterval){
    clearInterval(autoCheckInterval);
    autoCheckInterval = null;
    $('#autoCheckStatus').textContent = '开启自动检查';
    $('#autoCheckInfo').textContent = '定时检查：未启用';
    localStorage.removeItem('autoCheck');
  } else {
    autoCheckInterval = setInterval(async () => {
      try {
        const res = await fetch('/api/health/check', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({domain:''})});
        const data = await res.json();
        if(data.code !== 0){
          // 检测到问题，显示通知
          const issues = (data.output || '').match(/✗/g);
          if(issues && issues.length > 0){
            showMsg(`检测到 ${issues.length} 个问题`, 'bad');
          }
        }
      } catch(e){}
    }, 300000); // 5分钟检查一次
    $('#autoCheckStatus').textContent = '关闭自动检查';
    $('#autoCheckInfo').textContent = '定时检查：每5分钟';
    localStorage.setItem('autoCheck', 'enabled');
  }
}

// 初始化自动检查
if(localStorage.getItem('autoCheck') === 'enabled'){
  toggleAutoCheck();
}

// 全局搜索
function showGlobalSearch(){
  $('#globalSearch').style.display = 'flex';
  $('#globalSearchInput').focus();
  $('#globalSearchInput').value = '';
  performGlobalSearch('');
}

function hideGlobalSearch(){
  $('#globalSearch').style.display = 'none';
}

$('#globalSearch').onclick = (e) => {
  if(e.target.id === 'globalSearch') hideGlobalSearch();
};

$('#globalSearchInput').oninput = (e) => {
  performGlobalSearch(e.target.value);
};

function performGlobalSearch(query){
  if(!query){
    $('#globalSearchResults').innerHTML = `
      <div style="padding:20px;text-align:center;color:var(--muted)">
        <div style="font-size:24px;margin-bottom:10px">🔍</div>
        <div>输入关键词搜索站点、证书或操作</div>
      </div>
    `;
    return;
  }

  const q = query.toLowerCase();
  const results = [];

  // 搜索站点
  state.sites.forEach(s => {
    if((s.domain || '').toLowerCase().includes(q) ||
       (s.upstream || '').toLowerCase().includes(q)){
      results.push({
        type: 'site',
        title: s.domain,
        subtitle: s.upstream || s.root,
        action: () => { switchView('sites'); hideGlobalSearch(); }
      });
    }
  });

  // 搜索证书
  state.sites.forEach(s => {
    if(s.https && (s.domain || '').toLowerCase().includes(q)){
      results.push({
        type: 'cert',
        title: s.domain + ' 证书',
        subtitle: `剩余 ${s.cert_days} 天`,
        action: () => { switchView('certs'); hideGlobalSearch(); }
      });
    }
  });

  // 搜索操作
  const actions = [
    {title: '新增反向代理', view: 'create'},
    {title: '查看所有站点', view: 'sites'},
    {title: '证书管理', view: 'certs'},
    {title: '健康检查', view: 'tools', fn: runHealthCheck},
    {title: '配置备份', view: 'tools'},
    {title: '问题诊断', view: 'issues'}
  ];
  actions.forEach(a => {
    if(a.title.toLowerCase().includes(q)){
      results.push({
        type: 'action',
        title: a.title,
        subtitle: '快速操作',
        action: () => {
          if(a.fn){ a.fn(); }
          switchView(a.view);
          hideGlobalSearch();
        }
      });
    }
  });

  if(results.length === 0){
    $('#globalSearchResults').innerHTML = `
      <div style="padding:20px;text-align:center;color:var(--muted)">
        <div>未找到匹配的结果</div>
      </div>
    `;
  } else {
    $('#globalSearchResults').innerHTML = results.slice(0, 10).map((r, i) => `
      <div onclick="globalSearchResults[${i}].action()" style="padding:15px 20px;cursor:pointer;border-bottom:1px solid var(--line);transition:background 0.15s"
           onmouseover="this.style.background='var(--bg)'"
           onmouseout="this.style.background='transparent'">
        <div style="font-weight:500">${escapeHtml(r.title)}</div>
        <div style="font-size:12px;color:var(--muted);margin-top:4px">${escapeHtml(r.subtitle)}</div>
      </div>
    `).join('');
    window.globalSearchResults = results;
  }
}

// 快捷键
document.addEventListener('keydown', (e) => {
  if((e.ctrlKey || e.metaKey) && e.key === 'k'){
    e.preventDefault();
    showGlobalSearch();
  }
  if(e.key === 'Escape'){
    hideGlobalSearch();
  }
});

// 密码强度实时检测
document.addEventListener('DOMContentLoaded', () => {
  const newPasswordInput = $('#newPassword');
  if(newPasswordInput){
    newPasswordInput.addEventListener('input', (e) => {
      const password = e.target.value;
      const strengthEl = $('#passwordStrength');
      if(!password){
        strengthEl.innerHTML = '';
        return;
      }
      const {checks, strength, color} = checkPasswordStrength(password);
      strengthEl.innerHTML = `
        <div style="padding:10px;background:var(--bg);border-radius:6px;font-size:13px">
          <div style="margin-bottom:8px"><strong>密码强度：</strong> <span style="color:${color};font-weight:bold">${strength}</span></div>
          <div style="display:grid;gap:4px">
            <div style="color:${checks.length ? 'var(--green)' : 'var(--muted)'}">
              ${checks.length ? '✓' : '○'} 至少8位字符
            </div>
            <div style="color:${checks.upper ? 'var(--green)' : 'var(--muted)'}">
              ${checks.upper ? '✓' : '○'} 包含大写字母
            </div>
            <div style="color:${checks.lower ? 'var(--green)' : 'var(--muted)'}">
              ${checks.lower ? '✓' : '○'} 包含小写字母
            </div>
            <div style="color:${checks.number ? 'var(--green)' : 'var(--muted)'}">
              ${checks.number ? '✓' : '○'} 包含数字
            </div>
            <div style="color:${checks.special ? 'var(--green)' : 'var(--muted)'}">
              ${checks.special ? '✓' : '○'} 包含特殊字符
            </div>
          </div>
        </div>
      `;
    });
  }

  const confirmPasswordInput = $('#confirmPassword');
  if(confirmPasswordInput){
    confirmPasswordInput.addEventListener('input', () => {
      const newPass = $('#newPassword').value;
      const confirmPass = confirmPasswordInput.value;
      if(!confirmPass) return;
      if(newPass !== confirmPass){
        confirmPasswordInput.style.borderColor = 'var(--red)';
      }else{
        confirmPasswordInput.style.borderColor = 'var(--green)';
      }
    });
  }
});

// 修改密码表单提交
document.addEventListener('DOMContentLoaded', () => {
  const form = $('#changePasswordForm');
  if(form){
    form.addEventListener('submit', changePassword);
  }
});

// 配置模板
const templates = {
  api: {
    name: 'API 服务',
    body: '64m',
    readTimeout: '300s',
    sendTimeout: '300s',
    note: '适合 RESTful API、GraphQL 等后端服务'
  },
  web: {
    name: '静态网站',
    body: '10m',
    readTimeout: '60s',
    sendTimeout: '60s',
    note: '适合 React、Vue、纯HTML等静态资源'
  },
  websocket: {
    name: 'WebSocket 服务',
    body: '64m',
    readTimeout: '3600s',
    sendTimeout: '3600s',
    note: '适合实时通信、聊天室、游戏服务器'
  },
  upload: {
    name: '文件上传服务',
    body: '512m',
    readTimeout: '600s',
    sendTimeout: '600s',
    note: '适合图片、视频上传等大文件场景'
  },
  custom: {
    name: '自定义',
    body: '64m',
    readTimeout: '300s',
    sendTimeout: '300s',
    note: '手动配置所有参数'
  }
};

function applyTemplate(type){
  const t = templates[type];
  if(!t) return;

  $('#createForm [name="body"]').value = t.body;
  $('#createForm [name="readTimeout"]').value = t.readTimeout;
  $('#createForm [name="sendTimeout"]').value = t.sendTimeout;

  showMsg(`已应用 ${t.name} 模板：${t.note}`, 'ok');
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

// 更新主题按钮文本
const theme = localStorage.getItem('theme') || 'light';
if($('#themeBtn')) $('#themeBtn').textContent = theme === 'dark' ? '☀️ 亮色' : '🌙 暗色';

$('#siteSearch').addEventListener('input', e => { siteQuery = e.target.value; render(); });
$('#siteFilter').addEventListener('change', e => { siteFilter = e.target.value; render(); });
$('#siteSort').addEventListener('change', () => { render(); });
$('#siteSearchClear').onclick = () => { siteQuery = ''; siteFilter = 'all'; $('#siteSearch').value = ''; $('#siteFilter').value = 'all'; $('#siteSort').value = 'domain'; render(); };
$('#certSearch').addEventListener('input', e => { certQuery = e.target.value; render(); });
$('#certFilter').addEventListener('change', e => { certFilter = e.target.value; render(); });
$('#certSearchClear').onclick = () => { certQuery = ''; certFilter = 'all'; $('#certSearch').value = ''; $('#certFilter').value = 'all'; render(); };
$('#createForm').addEventListener('submit', async e => { e.preventDefault(); const f = new FormData(e.target); const body = {domain:f.get('domain'), upstream:f.get('upstream'), scheme:f.get('scheme'), email:f.get('email'), ssl:f.has('ssl'), body:f.get('body'), readTimeout:f.get('readTimeout'), sendTimeout:f.get('sendTimeout'), backendInsecure:f.has('backendInsecure')}; try { await action('/api/sites/add', body); e.target.reset(); } catch(err){ showMsg(err.message,'bad'); $('#output').textContent = err.message; } });
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
        clean_expired_sessions()
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(COOKIE_NAME)
        if not morsel:
            return False
        token = morsel.value
        if token not in SESSION_STORE:
            return False
        session = SESSION_STORE[token]
        now = time.time()
        if session["expires"] < now:
            del SESSION_STORE[token]
            return False
        # 更新最后活动时间，延长 session
        session["last_active"] = now
        session["expires"] = now + SESSION_TTL
        return True

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_json({"error": "未登录"}, HTTPStatus.UNAUTHORIZED)
        return False

    def do_GET(self) -> None:
        global TOTP_SECRET
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

        if path == "/api/account/info":
            if not self.require_auth():
                return
            self.send_json({
                "twoFactorEnabled": bool(TOTP_SECRET)
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
        global TOTP_SECRET, PASSWORD_HASH, PASSWORD
        path = urlparse(self.path).path
        try:
            data = self.read_json()
        except Exception:
            self.send_json({"error": "JSON 格式错误"}, 400)
            return

        if path == "/api/login":
            client_ip = self.client_address[0]

            # 检查限流
            if not check_login_attempts(client_ip):
                self.send_json({"error": "登录失败次数过多，请5分钟后再试"}, 429)
                return

            # 验证密码
            password_input = str(data.get("password", ""))
            totp_code = str(data.get("totpCode", ""))
            valid = False

            if PASSWORD_HASH:
                # 优先使用 hash 验证
                valid = verify_password(password_input, PASSWORD_HASH)
            elif PASSWORD:
                # 兼容明文密码（向后兼容）
                valid = hmac.compare_digest(password_input, PASSWORD)
            else:
                self.send_json({"error": "服务未设置密码 (HNG_WEB_PASSWORD_HASH 或 HNG_WEB_PASSWORD)"}, 500)
                return

            if not valid:
                record_failed_login(client_ip)
                self.send_json({"error": "密码错误"}, 403)
                return

            # 验证 2FA（如果已启用）
            if TOTP_SECRET:
                if not totp_code:
                    self.send_json({"error": "需要双因素认证码", "require2FA": True}, 403)
                    return
                if not verify_totp_code(TOTP_SECRET, totp_code):
                    record_failed_login(client_ip)
                    self.send_json({"error": "双因素认证码错误"}, 403)
                    return

            # 登录成功，创建 session
            token = secrets.token_urlsafe(32)
            now = time.time()
            SESSION_STORE[token] = {
                "expires": now + SESSION_TTL,
                "last_active": now,
                "ip": client_ip
            }

            # 清除登录失败记录
            if client_ip in LOGIN_ATTEMPTS:
                del LOGIN_ATTEMPTS[client_ip]

            cookie = f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"
            self.send_json({"ok": True}, headers={"Set-Cookie": cookie})
            return

        if path == "/api/logout":
            # 清除 session
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            morsel = cookie.get(COOKIE_NAME)
            if morsel and morsel.value in SESSION_STORE:
                del SESSION_STORE[morsel.value]
            self.send_json({"ok": True}, headers={"Set-Cookie": f"{COOKIE_NAME}=; Max-Age=0; Path=/"})
            return

        if not self.require_auth():
            return

        # 账户管理 API
        if path == "/api/account/change-password":
            current = str(data.get("currentPassword", ""))
            new_password = str(data.get("newPassword", ""))

            # 验证当前密码
            valid = False
            if PASSWORD_HASH:
                valid = verify_password(current, PASSWORD_HASH)
            elif PASSWORD:
                valid = hmac.compare_digest(current, PASSWORD)

            if not valid:
                self.send_json({"error": "当前密码错误"}, 403)
                return

            # 检查新密码是否与当前密码相同
            if current == new_password:
                self.send_json({"error": "新密码与当前密码相同"}, 400)
                return

            # 验证新密码强度
            valid_strength, error_msg = validate_password_strength(new_password)
            if not valid_strength:
                self.send_json({"error": error_msg}, 400)
                return

            # 生成新密码 hash
            new_hash = hash_password(new_password)

            # 更新配置文件
            env_file = pathlib.Path("/etc/host-nginx-manager/web.env")
            if env_file.exists():
                lines = env_file.read_text().splitlines()
                new_lines = []
                updated = False
                for line in lines:
                    if line.startswith("HNG_WEB_PASSWORD_HASH="):
                        new_lines.append(f"HNG_WEB_PASSWORD_HASH={new_hash}")
                        updated = True
                    elif line.startswith("HNG_WEB_PASSWORD="):
                        continue  # 删除明文密码
                    else:
                        new_lines.append(line)
                if not updated:
                    new_lines.append(f"HNG_WEB_PASSWORD_HASH={new_hash}")
                env_file.write_text("\n".join(new_lines) + "\n")

                # 立即更新内存中的密码变量
                PASSWORD_HASH = new_hash
                PASSWORD = ""

                self.send_json({"message": "密码修改成功"})
            else:
                self.send_json({"error": "配置文件不存在"}, 500)
            return

        if path == "/api/account/2fa/setup":
            secret = generate_totp_secret()
            qr_url = generate_totp_qr(secret)
            self.send_json({"secret": secret, "qrCode": qr_url})
            return

        if path == "/api/account/2fa/confirm":
            secret = str(data.get("secret", ""))
            code = str(data.get("code", ""))

            if not verify_totp_code(secret, code):
                self.send_json({"error": "验证码错误，请检查时间同步"}, 403)
                return

            # 保存到配置文件
            env_file = pathlib.Path("/etc/host-nginx-manager/web.env")
            try:
                if env_file.exists():
                    lines = env_file.read_text().splitlines()
                    new_lines = []
                    updated = False
                    for line in lines:
                        if line.startswith("HNG_WEB_TOTP_SECRET="):
                            new_lines.append(f"HNG_WEB_TOTP_SECRET={secret}")
                            updated = True
                        else:
                            new_lines.append(line)
                    if not updated:
                        new_lines.append(f"HNG_WEB_TOTP_SECRET={secret}")
                    env_file.write_text("\n".join(new_lines) + "\n")

                    # 运行时更新全局变量
                    TOTP_SECRET = secret

                    self.send_json({"message": "双因素认证已启用"})
                else:
                    # 无配置文件时，仅内存存储（重启后失效）
                    TOTP_SECRET = secret
                    self.send_json({"message": "双因素认证已启用（仅本次会话有效，重启后失效）"})
            except Exception as e:
                # 权限错误时降级到内存存储
                TOTP_SECRET = secret
                self.send_json({"message": f"双因素认证已启用（仅本次会话有效）\n提示: {str(e)}"})
            return

        if path == "/api/account/2fa/disable":
            # 删除 2FA 配置
            env_file = pathlib.Path("/etc/host-nginx-manager/web.env")
            try:
                if env_file.exists():
                    lines = env_file.read_text().splitlines()
                    new_lines = [line for line in lines if not line.startswith("HNG_WEB_TOTP_SECRET=")]
                    env_file.write_text("\n".join(new_lines) + "\n")

                # 运行时清除
                TOTP_SECRET = ""

                self.send_json({"message": "双因素认证已禁用"})
            except Exception as e:
                # 权限错误时仅清除内存
                TOTP_SECRET = ""
                self.send_json({"message": f"双因素认证已禁用（仅本次会话）\n提示: {str(e)}"})
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

        if path == "/api/backup/delete":
            backup_file = str(data.get("backup_file", "")).strip()
            if not backup_file or not backup_file.startswith("/etc/nginx/vps-proxy-manager/backups/"):
                self.send_json({"error": "无效的备份文件路径"}, 400)
                return
            result = run_cmd(["rm", "-f", backup_file], timeout=10)
            self.send_json({"message": "备份已删除", **result}, 200 if result["code"] == 0 else 500)
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
    import sys

    # 支持生成密码 hash
    if len(sys.argv) > 1 and sys.argv[1] == "hash-password":
        password = input("输入密码: ").strip()
        if not password:
            print("密码不能为空")
            sys.exit(1)
        hash_str = hash_password(password)
        print(f"\n密码 hash 已生成，设置环境变量：")
        print(f'export HNG_WEB_PASSWORD_HASH="{hash_str}"')
        sys.exit(0)

    # 支持重置密码
    if len(sys.argv) > 1 and sys.argv[1] == "reset-password":
        print("=== 重置 Web 管理密码 ===\n")

        # 读取新密码
        import getpass
        while True:
            new_password = getpass.getpass("输入新密码: ").strip()
            if not new_password:
                print("❌ 密码不能为空")
                continue

            # 验证密码强度
            valid, error_msg = validate_password_strength(new_password)
            if not valid:
                print(f"❌ {error_msg}")
                print("\n密码要求：")
                print("  • 最少12位")
                print("  • 包含大写字母、小写字母、数字、特殊字符")
                print("  • 不能有连续数字（如123）或连续字母（如abc）\n")
                continue

            confirm = getpass.getpass("确认新密码: ").strip()
            if new_password != confirm:
                print("❌ 两次输入的密码不一致\n")
                continue
            break

        # 生成 hash
        new_hash = hash_password(new_password)

        # 更新配置文件
        env_file = pathlib.Path("/etc/host-nginx-manager/web.env")
        if not env_file.exists():
            print(f"❌ 配置文件不存在: {env_file}")
            sys.exit(1)

        try:
            lines = env_file.read_text().splitlines()
            new_lines = []
            updated = False
            for line in lines:
                if line.startswith("HNG_WEB_PASSWORD_HASH="):
                    new_lines.append(f"HNG_WEB_PASSWORD_HASH={new_hash}")
                    updated = True
                elif line.startswith("HNG_WEB_PASSWORD="):
                    continue  # 删除明文密码
                else:
                    new_lines.append(line)

            if not updated:
                new_lines.append(f"HNG_WEB_PASSWORD_HASH={new_hash}")

            # 备份
            import shutil
            import datetime
            backup_file = env_file.parent / f"web.env.bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(env_file, backup_file)

            # 写入
            env_file.write_text("\n".join(new_lines) + "\n")

            print("\n✅ 密码重置成功！")
            print(f"   配置文件: {env_file}")
            print(f"   备份文件: {backup_file}")
            print("\n重启服务生效：")
            print("   systemctl restart host-nginx-manager-web")

        except Exception as e:
            print(f"❌ 重置失败: {e}")
            sys.exit(1)

        sys.exit(0)

    # 检查密码配置
    if not PASSWORD_HASH and not PASSWORD:
        print("错误：未设置密码！")
        print("方式1（推荐）：生成 hash 密码")
        print("  python3 web/host_nginx_web.py hash-password")
        print("方式2：使用明文密码（不推荐）")
        print("  export HNG_WEB_PASSWORD='your_password'")
        print("\n忘记密码？使用重置命令：")
        print("  python3 /opt/host-nginx-manager/web/host_nginx_web.py reset-password")
        sys.exit(1)

    if PASSWORD and not PASSWORD_HASH:
        print("⚠️  警告：使用明文密码，建议改用 hash 密码")
        print("   运行: python3 web/host_nginx_web.py hash-password")

    print(f"{APP_TITLE} 启动")
    print(f"  监听: http://{BIND}:{PORT}")
    print(f"  Session 超时: {SESSION_TTL // 60} 分钟")
    print(f"  登录限流: 5次失败锁定5分钟")

    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
