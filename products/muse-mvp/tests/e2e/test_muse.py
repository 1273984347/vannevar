# Muse MVP 端到端测试
# 用法：python tests/e2e/test_muse.py

import base64
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from playwright.sync_api import sync_playwright

EXT_SRC = Path(__file__).resolve().parents[2]


def _make_key_and_id():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashlib.sha256(public_der).digest()
    alphabet = 'abcdefghijklmnop'
    ext_id = ''.join(alphabet[b >> 4] + alphabet[b & 0x0F] for b in digest)[:32]
    key_b64 = base64.b64encode(public_der).decode('ascii')
    return key_b64, ext_id


def _run(headless):
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_mvp_'))
    ext_dir = tmpdir / 'muse-mvp'
    errors = []
    try:
        shutil.copytree(EXT_SRC, ext_dir)
        key_b64, ext_id = _make_key_and_id()

        manifest_path = ext_dir / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['key'] = key_b64
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

        with sync_playwright() as p:
            user_data_dir = tmpdir / 'userdata'
            user_data_dir.mkdir()
            args = [
                f'--disable-extensions-except={ext_dir}',
                f'--load-extension={ext_dir}',
                # 使用 Chrome 新版 headless，避免旧 headless 中扩展 popup 页面被 abort
                '--headless=new',
            ]
            ctx = p.chromium.launch_persistent_context(
                str(user_data_dir),
                args=args,
                headless=headless,
                accept_downloads=False,
            )

            def on_console(source, msg):
                line = f'[{source}] {msg.type}: {msg.text}'
                print(line)
                if msg.type == 'error':
                    errors.append(line)

            def attach_sw(sw):
                sw.on('console', lambda m: on_console('SW', m))

            ctx.on('serviceworker', lambda sw: attach_sw(sw))
            for sw in ctx.service_workers:
                attach_sw(sw)

            normal_page = ctx.new_page()
            normal_page.goto('https://example.com')

            # 给 Service Worker 一点启动时间
            normal_page.wait_for_timeout(2000)

            popup = ctx.new_page()
            popup.on('console', lambda m: on_console('POPUP', m))
            popup.on('pageerror', lambda err: errors.append(f'POPUP pageerror: {err}'))
            popup.goto(f'chrome-extension://{ext_id}/popup/popup.html')
            popup.wait_for_selector('#saveBtn', state='visible', timeout=10000)

            # 等待页面信息加载完成
            popup.wait_for_function(
                "() => document.getElementById('pageTitle').textContent !== '一键捕捉当前页面的灵感'",
                timeout=10000
            )

            # 填写备注并选择氛围
            popup.fill('#noteInput', '这是一个测试灵感')
            popup.click('.mood-tag:text("专注")')
            popup.click('#saveBtn')
            popup.wait_for_timeout(3000)

            status = popup.locator('#status').text_content()
            print(f'[test] save status: {status!r}')
            assert '灵感已保存' in status, f'保存失败: {status}'

            # 打开仪表盘验证列表
            dashboard = ctx.new_page()
            dashboard.on('console', lambda m: on_console('DASHBOARD', m))
            dashboard.on('pageerror', lambda err: errors.append(f'DASHBOARD pageerror: {err}'))
            dashboard.goto(f'chrome-extension://{ext_id}/dashboard/dashboard.html')
            dashboard.wait_for_selector('.card', state='visible', timeout=10000)
            assert dashboard.locator('.card').count() == 1, '仪表盘未显示保存的灵感'

            ctx.close()

        if errors:
            print('[test] errors:', errors)
            raise AssertionError('Console errors detected')

        print('[test] PASSED')
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    return _run(headless=False)


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
