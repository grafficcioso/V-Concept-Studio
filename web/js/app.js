// V-Concept Studio 웹앱 (폰·PC 브라우저). 데이터는 이 기기 브라우저(IndexedDB)에만 저장된다.
import { db } from "./db.js";
import { ApiError, editImage, listImageModels } from "./api.js";
import { Catalog } from "./pricing.js";
import { PaintLayer, blankLike, copyCanvas, decode, isolatePart, layerHasMarks, overlayCanvas, prepareForApi, saveOrShare, sizeOf,
  thumbnail, tintLayer } from "./images.js";
import { composePrompt, joinPrompt, splitPrompt } from "./prompt.js";
import { clearToken, hasValidToken, requestToken, summary as syncSummary, syncOnce } from "./sync.js";

export const WEB_VERSION = "0.4.0";

const FUNCTIONS = [
  { key: "front_view", label: "정면화", desc: "원화를 정면 머리 일러스트로 다시 그립니다." },
  { key: "parts_separation", label: "파트 분할", desc: "민머리·가발·액세서리 분리 시트를 만듭니다 (한 장의 시트)." },
  { key: "four_views", label: "사면도", desc: "정면/좌/우/후면 4뷰 시트를 만듭니다. 뷰 간 일관성은 완벽하지 않을 수 있습니다." },
  { key: "iris_restore", label: "홍채 복원", desc: "눈·홍채를 크게 확대한 선명한 참조 이미지를 만듭니다." },
  { key: "upscale", label: "업스케일", desc: "디자인을 바꾸지 않고 선명하게 다시 그립니다." },
];
// 파트 사면도 (웹 v0.4.0, PC 와 같은 기능 키·프롬프트)
const PART_TYPES = [["part_views_hair", "헤어 (가발)"], ["part_views_head", "헤드 (민머리)"], ["part_views_accessory", "액세서리"],
  ["part_views_other", "기타 파트"]];
const SET_KEY = "set_head_hair";
const LABELS = { ...Object.fromEntries(FUNCTIONS.map((f) => [f.key, f.label])), retouch: "부분 수정",
  part_views_hair: "헤어 사면도", part_views_head: "헤드 사면도", part_views_accessory: "액세서리 사면도", part_views_other: "파트 사면도" };
const PROMPT_KEYS = [...FUNCTIONS.map((f) => f.key), "retouch", ...PART_TYPES.map(([k]) => k)];
const QUICK = [
  ["머리카락 가닥 줄이기", "Reduce the thin, scattered hair strands in this area; merge them into fewer, larger, clean hair clumps."],
  ["원화 느낌 더 살리기", "Make this area look closer to the original illustration's art style, linework, coloring, and shading."],
  ["원화 색감 맞추기", "Match the colors and saturation of this area to the original illustration."],
  ["선 깔끔하게", "Clean up the linework in this area: crisp, consistent line weight, no sketchy or broken lines."],
  ["가려진 부분 복원", "Reconstruct the hidden or cut-off part in this area naturally, consistent with the design."],
  ["형태를 원화처럼", "Reshape this area to match the shape and silhouette in the original illustration."],
];
const AREA_MARKUP = "A separate annotation layer marks the area to change with semi-transparent red paint. Apply the requested change only inside the red-marked area and blend it naturally with the surrounding image.";
const AREA_WHOLE = "Apply the requested change to the whole image.";
const PART_REF = (n) => `The ${n} image is the original full character illustration, given only as a reference for scale, proportions, and art style. Do not draw any other part from it.`;
const HEAD_SHEET_REF = "The SECOND image is the four-view sheet of the bald head that this wig must fit. Use exactly the same view order, panel layout, panel positions, and head scale as that sheet, so each wig view can be laid directly over the matching head view: match the head width, skull height and shape, hairline, ear position, eye line, and jaw/chin line. Do not draw the face, skin, or head itself; draw only the wig where it would sit on that head.";
const REF_ORIGINAL = "The SECOND image is the original source illustration of the same character. Use it as the reference for the character's original look, art style, linework, colors, and hair shapes.";

const $ = (id) => document.getElementById(id);
const state = { fn: "front_view", inputId: null, resultId: null, detailId: null, busy: null, catalog: new Catalog(), defaults: {}, urls: [],
  config: {}, syncing: false, syncTimer: null };

// ------------------------------------------------------------------ 설정 저장
const KEY_STORE = "vc.apiKey";
const SET_STORE = "vc.settings";
const DEFAULT_SETTINGS = { model: "", quality: "auto", size: "auto", background: "auto", demo: false, credit: 0, creditSince: "", budget: 0, confirm: true,
  syncAuto: true };
function settings() { try { return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SET_STORE) || "{}") }; } catch { return { ...DEFAULT_SETTINGS }; } }
function saveSettings(patch) { localStorage.setItem(SET_STORE, JSON.stringify({ ...settings(), ...patch })); }
const apiKey = () => localStorage.getItem(KEY_STORE) || "";

function promptFor(key) { return localStorage.getItem("vc.prompt." + key) || state.defaults[key] || ""; }

// ------------------------------------------------------------------ UI 도우미
function toast(msg, ms = 2600) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, ms);
}
function modal(title, text, buttons = [["확인", true]]) {
  return new Promise((resolve) => {
    $("modalTitle").textContent = title; $("modalText").textContent = text;
    const box = $("modalBtns"); box.innerHTML = "";
    buttons.forEach(([label, value, cls]) => {
      const b = document.createElement("button");
      b.className = "btn " + (cls || (value === true ? "primary" : ""));
      b.textContent = label;
      b.onclick = () => { $("modal").hidden = true; resolve(value); };
      box.appendChild(b);
    });
    $("modal").hidden = false;
  });
}
function objUrl(blob) { const u = URL.createObjectURL(blob); state.urls.push(u); return u; }
function go(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.go === view));
  if (view === "history") renderHistory();
  if (view === "settings") renderSettings();
  window.scrollTo(0, 0);
}
const monthStart = () => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString(); };
const dayStart = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.toISOString(); };

