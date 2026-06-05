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

INDEX_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Host Nginx Manager</title>
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
    @media (max-width:860px) { .shell { grid-template-columns:1fr; } aside { position:sticky; top:0; z-index:5; } .nav { display:flex; overflow:auto; } .nav button { white-space:nowrap; } .stats,.form-grid { grid-template-columns:1fr; } main { padding:16px; } }
  </style>
</head>
<body>
<div id="login" class="login" hidden>
  <section class="panel">
    <h1>Host Nginx Manager</h1>
    <p class="muted">输入安装时生成的管理密码。</p>
    <section id="loginMessage"></section>
    <form id="loginForm">
      <label>管理密码<input id="password" type="password" autocomplete="current-password" required></label>
      <button class="btn primary" type="submit">登录</button>
    </form>
  </section>
</div>
<div id="app" class="shell" hidden>
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
        <div class="panel"><div class="stat-label">受管站点</div><div id="siteCount" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">监听地址</div><div id="bindInfo" class="stat-value">-</div></div>
        <div class="panel"><div class="stat-label">管理脚本</div><div id="managerInfo" class="stat-value">-</div></div>
      </div>
      <div class="panel"><h2>当前建议</h2><div class="notice">普通 Web/API 服务可以统一放到不同子域名的 443；Rathole、stream、ssl_preread 仍建议手工维护。</div></div>
    </section>
    <section id="sites" class="view">
      <div class="panel">
        <div class="row"><h2>受管站点</h2><span class="spacer"></span><button class="btn primary" data-jump="create">新增</button></div>
        <div style="overflow:auto"><table><thead><tr><th>域名</th><th>后端</th><th>HTTPS</th><th>操作</th></tr></thead><tbody id="siteRows"></tbody></table></div>
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
  const target = !$('#login').hidden && $('#loginMessage') ? $('#loginMessage') : $('#message');
  target.innerHTML = text ? `<div class="panel"><span class="tag ${type}">${type}</span> ${escapeHtml(text)}</div>` : '';
  const other = target.id === 'loginMessage' ? $('#message') : $('#loginMessage');
  if (other) other.innerHTML = '';
}
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function api(path, opts={}){ const res = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts}); if(res.status===401){ showLogin(); throw new Error('未登录'); } const data = await res.json(); if(!res.ok) throw new Error(data.error || '请求失败'); return data; }
function showLogin(){ $('#login').hidden=false; $('#app').hidden=true; }
function showApp(){ $('#login').hidden=true; $('#app').hidden=false; }
async function load(){ state = await api('/api/status'); showApp(); render(); }
function render(){ $('#nginxStatus').innerHTML = `<span class="tag ${state.nginx_active==='active'?'ok':'bad'}">${escapeHtml(state.nginx_active)}</span>`; $('#siteCount').textContent = state.sites.length; $('#bindInfo').textContent = state.bind + ':' + state.port; $('#managerInfo').textContent = state.manager_exists ? '已安装' : '缺失'; const rows = state.sites.map(s => `<tr><td><strong>${escapeHtml(s.DOMAIN)}</strong></td><td>${escapeHtml(s.UPSTREAM_SCHEME)}://${escapeHtml(s.UPSTREAM)}</td><td><span class="tag ${s.ENABLE_SSL==='1'?'ok':'warn'}">${s.ENABLE_SSL==='1'?'HTTPS':'HTTP'}</span></td><td class="row"><button class="btn small" onclick="enableSsl('${escapeHtml(s.DOMAIN)}')">启用HTTPS</button><button class="btn small" onclick="disableSsl('${escapeHtml(s.DOMAIN)}')">关闭HTTPS</button><button class="btn small danger" onclick="removeSite('${escapeHtml(s.DOMAIN)}')">删除</button></td></tr>`).join(''); $('#siteRows').innerHTML = rows || '<tr><td colspan="4" class="muted">暂无受管站点</td></tr>'; }
async function action(path, body){ $('#output').textContent='执行中...'; const data = await api(path,{method:'POST',body:JSON.stringify(body||{})}); $('#output').textContent = data.output || '完成'; showMsg(data.message || '操作完成','ok'); await load(); }
async function enableSsl(domain){ const email = prompt('证书邮箱，可留空'); await action('/api/sites/enable-ssl',{domain,email:email||''}); }
async function disableSsl(domain){ if(confirm('确认关闭 HTTPS？')) await action('/api/sites/disable-ssl',{domain}); }
async function removeSite(domain){ if(confirm('确认删除站点？')) await action('/api/sites/remove',{domain, delete_cert:false}); }
$('#loginForm').addEventListener('submit', async e => { e.preventDefault(); try { await api('/api/login',{method:'POST',body:JSON.stringify({password:$('#password').value})}); await load(); } catch(err){ showMsg(err.message,'bad'); } });
$('#logoutBtn').onclick = async()=>{ await api('/api/logout',{method:'POST',body:'{}'}); showLogin(); };
$('#refreshBtn').onclick = ()=>load().catch(e=>showMsg(e.message,'bad'));
$('#testBtn').onclick = ()=>action('/api/nginx/test',{}).catch(e=>showMsg(e.message,'bad'));
$('#reloadBtn').onclick = ()=>action('/api/nginx/reload',{}).catch(e=>showMsg(e.message,'bad'));
$('#createForm').addEventListener('submit', async e => { e.preventDefault(); const f = new FormData(e.target); const body = {domain:f.get('domain'), upstream:f.get('upstream'), scheme:f.get('scheme'), email:f.get('email'), ssl:f.has('ssl'), body:f.get('body'), readTimeout:f.get('readTimeout'), backendInsecure:f.has('backendInsecure')}; try { await action('/api/sites/add', body); e.target.reset(); } catch(err){ showMsg(err.message,'bad'); $('#output').textContent = err.message; } });
document.querySelectorAll('.nav button,[data-jump]').forEach(b => b.onclick = () => { const v=b.dataset.view||b.dataset.jump; document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===v)); document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x.dataset.view===v)); $('#title').textContent = ({dashboard:'概览',sites:'站点',create:'新增反代',tools:'维护'})[v] || '概览'; });
load().catch(()=>showLogin());
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


def list_sites() -> list[dict[str, str]]:
    if not STATE_DIR.exists():
        return []
    sites = [parse_state_file(p) for p in sorted(STATE_DIR.glob("*.env"))]
    return [s for s in sites if s.get("DOMAIN")]


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
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

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
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            if not self.require_auth():
                return
            active = run_cmd(["systemctl", "is-active", "nginx"], timeout=10)
            self.send_json({
                "sites": list_sites(),
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
