"""Audio-turn overlays: preserve canonical transcript bytes and paragraph anchors."""
import datetime
import hashlib
import json
import math
from pathlib import Path

from podcast_host import (BridgeError, EPISODE_RE, MAX_BYTES, Vault, atomic_write,
                          read_text, scoped, source_entries)
from transcription_jobs import merged_segments, timestamp

VERSION = 'podcast-speakers-v1'
FILE_LIMIT = 8 * 1024 * 1024
MAX_SEGMENTS = 50000
HASH_FIELDS = ('sourceHash', 'audioHash', 'checkpointHash')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_hash(path):
    checksum = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def checked_path(root, path):
    # Reject in-vault symlinks as well as links escaping the configured vault.
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
        raise BridgeError('说话人附件路径包含符号链接，已拒绝读取或覆盖。')
    return scoped(root, path)


def paths(config, episode_id):
    if not isinstance(episode_id, str) or not EPISODE_RE.fullmatch(episode_id):
        raise BridgeError('节目编号无效。')
    vault = Vault(config)
    folder = checked_path(vault.root, vault.sources / '_attachments/xiaoyuzhou' / episode_id)
    document = checked_path(vault.root, vault.readings / f'小宇宙-{episode_id}-说话人版全文.md')
    data = checked_path(vault.root, vault.readings / '_attachments/xiaoyuzhou' / episode_id / 'speakers-v1.json')
    return vault, folder, document, data


def read_json(path):
    if path.stat().st_size > FILE_LIMIT:
        raise BridgeError('说话人数据或转写检查点过大，未截断内容。')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as error:
        raise BridgeError('说话人数据或转写检查点损坏。') from error


def number(value, maximum=28801):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def align_source(entries, segments, duration):
    """Map exact ASR strings to source spans, never infer word timing or meaning.

    Offsets are Python Unicode code-point indices. The preceding join space
    belongs to the following segment so every source character occurs once.
    """
    groups = merged_segments(segments, duration)
    spoken = [item for item in entries if item['speaker'] != '转写说明'
              and not item['text'].lstrip().startswith('[^')]
    if len(groups) != len(spoken) or any(
            group['text'] != entry['text'] or math.floor(group['start']) != entry['start']
            for group, entry in zip(groups, spoken)):
        raise BridgeError('原稿与本机 ASR 检查点不能逐字对应，未猜测说话人归属。')
    result, group_index, offset = [], 0, 0
    for index, segment in enumerate(segments):
        value = segment['text'].strip()
        if not value:
            continue
        entry = spoken[group_index]
        exact = (' ' if offset else '') + value
        stop = offset + len(exact)
        if entry['text'][offset:stop] != exact:
            raise BridgeError('ASR 片段与原稿字符范围不一致。')
        result.append(dict(asrIndex=index, sourceEntryId=entry['id'],
                           sourceStart=offset, sourceEnd=stop,
                           sourceSpans=[dict(id=entry['id'], charStart=offset, charEnd=stop)],
                           start=segment['start'], end=segment['end'], text=exact))
        offset = stop
        if offset == len(entry['text']):
            group_index += 1
            offset = 0
    if group_index != len(spoken) or offset:
        raise BridgeError('ASR 检查点未覆盖全部原稿，未生成不完整的说话人版。')
    return result


