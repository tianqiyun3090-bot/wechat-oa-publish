import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publish_draft', ROOT / 'scripts/publish_draft.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode('utf-8')


class WechatTransport:
    def __init__(self):
        self.calls = []
        self.article = None
        self.fail_verification = False
        self.uncertain_create = False
        self.reject_create = False
        self.wrong_card = False
        self.dom = '<section><mp-common-product data-windowproduct="TEST_WIDGET_SIGNATURE" data-title="测试商品"></mp-common-product></section>'

    def __call__(self, request, timeout):
        route = urllib.parse.urlsplit(request.full_url).path
        self.calls.append((route, request))
        if route == '/cgi-bin/token':
            return Response({'access_token': 'TEST_ACCESS_TOKEN'})
        if route.endswith('/getcardinfo'):
            return Response({'errcode': 0, 'DOM': self.dom})
        if route.endswith('/add_material'):
            return Response({'media_id': 'TEST_THUMB'})
        if route == '/cgi-bin/draft/add':
            if self.uncertain_create:
                raise urllib.error.URLError('simulated timeout')
            if self.reject_create:
                return Response({'errcode': 45009, 'errmsg': 'test quota'})
            self.article = json.loads(request.data.decode('utf-8'))['articles'][0]
            return Response({'media_id': 'TEST_DRAFT'})
        if route == '/cgi-bin/draft/get':
            if self.fail_verification:
                return Response({'errcode': 50000, 'errmsg': 'test unavailable'})
            article = dict(self.article)
            if self.wrong_card:
                article['content'] = article['content'].replace('TEST_WIDGET_SIGNATURE', 'OTHER_WIDGET')
            return Response({'news_item': [article]})
        raise AssertionError('Unexpected route: ' + route)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.content = self.folder / 'content.html'
        self.content.write_text('<section><p>中文正文</p><!--PRODUCT_CARD--></section>', encoding='utf-8')
        self.cover = self.folder / 'cover.png'
        self.cover.write_bytes(b'FAKE_COVER_FOR_MOCK_TRANSPORT')
        self.output = self.folder / 'wechat'
        self.receipt_path = self.output / 'publish-receipt.json'
        self.transport = WechatTransport()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(publish, 'load_account_file', return_value={}).start()
        self.network = patch.object(publish.urllib.request, 'urlopen', side_effect=self.transport).start()
        self.stdout = io.StringIO()
        patch('sys.stdout', self.stdout).start()
        patch('sys.stderr', io.StringIO()).start()
        publish.SENSITIVE_VALUES.clear()

    def args(self, *extra):
        return ['--content', str(self.content), '--title', '中文测试标题',
                '--digest', '测试摘要', '--cover', str(self.cover),
                '--output-dir', str(self.output), '--appid', 'TEST_APPID',
                '--secret', 'TEST_APP_SECRET', *extra]

    def receipt(self):
        return json.loads(self.receipt_path.read_text(encoding='utf-8'))

    def routes(self):
        return [route for route, _ in self.transport.calls]

    def test_preview_is_offline_without_any_credentials(self):
        with patch.object(publish, 'load_account_file', side_effect=AssertionError('must not load credentials')):
            result = publish.main(['--content', str(self.content), '--title', '预览标题',
                                   '--output-dir', str(self.output), '--dry-run'])
        self.assertEqual(result, 0)
        self.network.assert_not_called()
        data = json.loads((self.output / 'preview.json').read_text())
        self.assertEqual(data['product_id'], '10000828419925')
        self.assertEqual(data['author'], '天启云AI')
        self.assertIn('商品卡待联网获取', (self.output / 'preview.html').read_text())
        self.assertFalse(self.receipt_path.exists())

    def test_publish_defaults_utf8_readback_and_no_credentials_in_outputs(self):
        self.assertEqual(publish.main(self.args()), 0)
        self.assertEqual(self.routes(), ['/cgi-bin/token', '/channels/ec/service/product/getcardinfo',
                                         '/cgi-bin/material/add_material', '/cgi-bin/draft/add', '/cgi-bin/draft/get'])
        card_request = self.transport.calls[1][1]
        self.assertEqual(json.loads(card_request.data)['product_id'], '10000828419925')
        self.assertEqual(self.transport.article['author'], '天启云AI')
        self.assertIn('中文正文'.encode('utf-8'), self.transport.calls[3][1].data)
        self.assertTrue(self.receipt()['readback_verified'])
        self.assertEqual(self.receipt()['status'], 'verified')
        self.assertIn('TEST_WIDGET_SIGNATURE', (self.output / 'article-saved.html').read_text())
        for path in self.output.iterdir():
            self.assertNotIn('TEST_APP_SECRET', path.read_text())
            self.assertNotIn('TEST_ACCESS_TOKEN', path.read_text())

    def test_no_product_skips_card_endpoint(self):
        self.assertEqual(publish.main(self.args('--no-product-card')), 0)
        self.assertNotIn('/channels/ec/service/product/getcardinfo', self.routes())
        self.assertNotIn(publish.PLACEHOLDER, self.transport.article['content'])
        self.assertEqual(self.receipt()['product_id'], '')

    def test_single_use_settings_override_defaults(self):
        self.assertEqual(publish.main(self.args('--product-id', 'OTHER_PRODUCT', '--author', '新作者', '--card-type', '3')), 0)
        request = json.loads(self.transport.calls[1][1].data)
        self.assertEqual(request['product_id'], 'OTHER_PRODUCT')
        self.assertEqual(request['card_type'], 3)
        self.assertEqual(self.transport.article['author'], '新作者')

    def test_duplicate_attempt_does_not_make_any_new_network_request(self):
        publish.main(self.args())
        count = self.network.call_count
        with self.assertRaises(publish.PublishError):
            publish.main(self.args())
        self.assertEqual(self.network.call_count, count)

    def test_failed_readback_preserves_id_and_can_be_verified_without_new_draft(self):
        self.transport.fail_verification = True
        self.assertEqual(publish.main(self.args()), 2)
        self.assertEqual(self.receipt()['media_id'], 'TEST_DRAFT')
        self.assertEqual(self.receipt()['status'], 'created')
        self.assertFalse(self.receipt()['readback_verified'])
        self.transport.fail_verification = False
        self.transport.calls.clear()
        self.assertEqual(publish.main(['--verify-receipt', str(self.receipt_path),
                                       '--appid', 'TEST_APPID', '--secret', 'TEST_APP_SECRET']), 0)
        self.assertEqual(self.routes(), ['/cgi-bin/token', '/cgi-bin/draft/get'])
        self.assertTrue(self.receipt()['readback_verified'])

    def test_uncertain_submission_is_recorded_and_blocks_automatic_resubmission(self):
        self.transport.uncertain_create = True
        with self.assertRaises(publish.PublishError):
            publish.main(self.args())
        self.assertEqual(self.receipt()['status'], 'submitting')
        self.assertNotIn('media_id', self.receipt())
        count = self.network.call_count
        with self.assertRaises(publish.PublishError):
            publish.main(self.args())
        self.assertEqual(self.network.call_count, count)

    def test_definitive_wechat_rejection_has_rejected_state(self):
        self.transport.reject_create = True
        with self.assertRaises(publish.WechatAPIError):
            publish.main(self.args())
        self.assertEqual(self.receipt()['status'], 'rejected')
        self.assertNotIn('media_id', self.receipt())

    def test_missing_card_placeholder_fails_before_network(self):
        self.content.write_text('<p>没有商品占位符</p>', encoding='utf-8')
        with self.assertRaises(publish.PublishError):
            publish.main(self.args())
        self.network.assert_not_called()

    def test_injected_content_limit_checked_before_cover_upload(self):
        self.transport.dom += 'x' * 20000
        with self.assertRaises(publish.PublishError):
            publish.main(self.args())
        self.assertNotIn('/cgi-bin/material/add_material', self.routes())
        self.assertNotIn('/cgi-bin/draft/add', self.routes())

    def test_different_product_signature_fails_readback(self):
        self.transport.wrong_card = True
        self.assertEqual(publish.main(self.args()), 2)
        self.assertFalse(self.receipt()['verification_checks']['product_card'])
        self.assertEqual(self.receipt()['media_id'], 'TEST_DRAFT')

    def test_error_messages_redact_secret_and_access_token(self):
        publish.SENSITIVE_VALUES.update({'TEST_APP_SECRET', 'TEST_ACCESS_TOKEN'})
        with patch.object(publish.urllib.request, 'urlopen', return_value=Response({
            'errcode': 40001, 'errmsg': 'TEST_APP_SECRET access_token=TEST_ACCESS_TOKEN'})):
            with self.assertRaises(publish.WechatAPIError) as raised:
                publish.http_json('https://api.weixin.qq.com/test')
        self.assertNotIn('TEST_APP_SECRET', str(raised.exception))
        self.assertNotIn('TEST_ACCESS_TOKEN', str(raised.exception))

    def test_force_new_draft_retains_previous_receipt(self):
        publish.main(self.args())
        self.assertEqual(publish.main(self.args('--force-new-draft')), 0)
        backups = list(self.output.glob('publish-receipt.*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())['media_id'], 'TEST_DRAFT')


if __name__ == '__main__':
    unittest.main()
