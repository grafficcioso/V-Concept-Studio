// 이미지 처리: 디코딩, 투명 부분 흰 배경 합치기, 썸네일, 부분 수정용 빨간 펜 캔버스.
export const MAX_SIDE = 4096;   // 휴대폰 고해상도 사진을 이 크기 안으로 줄여서 보낸다

export async function decode(blob) {
  // createImageBitmap 은 EXIF 회전을 반영한다
  try { return await createImageBitmap(blob, { imageOrientation: "from-image" }); }
  catch { return await createImageBitmap(blob); }
}

function canvasOf(w, h) {
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  return c;
}

const toBlob = (c, type = "image/png", q) => new Promise((res) => c.toBlob(res, type, q));

export function fitSize(w, h, max = MAX_SIDE) {
  const k = Math.min(1, max / Math.max(w, h));
  return [Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k))];
}

function hasTransparency(ctx, w, h) {
  const d = ctx.getImageData(0, 0, w, h).data;
  for (let i = 3; i < d.length; i += 4) if (d[i] < 255) return true;
  return false;
}

/** API 로 보낼 입력 이미지 준비.
 *  투명 부분은 흰 배경과 합친다(투명 픽셀에 숨은 검정을 모델이 배경으로 읽는 문제 방지).
 *  원본 Blob 은 바꾸지 않는다. 반환: { blob, width, height } */
export async function prepareForApi(blob) {
  const bmp = await decode(blob);
  const [w, h] = fitSize(bmp.width, bmp.height);
  const c = canvasOf(w, h);
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0, w, h);
  if (hasTransparency(ctx, w, h)) {
    const flat = canvasOf(w, h);
    const fctx = flat.getContext("2d");
    fctx.fillStyle = "#fff";
    fctx.fillRect(0, 0, w, h);
    fctx.drawImage(c, 0, 0);
    return { blob: await toBlob(flat, "image/png"), width: w, height: h };
  }
  return { blob: await toBlob(c, "image/png"), width: w, height: h };
}

export async function thumbnail(blob, size = 320) {
  const bmp = await decode(blob);
  const [w, h] = fitSize(bmp.width, bmp.height, size);
  const c = canvasOf(w, h);
  c.getContext("2d").drawImage(bmp, 0, 0, w, h);
  return toBlob(c, "image/webp", 0.85);
}

export async function sizeOf(blob) {
  const bmp = await decode(blob);
  return [bmp.width, bmp.height];
}

/** 빨간 반투명 펜 캔버스. 칠한 내용은 원본 해상도의 별도 레이어에 저장한다. */
export class PaintLayer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.brush = 40;   // 화면 기준 px
    this.eraser = false;
    this.undo = [];
    this.base = null;
    this.layer = null;
    this.last = null;
    this._bind();
  }

  async load(blob, restoreLayer = null) {
    this.base = await decode(blob);
    const [w, h] = fitSize(this.base.width, this.base.height);
    this.canvas.width = w; this.canvas.height = h;
    this.layer = canvasOf(w, h);
    if (restoreLayer && restoreLayer.width === w && restoreLayer.height === h) this.layer.getContext("2d").drawImage(restoreLayer, 0, 0);
    this.undo = [];
    this.draw();
  }

  scale() { return this.canvas.width / this.canvas.getBoundingClientRect().width || 1; }

  draw() {
    const { ctx, canvas } = this;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(this.base, 0, 0, canvas.width, canvas.height);
    ctx.globalAlpha = 0.45;
    ctx.drawImage(this.layer, 0, 0);
    ctx.globalAlpha = 1;
  }

  _pos(e) {
    const r = this.canvas.getBoundingClientRect();
    const k = this.canvas.width / r.width;
    return [(e.clientX - r.left) * k, (e.clientY - r.top) * k];
  }

  stroke(a, b) {
    const g = this.layer.getContext("2d");
    g.save();
    g.globalCompositeOperation = this.eraser ? "destination-out" : "source-over";
    g.strokeStyle = "#ff0000"; g.fillStyle = "#ff0000";
    g.lineCap = "round"; g.lineJoin = "round";
    g.lineWidth = this.brush * this.scale();
    g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0] + 0.01, b[1]); g.stroke();
    g.restore();
    this.draw();
  }

  _bind() {
    const c = this.canvas;
    c.addEventListener("pointerdown", (e) => {
      if (!this.layer) return;
      c.setPointerCapture(e.pointerId);
      const snap = canvasOf(this.layer.width, this.layer.height);
      snap.getContext("2d").drawImage(this.layer, 0, 0);
      this.undo.push(snap);
      if (this.undo.length > 25) this.undo.shift();
      this.last = this._pos(e);
      this.stroke(this.last, this.last);
      e.preventDefault();
    });
    c.addEventListener("pointermove", (e) => {
      if (!this.last) return;
      const p = this._pos(e);
      this.stroke(this.last, p);
      this.last = p;
      e.preventDefault();
    });
    const end = () => { this.last = null; };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
  }

  undoLast() {
    const prev = this.undo.pop();
    if (!prev) return;
    const g = this.layer.getContext("2d");
    g.clearRect(0, 0, this.layer.width, this.layer.height);
    g.drawImage(prev, 0, 0);
    this.draw();
  }

  clear() {
    const g = this.layer.getContext("2d");
    this.undo.push((() => { const s = canvasOf(this.layer.width, this.layer.height); s.getContext("2d").drawImage(this.layer, 0, 0); return s; })());
    g.clearRect(0, 0, this.layer.width, this.layer.height);
    this.draw();
  }

  hasMarks() {
    const d = this.layer.getContext("2d").getImageData(0, 0, this.layer.width, this.layer.height).data;
    for (let i = 3; i < d.length; i += 16) if (d[i] > 0) return true;
    return false;
  }

  /** API 로 보낼 주석 레이어: 입력 이미지와 같은 크기의 투명 PNG, 칠한 곳만 반투명 빨강 */
  async markupBlob() {
    const out = canvasOf(this.layer.width, this.layer.height);
    const g = out.getContext("2d");
    g.globalAlpha = 0.6;
    g.drawImage(this.layer, 0, 0);
    return toBlob(out, "image/png");
  }
}

export async function saveOrShare(blob, filename) {
  const file = new File([blob], filename, { type: blob.type || "image/png" });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: filename }); return "shared"; }
    catch (e) { if (e && e.name === "AbortError") return "cancelled"; }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return "downloaded";
}