def load_inputs(config, episode_id):
    vault, folder, _, _ = paths(config, episode_id)
    source = vault.source(episode_id)
    if source is None:
        raise BridgeError('请先完成并读取本集转写全文。')
    raw = read_text(source)
    audio = checked_path(vault.root, folder / 'source.mp3')
    checkpoint = checked_path(vault.root, folder / 'transcribe-segments.json')
    if not audio.is_file():
        raise BridgeError('缺少本集原始音频，无法按真实音频区分说话人。')
    if not checkpoint.is_file():
        raise BridgeError('缺少本集 ASR 片段检查点，无法将声音区间关联到原稿。')
    state = read_json(checkpoint)
    if not isinstance(state, dict):
        raise BridgeError('ASR 检查点格式无效。')
    duration = state.get('duration')
    if not number(duration, 28800) or duration <= 0 or not number(state.get('processedSeconds')) or state['processedSeconds'] < duration - 1:
        raise BridgeError('整集 ASR 尚未完成，暂不生成完整说话人版。')
    segments = state.get('segments')
    if not isinstance(segments, list) or not 0 < len(segments) <= MAX_SEGMENTS:
        raise BridgeError('ASR 检查点缺少有效片段。')
    audio_hash = file_hash(audio)
    if state.get('audioHash') != audio_hash:
        raise BridgeError('ASR 检查点对应的音频已变更，不能沿用说话人时间关系。')
    entries = source_entries(raw)
    aligned = align_source(entries, segments, duration)
    return dict(raw=raw, sourcePath=source.relative_to(vault.root).as_posix(),
                sourceHash=digest(raw.encode()), audioPath=str(audio), audioHash=audio_hash,
                checkpointHash=file_hash(checkpoint), duration=duration,
                segments=segments, asrSegments=segments, sourceEntries=entries, alignedSegments=aligned)


def normalized_turns(turns, duration):
    if not isinstance(turns, list) or not 0 < len(turns) <= MAX_SEGMENTS:
        raise BridgeError('音频说话人识别没有返回有效声段。')
    result = []
    for item in turns:
        if not isinstance(item, dict):
            raise BridgeError('说话人声段格式无效。')
        start, end, speaker = item.get('start'), item.get('end'), item.get('speaker')
        if (not number(start, duration + 1) or not number(end, duration + 1) or start >= end
                or type(speaker) not in (str, int) or not str(speaker).strip() or len(str(speaker)) > 120):
            raise BridgeError('说话人声段时间或标签无效。')
        if 'unassigned' in item and type(item['unassigned']) is not bool:
            raise BridgeError('说话人未分配标记必须是布尔值。')
        normalized = dict(start=start, end=end, cluster=str(speaker), unassigned=item.get('unassigned', False))
        if 'originalSpeaker' in item:
            original = item['originalSpeaker']
            if type(original) not in (str, int) or not str(original).strip() or len(str(original)) > 120:
                raise BridgeError('说话人声段的原始声簇来源无效。')
            normalized['originalSpeaker'] = original
        result.append(normalized)
    flags = {}
    for turn in result:
        flags.setdefault(turn['cluster'], set()).add(turn['unassigned'])
    if any(len(values) > 1 for values in flags.values()):
        raise BridgeError('同一声簇同时被标为可信和未分配，未猜测其归属。')
    return sorted(result, key=lambda item: (item['start'], item['end'], item['cluster']))


def cluster_evidence(turns, duration):
    """Count each cluster's audible interval union once; this is not confidence."""
    evidence = {}
    for turn in turns:  # normalized turns are sorted by start
        item = evidence.setdefault(turn['cluster'], dict(seconds=0.0, edge=0.0, turnCount=0))
        start, end = min(duration, turn['start']), min(duration, turn['end'])
        item['seconds'] += max(0, end - max(start, item['edge']))
        item['edge'] = max(item['edge'], end)
        item['turnCount'] += 1
    return evidence


def classify(segment, turns):
    start, end = segment['start'], segment['end']
    hits = [(max(start, turn['start']), min(end, turn['end']), turn['cluster'])
            for turn in turns if turn['start'] < end and turn['end'] > start]
    unassigned = sorted({turn['cluster'] for turn in turns
                         if turn.get('unassigned') and turn['start'] < end and turn['end'] > start})
    clusters = sorted({item[2] for item in hits} - set(unassigned))
    overlap = any(a[2] != b[2] and min(a[1], b[1]) > max(a[0], b[0]) + 0.000001
                  for index, a in enumerate(hits) for b in hits[index + 1:])
    covered, edge = 0.0, start
    for left, right, _ in sorted(hits):
        covered += max(0, right - max(left, edge))
        edge = max(edge, right)
    ratio = covered / (end - start) if end > start else 0
    # Explicitly uncertain embeddings and insufficient total audio evidence both
    # make touched ASR segments uncertain, even if a known speaker dominates.
    # Coverage is a time-overlap measurement, not confidence or word alignment.
    status = ('uncertain' if unassigned else 'overlap' if overlap else 'mixed' if len(clusters) > 1 else
              'single' if len(clusters) == 1 and ratio >= 0.9 else 'uncertain')
    return clusters, status, round(min(1, max(0, ratio)), 5), unassigned


