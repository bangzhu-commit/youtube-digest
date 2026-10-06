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
