"""Input validation utilities."""
import re
import ipaddress
from typing import Optional


DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def validate_domain(domain: str) -> bool:
    """验证域名格式.

    Args:
        domain: 域名

    Returns:
        是否有效
    """
    if not domain or len(domain) > 253:
        return False
    return bool(DOMAIN_RE.match(domain))


def validate_email(email: str) -> bool:
    """验证邮箱格式.

    Args:
        email: 邮箱地址

    Returns:
        是否有效
    """
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_RE.match(email))


def validate_ip(ip: str) -> bool:
    """验证 IP 地址（IPv4 或 IPv6）.

    Args:
        ip: IP 地址

    Returns:
        是否有效
    """
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def validate_port(port: str) -> bool:
    """验证端口号.

    Args:
        port: 端口号字符串

    Returns:
        是否有效
    """
    try:
        p = int(port)
        return 1 <= p <= 65535
    except (ValueError, TypeError):
        return False


def validate_upstream(upstream: str) -> bool:
    """验证上游地址格式 (ip:port 或 host:port).

    Args:
        upstream: 上游地址

    Returns:
        是否有效
    """
    if not upstream or ':' not in upstream:
        return False

    parts = upstream.rsplit(':', 1)
    if len(parts) != 2:
        return False

    host, port = parts
    return (validate_ip(host) or validate_domain(host)) and validate_port(port)


def sanitize_path(path: str) -> Optional[str]:
    """清理路径，防止路径遍历攻击.

    Args:
        path: 输入路径

    Returns:
        清理后的路径，如果不安全则返回 None
    """
    if not path:
        return None

    # 禁止路径遍历字符
    dangerous = ['..', '~', '$', '`', ';', '|', '&', '<', '>', '\n', '\r']
    if any(d in path for d in dangerous):
        return None

    return path


def sanitize_command_arg(arg: str) -> Optional[str]:
    """清理命令参数，防止命令注入.

    Args:
        arg: 命令参数

    Returns:
        清理后的参数，如果不安全则返回 None
    """
    if not arg:
        return None

    # 禁止 shell 特殊字符
    dangerous = ['$', '`', ';', '|', '&', '<', '>', '\n', '\r', '\\', '"', "'"]
    if any(d in arg for d in dangerous):
        return None

    return arg
