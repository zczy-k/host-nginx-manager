#!/usr/bin/env python3
"""Build script: merge modular code into single deployable file."""
import re
import sys
from pathlib import Path
from typing import Set, List


def extract_imports(content: str) -> tuple[Set[str], str]:
    """提取标准库导入和代码主体.

    Args:
        content: Python 文件内容

    Returns:
        (标准库导入集合, 代码主体)
    """
    lines = content.splitlines()
    imports = set()
    code_lines = []
    skip_until_code = True

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 跳过文件开头的 shebang、编码、文档字符串
        if i == 0 and stripped.startswith('#!'):
            continue
        if stripped.startswith('# -*- coding:') or stripped.startswith('# coding:'):
            continue
        if i <= 3 and (stripped.startswith('"""') or stripped.startswith("'''")):
            # 跳过模块文档字符串
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
            continue

        # 提取导入语句（在文件顶部，且是标准库导入）
        if skip_until_code and re.match(r'^(from __future__|import |from [\w.]+\s+import)', stripped):
            # 排除内部模块
            if not any(f' {mod}.' in line or f' {mod} ' in line or line.startswith(f'from {mod}') or line.startswith(f'import {mod}')
                      for mod in ['core', 'auth', 'certs', 'proxy', 'api', 'ui', 'utils']):
                imports.add(stripped)  # 只存储去除空格的导入语句
            continue

        # 遇到非导入代码，开始保留
        if stripped and not stripped.startswith('#'):
            skip_until_code = False

        # 保留所有非导入代码
        if not skip_until_code or stripped.startswith('def ') or stripped.startswith('class '):
            skip_until_code = False
            code_lines.append(line)

    return imports, '\n'.join(code_lines)


def collect_modules(src_dir: Path) -> List[tuple[Path, str]]:
    """收集所有模块文件.

    Args:
        src_dir: 源代码目录

    Returns:
        [(文件路径, 模块内容)] 列表
    """
    modules = []

    # 按依赖顺序排序
    order = [
        'utils/validators.py',
        'utils/qrcode.py',
        'core/database.py',
        'core/audit.py',
        'auth/password.py',
        'auth/totp.py',
        'auth/session.py',
        'auth/ratelimit.py',
    ]

    for rel_path in order:
        py_file = src_dir / rel_path
        if py_file.exists():
            modules.append((py_file, py_file.read_text(encoding='utf-8')))

    return modules


def merge_modules(src_dir: Path, output: Path, main_file: Path) -> None:
    """合并所有模块到单个文件.

    Args:
        src_dir: 源代码目录 (web/)
        output: 输出文件路径
        main_file: 主文件 (原始 host_nginx_web.py)
    """
    all_imports = set()
    code_blocks = []

    # 1. 收集模块
    modules = collect_modules(src_dir)

    print(f"📦 收集到 {len(modules)} 个模块")

    # 2. 提取每个模块的导入和代码
    for py_file, content in modules:
        imports, code = extract_imports(content)
        all_imports.update(imports)

        if code.strip():
            code_blocks.append(f"\n# ===== Module: {py_file.relative_to(src_dir)} =====\n")
            code_blocks.append(code)
            print(f"  ✓ {py_file.relative_to(src_dir)}")

    # 3. 读取主文件，替换导入为模块代码
    main_content = main_file.read_text(encoding='utf-8')
    main_imports, main_code = extract_imports(main_content)
    all_imports.update(main_imports)

    # 4. 移除主文件中对内部模块的导入
    main_code_lines = []
    for line in main_code.splitlines():
        stripped = line.strip()
        # 移除内部模块导入
        if any(stripped.startswith(f'from {mod}') or stripped.startswith(f'import {mod}')
              for mod in ['core', 'auth', 'certs', 'proxy', 'api', 'ui', 'utils']):
            continue
        main_code_lines.append(line)
    main_code = '\n'.join(main_code_lines)

    # 5. 生成单文件
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open('w', encoding='utf-8') as f:
        f.write('#!/usr/bin/env python3\n')
        f.write('"""Lightweight web UI for host-nginx-manager (single-file build)."""\n')
        f.write('from __future__ import annotations\n\n')

        # 写入所有标准库导入（排序去重）
        sorted_imports = sorted(all_imports)
        for imp in sorted_imports:
            if imp and not imp.startswith('from __future__'):
                f.write(imp + '\n')

        f.write('\n' + '='*80 + '\n')
        f.write('# MODULAR CODE (auto-merged by build.py)\n')
        f.write('='*80 + '\n')

        # 写入所有模块代码
        for block in code_blocks:
            f.write(block + '\n')

        f.write('\n' + '='*80 + '\n')
        f.write('# MAIN APPLICATION CODE\n')
        f.write('='*80 + '\n\n')

        # 写入主代码
        f.write(main_code)

    output.chmod(0o755)
    print(f"\n✅ 构建完成: {output}")
    print(f"   总行数: {len(output.read_text().splitlines())}")
    print(f"   文件大小: {output.stat().st_size / 1024:.1f} KB")


def main():
    """主函数."""
    script_dir = Path(__file__).parent
    src_dir = script_dir / 'web'
    output_dir = script_dir / 'dist'
    output_file = output_dir / 'host_nginx_web.py'
    main_file = src_dir / 'host_nginx_web.py'

    if not src_dir.exists():
        print(f"❌ 源目录不存在: {src_dir}")
        sys.exit(1)

    if not main_file.exists():
        print(f"❌ 主文件不存在: {main_file}")
        sys.exit(1)

    print("🔨 开始构建单文件版本...\n")
    merge_modules(src_dir, output_file, main_file)
    print("\n🎉 构建成功！")
    print(f"\n部署命令:")
    print(f"  sudo cp {output_file} /opt/host-nginx-manager/web/host_nginx_web.py")
    print(f"  sudo systemctl restart host-nginx-manager-web")


if __name__ == '__main__':
    main()
