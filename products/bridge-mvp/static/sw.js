// Bridge MVP Service Worker — 简化版 PWA 离线缓存
// 缓存基础静态资源，离线时回退到缓存
// B-SEC-05: CACHE_NAME 与 manifest/main.py 版本一致，激活时清理旧 cache
// Phase 7.3 (Lighthouse P1): 扩充 precache 列表 — 重复访问时静态资源从 SW 缓存读取
//   原 4 项 precache 改为 7 项，覆盖 stats.css / story.css / privacy-policy.html
//   需 Basic Auth 的页面（/ /admin/*）不预缓存（SW install 无凭据会 401）
const CACHE_NAME = 'bridge-v0.2.0';
const STATIC_ASSETS = [
  '/static/style.css',
  '/static/stats.css',
  '/static/story.css',
  '/static/manifest.json',
  '/static/privacy-policy.html',
  '/landing',
  '/'
];

// 安装：预缓存核心静态资源
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // addAll 失败时整个安装失败，用 add 逐个容忍
      return Promise.all(
        STATIC_ASSETS.map((url) =>
          cache.add(url).catch(() => {})
        )
      );
    })
  );
  self.skipWaiting();
});

// 激活：清理旧缓存
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// 抓取：GET 请求优先缓存，网络失败回退
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  // SSE 流式响应不缓存
  if (req.headers.get('accept') && req.headers.get('accept').includes('text/event-stream')) {
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((response) => {
        // 成功的静态资源响应缓存一份
        if (response.ok && req.url.includes('/static/')) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
        }
        return response;
      }).catch(() => {
        // 离线回退：导航请求返回缓存的公开引导页
        // P2-4: 优先回退到 /landing（公开页），避免落到需 Basic Auth 的 /
        // P2-6: 检查 caches.match 返回值，为 undefined 时返回 503
        if (req.mode === 'navigate') {
          return caches.match('/landing').then(r => r || caches.match('/')).then(r => r || new Response('离线', {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}}));
        }
        return new Response('离线状态，内容不可用', {
          status: 503,
          statusText: 'Offline',
          headers: { 'Content-Type': 'text/plain; charset=utf-8' }
        });
      });
    })
  );
});
