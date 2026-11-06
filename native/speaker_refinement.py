"""Conservative, local acoustic regrouping of completed diarization turns."""
from collections import Counter, defaultdict
import math

VERSION = 'mutual-knn-acoustic-cores-v1'
PARAMETERS = dict(method=VERSION, score='cosine_similarity', neighbors=10, edgeSimilarity=0.70,
    minCoreWindows=10, minCoreTurns=6, minCoreAudioSeconds=30,
    assignCentroidSimilarity=0.70, assignMargin=0.20, assignNeighborMedian=0.68,
    shortWindowSeconds=4, shortCentroidSimilarity=0.75, shortMargin=0.25, shortNeighborMedian=0.70,
    cleanIntervalMinimum=2.4, maximumWindow=8, margin=0.2, overlapMargin=0.1,
    secondWindowMinimum=18, sameGroupOverlapSeconds=0.3, sampleRate=16000, numThreads=2,
    minSpeakerSeconds=3.0, ambiguousPolicy='independent unknown turn; never propagate old cluster labels',
    accuracyValidated=False)
MAX_TURNS = 20000
MAX_WINDOWS = 6000


def validate_turns(turns, duration):
    if not isinstance(turns, list) or not 0 < len(turns) <= MAX_TURNS:
        raise ValueError('声音分割片段无效或超过二次声纹处理上限。')
    for turn in turns:
        if not isinstance(turn, dict):
            raise ValueError('声音分割片段格式无效。')
        start, end, speaker = turn.get('start'), turn.get('end'), turn.get('speaker')
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration + 1
                or type(speaker) not in (str, int) or not str(speaker).strip() or len(str(speaker)) > 120):
            raise ValueError('声音分割时间或编号无效。')


def select_windows(turns):
    """Subtract overlapping voices without discarding the entire long turn."""
    result = []
    for index, turn in enumerate(turns):
        start, end = turn['start'] + PARAMETERS['margin'], turn['end'] - PARAMETERS['margin']
        if end - start < PARAMETERS['cleanIntervalMinimum']:
            continue
        intervals = [(start, end)]
        for other in turns:
            if other['speaker'] == turn['speaker'] or other['end'] <= start or other['start'] >= end:
                continue
            remaining = []
            a, b = other['start'] - PARAMETERS['overlapMargin'], other['end'] + PARAMETERS['overlapMargin']
            for left, right in intervals:
                if b <= left or a >= right:
                    remaining.append((left, right))
                else:
                    if a > left:
                        remaining.append((left, min(right, a)))
                    if b < right:
                        remaining.append((max(left, b), right))
            intervals = remaining
        clean = sorted((part for part in intervals if part[1] - part[0] >= PARAMETERS['cleanIntervalMinimum']),
                       key=lambda part: part[1] - part[0], reverse=True)
        if not clean:
            continue
        first = clean[0]
        selected = [first]
        if first[1] - first[0] >= PARAMETERS['secondWindowMinimum']:
            selected = [(first[0], first[0] + 8), (first[1] - 8, first[1])]
        elif len(clean) > 1 and abs(sum(clean[1]) / 2 - sum(first) / 2) >= 10:
            selected.append(clean[1])
        for slot, (left, right) in enumerate(selected):
            if right - left > PARAMETERS['maximumWindow']:
                middle = (left + right) / 2
                left, right = middle - 4, middle + 4
            result.append(dict(turnIndex=index, window=slot, start=left, end=right))
            if len(result) > MAX_WINDOWS:
                raise ValueError('整集干净声音窗口过多，未截断或发布部分说话人结果。')
    return result


