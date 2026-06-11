"""TOTP (Two-Factor Authentication) implementation."""
import base64
import hashlib
import hmac
import secrets
import struct
import time


def generate_totp_secret() -> str:
    """生成 TOTP 密钥 (Base32).

    Returns:
        Base32 编码的密钥
    """
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii')


def generate_totp_code(secret: str, timestamp: int = None) -> str:
    """生成 TOTP 6位数字码.

    Args:
        secret: Base32 密钥
        timestamp: Unix 时间戳 (默认为当前时间)

    Returns:
        6位数字码
    """
    if timestamp is None:
        timestamp = int(time.time())

    key = base64.b32decode(secret)
    counter = timestamp // 30
    msg = struct.pack('>Q', counter)
    hmac_hash = hmac.new(key, msg, hashlib.sha1).digest()
    offset = hmac_hash[-1] & 0x0F
    code = struct.unpack('>I', hmac_hash[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1000000).zfill(6)


def verify_totp_code(secret: str, code: str, time_window: int = 1) -> bool:
    """验证 TOTP 码.

    Args:
        secret: Base32 密钥
        code: 用户输入的6位数字码
        time_window: 时间窗口 (前后N个30秒窗口)

    Returns:
        是否验证通过
    """
    if not secret or not code or len(code) != 6:
        return False

    try:
        timestamp = int(time.time())
        for offset in range(-time_window, time_window + 1):
            expected = generate_totp_code(secret, timestamp + offset * 30)
            if hmac.compare_digest(code, expected):
                return True
        return False
    except Exception:
        return False
