// 구글 드라이브 동기화 (웹앱). PC 프로그램(crs/sync)과 같은 형식·규칙을 쓴다.
// - 앱이 만든 폴더 1개(appProperties vc_root=1), 항목 = PNG + description(JSON 메타) + appProperties(vc_item, vc_uid)
// - 이름·태그·즐겨찾기·분류는 나중에 고친 쪽이 이긴다 / 지운 항목은 드라이브 휴지통으로
import { db } from "./db.js";
import { thumbnail } from "./images.js";

export const SCOPE = "https://www.googleapis.com/auth/drive.file";
const META_VERSION = 1;
const FOLDER_NAME = "V-Concept Studio 동기화";
const base = () => window.__VC_DRIVE_BASE || "https://www.googleapis.com";

let token = null;            // { value, expires }
try { token = JSON.parse(sessionStorage.getItem("vc.gtoken") || "null"); } catch { token = null; }

export function hasValidToken() { return !!(token && token.expires > Date.now() + 60000); }
export function clearToken() { token = null; sessionStorage.removeItem("vc.gtoken"); }

/** 구글 로그인 팝업으로 접근 토큰 받기 (사용자가 버튼을 눌렀을 때만 호출) */
export function requestToken(clientId, { consent = false } = {}) {
  return new Promise((resolve, reject) => {
    const g = window.google && window.google.accounts && window.google.accounts.oauth2;
    if (!g) { reject(new Error("구글 로그인 모듈을 불러오지 못했습니다. 인터넷 연결을 확인하고 새로고침해 주세요.")); return; }
    if (!clientId) { reject(new Error("구글 클라이언트 ID 가 설정되지 않았습니다.")); return; }
    const client = g.initTokenClient({
      client_id: clientId, scope: SCOPE,
      callback: (r) => {
        if (r && r.access_token) {
          token = { value: r.access_token, expires: Date.now() + (Number(r.expires_in) || 3600) * 1000 };
          sessionStorage.setItem("vc.gtoken", JSON.stringify(token));
          localStorage.setItem("vc.gconsented", "1");
          resolve(token.value);
        } else reject(new Error("구글 연결이 취소되었습니다."));
      },
      error_callback: (e) => reject(new Error(e && e.type === "popup_closed" ? "구글 로그인 창이 닫혔습니다." : "구글 로그인 창을 열지 못했습니다. 팝업 차단을 확인하세요.")),
    });
    client.requestAccessToken({ prompt: consent || !localStorage.getItem("vc.gconsented") ? "consent" : "" });
  });
}

class DriveError extends Error { constructor(msg, status) { super(msg); this.status = status; } }

async function api(method, path, { json, body, headers, upload = false, raw = false } = {}) {
  if (!hasValidToken()) throw new DriveError("구글 연결이 만료되었습니다. [동기화]를 다시 눌러 연결해 주세요.", 401);
  const res = await fetch(base() + (upload ? "/upload/drive/v3" : "/drive/v3") + path, {
    method, headers: { Authorization: `Bearer ${token.value}`, ...(json ? { "Content-Type": "application/json" } : {}), ...(headers || {}) },
    body: json ? JSON.stringify(json) : body,
  }).catch(() => { throw new DriveError("인터넷 연결 또는 구글 접속을 확인하세요.", 0); });
  if (!res.ok) {
    if (res.status === 401) clearToken();
    let msg = ""; try { msg = (await res.json()).error.message; } catch { /* 없음 */ }
    throw new DriveError(`구글 드라이브 오류(${res.status})${msg ? ": " + msg : ""}`, res.status);
  }
  return raw ? res.blob() : res.json();
}

async function findOrCreateFolder() {
  const q = "appProperties has { key='vc_root' and value='1' } and trashed=false and mimeType='application/vnd.google-apps.folder'";
  const r = await api("GET", "/files?" + new URLSearchParams({ q, fields: "files(id,name)", spaces: "drive" }));
  if (r.files && r.files.length) return r.files[0].id;
  return (await api("POST", "/files?fields=id", { json: { name: FOLDER_NAME, mimeType: "application/vnd.google-apps.folder", appProperties: { vc_root: "1" } } })).id;
}

async function listItems(folder) {
  const q = `'${folder}' in parents and trashed=false and appProperties has { key='vc_item' and value='1' }`;
  let out = [], page = null;
  do {
    const p = { q, pageSize: "200", spaces: "drive", fields: "nextPageToken,files(id,name,description,modifiedTime,appProperties)" };
    if (page) p.pageToken = page;
    const r = await api("GET", "/files?" + new URLSearchParams(p));
    out = out.concat(r.files || []); page = r.nextPageToken;
  } while (page);
  return out;
}

async function upload(folder, name, blob, meta) {
  const boundary = "vc" + Math.random().toString(16).slice(2);
  const metadata = { name, parents: [folder], description: JSON.stringify(meta), appProperties: { vc_item: "1", vc_uid: meta.uid }, mimeType: "image/png" };
  const body = new Blob([
    `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify(metadata)}\r\n--${boundary}\r\nContent-Type: image/png\r\n\r\n`,
    blob, `\r\n--${boundary}--\r\n`]);
  return api("POST", "/files?uploadType=multipart&fields=id,modifiedTime", { body, upload: true, headers: { "Content-Type": `multipart/related; boundary=${boundary}` } });
}

const parseMeta = (f) => {
  let m; try { m = JSON.parse(f.description || "{}"); } catch { return null; }
  m.uid = m.uid || (f.appProperties || {}).vc_uid;
  return m.uid && (m.kind === "output" || m.kind === "asset") ? m : null;
};
const tsKey = (s) => { const t = Date.parse(s || ""); return Number.isFinite(t) ? t : 0; };
const uid = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : Math.random().toString(16).slice(2) + Date.now().toString(16));

