const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
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

function panel({ cache = {}, load = async id => loaded(id), write = async () => {} } = {}) {
  const elements = new Map(), windowListeners = {};
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  element('annotationEditor').hidden = true;
  const sandbox = {
    console, URL, Blob, crypto: { randomUUID }, setTimeout: () => 0, clearTimeout() {}, YTD_MEDIA: media, YTD_PODCAST: core,
    document: { getElementById: element, querySelectorAll: () => [], createElement: () => new Element(), createTextNode: text => ({ textContent: text }) },
    window: { addEventListener: (name, fn) => windowListeners[name] = fn, close() {} },
    chrome: {
      storage: { local: { get: async key => structuredClone({ [key]: cache[key] }), set: async values => { await write(values); Object.assign(cache, structuredClone(values)); } } },
      runtime: { sendMessage: async message => message.action === 'podcastNative' ? load(message.request.episodeId) : {} },
      tabs: { query: () => new Promise(() => {}), sendMessage: async () => ({}), onActivated: { addListener() {} }, onUpdated: { addListener() {} } },
    },
  };
  vm.createContext(sandbox); vm.runInContext(source, sandbox);
  return { sandbox, element, cache, windowListeners, flush: () => vm.runInContext('storageWrites', sandbox) };
}

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
