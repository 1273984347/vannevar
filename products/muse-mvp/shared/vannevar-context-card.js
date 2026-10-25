// vannevar-context-card.js — Vannevar Context Card v1
//
// 统一的上下文卡片格式，让 Vannevar 产品族（Focus / Muse / Bridge）的数据
// 能够互相导入导出，实现"数据协同已落地"的可演示闭环。
//
// 卡片格式（v1）：
//   {
//     "schema": "vannevar-context-card/v1",
//     "type": "focus-snapshot" | "muse-inspiration" | "bridge-handoff",
//     "app": "focus" | "muse" | "bridge",
//     "exportedAt": "2026-07-08T10:00:00.000Z",
//     "data": { ... }
//   }
//
// 三类 data 载荷：
//   focus-snapshot:    { title, tabs: [{title, url}], tags: [], createdAt }
//   muse-inspiration:  { title, url, selectedText, note, mood, createdAt }
//   bridge-handoff:    { projectName, summary, outgoing, incoming, tasks: [], createdAt }
//
// 文件后缀约定：.vcf（Vannevar Context File，非 vCard）

/**
 * @typedef {Object} FocusSnapshotTab
 * @property {string} title 标签页标题
 * @property {string} url   标签页 URL
 */

/**
 * @typedef {Object} FocusSnapshotData
 * @property {string}                title     快照标题
 * @property {FocusSnapshotTab[]}    tabs      标签页列表
 * @property {string[]}              tags      场景标签（可空）
 * @property {number|string}         createdAt 创建时间（时间戳或 ISO 字符串）
 */

/**
 * @typedef {Object} MuseInspirationData
 * @property {string}                title        灵感标题
 * @property {string}                url          来源 URL
 * @property {string}                selectedText 选中片段
 * @property {string}                note         备注
 * @property {string}                mood         氛围标签
 * @property {number|string}         createdAt    创建时间
 */

/**
 * @typedef {Object} BridgeHandoffTask
 * @property {string} title 任务标题
 * @property {string} owner_name 负责人
 * @property {string} status 任务状态
 */

/**
 * @typedef {Object} BridgeHandoffData
 * @property {string}              projectName  项目名称
 * @property {string}              summary      交接摘要
 * @property {string}              outgoing     老志愿者
 * @property {string}              incoming     新志愿者
 * @property {BridgeHandoffTask[]} tasks        任务列表
 * @property {number|string}       createdAt    创建时间
 */

/**
 * @typedef {Object} VannevarContextCard
 * @property {string} schema      固定为 "vannevar-context-card/v1"
 * @property {('focus-snapshot'|'muse-inspiration'|'bridge-handoff')} type 卡片类型
 * @property {('focus'|'muse'|'bridge')} app                             来源产品
 * @property {string} exportedAt   导出时间（ISO 8601 字符串）
 * @property {FocusSnapshotData|MuseInspirationData|BridgeHandoffData} data 载荷
 */

export const SCHEMA_VERSION = 'vannevar-context-card/v1';
export const FILE_EXTENSION = '.vcf';

/** type → app 的允许映射 */
const TYPE_APP_MAP = {
  'focus-snapshot':   'focus',
  'muse-inspiration': 'muse',
  'bridge-handoff':   'bridge',
};

/** 每种 type 必填的 data 字段（值为非 undefined 即视为存在） */
const REQUIRED_FIELDS = {
  'focus-snapshot':   ['title', 'tabs', 'createdAt'],
  'muse-inspiration': ['title', 'createdAt'],
  'bridge-handoff':   ['projectName', 'summary', 'createdAt'],
};

/**
 * 创建一张 Context Card。
 *
 * @param {('focus-snapshot'|'muse-inspiration'|'bridge-handoff')} type 卡片类型
 * @param {('focus'|'muse'|'bridge')} app                             来源产品
 * @param {Object}                   data                             载荷数据
 * @returns {VannevarContextCard} 创建好的卡片对象
 * @throws {TypeError} 当 type / app 非法或与 type 不匹配时抛出
 */
export function createCard(type, app, data) {
  if (!Object.prototype.hasOwnProperty.call(TYPE_APP_MAP, type)) {
    throw new TypeError(`未知的 Context Card 类型：${type}`);
  }
  if (TYPE_APP_MAP[type] !== app) {
    throw new TypeError(`类型 "${type}" 必须搭配 app "${TYPE_APP_MAP[type]}"，收到 "${app}"`);
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new TypeError('data 必须是对象');
  }
  return {
    schema: SCHEMA_VERSION,
    type,
    app,
    exportedAt: new Date().toISOString(),
    data,
  };
}

/**
 * 校验 Context Card 格式是否合法。
 *
 * @param {*} card 待校验的对象
 * @returns {{ok: true, card: VannevarContextCard} | {ok: false, error: string}}
 *          校验结果，ok=true 时附带归一化后的 card
 */
export function validateCard(card) {
  if (!card || typeof card !== 'object' || Array.isArray(card)) {
    return { ok: false, error: '卡片必须是对象' };
  }
  if (card.schema !== SCHEMA_VERSION) {
    return { ok: false, error: `schema 必须为 ${SCHEMA_VERSION}，收到 ${card.schema}` };
  }
  if (!Object.prototype.hasOwnProperty.call(TYPE_APP_MAP, card.type)) {
    return { ok: false, error: `未知的 type：${card.type}` };
  }
  if (card.app !== TYPE_APP_MAP[card.type]) {
    return { ok: false, error: `type "${card.type}" 应搭配 app "${TYPE_APP_MAP[card.type]}"，收到 "${card.app}"` };
  }
  if (typeof card.exportedAt !== 'string' || !card.exportedAt) {
    return { ok: false, error: 'exportedAt 必须为非空字符串' };
  }
  if (!card.data || typeof card.data !== 'object' || Array.isArray(card.data)) {
    return { ok: false, error: 'data 必须是对象' };
  }
  const required = REQUIRED_FIELDS[card.type];
  for (const field of required) {
    if (!Object.prototype.hasOwnProperty.call(card.data, field) || card.data[field] === undefined) {
      return { ok: false, error: `data 缺少必填字段：${field}` };
    }
  }
  return { ok: true, card };
}

/**
 * 将卡片序列化为 JSON 字符串（2 空格缩进，便于跨产品阅读）。
 *
 * @param {VannevarContextCard} card 待序列化的卡片
 * @returns {string} JSON 字符串
 * @throws {TypeError} 当卡片格式非法时抛出
 */
export function serializeCard(card) {
  const result = validateCard(card);
  if (!result.ok) {
    throw new TypeError('序列化失败：' + result.error);
  }
  return JSON.stringify(result.card, null, 2);
}

/**
 * 反序列化 JSON 字符串为 Context Card，并完成格式校验。
 *
 * @param {string} jsonStr JSON 字符串
 * @returns {VannevarContextCard} 解析并校验通过的卡片对象
 * @throws {SyntaxError} 当 JSON 解析失败时抛出
 * @throws {TypeError}   当卡片格式不合法时抛出
 */
export function deserializeCard(jsonStr) {
  if (typeof jsonStr !== 'string') {
    throw new TypeError('反序列化输入必须是字符串');
  }
  const parsed = JSON.parse(jsonStr);
  const result = validateCard(parsed);
  if (!result.ok) {
    throw new TypeError('反序列化校验失败：' + result.error);
  }
  return result.card;
}
