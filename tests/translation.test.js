const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const read = (file) => fs.readFileSync(path.join(root, file), "utf8");

function loadSidepanelHelpers({
  sendMessage = () => Promise.resolve({}),
  setTimeoutImpl = () => 0,
  clearTimeoutImpl = () => {},
  initialStorage = {},
  documentOverride = {},
} = {}) {
  const listeners = { addListener() {} };
  const sessionStorage = {};
  const localStorage = { ...initialStorage };
  const sandbox = {
    console,
    URL,
    TextDecoder,
    TextEncoder,
    setTimeout: setTimeoutImpl,
    clearTimeout: clearTimeoutImpl,
    setInterval() {},
    clearInterval() {},
    IntersectionObserver: class {},
    CSS: { escape: (value) => value },
    window: { getSelection: () => null, close() {} },
    document: {
      addEventListener() {},
      querySelectorAll: () => [],
      querySelector: () => null,
      getElementById: () => null,
      createElement: () => {
        let value = "";
        return {
          set textContent(text) {
            value = String(text);
          },
          get innerHTML() {
            return value
              .replaceAll("&", "&amp;")
              .replaceAll("<", "&lt;")
              .replaceAll(">", "&gt;")
              .replaceAll('"', "&quot;");
          },
        };
      },
      ...documentOverride,
    },
    chrome: {
      runtime: { onMessage: listeners, sendMessage },
      storage: {
        local: {
          get: async (key) => key === null ? { ...localStorage } : ({ [key]: localStorage[key] }),
          set: async (values) => Object.assign(localStorage, values),
        },
        session: {
          get: async (key) => ({ [key]: sessionStorage[key] }),
          set: async (values) => Object.assign(sessionStorage, values),
        },
      },
      windows: { getCurrent: () => Promise.resolve({ id: 1 }) },
      tabs: { onUpdated: listeners, onActivated: listeners },
    },
    YTD_SETTINGS: {},
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(read("sidepanel.js"), sandbox);
  sandbox.__YTD_TRANSCRIPT_TESTING__.run = (code) => vm.runInContext(code, sandbox);
  sandbox.__YTD_TRANSCRIPT_TESTING__.storage = localStorage;
  return sandbox.__YTD_TRANSCRIPT_TESTING__;
}

function loadBackgroundHelpers({
  settings = {
    provider: "deepseek",
    aiApiKey: "test-key",
    aiBaseUrl: "https://api.deepseek.com",
    aiModel: "deepseek-v4-flash",
    translationQuality: "quick",
  },
  fetchImpl = fetch,
  setTimeoutImpl = () => 0,
  clearTimeoutImpl = () => {},
  sidePanel = {
    setPanelBehavior() {},
    setOptions: () => Promise.resolve(),
  },
} = {}) {
  const listeners = { addListener() {} };
  const localStorage = { ytd_settings: settings };
  const sandbox = {
    console,
    URL,
    TextDecoder,
    TextEncoder,
    fetch: fetchImpl,
    AbortController,
    setTimeout: setTimeoutImpl,
    clearTimeout: clearTimeoutImpl,
    importScripts() {},
    YTD_MEDIA: require("../media-platform.js"),
    chrome: {
      storage: {
        local: {
          setAccessLevel: () => Promise.resolve(),
          get: async (key) => {
            if (key === null) return { ...localStorage };
            if (Array.isArray(key)) {
              return Object.fromEntries(key.map((item) => [item, localStorage[item]]));
            }
            return { [key]: localStorage[key] };
          },
          set: async (values) => Object.assign(localStorage, values),
          remove: async (keys) => {
            for (const key of Array.isArray(keys) ? keys : [keys]) {
              delete localStorage[key];
            }
          },
        },
      },
      action: { onClicked: listeners },
      sidePanel,
      runtime: {
        onInstalled: listeners,
        onMessage: listeners,
        openOptionsPage() {},
        getURL: (resourcePath) => `chrome-extension://test/${resourcePath}`,
        sendMessage: () => Promise.resolve({ success: true }),
      },
      tabs: { onUpdated: listeners, onActivated: listeners },
    },
    YTD_SETTINGS: {
      STORAGE_KEY: "ytd_settings",
      normalize: (value) => value,
      chatCompletionsUrl: (settings) => `${settings.aiBaseUrl}/chat/completions`,
      completionBody: require("../settings.js").completionBody,
      canonicalYouTubeUrl: (videoId) =>
        `https://www.youtube.com/watch?v=${videoId}`,
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.readNativeTranscript = () => {};
  sandbox.chrome.tabs.get = async () => ({ url: "https://www.youtube.com/watch?v=ydTeb_I0b94" });
  sandbox.chrome.scripting = { executeScript: async () => [{ result: { success: true, source: "youtube-captions", transcript: [{ text: "Caption", start: 0 }], transcriptText: "Caption" } }] };
  vm.runInNewContext(read("background.js"), sandbox);
  return sandbox.__YTD_TRANSLATION_TESTING__;
}

test("native mode retrieves subtitles without sending keys or charging Supadata", async () => {
  let calls = 0;
  const background = loadBackgroundHelpers({
    settings: { provider: "openrouter", transcriptProvider: "native", aiApiKey: "", supadataApiKey: "optional-key" },
    fetchImpl: async () => { calls++; throw new Error("Paid API must not run"); },
  });
  const result = await background.handleFetchTranscript("ydTeb_I0b94", 17);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-captions");
  assert.equal(calls, 0);
});

test("Supadata prefers existing Chinese captions without generating or translating audio", async () => {
  let request;
  const background = loadBackgroundHelpers({
    settings: { transcriptProvider: "supadata", supadataApiKey: "test-key" },
    fetchImpl: async (url) => {
      request = new URL(url);
      return { ok: true, status: 200, json: async () => ({ lang: "zh", content: [{ text: "大家好呀。", offset: 1500, duration: 1000, lang: "zh" }] }) };
    },
  });
  const result = await background.handleFetchTranscript("ydTeb_I0b94", 17);
  assert.equal(request.searchParams.get("lang"), "zh");
  assert.equal(request.searchParams.get("mode"), "native");
  assert.equal(result.language, "zh");
  assert.equal(result.transcriptText, "大家好呀。");
});

test("Chinese detection covers variants and unlabelled Chinese DOM transcripts", () => {
  const { isChineseTranscript } = loadSidepanelHelpers();
  for (const language of ["zh", "zh-CN", "zh-Hans", "zh-Hant", "cmn", "yue-HK"]) {
    assert.equal(isChineseTranscript(language, "原话。"), true);
  }
  assert.equal(isChineseTranscript(null, "大家好，咱们今天接着聊一下 AI。"), true);
  assert.equal(isChineseTranscript("en", "Original English speech."), false);
  assert.equal(isChineseTranscript(null, "The Chinese name is 王海."), false);
  assert.equal(isChineseTranscript(null, "これは日本語の字幕です。"), false);
});

test("Chinese and bilingual views render native Chinese once with zero translation requests", async () => {
  let requests = 0;
  const rows = [];
  const transcriptList = { innerHTML: "", appendChild: (row) => rows.push(row) };
  const panel = loadSidepanelHelpers({
    sendMessage: async () => { requests++; throw new Error("Chinese source must not call translation"); },
    documentOverride: {
      getElementById: (id) => id === "transcriptList" ? transcriptList
        : id === "followPlaybackBtn" ? { style: {} }
          : id === "contentArea" ? { addEventListener() {}, removeEventListener() {} } : null,
      createElement: () => {
        let html = "";
        return { dataset: {}, addEventListener() {},
          set textContent(text) { html = String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); },
          set innerHTML(value) { html = value; },
          get innerHTML() { return html; },
        };
      },
    },
  });
  panel.run(`
    currentVideoId = "ydTeb_I0b94";
    currentTranscriptLanguage = "zh-Hans";
    currentTranscriptText = "咱们今天呀，接着说。";
    currentTranscript = [{ start: 0, text: currentTranscriptText }];
  `);
  for (const mode of ["zh", "bilingual"]) {
    rows.length = 0;
    panel.run(`currentTranscriptMode = "${mode}";`);
    await panel.translateTranscript();
    assert.equal(rows.length, 1);
    assert.match(rows[0].innerHTML, /咱们今天呀，接着说。/);
    assert.doesNotMatch(rows[0].innerHTML, /Waiting for translation|transcript-translation/);
    assert.equal(panel.getDisplayedTranscriptText(), "咱们今天呀，接着说。");
  }
  assert.equal(requests, 0);
});

test("legacy English-first caches refetch while new-policy caches are reusable and notes remain", async () => {
  const notes = [{ text: "保留我的笔记" }];
  const panel = loadSidepanelHelpers({ initialStorage: {
    digest_ydTeb_I0b94: { transcript: [{ text: "Old English" }], timestamp: Date.now() },
    notes_ydTeb_I0b94: notes,
  } });
  assert.equal(await panel.loadFromCache("ydTeb_I0b94"), null);
  assert.equal(panel.storage.notes_ydTeb_I0b94, notes);
  panel.run(`
    currentTranscriptLanguage = "zh-Hans";
    currentTranscriptText = "中文原话。";
    currentTranscript = [{ start: 0, text: currentTranscriptText }];
  `);
  await panel.saveToCache("ydTeb_I0b94");
  const cached = await panel.loadFromCache("ydTeb_I0b94");
  assert.equal(cached.transcriptText, "中文原话。");
  assert.equal(panel.storage.notes_ydTeb_I0b94, notes);
});

test("OpenRouter and 302.ai requests use their endpoints without DeepSeek fields", async () => {
  for (const provider of ["openrouter", "302ai"]) {
    const settings = require("../settings.js").normalize({ provider, aiApiKey: "selected-provider-key" });
    let request;
    const background = loadBackgroundHelpers({ settings, fetchImpl: async (url, options) => {
      request = { url, options };
      return { ok: true, json: async () => ({ choices: [{ message: { content: "translated" } }] }) };
    } });
    assert.equal((await background.requestAiCompletion({ messages: [{ role: "user", content: "Translate" }], maxTokens: 128 })).text, "translated");
    assert.equal(request.url, `${settings.aiBaseUrl}/chat/completions`);
    assert.equal(request.options.headers.Authorization, "Bearer selected-provider-key");
    assert.equal(Object.hasOwn(JSON.parse(request.options.body), "thinking"), false);
  }
});

test("non-YouTube tabs explicitly close before their panel is disabled", async () => {
  const calls = [];
  const background = loadBackgroundHelpers({
    sidePanel: {
      setPanelBehavior() {},
      close: async (options) => calls.push(["close", options]),
      setOptions: async (options) => calls.push(["setOptions", options]),
    },
  });

  await background.updatePanelForTab(17, "https://example.com/page", 4);

  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["close", { tabId: 17 }],
    ["setOptions", { tabId: 17, enabled: false }],
  ]);
});

