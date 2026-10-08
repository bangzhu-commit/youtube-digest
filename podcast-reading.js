/* Shared non-secret rules for source-aligned Chinese podcast reading. */
(function (root) {
  "use strict";
  const VERSION = "podcast-reading-v1";
  const AUDIO_MODELS = Object.freeze(["google/gemini-2.5-flash", "google/gemini-3.1-flash-lite-preview"]);
  const eligible = entry => entry.speaker !== "转写说明" && !/^\[\^[^\]]+\]:/.test(entry.text);
  function batches(entries, maxChars = 6500) {
    const result = []; let current = [], size = 0;
    for (const entry of entries.filter(eligible)) {
      if (current.length && (current.length === 3 || size + entry.text.length > maxChars)) { result.push(current); current = []; size = 0; }
      current.push(entry); size += entry.text.length;
    }
    if (current.length) result.push(current);
    return result;
  }
  function validateSources(segments) {
    if (!Array.isArray(segments) || !segments.length || segments.length > 3) throw new Error("每批精校需要 1 至 3 段原稿。");
    const ids = new Set(); let size = 0;
    return segments.map(segment => {
      if (!segment || typeof segment.id !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(segment.id) || ids.has(segment.id)) throw new Error("原稿段落编号无效。");
      ids.add(segment.id);
      if (typeof segment.text !== "string" || !segment.text.trim() || segment.text.length > 12000) throw new Error("原稿段落为空或过长。");
      size += segment.text.length;
      if (size > 18000) throw new Error("本批原稿过长，请分段精校。");
      return { id: segment.id, text: segment.text };
    });
  }
  function numbers(text) {
    const normalized = text.replace(/\[\^[^\]]+\]/g, "").replace(/(\d)[ \t]+(?=[\d.])/g, "$1").replace(/(\d\.)[ \t]+(?=\d)/g, "$1");
    return [...new Set(normalized.match(/\d+(?:\.\d+)?/g) || [])].sort();
  }
  function align(result, segments, audio = false) {
    const source = validateSources(segments);
    if (!Array.isArray(result?.segments) || result.segments.length !== source.length) throw new Error("精校缺少段落，未把不完整结果标成完成。");
    const map = new Map();
    for (const value of result.segments) {
      if (!value || map.has(value.id) || !source.some(item => item.id === value.id)) throw new Error("精校段落编号不匹配。");
      if (typeof value.text !== "string" || !value.text.trim() || value.text.length > 20000) throw new Error("精校返回了空段落或过长内容。");
      if (!Array.isArray(value.issues) || value.issues.length > 20 || value.issues.some(issue => typeof issue !== "string" || issue.length > 600)) throw new Error("精校疑点记录格式无效。");
      map.set(value.id, value);
    }
    return source.map(entry => {
      const value = map.get(entry.id);
      if (!audio && JSON.stringify(numbers(entry.text)) !== JSON.stringify(numbers(value.text))) throw new Error("文字精校改变了原稿数字，已保留原稿。请用听音复核确认。");
      if (value.text.length < Math.min(40, entry.text.length * 0.35)) throw new Error("精校内容疑似被摘要或截断，已保留原稿。");
      const issues = [...value.issues];
      for (const reference of entry.text.match(/\[\^[^\]]+\]/g) || []) {
        if (!value.text.includes(reference) && !issues.some(issue => issue.includes(reference))) {
          if (issues.length === 20) throw new Error("精校疑点过多，无法保留原稿脚注，请分段处理。");
          issues.push(`原稿含疑点标记 ${reference}，详见原稿脚注；仍需回听核验。`);
        }
      }
      return { id: entry.id, text: value.text.trim(), issues };
    });
  }
  function audioWindow(entries, entry) {
    if (!Number.isFinite(entry.start)) throw new Error("这一段没有可回听的时间戳。");
    const next = entries.find(item => item.start > entry.start);
    const start = Math.max(0, entry.start - 2);
    const end = Math.min(start + 90, next ? next.start + 2 : entry.start + 60);
    return { start, end };
  }
  async function fingerprint(raw) {
    const bytes = new TextEncoder().encode(raw);
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, "0")).join("");
  }
  function validReading(reading, transcript) {
    const ids = new Set();
    return !!transcript && reading?.version === VERSION && /^[a-f0-9]{64}$/.test(reading.sourceHash || "") && reading.sourceRaw === transcript.raw && Array.isArray(reading.items)
      && reading.items.length <= 500 && reading.items.every(item => {
        if (!item || ids.has(item.id) || typeof item.text !== "string" || !item.text.trim() || item.text.length > 20000 || !Array.isArray(item.issues) || item.issues.length > 20 || item.issues.some(issue => typeof issue !== "string" || issue.length > 600)) return false;
        ids.add(item.id);
        return transcript.entries.some(entry => eligible(entry) && entry.id === item.id && entry.text === item.sourceText && entry.start === item.start);
      });
  }
  const api = Object.freeze({ VERSION, AUDIO_MODELS, eligible, batches, validateSources, numbers, align, audioWindow, fingerprint, validReading });
  root.YTD_READING = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
