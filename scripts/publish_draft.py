#!/usr/bin/env python3
"""微信公众号图文草稿：本地预览、官方商品卡、封面上传、创建与回读。

仅依赖 Python 3.9+ 标准库；图片生成由 Codex 内置 ImageGen 负责。
"""

import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

API = 'https://api.weixin.qq.com'
SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULTS_FILE = SKILL_DIR / 'config/defaults.json'
PLACEHOLDER = '<!--PRODUCT_CARD-->'
SENSITIVE_VALUES = set()


class PublishError(Exception):
    pass


class WechatAPIError(PublishError):
    pass


def redact(message):
    value = str(message)
    for secret in sorted(SENSITIVE_VALUES, key=len, reverse=True):
        if secret:
            value = value.replace(secret, '[REDACTED]')
    return re.sub(r'(?i)((?:access_token|secret)=)[^&\s]+', r'\1[REDACTED]', value)


def write_json(path, data):
    """原子保存回执，以便创建之后的验证失败仍能重查。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise PublishError('JSON 配置或回执必须是一个对象。')
    return data


def load_account_file(explicit=None):
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise PublishError('指定的账号配置文件不存在。')
    else:
        config_root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        path = config_root / 'wechat-oa-publish/wechat.env'
        if not path.is_file():
            path = Path.home() / '.workbuddy/wechat.env'
        if not path.is_file():
            return {}
    allowed = {'WECHAT_APPID', 'WECHAT_SECRET', 'WECHAT_AUTHOR', 'WECHAT_PRODUCT_ID', 'WECHAT_CARD_TYPE'}
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:]
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if key in allowed:
            values[key] = value.strip().strip('"').strip("'")
    return values


def setting(cli, variable, account, fallback=''):
    if cli is not None:
        return cli
    return os.environ.get(variable, account.get(variable, fallback))


def credentials(args, account):
    appid = setting(args.appid, 'WECHAT_APPID', account)
    secret = setting(args.secret, 'WECHAT_SECRET', account)
    if not appid or not secret:
        raise PublishError('缺少 AppID / AppSecret。请使用环境变量或仓库外账号文件。')
    SENSITIVE_VALUES.add(secret)
    return appid, secret


def api_url(path, token=None, **query):
    if token:
        query['access_token'] = token
    suffix = '?' + urllib.parse.urlencode(query) if query else ''
    return API + path + suffix


def http_json(url, payload=None, *, data=None, headers=None, timeout=60):
    headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        raise PublishError(f'微信请求返回 HTTP {error.code}；未自动重试。') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PublishError('微信网络请求未完成；未自动重试。') from None
    except (ValueError, UnicodeError):
        raise PublishError('微信返回了无法解析的响应；未自动重试。') from None
    if not isinstance(result, dict):
        raise PublishError('微信响应格式不正确；未自动重试。')
    if result.get('errcode', 0) != 0:
        raise WechatAPIError(f"微信错误码 {result['errcode']}：{redact(result.get('errmsg', '请求失败'))}")
    return result


def get_token(appid, secret):
    SENSITIVE_VALUES.add(secret)
    result = http_json(api_url('/cgi-bin/token', grant_type='client_credential', appid=appid, secret=secret))
    token = result.get('access_token')
    if not token:
        raise PublishError('微信未返回 access_token。')
    SENSITIVE_VALUES.add(token)
    return token


def get_product_card(token, product_id, card_type=1):
    result = http_json(api_url('/channels/ec/service/product/getcardinfo', token), {
        'product_id': str(product_id), 'article_type': 'news', 'card_type': card_type,
    })
    dom = result.get('DOM')
    if not isinstance(dom, str) or not card_signatures(dom):
        raise PublishError('微信未返回有效的官方商品卡 DOM。')
    return dom


def upload_image(token, path):
    path = Path(path)
    boundary = '----wechat' + uuid.uuid4().hex
    content_type = mimetypes.guess_type(path.name)[0] or 'image/png'
    prefix = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="media"; filename="cover{path.suffix.lower()}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'
    ).encode('utf-8')
    body = prefix + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode('ascii')
    result = http_json(api_url('/cgi-bin/material/add_material', token, type='image'),
                       data=body, headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, timeout=120)
    if not result.get('media_id'):
        raise PublishError('微信未返回封面素材 media_id。')
    return result['media_id']


def create_draft(token, title, author, digest, content, thumb_media_id):
    return http_json(api_url('/cgi-bin/draft/add', token), {'articles': [{
        'article_type': 'news', 'title': title, 'author': author, 'digest': digest,
        'content': content, 'thumb_media_id': thumb_media_id,
        'need_open_comment': 1, 'only_fans_can_comment': 0,
    }]})


class ProductCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.signatures = []

    def handle_starttag(self, tag, attrs):
        if tag == 'mp-common-product':
            value = dict(attrs).get('data-windowproduct')
            if value:
                self.signatures.append(value)


def card_signatures(content):
    parser = ProductCards()
    parser.feed(content)
    return parser.signatures


def verify_draft(token, receipt, receipt_path):
    result = http_json(api_url('/cgi-bin/draft/get', token), {'media_id': receipt['media_id']})
    articles = result.get('news_item', [])
    article = next((item for item in articles if item.get('title') == receipt['title']), None)
    if not article:
        raise PublishError('回读草稿未找到匹配的文章标题。')
    content = article.get('content', '')
    checks = {
        'title': article.get('title') == receipt['title'],
        'author': article.get('author', '') == receipt['author'],
        'digest': article.get('digest', '') == receipt['digest'],
        'cover': article.get('thumb_media_id') == receipt['thumb_media_id'],
        'product_card': not receipt['product_id'] or receipt['product_card_signature'] in card_signatures(content),
    }
    receipt['verification_checks'] = checks
    receipt['readback_verified'] = all(checks.values())
    receipt['status'] = 'verified' if receipt['readback_verified'] else 'created'
    receipt.pop('verification_error', None)
    Path(receipt_path).parent.joinpath('article-saved.html').write_text(content, encoding='utf-8')
    write_json(receipt_path, receipt)
    if not receipt['readback_verified']:
        raise PublishError('回读未通过：' + '、'.join(name for name, passed in checks.items() if not passed))
    return receipt


def validate_content(content, title):
    if not title or len(title.encode('utf-8')) > 64:
        raise PublishError('标题不能为空，且需控制在 64 UTF-8 字节以内。')
    if not content.strip() or len(content) >= 20000:
        raise PublishError('正文不能为空，且需少于 20000 字符。')
    if re.search(r'<\s*(?:script|style)\b', content, re.I):
        raise PublishError('正文请使用内联样式，并移除 script/style 标签。')


def parser():
    p = argparse.ArgumentParser(description='公众号草稿：本地预览、官方商品卡、封面上传与回读')
    p.add_argument('--content', type=Path, help='正文 HTML')
    p.add_argument('--title', help='文章标题')
    p.add_argument('--digest', default='')
    p.add_argument('--cover', type=Path, help='封面图片；推荐 900×383')
    p.add_argument('--thumb-media-id', help='使用已有永久封面素材 ID')
    p.add_argument('--config', type=Path, default=DEFAULTS_FILE, help='非敏感默认配置 JSON')
    p.add_argument('--env-file', help='仓库之外的账号文件')
    p.add_argument('--author')
    p.add_argument('--appid')
    p.add_argument('--secret', help='兼容选项；推荐环境变量或仓库外账号文件')
    p.add_argument('--product-id')
    p.add_argument('--card-type', type=int, choices=range(4), help='0 大卡 / 1 小卡 / 2 文字链 / 3 条卡')
    p.add_argument('--no-product-card', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='纯本地预览；无联网，无账号要求')
    p.add_argument('--output-dir', type=Path, help='预览、提交正文和回执保存目录')
    p.add_argument('--verify-receipt', type=Path, help='只回读已有回执，不创建草稿')
    p.add_argument('--force-new-draft', action='store_true', help='明确要求新建时，允许覆盖旧回执；先备份旧回执')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.verify_receipt:
        if args.dry_run or args.force_new_draft:
            raise PublishError('重查回执不能同时指定预览或新建。')
        receipt = load_json(args.verify_receipt)
        if not receipt.get('media_id'):
            raise PublishError('回执没有 media_id，请先检查公众号后台，不能通过本命令重建。')
        account = load_account_file(args.env_file)
        token = get_token(*credentials(args, account))
        try:
            verify_draft(token, receipt, args.verify_receipt)
        except PublishError as error:
            receipt['verification_error'] = redact(error)
            write_json(args.verify_receipt, receipt)
            print(f"草稿已存在，回读未通过。media_id：{receipt['media_id']}")
            print(redact(error), file=sys.stderr)
            return 2
        print(f"✅ 草稿回读验证通过。media_id：{receipt['media_id']}")
        return 0

    if args.content is None or args.title is None:
        raise PublishError('请指定 --content 和 --title，或使用 --verify-receipt。')
    defaults = load_json(args.config)
    account = {} if args.dry_run else load_account_file(args.env_file)
    author = setting(args.author, 'WECHAT_AUTHOR', account, defaults.get('author', ''))
    product_id = '' if args.no_product_card else str(setting(args.product_id, 'WECHAT_PRODUCT_ID', account, defaults.get('product_id', '')))
    try:
        card_type = int(setting(args.card_type, 'WECHAT_CARD_TYPE', account, defaults.get('card_type', 1)))
    except (TypeError, ValueError):
        raise PublishError('商品卡类型必须是 0、1、2 或 3。') from None
    if card_type not in range(4):
        raise PublishError('商品卡类型必须是 0、1、2 或 3。')
    content = args.content.read_text(encoding='utf-8')
    validate_content(content, args.title)
    if product_id and content.count(PLACEHOLDER) != 1:
        raise PublishError('挂商品时，正文必须有且仅有一个 <!--PRODUCT_CARD--> 占位符。')
    if args.cover and (not args.cover.is_file() or not args.cover.stat().st_size):
        raise PublishError('封面文件不存在或为空。')
    if args.cover and args.thumb_media_id:
        raise PublishError('--cover 和 --thumb-media-id 请只指定一个。')
    output = args.output_dir or args.content.resolve().parent / 'wechat-output'
    output.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        note = f'<p style="color:#888;">商品卡待联网获取：{html.escape(product_id)}</p>' if product_id else ''
        preview_content = content.replace(PLACEHOLDER, note)
        preview = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(args.title) + '</title><body style="max-width:700px;margin:32px auto;padding:0 20px;">' + preview_content + '</body></html>'
        output.joinpath('preview.html').write_text(preview, encoding='utf-8')
        write_json(output / 'preview.json', {
            'mode': 'local-preview', 'title': args.title, 'author': author,
            'product_id': product_id, 'card_type': card_type, 'cover_ready': bool(args.cover or args.thumb_media_id),
        })
        print(f'✅ 本地预览已保存：{output / "preview.html"}（未联网、未上传）')
        return 0

    receipt_path = output / 'publish-receipt.json'
    if receipt_path.exists():
        previous = load_json(receipt_path)
        if previous.get('media_id') or previous.get('status') == 'submitting':
            if not args.force_new_draft:
                raise PublishError('该目录已有创建记录，请先回读或核对后台；未重复提交。明确新建才可用 --force-new-draft。')
            backup = output / ('publish-receipt.' + uuid.uuid4().hex + '.json')
            receipt_path.rename(backup)
    if not args.cover and not args.thumb_media_id:
        raise PublishError('保存草稿需提供 --cover 或 --thumb-media-id。')
    token = get_token(*credentials(args, account))
    print('✅ 微信账号验证成功（token 不写入文件）')
    signature = ''
    if product_id:
        dom = get_product_card(token, product_id, card_type)
        signature = card_signatures(dom)[0]
        content = content.replace(PLACEHOLDER, dom)
        print(f'✅ 已插入官方商品卡：{product_id}')
    else:
        content = content.replace(PLACEHOLDER, '')
    validate_content(content, args.title)
    thumb = args.thumb_media_id or upload_image(token, args.cover)
    print('✅ 封面素材已准备')
    output.joinpath('article-submitted.html').write_text(content, encoding='utf-8')
    receipt = {
        'status': 'submitting', 'created_at': datetime.now(timezone.utc).isoformat(),
        'title': args.title, 'author': author, 'digest': args.digest,
        'product_id': product_id, 'card_type': card_type,
        'product_card_signature': signature, 'thumb_media_id': thumb,
        'content_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
        'readback_verified': False,
    }
    write_json(receipt_path, receipt)
    try:
        result = create_draft(token, args.title, author, args.digest, content, thumb)
    except WechatAPIError:
        receipt['status'] = 'rejected'
        write_json(receipt_path, receipt)
        raise
    if not result.get('media_id'):
        raise PublishError('创建请求没有返回 media_id；请先核对后台，未自动重试。')
    receipt['media_id'] = result['media_id']
    receipt['status'] = 'created'
    write_json(receipt_path, receipt)
    print(f"✅ 草稿已创建。media_id：{receipt['media_id']}")
    try:
        verify_draft(token, receipt, receipt_path)
    except PublishError as error:
        receipt['verification_error'] = redact(error)
        write_json(receipt_path, receipt)
        print('草稿已创建，回读尚未通过；请使用 --verify-receipt 重查，避免重新提交。')
        print(redact(error), file=sys.stderr)
        return 2
    print('✅ 回读确认：标题、作者、摘要、封面及指定商品卡均已保存。')
    print(f'回执：{receipt_path}')
    print('公众号后台：https://mp.weixin.qq.com → 内容管理 → 草稿箱')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except PublishError as error:
        print('❌ ' + redact(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, TypeError, KeyError):
        print('❌ 本地文件或配置无法读取，请检查输入文件和 JSON 格式；未自动重试。', file=sys.stderr)
        sys.exit(1)
