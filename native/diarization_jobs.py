#!/usr/bin/env python3
"""Offline whole-episode speaker clustering, isolated from the native host."""
import argparse
import datetime
from importlib.metadata import PackageNotFoundError, version as distribution_version
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from transcription_jobs import atomic_json, read_json, lock_file, paths as source_paths

MODEL = 'sherpa-onnx/2.0.1/pyannote-segmentation-3.0-int8/3dspeaker-eres2net-zh'
PARAMS = dict(numClusters=-1, threshold=0.8, minSpeakerSeconds=3.0, minDurationOn=0.3, minDurationOff=0.5,
              windowShiftRatio=0.1, sampleRate=16000, provider='cpu', numThreads=2)
MAX_SECONDS = 28800
MAX_TURNS = 100000
FILES = ('speaker-job.json', 'speaker-config.json', 'speaker-worker.log',
         'speaker-cancel', 'speaker-worker.lock', 'speaker-control.lock', 'speaker-turns.json', 'refinement-turns.json')
RETRY_MESSAGE = '需重新运行；原稿、音频、模型和参数匹配时可复用已完成的声音分割，未完成声纹阶段重新计算。'


class JobError(ValueError):
    pass


class Cancelled(JobError):
    pass


def paths(config, episode_id):
    root, _, folder = source_paths(config, episode_id)
    lexical = root / '知识库/原始素材库/_attachments/xiaoyuzhou' / episode_id
    for parent in (lexical, *lexical.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise JobError('说话人识别目录包含符号链接，已拒绝读取或覆盖。')
    for name in FILES:
        if (folder / name).is_symlink():
            raise JobError('说话人任务文件是符号链接，已拒绝读取或覆盖。')
    return root, folder


def public_job(state):
    fields = ('status', 'phase', 'progress', 'message', 'error', 'speakerCount', 'sourceHash',
              'sourcePath', 'speakerPath', 'segmentCount', 'updatedAt', 'model')
    return {key: state[key] for key in fields if key in state}


def active(folder):
    with lock_file(folder / 'speaker-worker.lock', False) as held:
        return held is None


def status(config, episode_id):
    _, folder = paths(config, episode_id)
    state = read_json(folder / 'speaker-job.json', {})
    if not state:
        state = dict(status='idle', phase='idle', progress=0, message='按整集声音区分说话人，未知身份仅标编号。')
    elif (state.get('status') == 'running' and time.time() - state.get('heartbeat', 0) > 30
          and not active(folder)):
        state = dict(state, status='failed', phase='failed',
                     error='上次说话人识别进程已停止。' + RETRY_MESSAGE)
    return dict(success=True, job=public_job(state))


def require_tools(config):
    for key, label in (('diarizationPython', '说话人识别 Python'), ('segmentationModel', '声音分割模型'),
                       ('speakerEmbeddingModel', '说话人特征模型'), ('ffmpeg', 'FFmpeg')):
        value = config.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).is_file():
            raise JobError(f'尚未配置本地{label}。请先完成本机安装；识别不会自动下载工具或模型。')
        if key.endswith('Model') and Path(value).is_symlink():
            raise JobError('说话人模型路径是符号链接，请配置已核验的本地模型文件。')
    if not os.access(config['diarizationPython'], os.X_OK) or not os.access(config['ffmpeg'], os.X_OK):
        raise JobError('已配置的本地 Python 或 FFmpeg 不可执行。')


def start(config, episode_id):
    root, folder = paths(config, episode_id)
    with lock_file(folder / 'speaker-control.lock'):
        current = status(config, episode_id)
        if current['job']['status'] == 'running' or active(folder):
            return current
        require_tools(config)
        if not (folder / 'source.mp3').is_file() or not (folder / 'transcribe-segments.json').is_file():
            raise JobError('请先完成本集整集转写；说话人识别需要已有音频及 ASR 时间检查点。')
        cancel_path = folder / 'speaker-cancel'
        if cancel_path.exists():
            cancel_path.unlink()
        state = dict(status='running', phase='queued', progress=0, heartbeat=time.time(), model=MODEL,
                     message='已排队；关闭侧栏后继续。' + RETRY_MESSAGE)
        atomic_json(folder / 'speaker-job.json', state)
        # Only scoped paths; no API keys, prompts, transcript or cloud credentials.
        snapshot = {key: config[key] for key in ('diarizationPython', 'segmentationModel', 'speakerEmbeddingModel', 'ffmpeg')}
        snapshot['vault'] = str(root)
        atomic_json(folder / 'speaker-config.json', snapshot)
        try:
            with (folder / 'speaker-worker.log').open('ab') as log:
                subprocess.Popen([config['diarizationPython'], str(Path(__file__).resolve()), '--worker',
                                  str(folder / 'speaker-config.json'), episode_id], stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True, close_fds=True)
        except OSError as error:
            state.update(status='failed', phase='failed', error='无法启动本地说话人识别进程。')
            atomic_json(folder / 'speaker-job.json', state)
            raise JobError(state['error']) from error
        return dict(success=True, job=public_job(state))


