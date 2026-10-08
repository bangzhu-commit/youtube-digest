const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const media = require("../media-platform.js");
const core = require("../podcast-core.js");
const ID = "aaaaaaaaaaaaaaaaaaaaaaaa";

test("routing recognizes individual episodes without admitting lookalike hosts", () => {
  const episode = media.identify(`https://www.xiaoyuzhoufm.com/episode/${ID}?s=share`);
  assert.equal(episode.id, ID);
  assert.equal(episode.panel, "podcast-panel.html");
  assert.equal(episode.url, `https://www.xiaoyuzhoufm.com/episode/${ID}`);
  assert.equal(media.identify(`https://xiaoyuzhoufm.com/episode/${ID}/`).id, ID);
  for (const url of ["https://www.youtube.com.attacker.example/watch?v=x", `https://xiaoyuzhoufm.com.attacker.example/episode/${ID}`, "https://www.xiaoyuzhoufm.com/podcast/x", `http://www.xiaoyuzhoufm.com/episode/${ID}`]) assert.equal(media.identify(url), null);
  assert.equal(media.identify("https://www.youtube.com/watch?v=video").panel, "sidepanel.html");
});

test("material parsing keeps speech, repair qualifiers and footnotes; excludes shownotes", () => {
  const raw = "# 标题\n\n## 节目简介\n这不是全文。\n\n## 转写全文\n\n### [00:00:00]\n\n**[00:00:00] Speaker-02**\n\n原话 1 0 1、3.14，不改写。\n\n第二段仍属于原说话人。\n\n**[00:01:05] 定点补齐**\n\n疑词[^u1]\n\n[^u1]: 这是未确认的猜测。\n";
  const result = core.parseTranscript(raw, "md");
  assert.equal(result.raw, raw);
  assert.equal(result.entries.length, 4);
  assert.equal(result.entries[0].start, 0);
  assert.equal(result.entries[0].speaker, "Speaker-02");
  assert.equal(result.entries[1].start, 0);
  assert.equal(result.entries[2].start, 65);
  assert.equal(result.entries[2].speaker, "定点补齐");
  assert.equal(result.entries[3].start, null);
  assert.equal(result.entries[3].speaker, "转写说明");
  assert.equal(result.entries.map(entry => entry.text).join("\n"), "原话 1 0 1、3.14，不改写。\n第二段仍属于原说话人。\n疑词[^u1]\n[^u1]: 这是未确认的猜测。");
});

test("plain inline timestamps do not discard the words after the timestamp", () => {
  const result = core.parseTranscript("[0:02] 这是原话。\n\n[1:03:04] 另一句。", "txt");
  assert.equal(result.entries[0].text, "这是原话。");
  assert.equal(result.entries[1].start, 3784);
});

test("SRT, VTT and JSON preserve fractional time and source copy", () => {
  const srt = "1\n00:01:02,250 --> 00:01:04,000\n第一句\n第二行\n\n2\n00:02:00,000 --> 00:02:01,000\n最后一句\n";
  const result = core.parseTranscript(srt, "srt");
  assert.equal(result.entries[0].start, 62.25);
  assert.equal(result.entries[0].text, "第一句\n第二行");
  assert.equal(result.entries[1].text, "最后一句");
  assert.equal(core.parseTranscript("WEBVTT\n\n00:01.500 --> 00:02.000\nhello", "vtt").entries[0].start, 1.5);
  assert.equal(core.parseTranscript(JSON.stringify([{start: 0.5, text: "正文"}]), "json").entries[0].start, 0.5);
  for (const raw of ['{"mediaId":"reference"}', '{"description":"简介"}', '[{"start":-1,"text":"正文"}]']) assert.throws(() => core.parseTranscript(raw, "json"));
});

test("public transcript extraction never treats a reference or another episode as full text", () => {
  assert.equal(core.publicEpisode({episode: {eid: ID, description: "简介", transcript: {mediaId: "ref"}}}, ID), null);
  assert.equal(core.publicEpisode({episode: {eid: "bbbbbbbbbbbbbbbbbbbbbbbb", transcript: "别集全文"}}, ID), null);
  assert.equal(core.publicEpisode({props: {episode: {eid: ID, transcript: "完整原话"}}}, ID).raw, "完整原话");
});

test("annotations keep the exact user's thought and quote, including multiline content", () => {
  const note = core.annotation({id: "test-note-01", quote: "原句。\n下一句", thought: "我还不确定。\n  保留缩进", start: null});
  assert.equal(note.thought, "我还不确定。\n  保留缩进");
  assert.match(core.markdownAnnotation(note), /> 原句。\n> 下一句/);
  assert.match(core.markdownAnnotation(note), /我还不确定。\n  保留缩进/);
  assert.throws(() => core.annotation({...note, thought: " "}));
  assert.throws(() => core.annotation({...note, start: NaN}));
});

test("player requests require the current episode identity before accessing audio", async () => {
  let listener, played = false;
  const player = {currentTime: 0, play: async () => { played = true; }};
  const sandbox = {YTD_MEDIA: media, YTD_PODCAST: core, location: {href: `https://www.xiaoyuzhoufm.com/episode/${ID}`}, chrome: {runtime: {onMessage: {addListener: fn => listener = fn}}}, document: {querySelector: selector => selector === "audio" ? player : null}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../podcast-content.js"), "utf8"), sandbox);
  assert.equal(listener({action:"podcastSeek", episodeId:"bbbbbbbbbbbbbbbbbbbbbbbb", seconds:42}, {}, () => {}), false);
  assert.equal(player.currentTime, 0);
  const reply = await new Promise(resolve => listener({action:"podcastSeek", episodeId:ID, seconds:42}, {}, resolve));
  assert.equal(reply.success, true); assert.equal(player.currentTime, 42); assert.equal(played, true);
});
