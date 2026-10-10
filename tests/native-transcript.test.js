const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

function fixture({ videoId = "ydTeb_I0b94", tracks = [], rows = [], fetchImpl = () => { throw new Error("unexpected fetch"); } } = {}) {
  const response = { videoDetails: { videoId }, captions: { playerCaptionsTracklistRenderer: { captionTracks: tracks } } };
  const sandbox = {
    URL, AbortSignal, location: { href: `https://www.youtube.com/watch?v=${videoId}` }, window: {},
    setTimeout: (callback) => { callback(); return 0; }, fetch: fetchImpl,
    document: {
      getElementById: () => ({ getPlayerResponse: () => response }),
      querySelector: (selector) => selector.startsWith("ytd-engagement") ? {
        querySelectorAll: () => rows.map((row) => ({ querySelector: (key) => ({ textContent: key === ".segment-timestamp" ? row.stamp : row.text }) })),
      } : null,
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../native-transcript.js"), "utf8"), sandbox);
  return sandbox.readNativeTranscript;
}

test("current-video captions preserve timestamp and language without API keys", async () => {
  let fetched;
  const read = fixture({ tracks: [{ languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94" }], fetchImpl: async (url) => {
    fetched = url;
    return { ok: true, json: async () => ({ events: [{ tStartMs: 1250, dDurationMs: 2200, segs: [{ utf8: "Hello\n" }, { utf8: "world" }] }] }) };
  } });
  const result = await read("ydTeb_I0b94");
  assert.equal(result.success, true);
  assert.equal(result.transcript[0].start, 1.25);
  assert.equal(result.transcript[0].duration, 2.2);
  assert.equal(result.language, "en");
  assert.equal(result.transcriptTextTimestamped, "[0:01] Hello world");
  assert.equal(fetched, "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94");
});

test("Chinese captions win over English dubbed ASR and preserve spoken wording", async () => {
  const fetched = [];
  const read = fixture({ tracks: [
    { languageCode: "en", kind: "asr", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=en" },
    { languageCode: "zh-Hant", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=zh-Hant" },
    { languageCode: "zh-Hans", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=zh-Hans" },
  ], fetchImpl: async (url) => {
    fetched.push(url);
    return { ok: true, json: async () => ({ events: [{ tStartMs: 1600, segs: [{ utf8: "大家好呀，咱们今天接着说。" }] }] }) };
  } });
  const result = await read("ydTeb_I0b94");
  assert.equal(result.language, "zh-Hans");
  assert.equal(result.transcriptText, "大家好呀，咱们今天接着说。");
  assert.equal(result.transcript[0].start, 1.6);
  assert.equal(fetched.length, 1);
  assert.equal(new URL(fetched[0]).searchParams.get("lang"), "zh-Hans");
  assert.equal(new URL(fetched[0]).searchParams.has("tlang"), false);
});

test("manual Traditional Chinese wins over simplified ASR", async () => {
  const read = fixture({ tracks: [
    { languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=en" },
    { languageCode: "zh-Hans", kind: "asr", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=zh-Hans" },
    { languageCode: "zh-Hant", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=zh-Hant" },
  ], fetchImpl: async () => ({ ok: true, json: async () => ({ events: [{ tStartMs: 0, segs: [{ utf8: "咱們接著說。" }] }] }) }) });
  const result = await read("ydTeb_I0b94");
  assert.equal(result.language, "zh-Hant");
  assert.equal(result.transcriptText, "咱們接著說。");
});

test("empty preferred Chinese tracks still allow a readable original English track", async () => {
  const read = fixture({ tracks: [
    { languageCode: "zh-Hans", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=zh-Hans" },
    { languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94&lang=en" },
  ], fetchImpl: async (url) => ({ ok: true, json: async () => ({ events: new URL(url).searchParams.get("lang") === "en"
    ? [{ tStartMs: 0, segs: [{ utf8: "Original English speech." }] }] : [] }) }) });
  const result = await read("ydTeb_I0b94");
  assert.equal(result.language, "en");
  assert.equal(result.transcriptText, "Original English speech.");
});

test("empty caption responses fall back to the full built-in transcript", async () => {
  const read = fixture({ tracks: [{ baseUrl: "https://www.youtube.com/api/timedtext?v=ydTeb_I0b94" }], fetchImpl: async () => ({ ok: true, json: async () => ({ events: [] }) }), rows: [{ stamp: "1:02:03", text: "first" }, { stamp: "1:02:08", text: "next" }] });
  const result = await read("ydTeb_I0b94");
  assert.equal(result.source, "youtube-transcript");
  assert.equal(result.transcript[0].start, 3723);
  assert.equal(result.transcript[0].duration, 5);
  assert.equal(result.transcript.length, 2);
});

test("subtitle URLs cannot fetch an arbitrary third-party origin", async () => {
  const read = fixture({ tracks: [{ baseUrl: "https://attacker.example/api/timedtext" }], rows: [{ stamp: "0:00", text: "safe" }] });
  assert.equal((await read("ydTeb_I0b94")).transcriptText, "safe");
});

test("navigation invalidates results before reading a different video", async () => {
  assert.equal((await fixture()("another_id")).error, "VIDEO_CHANGED");
});

test("missing captions reports unavailability without paid transcription", async () => {
  const result = await fixture()("ydTeb_I0b94");
  assert.equal(result.success, false);
  assert.equal(result.error, "NATIVE_TRANSCRIPT_UNAVAILABLE");
});