def cancel(config, episode_id):
    _, folder = paths(config, episode_id)
    with lock_file(folder / 'speaker-control.lock'):
        result = status(config, episode_id)
        if result['job']['status'] == 'running':
            (folder / 'speaker-cancel').write_text('cancel\n', encoding='utf-8')
            result['job']['message'] = '正在停止本地任务；已有文稿保留。' + RETRY_MESSAGE
        return result


def check_cancel(folder):
    if (folder / 'speaker-cancel').exists():
        raise Cancelled('已取消；原稿及已有说话人版均保留。' + RETRY_MESSAGE)


def decode(config, audio, output, duration, folder):
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise JobError('音频时长无效或超过 8 小时。')
    command = [config['ffmpeg'], '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-protocol_whitelist', 'file,pipe', '-i', str(audio), '-vn', '-map', '0:a:0',
               '-ac', '1', '-ar', '16000', '-c:a', 'pcm_f32le', '-f', 'f32le', '-n', str(output)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 900
    try:
        while process.poll() is None:
            check_cancel(folder)
            if time.monotonic() > deadline:
                raise JobError('本地音频解码超时，未生成说话人结果。')
            if output.exists() and output.stat().st_size > (MAX_SECONDS + 2) * 16000 * 4:
                raise JobError('解码音频超过时长上限。')
            time.sleep(0.2)
        if process.returncode:
            raise JobError('本地音频解码失败，未重新下载音频。')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    check_cancel(folder)
    size = output.stat().st_size
    decoded = size / 4 / 16000
    if not size or size % 4 or abs(decoded - duration) > max(2, min(10, duration * 0.001)):
        raise JobError('解码音频长度与整集检查点不符，未发布不完整的说话人版。')


def require_engine_version():
    try:
        actual_version = distribution_version('sherpa-onnx')
    except PackageNotFoundError as error:
        raise JobError('隔离环境未安装已核验的 sherpa-onnx 2.0.1；没有自动安装。') from error
    if actual_version != '2.0.1':
        raise JobError(f'本地 sherpa-onnx 版本为 {actual_version[:80]}，与已核验的 2.0.1 不符；未错误标注模型来源。')


def recognize(config, pcm, duration, folder, progress):
    # Imports happen only in the explicitly configured isolated worker Python.
    require_engine_version()
    try:
        import numpy as np
        import sherpa_onnx as sherpa
    except ImportError as error:
        raise JobError('隔离环境缺少 sherpa-onnx 或 numpy；没有自动安装或下载。') from error
    configuration = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=config['segmentationModel'], window_shift_ratio=PARAMS['windowShiftRatio']),
            num_threads=PARAMS['numThreads'], provider='cpu'),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=config['speakerEmbeddingModel'],
                                                        num_threads=PARAMS['numThreads'], provider='cpu'),
        clustering=sherpa.FastClusteringConfig(num_clusters=-1, threshold=PARAMS['threshold']),
        min_duration_on=PARAMS['minDurationOn'], min_duration_off=PARAMS['minDurationOff'])
    if not configuration.validate():
        raise JobError('本地声音模型配置无效。')
    diarizer = sherpa.OfflineSpeakerDiarization(configuration)
    if diarizer.sample_rate != 16000:
        raise JobError('声音模型采样率与整集音频准备参数不一致。')
    check_cancel(folder)
    audio = np.memmap(pcm, dtype='<f4', mode='r')
    last_update = 0.0
    def callback(processed, total):
        nonlocal last_update
        check_cancel(folder)
        now = time.monotonic()
        if total > 0 and (now - last_update >= 1 or processed >= total):
            progress(min(1, max(0, processed / total)))
            last_update = now
        # Sherpa ignores the return value; cancellation raises above.
        return 0
    try:
        result = diarizer.process(audio, callback=callback).sort_by_start_time()
        check_cancel(folder)
        turns = []
        for turn in result:
            if len(turns) >= MAX_TURNS:
                raise JobError('说话人声段过多，未截断或发布部分结果。')
            start, end = float(turn.start), float(turn.end)
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration + 1:
                raise JobError('声音模型返回了无效时间范围。')
            turns.append(dict(start=start, end=min(duration, end), speaker=int(turn.speaker)))
        if not turns:
            raise JobError('未识别到有效声音片段，未猜测说话人。')
        return turns
    finally:
        del audio


