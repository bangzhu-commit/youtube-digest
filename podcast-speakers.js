/* Validate the separate audio-speaker overlay without changing source anchors. */
(function (root) {
  "use strict";
  const VERSION = "podcast-speakers-v1";
  const STATUSES = new Set(["single", "mixed", "overlap", "uncertain"]);
  const spoken = entry => entry.speaker !== "转写说明" && !entry.text.trimStart().startsWith("[^");
  function validate(data, transcript, sourceHash, episodeId) {
    const fail = () => { throw new Error("说话人版与当前原稿无法逐字对应，已保留原稿，请重新读取或识别。"); };
    if (!data || data.version !== VERSION || data.episodeId !== episodeId || data.sourceHash !== sourceHash || !/^[a-f0-9]{64}$/.test(sourceHash || "") || data.offsetUnit !== "unicode-code-point" || data.wordAlignment !== false || !transcript?.entries) fail();
    if (!Number.isFinite(data.duration) || data.duration <= 0 || data.duration > 28800 || !Array.isArray(data.speakers) || data.speakers.length > 50000 || !Array.isArray(data.segments) || !data.segments.length || data.segments.length > 50000) fail();
    const speakers = new Map();
    for (const speaker of data.speakers) {
      if (!speaker || !/^S[1-9]\d*$/.test(speaker.id) || speakers.has(speaker.id) || speaker.label !== `说话人 ${speaker.id.slice(1)}`) fail();
      speakers.set(speaker.id, speaker.label);
    }
    const entries = transcript.entries.filter(spoken);
    const source = new Map(entries.map((entry, index) => [entry.id, { chars: Array.from(entry.text), index }]));
    const offsets = new Map(), ids = new Set(); let lastIndex = -1, lastStart = -1;
    for (const segment of data.segments) {
      if (!segment || typeof segment.id !== "string" || !/^s-\d+$/.test(segment.id) || ids.has(segment.id)) fail();
      const entry = source.get(segment.sourceEntryId), left = segment.sourceStart, right = segment.sourceEnd;
      if (!entry || entry.index < lastIndex || !Number.isInteger(left) || !Number.isInteger(right) || left !== (offsets.get(segment.sourceEntryId) || 0) || right <= left || right > entry.chars.length || typeof segment.text !== "string" || entry.chars.slice(left, right).join("") !== segment.text) fail();
      if (!STATUSES.has(segment.status) || !Array.isArray(segment.speakerIds) || new Set(segment.speakerIds).size !== segment.speakerIds.length || segment.speakerIds.some(id => !speakers.has(id)) || segment.status === "single" && segment.speakerIds.length !== 1 || ["mixed", "overlap"].includes(segment.status) && segment.speakerIds.length < 2) fail();
      if (!speakers.size && (segment.status !== "uncertain" || segment.speakerIds.length)) fail();
      if (![segment.start, segment.end].every(value => Number.isFinite(value) && value >= 0 && value <= data.duration + 1) || segment.start > segment.end || segment.start < lastStart) fail();
      if (segment.sourceSpans && (segment.sourceSpans.length !== 1 || segment.sourceSpans[0]?.id !== segment.sourceEntryId || segment.sourceSpans[0]?.charStart !== left || segment.sourceSpans[0]?.charEnd !== right)) fail();
      ids.add(segment.id); offsets.set(segment.sourceEntryId, right); lastIndex = entry.index; lastStart = segment.start;
    }
    if (offsets.size !== source.size || entries.some(entry => offsets.get(entry.id) !== source.get(entry.id).chars.length)) fail();
    return data;
  }
  function label(segment, data) {
    const names = segment.speakerIds.map(id => data.speakers.find(speaker => speaker.id === id)?.label).filter(Boolean).join(" / ");
    if (segment.status === "single") return names;
    if (segment.status === "mixed") return `${names} · 片段内换人`;
    if (segment.status === "overlap") return `${names} · 声音重叠，文字归属不确定`;
    return names ? `${names} · 归属不确定` : "说话人不确定";
  }
  const api = Object.freeze({ VERSION, validate, label });
  root.YTD_SPEAKERS = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