def acoustic_groups(vectors, records):
    import numpy as np
    count = len(records)
    if not count:
        return [], [], []
    similarities = vectors @ vectors.T
    nearest = np.argsort(-similarities, axis=1, kind='stable')[:, :PARAMETERS['neighbors'] + 1]
    graph = np.zeros((count, count), dtype=bool)
    for index in range(count):
        neighbors = [int(j) for j in nearest[index] if j != index][:PARAMETERS['neighbors']]
        graph[index, neighbors] = similarities[index, neighbors] >= PARAMETERS['edgeSimilarity']
    graph &= graph.T
    components, seen = [], set()
    for index in range(count):
        if index in seen:
            continue
        seen.add(index)
        stack, group = [index], []
        while stack:
            current = stack.pop()
            group.append(current)
            for neighbor in np.flatnonzero(graph[current]):
                neighbor = int(neighbor)
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(group)
    components.sort(key=lambda group: -len(group))
    cores = [group for group in components if len(group) >= PARAMETERS['minCoreWindows']
             and len({records[i]['turnIndex'] for i in group}) >= PARAMETERS['minCoreTurns']
             and sum(records[i]['end'] - records[i]['start'] for i in group) >= PARAMETERS['minCoreAudioSeconds']]
    if not cores:
        return [], [None] * count, [len(group) for group in components]
    centers = np.stack([vectors[group].mean(0) for group in cores])
    centers /= np.maximum(np.linalg.norm(centers, axis=1, keepdims=True), 1e-12)
    scores = vectors @ centers.T
    assignments = []
    for index, record in enumerate(records):
        ranked = np.argsort(-scores[index])
        best = int(ranked[0])
        score = float(scores[index, best])
        second = float(scores[index, ranked[1]]) if len(ranked) > 1 else 0.0
        others = [j for j in cores[best] if records[j]['turnIndex'] != record['turnIndex']]
        nearest_score = float(np.median(np.sort(similarities[index, others])[-5:])) if len(others) >= 5 else 0.0
        short = record['end'] - record['start'] < PARAMETERS['shortWindowSeconds']
        accepted = (score >= PARAMETERS['shortCentroidSimilarity' if short else 'assignCentroidSimilarity']
                    and score - second >= PARAMETERS['shortMargin' if short else 'assignMargin']
                    and nearest_score >= PARAMETERS['shortNeighborMedian' if short else 'assignNeighborMedian'])
        assignments.append(best if accepted else None)
    return cores, assignments, [len(group) for group in components]


def assign_turns(turns, records, assignments):
    by_turn = defaultdict(list)
    for record, group in zip(records, assignments):
        by_turn[record['turnIndex']].append(group)
    result, reasons = [], Counter()
    for index, turn in enumerate(turns):
        observed = by_turn[index]
        labels = set(observed)
        known = bool(observed) and None not in labels and len(labels) == 1
        reason = ('no-long-clean-window' if not observed else 'weak-or-inconsistent-embedding' if not known else None)
        if reason:
            reasons[reason] += 1
        result.append(dict(start=turn['start'], end=turn['end'],
                           speaker=f'R{next(iter(labels))}' if known else f'U{index}',
                           unassigned=not known, originalSpeaker=turn['speaker']))
    overlaps = 0
    for index, turn in enumerate(result):
        if turn['unassigned']:
            continue
        for previous in range(index):
            other = result[previous]
            if other['unassigned'] or turn['speaker'] != other['speaker']:
                continue
            overlap = min(turn['end'], other['end']) - max(turn['start'], other['start'])
            if overlap > PARAMETERS['sameGroupOverlapSeconds']:
                dropped = previous if other['end'] - other['start'] <= turn['end'] - turn['start'] else index
                result[dropped].update(speaker=f'U{dropped}', unassigned=True)
                overlaps += 1
                if dropped == index:
                    break
    reasons['same-group-overlap-uncertain'] = overlaps
    return result, dict(reasons)


def refine(pcm, duration, turns, embedding_model, progress, check_cancel):
    import numpy as np
    import sherpa_onnx
    validate_turns(turns, duration)
    check_cancel()
    records = select_windows(turns)
    vectors = []
    audio = np.memmap(pcm, dtype='<f4', mode='r')
    try:
        if records:
            extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=embedding_model, num_threads=PARAMETERS['numThreads'], provider='cpu'))
        for index, record in enumerate(records):
            check_cancel()
            stream = extractor.create_stream()
            stream.accept_waveform(16000, audio[round(record['start'] * 16000):round(record['end'] * 16000)])
            stream.input_finished()
            if not extractor.is_ready(stream):
                raise ValueError('干净声音片段不足以提取声纹，未猜测归属。')
            vector = np.asarray(extractor.compute(stream), dtype=np.float32)
            norm = np.linalg.norm(vector)
            if not np.all(np.isfinite(vector)) or norm <= 0:
                raise ValueError('声纹模型返回无效向量，未发布部分结果。')
            vectors.append(vector / norm)
            if (index + 1) % 5 == 0 or index + 1 == len(records):
                progress(0.85 * (index + 1) / len(records))
    finally:
        del audio
    check_cancel()
    cores, assignments, components = acoustic_groups(np.asarray(vectors, dtype=np.float32), records)
    progress(0.95)
    refined, reasons = assign_turns(turns, records, assignments)
    check_cancel()
    return dict(turns=refined, refinement=dict(PARAMETERS),
                diagnostics=dict(windowCount=len(records), coreWindowCounts=[len(group) for group in cores],
                                 componentSizes=components, unknownReasons=reasons,
                                 assignedTurns=sum(not turn['unassigned'] for turn in refined),
                                 unknownTurns=sum(turn['unassigned'] for turn in refined)))
