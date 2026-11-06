#!/usr/bin/env python3
"""Bounded Xiaoyuzhou downloads and resumable, offline full-episode ASR jobs."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

EPISODE_RE = re.compile(r'^[a-f0-9]{24}$')
PAGE_LIMIT = 6 * 1024 * 1024
AUDIO_LIMIT = 512 * 1024 * 1024
MAX_DURATION = 8 * 60 * 60
CHUNK_SECONDS = 300
MODEL = 'faster-whisper/medium/int8'
MEDIA_DOMAINS = ('media.xyzcdn.net',)
FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')


class JobError(ValueError):
    pass


class Cancelled(JobError):
    pass


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.digest-job-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    if not path.exists():
        return default
    if path.stat().st_size > 8 * 1024 * 1024:
        raise JobError('转写检查点过大，已保留原文件。')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as error:
        raise JobError('转写检查点损坏，已保留原文件。') from error


def paths(config, episode_id):
    if not isinstance(episode_id, str) or not EPISODE_RE.fullmatch(episode_id):
        raise JobError('节目编号无效。')
    root = Path(config['vault']).expanduser().resolve()
    if not root.is_dir():
        raise JobError('配置的 Obsidian 目录不存在。')
    sources = (root / '知识库/原始素材库').resolve()
    folder = (sources / '_attachments/xiaoyuzhou' / episode_id).resolve()
    if not sources.is_relative_to(root) or not folder.is_relative_to(sources):
        raise JobError('转写目录超出已配置的素材库。')
    for name in ('transcribe-job.json', 'transcribe-config.json', 'transcribe-segments.json',
                 'transcribe-metadata.json', 'transcribe-worker.log', 'transcribe-cancel',
                 'transcribe-worker.lock', 'transcribe-control.lock', 'source.mp3',
                 'source-publish.lock',
                 'source-download.bin', 'source-preparing.mp3', 'source-download.download'):
        if (folder / name).is_symlink():
            raise JobError('转写附件包含符号链接，已拒绝读取或覆盖。')
    return root, sources, folder


@contextlib.contextmanager
def lock_file(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def worker_active(folder):
    with lock_file(folder / 'transcribe-worker.lock', False) as held:
        return held is None


def public_job(state):
    fields = ('status', 'phase', 'progress', 'processedSeconds', 'duration', 'message',
              'error', 'sourcePath', 'segmentCount', 'updatedAt', 'model')
    return {key: state[key] for key in fields if key in state}


def status(config, episode_id):
    _, _, folder = paths(config, episode_id)
    state = read_json(folder / 'transcribe-job.json', {})
    if not state:
        return dict(success=True, job=dict(status='idle', phase='idle', progress=0,
                                           processedSeconds=0, duration=0))
    if (state.get('status') == 'running' and time.time() - state.get('heartbeat', 0) > 30
            and not worker_active(folder)):
        state = dict(state, status='failed', phase='failed',
                     error='上次转写进程已停止。点击整集转写可从已完成片段继续。')
    return dict(success=True, job=public_job(state))


def require_tools(config):
    ffmpeg = config.get('ffmpeg')
    if not isinstance(ffmpeg, str) or not Path(ffmpeg).is_file():
        raise JobError('本机连接未配置 FFmpeg，请重新运行安装器。')
    ffprobe = str(Path(ffmpeg).with_name('ffprobe'))
    if not Path(ffprobe).is_file():
        raise JobError('FFmpeg 同目录缺少 ffprobe，无法核验整集音频长度。')
    return ffmpeg, ffprobe


def start(config, episode_id, title=''):
    root, sources, folder = paths(config, episode_id)
    require_tools(config)
    if not isinstance(title, str) or len(title) > 500:
        raise JobError('节目标题格式无效。')
    with lock_file(folder / 'transcribe-control.lock'):
        current = status(config, episode_id)
        job = current['job']
        if job['status'] == 'running' or (job['status'] == 'completed' and
                (root / job.get('sourcePath', '')).is_file()):
            return current
        # Keep generated artifacts separate and never replace a user's text.
        source = sources / f'小宇宙-{episode_id}-转写全文.md'
        if source.exists():
            raise JobError('此集转写全文文件已存在。请读取素材库，未覆盖现有文稿。')
        cancel = folder / 'transcribe-cancel'
        if cancel.exists():
            cancel.unlink()
        state = dict(status='running', phase='queued', progress=job.get('progress', 0),
                     processedSeconds=job.get('processedSeconds', 0), duration=job.get('duration', 0),
                     title=title, heartbeat=time.time(), model=MODEL,
                     message='已排队；关闭侧栏后转写仍会继续。')
        atomic_json(folder / 'transcribe-job.json', state)
        # This private file contains paths only, never API credentials.
        worker_config = dict(vault=str(root), ffmpeg=config['ffmpeg'], title=title,
                             allowFakeIpDns=config.get('allowFakeIpDns') is True)
        atomic_json(folder / 'transcribe-config.json', worker_config)
        try:
            with (folder / 'transcribe-worker.log').open('ab') as log:
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker',
                                  str(folder / 'transcribe-config.json'), episode_id],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
        except OSError as error:
            state.update(status='failed', phase='failed', error='无法启动本地转写进程。')
            atomic_json(folder / 'transcribe-job.json', state)
            raise JobError(state['error']) from error
        return dict(success=True, job=public_job(state))


def cancel(config, episode_id):
    _, _, folder = paths(config, episode_id)
    with lock_file(folder / 'transcribe-control.lock'):
        result = status(config, episode_id)
        if result['job']['status'] == 'running':
            (folder / 'transcribe-cancel').write_text('cancel\n', encoding='utf-8')
            result['job']['message'] = '正在停止；已完成片段会保留，可继续转写。'
        return result


def validate_url(url, media=False, resolve=True, allow_fake_ip=False):
    if not isinstance(url, str) or len(url) > 4096:
        raise JobError('节目音频地址无效。')
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise JobError('节目音频地址端口无效。') from error
    allowed = host in MEDIA_DOMAINS if media else host in ('www.xiaoyuzhoufm.com', 'xiaoyuzhoufm.com')
    if (parsed.scheme != 'https' or not allowed or parsed.username or parsed.password
            or port not in (None, 443) or parsed.fragment):
        raise JobError('节目音频地址不属于已支持的公开小宇宙 CDN。')
    if resolve:
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as error:
            raise JobError('无法连接公开节目地址，请检查网络后继续。') from error
        def safe_address(value):
            address = ipaddress.ip_address(value)
            return address.is_global or (allow_fake_ip is True and address.version == 4 and address in FAKE_IP_RANGE)
        if not addresses or any(not safe_address(item[4][0]) for item in addresses):
            raise JobError('节目地址解析到非公开网络，已拒绝访问。')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, media=False, allow_fake_ip=False):
        super().__init__()
        self.media = media
        self.allow_fake_ip = allow_fake_ip
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl, self.media, allow_fake_ip=self.allow_fake_ip)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_public(url, media=False, allow_fake_ip=False):
    validate_url(url, media, allow_fake_ip=allow_fake_ip)
    # No cookie jar, credentials, browser profile, or ambient HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect(media, allow_fake_ip))
    request = urllib.request.Request(url, headers={'User-Agent': 'YouTube-Digest/1.5 (podcast reader)'})
    return opener.open(request, timeout=30)


def declared_duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= MAX_DURATION:
        raise JobError('节目页面缺少有效的整集时长，未将音频当作完整节目。')
    return float(value)


def public_source_mode(value):
    if value is not None and value != 'PUBLIC':
        raise JobError('节目音频来源不是公开完整音频，未下载或转写试听片段。')
    return value


def metadata_complete(metadata, episode_id):
    if not isinstance(metadata, dict):
        return False
    # Old metadata did not establish duration or source mode. Refresh it from
    # the fixed episode page rather than trusting a pre-existing local clip.
    if metadata.get('episodeId') != episode_id or 'sourceMode' not in metadata:
        return False
    public_source_mode(metadata['sourceMode'])
    try:
        declared_duration(metadata.get('duration'))
        validate_url(metadata.get('audioUrl'), media=True, resolve=False)
    except JobError:
        return False
    return True


def verify_full_duration(actual, advertised):
    expected = declared_duration(advertised)
    actual = declared_duration(actual)
    # Allow rounding/encoder padding, not a preview or truncated episode.
    tolerance = max(2.0, min(10.0, expected * 0.001))
    if abs(actual - expected) > tolerance:
        raise JobError(f'实际音频时长 {actual:.1f} 秒与节目整集时长 {expected:.1f} 秒不符；'
                       '可能是试听或不完整音频，已保留文件，未作为整集转写。')
    return actual


def episode_metadata(episode_id, allow_fake_ip=False):
    url = 'https://www.xiaoyuzhoufm.com/episode/' + episode_id
    with open_public(url, allow_fake_ip=allow_fake_ip) as response:
        content = response.read(PAGE_LIMIT + 1)
    if len(content) > PAGE_LIMIT:
        raise JobError('节目页面过大，未下载音频。')
    html = content.decode('utf-8')
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S | re.I)
    if not match:
        raise JobError('节目页面没有可读取的音频资料，未猜测下载地址。')
    try:
        data = json.loads(match[1])
        episode = data['props']['pageProps']['episode']
        if not isinstance(episode, dict) or episode.get('eid', episode.get('id')) != episode_id:
            raise KeyError('episode id mismatch')
        enclosure = episode.get('enclosure') or {}
        media = episode.get('media') or {}
        if not isinstance(enclosure, dict) or not isinstance(media, dict):
            raise TypeError('invalid media metadata')
        source = media.get('source') or {}
        if not isinstance(source, dict):
            raise TypeError('invalid audio source metadata')
        mode = public_source_mode(source.get('mode'))
        duration = declared_duration(episode.get('duration'))
        audio_url = enclosure.get('url') or source.get('url')
        validate_url(audio_url, media=True, allow_fake_ip=allow_fake_ip)
        title = episode.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise KeyError('title')
        return dict(episodeId=episode_id, title=title, audioUrl=audio_url,
                    duration=duration, sourceMode=mode)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, JobError):
            raise
        raise JobError('节目音频资料不完整，未猜测下载地址。') from error


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def download(url, destination, check_cancel, update, allow_fake_ip=False):
    temporary = destination.with_suffix('.download')
    started = time.monotonic()
    count = 0
    try:
        with open_public(url, media=True, allow_fake_ip=allow_fake_ip) as response, temporary.open('wb') as output:
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > AUDIO_LIMIT):
                raise JobError('整集音频超过 512 MiB 限制，未下载。')
            expected = int(length) if length else None
            while True:
                check_cancel()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > AUDIO_LIMIT or time.monotonic() - started > 900:
                    raise JobError('音频下载超过大小或时间限制，可稍后继续。')
                output.write(chunk)
                update(message=f'正在下载音频：{count // (1024 * 1024)} MiB')
            if count == 0 or (expected is not None and count != expected):
                raise JobError('音频下载不完整，可稍后继续。')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def probe(ffprobe, source):
    result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-format_whitelist', 'mp3,mov,wav,flac,ogg,aac', '-show_entries', 'format=duration',
                             '-of', 'json', str(source)], capture_output=True, timeout=40, check=True)
    try:
        duration = float(json.loads(result.stdout)['format']['duration'])
    except (ValueError, KeyError, TypeError) as error:
        raise JobError('无法读取音频时长。') from error
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise JobError('音频时长无效或超过 8 小时限制。')
    return duration


def merged_segments(segments, duration):
    """Keep every ASR character, with a bounded number of readable source anchors."""
    groups = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str):
            raise JobError('转写片段检查点格式无效。')
        start, end, text = segment.get('start'), segment.get('end'), segment['text'].strip()
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end <= duration + 1):
            raise JobError('转写片段时间戳无效。')
        if not text:
            continue
        if groups and start < groups[-1]['start']:
            raise JobError('转写片段时间顺序无效。')
        if not groups or start - groups[-1]['start'] >= max(35, duration / 450) or len(groups[-1]['text']) >= 700:
            groups.append(dict(start=start, end=end, text=text))
        else:
            groups[-1]['text'] += ' ' + text
            groups[-1]['end'] = end
    if len(groups) > 480:
        size = math.ceil(len(groups) / 480)
        groups = [dict(start=part[0]['start'], end=part[-1]['end'], text=' '.join(item['text'] for item in part))
                  for i in range(0, len(groups), size) if (part := groups[i:i + size])]
    if any(len(group['text']) > 12000 for group in groups):
        raise JobError('单段转写过长，已保留检查点，未截断内容。')
    return groups


def timestamp(seconds):
    value = int(seconds)
    return f'{value // 3600}:{value // 60 % 60:02}:{value % 60:02}' if value >= 3600 else f'{value // 60:02}:{value % 60:02}'


def transcript_markdown(episode_id, title, groups, duration):
    title = re.sub(r'[\x00-\x1f]', ' ', title).strip()
    day = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
    content = (f'# {title}\n\n来源：https://www.xiaoyuzhoufm.com/episode/{episode_id}\n\n'
               f'转写日期：{day}\n\n音频时长：{timestamp(duration)}\n\n'
               f'转写工具：{MODEL}（本机运行）\n\n'
               '> 机器转写全文，尚未逐句人工听校；可能有同音字、人名、数字及分段错误。未识别说话人。\n\n'
               '## 转写全文\n\n')
    content += '\n\n'.join(f"**[{timestamp(item['start'])}]**\n\n{item['text']}" for item in groups) + '\n'
    if len(content.encode('utf-8')) > 850000:
        raise JobError('整集转写超过插件文稿上限，已保留完整片段，未截断。')
    return content


def publish_source(destination, content):
    """Publish a complete file atomically, refusing to replace any existing source."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.digest-source-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or destination.read_text(encoding='utf-8') != content:
                raise JobError('转写全文文件已存在且内容不同，未覆盖人工文稿。')
    finally:
        os.unlink(temporary)


