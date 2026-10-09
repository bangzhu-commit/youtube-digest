const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const reading = require('../podcast-reading.js');
const core = require('../podcast-core.js');
const segments = [{ id: 'p-0', text: '我感觉，嗯，这个模型可能是 2.5 版本，我不知道它的效果。' }];
const polished = { segments: [{ id: 'p-0', text: '我感觉这个模型可能是 2.5 版本，我不知道它的效果。', issues: [] }] };

test('source alignment rejects missing, duplicate IDs, silent number changes and summaries', () => {
  assert.equal(reading.align(polished, segments)[0].text, polished.segments[0].text);
  assert.throws(() => reading.align({ segments: [] }, segments), /缺少段落/);
  assert.throws(() => reading.align({ segments: [{ ...polished.segments[0], id: 'other' }] }, segments), /编号/);
  assert.throws(() => reading.align({ segments: [{ ...polished.segments[0], text: polished.segments[0].text.replace('2.5', '3') }] }, segments), /数字/);
  assert.throws(() => reading.align({ segments: [{ ...polished.segments[0], text: '2.5 版本' }] }, segments), /摘要/);
  assert.deepEqual(reading.numbers('模型 2. 5 和 1 0 0[^3]'), ['100', '2.5']);
});

test('audio windows cover equal-timestamp paragraphs and never exceed ninety seconds', () => {
  const entries = [{ start: 10 }, { start: 10 }, { start: 200 }];
  assert.deepEqual(reading.audioWindow(entries, entries[0]), { start: 8, end: 98, estimated: true });
  assert.deepEqual(reading.audioWindow(entries, entries[1]), { start: 97, end: 187, estimated: true });
  assert.throws(() => reading.audioWindow([], { start: null }), /时间戳/);
});

test('source uncertainty footnotes cannot disappear silently during polishing', () => {
  const source = [{ id: 'p-0', text: '这个词是真流[^u1]，可能涉及数据使用。' }];
  const result = { segments: [{ id: 'p-0', text: '这个词是真流，可能涉及数据使用。', issues: [] }] };
  assert.match(reading.align(result, source)[0].issues[0], /\[\^u1\]/);
});

test('cached reading is invalidated by source changes, duplicate IDs or malformed text', () => {
  const transcript = core.parseTranscript('[0:02] 原稿');
  const item = { id: 'p-0', sourceText: '原稿', text: '整理稿', start: 2, issues: [] };
  const cached = { version: reading.VERSION, sourceHash: 'a'.repeat(64), sourceRaw: transcript.raw, items: [item] };
  assert.equal(reading.validReading(cached, transcript), true);
  assert.equal(reading.validReading(cached, core.parseTranscript('[0:02] 新原稿')), false);
  assert.equal(reading.validReading({ ...cached, items: [item, item] }, transcript), false);
  assert.equal(reading.validReading({ ...cached, items: [{ ...item, text: '' }] }, transcript), false);
});

function background({ provider = 'openrouter', native = async () => ({ success: true, audio: 'YXVkaW8=', localText: segments[0].text, localModel: 'faster-whisper/medium/int8', start: 0, end: 40 }), responses = [polished, polished] } = {}) {
  const calls = [], nativeCalls = [], listeners = { addListener() {} };
  let listener;
  const settings = { provider, aiApiKey: 'test-key', aiBaseUrl: 'https://openrouter.ai/api/v1', aiModel: 'text-model' };
  const root = path.join(__dirname, '..');
  const sandbox = {
    console, URL, TextEncoder, TextDecoder, AbortController, setTimeout: () => 0, clearTimeout() {}, importScripts() {},
    YTD_MEDIA: require('../media-platform.js'), YTD_READING: reading,
    YTD_SETTINGS: { STORAGE_KEY: 'ytd_settings', normalize: value => value, chatCompletionsUrl: () => 'https://openrouter.ai/api/v1/chat/completions', completionBody: require('../settings.js').completionBody },
    fetch: async (url, options) => {
      if (url.startsWith('chrome-extension:')) return { ok: true, text: async () => fs.readFileSync(path.join(root, url.split('/').slice(3).join('/')), 'utf8') };
      calls.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(responses.shift()) } }] }));
    },
    chrome: {
      storage: { local: { setAccessLevel: async () => {}, get: async () => ({ ytd_settings: settings }) } },
      action: { onClicked: listeners }, tabs: { onUpdated: listeners, onActivated: listeners }, sidePanel: { setPanelBehavior() {} },
      runtime: { id: 'test', getURL: file => `chrome-extension://test/${file}`, onInstalled: listeners, onMessage: { addListener: fn => listener = fn }, sendNativeMessage: async (_, request) => { nativeCalls.push(request); return native(request); } },
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'background.js'), 'utf8'), sandbox);
  return { api: sandbox.__YTD_TRANSLATION_TESTING__, calls, nativeCalls, invoke: (request, sender) => new Promise(resolve => listener(request, sender, resolve)) };
}

test('podcast polish makes separate draft and source comparison calls', async () => {
  const b = background();
  const result = await b.api.handlePodcastReading({ action: 'podcastRefine', segments, context: { before: '', after: '' } });
  assert.equal(result.success, true); assert.equal(b.calls.length, 2);
  const review = JSON.parse(b.calls[1].messages[1].content);
  assert.equal(review.source.segments[0].text, segments[0].text);
  assert.equal(review.draft.segments[0].text, polished.segments[0].text);
  assert.equal(result.evidence, undefined); assert.equal(b.nativeCalls.length, 0);
});

test('cloud listening sends actual audio and local candidate, then separately reviews against source', async () => {
  const heard = { ...polished, heardText: '我感觉这个模型可能是二点五版本，我不知道它的效果。' };
  const b = background({ responses: [heard, polished] });
  const result = await b.api.handlePodcastReading({ action: 'podcastListen', episodeId: 'a'.repeat(24), segments, clip: { start: 0, end: 40 }, audioModel: reading.AUDIO_MODELS[0] });
  assert.equal(b.nativeCalls.length, 1); assert.equal(b.calls.length, 2);
  assert.equal(b.calls[0].model, reading.AUDIO_MODELS[0]);
  assert.equal(b.calls[0].messages[1].content[1].input_audio.data, 'YXVkaW8=');
  assert.equal(JSON.parse(b.calls[0].messages[1].content[0].text).localCandidate, segments[0].text);
  assert.equal(b.calls[1].model, 'text-model');
  assert.equal(result.evidence.heardText, heard.heardText);
});

test('unsupported providers and failed local recognition never send audio to the cloud', async () => {
  const request = { action: 'podcastListen', episodeId: 'a'.repeat(24), segments, clip: { start: 0, end: 40 }, audioModel: reading.AUDIO_MODELS[0] };
  const unsupported = background({ provider: 'deepseek' });
  await assert.rejects(unsupported.api.handlePodcastReading(request), /OpenRouter/);
  assert.equal(unsupported.nativeCalls.length, 0); assert.equal(unsupported.calls.length, 0);
  const failed = background({ native: async () => ({ success: false, error: '模型未缓存' }) });
  await assert.rejects(failed.api.handlePodcastReading(request), /未缓存/);
  assert.equal(failed.calls.length, 0);
});

test('website and content-script senders cannot trigger model or local audio processing', async () => {
  const b = background();
  const result = await b.invoke({ action: 'podcastListen', segments }, { id: 'test', url: 'https://www.xiaoyuzhoufm.com/episode/' + 'a'.repeat(24) });
  assert.equal(result.success, false); assert.equal(b.calls.length, 0); assert.equal(b.nativeCalls.length, 0);
});
