// 오프라인용 앱 껍데기 캐시. OpenAI 요청은 절대 캐시하지 않는다.
const CACHE = "vconcept-__BUILD__";
const SHELL = ["./", "index.html", "css/app.css", "js/app.js", "js/db.js", "js/api.js", "js/images.js", "js/pricing.js", "js/sync.js", "js/prompt.js",
  "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || e.request.method !== "GET") return;   // api.openai.com 등은 그대로 통과
  // 네트워크 우선 → 실패하면 캐시 (새 버전이 바로 반영되도록)
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return res;
  }).catch(() => caches.match(e.request)));
});