def terminate_child(process):
    """Only terminate the process group created by this supervisor."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def supervised_stage(config, pcm, duration, folder, progress, stage='baseline', raw_turns=None):
    """Keep cancellation responsive even when C++ holds the GIL without callbacks."""
    check_cancel(folder)
    working = pcm.parent
    for name in ('recognizer-progress.json', 'recognizer-result.json'):
        previous = working / name
        if previous.exists():
            previous.unlink()
    atomic_json(working / 'recognizer-input.json', dict(duration=duration, stage=stage, turns=raw_turns))
    process = subprocess.Popen([config['diarizationPython'], str(Path(__file__).resolve()),
                               '--recognizer', str(folder / 'speaker-config.json'),
                               '--work-dir', str(working), folder.name],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    last_progress = -1
    deadline = time.monotonic() + 24 * 60 * 60
    try:
        while process.poll() is None:
            check_cancel(folder)
            if time.monotonic() > deadline:
                raise JobError('本地说话人识别超过 24 小时，已停止，未发布部分结果。')
            current = read_json(working / 'recognizer-progress.json', {})
            value = current.get('progress') if isinstance(current, dict) else None
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 and value > last_progress:
                progress(value)
                last_progress = value
            time.sleep(0.2)
        check_cancel(folder)
        result = read_json(working / 'recognizer-result.json', {})
        if process.returncode or not isinstance(result, dict) or not result.get('success'):
            error = result.get('error') if isinstance(result, dict) else None
            raise JobError(str(error or '本地声音识别子进程已退出，未发布部分结果。')[:1000])
        turns = result.get('turns')
        if not isinstance(turns, list) or not 0 < len(turns) <= MAX_TURNS:
            raise JobError('声音识别子进程结果无效或过大。')
        return result
    finally:
        terminate_child(process)


def supervised_recognize(config, pcm, duration, folder, progress):
    return supervised_stage(config, pcm, duration, folder, progress)['turns']


def supervised_refine(config, pcm, duration, folder, progress, raw_turns):
    return supervised_stage(config, pcm, duration, folder, progress, 'refinement', raw_turns)


def recognizer_worker(config, episode_id, working):
    _, folder = paths(config, episode_id)
    # Only the supervisor-created temporary directory for this episode is used.
    if (not working.is_absolute() or working.is_symlink()
            or working.resolve().parent != Path(tempfile.gettempdir()).resolve()
            or not working.name.startswith('digest-speaker-' + episode_id + '-') or not working.is_dir()):
        raise JobError('声音识别临时目录无效。')
    for name in ('audio.f32', 'recognizer-input.json', 'recognizer-progress.json', 'recognizer-result.json'):
        if (working / name).is_symlink():
            raise JobError('声音识别临时文件包含符号链接。')
    result_path = working / 'recognizer-result.json'
    try:
        require_tools(config)
        request = read_json(working / 'recognizer-input.json')
        duration = request.get('duration') if isinstance(request, dict) else None
        pcm = working / 'audio.f32'
        if (type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS
                or not pcm.is_file() or pcm.stat().st_size > (MAX_SECONDS + 2) * 16000 * 4
                or abs(pcm.stat().st_size / 64000 - duration) > max(2, min(10, duration * 0.001))):
            raise JobError('声音识别临时音频长度无效。')
        require_engine_version()
        progress = lambda value: atomic_json(working / 'recognizer-progress.json', dict(progress=value))
        stage = request.get('stage', 'baseline')
        if stage == 'refinement':
            import speaker_refinement
            result = speaker_refinement.refine(pcm, duration, request.get('turns'), config['speakerEmbeddingModel'],
                                                progress, lambda: check_cancel(folder))
        elif stage == 'baseline':
            result = dict(turns=recognize(config, pcm, duration, folder, progress))
        else:
            raise JobError('声音识别阶段无效。')
        check_cancel(folder)
        atomic_json(result_path, dict(success=True, **result))
        return 0
    except Exception as error:
        atomic_json(result_path, dict(success=False, error=str(error)[:1000]))
        return 1


def cached_raw_turns(folder, metadata, duration):
    cached = read_json(folder / 'speaker-turns.json', {})
    if not isinstance(cached, dict) or cached.get('metadata') != metadata:
        return None
    from speaker_refinement import validate_turns
    turns = cached.get('turns')
    validate_turns(turns, duration)
    # A refined checkpoint is not a raw-segmentation cache.
    if any('unassigned' in turn or 'originalSpeaker' in turn for turn in turns):
        return None
    return turns


def worker(config, episode_id):
    _, folder = paths(config, episode_id)
    with lock_file(folder / 'speaker-worker.lock', False) as held:
        if held is None:
            return
        state = read_json(folder / 'speaker-job.json', {})
        def update(**fields):
            state.update(fields, heartbeat=time.time(), updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
            atomic_json(folder / 'speaker-job.json', state)
        try:
            check_cancel(folder)
            require_tools(config)
            update(status='running', phase='validating', progress=0.01, model=MODEL,
                   message='正在核验已有原稿、音频和完整 ASR 时间检查点。')
            import speaker_data
            from setup_diarization import sha256
            inputs = speaker_data.load_inputs(config, episode_id)
            metadata = {key: inputs[key] for key in ('sourceHash', 'audioHash', 'checkpointHash')}
            metadata.update(model=MODEL, params={**PARAMS,
                            'segmentationSha256': sha256(config['segmentationModel']),
                            'embeddingSha256': sha256(config['speakerEmbeddingModel'])})
            raw_turns = cached_raw_turns(folder, metadata, inputs['duration'])
            update(sourceHash=inputs['sourceHash'], sourcePath=inputs['sourcePath'], phase='decoding',
                   progress=0.03, message='正在本地准备整集音频；不会上传或重新转写。')
            check_cancel(folder)
            with tempfile.TemporaryDirectory(prefix='digest-speaker-' + episode_id + '-') as temporary:
                pcm = Path(temporary) / 'audio.f32'
                decode(config, inputs['audioPath'], pcm, inputs['duration'], folder)
                if raw_turns is None:
                    update(phase='recognizing', progress=0.08, message='正在完成整集声音分割，随后进行跨整集声纹归并。')
                    raw_turns = supervised_recognize(config, pcm, inputs['duration'], folder,
                        lambda fraction: update(phase='recognizing', progress=0.08 + 0.55 * fraction,
                                                message='正在处理声音片段，完成后将保留分割检查点。'))
                    check_cancel(folder)
                    atomic_json(folder / 'speaker-turns.json', dict(metadata=metadata, turns=raw_turns))
                update(phase='refining', progress=0.65,
                       message='声音分割已就绪；正在提取干净长声段的声纹，保守归并跨整集编号。')
                refined = supervised_refine(config, pcm, inputs['duration'], folder,
                    lambda fraction: update(phase='refining', progress=0.65 + 0.28 * fraction,
                                            message='正在二次声纹归并；短声、重叠及证据不足的片段保持不确定。'), raw_turns)
            check_cancel(folder)
            turns = refined['turns']
            metadata = {**metadata, 'params': {**metadata['params'], 'refinement': refined['refinement']}}
            # Raw segmentation stays unchanged; refined inputs remain separately reviewable.
            atomic_json(folder / 'refinement-turns.json', dict(metadata=metadata, turns=turns,
                                                              diagnostics=refined.get('diagnostics', {})))
            update(phase='publishing', progress=0.95, message='正在核对原稿版本并归档独立说话人版。')
            check_cancel(folder)
            result = speaker_data.publish(config, episode_id, turns, metadata)
            update(**{key: value for key, value in result.items() if key != 'success'}, status='completed',
                   phase='completed', progress=1, message='说话人版已归档。编号仅区分声音，不代表身份；证据不足的声段仍待确认，未经人工准确率核验。')
        except Cancelled as error:
            update(status='cancelled', phase='cancelled', message=str(error))
        except Exception as error:
            update(status='failed', phase='failed', error=str(error)[:1000],
                   message='识别未完成，原稿及已有文稿保留。' + RETRY_MESSAGE)


def handle(config, message):
    action = message.get('action')
    episode_id = message.get('episodeId', '')
    if action == 'speakerStart':
        return start(config, episode_id)
    if action == 'speakerStatus':
        return status(config, episode_id)
    if action == 'speakerCancel':
        return cancel(config, episode_id)
    raise JobError('不支持的说话人任务操作。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--worker', type=Path)
    mode.add_argument('--recognizer', type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('episode_id')
    args = parser.parse_args()
    configuration = args.worker or args.recognizer
    config = read_json(configuration)
    _, folder = paths(config, args.episode_id)
    if configuration.resolve() != folder / 'speaker-config.json':
        parser.error('worker configuration must belong to this episode')
    if args.recognizer:
        if args.work_dir is None:
            parser.error('recognizer requires its supervisor temporary directory')
        return recognizer_worker(config, args.episode_id, args.work_dir)
    def stopped(signum, frame):
        # Unwind the supervisor so its finally block reaps the owned child.
        raise Cancelled('本地任务已停止；已有文稿保留。' + RETRY_MESSAGE)
    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGINT, stopped)
    worker(config, args.episode_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
