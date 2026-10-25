// tests/test_media_session.mjs — Phase 4.2 Muse 媒体会话集成测试
// 用法：node --import ./tests/loader.mjs tests/test_media_session.mjs
//
// 测试覆盖：
//   1. saveInspiration 写入 media 字段（INSERT 包含 media_type/artist/album/artwork_url）
//   2. getInspirations 返回 media 字段（查询结果映射到 mediaType/Artist/Album/ArtworkUrl）
//   3. captureMediaMetadata 推断 mediaType：
//      - YouTube URL → video
//      - 喜马拉雅 URL → podcast
//      - 无 mediaSession.metadata → null
//   4. captureMediaMetadata artwork 数组按 size 降序取最大那张
//   5. saveInspiration 无 media 字段时写入 NULL（兼容旧数据）

import { strict as assert } from 'node:assert';
import { pathToFileURL } from 'node:url';
import { dirname, resolve as pathResolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const museJsPath = pathToFileURL(pathResolve(__dirname, '..', 'core', 'muse.js')).href;

// 重置 db mock 状态（每个测试前清空）
async function resetAll() {
  const dbModule = await import('./mocks/db.js');
  dbModule._resetDbState();
}

// mock chrome 全局对象（captureMediaMetadata 依赖）
// 参数说明：
//   - executeScriptResult: 直接返回模式（绕过 func，返回固定值）。null 表示默认无 metadata。
//   - pageMetadata: 页面侧 mock 模式 — 实际执行注入 func，让 func body 读取 navigator.mediaSession.metadata。
//     该模式用于覆盖 func 内部的 artwork 排序逻辑（直接返回模式绕过 func 会导致 0 覆盖）。
//   - tabUrl: chrome.tabs.get 返回的 URL，用于 source 推断。
function setupChromeMock({ executeScriptResult = null, pageMetadata = null, tabUrl = '' } = {}) {
  const _executeScriptResult = executeScriptResult;
  const _pageMetadata = pageMetadata;
  const _tabUrl = tabUrl;

  // 设置页面侧 navigator.mediaSession（仅在 pageMetadata 模式下生效）
  // executeScript 注入的 func 会在"页面上下文"执行，模拟该上下文以使 func body 能读取 mediaSession
  const originalNavigator = globalThis.navigator;
  if (_pageMetadata !== null) {
    // 注意：globalThis.navigator 在 Node.js 中是只读的，需用 Object.defineProperty 覆盖
    Object.defineProperty(globalThis, 'navigator', {
      value: { mediaSession: { metadata: _pageMetadata } },
      configurable: true,
      writable: true,
    });
  }

  globalThis.chrome = {
    scripting: {
      async executeScript({ target, func }) {
        // 页面侧 mock 模式：实际执行注入 func，使其读取 globalThis.navigator.mediaSession.metadata
        if (_pageMetadata !== null) {
          // func 在页面上下文执行；通过 globalThis.navigator 模拟该上下文
          const result = func();
          return [{ result }];
        }
        // 直接返回模式（绕过 func）
        if (typeof _executeScriptResult === 'function') {
          return [{ result: _executeScriptResult() }];
        }
        if (_executeScriptResult !== null) {
          return [{ result: _executeScriptResult }];
        }
        // 默认无 metadata
        return [{ result: null }];
      },
    },
    tabs: {
      async get(tabId) {
        return { id: tabId, url: _tabUrl };
      },
    },
  };
}

function clearChromeMock() {
  delete globalThis.chrome;
  // 恢复 navigator（若被 pageMetadata 模式覆盖）
  if (globalThis.navigator && globalThis.navigator.mediaSession) {
    delete globalThis.navigator;
  }
}

// 测试 1: saveInspiration 写入 media 字段
async function test_saveInspiration_writes_media_fields() {
  await resetAll();
  const muse = await import(museJsPath);
  await muse.saveInspiration({
    title: '夜曲灵感',
    url: 'https://example.com/inspiration',
    note: '夜晚听周杰伦的夜曲',
    mood: '',
    selectedText: '',
    mediaType: 'music',
    mediaArtist: '周杰伦',
    mediaAlbum: '十一月的萧邦',
    mediaArtworkUrl: 'https://example.com/artwork.jpg',
  });

  const dbModule = await import('./mocks/db.js');
  const rows = dbModule._getRows('inspirations');
  assert.equal(rows.length, 1, '应插入 1 条灵感');
  const row = rows[0];
  assert.equal(row.title, '夜曲灵感');
  assert.equal(row.media_type, 'music', 'media_type 应写入 music');
  assert.equal(row.media_artist, '周杰伦', 'media_artist 应写入');
  assert.equal(row.media_album, '十一月的萧邦', 'media_album 应写入');
  assert.equal(row.media_artwork_url, 'https://example.com/artwork.jpg', 'media_artwork_url 应写入');
  console.log('[PASS] test_saveInspiration_writes_media_fields');
}

// 测试 2: saveInspiration 无 media 字段时写入 NULL
async function test_saveInspiration_null_media_fields() {
  await resetAll();
  const muse = await import(museJsPath);
  await muse.saveInspiration({
    title: '普通灵感',
    url: 'https://example.com/plain',
    note: '',
    mood: '',
    selectedText: '',
    // 不传 media 字段，应默认 null
  });

  const dbModule = await import('./mocks/db.js');
  const rows = dbModule._getRows('inspirations');
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.media_type, null, 'media_type 未提供时应为 null');
  assert.equal(row.media_artist, null, 'media_artist 未提供时应为 null');
  assert.equal(row.media_album, null, 'media_album 未提供时应为 null');
  assert.equal(row.media_artwork_url, null, 'media_artwork_url 未提供时应为 null');
  console.log('[PASS] test_saveInspiration_null_media_fields');
}

