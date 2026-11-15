// IndexedDB 저장소: 이미지(원본/결과)와 사용량 기록. 이 기기·이 브라우저 안에만 저장된다.
const DB_NAME = "vconcept";
const DB_VER = 2;
let dbp = null;

function open() {
  if (dbp) return dbp;
  dbp = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VER);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("images")) {
        const s = db.createObjectStore("images", { keyPath: "id", autoIncrement: true });
        s.createIndex("kind", "kind");
      }
      if (!db.objectStoreNames.contains("usage")) db.createObjectStore("usage", { keyPath: "id", autoIncrement: true });
      if (!db.objectStoreNames.contains("tombstones")) db.createObjectStore("tombstones", { keyPath: "uid" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbp;
}

function tx(store, mode, fn) {
  return open().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const s = t.objectStore(store);
    let result;
    Promise.resolve(fn(s)).then((r) => { result = r; });
    t.oncomplete = () => resolve(result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error || new Error("저장소 작업이 취소되었습니다 (저장 공간 부족일 수 있음)"));
  }));
}

const req2p = (r) => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });

export const db = {
  async addImage(rec) {
    rec.created = rec.created || new Date().toISOString();
    rec.name = rec.name || ""; rec.tags = rec.tags || ""; rec.favorite = rec.favorite ? 1 : 0;
    return tx("images", "readwrite", (s) => req2p(s.add(rec)));
  },
  async getImage(id) { return id ? tx("images", "readonly", (s) => req2p(s.get(id))) : null; },
  /** silent=false(기본)이고 이름·태그·즐겨찾기·분류가 바뀌면 동기화용 수정 시각을 찍는다 */
  async updateImage(id, fields, { silent = false } = {}) {
    if (!silent && ["name", "tags", "note", "favorite", "category"].some((k) => k in fields)) fields = { ...fields, metaUpdated: new Date().toISOString() };
    return tx("images", "readwrite", async (s) => {
      const cur = await req2p(s.get(id));
      if (cur) await req2p(s.put({ ...cur, ...fields }));
    });
  },
  /** 드라이브에 올라간 항목을 지우면 '지움 표시'를 남겨 다음 동기화 때 드라이브 휴지통으로 옮긴다 */
  async deleteImage(id, { tombstone = true } = {}) {
    const cur = await this.getImage(id);
    if (tombstone && cur && cur.driveId && cur.uid) await tx("tombstones", "readwrite", (s) => req2p(s.put({ uid: cur.uid, driveId: cur.driveId })));
    return tx("images", "readwrite", (s) => req2p(s.delete(id)));
  },
  async listTombstones() { return tx("tombstones", "readonly", (s) => req2p(s.getAll())); },
  async dropTombstone(uid) { return tx("tombstones", "readwrite", (s) => req2p(s.delete(uid))); },
  async clearTombstones() { return tx("tombstones", "readwrite", (s) => req2p(s.clear())); },
  async listImages() {
    // 큰 Blob 은 빼고 목록용 정보만
    const all = await tx("images", "readonly", (s) => req2p(s.getAll()));
    return all.map(({ blob, ...rest }) => rest).sort((a, b) => b.id - a.id);
  },
  async addUsage(rec) {
    rec.created = rec.created || new Date().toISOString();
    return tx("usage", "readwrite", (s) => req2p(s.add(rec)));
  },
  async listUsage() {
    const all = await tx("usage", "readonly", (s) => req2p(s.getAll()));
    return all.sort((a, b) => b.id - a.id);
  },
  async wipe() {
    if (dbp) { (await dbp).close(); dbp = null; }
    await req2p(indexedDB.deleteDatabase(DB_NAME));
  },
};
