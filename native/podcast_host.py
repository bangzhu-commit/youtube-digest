#!/usr/bin/env python3
"""Scoped Obsidian bridge with bounded, offline audio snippets."""
import base64
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import wave

HOST_NAME = 'com.youtube_digest.obsidian'
MAX_BYTES = 950000
EPISODE_RE = re.compile(r'^[a-f0-9]{24}$')
AUDIO_MODELS = {'google/gemini-2.5-flash', 'google/gemini-3.1-flash-lite-preview'}
LOCAL_AUDIO_MODEL = 'faster-whisper/medium/int8'
AUDIO_EVIDENCE_FIELDS = {'start', 'end', 'localText', 'heardText', 'localModel', 'audioModel', 'provider', 'checkedAt'}


class BridgeError(ValueError):
    pass


def scoped(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise BridgeError('路径超出已配置的 Obsidian 素材库。')
    return resolved


def text(value, limit=600000):
    if not isinstance(value, str) or len(value) > limit:
        raise BridgeError('内容格式或长度无效。')
    return value


def safe_name(value):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|#\[\]]', ' ', text(value, 500)).strip(' .')[:120]
    return name or '小宇宙节目'


def read_text(path):
    if path.stat().st_size > MAX_BYTES:
        raise BridgeError('文稿过大，请分段导入。')
    return path.read_text(encoding='utf-8')


def checked_window(start, end):
    if (type(start) not in (int, float) or type(end) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(end)
            or not 0 <= start < end <= 604800 or end - start > 90):
        raise BridgeError('听音窗口无效，单次最多 90 秒。')
    return start, end


def checked_date(value):
    value = text(value, 80)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise BridgeError('精校日期无效。') from error
    if parsed.tzinfo is None:
        raise BridgeError('精校日期必须包含时区。')
    return value


def checked_audio_evidence(value, start):
    if not isinstance(value, dict) or set(value) != AUDIO_EVIDENCE_FIELDS:
        raise BridgeError('听音证据字段无效。')
    clip_start, clip_end = checked_window(value['start'], value['end'])
    if start is None or not clip_start <= start < clip_end:
        raise BridgeError('听音证据与原稿时间无法对应。')
    local, heard = text(value['localText'], 30000), text(value['heardText'], 30000)
    if not local.strip() or not heard.strip():
        raise BridgeError('听音证据缺少实际识别文字。')
    local_model, audio_model = text(value['localModel'], 200), text(value['audioModel'], 200)
    if (local_model != LOCAL_AUDIO_MODEL or audio_model not in AUDIO_MODELS
            or text(value['provider'], 80) != 'openrouter'):
        raise BridgeError('听音证据的模型或提供方无效。')
    return dict(start=clip_start, end=clip_end, localText=local, heardText=heard,
                localModel=LOCAL_AUDIO_MODEL, audioModel=audio_model,
                provider='openrouter', checkedAt=checked_date(value['checkedAt']))


def source_entries(raw):
    """Match podcast-core.js's p-N anchors for the original JSON, SRT, text or Markdown."""
    raw = raw.removeprefix('\ufeff')
    entries = []

    def add(value, start=None, speaker=''):
        if isinstance(value, str) and value.strip():
            entries.append(dict(id=f'p-{len(entries)}', text=value.strip(), start=start, speaker=speaker))

    def seconds(value):
        try:
            parts = [float(part) for part in value.strip().replace(',', '.').split(':')]
        except ValueError as error:
            raise BridgeError('原稿时间戳无效。') from error
        if (len(parts) not in (2, 3) or any(not math.isfinite(part) or part < 0 for part in parts)
                or parts[-1] >= 60 or (len(parts) == 3 and parts[1] >= 60)):
            raise BridgeError('原稿时间戳无效。')
        result = 0
        for part in parts:
            result = result * 60 + part
        return result

    if re.match(r'^[\[{]', raw.strip()) and not re.match(r'^\[\d+:', raw.strip()):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            items = parsed if isinstance(parsed, list) else parsed.get('segments') if isinstance(parsed, dict) else None
            if not isinstance(items, list):
                raise BridgeError('JSON 原稿缺少 segments。')
            for item in items:
                if not isinstance(item, dict):
                    raise BridgeError('JSON 原稿段落无效。')
                try:
                    start = float(item['start'] or 0) if 'start' in item else None
                except (ValueError, TypeError) as error:
                    raise BridgeError('原稿时间戳无效。') from error
                if start is not None and (not math.isfinite(start) or start < 0):
                    raise BridgeError('原稿时间戳无效。')
                add(item.get('text'), start, item.get('speaker') if isinstance(item.get('speaker'), str) else '')
            return entries
    raw = raw.replace('\r\n', '\n')
    if re.search(r'^\d{1,3}:\d{2}(?::\d{2})?[.,]\d+\s*-->\s*\d', raw, re.M):
        for block in re.split(r'\n\s*\n', raw):
            lines = block.split('\n')
            index = next((i for i, line in enumerate(lines) if '-->' in line), None)
            if index is not None:
                add('\n'.join(lines[index + 1:]), seconds(lines[index].split('-->')[0]))
        return entries
    body = re.split(r'^##\s+(?:转写全文|逐字稿|全文|Transcript)\s*$', raw, flags=re.M | re.I)[-1]
    start, speaker, buffer = None, '', []

    def flush():
        add('\n'.join(buffer), start, speaker)
        buffer.clear()

    for line in body.split('\n'):
        if re.match(r'^\[\^[^\]]+\]:', line):
            flush()
            start, speaker = None, '转写说明'
            buffer.append(line)
            continue
        anchor = re.fullmatch(r'\s*(?:#{1,6}\s*)?(?:\*\*)?\[(\d{1,3}:\d{2}(?::\d{2})?)\]\s*(.*?)\s*(?:\*\*)?\s*', line)
        if anchor:
            flush()
            start = seconds(anchor[1])
            tail = re.sub(r'\*\*$', '', anchor[2]).strip()
            if not re.match(r'^\s*(?:#|\*\*)', line) and tail:
                speaker = ''
                buffer.append(tail)
            else:
                speaker = tail
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    return entries


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.digest-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checked_note(item):
    if not isinstance(item, dict):
        raise BridgeError('批注格式无效。')
    note_id = text(item.get('id'), 80)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', note_id):
        raise BridgeError('批注编号无效。')
    quote, thought = text(item.get('quote'), 20000), text(item.get('thought'), 20000)
    if not quote.strip() or not thought.strip():
        raise BridgeError('批注必须包含原句和感想。')
    start = item.get('start')
    if start is not None and (type(start) not in (int, float) or not 0 <= start <= 604800):
        raise BridgeError('批注时间戳无效。')
    created = text(item.get('createdAt', ''), 80)
    if not re.fullmatch(r'[0-9TZ:+. -]*', created):
        raise BridgeError('批注日期无效。')
    result = dict(id=note_id, quote=quote, thought=thought, start=start, createdAt=created)
    if item.get('entryId'):
        entry_id = text(item['entryId'], 80)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', entry_id):
            raise BridgeError('原稿段落编号无效。')
        result['entryId'] = entry_id
    if item.get('readingQuote'):
        result['readingQuote'] = text(item['readingQuote'], 20000)
    return result


def stamp(value):
    return '无时间戳' if value is None else f'{int(value // 60)}:{int(value % 60):02}'


def note_block(note):
    # Machine metadata only contains identity/time; human text remains the readable source of truth.
    meta = {key: note[key] for key in ('id', 'start', 'createdAt')}
    if note.get('entryId'):
        meta['entryId'] = note['entryId']
    encoded = base64.urlsafe_b64encode(json.dumps(meta).encode()).decode()
    quote = '\n'.join('> ' + line for line in note['quote'].split('\n'))
    reading = ''
    if note.get('readingQuote'):
        reading_quote = '\n'.join('> ' + line for line in note['readingQuote'].split('\n'))
        reading = f'**阅读版摘句**\n\n{reading_quote}\n\n'
    return f"\n<!-- digest-note:{encoded} -->\n### {stamp(note['start'])}\n\n{reading}**原文**\n\n{quote}\n\n**我的感想**\n\n{note['thought']}\n"


def load_notes(content):
    notes = []
    blocks = re.split(r'\n<!-- digest-note:([A-Za-z0-9_=-]+) -->\n', content)
    for index in range(1, len(blocks), 2):
        try:
            meta = json.loads(base64.urlsafe_b64decode(blocks[index]))
            body = blocks[index + 1]
            quote_part, thought = body.split('**我的感想**\n\n', 1)
            quote = quote_part.split('**原文**\n\n', 1)[1].removesuffix('\n\n')
            quote = '\n'.join(line[2:] if line.startswith('> ') else line for line in quote.split('\n'))
            if '**阅读版摘句**\n\n' in quote_part:
                excerpt = quote_part.split('**阅读版摘句**\n\n', 1)[1].split('**原文**\n\n', 1)[0].removesuffix('\n\n')
                meta['readingQuote'] = '\n'.join(line[2:] if line.startswith('> ') else line for line in excerpt.split('\n'))
            notes.append(checked_note({**meta, 'quote': quote, 'thought': thought.removesuffix('\n')}))
        except (ValueError, KeyError, IndexError):
            # Never silently rewrite a manually edited/unrecognized annotation.
            raise BridgeError('已有批注格式被修改，已保留原文件；请在 Obsidian 中检查后再保存。')
    return notes


class Vault:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['vault']).expanduser().resolve()
        if not self.root.is_dir():
            raise BridgeError('配置的 Obsidian 目录不存在。')
        self.sources = scoped(self.root, self.root / '知识库/原始素材库')
        self.readings = scoped(self.root, self.root / '知识库/阅读工作台')

    def source(self, episode_id):
        url = 'https://www.xiaoyuzhoufm.com/episode/' + episode_id
        matches = []
        # Only this material directory, never arbitrary vault files or attachments.
        for path in sorted(self.sources.glob('*.md')):
            path = scoped(self.root, path)
            with path.open(encoding='utf-8') as handle:
                head = handle.read(20000)
            if re.search(r'https://(?:www\.)?xiaoyuzhoufm\.com/episode/' + episode_id + r'(?![a-f0-9])', head):
                if re.search(r'^##\s+(?:转写全文|逐字稿|全文|Transcript)\s*$', head, re.M | re.I):
                    matches.append(path)
        if len(matches) > 1:
            raise BridgeError('素材库里有多个同集全文，请先确认要使用的原始稿。')
        return matches[0] if matches else None

    def reading(self, episode_id):
        return scoped(self.root, self.readings / f'小宇宙-{episode_id}-阅读批注.md')

    def refined_paths(self, episode_id):
        document = scoped(self.root, self.readings / f'小宇宙-{episode_id}-精校阅读版.md')
        data = scoped(self.root, self.readings / '_attachments/xiaoyuzhou' / episode_id / 'reading-v1.json')
        return document, data

    def load(self, episode_id):
        source = self.source(episode_id)
        reading = self.reading(episode_id)
        annotations = load_notes(read_text(reading)) if reading.exists() else []
        if not source:
            return dict(success=True, found=False, annotations=annotations)
        raw = read_text(source)
        title = re.search(r'^#\s+(.+)$', raw, re.M)
        result = dict(success=True, found=True, raw=raw, format='md',
                    title=title[1] if title else source.stem,
                    sourcePath=source.relative_to(self.root).as_posix(), annotations=annotations)
        document, data = self.refined_paths(episode_id)
        if data.exists():
            try:
                reading = json.loads(read_text(data))
                if not isinstance(reading, dict):
                    raise ValueError('阅读版数据不是对象。')
            except ValueError:
                result['readingInvalid'] = True
                return result
            if not document.is_file() or reading.get('artifactHash') != hashlib.sha256(document.read_bytes()).hexdigest():
                result['readingManualEdited'] = True
            elif reading.get('sourceHash') != hashlib.sha256(raw.encode()).hexdigest():
                result['readingStale'] = True
            elif reading.get('sourceInputHash'):
                imported = scoped(self.root, self.sources / '_attachments/xiaoyuzhou' / episode_id / 'digest-import-original.txt')
                original_input = read_text(imported) if imported.is_file() else ''
                if reading['sourceInputHash'] != hashlib.sha256(original_input.encode()).hexdigest():
                    result['readingStale'] = True
                else:
                    result['raw'] = original_input
                    result['reading'] = dict(reading, sourceHash=reading['sourceInputHash'])
            else:
                result['reading'] = reading
        return result

    def audio_clip(self, episode_id, message):
        start, end = checked_window(message.get('start'), message.get('end'))
        source = scoped(self.root, self.sources / '_attachments/xiaoyuzhou' / episode_id / 'source.mp3')
        if not source.is_file():
            raise BridgeError('这集还没有本地原音频。请将该集音频存为素材附件中的 source.mp3 后再听音复核。')
        ffmpeg = self.config.get('ffmpeg')
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise BridgeError('本机连接未配置 FFmpeg，请重新运行安装器；它不会自动安装工具。')
        with tempfile.TemporaryDirectory(prefix='digest-listen-') as temporary:
            wav, mp3 = Path(temporary) / 'clip.wav', Path(temporary) / 'clip.mp3'
            try:
                subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(start), '-i', str(source), '-t', str(end - start), '-ac', '1', '-ar', '16000', str(wav)], check=True, capture_output=True, timeout=25)
                with wave.open(str(wav), 'rb') as clip:
                    duration = clip.getnframes() / clip.getframerate()
                    if (clip.getnchannels() != 1 or clip.getframerate() != 16000
                            or not 0 < duration <= end - start + 0.05):
                        raise BridgeError('本地音频片段为空或格式无效。')
                actual_end = min(end, start + duration)
                recognized = subprocess.run([sys.executable, str(Path(__file__).with_name('local_whisper.py')), str(wav)], check=True, capture_output=True, timeout=150)
                if len(recognized.stdout) > 100000:
                    raise BridgeError('本地识别结果过大。')
                candidate = json.loads(recognized.stdout)
                if not isinstance(candidate, dict) or set(candidate) != {'text', 'model'}:
                    raise BridgeError('本地识别结果格式无效。')
                local_text = text(candidate['text'], 30000)
                if not local_text.strip() or candidate['model'] != LOCAL_AUDIO_MODEL:
                    raise BridgeError('本地识别未得到有效文字或模型不匹配。')
                subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(wav), '-c:a', 'libmp3lame', '-b:a', '32k', str(mp3)], check=True, capture_output=True, timeout=25)
            except (subprocess.SubprocessError, ValueError, OSError, wave.Error, ZeroDivisionError) as error:
                if isinstance(error, BridgeError):
                    raise
                raise BridgeError('本地转写失败或超时。需要已安装的 faster-whisper 和已缓存的 medium 模型；没有自动下载或替换工具。') from error
            if not 0 < mp3.stat().st_size <= 525000:
                raise BridgeError('音频片段为空或过大，请缩短听音窗口。')
            audio = base64.b64encode(mp3.read_bytes()).decode()
            if len(audio) > 700000:
                raise BridgeError('音频片段过大，请缩短听音窗口。')
            return dict(success=True, audio=audio, localText=local_text, localModel=LOCAL_AUDIO_MODEL, start=start, end=actual_end)

    def save_refined(self, episode_id, title, raw, source, reading):
        if not isinstance(reading, dict) or reading.get('version') != 'podcast-reading-v1' or reading.get('sourceHash') != hashlib.sha256(raw.encode()).hexdigest():
            raise BridgeError('阅读版与原稿版本不一致，请重新载入后精校。')
        items = reading.get('items')
        total = reading.get('total')
        if not isinstance(items, list) or not items or len(items) > 500 or type(total) is not int or not len(items) <= total <= 500:
            raise BridgeError('阅读版覆盖记录无效。')
        canonical = read_text(source)
        if canonical != raw:
            imported = scoped(self.root, self.sources / '_attachments/xiaoyuzhou' / episode_id / 'digest-import-original.txt')
            if not imported.is_file() or read_text(imported) != raw:
                raise BridgeError('阅读版原稿与保存的素材不一致。')
        originals = [entry for entry in source_entries(raw)
                     if entry['speaker'] != '转写说明' and not re.match(r'^\[\^[^\]]+\]:', entry['text'])]
        by_id = {entry['id']: entry for entry in originals}
        if total != len(originals):
            raise BridgeError('阅读版覆盖总数与原稿不一致。')
        ids = set()
        checked_items = []
        last_index = -1
        for item in items:
            if not isinstance(item, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', text(item.get('id'), 80)) or item['id'] in ids:
                raise BridgeError('阅读版段落编号重复或无效。')
            ids.add(item['id'])
            original = by_id.get(item['id'])
            index = next((i for i, entry in enumerate(originals) if entry['id'] == item['id']), -1)
            if (not original or index <= last_index or text(item.get('sourceText'), 20000) != original['text']
                    or item['sourceText'] not in canonical or not text(item.get('text'), 20000).strip()):
                raise BridgeError('阅读版段落与原稿无法对应。')
            last_index = index
            start = item.get('start')
            if start is not None and (type(start) not in (int, float) or not 0 <= start <= 604800):
                raise BridgeError('阅读版时间戳无效。')
            speaker = text(item.get('speaker', ''), 500)
            if start != original['start'] or speaker != original['speaker']:
                raise BridgeError('阅读版时间或说话人标签与原稿不一致。')
            issues = item.get('issues')
            if not isinstance(issues, list) or len(issues) > 20:
                raise BridgeError('阅读版疑点记录无效。')
            for issue in issues:
                text(issue, 600)
            checked = {key: item[key] for key in ('id', 'sourceText', 'text', 'issues')}
            checked.update(start=start, speaker=speaker)
            for key, limit in (('provider', 80), ('model', 200)):
                if key in item:
                    value = text(item[key], limit)
                    if not value.strip() or re.search(r'[\x00-\x1f]', value):
                        raise BridgeError('精校模型记录无效。')
                    if key == 'provider' and value not in {'openrouter', '302ai', 'deepseek', 'codex'}:
                        raise BridgeError('精校提供方无效。')
                    checked[key] = value
            if 'updatedAt' in item:
                checked['updatedAt'] = checked_date(item['updatedAt'])
            if 'audioEvidence' in item:
                checked['audioEvidence'] = checked_audio_evidence(item['audioEvidence'], start)
            checked_items.append(checked)
        document, data = self.refined_paths(episode_id)
        if document.exists():
            try:
                previous = json.loads(read_text(data)) if data.exists() else {}
            except ValueError as error:
                raise BridgeError('阅读版记录已损坏，已保留原文件；请先在 Obsidian 中检查。') from error
            if not isinstance(previous, dict):
                raise BridgeError('阅读版记录格式无效，已保留原文件。')
            if previous.get('artifactHash') != hashlib.sha256(document.read_bytes()).hexdigest():
                raise BridgeError('阅读版已在 Obsidian 中人工修改，已保留原文件；请先核对再生成新版本。')
        relative = source.relative_to(self.root).as_posix()
        content = f'# {safe_name(title)}｜精校阅读版\n\n原始素材：[[{relative}]]\n\n> 精校 {len(items)}/{total} 段；模型整理稿，尚未逐句人工听校。文字精校仅处理文本；标有听音的段落还经过本地及云端音频识别。无疑点记录不代表准确无误。未精校内容请阅读原稿。\n\n'
        for item in checked_items:
            evidence = item.get('audioEvidence')
            label = f"本地＋云端听音片段复核（{stamp(evidence['start'])}–{stamp(evidence['end'])}）" if evidence else '文字精校'
            content += f"**[{stamp(item['start'])}] {item.get('speaker', '')} · {label}**\n\n{item['text']}\n\n"
            if item['issues']:
                content += '**校正记录与待核验事项**\n\n' + ''.join('- ' + issue.replace('\n', ' ') + '\n' for issue in item['issues']) + '\n'
        stored = {key: reading[key] for key in ('version', 'sourceHash', 'items', 'total')}
        stored['items'] = checked_items
        stored['sourceHash'] = hashlib.sha256(canonical.encode()).hexdigest()
        if raw != canonical:
            stored['sourceInputHash'] = hashlib.sha256(raw.encode()).hexdigest()
        stored['artifactHash'] = hashlib.sha256(content.encode()).hexdigest()
        serialized = json.dumps(stored, ensure_ascii=False, indent=2)
        if len(content.encode()) > MAX_BYTES or len(serialized.encode()) > MAX_BYTES:
            raise BridgeError('阅读版过大，请分段保存。')
        atomic_write(document, content)
        atomic_write(data, serialized)
        return document.relative_to(self.root).as_posix()

    def save(self, episode_id, message):
        title = text(message.get('title'), 500)
        raw = text(message.get('raw'))
        body = text(message.get('body'))
        if not raw.strip() or not body.strip():
            raise BridgeError('全文为空，未归档。')
        annotations = message.get('annotations', [])
        if not isinstance(annotations, list) or len(annotations) > 500:
            raise BridgeError('批注列表无效或过长。')
        notes = [checked_note(item) for item in annotations]
        if len({item['id'] for item in notes}) != len(notes):
            raise BridgeError('批注编号重复。')
        source = self.source(episode_id)
        if source and read_text(source) != raw:
            imported = scoped(self.root, self.sources / '_attachments/xiaoyuzhou' / episode_id / 'digest-import-original.txt')
            if not imported.exists() or read_text(imported) != raw:
                raise BridgeError('此集已有原始稿。请重新载入素材库全文后归档，避免批注关联到不同版本。')
        reading = self.reading(episode_id)
        existing = read_text(reading) if reading.exists() else ''
        old_notes = load_notes(existing) if existing else []
        old_by_id = {item['id']: item for item in old_notes}
        for note in notes:
            if note['id'] in old_by_id and note != old_by_id[note['id']]:
                raise BridgeError('这条批注已在 Obsidian 中修改，未覆盖；请重新载入后检查。')
        url = 'https://www.xiaoyuzhoufm.com/episode/' + episode_id
        day = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d')
        if not source:
            source = scoped(self.root, self.sources / f'{safe_name(title)}--{episode_id}.md')
            if source.exists():
                raise BridgeError('同名素材已存在，未覆盖。')
            content = (f'# {safe_name(title)}\n\n| 属性 | 值 |\n|------|-----|\n'
                       f'| 来源 | 小宇宙播客 |\n| 链接 | {url} |\n| 保存日期 | {day} |\n'
                       '| 文稿状态 | 导入全文，尚未逐句人工听校 |\n\n## 转写全文\n\n' + body + '\n')
            # Keep imported bytes/text separately from the reading representation.
            attachment = scoped(self.root, self.sources / '_attachments/xiaoyuzhou' / episode_id / 'digest-import-original.txt')
            if attachment.exists() and read_text(attachment) != raw:
                raise BridgeError('原始导入文件已存在且内容不同，未覆盖。')
            if not attachment.exists():
                atomic_write(attachment, raw)
            atomic_write(source, content)
        relative = source.relative_to(self.root).as_posix()
        if not existing:
            existing = (f'# {safe_name(title)}｜阅读批注\n\n| 属性 | 值 |\n|------|-----|\n'
                        f'| 来源 | {url} |\n| 原始素材 | [[{relative}]] |\n'
                        f'| 建立日期 | {day} |\n\n## 原文\n\n![[{relative}]]\n\n## 我的批注\n')
        additions = [note for note in notes if note['id'] not in old_by_id]
        # If a native reply is lost, a retry finds the same IDs and does not duplicate entries.
        if additions or not reading.exists():
            atomic_write(reading, existing + ''.join(note_block(note) for note in additions))
        refined_path = None
        if message.get('reading'):
            refined_path = self.save_refined(episode_id, title, raw, source, message['reading'])
        return dict(success=True, sourcePath=relative, readingPath=reading.relative_to(self.root).as_posix(), refinedPath=refined_path,
                    added=len(additions), savedIds=[note['id'] for note in notes])

    def handle(self, message):
        if not isinstance(message, dict):
            raise BridgeError('请求格式无效。')
        episode_id = message.get('episodeId', '')
        if not isinstance(episode_id, str) or not EPISODE_RE.fullmatch(episode_id):
            raise BridgeError('节目编号无效。')
        if message.get('action') == 'load':
            return self.load(episode_id)
        if message.get('action') == 'save':
            return self.save(episode_id, message)
        if message.get('action') == 'audioClip':
            return self.audio_clip(episode_id, message)
        raise BridgeError('不支持的操作。')


def read_exact(handle, length):
    parts = bytearray()
    while len(parts) < length:
        block = handle.read(length - len(parts))
        if not block:
            raise BridgeError('请求未完整到达。')
        parts.extend(block)
    return bytes(parts)


def serve(config_path, input_stream=None, output_stream=None):
    incoming, outgoing = input_stream or sys.stdin.buffer, output_stream or sys.stdout.buffer
    config = json.loads(Path(config_path).read_text())
    vault = Vault(config)
    while True:
        header = incoming.read(4)
        if not header:
            return
        length = 0
        try:
            if len(header) != 4:
                raise BridgeError('请求头无效。')
            length = struct.unpack('<I', header)[0]
            if length > MAX_BYTES:
                raise BridgeError('请求过大，请分段保存。')
            message = json.loads(read_exact(incoming, length))
            # Serialize writes across Chrome's one-process-per-message native hosts.
            with open(str(config_path) + '.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = vault.handle(message)
        except (ValueError, OSError, KeyError) as error:
            result = dict(success=False, error=str(error))
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > MAX_BYTES:
            encoded = json.dumps(dict(success=False, error='文稿过大，请分段导入。'), ensure_ascii=False).encode()
        outgoing.write(struct.pack('<I', len(encoded)) + encoded)
        outgoing.flush()
        if length > MAX_BYTES:
            return


if __name__ == '__main__':
    config_file = os.environ.get('YTD_OBSIDIAN_CONFIG', '')
    if not config_file:
        print('Obsidian bridge configuration is missing.', file=sys.stderr)
        sys.exit(1)
    serve(config_file)
