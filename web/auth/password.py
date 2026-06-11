"""Password hashing and validation."""
import base64
import hashlib
import hmac
import re
import secrets


def hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 哈希密码.

    Args:
        password: 明文密码

    Returns:
        Base64编码的 salt + hash
    """
    salt = secrets.token_bytes(32)
    pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return base64.b64encode(salt + pwdhash).decode('ascii')


def verify_password(password: str, hash_str: str) -> bool:
    """验证密码.

    Args:
        password: 明文密码
        hash_str: 存储的哈希值

    Returns:
        是否匹配
    """
    try:
        decoded = base64.b64decode(hash_str)
        salt = decoded[:32]
        stored_hash = decoded[32:]
        pwdhash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return hmac.compare_digest(pwdhash, stored_hash)
    except Exception:
        return False


def validate_password_strength(password: str) -> tuple[bool, str]:
    """验证密码强度.

    Args:
        password: 待验证密码

    Returns:
        (是否通过, 错误信息)
    """
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

    sequential_nums = [
        '0123', '1234', '2345', '3456', '4567', '5678', '6789',
        '9876', '8765', '7654', '6543', '5432', '4321', '3210'
    ]
    if any(seq in password for seq in sequential_nums):
        return False, "密码不能包含4位或更多连续数字"

    sequential_letters = [
        'abcd', 'bcde', 'cdef', 'defg', 'efgh', 'fghi', 'ghij', 'hijk',
        'dcba', 'edcb', 'fedc', 'gfed', 'hgfe', 'ihgf', 'jihg', 'kjih'
    ]
    if any(seq in password.lower() for seq in sequential_letters):
        return False, "密码不能包含4位或更多连续字母"

    return True, ""