test("a global panel closes by window when the tab close is rejected", async () => {
  const calls = [];
  const background = loadBackgroundHelpers({
    sidePanel: {
      setPanelBehavior() {},
      close: async (options) => {
        calls.push(["close", options]);
        if (options.tabId) throw new Error("Global panel");
      },
      setOptions: async (options) => calls.push(["setOptions", options]),
    },
  });

  await background.updatePanelForTab(17, "https://example.com/page", 4);

  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ["close", { tabId: 17 }],
    ["close", { windowId: 4 }],
    ["setOptions", { tabId: 17, enabled: false }],
  ]);
});

function createFakeTimers() {
  let nextId = 1;
  const timers = new Map();
  return {
    setTimeout(callback, delay) {
      const id = nextId++;
      timers.set(id, { callback, delay, active: true });
      return id;
    },
    clearTimeout(id) {
      const timer = timers.get(id);
      if (timer) timer.active = false;
    },
    fireActive(delay) {
      const match = [...timers.entries()].find(
        ([, timer]) => timer.active && timer.delay === delay,
      );
      assert.ok(match, `Expected an active ${delay}ms timer`);
      match[1].active = false;
      match[1].callback();
    },
    activeCount(delay) {
      return [...timers.values()].filter(
        (timer) => timer.active && timer.delay === delay,
      ).length;
    },
    createdCount(delay) {
      return [...timers.values()].filter((timer) => timer.delay === delay).length;
    },
  };
}

function streamingResponse(chunks, { ok = true, status = 200 } = {}) {
  let index = 0;
  return {
    ok,
    status,
    body: {
      getReader() {
        return {
          async read() {
            if (index >= chunks.length) return { done: true };
            return { done: false, value: chunks[index++] };
          },
          async cancel() {},
        };
      },
    },
  };
}

const encode = (value) => new TextEncoder().encode(value);
const nextTurn = () => new Promise((resolve) => setImmediate(resolve));

