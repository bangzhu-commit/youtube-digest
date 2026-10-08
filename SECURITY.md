# Security: local modified version

Install from the reviewed local source directory. Preserve the original MIT license, the adapted translation rules' third-party notice, and verify updates before reloading.

Enter keys only in the extension Settings. Never include them in source, chat, logs, screenshots or commits. Keys for OpenRouter, 302.ai and DeepSeek are separate and switching platforms does not reuse a different platform's key. Native caption extraction receives only the video ID, never credentials. API requests are made by the extension's trusted service worker to fixed provider endpoints.

Use provider spending limits. Native-only mode cannot charge Supadata; automatic fallback is explicitly selected. Translation and AI overview use the selected provider's API balance. No account creation, purchase or cloud publication is performed by this copy.

The optional Obsidian host is registered for exactly one extension ID and one chosen vault. Its protocol accepts only episode-scoped loads and annotation saves, with bounded messages and no arbitrary paths or command execution. Symlinks outside the vault are rejected. Existing source text is never overwritten, repeated saves are deduplicated, and manually edited annotations cannot be silently replaced. Keep the private host configuration outside the repository. Reinstall the host if the source folder or extension ID changes.

The release script checks syntax, tests, manifest references, package allowlist and potential secrets. This does not establish every provider's live behavior. Run npm test, npm run check, npm run package and test a real public captioned video and a Xiaoyuzhou episode after installation.
