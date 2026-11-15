// OpenAI Images API 호출 (브라우저에서 직접). 자동 재시도 없음.
export const API_BASE = "https://api.openai.com/v1";

export class ApiError extends Error {
  constructor(kind, message, { billingUncertain = false, requestId = null } = {}) {
    super(message);
    this.kind = kind; this.billingUncertain = billingUncertain; this.requestId = requestId;
  }
}

function mapError(status, body, requestId) {
  const err = (body && body.error) || {};
  const code = String(err.code || ""); const msg = String(err.message || ""); const low = msg.toLowerCase();
  const param = err.param ? ` (문제 옵션: ${err.param})` : "";
  const mk = (kind, text, uncertain = false) => new ApiError(kind, text + (msg ? `\n(${msg})` : ""), { billingUncertain: uncertain, requestId });
  if (status === 401) return mk("auth", "API 키가 올바르지 않거나 만료되었습니다. [설정]에서 키를 다시 넣어 주세요.");
  if (status === 429) {
    if (code === "insufficient_quota" || low.includes("quota") || low.includes("billing"))
      return mk("quota", "OpenAI 크레딧이 부족하거나 결제 한도를 넘었습니다. OpenAI 결제 페이지에서 잔액을 확인하세요.");
    return mk("rate_limit", "요청 한도(레이트 리밋)에 걸렸습니다. 잠시 뒤 직접 다시 시도하세요.");
  }
  if (status === 403) {
    if (low.includes("verif")) return mk("permission", "이 모델을 쓰려면 OpenAI 조직 인증(Organization Verification)이 필요합니다.");
    return mk("permission", "이 키로는 해당 모델·기능을 쓸 권한이 없습니다.");
  }
  if (status === 404) return mk("not_found", "모델을 찾을 수 없습니다. 다른 모델을 골라 주세요.");
  if (status === 400 || status === 422) {
    if (code.includes("moderation") || low.includes("safety") || code.includes("content_policy"))
      return mk("moderation", "안전 정책에 의해 거부되었습니다. 이미지나 요청 내용을 조정해 보세요.");
    if (low.includes("verif")) return mk("permission", "이 모델을 쓰려면 OpenAI 조직 인증이 필요합니다.");
    return mk("unsupported", `지원하지 않는 옵션이거나 잘못된 요청입니다${param}. 품질·크기·배경을 바꿔 보세요.`);
  }
  if (status >= 500) return mk("server", `OpenAI 서버 오류(${status})입니다. 과금 여부가 불확실할 수 있습니다. 잠시 뒤 직접 다시 시도하세요.`, true);
  return mk("unknown", `요청이 거부되었습니다(${status}).`);
}

async function call(path, { key, form, json, method = "POST", signal, timeoutMs = 300000 }) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort("timeout"), timeoutMs);
  if (signal) signal.addEventListener("abort", () => ctrl.abort("cancel"));
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: { Authorization: `Bearer ${key}`, ...(json ? { "Content-Type": "application/json" } : {}) },
      body: form || (json ? JSON.stringify(json) : undefined),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    if (ctrl.signal.reason === "cancel") throw new ApiError("cancelled", "요청을 취소했습니다. 서버에서 이미 처리가 시작됐다면 과금될 수 있습니다.", { billingUncertain: true });
    if (ctrl.signal.reason === "timeout") throw new ApiError("timeout", "응답 대기 시간이 지났습니다. 서버가 처리했을 수 있어 과금 여부를 알 수 없습니다. 자동 재시도는 하지 않습니다.", { billingUncertain: true });
    throw new ApiError("network", "네트워크 오류로 OpenAI 와 통신하지 못했습니다. 연결을 확인하세요. 과금 여부가 불확실합니다.", { billingUncertain: true });
  }
  clearTimeout(timer);
  const requestId = res.headers.get("x-request-id");
  let body = null;
  try { body = await res.json(); } catch { /* 본문 없음 */ }
  if (!res.ok) throw mapError(res.status, body, requestId);
  return { body, requestId };
}

function b64ToBlob(b64, type = "image/png") {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type });
}

/** 이미지 편집 요청. images: Blob[] (첫 장이 편집 대상), markup: Blob|null */
export async function editImage({ key, model, prompt, images, markup = null, quality = "auto", size = "auto", background = "auto", signal, timeoutMs }) {
  if (!key) throw new ApiError("auth", "API 키가 없습니다. [설정]에서 넣어 주세요.");
  const form = new FormData();
  form.append("model", model);
  form.append("prompt", prompt);
  form.append("n", "1");
  if (quality && quality !== "auto") form.append("quality", quality);
  if (size && size !== "auto") form.append("size", size);
  if (background && background !== "auto") {
    form.append("background", background);
    if (background === "transparent") form.append("output_format", "png");
  }
  images.forEach((b, i) => form.append("image[]", b, `input_${i}.png`));
  if (markup) form.append("markup", markup, "markup.png");
  const started = performance.now();
  const { body, requestId } = await call("/images/edits", { key, form, signal, timeoutMs });
  const item = body && body.data && body.data[0];
  if (!item || !item.b64_json) throw new ApiError("server", "응답에 이미지가 없습니다. 과금되었을 수 있으니 사용량을 확인하세요.", { billingUncertain: true, requestId });
  const fmt = body.output_format || "png";
  return { blob: b64ToBlob(item.b64_json, `image/${fmt === "jpeg" ? "jpeg" : fmt}`), usage: body.usage || null, requestId,
           size: body.size, quality: body.quality, elapsed: (performance.now() - started) / 1000 };
}

export async function listImageModels(key) {
  const { body } = await call("/models", { key, method: "GET", timeoutMs: 30000 });
  return (body.data || []).map((m) => m.id).filter((id) => id.toLowerCase().includes("image")).sort();
}