def source_vault(config):
    spec = importlib.util.spec_from_file_location('digest_source_host', Path(__file__).with_name('podcast_host.py'))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    return host.Vault(config)


def find_existing_source(config, episode_id, vault=None):
    # Keep potentially slow cloud-backed source discovery off the short native request.
    try:
        return (vault or source_vault(config)).source(episode_id)
    except ValueError as error:
        raise JobError(str(error)) from error


def run_worker(config, episode_id):
    root, sources, folder = paths(config, episode_id)
    with lock_file(folder / 'transcribe-worker.lock', False) as held:
        if held is None:
            return
        state = read_json(folder / 'transcribe-job.json', {})
        last_write = [0.0]

        def update(force=False, **values):
            state.update(values)
            state['heartbeat'] = time.time()
            state['updatedAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if force or time.monotonic() - last_write[0] > 1:
                atomic_json(folder / 'transcribe-job.json', state)
                last_write[0] = time.monotonic()

        def check_cancel():
            if (folder / 'transcribe-cancel').exists():
                raise Cancelled('已停止；完成的片段已保存，再次点击可继续转写。')

        try:
            update(force=True, status='running', phase='preparing', error='', model=MODEL)
            existing = find_existing_source(config, episode_id)
            if existing:
                update(force=True, status='completed', phase='completed', progress=1,
                       processedSeconds=0, duration=0, sourcePath=existing.relative_to(root).as_posix(),
                       message='素材库已有本集全文，已保留原稿。')
                return
            ffmpeg, ffprobe = require_tools(config)
            check_cancel()
            metadata = read_json(folder / 'transcribe-metadata.json', {})
            source = folder / 'source.mp3'
            if source.is_file() and not metadata_complete(metadata, episode_id):
                update(force=True, phase='preparing', message='正在核验已有音频是否覆盖公开单集全文。')
                metadata = episode_metadata(episode_id, allow_fake_ip=config.get('allowFakeIpDns') is True)
                atomic_json(folder / 'transcribe-metadata.json', metadata)
            if not source.is_file():
                update(force=True, phase='download', message='正在读取单集音频地址。')
                metadata = episode_metadata(episode_id, allow_fake_ip=config.get('allowFakeIpDns') is True)
                atomic_json(folder / 'transcribe-metadata.json', metadata)
                original = folder / 'source-download.bin'
                if not original.is_file():
                    download(metadata['audioUrl'], original, check_cancel, update,
                             allow_fake_ip=config.get('allowFakeIpDns') is True)
                check_cancel()
                verify_full_duration(probe(ffprobe, original), metadata['duration'])
                update(force=True, phase='preparing', message='正在准备本地音频。')
                prepared = folder / 'source-preparing.mp3'
                try:
                    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-protocol_whitelist', 'file,pipe',
                                    '-format_whitelist', 'mp3,mov,wav,flac,ogg,aac', '-i', str(original),
                                    '-vn', '-map_metadata', '-1', '-c:a', 'libmp3lame', '-b:a', '96k',
                                    str(prepared)], check=True, capture_output=True, timeout=900)
                    verify_full_duration(probe(ffprobe, prepared), metadata['duration'])
                    os.replace(prepared, source)
                finally:
                    if prepared.exists():
                        prepared.unlink()
                original.unlink()
            duration = verify_full_duration(probe(ffprobe, source), metadata['duration'])
            # Verify coverage before loading ASR; never download a model as a side effect.
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
            from faster_whisper import WhisperModel
            model = WhisperModel('medium', device='cpu', compute_type='int8',
                                 cpu_threads=min(8, os.cpu_count() or 4), local_files_only=True)
            audio_hash = digest(source)
            checkpoint = read_json(folder / 'transcribe-segments.json', {})
            if checkpoint and (checkpoint.get('audioHash') != audio_hash or checkpoint.get('model') != MODEL):
                raise JobError('本地音频或转写模型与检查点不同，未混用旧转写结果。')
            segments = checkpoint.get('segments', [])
            processed = checkpoint.get('processedSeconds', 0)
            if type(processed) not in (int, float) or not math.isfinite(processed) or not 0 <= processed <= duration:
                raise JobError('转写进度检查点无效。')
            merged_segments(segments, duration)
            update(force=True, phase='transcribing', duration=duration, processedSeconds=processed,
                   progress=min(1, processed / duration), message='本地 Whisper 正在整集转写。')
            while processed < duration - 0.01:
                check_cancel()
                end = min(duration, processed + CHUNK_SECONDS)
                with tempfile.TemporaryDirectory(prefix='digest-asr-') as temporary:
                    wav = Path(temporary) / 'chunk.wav'
                    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', str(processed),
                                    '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mp3', '-i', str(source),
                                    '-t', str(end - processed), '-vn', '-ac', '1', '-ar', '16000', str(wav)],
                                   check=True, capture_output=True, timeout=90)
                    recognized, _ = model.transcribe(str(wav), language='zh', beam_size=5,
                                                    condition_on_previous_text=False, vad_filter=True)
                    current = []
                    for segment in recognized:
                        check_cancel()
                        if segment.text.strip():
                            current.append(dict(start=min(end, processed + segment.start), end=min(end, processed + segment.end),
                                                text=segment.text.strip()))
                        seen = min(end, processed + segment.end)
                        update(processedSeconds=seen, progress=min(1, seen / duration),
                               message=f'已识别 {timestamp(seen)} / {timestamp(duration)}；每 5 分钟音频保存一次续跑点。')
                segments.extend(current)
                processed = end
                atomic_json(folder / 'transcribe-segments.json', dict(model=MODEL, audioHash=audio_hash,
                            processedSeconds=processed, duration=duration, segments=segments))
                update(force=True, processedSeconds=processed, progress=processed / duration)
            groups = merged_segments(segments, duration)
            if not groups:
                raise JobError('整集音频未识别到讲话，已保留音频供检查。')
            title = metadata.get('title') or config.get('title') or '小宇宙节目'
            content = transcript_markdown(episode_id, title, groups, duration)
            destination = sources / f'小宇宙-{episode_id}-转写全文.md'
            vault = source_vault(config)
            with vault.source_lock(episode_id):
                existing = find_existing_source(config, episode_id, vault)
                if existing:
                    update(force=True, status='completed', phase='completed', progress=1,
                           processedSeconds=duration, duration=duration, segmentCount=len(groups),
                           sourcePath=existing.relative_to(root).as_posix(),
                           message='转写期间素材库已有本集全文，已保留该原稿；ASR 片段检查点仍保留，未发布第二份全文。')
                    return
                publish_source(destination, content)
            update(force=True, status='completed', phase='completed', progress=1, processedSeconds=duration,
                   duration=duration, segmentCount=len(groups), sourcePath=destination.relative_to(root).as_posix(),
                   message='整集转写完成，已保存到素材库。')
        except Cancelled as error:
            update(force=True, status='cancelled', phase='cancelled', message=str(error), error='')
        except Exception as error:
            traceback.print_exc()
            message = str(error) if isinstance(error, JobError) else (
                '本地转写失败。请检查网络、已安装的 faster-whisper 和 medium 缓存，再从检查点继续。'
                if not isinstance(error, ImportError) else '本地 Python 缺少 faster-whisper；未自动安装工具。')
            update(force=True, status='failed', phase='failed', error=message, message='已保留音频及完成的片段。')


def handle(config, message):
    action, episode_id = message.get('action'), message.get('episodeId')
    if action == 'transcribeStart':
        return start(config, episode_id, message.get('title', ''))
    if action == 'transcribeStatus':
        return status(config, episode_id)
    if action == 'transcribeCancel':
        return cancel(config, episode_id)
    raise JobError('不支持的转写操作。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('episode_id')
    args = parser.parse_args()
    run_worker(read_json(args.worker), args.episode_id)