test("the header exposes one universal language control for all result tabs", () => {
  const html = read("sidepanel.html");
  const js = read("sidepanel.js");
  assert.match(html, /id="transcriptModeControl"[\s\S]*aria-label="Content language"/);
  assert.match(html, /id="transcriptModeControl"[\s\S]*id="tabsNav"/);
  assert.match(html, /data-transcript-mode="original"[\s\S]*?>Original</);
  assert.match(html, /data-transcript-mode="zh"[\s\S]*?>\u4e2d\u6587</);
  assert.match(html, /data-transcript-mode="bilingual"[\s\S]*?>\u53cc\u8bed</);
  assert.match(js, /handleDisplayLanguageModeChange\(button\.dataset\.transcriptMode\)/);
  assert.match(js, /contentType: "transcriptBatch"/);
  assert.match(js, /contentType: "interfaceBatch"/);
  assert.match(js, /translateOverviewContent/);
  assert.match(js, /translateNotesContent/);
  assert.doesNotMatch(js, /English \+ Chinese/);
  assert.doesNotMatch(`${html}\n${js}`, /From video subtitles/);
});

test("new videos default to Chinese while returning videos restore explicit choices", async () => {
  const { loadDisplayLanguageMode, saveDisplayLanguageMode } =
    loadSidepanelHelpers();

  await saveDisplayLanguageMode("video-a", "bilingual");
  assert.equal(await loadDisplayLanguageMode("video-a"), "bilingual");
  await saveDisplayLanguageMode("video-b", "original");
  assert.equal(await loadDisplayLanguageMode("video-b"), "original");
  assert.equal(await loadDisplayLanguageMode("unseen-video"), "zh");
});

function loadTranslationQueueFixture({ segmentCount = 24, cachedIndices = [] } = {}) {
  const helpers = loadSidepanelHelpers();
  helpers.run(`
    var queueTest = {
      segments: Array.from({ length: ${segmentCount} }, (_, index) => ({
        id: 'queue-segment-' + index,
        start: index * 10,
        text: 'An English subtitle sentence for segment ' + index + '.',
      })),
      calls: [], pending: [], observers: [], active: 0, maxActive: 0,
    };
    queueTest.rows = queueTest.segments.map((_, index) => ({
      dataset: { segmentIndex: String(index) },
      classList: {
        contains: name => name === 'translated' && transcriptParagraphCache.has(
          transcriptTranslationCacheKey(queueTest.segments[index]),
        ),
      },
    }));
    currentVideoId = 'queue-video';
    currentTranscriptMode = 'zh';
    currentTranscriptLanguage = 'en';
    getActiveTranscriptSegments = () => queueTest.segments;
    renderTranscriptModeRows = () => queueTest.rows;
    IntersectionObserver = class {
      constructor(callback, options) {
        this.callback = callback;
        this.options = options;
        this.disconnected = false;
        this.observed = [];
        queueTest.observers.push(this);
      }
      observe(row) { this.observed.push(row); }
      unobserve() {}
      disconnect() { this.disconnected = true; }
    };
    requestTranscriptTranslationBatch = async (indices) => {
      queueTest.calls.push([...indices]);
      queueTest.active += 1;
      queueTest.maxActive = Math.max(queueTest.maxActive, queueTest.active);
      const success = await new Promise(resolve => queueTest.pending.push(resolve));
      if (success) {
        indices.forEach(index => transcriptParagraphCache.set(
          transcriptTranslationCacheKey(queueTest.segments[index]),
          '第' + index + '段中文。',
        ));
      }
      queueTest.active -= 1;
    };
    ${JSON.stringify(cachedIndices)}.forEach(index => transcriptParagraphCache.set(
      transcriptTranslationCacheKey(queueTest.segments[index]), '已有中文。',
    ));
  `);

  const snapshot = () => JSON.parse(helpers.run(`JSON.stringify({
    calls: queueTest.calls,
    active: queueTest.active,
    maxActive: queueTest.maxActive,
    pending: queueTest.pending.length,
  })`));
  let previousVisible = new Set();
  let previousObserverCount = 0;
  const visible = async (indices) => {
    const observerCount = helpers.run("queueTest.observers.length");
    if (observerCount !== previousObserverCount) previousVisible = new Set();
    const nextVisible = new Set(indices);
    const changes = [
      ...[...previousVisible].filter(index => !nextVisible.has(index))
        .map(index => ({ index, isIntersecting: false })),
      ...indices.map(index => ({ index, isIntersecting: true })),
    ];
    helpers.run(`queueTest.observers.at(-1).callback(
      ${JSON.stringify(changes)}.map(({ index, isIntersecting }) => ({
        target: queueTest.rows[index], isIntersecting,
      })),
    )`);
    previousVisible = nextVisible;
    previousObserverCount = observerCount;
    await nextTurn();
  };
  const complete = async (success = true) => {
    assert.equal(helpers.run("queueTest.pending.length"), 1, "only one batch is active");
    helpers.run(`queueTest.pending.shift()(${JSON.stringify(success)})`);
    await nextTurn();
  };
  const drain = async () => {
    for (let count = 0; snapshot().pending; count++) {
      assert.ok(count < segmentCount + 2, "prefetch must terminate without looping");
      await complete();
    }
  };
  const start = async () => {
    await helpers.translateTranscript();
    await nextTurn();
  };
  return { helpers, snapshot, visible, complete, drain, start };
}

test("translation queue deduplicates pending first rows, runs serially and bounds initial prefetch", async () => {
  const fixture = loadTranslationQueueFixture();
  await fixture.start();
  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2]]);

  // The browser reports these rows after the initial batch has already started.
  await fixture.visible([0, 1, 2]);
  await fixture.visible([0, 1, 2]);
  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2]]);
  await fixture.drain();

  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2], [3, 4, 5], [6, 7, 8]]);
  assert.equal(fixture.snapshot().maxActive, 1);
  assert.equal(fixture.helpers.run("queueTest.observers[0].options.rootMargin"), "320px 0px");
  await fixture.visible([0, 1, 2]);
  assert.equal(fixture.snapshot().calls.length, 3, "cached viewport does not restart completed work");
});

test("rerendering the same transcript rebinds its observer and reuses an unfinished queue", async () => {
  const fixture = loadTranslationQueueFixture();
  await fixture.start();
  await fixture.start();
  assert.equal(fixture.helpers.run("queueTest.observers.length"), 2);
  assert.equal(fixture.helpers.run("queueTest.observers[0].disconnected"), true);
  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2]]);
  await fixture.visible([0, 1, 2]);
  await fixture.drain();
  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2], [3, 4, 5], [6, 7, 8]]);
  assert.equal(fixture.snapshot().maxActive, 1);
});