// 测试 3: getInspirations 返回 media 字段
async function test_getInspirations_returns_media_fields() {
  await resetAll();
  const dbModule = await import('./mocks/db.js');
  dbModule._seedRows('inspirations', [
    {
      id: 1, title: 'A', url: 'https://a.com', note: '', mood: '', selected_text: '',
      created_at: 1000, pinned: 0, updated_at: null,
      media_type: 'music', media_artist: 'Artist A', media_album: 'Album A', media_artwork_url: 'https://a.com/art.jpg',
    },
    {
      id: 2, title: 'B', url: 'https://b.com', note: '', mood: '', selected_text: '',
      created_at: 2000, pinned: 0, updated_at: null,
      media_type: null, media_artist: null, media_album: null, media_artwork_url: null,
    },
  ]);

  const muse = await import(museJsPath);
  const items = await muse.getInspirations(10);
  assert.equal(items.length, 2);
  // 按 created_at DESC 排序，B 在前
  const itemB = items[0];
  const itemA = items[1];
  assert.equal(itemB.mediaType, null, '无媒体灵感 mediaType 应为 null');
  assert.equal(itemB.mediaArtist, null);
  assert.equal(itemA.mediaType, 'music', '有媒体灵感 mediaType 应为 music');
  assert.equal(itemA.mediaArtist, 'Artist A');
  assert.equal(itemA.mediaAlbum, 'Album A');
  assert.equal(itemA.mediaArtworkUrl, 'https://a.com/art.jpg');
  console.log('[PASS] test_getInspirations_returns_media_fields');
}

