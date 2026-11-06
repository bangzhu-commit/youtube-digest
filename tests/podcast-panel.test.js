const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID, webcrypto } = require('node:crypto');
const media = require('../media-platform.js');
const core = require('../podcast-core.js');
const A = 'a'.repeat(24), B = 'b'.repeat(24);
const source = fs.readFileSync(path.join(__dirname, '../podcast-panel.js'), 'utf8');
const tab = id => ({ id: id === A ? 1 : 2, windowId: 9, active: true, url: `https://www.xiaoyuzhoufm.com/episode/${id}` });
const loaded = id => ({ success: true, found: true, raw: `# ${id}\n\n## 转写全文\n\n[0:02] ${id}原话`, format: 'md', title: id, sourcePath: `知识库/原始素材库/${id}.md`, annotations: [] });

class Element {
  constructor() { this.listeners = {}; this.children = []; this.dataset = {}; this.value = ''; this.textContent = ''; this.hidden = false; this.classList = { toggle() {} }; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  focus() {}
}

function panel({ cache = {}, load = async id => loaded(id), write = async () => {}, request = async () => ({}), nativeRequest = async () => ({ success: true, job: { status: 'idle', progress: 0 } }), config = { provider: 'codex', aiModel: '', hasAiKey: true } } = {}) {
  const elements = new Map(), windowListeners = {}, timers = new Map(); let timerId = 0;
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const audioButtons = () => {
    const found = new Set();
    const visit = item => {
      if (item.dataset?.audioReview !== undefined) found.add(item);
      for (const child of item.children || []) visit(child);
    };
    for (const root of elements.values()) visit(root);
    return [...found];
  };
  element('annotationEditor').hidden = true;
  const sandbox = {
    console, URL, Blob, TextEncoder, crypto: { randomUUID, subtle: webcrypto.subtle }, setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id), YTD_MEDIA: media, YTD_PODCAST: core, YTD_READING: require('../podcast-reading.js'),
    document: { getElementById: element, querySelectorAll: selector => selector === '[data-audio-review]' ? audioButtons() : [], createElement: () => new Element(), createTextNode: text => ({ textContent: text }) },
    window: { addEventListener: (name, fn) => windowListeners[name] = fn, close() {} },
    chrome: {
      storage: { local: { get: async key => structuredClone({ [key]: cache[key] }), set: async values => { await write(values); Object.assign(cache, structuredClone(values)); } } },
      runtime: { sendMessage: async message => message.action === 'podcastNative' ? message.request.action === 'load' ? load(message.request.episodeId) : nativeRequest(message.request) : message.action === 'checkConfig' ? config : request(message) },
      tabs: { query: () => new Promise(() => {}), sendMessage: async () => ({}), onActivated: { addListener() {} }, onUpdated: { addListener() {} } },
    },
  };
  vm.createContext(sandbox); vm.runInContext(source, sandbox);
  return { sandbox, element, cache, windowListeners, audioButtons, timers, flush: () => vm.runInContext('storageWrites', sandbox) };
}

test('audio review becomes clickable after a vault read and recovers after a failed reread', async () => {
  let rereading = false, release, entered;
  const waiting = new Promise(resolve => entered = resolve);
  const p = panel({ load: async id => {
    if (!rereading) return loaded(id);
    return new Promise(resolve => { release = resolve; entered(); });
  } });
  await p.sandbox.initialize(tab(A));
  assert.equal(p.audioButtons().length, 1);
  assert.equal(p.audioButtons()[0].disabled, false);
  rereading = true;
  const reread = p.sandbox.loadVault(); await waiting;
  assert.equal(p.audioButtons()[0].disabled, true);
  release({ success: false, error: 'temporary bridge failure' }); await reread;
  assert.equal(p.audioButtons()[0].disabled, false);
  assert.match(p.element('status').textContent, /temporary bridge failure/);
});

test('a late source read from another episode cannot replace the current text or title', async () => {
  let releaseA, receivedA;
  const entered = new Promise(resolve => receivedA = resolve);
  const p = panel({ load: id => id === A ? new Promise(resolve => { releaseA = resolve; receivedA(); }) : Promise.resolve(loaded(B)) });
  const old = p.sandbox.initialize(tab(A)); await entered;
  await p.sandbox.initialize(tab(B)); releaseA(loaded(A)); await old;
  assert.equal(p.element('podcastTitle').textContent, B);
  assert.equal(p.cache[`podcast_digest_${B}`].transcript.entries[0].text, B + '原话');
  assert.equal(p.cache[`podcast_digest_${A}`].transcript, null);
});