test("newly visible rows take the next batch ahead of queued speculative prefetch", async () => {
  const fixture = loadTranslationQueueFixture();
  await fixture.start();
  await fixture.visible([15, 16]);
  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2]], "an already sent batch remains active");
  await fixture.complete();
  assert.deepEqual(fixture.snapshot().calls[1].slice(0, 2), [15, 16]);
  await fixture.drain();
  const requested = fixture.snapshot().calls.flat();
  assert.equal(new Set(requested).size, requested.length);
  assert.deepEqual([...requested].sort((a, b) => a - b), [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 18, 19, 20, 21, 22,
  ]);
  assert.equal(fixture.snapshot().maxActive, 1);
});

test("rows scrolled away lose priority when a new viewport replaces them", async () => {
  const fixture = loadTranslationQueueFixture();
  await fixture.start();
  await fixture.visible([3, 4]);
  await fixture.visible([15, 16]);
  assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2]]);
  await fixture.complete();
  assert.deepEqual(fixture.snapshot().calls[1].slice(0, 2), [15, 16]);
  await fixture.drain();
  const requested = fixture.snapshot().calls.flat();
  assert.equal(new Set(requested).size, requested.length);
  assert.equal(fixture.snapshot().maxActive, 1);
});

test("scrolling prefetches six rows after the last visible row without extending to the whole video", async () => {
  const fixture = loadTranslationQueueFixture();
  await fixture.start();
  await fixture.drain();

  await fixture.visible([10, 11]);
  await fixture.drain();
  const requested = fixture.snapshot().calls.slice(3).flat();
  assert.deepEqual(requested, [10, 11, 12, 13, 14, 15, 16, 17]);
  assert.ok(!requested.includes(9), "rows outside the requested viewport are not backfilled");
  assert.ok(!requested.includes(18), "completion does not recursively prefetch the rest");
  assert.equal(fixture.snapshot().maxActive, 1);
});

test("translation queue skips cached segments before dispatching and does not duplicate requested rows", async () => {
  const fixture = loadTranslationQueueFixture({ cachedIndices: [0, 1, 2, 4, 6] });
  await fixture.start();
  await fixture.visible([0, 1, 2]);
  await fixture.drain();
  const requested = fixture.snapshot().calls.flat();
  assert.deepEqual([...requested].sort((a, b) => a - b), [3, 5, 7, 8]);
  assert.equal(new Set(requested).size, requested.length);
});

test("scrolling through cached rows still observes the viewport and prefetches beyond the cache", async () => {
  const fixture = loadTranslationQueueFixture({ cachedIndices: [0, 1, 2, 3, 4, 5, 6, 7, 8] });
  await fixture.start();
  assert.deepEqual(fixture.snapshot().calls, []);
  assert.equal(fixture.helpers.run("queueTest.observers[0].observed.includes(queueTest.rows[8])"), true);
  await fixture.visible([6, 7, 8]);
  await fixture.drain();
  assert.deepEqual(fixture.snapshot().calls.flat(), [9, 10, 11, 12, 13, 14]);
});

test("failed translation stays failed on observer updates until an explicit retry", async () => {
  const fixture = loadTranslationQueueFixture();
  await fixture.start();
  await fixture.visible([0, 1, 2]);
  await fixture.complete(false);
  await fixture.visible([0, 1, 2]);
  await fixture.drain();
  assert.equal(fixture.snapshot().calls.flat().filter(index => index === 0).length, 1);

  fixture.helpers.run("activeTranslationQueue.enqueue(0, true)");
  await nextTurn();
  assert.deepEqual(fixture.snapshot().calls.at(-1), [0]);
  await fixture.complete();
  assert.equal(fixture.snapshot().calls.flat().filter(index => index === 0).length, 2);
});

for (const [change, code] of [
  ["generation", "translationGeneration += 1"],
  ["video", "currentVideoId = 'another-video'"],
  ["display language", "currentTranscriptMode = 'bilingual'"],
]) {
  test(`an old translation queue stops after a ${change} change`, async () => {
    const fixture = loadTranslationQueueFixture();
    await fixture.start();
    fixture.helpers.run(code);
    await fixture.complete();
    await fixture.visible([10, 11]);
    assert.deepEqual(fixture.snapshot().calls, [[0, 1, 2]]);
    assert.equal(fixture.snapshot().pending, 0);
  });
}

for (const [surface, request] of [
  ["transcript", "requestTranscriptTranslationBatch([0], spinnerSegments, translationGeneration, currentVideoId, currentTranscriptMode)"],
  ["overview", "translateInterfaceSegments('overview', spinnerSegments, () => {})"],
]) {
  test(`an old ${surface} response cannot clear the new video's translation spinner`, async () => {
    const pending = [];
    let spinnerVisible = false;
    const helpers = loadSidepanelHelpers({
      sendMessage: (message) => new Promise(resolve => pending.push({ message, resolve })),
      documentOverride: {
        getElementById: (id) => id === "langSpinner"
          ? { classList: { toggle: (_, visible) => { spinnerVisible = visible; } } }
          : id === "tabsNav" ? { style: {} } : null,
      },
    });
    helpers.run(`
      var spinnerSegments = [{ id: 'spinner-0', start: 0, text: 'An English subtitle for the spinner test.' }];
      currentVideoId = 'spinner-video-a';
      currentTranscriptMode = 'zh';
      currentAnalysis = null;
      currentVideoTitle = '';
      currentChannelName = '';
      resetTranscriptSearch = () => {};
      loadTranscriptViewState = async () => null;
      loadDisplayLanguageMode = async () => 'zh';
      loadFromCache = async () => ({ transcript: spinnerSegments });
      renderTranscript = () => {};
      showState = () => {};
      restorePendingTranscriptViewState = () => {};
      loadNotes = () => {};
      setupExplainFeature = () => {};
      translateTranscript = () => {};
      updateTranslatedRow = () => {};
      refreshTranscriptSearch = () => {};
      updateCache = async () => {};
      var spinnerRequestA = ${request};
    `);
    await nextTurn();
    assert.equal(pending.length, 1);
    assert.equal(helpers.run("translationWorkCount"), 1);
    assert.equal(spinnerVisible, true);

    // Exercise startDigest's actual video-change reset, with only rendering and
    // cached transcript loading replaced. Both translation request paths are real.
    await helpers.run("startDigest('spinner-video-b', 'https://www.youtube.com/watch?v=spinner-video-b')");
    assert.equal(helpers.run("currentVideoId"), "spinner-video-b");
    assert.equal(helpers.run("translationWorkCount"), 0);
    assert.equal(spinnerVisible, false);
    helpers.run(`var spinnerRequestB = ${request}`);
    await nextTurn();
    assert.equal(pending.length, 2);
    assert.equal(helpers.run("translationWorkCount"), 1);
    assert.equal(spinnerVisible, true);

    const reply = { success: true, translatedContent: { segments: [{ id: "spinner-0", text: "中文译文。" }] } };
    pending[0].resolve(reply);
    await helpers.run("spinnerRequestA");
    assert.equal(helpers.run("translationWorkCount"), 1, "stale completion leaves the current request counted");
    assert.equal(spinnerVisible, true);
    pending[1].resolve(reply);
    await helpers.run("spinnerRequestB");
    assert.equal(helpers.run("translationWorkCount"), 0);
    assert.equal(spinnerVisible, false);
  });
}

