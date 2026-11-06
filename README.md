# YouTube Digest

Turn every YouTube video into a resource for deep learning.

This fork extends [Zara Zhang's original project](https://github.com/zarazhangrui/youtube-digest), based on v1.2.0. The original MIT license is retained. API accounts and credits are not included. Source changes are maintained in [this fork](https://github.com/bangzhu-commit/youtube-digest); credentials stay in the local Chrome profile.

## Local changes

- Native YouTube captions/transcript reading without a Supadata key. Adapted from Defuddle 0.19.4, it supports old/new transcript panels, JSON3/XML captions and fresh caption URLs from YouTube's player API. It may open the built-in transcript panel as a fallback.
- Supadata is optional. Select native only, native with Supadata fallback, or Supadata only. Fallback requests consume credits only when explicitly enabled.
- Local Codex (default for new installations, ChatGPT subscription login), OpenRouter, 302.ai and DeepSeek; exact model ID is editable. Existing provider selections are retained. Provider keys are separated so switching cannot send a previous provider's key to another service.
- Original transcripts can be read without an AI key. Translation, chapters, explanation and note cleanup use the selected provider: existing Codex subscription login or an API key and balance.
- The original side panel, timestamp jumps, bilingual alignment, search and local cache remain.
- Xiaoyuzhou episode pages have a Chinese reading panel with existing transcripts, search, timestamp playback, exact-quote annotations, draft recovery and Obsidian archival.

## Xiaoyuzhou and Obsidian

Click the extension icon on a Xiaoyuzhou episode page. The reader loads an existing Markdown source from `知识库/原始素材库`, matched by episode URL and an explicit transcript heading. You can also import Markdown, TXT, SRT, VTT or segment JSON. Shownotes and media references are not transcripts. Reading and annotations need no AI key; model processing runs only on request.

Click **整集转写** to download the selected public episode and transcribe its complete audio locally with cached faster-whisper medium. Progress is saved; the task continues after the panel closes and can be cancelled or resumed. Existing sources and manually edited files are preserved. Audio, timestamped checkpoints and final Markdown stay in the selected vault. No speech API is charged. FFmpeg, faster-whisper and the medium model must already be installed/cached; missing prerequisites are reported, not installed automatically. Downloads are limited to supported public Xiaoyuzhou media hosts.

For text processing, select **本机 Codex（订阅登录）** in Settings, check the connection and save. The bridge invokes `codex exec` with an existing ChatGPT login, without API-key fallback. Codex subscription limits apply. This is a local client for cloud inference, not an offline model: transcript text and task prompts are sent to OpenAI. The browser never reads account credentials. Login and installation remain manual. Text polishing cannot certify what the audio actually said.

Preview opening paragraphs whose timestamps start within the first five minutes (long paragraphs may extend past that window), then continue polishing the full transcript. Every batch has separate drafting and source comparison calls to the configured text model. Empty fillers are removed while content, numbers, attribution and uncertainty are preserved; uncertain words remain flagged. Completed batches are cached for resumption, pending rows remain labeled as source text, and the original is available for comparison. Text polishing is not audio verification.

For difficult passages, **听音复核** runs cached faster-whisper medium locally, sends at most ninety seconds of MP3 to a selected Gemini audio model via OpenRouter, then reviews it with the configured text model. Full-episode transcription saves the audio at `知识库/原始素材库/_attachments/xiaoyuzhou/EPISODE_ID/source.mp3`; audio review itself does not download it. FFmpeg and faster-whisper must already be installed and the model cached. This optional cloud review requires an OpenRouter key and uses its provider credits separately from Codex. Local and cloud candidates, actual audio coverage and correction issues are preserved.

Select an exact quote or click its paragraph annotation button and save your thought as a browser draft. Drafts are keyed by episode and survive panel closure. **Save to Obsidian** writes `知识库/阅读工作台/小宇宙-EPISODE_ID-阅读批注.md`, embedding the original source and appending the quote, timestamp and your unchanged thought. Existing sources are never overwritten, repeated saves are idempotent, and manual edits to archived annotations are respected. Conflicts require reloading the vault. Imported source text is also preserved in an original-text attachment. Without the local bridge, import, drafts and Markdown export still work.

Polished selections retain both the reading quote and source passage. The derivative is saved separately as `知识库/阅读工作台/小宇宙-EPISODE_ID-精校阅读版.md`; alignment and audio candidates are stored in `_attachments/xiaoyuzhou/EPISODE_ID/reading-v1.json` under that workspace. Changed sources invalidate cached polishing. Manually edited derivatives are not silently replaced.

When paragraphs share coarse timestamps, clip positions are estimated proportionally from text lengths with surrounding margin and explicitly flagged. Use actual recognition candidates to confirm which words the clip covers.

Direct vault reads and saves require Python 3 and a local Native Messaging host. On macOS, copy the extension ID from its details in `chrome://extensions` and run once from this source folder:

```sh
python3 native/install_host.py --extension-id YOUR_EXTENSION_ID --vault "/path/to/your/vault"
```

Only that extension ID, vault and installed FFmpeg/Codex paths are registered. The host exposes scoped reads, saves, fixed-episode transcription jobs, audio clips and bounded Codex text requests. There is no arbitrary file, shell-command or URL API. Audio processing executes fixed FFmpeg arguments and cached-only Whisper. Private configuration stays in Application Support outside the repository and ZIP. Keep this source folder. Other systems require explicit `--config-dir` and `--host-dir` and have not been browser-tested.

Rerun the host installer and reload the extension after upgrading, then refresh Xiaoyuzhou. Version 1.5.0 retains the existing browser permissions. Timestamp playback uses the current page's audio player; start playback on the episode page first if needed.

## Install and configure

Keep this source folder in a permanent location. Open Chrome's `chrome://extensions`, enable Developer mode, select **Load unpacked**, and choose this exact folder containing `manifest.json`. Moving or deleting the folder breaks the unpacked extension. This local copy does not auto-update; after editing, use Reload then refresh YouTube.

Open the extension's Settings. Select local Codex to use an existing ChatGPT subscription login; its default model is `gpt-6.1-sol`, editable to an available model on your account. The local adapter requires Codex CLI 0.166.0 or later. Existing provider selections are retained when upgrading. For OpenRouter, enter its key in Settings, not in chat or source files; its preset is `deepseek/deepseek-v4-flash`. Saving settings does not call a model. This OpenRouter preset uses `reasoning.enabled=false`; other models are left untouched. 302.ai uses its own model IDs (default `gpt-4o-mini`). DeepSeek-specific parameters and empty-JSON retry remain limited to DeepSeek.

API endpoints: OpenRouter `https://openrouter.ai/api/v1/chat/completions`; 302.ai `https://api.302.ai/v1/chat/completions`; DeepSeek `https://api.deepseek.com/chat/completions`. Local Codex uses Native Messaging and its own subscription login. Arbitrary custom API origins are not enabled. If your system proxy uses Fake-IP DNS, explicitly rerun the host installer with `--allow-fake-ip-dns`; `--no-allow-fake-ip-dns` disables it. This permits only the benchmark range for the fixed HTTPS download hosts, not arbitrary private addresses.

Select **YouTube page** for subtitles. This is the default and never sends requests to Supadata. If you choose a Supadata mode, enter a separate Supadata key. Supadata requests force `mode=native` and never purchase generated transcripts. Inspect each provider's current pricing in its dashboard.

## Translation quality

New videos default to Chinese; an explicit Original or Bilingual choice is remembered per video. Opening the Chinese or bilingual panel translates the current area with the configured provider and prefetches the next six segments. Batches remain serial with at most three segments, prioritizing the current reading position; prefetch does not recursively translate the full video. Cached, queued and in-flight segments are deduplicated, and failed segments require an explicit Retry. Switching videos or language stops further requests from the old queue. Existing Chinese captions are shown without model calls. Automatic translation and prefetch use provider credits; select Original to read without translation.

The default Reviewed mode adapts baoyu-translate: neighboring subtitle context and consistent terminology guide the draft, then a separate model call compares it with the original and revises the Chinese. This compact subtitle workflow uses two model calls per batch; it is not the full article refinement pipeline. Quick mode uses one call. Choose the mode in Settings and reopen the side panel. New-version and mode-specific cache keys prevent old translations from masking the change. API costs and waiting time increase in Reviewed mode; automatic-caption errors can still affect the result. The original transcript and timestamps stay aligned. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution and license.

## Limits and validation

Chrome 116+, standard public `youtube.com/watch` pages with captions. Native reads run in the current YouTube page with same-origin requests; no extra proxy permissions, Defuddle service or cloud key is required. YouTube page structures and caption endpoints can change; direct extraction is best-effort. If it fails, refresh the video and retry, or select Supadata fallback. YouTube videos without captions still require separate speech transcription; this release adds local full-audio transcription only for supported public Xiaoyuzhou episodes. Shorts, live and restricted videos are not verified.

Run `npm test`, `npm run check`, `npm run package`. A passing test suite validates local logic, not your real API credentials or every YouTube video. Verify caption reading, time jumps and bilingual translation on a real video after installing.

See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md).

## Maintenance

After user-requested changes pass validation, the coding agent commits and pushes them to this fork. The project instructions in [AGENTS.md](AGENTS.md) record this standing authorization. Source synchronization does not automatically reload an installed extension or create a background sync task.
