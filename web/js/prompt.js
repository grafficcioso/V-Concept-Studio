// Positive / Negative 프롬프트 (PC 프로그램 crs/functions.py 와 같은 규칙)
// 저장 형식: 앞부분 = Positive, "### Negative" 줄 아래 = Negative.
// 이미지 API 에 별도 negative 칸이 없어서, 보낼 때 맨 끝에 '피할 것' 문단으로 붙인다.
export const NEG_HEADER = "### Negative";
const NEG_RE = /^[ \t]*#{1,6}[ \t]*negative\b[^\n]*$/im;

export function splitPrompt(text) {
  text = (text || "").trim();
  const m = NEG_RE.exec(text);
  if (m) return [text.slice(0, m.index).trim(), text.slice(m.index + m[0].length).trim()];
  const paras = text.split("\n\n");
  if (paras.length > 1 && paras[paras.length - 1].trimStart().toLowerCase().startsWith("avoid:")) {
    const last = paras.pop();
    return [paras.join("\n\n").trim(), last.slice(last.indexOf(":") + 1).trim()];
  }
  return [text, ""];
}

export function joinPrompt(positive, negative) {
  positive = (positive || "").trim(); negative = (negative || "").trim();
  return negative ? `${positive}\n\n${NEG_HEADER}\n${negative}` : positive;
}

export function composePrompt(text) {
  const [pos, neg] = splitPrompt(text);
  return neg ? `${pos}\n\nAvoid (negative prompt). The result must NOT contain or show any of the following: ${neg.split(/\s+/).join(" ")}` : pos;
}