test("Overview shares the Transcript batch generation and retries when opened", () => {
  const js = read("sidepanel.js");
  const transcriptFunction = js.match(
    /async function translateTranscript\(\)[\s\S]*?\n}\n\nfunction setTranslatingSpinner/,
  )?.[0];

  assert.ok(transcriptFunction);
  assert.doesNotMatch(transcriptFunction, /translationGeneration \+= 1/);
  assert.match(js, /const TRANSLATION_BATCH_SIZE = 3/);
  assert.match(
    js,
    /const batch = missing\.slice\(start, start \+ TRANSLATION_BATCH_SIZE\)[\s\S]*?rerender\(\);[\s\S]*?await updateCache\(\)/,
  );
  assert.match(
    js,
    /tabName === "overview"[\s\S]*?currentAnalysis[\s\S]*?currentTranscriptMode !== "original"[\s\S]*?translateOverviewContent\(\)/,
  );
  assert.match(
    js,
    /Translate only the visible tab[\s\S]*?tabName === "notes"[\s\S]*?translateNotesContent\(\)/,
  );
  assert.match(
    js,
    /activeTabName === "overview"[\s\S]*?translateOverviewContent\(\)[\s\S]*?activeTabName === "notes"[\s\S]*?translateNotesContent\(\)[\s\S]*?activeTabName === "transcript"[\s\S]*?translateTranscript\(\)/,
  );
});

test("transcript reading position survives a side panel close", async () => {
  const { saveTranscriptViewState, loadTranscriptViewState } =
    loadSidepanelHelpers();

  await saveTranscriptViewState("video-a", 427.5);
  const restored = await loadTranscriptViewState("video-a");

  assert.deepEqual(JSON.parse(JSON.stringify(restored)), {
    videoId: "video-a",
    scrollTop: 427.5,
  });
});

test("selected transcript notes keep exact text and row timestamp", async () => {
  const providerMustNotRun = async () => {
    throw new Error("Selected note must not call a provider");
  };
  const { handleSaveNote } = loadBackgroundHelpers({
    fetchImpl: providerMustNotRun,
  });

  const result = await handleSaveNote(
    "video123",
    92.9,
    "Test video",
    "Test channel",
    "  The selected words stay exact.  ",
  );

  assert.equal(result.success, true);
  assert.equal(result.note.text, "The selected words stay exact.");
  assert.equal(result.note.rawText, "The selected words stay exact.");
  assert.equal(result.note.timestamp, "1:32");
  assert.equal(result.note.timestampSeconds, 92);
  assert.equal(
    result.note.timestampedUrl,
    "https://www.youtube.com/watch?v=video123&t=92s",
  );
});

test("semantic segmentation rebuilds sentences across caption boundaries", () => {
  const { groupTranscriptEntries } = loadSidepanelHelpers();
  const segments = groupTranscriptEntries(
    [
      { start: 0, text: "Caption boundaries should" },
      { start: 2, text: "not break a complete sentence." },
      { start: 5, text: "The next thought also" },
      { start: 7, text: "stays together!" },
    ],
    { minChars: 1, idealChars: 100, maxChars: 320, maxSeconds: 20 },
  );
  assert.equal(segments.length, 2);
  assert.equal(
    segments[0].text,
    "Caption boundaries should not break a complete sentence.",
  );
  assert.equal(segments[0].start, 0);
  assert.equal(segments[1].text, "The next thought also stays together!");
  assert.equal(segments[1].start, 5);
});

test("a huge raw Supadata entry is split into seekable bounded segments", () => {
  const { groupTranscriptEntries } = loadSidepanelHelpers();
  const text = Array.from({ length: 900 }, (_, index) => `word${index}`).join(" ");
  const segments = groupTranscriptEntries([
    { start: 12, duration: 90, text },
  ]);
  assert.ok(segments.length > 8);
  assert.ok(segments.every((segment) => segment.text.length <= 384));
  assert.equal(segments[0].start, 12);
  assert.ok(segments.at(-1).start > segments[0].start);
  assert.ok(segments.every((segment) => /^segment-\d+-\d+$/.test(segment.id)));
});

test("Chinese sentence and clause punctuation creates semantic guardrails", () => {
  const { groupTranscriptEntries } = loadSidepanelHelpers();
  const segments = groupTranscriptEntries(
    [
      { start: 0, text: "这是一个被字幕切开的" },
      { start: 2, text: "完整句子。这是第二个想法，" },
      { start: 5, text: "也应该保持语义完整！" },
    ],
    { minChars: 1, idealChars: 100, maxChars: 320, maxSeconds: 20 },
  );
  assert.equal(segments.length, 2);
  assert.equal(segments[0].text, "这是一个被字幕切开的完整句子。");
  assert.equal(segments[1].text, "这是第二个想法，也应该保持语义完整！");
});

