/* Runs in the active YouTube page's MAIN world. No keys or extension storage
 * are passed into the page. Only current-video captions leave this function. */
async function readNativeTranscript(videoId) {
  const isCurrentVideo = () => new URL(location.href).searchParams.get("v") === videoId;
  if (!isCurrentVideo()) return { success: false, error: "VIDEO_CHANGED" };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const formatResult = (rows, language, source) => {
    if (!isCurrentVideo()) return { success: false, error: "VIDEO_CHANGED" };
    const transcript = rows.filter((row) => row.text && Number.isFinite(row.start) && row.start >= 0);
    if (!transcript.length) return null;
    transcript.sort((a, b) => a.start - b.start);
    return {
      success: true, transcript, language: language || null, source,
      transcriptText: transcript.map((row) => row.text).join(" "),
      transcriptTextTimestamped: transcript.map((row) => {
        const seconds = Math.floor(row.start);
        return `[${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}] ${row.text}`;
      }).join("\n"),
    };
  };
  let playerResponse;
  try { playerResponse = document.getElementById("movie_player")?.getPlayerResponse?.(); } catch (_) {}
  if (playerResponse?.videoDetails?.videoId !== videoId) {
    playerResponse = window.ytInitialPlayerResponse?.videoDetails?.videoId === videoId
      ? window.ytInitialPlayerResponse : null;
  }
  const tracks = [...(playerResponse?.captions?.playerCaptionsTracklistRenderer?.captionTracks || [])];
  tracks.sort((a, b) => {
    const rank = (track) => (track.languageCode?.startsWith("en") ? 0 : 2) + (track.kind === "asr" ? 1 : 0);
    return rank(a) - rank(b);
  });
  for (const track of tracks.slice(0, 2)) {
    try {
      const url = new URL(track.baseUrl);
      if (url.protocol !== "https:" || !["www.youtube.com", "youtube.com"].includes(url.hostname)
        || url.pathname !== "/api/timedtext") continue;
      url.searchParams.set("fmt", "json3");
      const response = await fetch(url.href, { credentials: "include", signal: AbortSignal.timeout(6000) });
      if (!response.ok) continue;
      const data = await response.json();
      const rows = (data.events || []).filter((event) => Array.isArray(event.segs)).map((event) => ({
        text: event.segs.map((part) => part.utf8 || "").join("").replace(/\s+/g, " ").trim(),
        start: Number(event.tStartMs) / 1000,
        duration: Number(event.dDurationMs || 0) / 1000,
        language: track.languageCode || null,
      }));
      const result = formatResult(rows, track.languageCode, "youtube-captions");
      if (result) return result;
    } catch (_) { /* Timedtext can be empty or unavailable; try the visible transcript. */ }
  }
  // Ask YouTube to display its own transcript using the existing page button.
  // This route also works when timedtext requires player-specific tokens.
  const readDom = () => {
    const panel = document.querySelector('ytd-engagement-panel-section-list-renderer[target-id="engagement-panel-searchable-transcript"]');
    const nodes = panel?.querySelectorAll("ytd-transcript-segment-renderer") || [];
    const rows = [];
    for (const node of nodes) {
      const stamp = node.querySelector(".segment-timestamp")?.textContent?.trim();
      const text = node.querySelector(".segment-text")?.textContent?.replace(/\s+/g, " ").trim();
      if (!stamp || !/^\d+(?::\d{2}){1,2}$/.test(stamp)) continue;
      const start = stamp.split(":").reduce((seconds, part) => seconds * 60 + Number(part), 0);
      rows.push({ start, text, duration: 0, language: null });
    }
    for (let index = 0; index < rows.length - 1; index++) {
      rows[index].duration = Math.max(0, rows[index + 1].start - rows[index].start);
    }
    // Do not guess the source language if the built-in panel is set to another track.
    return formatResult(rows, null, "youtube-transcript");
  };
  const existing = readDom();
  if (existing) return existing;
  let button = document.querySelector("ytd-video-description-transcript-section-renderer button, ytd-video-description-transcript-section-renderer [role=button]");
  if (!button) {
    const expand = document.querySelector("ytd-watch-metadata #description-inline-expander #expand");
    if (expand) {
      expand.click();
      for (let attempt = 0; attempt < 6; attempt++) {
        await wait(300);
        if (!isCurrentVideo()) return { success: false, error: "VIDEO_CHANGED" };
        button = document.querySelector("ytd-video-description-transcript-section-renderer button, ytd-video-description-transcript-section-renderer [role=button]");
        if (button) break;
      }
    }
  }
  if (button) {
    button.click();
    for (let attempt = 0; attempt < 20; attempt++) {
      await wait(400);
      if (!isCurrentVideo()) return { success: false, error: "VIDEO_CHANGED" };
      const result = readDom();
      if (result) return result;
    }
  }
  return {
    success: false, error: "NATIVE_TRANSCRIPT_UNAVAILABLE",
    message: "未读到此视频的原生字幕。可先在 YouTube 打开“显示文字记录”后重试，或在设置中启用 Supadata 备用。无字幕视频需要另行语音转录。",
  };
}

if (typeof module !== "undefined" && module.exports) module.exports = { readNativeTranscript };
