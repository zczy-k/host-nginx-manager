"""Session management with SQLite persistence."""
import secrets
import time
from typing import Optional

from core.database import get_db
from core.audit import log_action


SESSION_TTL = 30 * 60  # 30分钟


def create_session(ip: str) -> str:
    """创建新会话.

    Args:
        ip: 客户端IP

    Returns:
        会话令牌
    """
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires = now + SESSION_TTL

    with get_db() as db:
        db.execute(
            "INSERT INTO sessions (token, ip, created_at, expires_at, last_active) VALUES (?, ?, ?, ?, ?)",
            (token, ip, now, expires, now)
        )

    log_action(ip, "auth.session_create", session_token=token)
    return token


def verify_session(token: str, update_activity: bool = True) -> Optional[dict]:
    """验证会话是否有效.

    Args:
        token: 会话令牌
        update_activity: 是否更新最后活动时间

    Returns:
        会话信息 (包含 ip, created_at 等) 或 None
    """
    if not token:
        return None

    now = int(time.time())

    with get_db() as db:
        row = db.execute(
            "SELECT * FROM sessions WHERE token = ? AND expires_at > ?",
            (token, now)
        ).fetchone()

        if not row:
            return None

        session = dict(row)

        if update_activity:
            new_expires = now + SESSION_TTL
            db.execute(
                "UPDATE sessions SET last_active = ?, expires_at = ? WHERE token = ?",
                (now, new_expires, token)
            )
            session['last_active'] = now
            session['expires_at'] = new_expires

        return session


def delete_session(token: str, ip: str = "") -> None:
    """删除会话（登出）.

    Args:
        token: 会话令牌
        ip: 客户端IP (用于审计)
    """
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))

    if ip:
        log_action(ip, "auth.logout", session_token=token)


def delete_all_sessions() -> None:
    """删除所有会话（如密码修改后）."""
    with get_db() as db:
        db.execute("DELETE FROM sessions")
