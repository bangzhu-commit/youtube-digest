const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const VIDEO_ID = "ydTeb_I0b94";
const ORIGIN = "https://www.youtube.com";
const source = fs.readFileSync(path.join(__dirname, "../native-transcript.js"), "utf8");

function playerData(videoId = VIDEO_ID, tracks = []) {
  return {
    videoDetails: { videoId },
    playabilityStatus: { status: "OK" },
    captions: { playerCaptionsTracklistRenderer: { captionTracks: tracks } },
  };
}

function jsonResponse(data) {
  return { ok: true, status: 200, text: async () => JSON.stringify(data), json: async () => data };
}

function textResponse(text) {
  return { ok: true, status: 200, text: async () => text, json: async () => JSON.parse(text) };
}

function captionData(text = "Hello world", startMs = 1250, durationMs = 2200) {
  return { events: [{ tStartMs: startMs, dDurationMs: durationMs, segs: [{ utf8: text }] }] };
}

function timedtext(label, videoId = VIDEO_ID) {
  return `${ORIGIN}/api/timedtext?v=${videoId}&lang=en&signature=${label}`;
}

function matchesTag(selector, tag) {
  return selector.split(",").some((part) => part.trim() === tag);
}

function loadingNode(mode = "visible") {
  return {
    textContent: "loading", hidden: mode === "hidden-self",
    closest: () => mode === "hidden-ancestor" ? {} : null,
    getClientRects: () => mode === "zero-rect" ? [] : [{ width: 20, height: 20 }],
  };
}

function segment(row, tag) {
  const modern = tag !== "ytd-transcript-segment-renderer";
  const alternateModern = tag === "yt-transcript-segment-view-model";
  const children = [
    { className: modern ? (alternateModern ? "ytTranscriptSegmentViewModelTimestamp" : "ytwTranscriptSegmentViewModelTimestamp") : "segment-timestamp", textContent: row.stamp },
    { className: modern ? (alternateModern ? "yt-core-attributed-string" : "ytAttributedStringHost") : "segment-text", textContent: row.text },
  ];
  // Some modern panels wrap the timestamp in the same span class as caption text.
  // The text reader must skip that candidate before trying another text selector.
  if (alternateModern) children.splice(1, 0, { className: "ytAttributedStringHost", textContent: row.stamp });
  const findChildren = (selector) => children.filter((child) => selector.split(",").some((part) =>
    part.trim() === `.${child.className}` || part.trim() === `span.${child.className}`));
  return {
    tagName: tag.toUpperCase(),
    textContent: `${row.stamp} ${row.text}`,
    querySelector: (selector) => findChildren(selector)[0] || null,
    querySelectorAll: findChildren,
    getAttribute: () => null,
  };
}

