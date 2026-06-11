#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build script: merge modular code into single deployable file."""
import re
import sys
from pathlib import Path
from typing import Set, List

# Windows 编码修复
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')


def extract_imports(content: str) -> tuple[Set[str], str]:
    """提取标准库导入和代码主体.

    Args:
        content: Python 文件内容

    Returns:
        (标准库导入集合, 代码主体)
    """
    lines = content.splitlines()
    imports = []
    code_lines = []
    in_docstring = False
    docstring_quote = None
    skip_imports = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 跳过 shebang 和编码
        if i == 0 and stripped.startswith('#!'):
            continue
        if stripped.startswith('# -*- coding:') or stripped.startswith('# coding:'):
            continue

        # 处理文档字符串
        if i <= 10 and not skip_imports:
            if not in_docstring:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    docstring_quote = '"""' if stripped.startswith('"""') else "'''"
                    if stripped.count(docstring_quote) >= 2:
                        continue  # 单行文档字符串
                    in_docstring = True
                    continue
            else:
                if docstring_quote in stripped:
                    in_docstring = False
                continue

        # 提取导入（标准库，非内部模块，非相对导入）
        if not skip_imports and re.match(r'^(from __future__|import |from [\w.]+\s+import)', stripped):
            # 排除内部模块和相对导入
            if not any(pattern in stripped for pattern in ['core.', 'auth.', 'utils.', 'certs.', 'proxy.', 'api.', 'ui.', 'from .', 'import .']):
                imports.append(stripped)
            continue

        # 遇到非导入代码，开始保留所有内容
        if stripped and not stripped.startswith('#'):
            skip_imports = True

        if skip_imports:
            code_lines.append(line)

    return set(imports), '\n'.join(code_lines)


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
            code_blocks.append(f"\n# Module: {py_file.relative_to(src_dir)}\n")
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
        f.write('# -*- coding: utf-8 -*-\n')
        f.write('"""Lightweight web UI for host-nginx-manager (single-file build)."""\n')
        f.write('from __future__ import annotations\n\n')

        # 合并重复的 from 导入
        from_imports = {}
        simple_imports = []

        for imp in sorted(all_imports):
            if imp.startswith('from ') and ' import ' in imp:
                parts = imp.split(' import ', 1)
                module = parts[0]
                items = parts[1].strip()
                if module in from_imports:
                    # 合并相同模块的导入
                    existing = from_imports[module].split(', ')
                    new_items = items.split(', ')
                    from_imports[module] = ', '.join(sorted(set(existing + new_items)))
                else:
                    from_imports[module] = items
            elif imp and not imp.startswith('from __future__'):
                simple_imports.append(imp)

        # 写入合并后的导入
        for module in sorted(from_imports.keys()):
            f.write(f"{module} import {from_imports[module]}\n")
        for imp in sorted(simple_imports):
            f.write(imp + '\n')

        f.write('\n')

        # 写入所有模块代码
        for block in code_blocks:
            f.write(block + '\n\n')

        # 写入主代码
        f.write(main_code)

    output.chmod(0o755)
    print(f"\n✅ 构建完成: {output}")
    print(f"   总行数: {len(output.read_text(encoding='utf-8').splitlines())}")
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
