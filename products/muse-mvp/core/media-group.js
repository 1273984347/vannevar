// core/media-group.js — Phase 4.2 按音乐/媒体分组的纯函数逻辑
// 从 dashboard.js 提取，便于单元测试（不依赖 DOM）
//
// groupByMusic(items)：
//   - group key = artist（优先），无 artist 但有 mediaType 用 title，都无则归入"(未关联媒体)"
//   - 组内按 created_at DESC
//   - 组间按灵感数量降序，"未关联媒体"组始终在最后
//
// 返回结构：[{ key, label, mediaType, artworkUrl, items, isNone }]

const NONE_KEY = '(未关联媒体)';

export function groupByMusic(items) {
  const map = new Map();
  for (const item of items) {
    const artist = (item.mediaArtist || '').trim();
    const title = (item.mediaType && item.title) ? String(item.title).trim() : '';
    let key;
    if (artist) {
      key = artist;
    } else if (item.mediaType) {
      key = title || '(未命名媒体)';
    } else {
      key = NONE_KEY;
    }
    if (!map.has(key)) {
      map.set(key, {
        items: [],
        mediaType: item.mediaType || null,
        artist: artist || '',
        artworkUrl: item.mediaArtworkUrl || '',
      });
    }
    map.get(key).items.push(item);
  }
  const groups = [...map.entries()].map(([key, data]) => {
    const isNone = key === NONE_KEY;
    const displayArtist = data.artist || (isNone ? '' : key);
    return {
      key,
      label: isNone ? NONE_KEY : (displayArtist || '(未命名媒体)'),
      mediaType: data.mediaType,
      artworkUrl: data.artworkUrl,
      items: data.items.slice().sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0)),
      isNone,
    };
  });
  // 有媒体的组按数量降序，"未关联媒体"组始终在最后
  groups.sort((a, b) => {
    if (a.isNone !== b.isNone) return a.isNone ? 1 : -1;
    return b.items.length - a.items.length;
  });
  return groups;
}