function fixture({
  tracks = [], playerResponse = playerData(VIDEO_ID, tracks), initialPlayerResponse,
  rows = [], segmentTag = "ytd-transcript-segment-renderer", fetchImpl,
  transcriptButton = false, panelBusy = false, loadingNodes = [], onWait,
} = {}) {
  const calls = [];
  const nodes = rows.map((row) => segment(row, segmentTag));
  const panel = {
    querySelectorAll: (selector) => /spinner|aria-busy/.test(selector) ? sandbox.loadingNodes
      : matchesTag(selector, segmentTag) ? sandbox.domNodes : [],
    querySelector: (selector) => /spinner|aria-busy/.test(selector) ? sandbox.loadingNodes[0] || null : null,
    getAttribute: () => null,
  };
  const webContext = {
    client: {
      clientName: "WEB", clientVersion: "2.20261005.01.00",
      visitorData: "public-visitor-data", hl: "en", gl: "US",
    },
    user: { lockedSafetyMode: false },
    request: { useSsl: true },
  };
  const config = {
    INNERTUBE_CONTEXT: webContext,
    INNERTUBE_CLIENT_VERSION: webContext.client.clientVersion,
    INNERTUBE_API_KEY: "PAGE_API_KEY_MUST_STAY_IN_PAGE",
  };
  const ytcfg = { get: (name) => config[name], data_: config };
  const button = { click: () => { sandbox.buttonClicks += 1; } };
  const sandbox = {
    URL, AbortSignal, Headers,
    location: { href: `${ORIGIN}/watch?v=${VIDEO_ID}`, origin: ORIGIN },
    pagePlayerResponse: playerResponse, playerReads: 0, domNodes: nodes, waitCalls: 0, buttonClicks: 0,
    loadingNodes: panelBusy ? [loadingNode()] : loadingNodes,
    window: { ytInitialPlayerResponse: initialPlayerResponse, ytcfg }, ytcfg,
    setTimeout: (callback) => { sandbox.waitCalls += 1; onWait?.(sandbox); callback(); return 0; },
    fetch: async (url, options = {}) => {
      const call = { url: String(url), options };
      calls.push(call);
      if (fetchImpl) return fetchImpl(call, sandbox);
      assert.equal(new URL(call.url).pathname, "/youtubei/v1/player", "Only the native player fallback may request data without a supplied caption fixture");
      return jsonResponse(playerData());
    },
    document: {
      getElementById: (id) => id === "movie_player" ? { getPlayerResponse: () => {
        sandbox.playerReads += 1;
        return sandbox.pagePlayerResponse;
      } } : null,
      querySelector: (selector) => {
        if (selector.includes("engagement-panel-searchable-transcript")) return panel;
        if (transcriptButton && selector.includes("ytd-video-description-transcript-section-renderer")) return button;
        return null;
      },
      querySelectorAll: (selector) => matchesTag(selector, segmentTag) ? nodes : [],
    },
  };
  vm.runInNewContext(source, sandbox);
  return { read: sandbox.readNativeTranscript, calls, sandbox, webContext };
}

function playerRequest(call) {
  assert.equal(new URL(call.url).pathname, "/youtubei/v1/player");
  assert.equal(call.options.method, "POST");
  return JSON.parse(call.options.body);
}

function assertNoCredentialsPassed(calls) {
  assert.ok(!JSON.stringify(calls).includes("PAGE_API_KEY_MUST_STAY_IN_PAGE"));
  for (const call of calls) {
    assert.equal(new URL(call.url).searchParams.has("key"), false);
    const headers = call.options.headers;
    const names = headers?.entries ? [...headers.entries()].map(([name]) => name) : Object.keys(headers || {});
    assert.ok(names.every((name) => !/authorization|api[-_]?key|identity[-_]?token|^user-agent$/i.test(name)), "Native requests must not forward authorization headers, API keys, or a custom User-Agent");
  }
}

