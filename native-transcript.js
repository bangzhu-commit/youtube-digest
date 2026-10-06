/* Runs in the active YouTube page's MAIN world. No keys or extension storage
 * are passed into the page. Only current-video captions leave this function.
 * Player/XML fallbacks adapted from Defuddle 0.19.4 (MIT); see notices. */
async function readNativeTranscript(videoId) {
  const isCurrentVideo = () => new URL(location.href).searchParams.get("v") === videoId;
  const changed = () => ({ success: false, error: "VIDEO_CHANGED" });
  if (!isCurrentVideo()) return changed();
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
  const formatResult = (rows, language, source) => {
    if (!isCurrentVideo()) return changed();
    const seen = new Set();
    const transcript = rows.filter((row) => {
      row.text = clean(row.text);
      if (!row.text || !Number.isFinite(row.start) || row.start < 0) return false;
      row.duration = Number.isFinite(row.duration) && row.duration >= 0 ? row.duration : 0;
      const key = row.start + "\n" + row.text;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).sort((a, b) => a.start - b.start);
    if (!transcript.length) return null;
    return {
      success: true, transcript, language: language || null, source,
      transcriptText: transcript.map((row) => row.text).join(" "),
      transcriptTextTimestamped: transcript.map((row) => {
        const seconds = Math.floor(row.start);
        return `[${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}] ${row.text}`;
      }).join("\n"),
    };
  };
  let pageResponse;
  try { pageResponse = document.getElementById("movie_player")?.getPlayerResponse?.(); } catch (_) {}
  const playerResponse = pageResponse?.videoDetails?.videoId === videoId ? pageResponse
    : window.ytInitialPlayerResponse?.videoDetails?.videoId === videoId ? window.ytInitialPlayerResponse : null;
  const captionTracks = (response) => {
    const tracks = response?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
    return Array.isArray(tracks) ? tracks.filter((track) => track && typeof track === "object") : [];
  };
  const safeCaptionUrl = (baseUrl) => {
    try {
      const url = new URL(baseUrl);
      if (url.origin !== new URL(location.href).origin || url.origin !== "https://www.youtube.com"
        || url.username || url.password
        || url.pathname !== "/api/timedtext" || url.searchParams.get("v") !== videoId) return null;
      return url;
    } catch (_) { return null; }
  };
  const parseJson = (data, language) => (Array.isArray(data?.events) ? data.events : [])
    .filter((event) => Array.isArray(event.segs)).map((event) => ({
      text: event.segs.map((part) => part.utf8 || "").join(""),
      start: Number(event.tStartMs) / 1000,
      duration: Number(event.dDurationMs || 0) / 1000, language,
    }));
  const parseBody = (body, language) => {
    const text = body.trim();
    if (!text) return [];
    if (text.startsWith("{")) return parseJson(JSON.parse(text), language);
    if (!text.startsWith("<") || typeof DOMParser === "undefined") return [];
    const xml = new DOMParser().parseFromString(text, "application/xml");
    if (xml.querySelector("parsererror")) return [];
    const number = (node, name) => {
      const value = node.getAttribute(name);
      return value === null || value.trim() === "" ? NaN : Number(value);
    };
    const paragraphs = [...xml.getElementsByTagName("p")];
    if (paragraphs.length) return paragraphs.map((node) => ({
      text: node.textContent, start: number(node, "t") / 1000,
      duration: number(node, "d") / 1000, language,
    }));
    return [...xml.getElementsByTagName("text")].map((node) => ({
      text: node.textContent, start: number(node, "start"),
      duration: number(node, "dur"), language,
    }));
  };
  const tried = new Set();
  const fetchTracks = async (tracks, source) => {
    const rank = (track) => (String(track.languageCode || "").startsWith("en") ? 0 : 2)
      + (track.kind === "asr" ? 1 : 0);
    for (const track of [...tracks].sort((a, b) => rank(a) - rank(b)).slice(0, 2)) {
      const original = safeCaptionUrl(track.baseUrl);
      if (!original) continue;
      const jsonUrl = new URL(original.href);
      jsonUrl.searchParams.set("fmt", "json3");
      // Keep YouTube's signed URL intact first; forcing json3 can yield empty 200s.
      for (const url of [original, jsonUrl]) {
        if (!isCurrentVideo()) return changed();
        if (tried.has(url.href)) continue;
        tried.add(url.href);
        try {
          const response = await fetch(url.href, { credentials: "include", signal: AbortSignal.timeout(4000) });
          if (!isCurrentVideo()) return changed();
          if (!response.ok) continue;
          const language = track.languageCode || null;
          const rows = typeof response.text === "function"
            ? parseBody(await response.text(), language) : parseJson(await response.json(), language);
          const result = formatResult(rows, language, source);
          if (result) return result;
        } catch (_) { /* Empty, invalid or expired caption URL: try another route. */ }
      }
    }
    return null;
  };
  const readDom = () => {
    if (!isCurrentVideo()) return changed();
    // Re-check the player on every poll: it can finish SPA navigation while waiting.
    let currentPlayer;
    try { currentPlayer = document.getElementById("movie_player")?.getPlayerResponse?.(); } catch (_) { return null; }
    if (currentPlayer?.videoDetails?.videoId && currentPlayer.videoDetails.videoId !== videoId) return null;
    const panel = document.querySelector('ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]')
      || document.querySelector('ytd-engagement-panel-section-list-renderer[visibility="ENGAGEMENT_PANEL_VISIBILITY_EXPANDED"]')
      || document.querySelector("ytd-transcript-renderer, transcript-renderer");
    const loadingSelector = 'tp-yt-paper-spinner[active], yt-spinner, [aria-busy="true"]';
    const firstLoading = panel?.querySelector?.(loadingSelector);
    const loading = firstLoading ? [firstLoading, ...(panel.querySelectorAll?.(loadingSelector) || [])] : [];
    if ([...loading].some((node) => !node.hidden && !node.closest?.("[hidden]")
      && (typeof node.getClientRects !== "function" || node.getClientRects().length > 0))) return null;
    const nodes = panel?.querySelectorAll("ytd-transcript-segment-renderer, transcript-segment-view-model, yt-transcript-segment-view-model") || [];
    const rows = [];
    for (const node of nodes) {
      const stampNode = node.querySelector(".segment-timestamp")
        || node.querySelector(".ytwTranscriptSegmentViewModelTimestamp, .ytTranscriptSegmentViewModelTimestamp");
      const stamp = clean(stampNode?.textContent);
      if (!/^\d+(?::\d{2}){1,2}$/.test(stamp)) continue;
      let text;
      for (const selector of [".segment-text", "span.ytAttributedStringHost", "span.yt-core-attributed-string", '[role="text"]']) {
        const candidate = clean(node.querySelector(selector)?.textContent);
        if (candidate && candidate !== stamp) { text = candidate; break; }
      }
      const start = stamp.split(":").reduce((seconds, part) => seconds * 60 + Number(part), 0);
      rows.push({ start, text, duration: 0, language: null });
    }
    rows.sort((a, b) => a.start - b.start);
    for (let index = 0; index < rows.length - 1; index++) rows[index].duration = Math.max(0, rows[index + 1].start - rows[index].start);
    return formatResult(rows, null, "youtube-transcript");
  };
  // A complete caption response takes priority over a partially rendered panel.
  const inline = await fetchTracks(captionTracks(playerResponse), "youtube-captions");
  if (inline) return inline;
  // MAIN-world same-origin requests avoid extension Origin problems without
  // extra permissions, a proxy, manually extracted cookies or provider keys.
  let webClient;
  try {
    const client = window.ytcfg?.get?.("INNERTUBE_CONTEXT")?.client;
    if (client?.clientName === "WEB" && typeof client.clientVersion === "string") {
      webClient = { clientName: "WEB", clientVersion: client.clientVersion };
      for (const key of ["hl", "gl", "visitorData"]) if (typeof client[key] === "string") webClient[key] = client[key];
    }
  } catch (_) {}
  // Chrome may ignore custom User-Agent headers; use client contexts without
  // broadening request permissions or rewriting the browser's headers.
  for (const client of [{ clientName: "IOS", clientVersion: "20.10.3" }, { clientName: "ANDROID", clientVersion: "20.10.38" }, webClient || { clientName: "WEB", clientVersion: "2.20240101.00.00" }]) {
    if (!isCurrentVideo()) return changed();
    try {
      const response = await fetch("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "include", signal: AbortSignal.timeout(4000),
        body: JSON.stringify({ context: { client }, videoId }),
      });
      if (!isCurrentVideo()) return changed();
      if (!response.ok) continue;
      const data = await response.json();
      if (!isCurrentVideo()) return changed();
      if (data?.videoDetails?.videoId && data.videoDetails.videoId !== videoId) continue;
      const result = await fetchTracks(captionTracks(data), "youtube-player-captions");
      if (result) return result;
    } catch (_) { /* Unsupported client, rate limit or unavailable video. */ }
  }
  if (!isCurrentVideo()) return changed();
  const findButton = () => document.querySelector("ytd-video-description-transcript-section-renderer button, ytd-video-description-transcript-section-renderer [role=button]")
    || [...(document.querySelectorAll?.("button") || [])].find((node) => /^(show transcript|显示文字记录|显示转录|转写文稿)$/i.test(clean(node.textContent)));
  const existingDom = readDom();
  let previousDom = existingDom?.success ? existingDom.transcriptTextTimestamped : null;
  let button = existingDom?.success ? null : findButton();
  if (!button && !existingDom?.success) {
    const expand = document.querySelector("ytd-watch-metadata #description-inline-expander #expand");
    if (expand) {
      expand.click();
      for (let attempt = 0; attempt < 6; attempt++) {
        await wait(300);
        if (!isCurrentVideo()) return changed();
        button = findButton();
        if (button) break;
      }
    }
  }
  if (button) button.click();
  // Wait for two identical non-loading snapshots instead of caching the first row.
  for (let attempt = 0; attempt < 20; attempt++) {
    await wait(400);
    if (!isCurrentVideo()) return changed();
    const result = readDom();
    if (result?.error === "VIDEO_CHANGED") return result;
    if (result?.success && result.transcriptTextTimestamped === previousDom) return result;
    previousDom = result?.success ? result.transcriptTextTimestamped : null;
  }
  return {
    success: false, error: "NATIVE_TRANSCRIPT_UNAVAILABLE",
    message: "YouTube 的字幕地址、播放器接口和文字记录面板均未返回可读字幕。请刷新视频后重试，或在设置中启用 Supadata 备用；无字幕视频需要另行语音转录。",
  };
}

if (typeof module !== "undefined" && module.exports) module.exports = { readNativeTranscript };
