/**
 * Shared, non-secret configuration helpers.
 *
 * API keys are stored in chrome.storage.local by options.js. This file contains
 * defaults and validation only, so it is safe to publish.
 */
var YTD_SETTINGS = (() => {
  const STORAGE_KEY = "ytd_settings";
  const PROVIDERS = Object.freeze({
    codex: { name: "本机 Codex（订阅登录）", baseUrl: "", model: "gpt-6.1-sol", keysUrl: "" },
    openrouter: { name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", model: "deepseek/deepseek-v4-flash", keysUrl: "https://openrouter.ai/settings/keys" },
    "302ai": { name: "302.ai", baseUrl: "https://api.302.ai/v1", model: "gpt-4o-mini", keysUrl: "https://302.ai/" },
    deepseek: { name: "DeepSeek", baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash", keysUrl: "https://platform.deepseek.com/api_keys" },
  });
  const DEFAULTS = Object.freeze({
    provider: "codex",
    aiApiKey: "",
    aiBaseUrl: PROVIDERS.codex.baseUrl,
    aiModel: PROVIDERS.codex.model,
    supadataApiKey: "",
    transcriptProvider: "native",
    translationQuality: "reviewed",
  });

  function isLegacyCustom(input) {
    return !!input && input.provider === "custom";
  }

  function normalize(input = {}) {
    input = input || {};
    // Preserve older key-bearing profiles which predate the provider field.
    const provider = Object.hasOwn(PROVIDERS, input.provider) ? input.provider
      : !input.provider && typeof input.aiApiKey === "string" && input.aiApiKey.trim() ? "openrouter" : DEFAULTS.provider;
    const preset = PROVIDERS[provider];
    const knownProvider = !input.provider || Object.hasOwn(PROVIDERS, input.provider);
    return {
      provider,
      aiApiKey: provider === "codex" || !knownProvider
        ? ""
        : typeof input.aiApiKey === "string"
          ? input.aiApiKey.trim()
          : "",
      aiBaseUrl: preset.baseUrl,
      aiModel: knownProvider && typeof input.aiModel === "string" && input.aiModel.trim()
        ? input.aiModel.trim() : preset.model,
      supadataApiKey:
        typeof input.supadataApiKey === "string"
          ? input.supadataApiKey.trim()
          : "",
      transcriptProvider: ["native", "native-fallback", "supadata"].includes(input.transcriptProvider)
        ? input.transcriptProvider : "native",
      translationQuality: input.translationQuality === "quick" ? "quick" : "reviewed",
    };
  }

  function migrateLegacyCustom(input = {}) {
    return {
      settings: normalize(input),
      migrated: isLegacyCustom(input),
    };
  }

  function chatCompletionsUrl(input = DEFAULTS) {
    if (normalize(input).provider === "codex") throw new Error("Codex uses the local subscription bridge, not an API endpoint.");
    return `${normalize(input).aiBaseUrl}/chat/completions`;
  }

  function completionBody(settings, { messages, maxTokens, temperature, responseFormat }) {
    const body = { model: settings.aiModel, max_tokens: maxTokens, messages };
    if (typeof temperature === "number") body.temperature = temperature;
    if (responseFormat) body.response_format = responseFormat;
    if (settings.provider === "deepseek") body.thinking = { type: "disabled" };
    // OpenRouter uses its own unified reasoning field, not DeepSeek's native one.
    // This exact model supports non-thinking mode; other model IDs are untouched.
    if (settings.provider === "openrouter" && settings.aiModel === "deepseek/deepseek-v4-flash") {
      body.reasoning = { enabled: false };
    }
    return body;
  }

  function canonicalYouTubeUrl(videoId) {
    const normalized = String(videoId || "").trim();
    if (!/^[A-Za-z0-9_-]{6,20}$/.test(normalized)) {
      throw new Error("Invalid YouTube video ID.");
    }
    return `https://www.youtube.com/watch?v=${normalized}`;
  }

  return {
    STORAGE_KEY,
    DEFAULTS,
    PROVIDERS,
    isLegacyCustom,
    normalize,
    migrateLegacyCustom,
    chatCompletionsUrl,
    completionBody,
    canonicalYouTubeUrl,
  };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = YTD_SETTINGS;
}