// 测试 4: captureMediaMetadata YouTube URL → video
async function test_captureMediaMetadata_youtube_to_video() {
  await resetAll();
  setupChromeMock({
    executeScriptResult: {
      title: 'Never Gonna Give You Up',
      artist: 'Rick Astley',
      album: 'Whenever You Need Somebody',
      artworkUrl: 'https://i.ytimg.com/art.jpg',
    },
    tabUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(123);
    assert.ok(result, '应返回 metadata');
    assert.equal(result.title, 'Never Gonna Give You Up');
    assert.equal(result.artist, 'Rick Astley');
    assert.equal(result.source, 'video', 'YouTube URL 应推断为 video');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_youtube_to_video');
}

// 测试 5: captureMediaMetadata 喜马拉雅 URL → podcast
async function test_captureMediaMetadata_ximalaya_to_podcast() {
  await resetAll();
  setupChromeMock({
    executeScriptResult: {
      title: 'Episode 42',
      artist: 'Some Podcaster',
      album: '',
      artworkUrl: '',
    },
    tabUrl: 'https://www.ximalaya.com/sound/123',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(456);
    assert.ok(result);
    assert.equal(result.source, 'podcast', '喜马拉雅 URL 应推断为 podcast');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_ximalaya_to_podcast');
}

// 测试 6: captureMediaMetadata 默认 → music
async function test_captureMediaMetadata_default_to_music() {
  await resetAll();
  setupChromeMock({
    executeScriptResult: {
      title: 'Some Song',
      artist: 'Some Artist',
      album: '',
      artworkUrl: '',
    },
    tabUrl: 'https://music.example.com/track/1',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(789);
    assert.ok(result);
    assert.equal(result.source, 'music', '非视频/播客 URL 应默认 music');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_default_to_music');
}

// 测试 7: captureMediaMetadata 无 metadata → null
async function test_captureMediaMetadata_no_metadata_returns_null() {
  await resetAll();
  setupChromeMock({
    executeScriptResult: null,
    tabUrl: 'https://example.com/page',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(111);
    assert.equal(result, null, '无 mediaSession.metadata 应返回 null');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_no_metadata_returns_null');
}

// 测试 8: captureMediaMetadata 无 title → null（即使 metadata 存在但 title 为空）
async function test_captureMediaMetadata_empty_title_returns_null() {
  await resetAll();
  setupChromeMock({
    executeScriptResult: {
      title: '',
      artist: 'Some Artist',
      album: '',
      artworkUrl: '',
    },
    tabUrl: 'https://example.com/page',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(222);
    assert.equal(result, null, 'title 为空时应返回 null');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_empty_title_returns_null');
}

// 测试 9: captureMediaMetadata 无 tabId → null
async function test_captureMediaMetadata_no_tabid_returns_null() {
  await resetAll();
  setupChromeMock({ executeScriptResult: null, tabUrl: '' });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(0);
    assert.equal(result, null, 'tabId 为 0/falsy 应返回 null');
    const result2 = await muse.captureMediaMetadata(null);
    assert.equal(result2, null, 'tabId 为 null 应返回 null');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_no_tabid_returns_null');
}

// 测试 9a: captureMediaMetadata artwork 数组按 size 降序取最大那张
// 覆盖注入 func 内部的 artwork 排序逻辑（之前 mock 绕过 func 导致 0 覆盖）
async function test_captureMediaMetadata_artwork_sorts_by_size_desc() {
  await resetAll();
  // 模拟页面侧 navigator.mediaSession.metadata — 3 张不同尺寸 artwork
  // 故意打乱顺序：小、中、大，验证排序后取最大
  setupChromeMock({
    pageMetadata: {
      title: '测试曲目',
      artist: '测试艺术家',
      album: '测试专辑',
      artwork: [
        { src: 'https://example.com/small.jpg', sizes: '128x128' },
        { src: 'https://example.com/large.jpg', sizes: '512x512' },
        { src: 'https://example.com/medium.jpg', sizes: '256x256' },
      ],
    },
    tabUrl: 'https://music.example.com/track/1',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(100);
    assert.ok(result, '应返回 metadata');
    assert.equal(result.title, '测试曲目');
    assert.equal(result.artist, '测试艺术家');
    assert.equal(result.album, '测试专辑');
    assert.equal(result.artworkUrl, 'https://example.com/large.jpg', '应取最大尺寸 512x512 的 artwork');
    assert.equal(result.source, 'music');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_artwork_sorts_by_size_desc');
}

// 测试 9b: captureMediaMetadata artwork 数组为空时 artworkUrl 为空字符串
async function test_captureMediaMetadata_empty_artwork_array() {
  await resetAll();
  setupChromeMock({
    pageMetadata: {
      title: '无封面曲目',
      artist: '某艺术家',
      album: '',
      artwork: [],
    },
    tabUrl: 'https://music.example.com/track/2',
  });
  try {
    const muse = await import(museJsPath);
    const result = await muse.captureMediaMetadata(101);
    assert.ok(result);
    assert.equal(result.artworkUrl, '', 'artwork 数组为空时 artworkUrl 应为空字符串');
  } finally {
    clearChromeMock();
  }
  console.log('[PASS] test_captureMediaMetadata_empty_artwork_array');
}

// ============================================================
// Phase 4.2 groupByMusic 纯函数测试（从 core/media-group.js 导入）
// ============================================================

const mediaGroupPath = pathToFileURL(pathResolve(__dirname, '..', 'core', 'media-group.js')).href;

// 测试 10: groupByMusic 按 artist 分组
async function test_groupByMusic_by_artist() {
  const { groupByMusic } = await import(mediaGroupPath);
  const items = [
    { id: 1, title: 'A', mediaType: 'music', mediaArtist: '周杰伦', createdAt: 1000 },
    { id: 2, title: 'B', mediaType: 'music', mediaArtist: '周杰伦', createdAt: 2000 },
    { id: 3, title: 'C', mediaType: 'music', mediaArtist: '周深', createdAt: 3000 },
  ];
  const groups = groupByMusic(items);
  assert.equal(groups.length, 2, '应分为 2 组');
  // 周杰伦组有 2 条，周深组有 1 条，按数量降序
  assert.equal(groups[0].label, '周杰伦');
  assert.equal(groups[0].items.length, 2);
  assert.equal(groups[1].label, '周深');
  assert.equal(groups[1].items.length, 1);
  // 组内按 created_at DESC
  assert.equal(groups[0].items[0].id, 2, '周杰伦组内 B(2000) 应在 A(1000) 前');
  assert.equal(groups[0].items[1].id, 1);
  console.log('[PASS] test_groupByMusic_by_artist');
}

// 测试 11: groupByMusic 无 artist 但有 mediaType 用 title
async function test_groupByMusic_no_artist_uses_title() {
  const { groupByMusic } = await import(mediaGroupPath);
  const items = [
    { id: 1, title: '某视频标题', mediaType: 'video', mediaArtist: '', createdAt: 1000 },
    { id: 2, title: '某视频标题', mediaType: 'video', mediaArtist: '', createdAt: 2000 },
    { id: 3, title: '另一个视频', mediaType: 'video', mediaArtist: '', createdAt: 3000 },
  ];
  const groups = groupByMusic(items);
  assert.equal(groups.length, 2, '应按 title 分为 2 组');
  assert.equal(groups[0].label, '某视频标题');
  assert.equal(groups[0].items.length, 2);
  assert.equal(groups[1].label, '另一个视频');
  console.log('[PASS] test_groupByMusic_no_artist_uses_title');
}

// 测试 12: groupByMusic 无 mediaType 归入"未关联媒体"组
async function test_groupByMusic_no_media_goes_to_none_group() {
  const { groupByMusic } = await import(mediaGroupPath);
  const items = [
    { id: 1, title: 'A', mediaType: 'music', mediaArtist: '周杰伦', createdAt: 1000 },
    { id: 2, title: 'B', mediaType: null, mediaArtist: null, createdAt: 2000 },
    { id: 3, title: 'C', mediaType: null, mediaArtist: null, createdAt: 3000 },
  ];
  const groups = groupByMusic(items);
  assert.equal(groups.length, 2, '应分为 2 组');
  // 周杰伦组在前，未关联媒体组在后（即使未关联有 2 条）
  assert.equal(groups[0].label, '周杰伦');
  assert.equal(groups[0].isNone, false);
  assert.equal(groups[1].label, '(未关联媒体)');
  assert.equal(groups[1].isNone, true);
  assert.equal(groups[1].items.length, 2);
  console.log('[PASS] test_groupByMusic_no_media_goes_to_none_group');
}

// 测试 13: groupByMusic "未关联媒体"组始终在最后（即使数量最多）
async function test_groupByMusic_none_group_always_last() {
  const { groupByMusic } = await import(mediaGroupPath);
  const items = [
    { id: 1, title: 'A', mediaType: 'music', mediaArtist: '周杰伦', createdAt: 1000 },
    { id: 2, title: 'B', mediaType: null, mediaArtist: null, createdAt: 2000 },
    { id: 3, title: 'C', mediaType: null, mediaArtist: null, createdAt: 3000 },
    { id: 4, title: 'D', mediaType: null, mediaArtist: null, createdAt: 4000 },
    { id: 5, title: 'E', mediaType: null, mediaArtist: null, createdAt: 5000 },
  ];
  const groups = groupByMusic(items);
  assert.equal(groups.length, 2);
  // 未关联媒体有 4 条，周杰伦 1 条，但周杰伦组在前
  assert.equal(groups[0].label, '周杰伦');
  assert.equal(groups[0].items.length, 1);
  assert.equal(groups[1].label, '(未关联媒体)');
  assert.equal(groups[1].items.length, 4);
  console.log('[PASS] test_groupByMusic_none_group_always_last');
}

// 测试 14: groupByMusic 空数组返回空数组
async function test_groupByMusic_empty_array() {
  const { groupByMusic } = await import(mediaGroupPath);
  const groups = groupByMusic([]);
  assert.equal(groups.length, 0);
  console.log('[PASS] test_groupByMusic_empty_array');
}

// 测试 15: groupByMusic 保留 artworkUrl 用于组头部渲染
async function test_groupByMusic_preserves_artwork_url() {
  const { groupByMusic } = await import(mediaGroupPath);
  const items = [
    { id: 1, title: 'A', mediaType: 'music', mediaArtist: '周杰伦', mediaArtworkUrl: 'https://example.com/art.jpg', createdAt: 1000 },
  ];
  const groups = groupByMusic(items);
  assert.equal(groups[0].artworkUrl, 'https://example.com/art.jpg');
  assert.equal(groups[0].mediaType, 'music');
  console.log('[PASS] test_groupByMusic_preserves_artwork_url');
}

// 测试 16: groupByMusic artist 优先于 title（即使 title 不同也按 artist 聚合）
async function test_groupByMusic_artist_priority_over_title() {
  const { groupByMusic } = await import(mediaGroupPath);
  const items = [
    { id: 1, title: '夜曲', mediaType: 'music', mediaArtist: '周杰伦', createdAt: 1000 },
    { id: 2, title: '青花瓷', mediaType: 'music', mediaArtist: '周杰伦', createdAt: 2000 },
  ];
  const groups = groupByMusic(items);
  assert.equal(groups.length, 1, '相同 artist 不同 title 应聚合为 1 组');
  assert.equal(groups[0].label, '周杰伦');
  assert.equal(groups[0].items.length, 2);
  console.log('[PASS] test_groupByMusic_artist_priority_over_title');
}

// 主测试运行器
async function main() {
  const tests = [
    test_saveInspiration_writes_media_fields,
    test_saveInspiration_null_media_fields,
    test_getInspirations_returns_media_fields,
    test_captureMediaMetadata_youtube_to_video,
    test_captureMediaMetadata_ximalaya_to_podcast,
    test_captureMediaMetadata_default_to_music,
    test_captureMediaMetadata_no_metadata_returns_null,
    test_captureMediaMetadata_empty_title_returns_null,
    test_captureMediaMetadata_no_tabid_returns_null,
    test_captureMediaMetadata_artwork_sorts_by_size_desc,
    test_captureMediaMetadata_empty_artwork_array,
    test_groupByMusic_by_artist,
    test_groupByMusic_no_artist_uses_title,
    test_groupByMusic_no_media_goes_to_none_group,
    test_groupByMusic_none_group_always_last,
    test_groupByMusic_empty_array,
    test_groupByMusic_preserves_artwork_url,
    test_groupByMusic_artist_priority_over_title,
  ];

  let passed = 0;
  let failed = 0;
  for (const t of tests) {
    try {
      await t();
      passed++;
    } catch (err) {
      failed++;
      console.error(`[FAIL] ${t.name}: ${err.message}`);
      console.error(err.stack);
    }
  }
  console.log(`\n${passed}/${tests.length} PASSED, ${failed} FAILED`);
  if (failed > 0) process.exit(1);
}

main().catch((err) => {
  console.error('测试运行失败:', err);
  process.exit(1);
});
