#!/usr/bin/env python3
"""Scoped Obsidian bridge: reads episode sources, appends annotations, no network/commands."""
import base64
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile

HOST_NAME = 'com.youtube_digest.obsidian'
MAX_BYTES = 950000
EPISODE_RE = re.compile(r'^[a-f0-9]{24}$')


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
    return dict(id=note_id, quote=quote, thought=thought, start=start, createdAt=created)


def stamp(value):
    return '无时间戳' if value is None else f'{int(value // 60)}:{int(value % 60):02}'


def note_block(note):
    # Machine metadata only contains identity/time; human text remains the readable source of truth.
    meta = {key: note[key] for key in ('id', 'start', 'createdAt')}
    encoded = base64.urlsafe_b64encode(json.dumps(meta).encode()).decode()
    quote = '\n'.join('> ' + line for line in note['quote'].split('\n'))
    return f"\n<!-- digest-note:{encoded} -->\n### {stamp(note['start'])}\n\n**原文**\n\n{quote}\n\n**我的感想**\n\n{note['thought']}\n"


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
            notes.append(checked_note({**meta, 'quote': quote, 'thought': thought.removesuffix('\n')}))
        except (ValueError, KeyError, IndexError):
            # Never silently rewrite a manually edited/unrecognized annotation.
            raise BridgeError('已有批注格式被修改，已保留原文件；请在 Obsidian 中检查后再保存。')
    return notes


class Vault:
    def __init__(self, config):
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

    def load(self, episode_id):
        source = self.source(episode_id)
        reading = self.reading(episode_id)
        annotations = load_notes(read_text(reading)) if reading.exists() else []
        if not source:
            return dict(success=True, found=False, annotations=annotations)
        raw = read_text(source)
        title = re.search(r'^#\s+(.+)$', raw, re.M)
        return dict(success=True, found=True, raw=raw, format='md',
                    title=title[1] if title else source.stem,
                    sourcePath=source.relative_to(self.root).as_posix(), annotations=annotations)

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
        return dict(success=True, sourcePath=relative, readingPath=reading.relative_to(self.root).as_posix(),
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