def build_data(episode_id, inputs, turns, metadata):
    if not isinstance(metadata, dict) or any(metadata.get(key) != inputs[key] for key in HASH_FIELDS):
        raise BridgeError('原稿、音频或 ASR 检查点已变更，未保存过期说话人结果。')
    model, params = metadata.get('model'), metadata.get('params', {})
    if not isinstance(model, str) or not model.strip() or len(model) > 300 or not isinstance(params, dict):
        raise BridgeError('说话人模型或参数记录无效。')
    try:
        if len(json.dumps(params, ensure_ascii=False, allow_nan=False).encode()) > 32000:
            raise ValueError()
    except (TypeError, ValueError) as error:
        raise BridgeError('说话人参数记录无效或过长。') from error
    minimum = params.get('minSpeakerSeconds', 0)
    if not number(minimum, 30):
        raise BridgeError('说话人最短声音证据阈值必须为 0 至 30 秒。')
    normalized = normalized_turns(turns, inputs['duration'])
    evidence = cluster_evidence(normalized, inputs['duration'])
    explicit_unknown = {turn['cluster'] for turn in normalized if turn['unassigned']}
    unassigned = explicit_unknown | {cluster for cluster, item in evidence.items() if item['seconds'] < minimum}
    reasons = {cluster: 'embedding-uncertain' if cluster in explicit_unknown else 'insufficient-speaker-duration'
               for cluster in unassigned}
    for turn in normalized:
        turn['unassigned'] = turn['cluster'] in unassigned
    classified = [(segment, *classify(segment, normalized)) for segment in inputs['alignedSegments']]
    first_reliable, first_turn = {}, {}
    for turn in normalized:
        if not turn['unassigned']:
            first_turn.setdefault(turn['cluster'], turn['start'])
    for segment, clusters, status, _, _ in classified:
        if status == 'single':
            first_reliable.setdefault(clusters[0], segment['start'])
    ordered = sorted(first_turn, key=lambda key: (key not in first_reliable, first_reliable.get(key, first_turn[key]), key))
    mapping = {cluster: f'S{index + 1}' for index, cluster in enumerate(ordered)}
    speakers = [dict(id=mapping[cluster], label=f'说话人 {index + 1}',
                     reliableAppearance=cluster in first_reliable) for index, cluster in enumerate(ordered)]
    rows = []
    for segment, clusters, status, ratio, short_clusters in classified:
        row = dict(segment, sourceSpans=[dict(span) for span in segment['sourceSpans']],
                   speakerIds=sorted((mapping[key] for key in clusters), key=lambda key: int(key[1:])),
                   status=status, coverageRatio=ratio, asrSegmentIndices=[segment['asrIndex']])
        del row['asrIndex']
        if short_clusters:
            row['unassignedClusterIds'] = short_clusters
        # Merge only same-source, contiguous, single-speaker segments. Never
        # redistribute words when an ASR segment straddles a speaker boundary.
        previous = rows[-1] if rows else None
        if (previous and previous['status'] == row['status'] == 'single'
                and previous['sourceEntryId'] == row['sourceEntryId']
                and previous['sourceEnd'] == row['sourceStart']
                and previous['speakerIds'] == row['speakerIds']
                and 0 <= row['start'] - previous['end'] <= 1.5):
            previous['sourceEnd'] = row['sourceEnd']
            previous['sourceSpans'][0]['charEnd'] = row['sourceEnd']
            previous['end'] = row['end']
            previous['text'] += row['text']
            previous['coverageRatio'] = min(previous['coverageRatio'], row['coverageRatio'])
            previous['asrSegmentIndices'].extend(row['asrSegmentIndices'])
        else:
            rows.append(row)
    for index, row in enumerate(rows):
        row['id'] = f's-{index}'
    return dict(version=VERSION, episodeId=episode_id, **{key: inputs[key] for key in HASH_FIELDS},
                sourcePath=inputs['sourcePath'], duration=inputs['duration'], model=model, params=params,
                generatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                alignment='asr-segment-overlap', offsetUnit='unicode-code-point', wordAlignment=False,
                identityStatus='unverified', minimumSingleSpeakerCoverage=0.9, minSpeakerSeconds=minimum,
                unassignedClusters=[dict(clusterId=cluster, durationSeconds=round(evidence[cluster]['seconds'], 6),
                                         turnCount=evidence[cluster]['turnCount'], reason=reasons[cluster])
                                    for cluster in evidence if cluster in unassigned],
                unassignedTurnCount=sum(turn['unassigned'] for turn in normalized),
                speakers=speakers, segments=rows,
                turns=[dict(id=f't-{i}', start=turn['start'], end=turn['end'], clusterId=turn['cluster'],
                            speakerId=mapping.get(turn['cluster']), unassigned=turn['unassigned'],
                            unassignedReason=reasons.get(turn['cluster']),
                            **({'originalSpeaker': turn['originalSpeaker']} if 'originalSpeaker' in turn else {}))
                       for i, turn in enumerate(normalized)])


