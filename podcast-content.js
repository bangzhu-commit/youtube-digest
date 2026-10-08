/* Player controls stay in the requested Xiaoyuzhou episode. No credentials or files. */
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const media = YTD_MEDIA.identify(location.href);
  if (media?.platform !== "xiaoyuzhou" || media.id !== message.episodeId) return false;
  if (message.action === "podcastInfo") {
    let transcript = null;
    try {
      const data = document.getElementById("__NEXT_DATA__")?.textContent;
      if (data && data.length < 2000000) transcript = YTD_PODCAST.publicEpisode(JSON.parse(data), media.id);
    } catch {}
    sendResponse({
      title: document.querySelector("h1")?.textContent?.trim() || document.title.replace(/\s*[-|].*$/, ""),
      channelName: document.querySelector('a[href^="/podcast/"]')?.textContent?.trim() || "",
      transcript,
    });
  } else if (message.action === "podcastSeek") {
    const player = document.querySelector("audio") || document.querySelector("video");
    const value = Number(message.seconds);
    if (!player || !Number.isFinite(value) || value < 0) {
      sendResponse({ success: false, error: "网页播放器尚未就绪，请先在节目页点击播放。" });
    } else {
      player.currentTime = value;
      player.play().then(() => sendResponse({ success: true }))
        .catch(() => sendResponse({ success: false, error: "请先在节目页允许播放音频。" }));
      return true;
    }
  } else return false;
  return false;
});