// ------------------------------------------------------------------ 시작
async function boot() {
  $("ver").textContent = "웹 v" + WEB_VERSION;
  try {
    const res = await fetch("data/models.json", { cache: "no-cache" });
    state.catalog = new Catalog(await res.json());
  } catch { toast("요금표를 불러오지 못했습니다."); }
  try { state.config = await (await fetch("data/config.json", { cache: "no-cache" })).json(); } catch { state.config = {}; }
  await Promise.all(PROMPT_KEYS.map(async (k) => {
    try { state.defaults[k] = (await (await fetch(`data/prompts/${k}.md`, { cache: "no-cache" })).text()).trim(); } catch { state.defaults[k] = ""; }
  }));
  buildFunctionChips(); buildOptions(); buildQuick(); bindEvents();
  refreshStatus();
  const last = (await db.listImages()).find((r) => r.kind === "original");
  if (last) await setInput(last.id);
  updateEstimate();
  refreshSyncStatus(); refreshPcStatus();
  if (hasValidToken()) doSync(false);
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
}

function refreshStatus() {
  const s = settings(); const el = $("apiStatus");
  el.textContent = s.demo ? "● 데모 모드" : apiKey() ? "● API 키 등록됨" : "○ API 키 없음";
  el.style.background = s.demo ? "rgba(255,190,60,.45)" : apiKey() ? "rgba(60,200,120,.4)" : "rgba(255,255,255,.22)";
}

function buildFunctionChips() {
  const box = $("fnChips"); box.innerHTML = "";
  FUNCTIONS.forEach((f) => {
    const b = document.createElement("button");
    b.className = "chip" + (f.key === state.fn ? " on" : ""); b.textContent = f.label; b.dataset.fn = f.key;
    b.onclick = () => { state.fn = f.key; buildFunctionChips(); updateEstimate(); };
    box.appendChild(b);
  });
  $("fnDesc").textContent = FUNCTIONS.find((f) => f.key === state.fn).desc;
}

function fillSelect(sel, values, wanted) {
  sel.innerHTML = "";
  values.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o); });
  sel.value = values.includes(wanted) ? wanted : values[0];
}

function buildOptions() {
  const s = settings(); const ids = state.catalog.ids();
  const ms = $("modelSel"); ms.innerHTML = "";
  ids.forEach((id) => { const o = document.createElement("option"); o.value = id; o.textContent = state.catalog.get(id).label || id; ms.appendChild(o); });
  ms.value = ids.includes(s.model) ? s.model : ids[0];
  onModelChange();
}

function onModelChange() {
  const s = settings(); const m = state.catalog.get($("modelSel").value);
  fillSelect($("qualitySel"), m.qualities, s.quality);
  fillSelect($("sizeSel"), m.sizes, s.size);
  fillSelect($("bgSel"), m.backgrounds, s.background);
  $("modelNote").textContent = (m.deprecated ? "⚠ 구형 모델입니다. " : "") + (m.backgrounds.includes("transparent") ? "투명 배경은 모델에 따라 불완전할 수 있습니다." : "");
  saveOpts();
}

function opts() { return { model: $("modelSel").value, quality: $("qualitySel").value, size: $("sizeSel").value, background: $("bgSel").value }; }
function saveOpts() {
  const o = opts(); saveSettings(o);
  $("optsSummary").textContent = `${state.catalog.get(o.model).label || o.model} · ${o.quality} · ${o.size} · ${o.background}`;
  updateEstimate();
}

async function updateEstimate(nInputs = 1) {
  const s = settings(); const o = opts();
  if (s.demo) { $("estimate").innerHTML = `데모 모드 — 비용 없음 <span class="note">API 를 부르지 않고 흑백 가짜 결과를 만듭니다.</span>`; return null; }
  const est = state.catalog.estimate(o.model, o.quality, o.size, nInputs, await db.listUsage());
  $("estimate").innerHTML = "";
  $("estimate").append(est.text);
  const n = document.createElement("span"); n.className = "note"; n.textContent = est.note; $("estimate").append(n);
  return est;
}

function buildQuick() {
  const box = $("rtQuick"); box.innerHTML = "";
  QUICK.forEach(([label, text]) => {
    const b = document.createElement("button"); b.className = "chip"; b.textContent = label;
    b.onclick = () => { const t = $("rtRequest"); t.value = (t.value.trim() ? t.value.trim() + "\n" : "") + text; };
    box.appendChild(b);
  });
}

// ------------------------------------------------------------------ 입력
async function addOriginal(blob, name) {
  if (!/^image\/(png|jpeg|webp)$/.test(blob.type)) { await modal("가져오기", "PNG · JPG · WEBP 이미지만 쓸 수 있습니다."); return; }
  let size;
  try { size = await sizeOf(blob); } catch { await modal("가져오기", "이미지를 열 수 없습니다."); return; }
  const id = await db.addImage({ kind: "original", blob, thumb: await thumbnail(blob), name: name || "원본", width: size[0], height: size[1] });
  await setInput(id);
  toast("입력 이미지로 지정했습니다.");
}

async function setInput(id) {
  const rec = await db.getImage(id);
  state.inputId = rec ? rec.id : null;
  $("inputImg").hidden = !rec; $("inputEmpty").hidden = !!rec;
  if (rec) $("inputImg").src = objUrl(rec.blob);
}

async function pasteImage() {
  try {
    const items = await navigator.clipboard.read();
    for (const it of items) {
      const type = it.types.find((t) => t.startsWith("image/"));
      if (type) { const b = await it.getType(type); return addOriginal(type === "image/png" ? b : new Blob([b], { type }), "붙여넣기"); }
    }
    toast("클립보드에 이미지가 없습니다.");
  } catch { toast("이 브라우저에서는 붙여넣기 버튼을 쓸 수 없습니다. [사진 선택]을 써 주세요."); }
}

// ------------------------------------------------------------------ 생성 공통
async function rootOriginal(id) {
  const seen = new Set(); let rec = await db.getImage(id);
  while (rec && !seen.has(rec.id)) { seen.add(rec.id); if (rec.kind === "original") return rec; rec = await db.getImage(rec.sourceId); }
  return null;
}

