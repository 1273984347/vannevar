# Muse MVP 对抗性测试
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


def _launch_extension(tmpdir, remove_context_menus=False):
    ext_dir = tmpdir / 'muse-mvp'
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
            '--headless=new',
        ],
        headless=False,
        accept_downloads=False,
    )
    return p, ctx, ext_id


def _attach_console_collectors(ctx, errors):
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

    return on_console


def _open_popup(ctx, ext_id, on_console=None):
    sws = ctx.service_workers
    if not sws:
        ctx.wait_for_event('serviceworker', timeout=10000)
    popup = ctx.new_page()
    if on_console:
        popup.on('console', lambda m: on_console('POPUP', m))
    popup.on('pageerror', lambda err: print(f'POPUP pageerror: {err}'))
    popup.goto(f'chrome-extension://{ext_id}/popup/popup.html')
    popup.wait_for_selector('#saveBtn', state='visible', timeout=10000)
    return popup


def _case_missing_permission():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_perm_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir, remove_context_menus=True)
        _attach_console_collectors(ctx, errors)

        normal_page = ctx.new_page()
        normal_page.goto('https://example.com')
        popup = _open_popup(ctx, ext_id)

        popup.wait_for_function(
            "() => document.getElementById('pageTitle').textContent !== '一键捕捉当前页面的灵感'",
            timeout=10000,
        )
        popup.click('#saveBtn')
        popup.wait_for_timeout(2000)
        status = popup.locator('#status').text_content()
        assert '灵感已保存' in status, f'缺失 contextMenus 时保存失败: {status}'
        print('[adv] 缺失 contextMenus 权限: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_disallowed_url():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_url_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

        bad_page = ctx.new_page()
        bad_page.goto('chrome://extensions/')
        popup = _open_popup(ctx, ext_id)

        popup.wait_for_function(
            "() => document.getElementById('pageTitle').textContent !== '一键捕捉当前页面的灵感'",
            timeout=10000,
        )
        status = popup.locator('#status').text_content()
        assert '当前页面无法被保存' in status or '无法读取当前页面' in status, f'危险 URL 处理异常: {status}'
        print('[adv] 危险 URL 过滤: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_invalid_save():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_invalid_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

        popup = _open_popup(ctx, ext_id)

        res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({
                action: 'SAVE_INSPIRATION',
                data: { title: '', url: 'https://example.com', mood: '' }
            }, (r) => resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null}));
        })''')
        assert res.get('lastError') is None, f'空标题保存导致连接错误: {res}'
        assert res.get('r') and res['r'].get('ok') is False, f'空标题应返回 ok=false: {res}'

        res2 = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({
                action: 'SAVE_INSPIRATION',
                data: { title: 't', url: 'javascript:alert(1)', mood: '' }
            }, (r) => resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null}));
        })''')
        assert res2.get('r') and res2['r'].get('ok') is False, f'危险 URL 应返回 ok=false: {res2}'

        res3 = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({
                action: 'SAVE_INSPIRATION',
                data: { title: 't', url: 'https://example.com', mood: '不存在' }
            }, (r) => resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null}));
        })''')
        assert res3.get('r') and res3['r'].get('ok') is False, f'非法 mood 应返回 ok=false: {res3}'

        print('[adv] 非法保存参数校验: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_rapid_saves():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_concurrent_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

        popup = _open_popup(ctx, ext_id)
        results = popup.evaluate('''() => Promise.all(Array.from({length: 5}, (_, i) =>
            new Promise((resolve) => {
                chrome.runtime.sendMessage({
                    action: 'SAVE_INSPIRATION',
                    data: { title: `rapid-${i}`, url: `https://example.com/${i}`, mood: '专注' }
                }, (r) => {
                    resolve({i, r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null});
                });
            })
        ))''')
        ok_count = sum(1 for r in results if r['r'] and r['r'].get('ok'))
        assert ok_count == 5, f'并发保存只有 {ok_count}/5 成功: {results}'

        res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({ action: 'GET_INSPIRATIONS' }, (r) => {
                resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null});
            });
        })''')
        assert res.get('lastError') is None, f'获取灵感失败: {res}'
        assert res['r'].get('ok') and len(res['r']['inspirations']) == 5, f'灵感数量异常: {res}'
        print('[adv] 并发保存: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_unknown_action():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_unknown_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

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


def _case_xss_sanitization():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_xss_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

        popup = _open_popup(ctx, ext_id)
        payload = '<img src=x onerror="alert(1)">'
        res = popup.evaluate(f'''() => new Promise((resolve) => {{
            chrome.runtime.sendMessage({{
                action: 'SAVE_INSPIRATION',
                data: {{ title: {json.dumps(payload)}, url: 'https://example.com', note: {json.dumps(payload)}, mood: '专注' }}
            }}, (r) => resolve({{r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null}}));
        }})''')
        assert res.get('r') and res['r'].get('ok'), f'XSS 测试保存失败: {res}'

        dashboard = ctx.new_page()
        dashboard.goto(f'chrome-extension://{ext_id}/dashboard/dashboard.html')
        dashboard.wait_for_selector('.card', state='visible', timeout=10000)

        html = dashboard.content()
        assert '<img src=x onerror="alert(1)">' not in html, 'dashboard 中发现了未转义的 HTML'
        assert '&lt;img src=x onerror="alert(1)"&gt;' in html or '&lt;img src=x onerror=\"alert(1)\"&gt;' in html, 'dashboard 没有正确转义 HTML'
        print('[adv] XSS 转义: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_search_special_chars():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_search_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

        popup = _open_popup(ctx, ext_id)
        res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({
                action: 'SAVE_INSPIRATION',
                data: { title: 'Special', url: 'https://example.com', note: 'test 100% note', mood: '专注' }
            }, (r) => resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null}));
        })''')
        assert res.get('r') and res['r'].get('ok'), f'保存失败: {res}'

        dashboard = ctx.new_page()
        dashboard.goto(f'chrome-extension://{ext_id}/dashboard/dashboard.html')
        dashboard.wait_for_selector('.card', state='visible', timeout=10000)
        dashboard.fill('#searchInput', '%')
        dashboard.wait_for_timeout(500)
        assert dashboard.locator('.card').count() == 1, '搜索特殊字符 % 应仍能匹配到记录'
        print('[adv] 搜索特殊字符: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _case_delete_nonexistent():
    tmpdir = Path(tempfile.mkdtemp(prefix='muse_adv_delete_'))
    p, ctx = None, None
    errors = []
    try:
        p, ctx, ext_id = _launch_extension(tmpdir)
        _attach_console_collectors(ctx, errors)

        popup = _open_popup(ctx, ext_id)
        res = popup.evaluate('''() => new Promise((resolve) => {
            chrome.runtime.sendMessage({ action: 'DELETE_INSPIRATION', id: 99999 }, (r) => {
                resolve({r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null});
            });
        })''')
        assert res.get('lastError') is None, f'删除不存在 ID 导致连接错误: {res}'
        assert res.get('r') and res['r'].get('ok'), f'删除不存在 ID 应正常返回 ok=true: {res}'
        print('[adv] 删除不存在 ID: PASSED')
    finally:
        if ctx:
            ctx.close()
        if p:
            p.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    cases = [
        _case_missing_permission,
        _case_disallowed_url,
        _case_invalid_save,
        _case_rapid_saves,
        _case_unknown_action,
        _case_xss_sanitization,
        _case_search_special_chars,
        _case_delete_nonexistent,
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
