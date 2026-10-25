# Focus MVP 最小化 Smoke Test
# 用法: py tests/e2e/smoke_test.py
#
# 5 项最小检查（不依赖完整保存/恢复流程，不依赖网络）：
#   T1 扩展能正常加载（manifest 解析无错误）
#   T2 popup 能正常打开（HTML/JS 无语法错误）
#   T3 options 页能正常打开
#   T4 background service worker 能注册
#   T5 db.js 能初始化 sql.js（WASM 加载成功）
#
# 设计原则：
#   - 不依赖网络（不访问 example.com 等 external 站点）
#   - 不测试完整保存/恢复流程（该流程依赖 chrome.tabs 真实标签页，脆弱）
#   - 只验证「扩展能加载、页面能打开、SW 能注册、DB 能初始化」
#   - 使用 headed 模式（MV3 SW 在 headless 下无法稳定注册）

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
IGNORE = shutil.ignore_patterns('tests', '__pycache__', '*.pyc', '.git', 'node_modules')


def _make_key_and_id():
    """生成固定扩展 ID 所需的 RSA 公钥（DER），并计算对应扩展 ID。"""
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


def run():
    tmpdir = Path(tempfile.mkdtemp(prefix='focus_smoke_'))
    ext_dir = tmpdir / 'focus-mvp'
    errors = []
    results = []

    try:
        shutil.copytree(EXT_SRC, ext_dir, ignore=IGNORE)
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
                headless=False,
                accept_downloads=False,
            )

            # --- T4: background service worker 能注册 ---
            sws = ctx.service_workers
            if not sws:
                try:
                    ctx.wait_for_event('serviceworker', timeout=15000)
                    sws = ctx.service_workers
                except Exception:
                    pass
            if sws:
                sw = sws[0]
                sw.on('console', lambda m: errors.append(f'[SW console] {m.text}') if m.type == 'error' else None)
                results.append(('T4 background SW 注册', True))
            else:
                results.append(('T4 background SW 注册', False))
                errors.append('Service Worker 未能在 15s 内注册（manifest 可能解析失败或扩展未加载）')

            # --- T1: 扩展能正常加载（依赖 T4：SW 注册成功 = manifest 解析成功 = 扩展已加载）---
            results.append(('T1 扩展加载（manifest 解析）', len(sws) > 0))

            # --- T2: popup 能正常打开 ---
            popup = ctx.new_page()
            popup_errors = []
            popup.on('console', lambda m: popup_errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
            popup.on('pageerror', lambda err: popup_errors.append(f'pageerror: {err}'))
            try:
                popup.goto(f'chrome-extension://{ext_id}/popup.html')
                popup.wait_for_selector('#saveBtn', state='visible', timeout=10000)
                results.append(('T2 popup 打开（#saveBtn 可见）', len(popup_errors) == 0))
                errors.extend(f'[popup] {e}' for e in popup_errors)
            except Exception as e:
                results.append(('T2 popup 打开（#saveBtn 可见）', False))
                errors.append(f'[popup] 打开失败: {e}')

            # --- T3: options 页能正常打开 ---
            options = ctx.new_page()
            options_errors = []
            options.on('console', lambda m: options_errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
            options.on('pageerror', lambda err: options_errors.append(f'pageerror: {err}'))
            try:
                options.goto(f'chrome-extension://{ext_id}/options.html')
                options.wait_for_load_state('domcontentloaded', timeout=10000)
                results.append(('T3 options 页打开', len(options_errors) == 0))
                errors.extend(f'[options] {e}' for e in options_errors)
            except Exception as e:
                results.append(('T3 options 页打开', False))
                errors.append(f'[options] 打开失败: {e}')

            # --- T5: db.js 能初始化 sql.js（WASM 加载成功）---
            # GET_SNAPSHOTS 消息内部会 await initDatabase()，若 WASM 加载失败则 ok=false
            if sws:
                try:
                    db_res = popup.evaluate('''() => new Promise((resolve) => {
                        const t = setTimeout(() => resolve({ timeout: true }), 20000);
                        chrome.runtime.sendMessage({ action: 'GET_SNAPSHOTS' }, (r) => {
                            clearTimeout(t);
                            resolve({ r: r, lastError: chrome.runtime.lastError ? chrome.runtime.lastError.message : null });
                        });
                    })''')
                    db_ok = (db_res.get('timeout') is not True
                             and db_res.get('lastError') is None
                             and db_res.get('r') is not None
                             and db_res['r'].get('ok') is True)
                    results.append(('T5 DB 初始化（sql.js WASM 加载）', db_ok))
                    if not db_ok:
                        errors.append(f'[db] GET_SNAPSHOTS 返回异常: {db_res}')
                except Exception as e:
                    results.append(('T5 DB 初始化（sql.js WASM 加载）', False))
                    errors.append(f'[db] evaluate 失败: {e}')
            else:
                results.append(('T5 DB 初始化（sql.js WASM 加载）', False))
                errors.append('[db] SW 未注册，跳过 DB 初始化测试')

            ctx.close()

        # 输出结果
        print('\n' + '=' * 55)
        print('  Focus MVP 最小化 Smoke Test 结果')
        print('=' * 55)
        all_passed = True
        for name, passed in results:
            status = 'PASS' if passed else 'FAIL'
            print(f'  [{status}]  {name}')
            if not passed:
                all_passed = False

        if errors:
            print('\n--- 错误详情 ---')
            for e in errors:
                print(f'  {e}')

        passed_count = sum(1 for _, p in results if p)
        total = len(results)
        print(f'\n总计: {passed_count}/{total} 通过')
        print('结论: ' + ('ALL PASS — 扩展可加载可运行' if all_passed else '存在失败项 — 需排查'))
        return all_passed

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    ok = run()
    sys.exit(0 if ok else 1)