async function confirmCost(fnLabel, nInputs, ask = true, note = "") {
  const s = settings(); const o = opts();
  if (s.demo) return true;
  const problems = state.catalog.validate(o.model, o.quality, o.size, o.background);
  if (problems.length) { await modal("지원되지 않는 옵션", problems.join("\n")); return false; }
  const usage = await db.listUsage();
  const est = state.catalog.estimate(o.model, o.quality, o.size, nInputs, usage);
  const month = usage.filter((u) => u.created >= monthStart() && u.status === "ok").reduce((a, u) => a + (u.cost || 0), 0);
  const over = s.budget > 0 && month + (est.high || 0) > s.budget;
  if (!over && (!ask || !s.confirm)) return true;
  let text = `[${fnLabel}] 요청을 OpenAI 로 보냅니다 (유료).\n모델 ${o.model} · 품질 ${o.quality} · 크기 ${o.size} · 배경 ${o.background}\n참조 이미지 ${nInputs}장${note}\n\n${est.text}\n${est.note}`;
  if (s.budget > 0) text += `\n\n이번 달 앱 추정 사용액 $${month.toFixed(3)} / 소프트 예산 $${(+s.budget).toFixed(2)}` + (over ? "\n⚠ 예산을 넘습니다." : "");
  return modal("유료 생성 요청 확인", text, [["취소", false], ["생성", true]]);
}

async function demoResult(blob) {
  const bmp = await decode(blob);
  const c = document.createElement("canvas"); c.width = Math.min(1024, bmp.width); c.height = Math.round(c.width * bmp.height / bmp.width);
  const g = c.getContext("2d"); g.filter = "grayscale(1)"; g.drawImage(bmp, 0, 0, c.width, c.height); g.filter = "none";
  g.fillStyle = "#333"; g.fillRect(0, 0, c.width, 34); g.fillStyle = "#ffd84d"; g.font = "16px sans-serif";
  g.fillText("DEMO - API 호출 없음 (AI 결과 아님)", 10, 23);
  await new Promise((r) => setTimeout(r, 600));
  return new Promise((res) => c.toBlob((b) => res({ blob: b, usage: null, requestId: "demo", elapsed: 0.6 }), "image/png"));
}

/** 공통 생성 흐름. job: { fn, prompt, inputIds, markup, sourceId, extra } */
async function runJob(job) {
  if (state.busy) { toast("이미 생성 중입니다."); return null; }
  const s = settings();
  if (!s.demo && !apiKey()) { await modal("API 키 필요", "[설정]에서 OpenAI API 키를 먼저 넣어 주세요."); go("settings"); return null; }
  if (!(await confirmCost(LABELS[job.fn] || job.fn, job.inputIds.length, !job.noConfirm, job.note || ""))) return null;
  let outId = null;
  const o = opts();
  const ctrl = new AbortController(); state.busy = ctrl;
  setBusy(true, LABELS[job.fn]);
  let wake = null;
  try { wake = await navigator.wakeLock?.request("screen"); } catch { /* 지원 안 함 */ }
  const base = { function: job.fn, model: o.model, quality: o.quality, size: o.size, n_inputs: job.inputIds.length };
  try {
    const recs = await Promise.all(job.inputIds.map((id) => db.getImage(id)));
    let result;
    if (s.demo) result = await demoResult(recs[0].blob);
    else {
      const prepared = await Promise.all(recs.map((r) => prepareForApi(r.blob)));
      result = await editImage({ key: apiKey(), model: o.model, prompt: job.prompt, images: prepared.map((p) => p.blob),
        markup: job.markup || null, quality: o.quality, size: o.size, background: o.background, signal: ctrl.signal });
    }
    const cost = s.demo ? 0 : state.catalog.actualCost(o.model, result.usage);
    const id = await db.addImage({ kind: "output", blob: result.blob, thumb: await thumbnail(result.blob), function: job.fn,
      sourceId: job.sourceId, prompt: job.prompt, ...o, requestId: result.requestId, usage: result.usage, cost, extra: job.extra || null });
    const det = (result.usage || {}).input_tokens_details || {};
    await db.addUsage({ ...base, status: s.demo ? "demo" : "ok", cost, request_id: result.requestId, output_tokens: result.usage?.output_tokens ?? null,
      input_image_tokens: det.image_tokens ?? null, input_text_tokens: det.text_tokens ?? null,
      message: s.demo ? "데모" : result.usage ? null : "사용량 정보 없음" });
    outId = id;
    await showResult(id);
    toast(`완료 (${Math.round(result.elapsed || 0)}초)`);
    scheduleSync();
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError("unknown", `예상하지 못한 오류: ${e && e.message}`, { billingUncertain: true });
    await db.addUsage({ ...base, status: err.billingUncertain && !s.demo ? "unknown" : "failed", cost: null, request_id: err.requestId, message: `[${err.kind}] ${err.message}`.slice(0, 400) });
    await modal("생성 실패", err.message + (err.requestId ? `\n\n요청 ID: ${err.requestId}` : ""));
  } finally {
    state.busy = null; setBusy(false);
    try { await wake?.release(); } catch { /* 무시 */ }
    updateEstimate(); refreshMoney();
  }
  return outId;
}

function setBusy(on, label = "") {
  $("genBtn").disabled = on; $("rtSend").disabled = on; $("pvSend").disabled = on; $("progress").hidden = !on;
  clearInterval(setBusy._t);
  if (on) {
    const t0 = Date.now();
    const tick = () => { $("progressText").textContent = `[${label}] 생성 중… ${Math.round((Date.now() - t0) / 1000)}초 (보통 30초~2분)`; };
    tick(); setBusy._t = setInterval(tick, 1000);
  }
}

async function generate() {
  if (!state.inputId) { await modal("입력 이미지", "먼저 사진을 골라 주세요."); return; }
  await runJob({ fn: state.fn, prompt: composePrompt(promptFor(state.fn)), inputIds: [state.inputId], sourceId: state.inputId });
}