test("structured translation batches align by stable ID and expose missing fallback", () => {
  const sidepanel = loadSidepanelHelpers();
  const background = loadBackgroundHelpers();
  const source = [
    { id: "segment-0-0", text: "A complete first sentence." },
    { id: "segment-1-5000", text: "A complete second sentence." },
  ];
  assert.deepEqual(
    JSON.parse(JSON.stringify(background.validateTranscriptBatchRequest({ segments: source }))),
    source,
  );

  const normalized = background.normalizeTranslatedSegmentBatch(
    {
      segments: [
        { id: "unknown", text: "\u5ffd\u7565" },
        { id: "segment-1-5000", text: "\u7b2c\u4e8c\u4e2a\u5b8c\u6574\u53e5\u5b50\u3002" },
      ],
    },
    source,
  );
  const aligned = sidepanel.alignTranslatedSegmentBatch(
    source,
    normalized.segments,
  );
  assert.equal(aligned[0].id, source[0].id);
  assert.equal(aligned[0].text, "");
  assert.match(aligned[0].error, /unavailable/i);
  assert.equal(aligned[1].text, "\u7b2c\u4e8c\u4e2a\u5b8c\u6574\u53e5\u5b50\u3002");
});

test("translated-only omits English while bilingual renders aligned English and Chinese", () => {
  const { renderTranscriptSegmentContent } = loadSidepanelHelpers();
  const segment = { id: "segment-0-0", text: "Original English sentence." };
  const translatedOnly = renderTranscriptSegmentContent(
    segment,
    "zh",
    "\u4e2d\u6587\u8bd1\u6587\u3002",
    "",
  );
  const bilingual = renderTranscriptSegmentContent(
    segment,
    "bilingual",
    "\u4e2d\u6587\u8bd1\u6587\u3002",
    "",
  );
  assert.doesNotMatch(translatedOnly, /Original English sentence/);
  assert.match(translatedOnly, /\u4e2d\u6587\u8bd1\u6587/);
  assert.match(bilingual, /transcript-original/);
  assert.match(bilingual, /Original English sentence/);
  assert.match(bilingual, /\u4e2d\u6587\u8bd1\u6587/);
});

test("subtitle formatting tags render in original and translated segment text", () => {
  const { renderTranscriptSegmentContent } = loadSidepanelHelpers();
  const html = renderTranscriptSegmentContent(
    {
      id: "segment-0-0",
      text: "Think <i>deeply</i>, <b>carefully</b>, and <u>clearly</u>.<br>Next line.",
    },
    "bilingual",
    "\u5b57\u5730<i>\u601d\u8003</i>\u7684\u3002<strong>\u91cd\u70b9</strong>",
    "",
  );

  assert.match(html, /Think <i>deeply<\/i>/);
  assert.match(html, /<b>carefully<\/b>/);
  assert.match(html, /<u>clearly<\/u>\.<br>Next line/);
  assert.match(html, /\u5b57\u5730<i>\u601d\u8003<\/i>\u7684\u3002<strong>\u91cd\u70b9<\/strong>/);
});

test("subtitle markup renderer keeps attributed and arbitrary HTML escaped", () => {
  const { renderSubtitleInlineMarkup } = loadSidepanelHelpers();
  const html = renderSubtitleInlineMarkup(
    '<img src=x onerror="alert(1)"><i onclick="alert(2)">unsafe</i><script>alert(3)</script>',
  );

  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /&lt;i onclick=&quot;alert\(2\)&quot;&gt;unsafe<\/i>/);
  assert.match(html, /&lt;script&gt;alert\(3\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<img\b|<i\s+onclick|<script\b/);
});

test("background rejects unsupported language fallthrough and malformed batches", () => {
  const source = read("background.js");
  const { validateTranscriptBatchRequest } = loadBackgroundHelpers();
  assert.match(source, /targetLanguage !== "zh"/);
  assert.match(source, /\["transcriptBatch", "interfaceBatch"\]/);
  assert.throws(
    () => validateTranscriptBatchRequest({ segments: [] }),
    /1 to 4 segments/,
  );
  assert.throws(
    () =>
      validateTranscriptBatchRequest({
        segments: [
          { id: "duplicate", text: "first" },
          { id: "duplicate", text: "second" },
        ],
      }),
    /unique and stable/,
  );
});

test("all AI product requests use DeepSeek non-thinking and JSON behavior", async () => {
  const deepSeekRequests = [];
  const successfulFetch = (requests) => async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return {
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "translated" } }],
      }),
    };
  };

  const deepSeek = loadBackgroundHelpers({
    fetchImpl: successfulFetch(deepSeekRequests),
  });
  const deepSeekResult = await deepSeek.requestAiCompletion({
    maxTokens: 128,
    responseFormat: { type: "json_object" },
    messages: [{ role: "user", content: "Hello." }],
  });
  assert.equal(deepSeekResult.text, "translated");
  assert.deepEqual(deepSeekRequests[0].thinking, { type: "disabled" });
  assert.deepEqual(deepSeekRequests[0].response_format, {
    type: "json_object",
  });

  const backgroundSource = read("background.js");
  assert.equal(
    (backgroundSource.match(/await requestAiCompletion\(\{/g) || []).length,
    5,
  );
  assert.doesNotMatch(backgroundSource, /disableThinking/);
  for (const callPath of [
    "handleAnalyzeTranscript",
    "cleanupNoteText",
    "handleExplainSelection",
    "callAiTranslation",
  ]) {
    assert.match(
      backgroundSource,
      new RegExp(`async function ${callPath}\\([\\s\\S]*?requestAiCompletion\\(\\{`),
    );
  }
});

test("blank-line chunks reset provider idle timeout and valid JSON succeeds", async () => {
  const timers = createFakeTimers();
  const helpers = loadBackgroundHelpers({
    setTimeoutImpl: timers.setTimeout,
    clearTimeoutImpl: timers.clearTimeout,
    fetchImpl: async () =>
      streamingResponse([
        encode("\n"),
        encode("\n"),
        encode('{"choices":[{"message":{"content":"translated"}}]}'),
      ]),
  });

  const result = await helpers.callAiTranslation("Translate.", "Hello.");
  assert.equal(result.success, true);
  assert.equal(result.text, "translated");
  assert.equal(timers.createdCount(50_000), 5);
  assert.equal(timers.activeCount(50_000), 0);
  assert.equal(timers.activeCount(120_000), 0);
});

