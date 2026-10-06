# YouTube Digest

Turn every YouTube video into a resource for deep learning.

This fork extends [Zara Zhang's original project](https://github.com/zarazhangrui/youtube-digest), based on v1.2.0. The original MIT license is retained. API accounts and credits are not included. Source changes are maintained in [this fork](https://github.com/bangzhu-commit/youtube-digest); credentials stay in the local Chrome profile.

## Local changes

- Native YouTube captions/transcript reading without a Supadata key. Adapted from Defuddle 0.19.4, it supports old/new transcript panels, JSON3/XML captions and fresh caption URLs from YouTube's player API. It may open the built-in transcript panel as a fallback.
- Supadata is optional. Select native only, native with Supadata fallback, or Supadata only. Fallback requests consume credits only when explicitly enabled.
- OpenRouter (default), 302.ai and DeepSeek; exact model ID is editable. Provider keys are separated so switching cannot send a previous provider's key to another service.
- Original transcripts can be read without an AI key. Translation, chapters, explanation and note cleanup require the selected provider's key and balance.
- The original side panel, timestamp jumps, bilingual alignment, search and local cache remain.

## Install and configure

Keep this source folder in a permanent location. Open Chrome's `chrome://extensions`, enable Developer mode, select **Load unpacked**, and choose this exact folder containing `manifest.json`. Moving or deleting the folder breaks the unpacked extension. This local copy does not auto-update; after editing, use Reload then refresh YouTube.

Open the extension's Settings. Choose OpenRouter and enter your key there, not in chat or source files. The default model ID is `deepseek/deepseek-v4-flash`; change it to an available text chat model supporting JSON output if needed. Settings save does not call the model. The default DeepSeek V4 Flash on OpenRouter uses its standard `reasoning.enabled=false` to reduce latency; other models are left untouched. 302.ai uses its own model IDs (default `gpt-4o-mini`). DeepSeek retains its own non-thinking parameters and empty-JSON retry; these are not sent to the other providers.

Endpoints: OpenRouter `https://openrouter.ai/api/v1/chat/completions`; 302.ai `https://api.302.ai/v1/chat/completions`; DeepSeek `https://api.deepseek.com/chat/completions`. Arbitrary custom origins are not enabled.

Select **YouTube page** for subtitles. This is the default and never sends requests to Supadata. If you choose a Supadata mode, enter a separate Supadata key. Supadata requests force `mode=native` and never purchase generated transcripts. Inspect each provider's current pricing in its dashboard.

## Translation quality

The default Reviewed mode adapts baoyu-translate: neighboring subtitle context and consistent terminology guide the draft, then a separate model call compares it with the original and revises the Chinese. This compact subtitle workflow uses two model calls per batch; it is not the full article refinement pipeline. Quick mode uses one call. Choose the mode in Settings and reopen the side panel. New-version and mode-specific cache keys prevent old translations from masking the change. API costs and waiting time increase in Reviewed mode; automatic-caption errors can still affect the result. The original transcript and timestamps stay aligned. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution and license.

## Limits and validation

Chrome 116+, standard public `youtube.com/watch` pages with captions. Native reads run in the current YouTube page with same-origin requests; no extra proxy permissions, Defuddle service or cloud key is required. YouTube page structures and caption endpoints can change; direct extraction is best-effort. If it fails, refresh the video and retry, or select Supadata fallback. Videos without captions require separate speech transcription, which this version does not perform. Shorts, live and restricted videos are not verified.

Run `npm test`, `npm run check`, `npm run package`. A passing test suite validates local logic, not your real API credentials or every YouTube video. Verify caption reading, time jumps and bilingual translation on a real video after installing.

See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md).

## Maintenance

After user-requested changes pass validation, the coding agent commits and pushes them to this fork. The project instructions in [AGENTS.md](AGENTS.md) record this standing authorization. Source synchronization does not automatically reload an installed extension or create a background sync task.