test("the original signed caption URL is parsed before adding fmt=json3", async () => {
  const baseUrl = `${timedtext("original-token")}&fmt=srv3`;
  const { read, calls } = fixture({
    tracks: [{ baseUrl, languageCode: "en-US" }],
    fetchImpl: async () => textResponse(JSON.stringify(captionData("Hello\nworld"))),
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, baseUrl, "Do not rewrite signed caption parameters on the first request");
  assert.equal(result.language, "en-US");
  assert.equal(result.transcript[0].language, "en-US");
  assert.equal(result.transcript[0].start, 1.25);
  assert.equal(result.transcript[0].duration, 2.2);
  assert.equal(result.transcriptTextTimestamped, "[0:01] Hello world");
  assertNoCredentialsPassed(calls);
});

test("an empty 200 caption body retries json3 and then requests the IOS player", async () => {
  const baseUrl = timedtext("page-track");
  const iosUrl = timedtext("ios-track");
  const { read, calls } = fixture({
    tracks: [{ baseUrl, languageCode: "en" }],
    fetchImpl: async (call) => {
      if (new URL(call.url).pathname === "/youtubei/v1/player") {
        const request = playerRequest(call);
        assert.equal(request.videoId, VIDEO_ID);
        assert.equal(request.context.client.clientName, "IOS");
        assert.equal(request.context.client.clientVersion, "20.10.3");
        return jsonResponse(playerData(VIDEO_ID, [{ baseUrl: iosUrl, languageCode: "en" }]));
      }
      return call.url === iosUrl ? jsonResponse(captionData("Recovered caption")) : textResponse("");
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-player-captions");
  assert.equal(result.transcriptText, "Recovered caption");
  assert.equal(calls.length, 4);
  assert.equal(calls[0].url, baseUrl);
  const alternateUrl = new URL(baseUrl);
  alternateUrl.searchParams.set("fmt", "json3");
  assert.equal(calls[1].url, alternateUrl.href);
  assert.equal(new URL(calls[2].url).pathname, "/youtubei/v1/player");
  assert.equal(calls[3].url, iosUrl);
  assertNoCredentialsPassed(calls);
});

test("a json3 variant is only used after the original response has no valid rows", async () => {
  const baseUrl = timedtext("variant-track");
  const { read, calls } = fixture({
    tracks: [{ baseUrl, languageCode: "en" }],
    fetchImpl: async (call) => new URL(call.url).searchParams.get("fmt") === "json3"
      ? jsonResponse(captionData("Variant worked", 9000, 1000))
      : jsonResponse({ events: [{ tStartMs: 0, segs: [] }] }),
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.transcriptText, "Variant worked");
  assert.equal(result.transcript[0].start, 9);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, baseUrl);
});

test("the WEB fallback uses the current page client context after IOS and ANDROID have no usable captions", async () => {
  const baseUrl = timedtext("web-track");
  const { read, calls, webContext } = fixture({
    fetchImpl: async (call) => {
      if (new URL(call.url).pathname !== "/youtubei/v1/player") return jsonResponse(captionData("WEB recovered"));
      const request = playerRequest(call);
      assert.equal(request.videoId, VIDEO_ID);
      return jsonResponse(request.context.client.clientName === "WEB" ? playerData(VIDEO_ID, [{ baseUrl, languageCode: "en" }]) : playerData());
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-player-captions");
  assert.equal(result.transcriptText, "WEB recovered");
  assert.equal(calls.length, 4);
  const ios = playerRequest(calls[0]);
  const android = playerRequest(calls[1]);
  const web = playerRequest(calls[2]);
  assert.equal(ios.context.client.clientName, "IOS");
  assert.equal(ios.context.client.clientVersion, "20.10.3");
  assert.equal(android.context.client.clientName, "ANDROID");
  assert.equal(android.context.client.clientVersion, "20.10.38");
  assert.equal(web.context.client.clientName, "WEB");
  assert.equal(web.context.client.clientVersion, webContext.client.clientVersion);
  assert.equal(web.context.client.visitorData, webContext.client.visitorData);
  assert.equal(web.context.client.hl, webContext.client.hl);
  assert.equal(calls[3].url, baseUrl);
  assertNoCredentialsPassed(calls);
});

test("an explicit foreign player video ID is rejected even if its caption URL claims the current video", async () => {
  const staleUrl = timedtext("stale-track");
  const currentUrl = timedtext("current-track");
  const stalePlayer = playerData("another_id", [{ baseUrl: staleUrl, languageCode: "en" }]);
  const { read, calls } = fixture({
    playerResponse: stalePlayer, initialPlayerResponse: stalePlayer,
    fetchImpl: async (call) => {
      if (new URL(call.url).pathname !== "/youtubei/v1/player") {
        assert.equal(call.url, currentUrl, "A stale player's captions must never be fetched");
        return jsonResponse(captionData("Current video only"));
      }
      const request = playerRequest(call);
      return jsonResponse(request.context.client.clientName === "WEB" ? playerData(VIDEO_ID, [{ baseUrl: currentUrl, languageCode: "en" }]) : stalePlayer);
    },
  });
  assert.equal((await read(VIDEO_ID)).transcriptText, "Current video only");
  assert.equal(calls.length, 4);
  assert.ok(calls.every((call) => call.url !== staleUrl));
});

test("ANDROID captions recover after IOS fails without trying WEB or a custom User-Agent", async () => {
  const baseUrl = timedtext("android-track");
  const { read, calls } = fixture({
    fetchImpl: async (call) => {
      if (new URL(call.url).pathname !== "/youtubei/v1/player") return jsonResponse(captionData("ANDROID recovered", 12500, 1000));
      const request = playerRequest(call);
      assert.equal(request.videoId, VIDEO_ID);
      if (request.context.client.clientName === "IOS") return jsonResponse(playerData());
      assert.equal(request.context.client.clientName, "ANDROID");
      assert.equal(request.context.client.clientVersion, "20.10.38");
      return jsonResponse(playerData(VIDEO_ID, [{ baseUrl, languageCode: "fr" }]));
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-player-captions");
  assert.equal(result.transcriptText, "ANDROID recovered");
  assert.equal(result.language, "fr");
  assert.equal(result.transcript[0].language, "fr");
  assert.equal(result.transcript[0].start, 12.5);
  assert.equal(calls.length, 3);
  assert.equal(calls[2].url, baseUrl);
  assertNoCredentialsPassed(calls);
});

test("player responses without video metadata can use a caption URL bound to the current video", async (t) => {
  for (const metadata of ["no videoDetails", "videoDetails without videoId"]) {
    await t.test(metadata, async () => {
      const baseUrl = timedtext("metadata-free-track");
      const data = playerData(VIDEO_ID, [{ baseUrl, languageCode: "en" }]);
      if (metadata === "no videoDetails") delete data.videoDetails;
      else data.videoDetails = {};
      const { read, calls } = fixture({
        fetchImpl: async (call) => new URL(call.url).pathname === "/youtubei/v1/player"
          ? jsonResponse(data) : jsonResponse(captionData("Current video without metadata")),
      });
      const result = await read(VIDEO_ID);
      assert.equal(result.success, true);
      assert.equal(result.source, "youtube-player-captions");
      assert.equal(result.transcriptText, "Current video without metadata");
      assert.equal(calls.length, 2);
      assert.equal(calls[1].url, baseUrl);
    });
  }
});

test("missing player metadata never permits a caption URL for another video", async () => {
  const foreignCaption = timedtext("wrong-video", "another_id");
  const data = playerData(VIDEO_ID, [{ baseUrl: foreignCaption, languageCode: "en" }]);
  delete data.videoDetails;
  const { read, calls } = fixture({
    fetchImpl: async (call) => {
      const request = playerRequest(call);
      return jsonResponse(request.context.client.clientName === "IOS" ? data : playerData());
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, false);
  assert.equal(result.error, "NATIVE_TRANSCRIPT_UNAVAILABLE");
  assert.equal(calls.length, 3);
  assert.ok(calls.every((call) => new URL(call.url).pathname === "/youtubei/v1/player"), "Video-bound caption URLs remain mandatory when player metadata is absent");
});

test("caption requests reject foreign origins, invalid paths, and missing or mismatched video IDs", async (t) => {
  const rejected = [
    "https://attacker.example/api/timedtext?v=ydTeb_I0b94",
    "https://www.youtube.com.attacker.example/api/timedtext?v=ydTeb_I0b94",
    "https://youtube.com/api/timedtext?v=ydTeb_I0b94",
    "https://user@www.youtube.com/api/timedtext?v=ydTeb_I0b94",
    "https://user:password@www.youtube.com/api/timedtext?v=ydTeb_I0b94",
    "https://www.youtube.com:8443/api/timedtext?v=ydTeb_I0b94",
    "http://www.youtube.com/api/timedtext?v=ydTeb_I0b94",
    "https://www.youtube.com/not-timedtext?v=ydTeb_I0b94",
    "https://www.youtube.com/api/timedtext?v=another_id",
    "https://www.youtube.com/api/timedtext?lang=en",
  ];
  for (const baseUrl of rejected) {
    await t.test(baseUrl, async () => {
      const { read, calls } = fixture({ tracks: [{ baseUrl, languageCode: "en" }] });
      const result = await read(VIDEO_ID);
      assert.equal(result.success, false);
      assert.equal(result.error, "NATIVE_TRANSCRIPT_UNAVAILABLE");
      assert.ok(calls.every((call) => new URL(call.url).pathname === "/youtubei/v1/player"), "Untrusted timedtext URLs must be rejected without any fetch");
    });
  }
});

test("modern transcript DOM is read after caption and player network routes fail", async (t) => {
  for (const segmentTag of ["transcript-segment-view-model", "yt-transcript-segment-view-model"]) {
    await t.test(segmentTag, async () => {
      const baseUrl = timedtext("unavailable-track");
      const { read, calls, sandbox } = fixture({
        tracks: [{ baseUrl, languageCode: "en" }], segmentTag,
        rows: [{ stamp: "1:02:03", text: "  first\nline  " }, { stamp: "1:02:08", text: "next line" }],
        fetchImpl: async (call) => new URL(call.url).pathname === "/youtubei/v1/player"
          ? jsonResponse(playerData()) : textResponse(""),
      });
      const result = await read(VIDEO_ID);
      assert.equal(result.success, true);
      assert.equal(result.source, "youtube-transcript");
      assert.equal(result.transcriptText, "first line next line");
      assert.equal(result.transcript.length, 2);
      assert.equal(result.transcript[0].start, 3723);
      assert.equal(result.transcript[0].duration, 5);
      assert.equal(result.transcript[1].start, 3728);
      assert.equal(result.language, null, "DOM language is unknown unless the panel provides a label");
      assert.equal(calls.length, 5);
      assert.equal(calls[0].url, baseUrl);
      assert.equal(new URL(calls[1].url).searchParams.get("fmt"), "json3");
      assert.equal(playerRequest(calls[2]).context.client.clientName, "IOS");
      assert.equal(playerRequest(calls[3]).context.client.clientName, "ANDROID");
      assert.equal(playerRequest(calls[4]).context.client.clientName, "WEB");
      assert.equal(sandbox.waitCalls, 1, "The initial DOM and next poll must provide identical snapshots");
    });
  }
});

test("a navigation before extraction prevents all native requests", async () => {
  const { read, calls } = fixture({ tracks: [{ baseUrl: timedtext("unused-track"), languageCode: "en" }] });
  const result = await read("another_id");
  assert.equal(result.success, false);
  assert.equal(result.error, "VIDEO_CHANGED");
  assert.equal(calls.length, 0);
});

test("an active transcript spinner prevents a partial DOM result from replacing complete captions", async () => {
  const { read, calls } = fixture({
    panelBusy: true, rows: [{ stamp: "0:00", text: "Only a partial line" }],
    tracks: [{ baseUrl: timedtext("complete-track"), languageCode: "en" }],
    fetchImpl: async () => jsonResponse(captionData("Complete caption response")),
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-captions");
  assert.equal(result.transcriptText, "Complete caption response");
  assert.equal(calls.length, 1);
});

test("hidden and zero-rectangle spinners do not block a stable transcript DOM", async (t) => {
  for (const mode of ["hidden-self", "hidden-ancestor", "zero-rect"]) {
    await t.test(mode, async () => {
      const { read, calls, sandbox } = fixture({
        loadingNodes: [loadingNode(mode)],
        rows: [{ stamp: "0:00", text: "Readable stable panel" }, { stamp: "0:05", text: "Next line" }],
      });
      const result = await read(VIDEO_ID);
      assert.equal(result.success, true);
      assert.equal(result.source, "youtube-transcript");
      assert.equal(result.transcriptText, "Readable stable panel Next line");
      assert.equal(calls.length, 3);
      assert.equal(sandbox.waitCalls, 1);
    });
  }
});

test("visible spinners still block DOM, including a visible spinner after a hidden one", async (t) => {
  for (const mode of ["visible", "hidden-first-visible-second"]) {
    await t.test(mode, async () => {
      const { read, calls, sandbox } = fixture({
        loadingNodes: mode === "visible" ? [loadingNode()] : [loadingNode("hidden-self"), loadingNode()],
        rows: [{ stamp: "0:00", text: "Partial panel while still loading" }],
      });
      const result = await read(VIDEO_ID);
      assert.equal(result.success, false);
      assert.equal(result.error, "NATIVE_TRANSCRIPT_UNAVAILABLE");
      assert.equal(calls.length, 3);
      assert.equal(sandbox.waitCalls, 20, "A visible loading indicator must never produce a stable transcript result");
    });
  }
});

test("a retained transcript from the previous SPA video is ignored", async () => {
  const baseUrl = timedtext("current-track");
  const { read, calls } = fixture({
    playerResponse: playerData("another_id"), rows: [{ stamp: "0:00", text: "Previous video's transcript" }],
    initialPlayerResponse: playerData(VIDEO_ID, [{ baseUrl, languageCode: "en" }]),
    fetchImpl: async () => jsonResponse(captionData("Current video's captions")),
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-captions");
  assert.equal(result.transcriptText, "Current video's captions");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, baseUrl);
});

test("a single DOM line at 40:00 cannot hide the beginning of a complete caption response", async () => {
  const { read, calls } = fixture({
    tracks: [{ baseUrl: timedtext("complete-response"), languageCode: "en" }],
    rows: [{ stamp: "40:00", text: "Only the currently rendered late line" }],
    fetchImpl: async () => jsonResponse({ events: [
      { tStartMs: 0, dDurationMs: 2000, segs: [{ utf8: "Beginning of the video" }] },
      { tStartMs: 2400000, dDurationMs: 3000, segs: [{ utf8: "Later in the video" }] },
    ] }),
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-captions");
  assert.equal(result.transcriptText, "Beginning of the video Later in the video");
  assert.equal(result.transcript.length, 2);
  assert.equal(result.transcript[0].start, 0);
  assert.equal(result.transcript[1].start, 2400);
  assert.equal(calls.length, 1);
});

test("DOM polling re-reads a stale SPA player until it belongs to the current video", async () => {
  const { read, calls, sandbox } = fixture({
    playerResponse: playerData("another_id"),
    rows: [{ stamp: "0:00", text: "Current video panel" }, { stamp: "0:05", text: "Next line" }],
    onWait: (state) => { state.pagePlayerResponse = playerData(); },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.source, "youtube-transcript");
  assert.equal(result.transcriptText, "Current video panel Next line");
  assert.equal(calls.length, 3, "IOS, ANDROID, and WEB fail before DOM polling");
  assert.equal(sandbox.waitCalls, 2, "Two matching snapshots are needed after the player's video ID catches up");
  assert.ok(sandbox.playerReads >= 4, "DOM polls must read the live player rather than the initial stale snapshot");
});

test("DOM fallback waits for identical snapshots when transcript rows change during loading", async () => {
  const { read, sandbox } = fixture({
    rows: [{ stamp: "0:00", text: "First line" }],
    onWait: (state) => {
      if (state.waitCalls === 1) state.domNodes.push(segment({ stamp: "0:05", text: "Newly rendered line" }, "ytd-transcript-segment-renderer"));
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, true);
  assert.equal(result.transcriptText, "First line Newly rendered line");
  assert.equal(result.transcript.length, 2);
  assert.equal(sandbox.waitCalls, 2, "A changed first poll must reset the stable-snapshot requirement");
});

test("navigation during an empty timedtext response stops further fallback requests", async () => {
  const { read, calls } = fixture({
    tracks: [{ baseUrl: timedtext("page-track"), languageCode: "en" }],
    fetchImpl: async (_call, sandbox) => {
      sandbox.location.href = `${ORIGIN}/watch?v=another_id`;
      return textResponse("");
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.error, "VIDEO_CHANGED");
  assert.equal(calls.length, 1, "Changing videos must not trigger json3 or player retries");
});

test("navigation during the player response prevents captions from leaving the changed page", async () => {
  const { read, calls } = fixture({
    fetchImpl: async (call, sandbox) => {
      playerRequest(call);
      sandbox.location.href = `${ORIGIN}/watch?v=another_id`;
      return jsonResponse(playerData(VIDEO_ID, [{ baseUrl: timedtext("previous-video-track"), languageCode: "en" }]));
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.error, "VIDEO_CHANGED");
  assert.equal(calls.length, 1, "Do not fetch captions or try WEB after navigation");
});

test("navigation while reading a caption response body invalidates empty, valid, and malformed bodies", async (t) => {
  for (const [label, body] of [
    ["empty body", ""],
    ["valid caption body", JSON.stringify(captionData("Previous video's text"))],
    ["malformed JSON body", "{invalid-json"],
  ]) {
    await t.test(label, async () => {
      const { read, calls } = fixture({
        tracks: [{ baseUrl: timedtext("page-track"), languageCode: "en" }],
        fetchImpl: async (_call, sandbox) => ({
          ok: true, status: 200,
          text: async () => {
            sandbox.location.href = `${ORIGIN}/watch?v=another_id`;
            return body;
          },
        }),
      });
      const result = await read(VIDEO_ID);
      assert.equal(result.success, false);
      assert.equal(result.error, "VIDEO_CHANGED");
      assert.equal(calls.length, 1, "Reading a response body must not restart extraction for another video");
    });
  }
});

test("navigation while decoding player JSON prevents use of that player's captions", async () => {
  const { read, calls } = fixture({
    fetchImpl: async (call, sandbox) => {
      playerRequest(call);
      return {
        ok: true, status: 200,
        json: async () => {
          sandbox.location.href = `${ORIGIN}/watch?v=another_id`;
          return playerData(VIDEO_ID, [{ baseUrl: timedtext("previous-video-track"), languageCode: "en" }]);
        },
      };
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.error, "VIDEO_CHANGED");
  assert.equal(calls.length, 1);
});

test("a failed final WEB request after navigation must not click the new video's transcript button", async () => {
  const { read, calls, sandbox } = fixture({
    transcriptButton: true,
    fetchImpl: async (call, state) => {
      const request = playerRequest(call);
      if (request.context.client.clientName !== "WEB") return jsonResponse(playerData());
      assert.equal(request.context.client.clientName, "WEB");
      state.location.href = `${ORIGIN}/watch?v=another_id`;
      throw new Error("WEB request failed during SPA navigation");
    },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.success, false);
  assert.equal(result.error, "VIDEO_CHANGED");
  assert.equal(calls.length, 3);
  assert.equal(sandbox.buttonClicks, 0, "The final failed player request must not operate the changed page's UI");
});

test("navigation while waiting for YouTube's transcript panel invalidates extraction", async () => {
  const { read, sandbox } = fixture({
    transcriptButton: true,
    onWait: (state) => { state.location.href = `${ORIGIN}/watch?v=another_id`; },
  });
  const result = await read(VIDEO_ID);
  assert.equal(result.error, "VIDEO_CHANGED");
  assert.notEqual(sandbox.location.href, `${ORIGIN}/watch?v=${VIDEO_ID}`);
});

// XML parsing is checked in the real browser harness. This Node suite intentionally
// has no DOMParser shim: it would otherwise verify the shim instead of the browser.
