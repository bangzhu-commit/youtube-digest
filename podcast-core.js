(function (root) {
  "use strict";
  const MAX_TEXT = 600000;
  function seconds(stamp) {
    const parts = String(stamp).trim().replace(",", ".").split(":").map(Number);
    if (parts.length < 2 || parts.length > 3 || parts.some(x => !Number.isFinite(x) || x < 0)) return null;
    if (parts.at(-1) >= 60 || (parts.length === 3 && parts[1] >= 60)) return null;
    return parts.reduce((sum, value) => sum * 60 + value, 0);
  }
  function stamp(value) {
    if (value === null || !Number.isFinite(value)) return "无时间戳";
    return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
  }
  function parseTranscript(input, format = "") {
    const raw = String(input || "").replace(/^\uFEFF/, "");
    if (!raw.trim()) throw new Error("文稿为空。");
    if (raw.length > MAX_TEXT) throw new Error("文稿超过 60 万字符，请分段导入。");
    const entries = [];
    const add = (text, start = null, speaker = "") => {
      if (typeof text === "string" && text.trim()) entries.push({
        id: `p-${entries.length}`, text: text.trim(), start, speaker,
      });
    };
    if (format === "json" || /^[\[{]/.test(raw.trim()) && !/^\[\d+:/.test(raw.trim())) {
      let data;
      try { data = JSON.parse(raw); } catch { if (format === "json") throw new Error("JSON 文稿格式不正确。"); }
      if (data !== undefined) {
        const items = Array.isArray(data) ? data : data.segments;
        if (!Array.isArray(items)) throw new Error("JSON 中没有可读取的 segments 全文。");
        items.forEach(item => {
          const start = item.start === undefined ? null : Number(item.start);
          if (start !== null && (!Number.isFinite(start) || start < 0)) throw new Error("文稿包含无效时间戳。");
          add(item.text, start, typeof item.speaker === "string" ? item.speaker : "");
        });
        if (!entries.length) throw new Error("没有找到全文，引用或节目简介不能作为逐字稿。");
        return { entries, raw };
      }
    }
    const timed = /^(\d{1,3}:\d{2}(?::\d{2})?[.,]\d+)\s*-->\s*\d/gm;
    if (timed.test(raw)) {
      for (const block of raw.replace(/\r\n/g, "\n").split(/\n\s*\n/)) {
        const lines = block.split("\n");
        const index = lines.findIndex(line => line.includes("-->"));
        if (index < 0) continue;
        const start = seconds(lines[index].split("-->")[0].trim());
        if (start === null) throw new Error("字幕时间戳无效。");
        add(lines.slice(index + 1).join("\n"), start);
      }
    } else {
      // Skip metadata/shownotes only when an explicit full-transcript heading exists.
      const body = raw.split(/^##\s+(?:转写全文|逐字稿|全文|Transcript)\s*$/mi).at(-1);
      let start = null, speaker = "", buffer = [];
      const flush = () => { add(buffer.join("\n"), start, speaker); buffer = []; };
      for (const line of body.replace(/\r\n/g, "\n").split("\n")) {
        if (/^\[\^[^\]]+\]:/.test(line)) {
          flush(); start = null; speaker = "转写说明"; buffer.push(line); continue;
        }
        const anchor = /^\s*(?:#{1,6}\s*)?(?:\*\*)?\[(\d{1,3}:\d{2}(?::\d{2})?)\]\s*(.*?)\s*(?:\*\*)?\s*$/.exec(line);
        if (anchor) {
          flush();
          start = seconds(anchor[1]);
          if (start === null) throw new Error("文稿包含无效时间戳。");
          const tail = anchor[2].replace(/\*\*$/, "").trim();
          // A bare [timestamp] followed by speech is content, not a speaker label.
          if (!/^\s*(?:#|\*\*)/.test(line) && tail) { speaker = ""; buffer.push(tail); }
          else speaker = tail;
        } else if (!line.trim()) flush();
        else buffer.push(line);
      }
      flush();
    }
    if (!entries.length) throw new Error("没有找到可读取的全文。");
    return { entries, raw };
  }
  function annotation(input) {
    const id = String(input.id || "");
    if (!/^[a-zA-Z0-9_-]{8,80}$/.test(id)) throw new Error("批注编号无效。");
    const quote = String(input.quote || ""), thought = String(input.thought || "");
    if (!quote.trim() || !thought.trim()) throw new Error("请保留原句并填写感想。");
    if (quote.length > 20000 || thought.length > 20000) throw new Error("单条批注过长。");
    const start = input.start === null ? null : Number(input.start);
    if (start !== null && (!Number.isFinite(start) || start < 0)) throw new Error("批注时间戳无效。");
    const result = { id, quote, thought, start, createdAt: String(input.createdAt || new Date().toISOString()) };
    if (input.entryId !== undefined) {
      if (typeof input.entryId !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(input.entryId)) throw new Error("批注段落编号无效。");
      result.entryId = input.entryId;
    }
    if (input.readingQuote !== undefined) {
      if (typeof input.readingQuote !== "string" || !input.readingQuote.trim() || input.readingQuote.length > 20000) throw new Error("阅读版摘句无效。");
      result.readingQuote = input.readingQuote;
    }
    return result;
  }
  function markdownAnnotation(note) {
    const value = annotation(note);
    const reading = value.readingQuote ? `**阅读版摘句**\n\n${value.readingQuote.split("\n").map(line => `> ${line}`).join("\n")}\n\n` : "";
    return `### ${stamp(value.start)}\n\n${reading}**原文**\n\n${value.quote.split("\n").map(line => `> ${line}`).join("\n")}\n\n**我的感想**\n\n${value.thought}\n`;
  }
  function publicEpisode(data, id) {
    const stack = [data]; let inspected = 0;
    while (stack.length && inspected++ < 10000) {
      const value = stack.pop();
      if (!value || typeof value !== "object") continue;
      if (value.eid === id || value.id === id) {
        const transcript = value.transcript;
        if (typeof transcript === "string" && transcript.trim()) return { raw: transcript, format: "txt" };
        const entries = Array.isArray(transcript) ? transcript : transcript?.segments;
        if (Array.isArray(entries) && entries.some(entry => typeof entry?.text === "string" && entry.text.trim())) {
          return { raw: JSON.stringify(entries), format: "json" };
        }
        return null;
      }
      for (const child of Object.values(value)) if (child && typeof child === "object") stack.push(child);
    }
    return null;
  }
  const api = Object.freeze({ parseTranscript, seconds, stamp, annotation, markdownAnnotation, publicEpisode, MAX_TEXT });
  root.YTD_PODCAST = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
