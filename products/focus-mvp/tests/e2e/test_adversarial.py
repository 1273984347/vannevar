# Focus MVP 对抗测试
# 用法：python tests/e2e/test_adversarial.py

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


def _launch_extension(tmpdir, headless, remove_context_menus=False):
    ext_dir = tmpdir / 'focus-mvp'
    shutil.copytree(EXT_SRC, ext_dir)
    key_b64, ext_id = _make_key_and_id()

    manifest_path = ext_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['key'] = key_b64
    if remove_context_menus:
        manifest['permissions'] = [p for p in manifest['permissions'] if p != 'contextMenus']
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    p = sync_playwright().start()
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
    return p, ctx, ext_id


def _open_popup(ctx, ext_id):
    sws = ctx.service_workers
    if not sws:
        ctx.wait_for_event('serviceworker', timeout=10000)
    popup = ctx.new_page()
    popup.goto(f'chrome-extension://{ext_id}/popup.html')
    popup.wait_for_selector('#saveBtn', state='visible', timeout=10000)
    return popup


def _case_missing_permission():
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_adv_perm_'))
    p, ctx, ext_id = None, None, None
    try:
        p, ctx, ext_id = _launch_extension(tmpdir, headless=False, remove_context_menus=True)
        popup = _open_popup(ctx, ext_id)
        popup.click('#saveBtn')
        popup.wait_for_timeout(2000)
        status = popup.locator('#status').text_content()
        assert '已保存' in status or '0 个标签页' in status, f'缺失 contextMenus 时保存失败: {status}'
        print('[adv] 缺失 contextMenus 权限: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_disallowed_urls():
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_adv_url_'))
    p, ctx, ext_id = None, None, None
    try:
        p, ctx, ext_id = _launch_extension(tmpdir, headless=False)
        # 打开一个 chrome:// 页面，再打开 popup
        bad_page = ctx.new_page()
        bad_page.goto('chrome://extensions/')
        popup = _open_popup(ctx, ext_id)
        popup.click('#saveBtn')
        popup.wait_for_timeout(2000)
        status = popup.locator('#status').text_content()
        # 只有 chrome:// 和 chrome-extension:// 页面时，应保存 0 个标签页
        assert '0 个标签页' in status, f'危险 URL 过滤异常: {status}'
        print('[adv] 危险 URL 过滤: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_restore_blocked_url():
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_adv_restore_'))
    p, ctx, ext_id = None, None, None
    try:
        p, ctx, ext_id = _launch_extension(tmpdir, headless=False)
        popup = _open_popup(ctx, ext_id)
        # 直接调用后台恢复一个包含 javascript: URL 的快照
        res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({
                action: 'RESTORE_SNAPSHOT',
                snapshot: { tabs: [{ url: 'javascript:alert(1)' }] }
            }, (r) => resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null}));
        })''')
        assert res.get('lastError') is None, f'恢复被阻止的 URL 时发生连接错误: {res}'
        print('[adv] 恢复被阻止的 URL 不崩溃: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_concurrent_saves():
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_adv_concurrent_'))
    p, ctx, ext_id = None, None, None
    try:
        p, ctx, ext_id = _launch_extension(tmpdir, headless=False)
        normal_page = ctx.new_page()
        normal_page.goto('https://example.com')
        popup = _open_popup(ctx, ext_id)

        # 连续发送 5 条保存消息，测试初始化锁
        results = popup.evaluate('''() => Promise.all(Array.from({length: 5}, (_, i) =>
            new Promise((resolve) => {
                chrome.runtime.sendMessage({ action: 'SAVE_CONTEXT' }, (r) => {
                    resolve({i, r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null});
                });
            })
        ))''')
        ok_count = sum(1 for r in results if r['r'] and r['r'].get('ok'))
        assert ok_count == 5, f'并发保存只有 {ok_count}/5 成功: {results}'
        # 通过后台消息验证快照数量，而不是依赖 popup UI 刷新
        snapshot_res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({ action: 'GET_SNAPSHOTS' }, (r) => {
                resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null});
            });
        })''')
        assert snapshot_res.get('lastError') is None, f'获取快照失败: {snapshot_res}'
        assert snapshot_res['r'].get('ok') and len(snapshot_res['r']['snapshots']) == 5, f'快照数量异常: {snapshot_res}'
        print('[adv] 并发保存: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_unknown_action():
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_adv_unknown_'))
    p, ctx, ext_id = None, None, None
    try:
        p, ctx, ext_id = _launch_extension(tmpdir, headless=False)
        popup = _open_popup(ctx, ext_id)
        res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({ action: 'UNKNOWN_ACTION' }, (r) => {
                resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null});
            });
        })''')
        assert res.get('lastError') is None, f'未知 action 导致连接错误: {res}'
        assert res.get('r') and res['r'].get('ok') is False, f'未知 action 应返回 ok=false: {res}'
        print('[adv] 未知 action 处理: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    cases = [
        _case_missing_permission,
        _case_disallowed_urls,
        _case_restore_blocked_url,
        _case_concurrent_saves,
        _case_unknown_action,
    ]
    for case in cases:
        try:
            case()
        except Exception as e:
            print(f'[adv] {case.__name__} FAILED: {e}')
            return False
    print('\n[adv] 所有对抗测试 PASSED')
    return True


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
