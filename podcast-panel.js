"use strict";
const $ = id => document.getElementById(id);
let episode = null, episodeTabId = null, panelWindowId = null, generation = 0;
let transcript = null, annotations = [], selected = null, title = "", channel = "";
let sourcePath = "", busy = false, activeView = "transcript";
let editorDraft = null, storageWrites = Promise.resolve(), draftSaveTimer;
let savingAnnotation = false;
let reading = null, readMode = "reading", stopRefine = false, refining = false;
const key = id => `podcast_digest_${id}`;
function status(message, error = false) { $("status").textContent = message; $("status").dataset.error = String(error); }
function button(label, onClick, className = "enhance-btn") {
  const element = document.createElement("button");
  element.type = "button"; element.className = className; element.textContent = label;
  element.addEventListener("click", onClick); return element;
}
function paragraph(text, className = "") {
  const element = document.createElement("p"); element.textContent = text; element.className = className; return element;
}
function controls() {
  for (const id of ["archiveBtn", "exportBtn", "analyzeBtn", "refineBtn"]) $(id).disabled = !transcript || busy;
  $("reloadBtn").disabled = busy;
  $("stopRefineBtn").hidden = !refining;
  $("stopRefineBtn").disabled = stopRefine;
  $("refineScope").disabled = busy; $("audioModel").disabled = busy;
  document.querySelectorAll("[data-audio-review]").forEach(element => { element.disabled = busy; });
}
function view(name) {
  activeView = name;
  document.querySelectorAll("[data-podcast-view]").forEach(element => { element.hidden = element.dataset.podcastView !== name; });
  document.querySelectorAll("[data-view]").forEach(element => { element.classList.toggle("active", element.dataset.view === name); });
}
async function persist(id = episode?.id, value = snapshot()) {
  if (!id) return;
  const write = storageWrites.catch(() => {}).then(() => chrome.storage.local.set({ [key(id)]: value }));
  storageWrites = write;
  await write;
}
function snapshot() { return { transcript, annotations, title, channel, sourcePath, editorDraft, reading, readMode, audioModel: $("audioModel").value }; }
function readingItem(entry) { return reading?.items.find(item => item.id === entry.id); }
function displayText(entry) { return readMode === "reading" ? readingItem(entry)?.text || entry.text : entry.text; }
function coverage() {
  const total = transcript?.entries.filter(YTD_READING.eligible).length || 0;
  const count = reading?.items.length || 0;
  $("readingStatus").textContent = total ? `已精校 ${count}/${total} 段。${count < total ? "待处理段落显示原稿；可继续生成。" : "文字精校完成。"}未标记听音的段落只做了文字审校。` : "先读取或导入全文，再生成精校阅读版。";
}
function mergeNotes(local, stored) {
  const notes = new Map(local.map(note => [note.id, note]));
  for (const note of stored) notes.set(note.id, { ...YTD_PODCAST.annotation(note), saved: true });
  return [...notes.values()];
}
async function native(action, data = {}) {
  return chrome.runtime.sendMessage({ action: "podcastNative", request: { action, episodeId: episode.id, ...data } });
}
async function seek(seconds) {
  if (seconds === null || !episode) return;
  const expected = episode.id;
  try {
    const tab = await chrome.tabs.get(episodeTabId);
    if (YTD_MEDIA.identify(tab.url)?.id !== expected) throw new Error("节目页面已切换，请重新载入。");
    const response = await chrome.tabs.sendMessage(tab.id, { action: "podcastSeek", episodeId: expected, seconds });
    if (!response?.success) throw new Error(response?.error || "请先在节目页点击播放。");
  } catch (error) { status(error.message || "无法连接网页播放器。", true); }
}
function sourceBody() {
  return transcript.entries.map(entry => `${entry.start === null ? "" : `**[${YTD_PODCAST.stamp(entry.start)}] ${entry.speaker}**\n\n`}${entry.text}`).join("\n\n");
}
function highlight(element, text, needle) {
  if (!needle) { element.textContent = text; return; }
  const lower = text.toLocaleLowerCase(), query = needle.toLocaleLowerCase();
  let cursor = 0, index;
  while ((index = lower.indexOf(query, cursor)) !== -1) {
    element.append(document.createTextNode(text.slice(cursor, index)));
    const mark = document.createElement("mark"); mark.textContent = text.slice(index, index + needle.length); element.append(mark);
    cursor = index + needle.length;
  }
  element.append(document.createTextNode(text.slice(cursor)));
}
function renderTranscript() {
  const list = $("transcriptList"); list.replaceChildren();
  $("emptyState").hidden = !!transcript;
  const query = $("searchInput").value.trim();
  coverage();
  const visible = (transcript?.entries || []).filter(entry => !query || displayText(entry).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  $("searchCount").textContent = query ? `${visible.length} 段` : "";
  for (const entry of visible) {
    const row = document.createElement("article"); row.className = "podcast-row"; row.dataset.entryId = entry.id;
    const header = document.createElement("div"); header.className = "podcast-row-header";
    const time = button(YTD_PODCAST.stamp(entry.start), () => seek(entry.start), "podcast-time"); time.disabled = entry.start === null;
    const speaker = paragraph(entry.speaker, "podcast-speaker");
    const item = readingItem(entry), textValue = displayText(entry);
    header.append(time, speaker);
    if (YTD_READING.eligible(entry) && entry.start !== null) {
      const listen = button("听音复核", () => listenEntry(entry)); listen.dataset.audioReview = ""; listen.disabled = busy; header.append(listen);
    }
    header.append(button("批注", () => openEditor(entry.text, entry.start, entry.id, readMode === "reading" && item ? item.text : undefined)));
    const text = paragraph("", "podcast-text"); highlight(text, textValue, query); row.append(header);
    if (readMode === "reading" && YTD_READING.eligible(entry)) row.append(paragraph(item ? item.audioEvidence ? "已复核音频片段 · 本地＋云端" : "阅读版 · 两轮文字精校" : "原始转写 · 待精校", "reading-badge"));
    row.append(text);
    if (readMode === "reading" && item) {
      if (item.issues.length) row.append(paragraph(item.issues.join("\n"), "reading-issues"));
      const details = document.createElement("details"); details.className = "source-compare";
      const summary = document.createElement("summary"); summary.textContent = "对照原始转写";
      details.append(summary, paragraph(entry.text, "podcast-text")); row.append(details);
    }
    for (const note of annotations.filter(note => note.entryId ? note.entryId === entry.id && entry.text.includes(note.quote) : entry.text.includes(note.quote) && note.start === entry.start)) {
      const thought = document.createElement("div"); thought.className = "podcast-inline-note";
      const label = document.createElement("small"); label.textContent = note.saved ? "我的感想 · 已归档" : "我的感想 · 草稿";
      thought.append(label, document.createTextNode(note.thought)); row.append(thought);
    }
    list.append(row);
  }
}
function renderAnnotations() {
  $("annotationCount").textContent = String(annotations.length);
  const list = $("annotationsList"); list.replaceChildren();
  if (!annotations.length) list.append(paragraph("在全文中选中一句话，或点击段落旁的“批注”，写下你的感想。", "podcast-empty"));
  for (const note of annotations) {
    const card = document.createElement("article"); card.className = "annotation-card";
    const header = document.createElement("div"); header.className = "section-header";
    const time = button(YTD_PODCAST.stamp(note.start), () => seek(note.start), "podcast-time"); time.disabled = note.start === null;
    header.append(time, paragraph(note.saved ? "已保存到 Obsidian" : "浏览器草稿", "annotation-label"));
    card.append(header);
    if (note.readingQuote) card.append(paragraph("阅读版摘句", "annotation-label"), paragraph(note.readingQuote, "annotation-quote"));
    card.append(paragraph("原稿出处", "annotation-label"), paragraph(note.quote, "annotation-quote"), paragraph("我的感想", "annotation-label"), paragraph(note.thought, "annotation-thought"));
    list.append(card);
  }
}
function render() {
  $("podcastTitle").textContent = title || "小宇宙阅读";
  $("podcastChannel").textContent = channel;
  renderTranscript(); renderAnnotations(); controls();
}
function closeEditor() { selected = null; editorDraft = null; $("annotationEditor").hidden = true; $("thoughtInput").value = ""; }
function openEditor(quote, start, entryId, readingQuote) {
  if (!episode) return;
  if (!$("annotationEditor").hidden && $("thoughtInput").value.trim()) {
    $("editorStatus").textContent = "请先保存或取消当前感想，再批注另一句。"; return;
  }
  selected = { quote, start, entryId, ...(readingQuote ? { readingQuote } : {}), episodeId: episode.id, generation };
  editorDraft = null;
  $("selectedQuote").textContent = readingQuote || quote; $("thoughtInput").value = "";
  $("editorStatus").textContent = readingQuote ? "将同时保存阅读版摘句、原稿出处和你的原话。" : "保存会保留你的原话。"; $("explanation").hidden = true;
  $("annotationEditor").hidden = false; $("thoughtInput").focus();
}
async function saveAnnotation() {
  const selectedNow = selected;
  if (busy) { $("editorStatus").textContent = "请等待当前操作完成后保存批注。"; return; }
  if (savingAnnotation || !selectedNow || selectedNow.episodeId !== episode?.id) return;
  const previous = annotations, previousDraft = editorDraft;
  savingAnnotation = true; $("saveAnnotationBtn").disabled = true;
  try {
    const note = { ...YTD_PODCAST.annotation({ ...selectedNow, id: crypto.randomUUID(), thought: $("thoughtInput").value }), entryId: selectedNow.entryId, saved: false };
    const next = [...annotations, note];
    annotations = next; editorDraft = null;
    clearTimeout(draftSaveTimer);
    await persist(episode.id, { ...snapshot(), annotations: next, editorDraft: null });
    if (selectedNow.generation !== generation) return;
    annotations = next; closeEditor(); render();
    status("批注草稿已保存在浏览器。点击“保存到 Obsidian”归档。");
  } catch (error) {
    if (selectedNow.generation === generation) {
      annotations = previous; editorDraft = previousDraft; $("editorStatus").textContent = error.message;
    }
  } finally { savingAnnotation = false; $("saveAnnotationBtn").disabled = false; }
}
async function explain() {
  const selectedNow = selected;
  if (!selectedNow) return;
  $("explainBtn").disabled = true; $("editorStatus").textContent = "正在解释原句…";
  try {
    const result = await chrome.runtime.sendMessage({ action: "explainSelection", selectedText: selectedNow.readingQuote || selectedNow.quote, transcriptContext: selectedNow.quote, videoTitle: title });
    if (!result?.success) throw new Error(result?.message || result?.error || "解释未完成。");
    if (selected !== selectedNow || selectedNow.generation !== generation) return;
    $("explanationText").textContent = result.explanation; $("explanation").hidden = false; $("editorStatus").textContent = "AI 解释不会写入你的感想。";
  } catch (error) { if (selected === selectedNow) $("editorStatus").textContent = error.message; }
  finally { $("explainBtn").disabled = false; }
}
async function loadVault() {
  if (!episode || busy) return;
  if (!$("annotationEditor").hidden && $("thoughtInput").value.trim()) { $("editorStatus").textContent = "请先保存当前感想。"; return; }
  const run = generation;
  busy = true; controls(); status("正在读取 Obsidian 素材库…");
  try {
    const result = await native("load");
    if (run !== generation) return;
    if (!result?.success) throw new Error(result?.error || "本机连接不可用。");
    if (result.found) {
      transcript = { ...YTD_PODCAST.parseTranscript(result.raw, result.format), format: result.format, origin: "vault" };
      title = result.title || title; sourcePath = result.sourcePath;
      if (result.readingManualEdited || result.readingInvalid || result.readingStale) reading = null;
      if (!YTD_READING.validReading(reading, transcript)) reading = null;
      if (result.reading && result.reading.sourceHash === await YTD_READING.fingerprint(transcript.raw)) {
        if (run !== generation) return;
        const stored = { ...result.reading, sourceRaw: transcript.raw };
        if (YTD_READING.validReading(stored, transcript)) {
          const items = new Map(stored.items.map(item => [item.id, item]));
          for (const item of reading?.items || []) {
            const prior = items.get(item.id);
            if (!prior || (item.updatedAt || "") > (prior.updatedAt || "")) items.set(item.id, item);
          }
          reading = { ...stored, items: [...items.values()] };
        }
      }
    }
    annotations = mergeNotes(annotations, result.annotations || []);
    await persist();
    if (run !== generation) return;
    render(); status(result.readingManualEdited ? "Obsidian 阅读版已有人工修改，已保留该文件；侧栏暂显示原稿。" : result.readingInvalid ? "阅读版数据格式有误，已保留文件；侧栏显示原稿，请在 Obsidian 核对。" : result.readingStale ? "原稿版本有变化，旧阅读版未自动套用。" : result.found ? "已读取素材库全文。选中原句即可写感想。" : "素材库里还没有这集全文。可导入已有文稿；本版不自动转写音频。");
  } catch (error) {
    if (run === generation) status(`${error.message} 可先导入全文、写批注并导出 Markdown。`, true);
  } finally { if (run === generation) { busy = false; controls(); } }
}
async function archive() {
  if (!episode || !transcript || busy) return;
  if (!$("annotationEditor").hidden && $("thoughtInput").value.trim()) { $("editorStatus").textContent = "请先保存当前感想，再归档。"; return; }
  const run = generation, id = episode.id, captured = snapshot();
  busy = true; controls(); status("正在保存原始素材与阅读批注…");
  try {
    const result = await native("save", { title, raw: transcript.raw, body: sourceBody(), annotations: annotations.map(YTD_PODCAST.annotation), ...(reading?.items.length ? { reading: { version: reading.version, sourceHash: reading.sourceHash, items: reading.items, total: transcript.entries.filter(YTD_READING.eligible).length } } : {}) });
    if (!result?.success) throw new Error(result?.error || "保存失败，浏览器草稿仍保留。");
    const savedIds = new Set(result.savedIds);
    await storageWrites.catch(() => {});
    const base = run === generation ? snapshot() : (await chrome.storage.local.get(key(id)))[key(id)] || captured;
    const next = { ...base, sourcePath: result.sourcePath, annotations: base.annotations.map(note => ({ ...note, saved: savedIds.has(note.id) || note.saved })) };
    await persist(id, next);
    if (run !== generation) return;
    sourcePath = next.sourcePath; annotations = next.annotations; render();
    status(`已保存${result.refinedPath ? "精校阅读版和批注" : "阅读批注"}。新增 ${result.added} 条批注。`);
  } catch (error) { if (run === generation) status(error.message, true); }
  finally { if (run === generation) { busy = false; controls(); } }
}
function download() {
  if (!transcript || !episode) return;
  const refined = reading?.items.length ? `## 精校阅读版（${reading.items.length}/${transcript.entries.filter(YTD_READING.eligible).length} 段）\n\n${transcript.entries.filter(YTD_READING.eligible).map(entry => { const item = readingItem(entry); return `### ${YTD_PODCAST.stamp(entry.start)} ${entry.speaker}\n\n${item?.text || entry.text}\n\n${item ? item.audioEvidence ? "*已复核音频片段*" : "*文字精校*" : "*原稿，待精校*"}${item?.issues.length ? `\n\n疑点：${item.issues.join("；")}` : ""}`; }).join("\n\n")}\n\n` : "";
  const content = `# ${title}｜阅读与批注\n\n来源：${episode.url}\n\n${refined}## 原文\n\n${sourcePath ? `![[${sourcePath}]]` : sourceBody()}\n\n## 我的批注\n\n${annotations.map(YTD_PODCAST.markdownAnnotation).join("\n")}`;
  const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `小宇宙-${episode.id}-阅读批注.md`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function analyze() {
  if (!transcript || busy) return;
  const run = generation;
  busy = true; controls(); status("正在根据全文生成 AI 概览…");
  try {
    const result = await chrome.runtime.sendMessage({ action: "analyzeTranscript", transcriptText: transcript.entries.map(entry => `[${YTD_PODCAST.stamp(entry.start)}] ${entry.text}`).join("\n"), videoTitle: title, channelName: channel, videoDuration: Math.max(0, ...transcript.entries.map(entry => entry.start || 0)) });
    if (run !== generation) return;
    if (!result?.success) throw new Error(result?.message || result?.error || "概览生成失败。");
    const list = $("overview"); list.replaceChildren();
    for (const chapter of result.analysis.chapters || []) {
      const section = document.createElement("article"); section.className = "podcast-chapter";
      section.append(button(chapter.timestamp, () => seek(chapter.timestampSeconds), "podcast-time"));
      const heading = document.createElement("h3"); heading.textContent = chapter.title; section.append(heading, paragraph(chapter.summary)); list.append(section);
    }
    status("AI 概览已生成。全文与个人感想保持原样。");
  } catch (error) { if (run === generation) status(error.message, true); }
  finally { if (run === generation) { busy = false; controls(); } }
}
function neighborContext(entries, batch) {
  const first = entries.indexOf(batch[0]), last = entries.indexOf(batch.at(-1));
  return { before: entries.slice(Math.max(0, first - 1), first).map(entry => entry.text).join("\n").slice(-2000), after: entries.slice(last + 1, last + 2).map(entry => entry.text).join("\n").slice(0, 2000) };
}
function terminology() { return transcript.raw.split(/^##\s+(?:转写全文|逐字稿|全文|Transcript)\s*$/mi)[0].slice(0, 3000); }
async function prepareReading() {
  if (!YTD_READING.validReading(reading, transcript)) {
    const raw = transcript.raw;
    const hash = await YTD_READING.fingerprint(raw);
    if (transcript?.raw !== raw) throw new Error("原稿已切换，请重新生成。");
    reading = { version: YTD_READING.VERSION, sourceRaw: raw, sourceHash: hash, items: [] };
  }
}
function acceptReading(result, batch) {
  const aligned = YTD_READING.align(result, batch, !!result.evidence);
  const items = new Map(reading.items.map(item => [item.id, item]));
  for (const value of aligned) {
    const entry = batch.find(entry => entry.id === value.id);
    items.set(value.id, { ...value, sourceText: entry.text, start: entry.start, speaker: entry.speaker, provider: result.provider, model: result.model, updatedAt: new Date().toISOString(), ...(result.evidence ? { audioEvidence: result.evidence } : {}) });
  }
  reading = { ...reading, items: [...items.values()].sort((a, b) => transcript.entries.findIndex(entry => entry.id === a.id) - transcript.entries.findIndex(entry => entry.id === b.id)) };
}
async function refine() {
  if (!transcript || busy || !episode) return;
  const run = generation, raw = transcript.raw;
  busy = true; refining = true; stopRefine = false; controls(); renderTranscript();
  try {
    await prepareReading();
    if (run !== generation) return;
    const entries = transcript.entries.filter(YTD_READING.eligible);
    const candidates = entries.filter(entry => $("refineScope").value === "all" || entry.start !== null && entry.start < 300);
    if (!candidates.length) throw new Error("无法确定前 5 分钟，请选择精校全文。");
    const pending = candidates.filter(entry => !readingItem(entry));
    const batches = YTD_READING.batches(pending);
    for (let i = 0; i < batches.length; i++) {
      if (stopRefine) break;
      const batch = batches[i];
      status(`正在精校第 ${i + 1}/${batches.length} 批：初稿与对照审校。进度逐批保存在浏览器。`);
      const result = await chrome.runtime.sendMessage({ action: "podcastRefine", segments: batch.map(({ id, text }) => ({ id, text })), context: neighborContext(entries, batch), title, terminology: terminology() });
      if (run !== generation || transcript.raw !== raw) return;
      if (!result?.success) throw new Error(result?.error || "本批精校失败。");
      acceptReading(result, batch); readMode = "reading"; $("readMode").value = readMode; await persist();
      if (run !== generation) return;
      renderTranscript();
    }
    if (run === generation) status(stopRefine ? "已停止，已完成的段落保留；再次生成会接着处理。" : "所选范围已精校。可对照原稿，或保存到 Obsidian。");
  } catch (error) { if (run === generation) status(`${error.message} 已完成段落保留，再次生成可继续。`, true); }
  finally { if (run === generation) { busy = false; refining = false; controls(); renderTranscript(); } }
}
async function listenEntry(entry) {
  if (!transcript || busy || !episode) return;
  const run = generation, raw = transcript.raw;
  busy = true; controls(); renderTranscript();
  status(`正在复核 ${YTD_PODCAST.stamp(entry.start)} 的音频片段：本地转写、云端听音、对照审校。`);
  try {
    await prepareReading();
    if (run !== generation) return;
    const entries = transcript.entries.filter(YTD_READING.eligible), clip = YTD_READING.audioWindow(entries, entry);
    const result = await chrome.runtime.sendMessage({ action: "podcastListen", episodeId: episode.id, segments: [{ id: entry.id, text: entry.text }], context: neighborContext(entries, [entry]), title, terminology: terminology(), clip, audioModel: $("audioModel").value || YTD_READING.AUDIO_MODELS[0] });
    if (run !== generation || transcript.raw !== raw) return;
    if (!result?.success) throw new Error(result?.error || "听音复核失败。");
    if (!result.evidence || !Number.isFinite(result.evidence.end)) throw new Error("缺少实际听音证据，未采用结果。");
    const next = entries.find(item => item.start > entry.start);
    if (clip.estimated) {
      for (const item of result.segments) item.issues = [...item.issues.slice(0, 19), "原稿多段共用时间戳，听音窗口按文字比例预估；请对照两份听音候选确认覆盖。"];
    }
    if (!next || next.start + 2 > result.evidence.end) {
      for (const item of result.segments) {
        const issue = `本次只听取 ${YTD_PODCAST.stamp(result.evidence.start)}–${YTD_PODCAST.stamp(result.evidence.end)}；超出片段的内容未听音核验。`;
        item.issues = [...item.issues.slice(0, 19), issue];
      }
    }
    acceptReading(result, [entry]); readMode = "reading"; $("readMode").value = readMode; await persist();
    if (run === generation) { renderTranscript(); status("音频片段复核完成。疑点与候选记录会随阅读版归档。"); }
  } catch (error) { if (run === generation) status(`${error.message} 原稿与已有阅读版保留。`, true); }
  finally { if (run === generation) { busy = false; controls(); renderTranscript(); } }
}
async function initialize(tab) {
  const media = YTD_MEDIA.identify(tab?.url);
  if (media?.platform !== "xiaoyuzhou") { window.close(); return; }
  if (episode?.id === media.id && episodeTabId === tab.id) return;
  if (episode) { clearTimeout(draftSaveTimer); void persist().catch(() => {}); }
  const run = ++generation;
  episode = media; episodeTabId = tab.id; panelWindowId = tab.windowId; busy = false; refining = false; stopRefine = true;
  transcript = null; annotations = []; sourcePath = ""; title = "小宇宙节目"; channel = "";
  reading = null; readMode = "reading"; $("readMode").value = readMode; $("audioModel").value = YTD_READING.AUDIO_MODELS[0];
  closeEditor(); $("overview").replaceChildren(); $("searchInput").value = ""; render();
  try {
    const cached = (await chrome.storage.local.get(key(media.id)))[key(media.id)];
    if (run !== generation) return;
    if (cached?.transcript?.raw) {
      transcript = { ...YTD_PODCAST.parseTranscript(cached.transcript.raw, cached.transcript.format), format: cached.transcript.format, origin: cached.transcript.origin };
      if (YTD_READING.validReading(cached.reading, transcript)) reading = cached.reading;
      readMode = cached.readMode === "source" ? "source" : "reading"; $("readMode").value = readMode;
      if (YTD_READING.AUDIO_MODELS.includes(cached.audioModel)) $("audioModel").value = cached.audioModel;
      annotations = (cached.annotations || []).map(note => ({ ...YTD_PODCAST.annotation(note), entryId: note.entryId, saved: !!note.saved }));
      title = cached.title || title; channel = cached.channel || ""; sourcePath = cached.sourcePath || ""; render();
    }
    editorDraft = cached?.editorDraft || null;
    let info;
    try { info = await chrome.tabs.sendMessage(tab.id, { action: "podcastInfo", episodeId: media.id }); } catch {}
    if (run !== generation) return;
    title = info?.title || title; channel = info?.channelName || channel; render();
    if (!transcript && info?.transcript) {
      transcript = { ...YTD_PODCAST.parseTranscript(info.transcript.raw, info.transcript.format), format: info.transcript.format, origin: "public" }; render();
    }
    await loadVault();
    if (run !== generation) return;
    if (cached?.editorDraft?.thought) {
      const draft = cached.editorDraft;
      openEditor(draft.quote, draft.start, draft.entryId, draft.readingQuote);
      editorDraft = draft; $("thoughtInput").value = draft.thought;
      $("editorStatus").textContent = "已恢复上次未提交的感想。";
    }
  } catch (error) { if (run === generation) status(error.message, true); }
}
document.querySelectorAll("[data-view]").forEach(element => element.addEventListener("click", () => view(element.dataset.view)));
$("reloadBtn").addEventListener("click", loadVault);
$("archiveBtn").addEventListener("click", archive);
$("exportBtn").addEventListener("click", download);
$("analyzeBtn").addEventListener("click", analyze);
$("refineBtn").addEventListener("click", refine);
$("stopRefineBtn").addEventListener("click", () => { stopRefine = true; $("stopRefineBtn").disabled = true; });
$("readMode").addEventListener("change", () => { readMode = $("readMode").value; renderTranscript(); void persist().catch(error => status(error.message, true)); });
$("audioModel").addEventListener("change", () => { void persist().catch(error => status(error.message, true)); });
$("settingsBtn").addEventListener("click", () => chrome.runtime.sendMessage({ action: "openOptions" }));
$("cancelAnnotationBtn").addEventListener("click", () => { closeEditor(); void persist().catch(error => status(error.message, true)); });
$("saveAnnotationBtn").addEventListener("click", saveAnnotation);
$("explainBtn").addEventListener("click", explain);
$("searchInput").addEventListener("input", renderTranscript);
$("thoughtInput").addEventListener("input", () => {
  if (!selected) return;
  editorDraft = { ...selected, thought: $("thoughtInput").value };
  const id = episode.id, captured = snapshot();
  clearTimeout(draftSaveTimer);
  draftSaveTimer = setTimeout(() => persist(id, captured).catch(error => status(`草稿未保存：${error.message}`, true)), 250);
});
window.addEventListener("pagehide", () => { clearTimeout(draftSaveTimer); void persist().catch(() => {}); });
$("importFile").addEventListener("change", async event => {
  const file = event.target.files[0], run = generation;
  if (!file || !episode) return;
  try {
    if (busy || $("thoughtInput").value.trim()) throw new Error("请先完成当前操作或保存感想。");
    if (file.size > 950000) throw new Error("文稿超过 950 KB，请分段导入。");
    const raw = await file.text();
    if (run !== generation) return;
    const format = file.name.split(".").at(-1).toLowerCase();
    const next = { ...YTD_PODCAST.parseTranscript(raw, format), format, origin: "import" };
    await persist(episode.id, { ...snapshot(), transcript: next, sourcePath: "", reading: null });
    if (run !== generation) return;
    transcript = next; sourcePath = ""; reading = null; closeEditor(); render(); status(`已导入 ${transcript.entries.length} 段全文。`);
  } catch (error) { status(error.message, true); }
  finally { event.target.value = ""; }
});
$("transcriptList").addEventListener("mouseup", event => {
  if (event.target.closest("button")) return;
  const selection = window.getSelection();
  const quote = selection?.toString().trim();
  const node = selection?.anchorNode?.parentElement?.closest(".podcast-row");
  const end = selection?.focusNode?.parentElement?.closest(".podcast-row");
  if (!quote || !node || node !== end || !node.contains(selection.anchorNode)) return;
  const entry = transcript?.entries.find(entry => entry.id === node.dataset.entryId);
  if (entry && displayText(entry).includes(quote)) {
    const processed = readMode === "reading" && readingItem(entry);
    openEditor(processed ? entry.text : quote, entry.start, entry.id, processed ? quote : undefined);
  } else if (entry?.text.includes(quote)) openEditor(quote, entry.start, entry.id);
});
chrome.tabs.onActivated.addListener(async ({ tabId, windowId }) => {
  if (panelWindowId !== null && windowId !== panelWindowId) return;
  try { await initialize(await chrome.tabs.get(tabId)); } catch {}
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (!tab.active || panelWindowId !== null && tab.windowId !== panelWindowId) return;
  if (change.url || change.status === "complete") void initialize(tab);
});
chrome.tabs.query({ active: true, currentWindow: true }).then(tabs => initialize(tabs[0])).catch(error => status(error.message, true));
