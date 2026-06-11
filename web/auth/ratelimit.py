"""Rate limiting and authentication middleware."""
import time
from typing import Optional

from core.database import get_db
from core.audit import log_action


MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_DURATION = 5 * 60  # 5分钟
API_RATE_LIMIT = 60  # 每分钟60次
API_RATE_WINDOW = 60  # 1分钟窗口


def check_login_attempts(ip: str) -> bool:
    """检查登录尝试是否超限.

    Args:
        ip: 客户端IP

    Returns:
        是否允许登录
    """
    now = int(time.time())

    with get_db() as db:
        row = db.execute(
            "SELECT count, locked_until FROM login_attempts WHERE ip = ?",
            (ip,)
        ).fetchone()

        if not row:
            return True

        count, locked_until = row['count'], row['locked_until']

        # 检查是否在锁定期内
        if locked_until > now:
            return False

        # 锁定期过后，重置计数
        if locked_until > 0 and locked_until <= now:
            db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
            return True

        # 未达到锁定次数
        return count < MAX_LOGIN_ATTEMPTS


def record_failed_login(ip: str) -> None:
    """记录失败的登录尝试.

    Args:
        ip: 客户端IP
    """
    now = int(time.time())

    with get_db() as db:
        row = db.execute(
            "SELECT count FROM login_attempts WHERE ip = ?",
            (ip,)
        ).fetchone()

        if row:
            new_count = row['count'] + 1
            locked_until = now + LOGIN_LOCKOUT_DURATION if new_count >= MAX_LOGIN_ATTEMPTS else 0
            db.execute(
                "UPDATE login_attempts SET count = ?, locked_until = ?, last_attempt = ? WHERE ip = ?",
                (new_count, locked_until, now, ip)
            )
        else:
            db.execute(
                "INSERT INTO login_attempts (ip, count, locked_until, last_attempt) VALUES (?, ?, ?, ?)",
                (ip, 1, 0, now)
            )

    log_action(ip, "auth.login_failed", result="failure")


def reset_login_attempts(ip: str) -> None:
    """重置登录尝试计数（登录成功后）.

    Args:
        ip: 客户端IP
    """
    with get_db() as db:
        db.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))


def check_api_rate_limit(ip: str) -> bool:
    """检查 API 速率限制.

    Args:
        ip: 客户端IP

    Returns:
        是否允许访问
    """
    now = int(time.time())

    with get_db() as db:
        row = db.execute(
            "SELECT count, reset_time FROM api_rate_limits WHERE ip = ?",
            (ip,)
        ).fetchone()

        if not row:
            # 首次访问，创建记录
            db.execute(
                "INSERT INTO api_rate_limits (ip, count, reset_time) VALUES (?, ?, ?)",
                (ip, 1, now + API_RATE_WINDOW)
            )
            return True

        count, reset_time = row['count'], row['reset_time']

        # 窗口已过期，重置
        if now >= reset_time:
            db.execute(
                "UPDATE api_rate_limits SET count = ?, reset_time = ? WHERE ip = ?",
                (1, now + API_RATE_WINDOW, ip)
            )
            return True

        # 窗口内，检查是否超限
        if count >= API_RATE_LIMIT:
            return False

        # 增加计数
        db.execute(
            "UPDATE api_rate_limits SET count = ? WHERE ip = ?",
            (count + 1, ip)
        )
        return True


def get_remaining_lockout_time(ip: str) -> int:
    """获取剩余锁定时间（秒）.

    Args:
        ip: 客户端IP

    Returns:
        剩余锁定秒数，0表示未锁定
    """
    now = int(time.time())

    with get_db() as db:
        row = db.execute(
            "SELECT locked_until FROM login_attempts WHERE ip = ?",
            (ip,)
        ).fetchone()

        if not row:
            return 0

        locked_until = row['locked_until']
        return max(0, locked_until - now)
