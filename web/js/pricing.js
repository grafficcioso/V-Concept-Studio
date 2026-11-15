// 요금표(data/models.json)와 비용 추정. 모든 금액은 앱의 추정치.
export const DISCLAIMER = "앱 추정치입니다. 실제 청구액은 OpenAI 결제 페이지에서 확인하세요. 입력 이미지·텍스트 토큰 등 추가 비용이 있을 수 있습니다.";
const GENERIC = { qualities: ["auto", "low", "medium", "high"], sizes: ["auto", "1024x1024", "1024x1536", "1536x1024"],
  backgrounds: ["auto", "opaque", "transparent"], markup: false, pricing: {}, reference_per_image_usd: {} };

export class Catalog {
  constructor(data) { this.data = data || { models: [] }; }
  get verified() { return this.data.verified_date || "미확인"; }
  ids() { return this.data.models.map((m) => m.id); }
  get(id) { const m = this.data.models.find((x) => x.id === id); return { ...GENERIC, id, label: id, ...(m || {}) }; }

  validate(id, quality, size, bg) {
    const m = this.get(id); const p = [];
    if (!m.qualities.includes(quality)) p.push(`품질 '${quality}'은(는) 이 모델에서 지원되지 않습니다.`);
    if (!m.sizes.includes(size)) p.push(`크기 '${size}'은(는) 이 모델에서 지원되지 않습니다.`);
    if (!m.backgrounds.includes(bg)) p.push(`배경 '${bg}'은(는) 이 모델에서 지원되지 않습니다.`);
    return p;
  }

  actualCost(id, usage) {
    const p = this.get(id).pricing || {};
    if (!usage || p.image_output == null) return null;
    const d = usage.input_tokens_details || {};
    let text = d.text_tokens, img = d.image_tokens;
    if (text == null && img == null) { text = 0; img = usage.input_tokens || 0; }
    return ((text || 0) * (p.text_input || 0) + (img || 0) * (p.image_input || 0) + (usage.output_tokens || 0) * p.image_output) / 1e6;
  }

  /** usageRows: 같은 모델의 성공 기록 */
  estimate(id, quality, size, nInputs, usageRows) {
    const m = this.get(id); const p = m.pricing || {};
    if (p.image_output == null) return { ok: false, text: "정확한 예상 비용 계산 불가", note: "요금표에 이 모델의 가격이 없습니다." };
    const hist = usageRows.filter((u) => u.status === "ok" && u.model === id && u.quality === quality && u.size === size && u.output_tokens != null).slice(0, 20);
    const imgHist = usageRows.filter((u) => u.status === "ok" && u.model === id && u.input_image_tokens != null && u.n_inputs > 0).slice(0, 20)
      .map((u) => u.input_image_tokens / u.n_inputs);
    const imgCost = nInputs && imgHist.length ? nInputs * (imgHist.reduce((a, b) => a + b, 0) / imgHist.length) * (p.image_input || 0) / 1e6 : 0;
    if (hist.length) {
      const outs = hist.map((u) => u.output_tokens);
      const rate = p.image_output / 1e6;
      const avg = outs.reduce((a, b) => a + b, 0) / outs.length;
      const lo = imgCost + rate * Math.min(...outs), hi = imgCost + rate * Math.max(...outs), ty = imgCost + rate * avg;
      return { ok: true, typical: ty, high: hi,
        text: hi - lo > 1e-6 ? `예상 약 $${ty.toFixed(3)} (범위 $${lo.toFixed(3)} ~ $${hi.toFixed(3)})` : `예상 약 $${ty.toFixed(3)}`,
        note: `같은 모델·품질·크기 최근 ${hist.length}회 기록 기준 · ${DISCLAIMER}` };
    }
    const ref = (m.reference_per_image_usd || {})[`${quality}|${size}`];
    if (ref != null) return { ok: true, typical: ref + imgCost, high: ref + imgCost, text: `예상 약 $${(ref + imgCost).toFixed(3)}`, note: `요금표 참고 단가 기준 · ${DISCLAIMER}` };
    return { ok: false, text: "정확한 예상 비용 계산 불가 (사용 기록 없음)",
      note: `이 조합의 출력 토큰 수를 알 수 없습니다. 한 번 생성하면 그 기록으로 추정합니다. 이미지 출력 $${p.image_output}/1M 토큰 (요금표 확인일 ${this.verified})` };
  }
}
