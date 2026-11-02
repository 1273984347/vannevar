// tests/test_builtin_ai.mjs — T-10：Chrome 内置 AI 端侧层降级链
//
// 运行：node tests/test_builtin_ai.mjs
// （不依赖 db.js，无需 loader；chrome.storage / LanguageModel / fetch 均为 globalThis mock）
//
// 覆盖：
//   未配置云端 + 端侧 available → 端侧结果被解析采用；
//   端侧输出带 markdown 围栏 → 宽松 JSON 提取仍成功；
//   unavailable / downloadable → 不可用（downloadable 不静默下载）；
//   create() 抛错 → null；已配置云端 → 云端优先，端侧零调用。

import assert from 'node:assert/strict';

// ---------- mock globalThis.chrome.storage.local ----------
const _storage = new Map();
globalThis.chrome = {
  storage: {
    local: {
      async get(key) {
        if (typeof key === 'string') {
          return _storage.has(key) ? { [key]: _storage.get(key) } : {};
        }
        return {};
      },
      async set(obj) {
        for (const [k, v] of Object.entries(obj)) _storage.set(k, v);
      },
      async remove(key) {
        if (typeof key === 'string') _storage.delete(key);
      },
    },
  },
};

// ---------- mock globalThis.LanguageModel（Chrome 内置 Prompt API） ----------
let _lmAvailability = 'available';
let _promptImpl = async () => '';
let _createCalls = 0;
let _throwOnCreate = false;
globalThis.LanguageModel = {
  async availability() { return _lmAvailability; },
  async create() {
    _createCalls++;
    if (_throwOnCreate) throw new Error('model init failed');
    return { prompt: async (text) => _promptImpl(text) };
  },
};

// ---------- mock globalThis.fetch（云端路径） ----------
let _fetchCalls = 0;
let _cloudBody = '';
globalThis.fetch = async () => {
  _fetchCalls++;
  return new Response(JSON.stringify({ choices: [{ message: { content: _cloudBody } }] }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  });
};

const { generateAISummary, generateAISummaryStream, setAIConfig, clearAIConfig } = await import('../ai.js');
const { createBuiltinAI } = await import('../lib/vannevar-builtin-ai.js');

const TABS = [{ title: '设计稿评审', url: 'https://figma.com/x' }, { title: 'React 文档', url: 'https://react.dev' }];
const GOOD_JSON = JSON.stringify({ scene: '前端开发', summary: '两页设计评审与框架文档并读。', keywords: ['设计'], nextSteps: ['恢复评审'], contextTags: ['深度专注'] });
const FENCED_JSON = '好的，以下是结果：\n```json\n' + GOOD_JSON + '\n```\n希望有帮助。';

let pass = 0, fail = 0;
async function t(name, fn) {
  try {
    _storage.clear(); _fetchCalls = 0; _createCalls = 0;
    _lmAvailability = 'available'; _throwOnCreate = false; _promptImpl = async () => GOOD_JSON;
    await fn();
    pass++; console.log('  ✓ ' + name);
  } catch (err) {
    fail++; console.log('  ✗ ' + name + '\n    ' + (err && err.message || err));
  }
}

await t('未配置云端 + 端侧 available → 端侧 JSON 被解析采用', async () => {
  const r = await generateAISummary(TABS);
  assert.equal(r.scene, '前端开发');
  assert.equal(_createCalls, 1, '端侧会话应被创建');
  assert.equal(_fetchCalls, 0, '不得发起云端请求');
});

await t('端侧输出带 markdown 围栏 → 宽松提取仍成功', async () => {
  _promptImpl = async () => FENCED_JSON;
  const r = await generateAISummary(TABS);
  assert.equal(r.summary, '两页设计评审与框架文档并读。');
});

await t('端侧 unavailable → null（降级规则引擎）', async () => {
  _lmAvailability = 'unavailable';
  assert.equal(await generateAISummary(TABS), null);
});

await t('端侧 downloadable → null（不静默下载数百 MB 模型）', async () => {
  _lmAvailability = 'downloadable';
  assert.equal(await generateAISummary(TABS), null);
  assert.equal(_createCalls, 0, 'downloadable 状态不得触发 create/下载');
});

await t('端侧 create() 抛错 → null 静默降级（全新模块实例验证首次 create）', async () => {
  _throwOnCreate = true;
  // 注意：ai.js 的端侧会话为进程内单例，此前用例可能已建会话；
  // 用 query 变体加载全新模块实例，确保 create() 真正被走到。
  const fresh = await import('../ai.js?case=create-throw');
  assert.equal(await fresh.generateAISummary(TABS), null);
});

await t('已配置云端 → 云端优先，端侧零调用', async () => {
  await setAIConfig({ apiKey: 'sk-test', baseUrl: 'https://api.example.com/v1/chat/completions', model: 'test-model' });
  _cloudBody = GOOD_JSON;
  const r = await generateAISummary(TABS);
  assert.equal(r.scene, '前端开发');
  assert.equal(_fetchCalls, 1, '应走云端');
  assert.equal(_createCalls, 0, '端侧不得被触发');
  await clearAIConfig();
});

await t('流式：未配置云端 → 端侧整段生成并一次性回调', async () => {
  _promptImpl = async () => '这是一段端侧生成的概述。';
  const chunks = [];
  const r = await generateAISummaryStream(TABS, (c) => chunks.push(c));
  assert.equal(r, '这是一段端侧生成的概述。');
  assert.deepEqual(chunks, ['这是一段端侧生成的概述。']);
  assert.equal(_fetchCalls, 0);
});

await t('适配层直接调用：availability 透传原始字符串', async () => {
  _lmAvailability = 'downloading';
  const adapter = createBuiltinAI();
  assert.equal(await adapter.availability(), 'downloading');
  assert.equal(await adapter.isAvailable(), false);
});

console.log('-----------------------------------');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