def markdown(data):
    labels = {item['id']: item['label'] for item in data['speakers']}
    parts = [f'# 小宇宙说话人版全文\n\n来源：https://www.xiaoyuzhoufm.com/episode/{data["episodeId"]}\n\n'
             f'原始素材：[[{data["sourcePath"]}]]\n\n'
             '> 依据真实音频声段与 ASR 片段的时间重叠关系标注，未逐词对齐，未经人工逐句听校；编号不代表已确认身份。'
             '跨换人边界的文字整段保留，不按语义猜分。原稿、精校和概览不作修改。\n\n']
    if data.get('minSpeakerSeconds', 0) > 0:
        parts.append(f'> 同一声簇累计声音区间并集不足 {data["minSpeakerSeconds"]:g} 秒时不分配稳定编号；'
                     '声纹归属被显式标为不确定的片段，即使时间较长也不分配编号。'
                     f'本次保留 {len(data["unassignedClusters"])} 个未分配声簇，相关文字标为不确定。'
                     '这是声音证据量筛选，不代表置信度或识别准确率。\n\n')
    for segment in data['segments']:
        label = ' / '.join(labels[key] for key in segment['speakerIds']) or '说话人不确定'
        suffix = {'single': '', 'mixed': '｜多人，片段内换人，未逐词对齐',
                  'overlap': '｜多人声音重叠，文字归属不确定', 'uncertain': '｜声音证据不足或归属不确定'}[segment['status']]
        parts.append(f'## [{timestamp(segment["start"])}–{timestamp(segment["end"])}] {label}{suffix}\n\n'
                     + segment['text'] + '\n\n')
    return ''.join(parts)


def publish(config, episode_id, turns, metadata):
    vault, _, document, path = paths(config, episode_id)
    with vault.source_lock(episode_id):
        inputs = load_inputs(config, episode_id)
        data = build_data(episode_id, inputs, turns, metadata)
        if document.exists():
            existing = read_json(path) if path.exists() else {}
            if existing.get('artifactHash') != file_hash(document):
                raise BridgeError('说话人版全文已在 Obsidian 中修改，未覆盖人工内容。')
        content = markdown(data)
        data['artifactHash'] = digest(content.encode())
        serialized = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(serialized.encode()) > FILE_LIMIT or len(content.encode()) > FILE_LIMIT:
            raise BridgeError('说话人版全文过大，已保留计算结果，未截断保存。')
        atomic_write(document, content)
        atomic_write(path, serialized)
        return dict(success=True, sourceHash=inputs['sourceHash'], speakerCount=len(data['speakers']),
                    segmentCount=len(data['segments']), sourcePath=inputs['sourcePath'],
                    speakerPath=document.relative_to(vault.root).as_posix())


