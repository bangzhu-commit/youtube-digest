# Privacy: local modified version

This copy has no developer server, analytics or telemetry. Native subtitle reading runs only on the YouTube video requested by the side panel, verifies its video ID, and uses YouTube's same-origin caption URLs, player API or built-in transcript panel. Player requests contain that video ID and a client context; WEB requests use the current page's client version, locale and visitor data when available. The browser handles the current YouTube session normally; the extension does not extract cookies or send this context to another service. It does not access other browsing content and receives no API credentials.

Supadata receives the canonical video URL and its own key only if the user selects a Supadata mode. Native-only mode never calls Supadata. Speech transcription is not performed.

Translation, overview, explanations and note cleanup send the required transcript/context and the selected provider's own key directly to OpenRouter, 302.ai or DeepSeek. Reviewed translation sends each subtitle batch, up to two neighboring segments on either side (at most 2000 characters per side), and the draft to the selected provider for a second accuracy/revision call. Quick mode uses one call. The local Skill and personal configuration files are not transmitted. Only these fixed origins are permitted. Each service applies its own data and pricing policies. No key is passed into YouTube's page context.

Keys, separate provider profiles, notes and caches live in chrome.storage.local within the current browser profile. They are not written to source files. Storage access is restricted to trusted extension contexts. Existing cache limits from the original project apply. Removing/resetting the extension clears local data but does not clear provider-side records. Chrome local extension storage is not a password vault.

Permissions: sidePanel for the interface; storage for settings and cache; tabs for current-video routing; scripting for caption reading and existing player controls. Host access is limited to www.youtube.com, api.supadata.ai, api.deepseek.com, openrouter.ai and api.302.ai.
