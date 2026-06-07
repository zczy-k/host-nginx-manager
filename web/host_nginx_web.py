#!/usr/bin/env python3
"""Lightweight web UI for host-nginx-manager."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookies
import json
import os
import pathlib
import re
import secrets
import subprocess
import time
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
    .login { min-height:100vh; display:grid; place-items:center; padding:24px; }
    .login .panel { width:min(420px,100%); }
    .login-message { margin:12px 0; }
    @media (max-width:860px) { .shell { grid-template-columns:1fr; } aside { position:sticky; top:0; z-index:5; } .nav { display:flex; overflow:auto; } .nav button { white-space:nowrap; } .stats,.form-grid { grid-template-columns:1fr; } main { padding:16px; } }
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
      <button data-view="sites">站点</button>
      <button data-view="create">新增反代</button>
      <button data-view="tools">维护</button>
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
      </div>
      <div class="panel"><h2>当前建议</h2><div class="notice">普通 Web/API 服务可以统一放到不同子域名的 443；Rathole、stream、ssl_preread 仍建议手工维护。</div></div>
    </section>
    <section id="sites" class="view">
      <div class="panel">
        <div class="row"><h2>Nginx 站点</h2><span class="spacer"></span><button class="btn primary" data-jump="create">新增</button></div>
        <div style="overflow:auto"><table><thead><tr><th>域名</th><th>监听</th><th>类型</th><th>目标/目录</th><th>来源</th><th>操作</th></tr></thead><tbody id="siteRows"></tbody></table></div>
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
  </main>
</div>
<script>
let state = null;
const $ = (s) => document.querySelector(s);
function showMsg(text, type='info'){
  $('#message').innerHTML = text ? `<div class="panel"><span class="tag ${type}">${type}</span> ${escapeHtml(text)}</div>` : '';
}
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function api(path, opts={}){ const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts}); if(res.status===401){ window.location.replace('/login'); throw new Error('未登录'); } const data = await res.json(); if(!res.ok) throw new Error(data.error || data.output || '请求失败'); return data; }
async function load(){ state = await api('/api/status'); render(); }
function render(){
  $('#nginxStatus').innerHTML = `<span class="tag ${state.nginx_active==='active'?'ok':'bad'}">${escapeHtml(state.nginx_active)}</span>`;
  $('#siteCount').textContent = state.sites.length;
  $('#bindInfo').textContent = state.bind + ':' + state.port;
  $('#managerInfo').textContent = state.manager_exists ? '已安装' : '缺失';
  const rows = state.sites.map(s => {
    const domain = s.domain || '(默认站点)';
    const actionDomain = String(s.managed_domain || domain).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionSource = String(s.source || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const actionTarget = String(s.upstream || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const names = Array.isArray(s.names) && s.names.length ? s.names.join(', ') : domain;
    const listen = Array.isArray(s.listen) && s.listen.length ? s.listen.join(', ') : '-';
    const target = s.upstream || s.root || '-';
    const owner = s.managed ? `<span class="tag ok">${s.migrated || !s.imported ? '受管' : '已接管'}</span>` : '<span class="tag">已有</span>';
    const https = s.https ? '<span class="tag ok">HTTPS</span>' : '<span class="tag warn">HTTP</span>';
    let actions = '<span class="muted">只读</span>';
    if (s.migrated) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><span class="muted">原样受管</span>`;
    } else if (s.managed && !s.imported) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button><button class="btn small" onclick="enableSsl('${actionDomain}')">启用HTTPS</button><button class="btn small" onclick="disableSsl('${actionDomain}')">关闭HTTPS</button><button class="btn small danger" onclick="removeSite('${actionDomain}')">删除</button>`;
    } else if (s.imported) {
      actions = `<button class="btn small primary" onclick="editSite('${actionDomain}', '${actionTarget}')">编辑</button>${s.migrated ? '<span class="muted">受管文件</span>' : `<button class="btn small" onclick="migrateSite('${actionDomain}')">迁移为受管</button><span class="muted">原配置</span>`}`;
    } else if (s.importable) {
      actions = `<button class="btn small primary" onclick="importSite('${actionDomain}', '${actionSource}')">导入/接管</button>`;
    } else if (s.readonly_reason) {
      actions = `<span class="muted">${escapeHtml(s.readonly_reason)}</span>`;
    }
    return `<tr><td><strong>${escapeHtml(domain)}</strong><div class="muted">${escapeHtml(names)}</div></td><td>${escapeHtml(listen)}</td><td>${owner} ${https}<div class="muted">${escapeHtml(s.kind || 'Nginx 服务')}</div></td><td>${escapeHtml(target)}</td><td>${escapeHtml(s.source || '-')}</td><td class="row">${actions}</td></tr>`;
  }).join('');
  $('#siteRows').innerHTML = rows || '<tr><td colspan="6" class="muted">当前 nginx 配置里没有发现 server 站点</td></tr>';
}
async function action(path, body){ $('#output').textContent='执行中...'; const data = await api(path,{method:'POST',body:JSON.stringify(body||{})}); $('#output').textContent = data.output || '完成'; showMsg(data.message || '操作完成','ok'); await load(); }
async function enableSsl(domain){ const email = prompt('证书邮箱，可留空'); await action('/api/sites/enable-ssl',{domain,email:email||''}); }
async function editSite(domain, currentTarget){ const target = prompt('新的后端地址，例如 127.0.0.1:3002 或 http://127.0.0.1:3002', currentTarget || ''); if(target) await action('/api/sites/update',{domain,target}); }
async function disableSsl(domain){ if(confirm('确认关闭 HTTPS？')) await action('/api/sites/disable-ssl',{domain}); }
async function removeSite(domain){ if(confirm('确认删除站点？')) await action('/api/sites/remove',{domain, delete_cert:false}); }
async function importSite(domain, source){ if(confirm('确认导入这个已有反向代理站点？导入不会删除原 nginx 配置。')) await action('/api/sites/import',{domain, source}); }
async function migrateSite(domain){ if(confirm('确认将这个已接管站点迁移为工具受管配置？会备份并注释原始配置块。')) await action('/api/sites/migrate',{domain}); }
$('#logoutBtn').onclick = async()=>{ await api('/api/logout',{method:'POST',body:'{}'}); window.location.replace('/login'); };
$('#refreshBtn').onclick = ()=>load().catch(e=>showMsg(e.message,'bad'));
$('#testBtn').onclick = ()=>action('/api/nginx/test',{}).catch(e=>showMsg(e.message,'bad'));
$('#reloadBtn').onclick = ()=>action('/api/nginx/reload',{}).catch(e=>showMsg(e.message,'bad'));
$('#createForm').addEventListener('submit', async e => { e.preventDefault(); const f = new FormData(e.target); const body = {domain:f.get('domain'), upstream:f.get('upstream'), scheme:f.get('scheme'), email:f.get('email'), ssl:f.has('ssl'), body:f.get('body'), readTimeout:f.get('readTimeout'), backendInsecure:f.has('backendInsecure')}; try { await action('/api/sites/add', body); e.target.reset(); } catch(err){ showMsg(err.message,'bad'); $('#output').textContent = err.message; } });
document.querySelectorAll('.nav button,[data-jump]').forEach(b => b.onclick = () => { const v=b.dataset.view||b.dataset.jump; document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===v)); document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.view===v)); $('#title').textContent = ({dashboard:'概览',sites:'站点',create:'新增反代',tools:'维护'})[v] || '概览'; });
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


def parse_server_block(block: list[str], source: str, managed_by_domain: dict[str, dict[str, str]]) -> dict[str, object]:
    names: list[str] = []
    listens: list[str] = []
    proxy_passes: list[str] = []
    roots: list[str] = []
    has_ssl_cert = False

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
        if line.startswith("ssl_certificate "):
            has_ssl_cert = True

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
    }


def list_nginx_servers() -> list[dict[str, object]]:
    managed_sites = list_managed_sites()
    managed_by_domain = {str(site.get("DOMAIN", "")): site for site in managed_sites}
    dump = run_cmd(["nginx", "-T"], timeout=20)
    if dump["code"] != 0:
        return [{
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
        } for site in managed_sites]

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
            servers.append({
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
            })

    return servers


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
                "nginx_active": (active["output"] or "unknown"),
                "manager_exists": pathlib.Path(MANAGER_BIN).exists(),
                "bind": BIND,
                "port": PORT,
            })
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

        self.send_error(404)


def main() -> None:
    if not PASSWORD:
        print("警告：未设置 HNG_WEB_PASSWORD，登录将不可用。")
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"{APP_TITLE} listening on http://{BIND}:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
