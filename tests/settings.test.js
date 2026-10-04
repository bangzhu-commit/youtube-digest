const test = require("node:test");
const assert = require("node:assert/strict");
const settings = require("../settings.js");

test("new profiles default to OpenRouter and native captions without keys", () => {
  const value = settings.normalize();
  assert.equal(value.provider, "openrouter");
  assert.equal(value.transcriptProvider, "native");
  assert.equal(value.aiApiKey, "");
  assert.equal(value.supadataApiKey, "");
});

test("provider endpoints cannot redirect a key to custom origins", () => {
  for (const provider of ["openrouter", "302ai", "deepseek"]) {
    const value = settings.normalize({ provider, aiApiKey: " test-key ", aiBaseUrl: "https://attacker.example", aiModel: "chosen/model" });
    assert.equal(value.aiBaseUrl, settings.PROVIDERS[provider].baseUrl);
    assert.equal(value.aiModel, "chosen/model");
    assert.equal(value.aiApiKey, "test-key");
    assert.equal(settings.chatCompletionsUrl(value), `${value.aiBaseUrl}/chat/completions`);
  }
});

test("existing DeepSeek settings preserve keys; unknown providers clear them", () => {
  const old = settings.normalize({ provider: "deepseek", aiApiKey: "legacy-key" });
  assert.equal(old.provider, "deepseek");
  assert.equal(old.aiApiKey, "legacy-key");
  const legacy = settings.migrateLegacyCustom({ provider: "custom", aiApiKey: "old-secret", supadataApiKey: "subtitle-key" });
  assert.equal(legacy.migrated, true);
  assert.equal(legacy.settings.aiApiKey, "");
  assert.equal(legacy.settings.supadataApiKey, "subtitle-key");
  assert.equal(settings.migrateLegacyCustom(legacy.settings).migrated, false);
  assert.equal(settings.normalize({ provider: "unexpected", aiApiKey: "old-secret" }).aiApiKey, "");
});

test("DeepSeek-only thinking parameter stays out of OpenRouter and 302.ai", () => {
  const request = { messages: [{ role: "user", content: "Translate" }], maxTokens: 128, temperature: 0.2, responseFormat: { type: "json_object" } };
  for (const provider of ["openrouter", "302ai", "deepseek"]) {
    const body = settings.completionBody(settings.normalize({ provider }), request);
    assert.equal(Object.hasOwn(body, "thinking"), provider === "deepseek");
    assert.deepEqual(body.response_format, { type: "json_object" });
  }
});

test("Supadata receives only a canonical YouTube URL", () => {
  assert.equal(settings.canonicalYouTubeUrl("ydTeb_I0b94"), "https://www.youtube.com/watch?v=ydTeb_I0b94");
  assert.throws(() => settings.canonicalYouTubeUrl('invalid<>'), /Invalid YouTube/);
});

test("OpenRouter V4 Flash disables default reasoning without native DeepSeek fields", () => {
  const request = { messages: [{ role: "user", content: "Translate" }], maxTokens: 1536 };
  const body = settings.completionBody(settings.normalize({ provider: "openrouter", aiModel: "deepseek/deepseek-v4-flash" }), request);
  assert.deepEqual(body.reasoning, { enabled: false });
  assert.equal(Object.hasOwn(body, "thinking"), false);
  const other = settings.completionBody(settings.normalize({ provider: "openrouter", aiModel: "openai/gpt-4.1-mini" }), request);
  assert.equal(Object.hasOwn(other, "reasoning"), false);
});
