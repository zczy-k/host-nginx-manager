#!/usr/bin/env python3
"""简单测试脚本，验证模块化改造是否正常工作."""
import os
import sys
import tempfile
import pathlib

# 添加父目录到路径
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# 设置测试数据库
test_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
os.environ['HNG_DB_PATH'] = test_db.name

def test_imports():
    """测试所有模块是否能正常导入."""
    print("测试模块导入...")
    try:
        from core.database import init_database, get_db, clean_expired_data
        from core.audit import log_action, get_audit_logs
        from auth.session import create_session, verify_session, delete_session
        from auth.password import hash_password, verify_password, validate_password_strength
        from auth.totp import generate_totp_secret, generate_totp_code, verify_totp_code
        from auth.ratelimit import check_login_attempts, record_failed_login
        from utils.validators import validate_domain, validate_email, validate_upstream
        from utils.qrcode import generate_totp_qr_svg
        print("✅ 所有模块导入成功")
        return True
    except Exception as e:
        print(f"❌ 模块导入失败: {e}")
        return False

def test_database():
    """测试数据库初始化."""
    print("\n测试数据库...")
    try:
        from core.database import init_database, get_db
        init_database()

        with get_db() as db:
            cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

        expected_tables = ['sessions', 'login_attempts', 'api_rate_limits', 'audit_logs', 'config']
        missing = set(expected_tables) - set(tables)

        if missing:
            print(f"❌ 缺少表: {missing}")
            return False

        print(f"✅ 数据库初始化成功，包含 {len(tables)} 个表")
        return True
    except Exception as e:
        print(f"❌ 数据库测试失败: {e}")
        return False

def test_auth():
    """测试认证功能."""
    print("\n测试认证模块...")
    try:
        from auth.password import hash_password, verify_password, validate_password_strength
        from auth.totp import generate_totp_secret, generate_totp_code, verify_totp_code

        # 测试密码
        pwd = "TestPassword123!"
        pwd_hash = hash_password(pwd)
        assert verify_password(pwd, pwd_hash), "密码验证失败"
        assert not verify_password("wrong", pwd_hash), "错误密码应该失败"

        # 测试密码强度
        valid, msg = validate_password_strength("Weak1!")
        assert not valid, "弱密码应该被拒绝"

        valid, msg = validate_password_strength("StrongP@ssw0rd123")
        assert valid, f"强密码应该通过: {msg}"

        # 测试 TOTP
        secret = generate_totp_secret()
        code = generate_totp_code(secret)
        assert len(code) == 6, "TOTP 码应该是 6 位"
        assert verify_totp_code(secret, code), "TOTP 验证应该成功"
        assert not verify_totp_code(secret, "000000"), "错误的 TOTP 码应该失败"

        print("✅ 认证模块测试通过")
        return True
    except Exception as e:
        print(f"❌ 认证测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_session():
    """测试会话管理."""
    print("\n测试会话模块...")
    try:
        from core.database import init_database
        from auth.session import create_session, verify_session, delete_session

        init_database()

        # 创建会话
        token = create_session("127.0.0.1")
        assert token, "会话令牌应该非空"

        # 验证会话
        session = verify_session(token)
        assert session, "会话应该有效"
        assert session['ip'] == "127.0.0.1", "IP 应该匹配"

        # 删除会话
        delete_session(token, "127.0.0.1")
        session = verify_session(token)
        assert session is None, "删除后会话应该无效"

        print("✅ 会话模块测试通过")
        return True
    except Exception as e:
        print(f"❌ 会话测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_audit():
    """测试审计日志."""
    print("\n测试审计日志...")
    try:
        from core.database import init_database
        from core.audit import log_action, get_audit_logs

        init_database()

        # 记录操作
        log_action("192.168.1.1", "test.action", resource="test.domain",
                  details={"foo": "bar"}, result="success")

        # 查询日志
        logs = get_audit_logs(limit=10)
        assert len(logs) > 0, "应该有审计日志"
        assert logs[0]['action'] == "test.action", "操作类型应该匹配"
        assert logs[0]['ip'] == "192.168.1.1", "IP 应该匹配"

        print("✅ 审计日志测试通过")
        return True
    except Exception as e:
        print(f"❌ 审计日志测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_validators():
    """测试验证器."""
    print("\n测试验证器...")
    try:
        from utils.validators import validate_domain, validate_email, validate_upstream

        # 测试域名验证
        assert validate_domain("example.com"), "合法域名应该通过"
        assert validate_domain("sub.example.com"), "子域名应该通过"
        assert not validate_domain(""), "空域名应该失败"
        assert not validate_domain("../etc/passwd"), "路径遍历应该失败"

        # 测试邮箱验证
        assert validate_email("user@example.com"), "合法邮箱应该通过"
        assert not validate_email("invalid"), "非法邮箱应该失败"

        # 测试上游地址验证
        assert validate_upstream("127.0.0.1:8080"), "合法上游应该通过"
        assert validate_upstream("localhost:3000"), "域名上游应该通过"
        assert not validate_upstream("invalid"), "非法上游应该失败"

        print("✅ 验证器测试通过")
        return True
    except Exception as e:
        print(f"❌ 验证器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def cleanup():
    """清理测试数据."""
    try:
        os.unlink(test_db.name)
    except:
        pass

def main():
    """运行所有测试."""
    print("="*60)
    print("Host Nginx Manager - 模块化测试")
    print("="*60)

    tests = [
        test_imports,
        test_database,
        test_auth,
        test_session,
        test_audit,
        test_validators,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"❌ 测试崩溃: {e}")
            results.append(False)

    cleanup()

    print("\n" + "="*60)
    print(f"测试完成: {sum(results)}/{len(results)} 通过")
    print("="*60)

    return all(results)

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