function metaOf(r) {
  return { v: META_VERSION, uid: r.uid, kind: r.kind === "asset" ? "asset" : "output", function: r.function || "", category: r.category || "",
    name: r.name || "", tags: r.tags || "", note: r.note || "", favorite: r.favorite ? 1 : 0, created: r.created,
    updated: r.metaUpdated || r.created, model: r.model || "", quality: r.quality || "", size: r.size || "",
    background: r.background || "", project: r.project || "웹앱", request_id: r.requestId || "" };
}

/** 동기화 1회. say(text) 로 진행 상황을 알린다. 반환: 통계 */
export async function syncOnce(say = () => {}) {
  const st = { uploaded: 0, downloaded: 0, meta: 0, deleted: 0, errors: [] };
  say("드라이브 폴더 확인 중…");
  const folder = await findOrCreateFolder();
  if (localStorage.getItem("vc.syncFolder") !== folder) {     // 다른 계정 → 연결 정보만 초기화 (지우지 않음)
    for (const r of await db.listImages()) if (r.driveId) await db.updateImage(r.id, { driveId: null, syncedMeta: null }, { silent: true });
    await db.clearTombstones();
    localStorage.setItem("vc.syncFolder", folder);
  }
  say("목록 읽는 중…");
  const remote = new Map();
  for (const f of await listItems(folder)) { const m = parseMeta(f); if (m) remote.set(m.uid, { f, m }); }

  for (const t of await db.listTombstones()) {
    try { if (t.driveId) await api("PATCH", `/files/${t.driveId}?fields=id`, { json: { trashed: true } }).catch((e) => { if (e.status !== 404) throw e; }); remote.delete(t.uid); await db.dropTombstone(t.uid); st.deleted++; }
    catch (e) { st.errors.push("삭제 반영 실패: " + e.message); }
  }

  const local = new Map();
  for (const r of (await db.listImages()).filter((x) => x.kind === "output" || x.kind === "asset")) local.set(r.uid || "__new" + r.id, r);

  for (const [u, { f, m }] of remote) {
    const r = local.get(u);
    if (!r) {
      try {
        say(`받는 중: ${m.name || f.name}`);
        const blob = await api("GET", `/files/${f.id}?alt=media`, { raw: true });
        const png = blob.type === "image/png" ? blob : new Blob([blob], { type: "image/png" });
        await db.addImage({ kind: m.kind, blob: png, thumb: await thumbnail(png), function: m.function, category: m.category,
          name: m.name, tags: m.tags, note: m.note, favorite: m.favorite, created: m.created, model: m.model, quality: m.quality,
          size: m.size, background: m.background, project: m.project, requestId: m.request_id, uid: u, driveId: f.id,
          metaUpdated: m.updated, syncedMeta: m.updated });
        st.downloaded++;
      } catch (e) { st.errors.push(`받기 실패(${m.name || u.slice(0, 8)}): ${e.message}`); }
      continue;
    }
    if (r.driveId !== f.id) await db.updateImage(r.id, { driveId: f.id }, { silent: true });
    const dirty = (r.metaUpdated || null) !== (r.syncedMeta || null);
    const lk = tsKey(r.metaUpdated || r.created), rk = tsKey(m.updated);
    try {
      if (dirty && lk >= rk) {
        await api("PATCH", `/files/${f.id}?fields=id`, { json: { description: JSON.stringify(metaOf(r)) } });
        await db.updateImage(r.id, { syncedMeta: r.metaUpdated }, { silent: true }); st.meta++;
      } else if (rk > lk) {
        await db.updateImage(r.id, { name: m.name || "", tags: m.tags || "", note: m.note || "", favorite: m.favorite ? 1 : 0,
          category: m.category || r.category || "", metaUpdated: m.updated, syncedMeta: m.updated }, { silent: true }); st.meta++;
      } else if (dirty) await db.updateImage(r.id, { syncedMeta: r.metaUpdated }, { silent: true });
    } catch (e) { st.errors.push("정보 갱신 실패: " + e.message); }
  }

  for (const [u, r] of local) {
    if (r.driveId && !remote.has(u)) { await db.deleteImage(r.id, { tombstone: false }); st.deleted++; }
  }

  for (const [u, r0] of local) {
    if (r0.driveId || remote.has(u)) continue;
    try {
      const r = await db.getImage(r0.id); if (!r) continue;
      if (!r.uid) { r.uid = uid(); await db.updateImage(r.id, { uid: r.uid }, { silent: true }); }
      say(`올리는 중: ${r.name || r.uid.slice(0, 8)}`);
      const meta = metaOf(r);
      const res = await upload(folder, `${meta.function || meta.category || "item"}_${r.uid.slice(0, 8)}.png`, r.blob, meta);
      await db.updateImage(r.id, { driveId: res.id, syncedMeta: r.metaUpdated || null }, { silent: true });
      st.uploaded++;
    } catch (e) { st.errors.push(`올리기 실패(${r0.name || r0.id}): ${e.message}`); }
  }
  localStorage.setItem("vc.lastSync", new Date().toISOString());
  return st;
}

export function summary(st) {
  const p = [`올림 ${st.uploaded}`, `받음 ${st.downloaded}`];
  if (st.meta) p.push(`정보 갱신 ${st.meta}`);
  if (st.deleted) p.push(`삭제 반영 ${st.deleted}`);
  if (st.errors.length) p.push(`오류 ${st.errors.length}`);
  return p.join(" · ");
}
