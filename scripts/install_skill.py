#!/usr/bin/env python3
"""把当前仓库安装到 Codex 的用户 skill 目录；替换前保留旧版本。"""
import argparse
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def main(argv=None):
    p = argparse.ArgumentParser(description='安装 wechat-oa-publish 到 Codex')
    p.add_argument('--dest', type=Path, help='目标 skills 目录；默认 $CODEX_HOME/skills 或 ~/.codex/skills')
    p.add_argument('--force', action='store_true', help='备份并替换已安装的版本')
    args = p.parse_args(argv)
    source = Path(__file__).resolve().parents[1]
    codex_root = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser()
    skills_root = (args.dest or codex_root / 'skills').expanduser()
    target = skills_root / 'wechat-oa-publish'
    if source == target.resolve():
        print(f'当前文件已位于安装目录：{target}')
        return 0
    if not source.joinpath('SKILL.md').is_file():
        raise RuntimeError('源码目录缺少 SKILL.md。')
    if target.exists() and not args.force:
        raise RuntimeError('该 skill 已安装；更新时使用 --force，旧版本会先备份。')
    skills_root.mkdir(parents=True, exist_ok=True)
    staging_root = skills_root.parent / 'skill-staging'
    staging_root.mkdir(parents=True, exist_ok=True)
    staged = staging_root / ('wechat-oa-publish-' + uuid.uuid4().hex)
    backup = None
    try:
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns(
            '.git', '__pycache__', '*.pyc', '.DS_Store', '.env', '.env.*', 'wechat.env',
            '*-receipt.json', 'article-submitted.html', 'article-saved.html',
            'outputs', 'output', 'work', 'articles', 'covers', 'wechat-output',
        ))
        if target.exists():
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup = skills_root.parent / 'skill-backups' / ('wechat-oa-publish-' + timestamp + '-' + uuid.uuid4().hex[:8])
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup and not target.exists():
                backup.rename(target)
            raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    print(f'✅ 安装完成：{target}')
    if backup:
        print(f'旧版本备份：{backup}')
    print('下一条 Codex 消息可使用 $wechat-oa-publish。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print('安装失败：' + str(error), file=sys.stderr)
        sys.exit(1)
