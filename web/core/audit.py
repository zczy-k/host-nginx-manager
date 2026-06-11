"""Audit logging system."""
import json
import time
from typing import Optional, Any

from .database import get_db


def log_action(
    ip: str,
    action: str,
    session_token: str = "",
    resource: str = "",
    details: Optional[dict[str, Any]] = None,
    result: str = "success",
    error: str = ""
) -> None:
    """记录操作审计日志.

    Args:
        ip: 客户端IP
        action: 操作类型 (如 'auth.login', 'site.create')
        session_token: 会话令牌
        resource: 操作资源 (如域名)
        details: 详细信息 (dict)
        result: 结果 ('success' 或 'failure')
        error: 错误信息
    """
    with get_db() as db:
        db.execute(
            """INSERT INTO audit_logs
               (timestamp, ip, session_token, action, resource, details, result, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(time.time()),
                ip,
                session_token or "",
                action,
                resource or "",
                json.dumps(details or {}, ensure_ascii=False),
                result,
                error or ""
            )
        )


def get_audit_logs(
    limit: int = 100,
    offset: int = 0,
    action_filter: str = "",
    result_filter: str = "",
    ip_filter: str = ""
) -> list[dict[str, Any]]:
    """查询审计日志.

    Args:
        limit: 返回记录数
        offset: 偏移量
        action_filter: 操作类型过滤
        result_filter: 结果过滤
        ip_filter: IP过滤

    Returns:
        日志记录列表
    """
    query = "SELECT * FROM audit_logs WHERE 1=1"
    params = []

    if action_filter:
        query += " AND action LIKE ?"
        params.append(f"%{action_filter}%")

    if result_filter:
        query += " AND result = ?"
        params.append(result_filter)

    if ip_filter:
        query += " AND ip = ?"
        params.append(ip_filter)

    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_db() as db:
        rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def cleanup_old_logs(days: int = 90) -> int:
    """清理旧日志.

    Args:
        days: 保留最近N天的日志

    Returns:
        删除的记录数
    """
    cutoff = int(time.time()) - (days * 86400)
    with get_db() as db:
        cursor = db.execute("DELETE FROM audit_logs WHERE timestamp < ?", (cutoff,))
        return cursor.rowcount
