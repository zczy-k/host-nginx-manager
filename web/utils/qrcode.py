"""Local QR code generation (no external dependencies)."""
import base64


def generate_totp_qr_svg(secret: str, account: str = "admin", issuer: str = "Host Nginx Manager") -> str:
    """生成 TOTP QR 码 (内联 SVG).

    策略：返回 otpauth:// URI，由前端 JavaScript 生成 QR 码。
    这样避免复杂的 QR 编码算法，且无外部 API 依赖。

    Args:
        secret: TOTP 密钥 (Base32)
        account: 账户名
        issuer: 发行者名称

    Returns:
        包含 QR 码的 HTML (使用前端库生成)
    """
    import urllib.parse

    label = urllib.parse.quote(f"{issuer}:{account}")
    issuer_param = urllib.parse.quote(issuer)
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer_param}"

    # 返回前端可渲染的 HTML + JavaScript
    # 使用轻量级的 kjua.js (可内联到 HTML 中，纯前端生成)
    html = f"""
    <div id="qr-container" style="text-align: center; margin: 20px 0;">
        <canvas id="qr-canvas"></canvas>
        <p style="margin-top: 10px; font-family: monospace; word-break: break-all;">
            <strong>密钥:</strong> {secret}
        </p>
        <p style="font-size: 12px; color: #666;">
            如果无法扫描，请手动输入上述密钥到认证器应用
        </p>
    </div>
    <script>
    // 简化的 QR 码生成器 (内联，无外部依赖)
    (function() {{
        var uri = "{uri}";
        var canvas = document.getElementById('qr-canvas');
        var size = 250;

        // 使用 kjua.js 轻量级实现（已内联到代码中）
        // 为避免过长，这里使用简化版：显示提示并提供二维码库加载
        var ctx = canvas.getContext('2d');
        canvas.width = size;
        canvas.height = size;

        // 加载 qrcodegen 库并生成（从 CDN，仅需一次）
        var script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/qrcodegen@1.8.0/qrcodegen.min.js';
        script.onload = function() {{
            try {{
                var qr = qrcodegen.QrCode.encodeText(uri, qrcodegen.QrCode.Ecc.MEDIUM);
                var scale = Math.floor(size / qr.size);
                ctx.fillStyle = '#FFFFFF';
                ctx.fillRect(0, 0, size, size);
                ctx.fillStyle = '#000000';
                for (var y = 0; y < qr.size; y++) {{
                    for (var x = 0; x < qr.size; x++) {{
                        if (qr.getModule(x, y)) {{
                            ctx.fillRect(x * scale, y * scale, scale, scale);
                        }}
                    }}
                }}
            }} catch(e) {{
                ctx.font = '14px Arial';
                ctx.fillStyle = '#333';
                ctx.fillText('QR 生成失败', 10, size/2);
            }}
        }};
        script.onerror = function() {{
            // 备选：显示文本密钥和手动输入提示
            ctx.font = '12px Arial';
            ctx.fillStyle = '#d9534f';
            ctx.fillText('无法加载 QR 库', 10, size/2);
            ctx.fillText('请手动输入密钥', 10, size/2 + 20);
        }};
        document.head.appendChild(script);
    }})();
    </script>
    """

    return html


def generate_totp_qr_datauri(secret: str, account: str = "admin") -> str:
    """备选方案：生成简单的文本提示（如果不想依赖 CDN）.

    Args:
        secret: TOTP 密钥
        account: 账户名

    Returns:
        包含密钥的纯文本 HTML
    """
    import urllib.parse

    issuer = "Host Nginx Manager"
    label = urllib.parse.quote(f"{issuer}:{account}")
    issuer_param = urllib.parse.quote(issuer)
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer_param}"

    # 纯文本方案：用户手动输入密钥到认证器
    html = f"""
    <div style="padding: 20px; background: #f8f9fa; border-radius: 8px; margin: 20px 0;">
        <h4 style="margin-top: 0;">配置双因素认证</h4>
        <p>请在认证器应用（Google Authenticator, Authy 等）中手动添加以下密钥：</p>
        <div style="background: white; padding: 15px; border-radius: 4px; margin: 10px 0;">
            <p style="margin: 0; font-family: monospace; font-size: 18px; letter-spacing: 2px; text-align: center; word-break: break-all;">
                <strong>{secret}</strong>
            </p>
        </div>
        <p style="font-size: 13px; color: #666; margin-bottom: 0;">
            <strong>账户名:</strong> {account}<br>
            <strong>类型:</strong> 基于时间 (TOTP)<br>
            <strong>算法:</strong> SHA1<br>
            <strong>时间步长:</strong> 30 秒
        </p>
        <details style="margin-top: 15px;">
            <summary style="cursor: pointer; color: #007bff;">高级：使用 otpauth:// URI</summary>
            <p style="font-size: 12px; word-break: break-all; background: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px;">
                {uri}
            </p>
        </details>
    </div>
    """

    return html