async function showResult(id) {
  const rec = await db.getImage(id); if (!rec) return;
  state.resultId = id;
  $("resultCard").hidden = false;
  $("resultImg").src = objUrl(rec.blob);
  $("resultCard").querySelector("[data-act=overlay]").hidden = !(rec.extra && rec.extra.head_sheet_id);
  $("resultMeta").textContent = `${LABELS[rec.function] || ""} · ${rec.model || ""} · ${rec.cost == null ? "비용 계산 불가" : "$" + rec.cost.toFixed(4)}${rec.favorite ? " · ★" : ""}`;
  $("resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ 결과·기록 작업
async function act(action, id) {
  const rec = await db.getImage(id); if (!rec) return;
  if (action === "save") {
    const r = await saveOrShare(rec.blob, `${LABELS[rec.function] || "image"}_${id}.png`);
    if (r === "downloaded") toast("다운로드했습니다.");
  } else if (action === "fav") {
    await db.updateImage(id, { favorite: rec.favorite ? 0 : 1 });
    toast(rec.favorite ? "즐겨찾기 해제" : "★ 즐겨찾기");
    scheduleSync();
    if (id === state.resultId) showResult(id);
    if (!$("detail").hidden) openDetail(id);
  } else if (action === "use") {
    await setInput(id); $("detail").hidden = true; go("create"); toast("입력으로 지정했습니다.");
  } else if (action === "retouch") {
    $("detail").hidden = true; openRetouch(id);
  } else if (action === "parts") {
    $("detail").hidden = true; openPartViews(id);
  } else if (action === "overlay") {
    $("detail").hidden = true; openOverlay(id);
  } else if (action === "delete") {
    if (await modal("삭제", "이 이미지를 이 기기에서 삭제할까요?", [["취소", false], ["삭제", true, "danger"]])) {
      await db.deleteImage(id);
      if (state.inputId === id) await setInput(null);
      if (state.resultId === id) { state.resultId = null; $("resultCard").hidden = true; }
      $("detail").hidden = true; renderHistory(); scheduleSync();
    }
  } else if (action === "meta") {
    await db.updateImage(id, { name: $("detailName").value.trim(), tags: $("detailTags").value.trim() });
    toast("저장했습니다."); renderHistory(); scheduleSync();
  }
}

async function renderHistory() {
  state.urls.forEach((u) => URL.revokeObjectURL(u)); state.urls = [];
  if (state.inputId) setInput(state.inputId);
  if (state.resultId) { const r = await db.getImage(state.resultId); if (r) $("resultImg").src = objUrl(r.blob); }
  const f = document.querySelector("#histFilter .on").dataset.f;
  const q = $("histSearch").value.trim().toLowerCase();
  const rows = (await db.listImages()).filter((r) =>
    (f === "fav" ? r.favorite : r.kind === f) && (!q || `${r.name} ${r.tags} ${r.category || ""}`.toLowerCase().includes(q)));
  const grid = $("histGrid"); grid.innerHTML = "";
  $("histEmpty").hidden = rows.length > 0;
  rows.forEach((r) => {
    const d = document.createElement("div"); d.className = "thumb"; d.dataset.id = r.id;
    const img = document.createElement("img"); img.loading = "lazy"; img.src = objUrl(r.thumb); d.appendChild(img);
    const cap = document.createElement("div"); cap.className = "cap";
    cap.textContent = r.name || `${r.kind === "asset" ? (r.category || "에셋") : (LABELS[r.function] || "원본")} ${r.created.slice(5, 16).replace("T", " ")}`; d.appendChild(cap);
    if (r.favorite) { const st = document.createElement("div"); st.className = "star"; st.textContent = "★"; d.appendChild(st); }
    d.onclick = () => openDetail(r.id);
    grid.appendChild(d);
  });
}

async function openDetail(id) {
  const rec = await db.getImage(id); if (!rec) return;
  state.detailId = id;
  $("detailTitle").textContent = (rec.kind === "asset" ? `에셋 · ${rec.category || ""}` : (LABELS[rec.function] || "원본")) + (rec.favorite ? " ★" : "");
  $("detailImg").src = objUrl(rec.blob);
  $("detailName").value = rec.name || ""; $("detailTags").value = rec.tags || "";
  $("detailMeta").textContent = [rec.created.replace("T", " ").slice(0, 19), rec.project ? `프로젝트 ${rec.project}` : null, rec.driveId ? "☁ 드라이브에 있음" : null, rec.model, rec.quality, rec.size,
    rec.cost != null ? `앱 추정 $${rec.cost.toFixed(4)}` : null, rec.requestId ? `요청 ${rec.requestId}` : null].filter(Boolean).join(" · ");
  $("detail").hidden = false;
}

// ------------------------------------------------------------------ 부분 수정
let paint = null;
const rtStates = new Map();   // 이미지별로 칠한 레이어와 요청 문장 보관 (닫아도 유지)
async function openRetouch(id) {
  const rec = await db.getImage(id); if (!rec) return;
  state.retouchId = id;
  $("retouch").hidden = false;
  paint = paint || new PaintLayer($("rtCanvas"));
  const saved = rtStates.get(id);
  await paint.load(rec.blob, saved && saved.layer);
  paint.brush = +$("rtSize").value;
  $("rtRequest").value = saved ? saved.request : "";
  const orig = await rootOriginal(id);
  $("rtUseOrig").disabled = !orig || orig.id === id; $("rtUseOrig").checked = !!orig && orig.id !== id;
}
function keepRetouchState() { if (paint && state.retouchId) rtStates.set(state.retouchId, { layer: paint.layer, request: $("rtRequest").value }); }

async function sendRetouch() {
  const request = $("rtRequest").value.trim();
  if (!request) { await modal("부분 수정", "어떻게 바꿀지 적어 주세요."); return; }
  const id = state.retouchId; const marks = paint.hasMarks();
  const m = state.catalog.get(opts().model);
  if (marks && !m.markup) { await modal("영역 지정 미지원", `현재 모델(${m.label || m.id})은 빨간 펜 영역 지정을 지원하지 않습니다. 생성 화면의 옵션에서 GPT-Image-2.5/2.6 계열로 바꾸거나, 칠한 것을 지우고 전체 수정으로 보내세요.`); return; }
  const orig = $("rtUseOrig").checked ? await rootOriginal(id) : null;
  let [prompt, negative] = splitPrompt(promptFor("retouch") || "{area_instruction} {reference_instruction}\n\nRequested change: {request}");
  if (!prompt.includes("{request}")) prompt += "\n\nRequested change: {request}";
  prompt = joinPrompt(prompt, negative).replaceAll("{area_instruction}", marks ? AREA_MARKUP : AREA_WHOLE)
    .replaceAll("{reference_instruction}", orig ? REF_ORIGINAL : "").replaceAll("{request}", request).replace(/[ \t]+/g, " ").trim();
  prompt = composePrompt(prompt);
  keepRetouchState();
  $("retouch").hidden = true; go("create");
  await runJob({ fn: "retouch", prompt, inputIds: orig ? [id, orig.id] : [id], markup: marks ? await paint.markupBlob() : null,
    sourceId: id, extra: { request } });
  if (state.resultId && (await db.getImage(state.resultId))?.sourceId === id) rtStates.delete(id);
}

// ------------------------------------------------------------------ 파트 사면도 (웹 v0.4.0)
let pv = null;
const pvStates = new Map();   // 이미지별 { type, layers:{head,hair}, overlay, cur, useOrig, head }
const pvUi = { id: null, cur: null, layers: null };

async function headSheets(id) {
  const root = await rootOriginal(id);
  const rows = (await db.listImages()).filter((r) => r.kind === "output" && r.function === "part_views_head");
  let same = null;
  for (const r of rows) { const o = await rootOriginal(r.id); if (!same && root && o && o.id === root.id) same = r.id; }
  return { rows, same };
}

async function openPartViews(id) {
  const rec = await db.getImage(id); if (!rec) return;
  pvUi.id = id;
  $("partviews").hidden = false;
  pv = pv || new PaintLayer($("pvCanvas"));
  const saved = pvStates.get(id);
  await pv.load(rec.blob, saved && saved.overlay);
  pv.brush = +$("pvSize").value; pv.ghost = null;
  pvUi.layers = { head: saved && saved.layers ? copyCanvas(saved.layers.head) : blankLike(pv.layer),
    hair: saved && saved.layers ? copyCanvas(saved.layers.hair) : blankLike(pv.layer) };
  pvUi.cur = null;
  const sel = $("pvType");
  if (!sel.options.length) {
    [...PART_TYPES, [SET_KEY, "헤드+헤어 세트 (2장, 크기 맞춤)"]].forEach(([k, l]) => { const o = document.createElement("option"); o.value = k; o.textContent = l; sel.appendChild(o); });
  }
  sel.value = (saved && saved.type) || "part_views_hair";
  const { rows, same } = await headSheets(id);
  const hs = $("pvHead"); hs.innerHTML = "";
  [[ "", "(쓰지 않음)"], ...rows.map((r) => [String(r.id), `${r.name || "헤드 사면도"} ${r.created.slice(5, 16).replace("T", " ")}`])]
    .forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; hs.appendChild(o); });
  hs.value = saved && saved.head != null ? saved.head : (same ? String(same) : "");
  const orig = await rootOriginal(id);
  $("pvUseOrig").disabled = !orig || orig.id === id;
  $("pvUseOrig").checked = saved ? saved.useOrig && !$("pvUseOrig").disabled : !!orig && orig.id !== id;
  if (sel.value === SET_KEY) { pvUi.cur = "head"; pvApplyLayer(); if (saved && saved.cur === "hair") pvSwitch("hair"); }
  pvOnType();
}