test("provider idle silence aborts with a distinct Retry-able error", async () => {
  const timers = createFakeTimers();
  const helpers = loadBackgroundHelpers({
    setTimeoutImpl: timers.setTimeout,
    clearTimeoutImpl: timers.clearTimeout,
    fetchImpl: async (_url, { signal }) => ({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: () =>
            new Promise((_resolve, reject) => {
              signal.addEventListener("abort", () => {
                const error = new Error("aborted");
                error.name = "AbortError";
                reject(error);
              });
            }),
        }),
      },
    }),
  });

  const request = helpers.callAiTranslation("Translate.", "Hello.");
  await nextTurn();
  timers.fireActive(50_000);
  const result = await request;
  assert.equal(result.success, false);
  assert.equal(result.code, "AI_IDLE_TIMEOUT");
  assert.match(result.error, /inactive for 50 seconds.*Retry/i);
  assert.equal(timers.activeCount(120_000), 0);
});

test("blank-line keepalives cannot evade the provider hard cap", async () => {
  const timers = createFakeTimers();
  let releaseRead;
  let signal;
  const helpers = loadBackgroundHelpers({
    setTimeoutImpl: timers.setTimeout,
    clearTimeoutImpl: timers.clearTimeout,
    fetchImpl: async (_url, options) => {
      signal = options.signal;
      return {
        ok: true,
        status: 200,
        body: {
          getReader: () => ({
            read: () =>
              new Promise((resolve, reject) => {
                releaseRead = () => resolve({ done: false, value: encode("\n") });
                signal.addEventListener("abort", () => {
                  const error = new Error("aborted");
                  error.name = "AbortError";
                  reject(error);
                }, { once: true });
              }),
          }),
        },
      };
    },
  });

  const request = helpers.callAiTranslation("Translate.", "Hello.");
  await nextTurn();
  releaseRead();
  await nextTurn();
  releaseRead();
  await nextTurn();
  assert.equal(timers.activeCount(50_000), 1);
  timers.fireActive(120_000);
  const result = await request;
  assert.equal(result.success, false);
  assert.equal(result.code, "AI_HARD_TIMEOUT");
  assert.match(result.error, /120-second limit.*Retry/i);
  assert.equal(timers.activeCount(50_000), 0);
});

test("provider response reader accepts leading whitespace before JSON", async () => {
  const helpers = loadBackgroundHelpers({
    fetchImpl: async () =>
      streamingResponse([
        encode('  \n\t{"choices":[{"message":{"content":"ok"}}]}'),
      ]),
  });
  const result = await helpers.callAiTranslation("Translate.", "Hello.");
  assert.equal(result.success, true);
  assert.equal(result.text, "ok");
});

test("provider response reader rejects bodies over 2 MiB", async () => {
  const helpers = loadBackgroundHelpers({
    fetchImpl: async () =>
      streamingResponse([new Uint8Array(2 * 1024 * 1024 + 1)]),
  });
  const result = await helpers.callAiTranslation("Translate.", "Hello.");
  assert.equal(result.success, false);
  assert.equal(result.code, "AI_RESPONSE_TOO_LARGE");
  assert.match(result.error, /2 MiB limit/);
});

test("DeepSeek retries one empty transcript JSON response without response_format", async () => {
  const requests = [];
  const helpers = loadBackgroundHelpers({
    fetchImpl: async (url, options) => {
      if (url.startsWith("chrome-extension://")) {
        return { ok: true, text: async () => read("prompts/translation.md") };
      }
      requests.push(JSON.parse(options.body));
      return {
        ok: true,
        json: async () => ({
          choices: [{
            message: {
              content: requests.length === 1
                ? ""
                : '{"segments":[{"id":"segment-0-0","text":"\u4e2d\u6587\u8bd1\u6587\u3002"}]}',
            },
          }],
        }),
      };
    },
  });
  const result = await helpers.handleTranslateContent(
    { segments: [{ id: "segment-0-0", text: "English source sentence." }] },
    "transcriptBatch",
    "zh",
    "Video",
  );
  assert.equal(result.success, true);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0].response_format, { type: "json_object" });
  assert.equal(Object.hasOwn(requests[1], "response_format"), false);
  assert.equal(requests[0].max_tokens, 1536);
});

test("interface batches use the dedicated Overview and Notes translation prompt", async () => {
  const requests = [];
  const helpers = loadBackgroundHelpers({
    fetchImpl: async (url, options) => {
      if (url.startsWith("chrome-extension://")) {
        return { ok: true, text: async () => read("prompts/translation.md") };
      }
      requests.push(JSON.parse(options.body));
      return {
        ok: true,
        json: async () => ({
          choices: [{
            message: {
              content: '{"segments":[{"id":"note-1","text":"\u4e2d\u6587\u7b14\u8bb0\u3002"}]}',
            },
          }],
        }),
      };
    },
  });

  const result = await helpers.handleTranslateContent(
    { segments: [{ id: "note-1", text: "Saved note." }] },
    "interfaceBatch",
    "zh",
    "Video",
  );

  assert.equal(result.success, true);
  assert.equal(result.translatedContent.segments[0].text, "\u4e2d\u6587\u7b14\u8bb0\u3002");
  assert.match(
    requests[0].messages[0].content,
    /chapter titles, summaries, quotes, and saved notes/,
  );
});

test("translation message watchdog rejects, clears its timer, and ignores late replies", async () => {
  let timeoutCallback;
  let timeoutDelay;
  let resolveMessage;
  let clearCount = 0;
  const helpers = loadSidepanelHelpers({
    sendMessage: () =>
      new Promise((resolve) => {
        resolveMessage = resolve;
      }),
    setTimeoutImpl(callback, delay) {
      timeoutCallback = callback;
      timeoutDelay = delay;
      return 73;
    },
    clearTimeoutImpl(id) {
      assert.equal(id, 73);
      clearCount += 1;
    },
  });

  const request = helpers.sendTranslationMessage({
    action: "translateContent",
  });
  assert.equal(timeoutDelay, 260_000);
  timeoutCallback();
  await assert.rejects(request, /timed out after 260 seconds.*Retry/i);
  assert.equal(clearCount, 1);

  resolveMessage({ success: true });
  await Promise.resolve();
  assert.equal(clearCount, 1);

  let successTimeoutCallback;
  let successClearCount = 0;
  const successfulHelpers = loadSidepanelHelpers({
    sendMessage: () => Promise.resolve({ success: true }),
    setTimeoutImpl(callback) {
      successTimeoutCallback = callback;
      return 91;
    },
    clearTimeoutImpl(id) {
      assert.equal(id, 91);
      successClearCount += 1;
    },
  });
  assert.deepEqual(
    await successfulHelpers.sendTranslationMessage({
      action: "translateContent",
    }),
    { success: true },
  );
  assert.equal(successClearCount, 1);
  successTimeoutCallback();
  assert.equal(successClearCount, 1);
});