test('double-clicking save while storage is pending records one note for the original episode', async () => {
  let release, entered;
  const waiting = new Promise(resolve => entered = resolve);
  let held = false;
  const p = panel({ write: values => {
    if (!held && values[`podcast_digest_${A}`]?.annotations.length === 1) {
      held = true; entered(); return new Promise(resolve => release = resolve);
    }
  } });
  await p.sandbox.initialize(tab(A)); p.sandbox.openEditor(A + '原话', 2, 'p-0');
  p.element('thoughtInput').value = '我还不确定。\n  保留原话';
  const first = p.sandbox.saveAnnotation(); await waiting;
  await p.sandbox.saveAnnotation(); const switched = p.sandbox.initialize(tab(B));
  release(); await first; await switched; await p.flush();
  assert.equal(p.cache[`podcast_digest_${A}`].annotations.length, 1);
  assert.equal(p.cache[`podcast_digest_${A}`].annotations[0].thought, '我还不确定。\n  保留原话');
  assert.equal(p.cache[`podcast_digest_${B}`].annotations.length, 0);
  assert.equal(p.element('podcastTitle').textContent, B);
});

test('an unfinished thought survives panel closure and reopens beside its original quote', async () => {
  const p = panel(); await p.sandbox.initialize(tab(A)); p.sandbox.openEditor(A + '原话', 2, 'p-0');
  p.element('thoughtInput').value = '尚未提交的想法\n'; p.element('thoughtInput').listeners.input();
  p.windowListeners.pagehide(); await p.flush();
  const restored = panel({ cache: p.cache }); await restored.sandbox.initialize(tab(A));
  assert.equal(restored.element('thoughtInput').value, '尚未提交的想法\n');
  assert.equal(restored.element('selectedQuote').textContent, A + '原话');
  assert.equal(restored.element('annotationEditor').hidden, false);
  assert.equal(restored.cache[`podcast_digest_${A}`].annotations.length, 0);
});