function pvIsSet() { return $("pvType").value === SET_KEY; }
function pvLayerOf(name) { return pvUi.cur === name ? pv.layer : pvUi.layers[name]; }
function pvApplyLayer() {
  const other = pvUi.cur === "head" ? "hair" : "head";
  pv.layer = pvUi.layers[pvUi.cur]; pv.ghost = tintLayer(pvUi.layers[other]); pv.undo = []; pv.draw();
  $("pvLayerHead").classList.toggle("on", pvUi.cur === "head"); $("pvLayerHair").classList.toggle("on", pvUi.cur === "hair");
}
function pvSwitch(name) {
  if (!pvUi.cur || name === pvUi.cur) return;
  pvUi.layers[pvUi.cur] = pv.layer; pvUi.cur = name; pvApplyLayer();
}
function pvOnType() {
  const set = pvIsSet();
  if (set && !pvUi.cur) { pvUi.layers.head = pv.layer; pvUi.cur = "head"; pvApplyLayer(); }
  else if (!set && pvUi.cur) { pvUi.layers[pvUi.cur] = pv.layer; pvUi.cur = null; pv.ghost = null; pv.draw(); }
  $("pvLayers").hidden = !set;
  $("pvHeadBox").hidden = !($("pvType").value === "part_views_hair" && $("pvHead").options.length > 1);
  $("pvSend").textContent = set ? "세트 생성 (유료 2장)" : "사면도 생성 (유료)";
  $("pvInfo").textContent = set
    ? "[헤드]로 민머리를, [헤어]로 가발을 칠하세요. 헤드 사면도를 먼저 만들고 그 결과에 크기를 맞춰 헤어 사면도를 이어서 만듭니다. 유료 2회 · 헤드가 실패하면 헤어는 보내지 않습니다."
    : "돌릴 파트 하나를 넉넉하게 감싸도록 칠하세요. 칠한 부분만 잘라내고 나머지는 흰색으로 지운 뒤 보냅니다. 유료 생성 1회입니다.";
}
function keepPvState() {
  if (!pv || !pvUi.id) return;
  const layers = { head: copyCanvas(pvLayerOf("head")), hair: copyCanvas(pvLayerOf("hair")) };
  pvStates.set(pvUi.id, { type: $("pvType").value, layers, overlay: copyCanvas(pv.layer), cur: pvUi.cur,
    useOrig: $("pvUseOrig").checked, head: $("pvHead").value });
}

