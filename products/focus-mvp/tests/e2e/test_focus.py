# Focus MVP 端到端测试
# 用法：python tests/e2e/test_focus.py

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

# 被测扩展根目录
EXT_SRC = Path(__file__).resolve().parents[2]


def _make_key_and_id():
    """生成固定扩展 ID 所需的 RSA key，并计算对应扩展 ID。"""
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


def _run(headless: bool):
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_mvp_e2e_'))
    ext_dir = tmpdir / 'focus-mvp'
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

            ctx = p.chromium.launch_persistent_context(
                str(user_data_dir),
                args=[
                    f'--disable-extensions-except={ext_dir}',
                    f'--load-extension={ext_dir}',
                ],
                headless=headless,
                accept_downloads=False,
            )

            def on_console(source, msg):
                line = f'[{source}] {msg.type}: {msg.text}'
                print(line)
                if msg.type == 'error':
                    errors.append(line)

            # 等待 Service Worker 出现
            sws = ctx.service_workers
            if not sws:
                ctx.wait_for_event('serviceworker', timeout=10000)
                sws = ctx.service_workers
            for sw in sws:
                sw.on('console', lambda m: on_console('SW', m))

            # 打开一个普通页面，再打开 popup.html，让当前窗口包含可保存的标签页
            normal_page = ctx.new_page()
            normal_page.goto('https://example.com')

            popup = ctx.new_page()
            popup.on('console', lambda m: on_console('POPUP', m))
            popup.on('pageerror', lambda err: errors.append(f'POPUP pageerror: {err}'))
            popup.goto(f'chrome-extension://{ext_id}/popup.html')
            popup.wait_for_selector('#saveBtn', state='visible', timeout=10000)

            # 保存当前上下文
            popup.click('#saveBtn')
            popup.wait_for_timeout(3000)
            status = popup.locator('#status').text_content()
            print(f'[test] save status: {status!r}')
            assert '已保存' in status, f'保存失败: {status}'
            assert popup.locator('#snapshotList li').count() == 1, '快照列表未更新'

            # 删除快照
            popup.on('dialog', lambda dialog: dialog.accept())
            popup.click('.delete-btn')
            popup.wait_for_timeout(1000)
            assert popup.locator('#snapshotList li').count() == 0, '快照未删除'

            ctx.close()

        if errors:
            print('[test] 发现控制台错误:')
            for e in errors:
                print('  ', e)
            raise AssertionError('控制台存在 error')

        print('[test] PASSED')
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    try:
        return _run(headless=True)
    except Exception as e:
        print(f'[test] headless 失败: {e}，尝试 headed 模式')
        return _run(headless=False)


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
