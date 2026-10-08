(function (root) {
  "use strict";
  function identify(url) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "https:") return null;
      if (parsed.hostname === "www.youtube.com") {
        return { platform: "youtube", id: parsed.searchParams.get("v"), panel: "sidepanel.html" };
      }
      if (["www.xiaoyuzhoufm.com", "xiaoyuzhoufm.com"].includes(parsed.hostname)) {
        const match = /^\/episode\/([a-f0-9]{24})\/?$/i.exec(parsed.pathname);
        if (match) return {
          platform: "xiaoyuzhou", id: match[1].toLowerCase(), panel: "podcast-panel.html",
          url: `https://www.xiaoyuzhoufm.com/episode/${match[1].toLowerCase()}`,
        };
      }
    } catch {}
    return null;
  }
  const api = Object.freeze({ identify });
  root.YTD_MEDIA = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