function partPrompt(key, hasOrig, headSheet) {
  const refs = [];
  if (headSheet) refs.push(HEAD_SHEET_REF);
  if (hasOrig) refs.push(PART_REF(headSheet ? "THIRD" : "SECOND"));
  const ref = refs.join(" ");
  let [pos, neg] = splitPrompt(promptFor(key));
  if (ref && !pos.includes("{reference_instruction}")) pos += "\n\n{reference_instruction}";
  const filled = joinPrompt(pos, neg).replaceAll("{reference_instruction}", ref).split("\n")
    .map((l) => (l.trim() ? l.replace(/[ \t]+/g, " ").trim() : "")).join("\n").trim();
  return composePrompt(filled);
}

async function savePartInput(blob, fromId, label) {
  return db.addImage({ kind: "part_input", blob, thumb: await thumbnail(blob), name: `${label} 파트`, sourceId: fromId });
}

async function runPart(key, partId, fromId, orig, headId, extra = {}, more = {}) {
  const inputIds = [partId, ...(headId ? [headId] : []), ...(orig ? [orig.id] : [])];
  return runJob({ fn: key, prompt: partPrompt(key, !!orig, !!headId), inputIds, sourceId: partId,
    extra: { part_type: key, from_image_id: fromId, used_original_reference: !!orig, ...(headId ? { head_sheet_id: headId } : {}), ...extra }, ...more });
}

async function sendPartViews() {
  const id = pvUi.id; const key = $("pvType").value; const set = key === SET_KEY;
  if (set) {
    const missing = [["head", "헤드"], ["hair", "헤어"]].filter(([k]) => !layerHasMarks(pvLayerOf(k))).map(([, n]) => n);
    if (missing.length) { await modal("파트 사면도", `${missing.join("·")} 파트를 칠해 주세요. (위의 [헤드]/[헤어]를 바꿔 가며 둘 다 칠해야 합니다)`); return; }
  } else if (!pv.hasMarks()) { await modal("파트 사면도", "돌릴 파트를 빨간 펜으로 칠해 주세요."); return; }
  const orig = $("pvUseOrig").checked ? await rootOriginal(id) : null;
  keepPvState();
  $("partviews").hidden = true; go("create");
  if (set) {
    const headPart = await savePartInput(await isolatePart(pv.base, pvLayerOf("head")), id, "헤드 (민머리)");
    const hairPart = await savePartInput(await isolatePart(pv.base, pvLayerOf("hair")), id, "헤어 (가발)");
    const headOut = await runPart("part_views_head", headPart, id, orig, null, { set_step: 1, set_total: 2 },
      { note: "\n헤드+헤어 세트: 이어서 헤어 사면도도 보냅니다 (총 2장, 비용 약 2배)" });
    if (!headOut) { await db.deleteImage(headPart); await db.deleteImage(hairPart); toast("세트: 헤어는 보내지 않았습니다."); return; }
    const hairOut = await runPart("part_views_hair", hairPart, id, orig, headOut, { set_step: 2, set_total: 2 }, { noConfirm: true });
    if (!hairOut) await db.deleteImage(hairPart);
    if (hairOut) pvStates.delete(id);
    return;
  }
  const label = Object.fromEntries(PART_TYPES)[key];
  const partId = await savePartInput(await isolatePart(pv.base, pv.layer), id, label);
  const headId = key === "part_views_hair" && $("pvHead").value ? +$("pvHead").value : null;
  const out = await runPart(key, partId, id, orig, headId);
  if (out) pvStates.delete(id); else await db.deleteImage(partId);
}

// 정렬 가이드: 헤어 사면도를 기준 헤드 사면도와 겹쳐 보기
async function openOverlay(id) {
  const rec = await db.getImage(id);
  const head = rec && rec.extra && rec.extra.head_sheet_id ? await db.getImage(rec.extra.head_sheet_id) : null;
  if (!head) { await modal("헤드와 겹쳐 보기", "이 결과는 헤드 사면도를 기준으로 만든 헤어 사면도가 아닙니다."); return; }
  state.overlay = { below: head.blob, above: rec.blob };
  $("overlay").hidden = false;
  await renderOverlay();
}
async function renderOverlay() {
  if (!state.overlay) return;
  const a = +$("ovAlpha").value / 100;
  $("ovLabel").textContent = `헤어 ${Math.round(a * 100)}%`;
  const c = await overlayCanvas(state.overlay.below, state.overlay.above, a);
  const v = $("ovCanvas"); v.width = c.width; v.height = c.height; v.getContext("2d").drawImage(c, 0, 0);
}

function showPrompt(k) { const [p, n] = splitPrompt(promptFor(k)); $("promptText").value = p; $("promptNeg").value = n; }

// ------------------------------------------------------------------ PC로 원본 보내기
let pcPicked = [];
async function refreshPcStatus(text) {
  const all = (await db.listImages()).filter((r) => r.pcInbox);
  const pending = all.filter((r) => r.pcInbox === "pending").length, sent = all.length - pending;
  $("pcSummary").textContent = pending ? `대기 ${pending}장` : (sent ? `보냄 ${sent}장` : "");
  $("pcStatus").textContent = (text ? text + "\n" : "") + (pending ? `보낼 대기 ${pending}장 (구글 연결되면 보냅니다) · ` : "") + `지금까지 보낸 원본 ${sent}장`;
  $("pcStatus").style.whiteSpace = "pre-wrap";
}
async function sendToPc() {
  if (!pcPicked.length) { toast("먼저 사진을 골라 주세요."); return; }
  const project = $("pcProject").value.trim(), note = $("pcNote").value.trim();
  for (const f of pcPicked) {
    if (!/^image\/(png|jpeg|webp)$/.test(f.type)) continue;
    let size = [0, 0]; try { size = await sizeOf(f); } catch { continue; }
    const blob = f.slice(0, f.size, f.type);
    await db.addImage({ kind: "original", blob, thumb: await thumbnail(blob), name: (f.name || "원본").replace(/\.[^.]+$/, ""),
      width: size[0], height: size[1], pcInbox: "pending", pcProject: project, pcNote: note });
  }
  const n = pcPicked.length; pcPicked = []; $("pcPicked").textContent = ""; $("pcFiles").value = "";
  await refreshPcStatus(`${n}장을 보낼 목록에 넣었습니다.`);
  await doSync(true);
  await refreshPcStatus();
}