test("Chinese prompt preserves natural bilingual-learning style rules", () => {
  const prompt = read("prompts/translation.md");
  assert.match(prompt, /Translate the complete thought/);
  assert.match(prompt, /Use 你, never 您/);
  assert.match(prompt, /spaces between Chinese and adjacent English words or digits/);
  assert.match(prompt, /source-language `text`/);
});


function translationFixture(replies, quality = "reviewed") {
  const requests = [];
  const settings = require("../settings.js").normalize({
    provider: "openrouter", aiApiKey: "test-key", translationQuality: quality,
  });
  const helpers = loadBackgroundHelpers({ settings, fetchImpl: async (url, options) => {
    if (url.startsWith("chrome-extension://")) {
      return { ok: true, text: async () => read("prompts/translation.md") };
    }
    requests.push(JSON.parse(options.body));
    const reply = replies[requests.length - 1];
    if (reply instanceof Error) throw reply;
    return { ok: true, json: async () => ({ choices: [{ message: { content: JSON.stringify(reply) } }] }) };
  } });
  return { helpers, requests };
}

test("reviewed translation sends source, neighboring context and draft to a separate revision call", async () => {
  const content = {
    segments: [{ id: "segment-1-5000", text: "It did not happen in 2025. My back-of-the-envelope calculation was wrong." }],
    context: { before: "We are discussing the experiment.", after: "It happened in 2026." },
  };
  const draft = { segments: [{ id: "segment-1-5000", text: "这发生在 2025 年。我的信封背面计算错了。" }] };
  const revised = { segments: [{ id: "segment-1-5000", text: "这并没有发生在 2025 年。我的粗略估算错了。" }] };
  const { helpers, requests } = translationFixture([draft, revised]);
  const result = await helpers.handleTranslateContent(content, "transcriptBatch", "zh", "Experiment");
  assert.equal(result.success, true);
  assert.equal(result.quality, "reviewed");
  assert.equal(result.translatedContent.segments[0].text, revised.segments[0].text);
  assert.equal(requests.length, 2);
  assert.deepEqual(JSON.parse(requests[0].messages[1].content), content);
  const reviewInput = JSON.parse(requests[1].messages[1].content);
  assert.deepEqual(reviewInput.source, content);
  assert.deepEqual(reviewInput.draft, draft);
  assert.match(requests[1].messages[0].content, /original source/);
  assert.match(requests[1].messages[0].content, /negation, numbers, dates/);
  assert.equal(requests[1].temperature, 0.1);
});

test("review failure or missing source IDs cannot turn an unreviewed draft into success", async () => {
  const content = { segments: [{ id: "s1", text: "This English sentence needs careful translation." }] };
  const draft = { segments: [{ id: "s1", text: "这句英文需要认真翻译。" }] };
  for (const reply of [new Error("Provider unavailable"), { segments: [{ id: "wrong-id", text: "这不是对应的段落。" }] }]) {
    const { helpers, requests } = translationFixture([draft, reply]);
    const result = await helpers.handleTranslateContent(content, "transcriptBatch", "zh", "Video");
    assert.equal(result.success, false);
    assert.match(result.error, /精校/);
    assert.equal(result.translatedContent, undefined);
    assert.equal(requests.length, 2);
  }
});

test("an incomplete draft stops before the paid review call", async () => {
  const content = { segments: [
    { id: "s1", text: "First complete English source sentence." },
    { id: "s2", text: "Second complete English source sentence." },
  ] };
  const { helpers, requests } = translationFixture([{ segments: [{ id: "s1", text: "第一句完整英文。" }] }]);
  const result = await helpers.handleTranslateContent(content, "transcriptBatch", "zh", "Video");
  assert.equal(result.success, false);
  assert.match(result.error, /初译缺少/);
  assert.equal(requests.length, 1);
});

test("neighbor context remains bounded and excludes the requested subtitle rows", () => {
  const { getNeighboringTranscriptContext } = loadSidepanelHelpers();
  const segments = ["Earlier", "Before", "Translate this", "And this", "After", "Later"].map(text => ({ text }));
  assert.deepEqual(JSON.parse(JSON.stringify(getNeighboringTranscriptContext([2, 3], segments))), { before: "Earlier Before", after: "After Later" });
  assert.equal(getNeighboringTranscriptContext([0], segments).before, "");
  assert.equal(getNeighboringTranscriptContext([5], segments).after, "");
  assert.equal(getNeighboringTranscriptContext([1], [{ text: "x".repeat(2500) }, { text: "source" }, { text: "y".repeat(2500) }]).before.length, 2000);
});

test("invalid neighboring context is rejected before any provider call", async () => {
  for (const context of ["not an object", { before: "x".repeat(2001) }, { after: 1 }]) {
    const { helpers, requests } = translationFixture([]);
    const result = await helpers.handleTranslateContent({ segments: [{ id: "s1", text: "Source English sentence." }], context }, "transcriptBatch", "zh", "Video");
    assert.equal(result.success, false);
    assert.equal(requests.length, 0);
  }
});

test("changed quality settings cannot write a quick response into a reviewed cache", async () => {
  const { helpers, requests } = translationFixture([], "quick");
  const result = await helpers.handleTranslateContent({ segments: [{ id: "s1", text: "Source English sentence." }] }, "transcriptBatch", "zh", "Video", "reviewed");
  assert.equal(result.success, false);
  assert.match(result.error, /settings changed/);
  assert.equal(requests.length, 0);
  const panel = loadSidepanelHelpers();
  assert.match(panel.transcriptTranslationCacheKey({ id: "s1" }), /baoyu-subtitles-v1:reviewed:semantic:s1$/);
  assert.match(panel.interfaceTranslationCacheKey("overview", "s1", "Source"), /baoyu-subtitles-v1:reviewed:overview:/);
});

test("large valid batches receive bounded output room without losing source text", async () => {
  const content = { segments: Array.from({ length: 3 }, (_, index) => ({ id: `s${index}`, text: "word ".repeat(780).trim() })) };
  const { helpers, requests } = translationFixture([{ segments: content.segments.map(({ id }) => ({ id, text: "完整译文。" })) }], "quick");
  const result = await helpers.handleTranslateContent(content, "transcriptBatch", "zh", "Video");
  assert.equal(result.success, true);
  assert.equal(result.quality, "quick");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].max_tokens, 8192);
  assert.deepEqual(JSON.parse(requests[0].messages[1].content).segments, content.segments);
});