test('the file bridge rejects website/content-script senders and invalid requests before contacting the host', async () => {
  let messageListener, nativeCalls = 0;
  const listener = { addListener() {} };
  const sandbox = {
    console, URL, TextEncoder, setTimeout, clearTimeout, importScripts() {}, YTD_MEDIA: media,
    chrome: {
      storage: { local: { setAccessLevel: async () => {} } }, action: { onClicked: listener },
      tabs: { onUpdated: listener, onActivated: listener }, sidePanel: { setPanelBehavior() {} },
      runtime: { id: 'test', onInstalled: listener, onMessage: { addListener: fn => messageListener = fn }, getURL: file => `chrome-extension://test/${file}`, sendNativeMessage: async () => { nativeCalls++; return { success: true }; } },
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../background.js'), 'utf8'), sandbox);
  const message = { action: 'podcastNative', request: { action: 'load', episodeId: A } };
  const invoke = (request, sender) => new Promise(resolve => messageListener(request, sender, resolve));
  for (const sender of [{ id: 'test', url: tab(A).url }, { id: 'other', url: 'chrome-extension://test/podcast-panel.html' }, { id: 'test', url: 'chrome-extension://test/options.html' }]) {
    assert.equal((await invoke(message, sender)).success, false);
  }
  const trusted = { id: 'test', url: 'chrome-extension://test/podcast-panel.html' };
  assert.equal((await invoke({ ...message, request: { action: 'delete', episodeId: A } }, trusted)).success, false);
  assert.equal(nativeCalls, 0);
  assert.equal((await invoke(message, trusted)).success, true); assert.equal(nativeCalls, 1);
});

test('partial reading survives failure and resumes without sending personal thoughts to the model', async () => {
  const response = loaded(A);
  response.raw = '## 转写全文\n\n[0:02] 嗯，可能第一段。\n\n[0:03] 第二段。\n\n[0:04] 第三段。\n\n[0:05] 第四段。';
  const requests = []; let fail = true;
  const p = panel({ load: async () => response, request: async message => {
    requests.push(message);
    if (message.segments.some(entry => entry.id === 'p-3') && fail) return { success: false, error: '模拟中断' };
    return { success: true, segments: message.segments.map(entry => ({ ...entry, text: entry.text.replace('嗯，', ''), issues: [] })), provider: 'openrouter', model: 'test-model' };
  } });
  await p.sandbox.initialize(tab(A)); p.element('refineScope').value = 'all';
  p.sandbox.openEditor('嗯，可能第一段。', 2, 'p-0'); p.element('thoughtInput').value = '私人感想，不能送精校'; await p.sandbox.saveAnnotation();
  await p.sandbox.refine();
  assert.equal(p.cache[`podcast_digest_${A}`].reading.items.length, 3);
  assert.match(p.element('status').textContent, /模拟中断/);
  fail = false; await p.sandbox.refine();
  assert.equal(p.cache[`podcast_digest_${A}`].reading.items.length, 4);
  assert.deepEqual(requests.at(-1).segments.map(entry => entry.id), ['p-3']);
  assert.equal(JSON.stringify(requests).includes('私人感想'), false);
});

test('a late reading response from the previous episode cannot overwrite the new one', async () => {
  let release, entered;
  const waiting = new Promise(resolve => entered = resolve);
  const p = panel({ request: message => new Promise(resolve => { release = () => resolve({ success: true, segments: message.segments.map(entry => ({ ...entry, issues: [] })) }); entered(); }) });
  await p.sandbox.initialize(tab(A)); p.element('refineScope').value = 'all';
  const old = p.sandbox.refine(); await waiting;
  await p.sandbox.initialize(tab(B)); release(); await old;
  assert.equal(p.cache[`podcast_digest_${B}`].reading, null);
  assert.equal(p.element('podcastTitle').textContent, B);
});

test('a reading quote is linked to the original segment and survives closure unchanged', async () => {
  const p = panel(); await p.sandbox.initialize(tab(A));
  p.sandbox.openEditor(A + '原话', 2, 'p-0', '整理后的摘句');
  p.element('thoughtInput').value = '  我的感想\n原样保存'; await p.sandbox.saveAnnotation();
  const note = p.cache[`podcast_digest_${A}`].annotations[0];
  assert.equal(note.quote, A + '原话'); assert.equal(note.readingQuote, '整理后的摘句');
  assert.equal(note.thought, '  我的感想\n原样保存'); assert.equal(note.entryId, 'p-0');
  const restored = panel({ cache: p.cache }); await restored.sandbox.initialize(tab(A));
  assert.equal(restored.cache[`podcast_digest_${A}`].annotations[0].readingQuote, '整理后的摘句');
});

const empty = async () => ({ success: true, found: false, annotations: [] });
const jobResult = (status, extra = {}) => ({ success: true, job: { status, phase: status === 'running' ? 'transcribing' : status, progress: status === 'completed' ? 1 : 0.25, processedSeconds: 30, duration: 120, ...extra } });

test('an existing API provider is identified with an explicit subscription-switch instruction', async () => {
  const config = { provider: 'openrouter', aiModel: 'existing-model', hasAiKey: true };
  const p = panel({ config });
  await p.sandbox.initialize(tab(A)); await p.sandbox.refreshTextConfig();
  assert.match(p.element('textModelStatus').textContent, /OpenRouter \/ existing-model/);
  assert.match(p.element('textModelStatus').textContent, /在设置中选择“本机 Codex（订阅登录）”并保存/);
  assert.equal(config.provider, 'openrouter');
  config.provider = 'codex'; config.aiModel = 'subscription-model';
  await p.sandbox.refreshTextConfig();
  assert.match(p.element('textModelStatus').textContent, /本机 Codex · 订阅登录/);
  assert.match(p.element('textModelStatus').textContent, /不需要 API Key/);
  assert.equal(p.element('refineBtn').disabled, false);
  assert.equal(p.element('analyzeBtn').disabled, false);
});

test('whole-episode transcription starts once and failed status polls preserve the running task', async () => {
  let release, failPoll = false; const calls = [];
  const p = panel({ load: empty, nativeRequest: message => {
    calls.push(message);
    if (message.action === 'transcribeStart') return new Promise(resolve => release = resolve);
    if (failPoll) throw new Error('连接暂时中断');
    return jobResult('idle');
  } });
  await p.sandbox.initialize(tab(A));
  assert.equal(p.element('transcribeBtn').disabled, false);
  const first = p.sandbox.startTranscription();
  await p.sandbox.startTranscription();
  assert.equal(calls.filter(call => call.action === 'transcribeStart').length, 1);
  assert.equal(p.element('transcribeBtn').disabled, true);
  release(jobResult('running')); await first;
  assert.equal(p.element('transcriptionProgress').value, 0.25);
  assert.match(p.element('transcriptionStatus').textContent, /0:30 \/ 2:00/);
  assert.equal(p.element('cancelTranscribeBtn').hidden, false);
  assert.match(p.element('status').textContent, /正在生成本集全文/);
  failPoll = true; await p.sandbox.refreshTranscription();
  assert.equal(p.element('transcribeBtn').disabled, true);
  assert.match(p.element('transcriptionStatus').textContent, /后台任务可能仍在运行/);
  assert.equal(p.timers.size, 1);
});

test('reopening resumes a durable job and closing the panel does not cancel it', async () => {
  const calls = [];
  const p = panel({ load: empty, nativeRequest: async message => { calls.push(message.action); return jobResult('running'); } });
  await p.sandbox.initialize(tab(A));
  assert.deepEqual(calls, ['transcribeStatus']);
  assert.equal(p.element('transcriptionTask').hidden, false);
  assert.equal(p.timers.size, 1);
  p.windowListeners.pagehide(); await p.flush();
  assert.equal(p.timers.size, 0);
  assert.equal(calls.includes('transcribeCancel'), false);
});

test('completion loads the complete source while retaining annotation drafts', async () => {
  let ready = false;
  const p = panel({ load: async id => ready ? loaded(id) : empty(), nativeRequest: async () => ready ? jobResult('completed', { sourcePath: loaded(A).sourcePath }) : jobResult('running') });
  await p.sandbox.initialize(tab(A)); ready = true;
  await p.sandbox.refreshTranscription();
  assert.equal(p.cache[`podcast_digest_${A}`].transcript.entries[0].text, A + '原话');
  assert.equal(p.element('transcriptionLabel').textContent, '整集转写完成');
  assert.equal(p.element('transcribeBtn').disabled, true);
  assert.equal(p.element('refineBtn').disabled, false);
});

test('a late transcription completion cannot replace a different episode', async () => {
  let release, hold = false;
  const calls = [];
  const p = panel({ load: async id => id === B ? loaded(B) : empty(), nativeRequest: message => {
    calls.push(message);
    if (hold && message.episodeId === A) return new Promise(resolve => release = resolve);
    return Promise.resolve(jobResult(message.episodeId === A ? 'running' : 'idle'));
  } });
  await p.sandbox.initialize(tab(A)); hold = true;
  const old = p.sandbox.refreshTranscription();
  await p.sandbox.initialize(tab(B));
  release(jobResult('completed', { sourcePath: loaded(A).sourcePath })); await old;
  assert.equal(p.element('podcastTitle').textContent, B);
  assert.equal(p.element('transcriptionTask').hidden, true);
  assert.equal(p.cache[`podcast_digest_${B}`].transcript.entries[0].text, B + '原话');
  assert.equal(calls.some(call => call.action === 'transcribeCancel'), false);
});

test('transcription completion preserves an imported manuscript until an explicit reload', async () => {
  let ready = false;
  const p = panel({ load: async id => ready ? loaded(id) : empty(), nativeRequest: async () => ready ? jobResult('completed', { sourcePath: loaded(A).sourcePath }) : jobResult('running') });
  await p.sandbox.initialize(tab(A));
  await p.element('importFile').listeners.change({ target: { value: '', files: [{ name: 'manual.txt', size: 30, text: async () => '我自己整理的全文。' }] } });
  ready = true; await p.sandbox.refreshTranscription();
  assert.equal(p.cache[`podcast_digest_${A}`].transcript.entries[0].text, '我自己整理的全文。');
  assert.match(p.element('transcriptionStatus').textContent, /当前文稿和感想已保留/);
});

test('cancel preserves progress and enables explicit resume', async () => {
  const calls = [];
  const p = panel({ load: empty, nativeRequest: async message => { calls.push(message.action); return jobResult(message.action === 'transcribeCancel' ? 'cancelled' : 'running'); } });
  await p.sandbox.initialize(tab(A)); await p.sandbox.cancelTranscription();
  assert.equal(p.element('transcribeBtn').textContent, '继续转写');
  assert.equal(p.element('transcribeBtn').disabled, false);
  assert.equal(p.element('cancelTranscribeBtn').hidden, true);
  assert.match(p.element('status').textContent, /转写已停止/);
  assert.equal(p.timers.size, 0);
  await p.sandbox.startTranscription();
  assert.deepEqual(calls, ['transcribeStatus', 'transcribeCancel', 'transcribeStart']);
});

test('overview uses the entire manuscript and persists with source identity for export and archival', async () => {
  const response = loaded(A);
  response.raw += '\n\n[133:45] 这是节目末尾，不能遗漏。';
  const calls = []; let archived;
  const p = panel({ load: async () => response, request: async message => {
    calls.push(message);
    return { success: true, provider: 'codex', model: 'account-model', analysis: { chapters: [{ title: '教育与选择', timestampSeconds: 2, summary: '开头讨论。' }, { title: '结尾', timestampSeconds: 8025, summary: '末尾讨论。' }] } };
  }, nativeRequest: async message => {
    if (message.action === 'save') { archived = message; return { success: true, sourcePath: response.sourcePath, overviewPath: 'overview.md', savedIds: [], added: 0 }; }
    return jobResult('idle');
  } });
  await p.sandbox.initialize(tab(A)); await p.sandbox.analyze();
  assert.equal(calls[0].outputLanguage, 'zh-CN');
  assert.match(calls[0].transcriptText, /133:45.*节目末尾/);
  const overview = p.cache[`podcast_digest_${A}`].overview;
  assert.equal(overview.sourceEntryCount, 2);
  assert.equal(overview.provider, 'codex');
  assert.equal(overview.chapters.length, 2);
  assert.match(p.sandbox.overviewMarkdown(), /133:45 结尾/);
  await p.sandbox.archive();
  assert.equal(archived.overview.sourceHash, overview.sourceHash);
  assert.equal(archived.overview.model, 'account-model');
  const restored = panel({ cache: p.cache, load: async () => response });
  await restored.sandbox.initialize(tab(A));
  assert.match(restored.sandbox.overviewMarkdown(), /末尾讨论/);
  const changed = panel({ cache: p.cache, load: async () => ({ ...response, raw: response.raw + '\n\n[133:50] 新补充。' }) });
  await changed.sandbox.initialize(tab(A));
  assert.equal(changed.sandbox.overviewMarkdown(), '');
});

for (const [flag, notice] of [
  ['overviewManualEdited', /已有人工修改/],
  ['overviewInvalid', /数据格式有误/],
  ['overviewStale', /原稿版本已变化/],
]) {
  test(`${flag} clears a same-source cached overview and keeps it out of annotation saves`, async () => {
    let flagged = false, storedOverview, archived;
    const p = panel({ load: async () => ({ ...loaded(A), ...(flagged ? { [flag]: true, overview: storedOverview } : {}) }), request: async () => ({ success: true, provider: 'codex', model: 'account-model', analysis: { chapters: [{ title: '原有概览', summary: '这是旧概览。', timestampSeconds: 2 }] } }), nativeRequest: async message => {
      if (message.action === 'save') { archived = message; return { success: true, sourcePath: loaded(A).sourcePath, savedIds: message.annotations.map(note => note.id), added: message.annotations.length }; }
      return jobResult('idle');
    } });
    await p.sandbox.initialize(tab(A)); await p.sandbox.analyze();
    storedOverview = p.cache[`podcast_digest_${A}`].overview;
    assert.match(p.sandbox.overviewMarkdown(), /这是旧概览/);
    flagged = true; await p.sandbox.loadVault();
    assert.equal(p.cache[`podcast_digest_${A}`].overview, null);
    assert.equal(p.element('overview').children.length, 0);
    assert.equal(p.sandbox.overviewMarkdown(), '');
    assert.match(p.element('status').textContent, notice);
    p.sandbox.openEditor(A + '原话', 2, 'p-0');
    p.element('thoughtInput').value = '概览有变化，但这条感想仍要保存。';
    await p.sandbox.saveAnnotation(); await p.sandbox.archive();
    assert.equal(Object.hasOwn(archived, 'overview'), false);
    assert.equal(archived.annotations.length, 1);
    assert.equal(p.cache[`podcast_digest_${A}`].annotations[0].saved, true);
  });
}

for (const [field, value] of [['title', ''], ['title', '  \n'], ['summary', ''], ['summary', '\t  ']]) {
  test(`overview rejects ${field} containing ${JSON.stringify(value)} without replacing valid cached content`, async () => {
    let invalid = false;
    const p = panel({ request: async () => ({ success: true, provider: 'codex', model: 'account-model', analysis: { chapters: [{ title: '有效标题', summary: '有效概览。', timestampSeconds: 2, ...(invalid ? { [field]: value } : {}) }] } }) });
    await p.sandbox.initialize(tab(A)); await p.sandbox.analyze();
    invalid = true; await p.sandbox.analyze();
    assert.equal(p.cache[`podcast_digest_${A}`].overview.chapters[0].title, '有效标题');
    assert.equal(p.cache[`podcast_digest_${A}`].overview.chapters[0].summary, '有效概览。');
    assert.match(p.element('status').textContent, /概览内容为空或格式无效/);
  });
}