// ------------------------------------------------------------------ 구글 드라이브 동기화
const gClientId = () => localStorage.getItem("vc.gClientId") || state.config.google_web_client_id || "";
function refreshSyncStatus(text) {
  const last = localStorage.getItem("vc.lastSync");
  const el = $("syncStatus");
  el.textContent = state.syncing ? "☁ 동기화 중" : hasValidToken() ? "☁ 연결됨" : "☁";
  el.title = text || (last ? "마지막 동기화 " + new Date(last).toLocaleString() : "구글 드라이브 동기화");
  const g = $("gStatus");
  if (g) g.textContent = (text ? text + "\n" : "") + (hasValidToken() ? "● 연결됨" : "○ 연결 안 됨 (버튼을 누르면 구글 로그인 창이 뜹니다)")
    + (last ? ` · 마지막 동기화 ${new Date(last).toLocaleString()}` : "");
  if (g) g.style.whiteSpace = "pre-wrap";
}

/** interactive=true 일 때만 구글 로그인 창을 띄운다 (브라우저는 버튼을 누른 순간에만 팝업을 허용) */
async function doSync(interactive) {
  if (state.syncing) return;
  if (!hasValidToken()) {
    if (!interactive) return;
    try { await requestToken(gClientId()); }
    catch (e) { refreshSyncStatus("✖ " + e.message); toast(e.message); return; }
  }
  state.syncing = true; refreshSyncStatus();
  try {
    const st = await syncOnce((t) => { $("syncStatus").textContent = "☁ " + t.slice(0, 18); });
    const msg = "동기화 완료 · " + syncSummary(st) + (st.errors.length ? "\n" + st.errors.slice(0, 5).join("\n") : "");
    state.syncing = false; refreshSyncStatus(msg);
    if (interactive || st.downloaded || st.deleted || st.sentToPc) toast(syncSummary(st));
    if (st.sentToPc) refreshPcStatus();
    if (document.getElementById("view-history").classList.contains("active")) renderHistory();
    if (state.inputId && !(await db.getImage(state.inputId))) await setInput(null);
  } catch (e) {
    state.syncing = false; refreshSyncStatus("✖ 동기화 실패: " + e.message);
    if (interactive) toast("동기화 실패: " + e.message);
  }
}
function scheduleSync() {
  if (!settings().syncAuto || !hasValidToken()) return;
  clearTimeout(state.syncTimer); state.syncTimer = setTimeout(() => doSync(false), 3000);
}

// ------------------------------------------------------------------ 설정 화면
async function refreshMoney() {
  const s = settings(); const usage = await db.listUsage();
  const sum = (since) => usage.filter((u) => u.created >= since && u.status === "ok").reduce((a, u) => a + (u.cost || 0), 0);
  const unknown = usage.filter((u) => u.created >= monthStart() && u.status === "unknown").length;
  let text = `오늘 앱 추정 $${sum(dayStart()).toFixed(3)} · 이번 달 $${sum(monthStart()).toFixed(3)}` + (unknown ? ` · 과금 여부 불명 ${unknown}건(합계 미포함)` : "");
  if (s.credit > 0) {
    const used = sum(s.creditSince ? s.creditSince + "T00:00:00" : "0000");
    text += `\n충전 $${(+s.credit).toFixed(2)} − 사용 $${used.toFixed(3)} = 추정 잔액 $${(s.credit - used).toFixed(3)}`;
  }
  $("usageSummary").style.whiteSpace = "pre-wrap"; $("usageSummary").textContent = text;
}

async function renderSettings() {
  const s = settings();
  $("keyInput").value = ""; $("keyInput").placeholder = apiKey() ? `등록됨 (${apiKey().slice(0, 3)}…${apiKey().slice(-4)}) — 바꾸려면 새 키 입력` : "sk-...";
  $("demoMode").checked = !!s.demo; $("confirmReq").checked = !!s.confirm;
  $("gClientId").value = localStorage.getItem("vc.gClientId") || ""; $("gClientId").placeholder = state.config.google_web_client_id || "xxxx.apps.googleusercontent.com";
  $("gAuto").checked = !!s.syncAuto; refreshSyncStatus();
  $("creditInput").value = s.credit || ""; $("creditSince").value = s.creditSince || ""; $("budgetInput").value = s.budget || "";
  const pf = $("promptFn");
  if (!pf.options.length) PROMPT_KEYS.forEach((k) => { const o = document.createElement("option"); o.value = k; o.textContent = LABELS[k]; pf.appendChild(o); });
  showPrompt(pf.value);
  $("aboutText").textContent = `웹 v${WEB_VERSION} · 요금표 확인일 ${state.catalog.verified}\n이미지·기록은 이 기기의 이 브라우저에만 저장됩니다. 브라우저 데이터를 지우면 함께 지워집니다.`;
  $("aboutText").style.whiteSpace = "pre-wrap";
  refreshMoney();
}