def load(config, episode_id):
    vault, _, document, path = paths(config, episode_id)
    if not path.exists():
        return dict(success=True, found=False)
    try:
        data = read_json(path)
        if not isinstance(data, dict) or data.get('version') != VERSION or data.get('episodeId') != episode_id:
            raise BridgeError('说话人数据版本或节目编号无效。')
        if not document.is_file() or data.get('artifactHash') != file_hash(document):
            return dict(success=True, found=True, manualEdited=True, error='说话人版全文已被手动修改，请在 Obsidian 中查看。')
        source = vault.source(episode_id)
        if source is None or digest(read_text(source).encode()) != data.get('sourceHash'):
            return dict(success=True, found=True, stale=True, error='原稿已变更，请重新区分说话人。')
        inputs = load_inputs(config, episode_id)
        if any(data.get(key) != inputs[key] for key in HASH_FIELDS):
            return dict(success=True, found=True, stale=True, error='原稿、音频或 ASR 检查点已变更，请重新区分说话人。')
        entries, offsets = {entry['id']: entry for entry in inputs['sourceEntries']}, {}
        segments = data.get('segments')
        speakers = data.get('speakers')
        if not isinstance(speakers, list) or not isinstance(segments, list) or not 0 < len(segments) <= MAX_SEGMENTS:
            raise BridgeError('说话人数据缺少有效片段或标签。')
        if not number(data.get('minSpeakerSeconds', 0), 30):
            raise BridgeError('说话人最短声音证据阈值无效。')
        speaker_ids = {speaker['id'] for speaker in speakers}
        for segment in segments:
            entry = entries.get(segment.get('sourceEntryId'))
            left, right = segment.get('sourceStart'), segment.get('sourceEnd')
            if (not entry or type(left) is not int or type(right) is not int
                    or left != offsets.get(entry['id'], 0) or not left < right <= len(entry['text'])
                    or entry['text'][left:right] != segment.get('text')
                    or segment.get('sourceSpans') != [dict(id=entry['id'], charStart=left, charEnd=right)]
                    or segment.get('status') not in {'single', 'mixed', 'overlap', 'uncertain'}
                    or not isinstance(segment.get('speakerIds'), list)
                    or any(key not in speaker_ids for key in segment['speakerIds'])
                    or (not speaker_ids and segment['status'] != 'uncertain')
                    or (segment['status'] == 'single' and len(segment['speakerIds']) != 1)
                    or (segment['status'] in {'mixed', 'overlap'} and len(segment['speakerIds']) < 2)
                    or (segment.get('unassignedClusterIds') and segment['status'] != 'uncertain')
                    or not number(segment.get('start'), inputs['duration'] + 1)
                    or not number(segment.get('end'), inputs['duration'] + 1)
                    or segment['start'] > segment['end']):
                raise BridgeError('说话人片段与原稿字符范围不一致。')
            offsets[entry['id']] = right
        expected = {item['sourceEntryId'] for item in inputs['alignedSegments']}
        if set(offsets) != expected or any(offsets[key] != len(entries[key]['text']) for key in expected):
            raise BridgeError('说话人数据未完整覆盖原稿。')
        public = {key: value for key, value in data.items() if key not in {'turns', 'artifactHash'}}
        public['speakerPath'] = document.relative_to(vault.root).as_posix()
        result = dict(success=True, found=True, data=public)
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES - 1000:
            return dict(success=False, found=True, error='说话人数据超过侧栏容量，请在 Obsidian 中查看已保存的完整说话人版。')
        return result
    except (ValueError, OSError, KeyError, TypeError) as error:
        return dict(success=False, found=True, error=str(error))