// ------------------------------------------------------------------ 이벤트
function bindEvents() {
  document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
  $("inputPicker").onclick = () => $("fileInput").click();
  $("pickBtn").onclick = () => $("fileInput").click();
  $("fileInput").onchange = async (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) await addOriginal(f, f.name.replace(/\.[^.]+$/, "")); };
  $("pasteBtn").onclick = pasteImage;
  $("fromHistoryBtn").onclick = () => go("history");
  document.addEventListener("paste", (e) => {
    const f = [...(e.clipboardData?.files || [])].find((x) => x.type.startsWith("image/"));
    if (f) { e.preventDefault(); addOriginal(f, "붙여넣기"); }
  });
  $("modelSel").onchange = onModelChange;
  ["qualitySel", "sizeSel", "bgSel"].forEach((id) => { $(id).onchange = saveOpts; });
  $("genBtn").onclick = generate;
  $("cancelBtn").onclick = () => state.busy?.abort();
  document.querySelectorAll("#resultCard [data-act]").forEach((b) => b.addEventListener("click", () => act(b.dataset.act, state.resultId)));
  document.querySelectorAll("#detail [data-dact]").forEach((b) => b.addEventListener("click", () => act(b.dataset.dact, state.detailId)));
  document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => {
    if (b.closest("#retouch")) keepRetouchState();
    if (b.closest("#partviews")) keepPvState();
    b.closest(".sheet").hidden = true;
  }));
  document.querySelectorAll("#histFilter button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#histFilter button").forEach((x) => x.classList.toggle("on", x === b)); renderHistory();
  }));
  $("histSearch").oninput = renderHistory;
  // 부분 수정
  $("rtSize").oninput = () => { if (paint) paint.brush = +$("rtSize").value; };
  $("rtEraser").onclick = () => { paint.eraser = !paint.eraser; $("rtEraser").classList.toggle("on", paint.eraser); };
  $("rtUndo").onclick = () => paint.undoLast();
  $("rtClear").onclick = () => paint.clear();
  $("rtSend").onclick = sendRetouch;
  // 파트 사면도
  $("pvSize").oninput = () => { if (pv) pv.brush = +$("pvSize").value; };
  $("pvEraser").onclick = () => { pv.eraser = !pv.eraser; $("pvEraser").classList.toggle("on", pv.eraser); };
  $("pvUndo").onclick = () => pv.undoLast();
  $("pvClear").onclick = () => pv.clear();
  $("pvType").onchange = pvOnType;
  $("pvLayerHead").onclick = () => pvSwitch("head");
  $("pvLayerHair").onclick = () => pvSwitch("hair");
  $("pvSend").onclick = sendPartViews;
  $("ovAlpha").oninput = renderOverlay;
  // 설정
  $("keySave").onclick = () => {
    const v = $("keyInput").value.trim();
    if (!v) { toast("키를 입력하세요."); return; }
    localStorage.setItem(KEY_STORE, v); $("keyInput").value = ""; renderSettings(); refreshStatus(); toast("키를 저장했습니다.");
  };
  $("keyDel").onclick = async () => {
    if (await modal("API 키", "저장된 키를 삭제할까요?", [["취소", false], ["삭제", true, "danger"]])) { localStorage.removeItem(KEY_STORE); renderSettings(); refreshStatus(); }
  };
  $("keyTest").onclick = async () => {
    if (!apiKey()) { $("keyMsg").textContent = "등록된 키가 없습니다."; return; }
    $("keyMsg").textContent = "확인 중…";
    try { const ids = await listImageModels(apiKey()); $("keyMsg").textContent = `✔ 연결 성공 · 이미지 모델 ${ids.length}개: ${ids.join(", ")} (조회는 과금되지 않습니다)`; }
    catch (e) { $("keyMsg").textContent = "✖ " + e.message; }
  };
  $("syncStatus").onclick = () => doSync(true);
  $("pcPick").onclick = () => $("pcFiles").click();
  $("pcFiles").onchange = () => { pcPicked = [...$("pcFiles").files]; $("pcPicked").textContent = pcPicked.length ? `${pcPicked.length}장 선택됨` : ""; };
  $("pcSend").onclick = () => sendToPc();
  $("gConnect").onclick = () => {
    const v = $("gClientId").value.trim();
    if (v) localStorage.setItem("vc.gClientId", v); else localStorage.removeItem("vc.gClientId");
    if (!gClientId()) { toast("웹 클라이언트 ID 를 넣어 주세요."); return; }
    doSync(true);
  };
  $("gDisconnect").onclick = () => {
    try { window.google?.accounts?.oauth2?.revoke?.(JSON.parse(sessionStorage.getItem("vc.gtoken") || "{}").value || "", () => {}); } catch { /* 무시 */ }
    clearToken(); localStorage.removeItem("vc.gconsented"); refreshSyncStatus("연결을 해제했습니다. 이 기기의 기록과 드라이브 파일은 그대로입니다.");
  };
  $("gAuto").onchange = () => saveSettings({ syncAuto: $("gAuto").checked });
  $("demoMode").onchange = () => { saveSettings({ demo: $("demoMode").checked }); refreshStatus(); updateEstimate(); };
  $("confirmReq").onchange = () => saveSettings({ confirm: $("confirmReq").checked });
  $("moneySave").onclick = () => {
    saveSettings({ credit: +$("creditInput").value || 0, creditSince: $("creditSince").value || "", budget: +$("budgetInput").value || 0 });
    refreshMoney(); toast("저장했습니다.");
  };
  $("promptFn").onchange = () => { showPrompt($("promptFn").value); $("promptMsg").textContent = ""; };
  $("promptSave").onclick = () => {
    const k = $("promptFn").value, v = joinPrompt($("promptText").value, $("promptNeg").value);
    if (!$("promptText").value.trim()) { toast("Positive 프롬프트가 비어 있습니다."); return; }
    if (v === joinPrompt(...splitPrompt(state.defaults[k]))) localStorage.removeItem("vc.prompt." + k); else localStorage.setItem("vc.prompt." + k, v);
    $("promptMsg").textContent = "저장했습니다. 다음 요청부터 적용됩니다.";
  };
  $("promptReset").onclick = () => {
    const k = $("promptFn").value; localStorage.removeItem("vc.prompt." + k); showPrompt(k); $("promptMsg").textContent = "기본값으로 되돌렸습니다.";
  };
  $("showChangelog").onclick = async () => {
    try { await modal("변경 내역", await (await fetch("data/WEB_CHANGELOG.md", { cache: "no-cache" })).text()); } catch { toast("불러오지 못했습니다."); }
  };
  $("wipeAll").onclick = async () => {
    if (await modal("모두 지우기", "이 기기에 저장된 이미지·기록·설정·API 키를 모두 지울까요? 되돌릴 수 없습니다.", [["취소", false], ["모두 지우기", true, "danger"]])) {
      await db.wipe(); localStorage.clear(); location.reload();
    }
  };
}

window.addEventListener("beforeunload", (e) => { if (state.busy) { e.preventDefault(); e.returnValue = ""; } });
boot();
