import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

NATIVE = Path(__file__).parents[1] / 'native'
sys.path.insert(0, str(NATIVE))
import diarization_jobs as jobs
import setup_diarization as setup

ID = 'b' * 24


class DiarizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        version = mock.patch.object(jobs, 'distribution_version', return_value='2.0.1')
        version.start()
        self.addCleanup(version.stop)
        self.real_refine = jobs.supervised_refine
        refinement = mock.patch.object(jobs, 'supervised_refine', side_effect=lambda config, pcm, duration, folder, progress, turns:
            dict(turns=[dict(turn, speaker='R0', originalSpeaker=turn['speaker'], unassigned=False) for turn in turns],
                 refinement=dict(method='test-acoustic-refinement'), diagnostics={}))
        self.refine_mock = refinement.start()
        self.addCleanup(refinement.stop)
        self.vault = self.root / 'vault'
        self.vault.mkdir()
        self.config = dict(vault=str(self.vault))
        for key in ('diarizationPython', 'ffmpeg', 'segmentationModel', 'speakerEmbeddingModel'):
            file = self.root / key
            file.write_bytes(b'fixture')
            file.chmod(0o700)
            self.config[key] = str(file)
        _, self.folder = jobs.paths(self.config, ID)
        self.folder.mkdir(parents=True)
        (self.folder / 'source.mp3').write_bytes(b'audio')
        (self.folder / 'transcribe-segments.json').write_text('{}')

    def test_unconfigured_dependencies_do_not_spawn_or_download(self):
        with mock.patch.object(jobs.subprocess, 'Popen') as spawn, self.assertRaises(jobs.JobError):
            jobs.start(dict(vault=str(self.vault)), ID)
        spawn.assert_not_called()
        self.assertFalse((self.folder / 'speaker-job.json').exists())

    def test_invalid_episode_and_symlink_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            jobs.paths(self.config, '../escape')
        (self.folder / 'speaker-job.json').symlink_to(self.root / 'private.json')
        with self.assertRaises(jobs.JobError):
            jobs.paths(self.config, ID)

    def test_start_is_short_scoped_and_idempotent_while_queued(self):
        with mock.patch.object(jobs.subprocess, 'Popen') as spawn:
            first = jobs.start({**self.config, 'apiKey': 'private-test-key'}, ID)
            second = jobs.start(self.config, ID)
        self.assertEqual(first['job']['status'], 'running')
        self.assertEqual(first, second)
        self.assertEqual(spawn.call_count, 1)
        command = spawn.call_args.args[0]
        self.assertEqual(command[0], self.config['diarizationPython'])
        snapshot = json.loads((self.folder / 'speaker-config.json').read_text())
        self.assertNotIn('apiKey', snapshot)
        self.assertEqual(snapshot['vault'], str(self.vault.resolve()))

    def test_cancel_flag_explains_restart_and_keeps_outputs(self):
        previous = self.folder / 'speaker-turns.json'
        previous.write_text('old-output')
        with mock.patch.object(jobs.subprocess, 'Popen'):
            jobs.start(self.config, ID)
        result = jobs.cancel(self.config, ID)
        self.assertIn('可复用', result['job']['message'])
        with self.assertRaises(jobs.Cancelled):
            jobs.check_cancel(self.folder)
        self.assertEqual(previous.read_text(), 'old-output')

    def test_interrupted_worker_reports_failure_without_resume_claim(self):
        jobs.atomic_json(self.folder / 'speaker-job.json', dict(status='running', heartbeat=time.time() - 40))
        state = jobs.status(self.config, ID)['job']
        self.assertEqual(state['status'], 'failed')
        self.assertIn('可复用', state['error'])

    def test_held_worker_lock_prevents_duplicate_spawn(self):
        jobs.atomic_json(self.folder / 'speaker-job.json', dict(status='running', heartbeat=0))
        with jobs.lock_file(self.folder / 'speaker-worker.lock'), mock.patch.object(jobs.subprocess, 'Popen') as spawn:
            self.assertEqual(jobs.start(self.config, ID)['job']['status'], 'running')
        spawn.assert_not_called()

    def test_decode_refuses_partial_audio(self):
        pcm = self.folder / 'short.f32'
        pcm.write_bytes(b'\0' * 64000)
        process = mock.Mock(returncode=0)
        process.poll.return_value = 0
        with mock.patch.object(jobs.subprocess, 'Popen', return_value=process), self.assertRaises(jobs.JobError):
            jobs.decode(self.config, self.folder / 'source.mp3', pcm, 600, self.folder)

    def test_decode_cancellation_terminates_only_its_child(self):
        (self.folder / 'speaker-cancel').write_text('cancel')
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(jobs.subprocess, 'Popen', return_value=process), self.assertRaises(jobs.Cancelled):
            jobs.decode(self.config, self.folder / 'source.mp3', self.folder / 'pcm', 600, self.folder)
        process.terminate.assert_called_once()

    def engine(self, callback_action=None):
        config_class = mock.Mock(return_value=types.SimpleNamespace(validate=lambda: True))
        result = types.SimpleNamespace(sort_by_start_time=lambda: [types.SimpleNamespace(start=0, end=1, speaker=7)])
        def process(audio, callback):
            if callback_action:
                callback_action()
            callback(1, 2)
            return result
        diarizer = types.SimpleNamespace(sample_rate=16000, process=process)
        sherpa = types.SimpleNamespace(OfflineSpeakerDiarizationConfig=config_class,
            OfflineSpeakerSegmentationModelConfig=mock.Mock(), OfflineSpeakerSegmentationPyannoteModelConfig=mock.Mock(),
            SpeakerEmbeddingExtractorConfig=mock.Mock(), FastClusteringConfig=mock.Mock(),
            OfflineSpeakerDiarization=mock.Mock(return_value=diarizer))
        numpy = types.SimpleNamespace(memmap=mock.Mock(return_value='mapped-audio'))
        return sherpa, numpy

    def test_recognize_uses_memmap_and_automatic_global_cluster_count(self):
        sherpa, numpy = self.engine()
        progress = mock.Mock()
        with mock.patch.dict(sys.modules, {'sherpa_onnx': sherpa, 'numpy': numpy}):
            turns = jobs.recognize(self.config, self.folder / 'pcm', 600, self.folder, progress)
        self.assertEqual(turns, [dict(start=0.0, end=1.0, speaker=7)])
        numpy.memmap.assert_called_once_with(self.folder / 'pcm', dtype='<f4', mode='r')
        sherpa.FastClusteringConfig.assert_called_once_with(num_clusters=-1, threshold=0.8)
        progress.assert_called_once_with(0.5)

    def test_callback_raises_for_cancel_instead_of_ignored_return_value(self):
        sherpa, numpy = self.engine(lambda: (self.folder / 'speaker-cancel').write_text('cancel'))
        with mock.patch.dict(sys.modules, {'sherpa_onnx': sherpa, 'numpy': numpy}), self.assertRaises(jobs.Cancelled):
            jobs.recognize(self.config, self.folder / 'pcm', 600, self.folder, mock.Mock())

    def test_unverified_sherpa_version_is_not_mislabeled_as_pinned_version(self):
        with mock.patch.object(jobs, 'distribution_version', return_value='1.12.0'), self.assertRaisesRegex(jobs.JobError, '1.12.0'):
            jobs.recognize(self.config, self.folder / 'pcm', 600, self.folder, mock.Mock())

    def test_worker_publishes_only_original_binding_and_keeps_raw_files(self):
        inputs = dict(sourceHash='source', audioHash='audio', checkpointHash='checkpoint',
                      sourcePath='source.md', audioPath=str(self.folder / 'source.mp3'), duration=600)
        data = types.SimpleNamespace(load_inputs=mock.Mock(return_value=inputs), publish=mock.Mock(return_value=dict(success=True, speakerCount=2)))
        turns = [dict(start=0, end=1, speaker=3)]
        with mock.patch.dict(sys.modules, {'speaker_data': data}), mock.patch.object(jobs, 'decode'), mock.patch.object(jobs, 'supervised_recognize', return_value=turns):
            jobs.worker(self.config, ID)
        binding = data.publish.call_args.args[3]
        self.assertEqual(binding['sourceHash'], 'source')
        self.assertEqual(binding['checkpointHash'], 'checkpoint')
        self.assertEqual(binding['params']['numClusters'], -1)
        self.assertEqual(jobs.status(self.config, ID)['job']['status'], 'completed')
        self.assertEqual((self.folder / 'source.mp3').read_bytes(), b'audio')
        self.assertEqual((self.folder / 'transcribe-segments.json').read_text(), '{}')
        self.assertFalse(any(self.folder.glob('.speaker-pcm-*')))

    def test_changed_source_failure_keeps_computed_turns_without_publishing(self):
        inputs = dict(sourceHash='old', audioHash='audio', checkpointHash='checkpoint',
                      sourcePath='source.md', audioPath='audio.mp3', duration=600)
        data = types.SimpleNamespace(load_inputs=mock.Mock(return_value=inputs), publish=mock.Mock(side_effect=ValueError('原稿已变更')))
        with mock.patch.dict(sys.modules, {'speaker_data': data}), mock.patch.object(jobs, 'decode'), mock.patch.object(jobs, 'supervised_recognize', return_value=[dict(start=0, end=1, speaker=0)]):
            jobs.worker(self.config, ID)
        self.assertEqual(jobs.status(self.config, ID)['job']['status'], 'failed')
        self.assertTrue((self.folder / 'speaker-turns.json').is_file())

    def test_cancelled_worker_preserves_previous_turns_and_never_publishes(self):
        previous = self.folder / 'speaker-turns.json'
        previous.write_text('{"metadata":{},"turns":[]}')
        inputs = dict(sourceHash='old', audioHash='audio', checkpointHash='checkpoint',
                      sourcePath='source.md', audioPath='audio.mp3', duration=600)
        data = types.SimpleNamespace(load_inputs=mock.Mock(return_value=inputs), publish=mock.Mock())
        with mock.patch.dict(sys.modules, {'speaker_data': data}), mock.patch.object(jobs, 'decode'), mock.patch.object(jobs, 'supervised_recognize', side_effect=jobs.Cancelled('从头重新识别')):
            jobs.worker(self.config, ID)
        data.publish.assert_not_called()
        self.assertEqual(jobs.status(self.config, ID)['job']['status'], 'cancelled')
        self.assertEqual(previous.read_text(), '{"metadata":{},"turns":[]}')

    def test_cancel_terminates_real_child_with_no_callbacks_and_does_not_publish(self):
        # A sleeping child emulates C++ holding the GIL without invoking Python.
        real_popen = subprocess.Popen
        child, started = None, None
        inputs = dict(sourceHash='old', audioHash='audio', checkpointHash='checkpoint',
                      sourcePath='source.md', audioPath='audio.mp3', duration=600)
        data = types.SimpleNamespace(load_inputs=mock.Mock(return_value=inputs), publish=mock.Mock())
        timer = threading.Timer(0.15, lambda: (self.folder / 'speaker-cancel').write_text('cancel'))
        self.addCleanup(timer.cancel)
        def spawn_owned_child(*args, **kwargs):
            nonlocal child, started
            child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                               start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(lambda: jobs.terminate_child(child))
            started = time.monotonic()
            timer.start()
            return child
        with mock.patch.dict(sys.modules, {'speaker_data': data}), mock.patch.object(jobs, 'decode'), mock.patch.object(jobs.subprocess, 'Popen', side_effect=spawn_owned_child) as spawn:
            jobs.worker(self.config, ID)
        spawn.assert_called_once()
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNotNone(child.poll())
        self.assertEqual(jobs.status(self.config, ID)['job']['status'], 'cancelled')
        data.publish.assert_not_called()

    def test_unresponsive_child_gets_term_then_kill_only_for_owned_group(self):
        child = mock.Mock(pid=987654)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('recognizer', 3), -9]
        with mock.patch.object(jobs.os, 'killpg') as kill:
            jobs.terminate_child(child)
        self.assertEqual(kill.call_args_list, [mock.call(child.pid, jobs.signal.SIGTERM), mock.call(child.pid, jobs.signal.SIGKILL)])

    def test_recognizer_mode_rejects_unscoped_temporary_directory(self):
        with self.assertRaises(jobs.JobError):
            jobs.recognizer_worker(self.config, ID, self.root)

    def test_recognizer_accepts_only_system_temp_prefix_for_its_episode(self):
        with tempfile.TemporaryDirectory(prefix='digest-speaker-' + ID + '-') as temporary:
            working = Path(temporary)
            (working / 'audio.f32').write_bytes(b'\0' * 64000)
            jobs.atomic_json(working / 'recognizer-input.json', dict(duration=1, stage='baseline'))
            with mock.patch.object(jobs, 'recognize', return_value=[dict(start=0, end=1, speaker=0)]):
                self.assertEqual(jobs.recognizer_worker(self.config, ID, working), 0)
            self.assertTrue((working / 'recognizer-result.json').is_file())
            with self.assertRaises(jobs.JobError):
                jobs.recognizer_worker(self.config, 'c' * 24, working)
        with tempfile.TemporaryDirectory(prefix='wrong-speaker-prefix-') as temporary:
            with self.assertRaises(jobs.JobError):
                jobs.recognizer_worker(self.config, ID, Path(temporary))

    def test_raw_cache_requires_all_source_model_and_parameter_bindings(self):
        metadata = dict(sourceHash='source', audioHash='audio', checkpointHash='checkpoint', model=jobs.MODEL,
                        params={**jobs.PARAMS, 'embeddingSha256': 'modelhash', 'segmentationSha256': 'segmentation'})
        turns = [dict(start=0, end=10, speaker=0)]
        jobs.atomic_json(self.folder / 'speaker-turns.json', dict(metadata=metadata, turns=turns))
        self.assertEqual(jobs.cached_raw_turns(self.folder, metadata, 600), turns)
        for key in ('sourceHash', 'audioHash', 'checkpointHash', 'model', 'params'):
            changed = {**metadata, key: {'changed': True} if key == 'params' else 'changed'}
            self.assertIsNone(jobs.cached_raw_turns(self.folder, changed, 600))
        jobs.atomic_json(self.folder / 'speaker-turns.json', dict(metadata=metadata,
                         turns=[dict(turns[0], unassigned=False, originalSpeaker=0)]))
        self.assertIsNone(jobs.cached_raw_turns(self.folder, metadata, 600))

    def test_worker_reuses_matching_raw_cache_and_writes_refined_checkpoint(self):
        inputs = dict(sourceHash='source', audioHash='audio', checkpointHash='checkpoint',
                      sourcePath='source.md', audioPath='audio.mp3', duration=600)
        metadata = {key: inputs[key] for key in ('sourceHash', 'audioHash', 'checkpointHash')}
        metadata.update(model=jobs.MODEL, params={**jobs.PARAMS,
                        'embeddingSha256': setup.sha256(self.config['speakerEmbeddingModel']),
                        'segmentationSha256': setup.sha256(self.config['segmentationModel'])})
        jobs.atomic_json(self.folder / 'speaker-turns.json', dict(metadata=metadata, turns=[dict(start=0, end=10, speaker=2)]))
        before = (self.folder / 'speaker-turns.json').read_bytes()
        data = types.SimpleNamespace(load_inputs=mock.Mock(return_value=inputs), publish=mock.Mock(return_value=dict(success=True, speakerCount=1)))
        with mock.patch.dict(sys.modules, {'speaker_data': data}), mock.patch.object(jobs, 'decode'), mock.patch.object(jobs, 'supervised_recognize') as baseline:
            jobs.worker(self.config, ID)
        baseline.assert_not_called()
        self.refine_mock.assert_called_once()
        self.assertEqual((self.folder / 'speaker-turns.json').read_bytes(), before)
        refined = json.loads((self.folder / 'refinement-turns.json').read_text())
        self.assertEqual(refined['turns'][0]['speaker'], 'R0')
        self.assertEqual(refined['turns'][0]['originalSpeaker'], 2)
        self.assertIn('refinement', refined['metadata']['params'])
        self.assertEqual(data.publish.call_args.args[2], refined['turns'])

    def test_cancel_refinement_after_cache_reuse_preserves_raw_and_never_publishes(self):
        inputs = dict(sourceHash='source', audioHash='audio', checkpointHash='checkpoint',
                      sourcePath='source.md', audioPath='audio.mp3', duration=600)
        metadata = {key: inputs[key] for key in ('sourceHash', 'audioHash', 'checkpointHash')}
        metadata.update(model=jobs.MODEL, params={**jobs.PARAMS,
                        'embeddingSha256': setup.sha256(self.config['speakerEmbeddingModel']),
                        'segmentationSha256': setup.sha256(self.config['segmentationModel'])})
        jobs.atomic_json(self.folder / 'speaker-turns.json', dict(metadata=metadata, turns=[dict(start=0, end=10, speaker=2)]))
        before = (self.folder / 'speaker-turns.json').read_bytes()
        real_popen = subprocess.Popen
        child = None
        timer = threading.Timer(0.15, lambda: (self.folder / 'speaker-cancel').write_text('cancel'))
        self.addCleanup(timer.cancel)
        def spawn_owned_child(*args, **kwargs):
            nonlocal child
            child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                               start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(lambda: jobs.terminate_child(child))
            timer.start()
            return child
        data = types.SimpleNamespace(load_inputs=mock.Mock(return_value=inputs), publish=mock.Mock())
        with mock.patch.dict(sys.modules, {'speaker_data': data}), mock.patch.object(jobs, 'decode'), mock.patch.object(jobs, 'supervised_refine', side_effect=self.real_refine), mock.patch.object(jobs, 'supervised_recognize') as baseline, mock.patch.object(jobs.subprocess, 'Popen', side_effect=spawn_owned_child) as spawn:
            jobs.worker(self.config, ID)
        spawn.assert_called_once()
        self.assertIsNotNone(child.poll())
        baseline.assert_not_called()
        data.publish.assert_not_called()
        self.assertEqual((self.folder / 'speaker-turns.json').read_bytes(), before)
        self.assertFalse((self.folder / 'refinement-turns.json').exists())
        self.assertEqual(jobs.status(self.config, ID)['job']['status'], 'cancelled')


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_check_and_dryrun_never_write_or_access_network(self):
        state = self.root / 'absent'
        with mock.patch.object(setup.urllib.request, 'build_opener') as network, mock.patch.object(setup.subprocess, 'run') as run:
            self.assertFalse(setup.check(state)['installed'])
            plan = setup.plan(state)
        network.assert_not_called()
        run.assert_not_called()
        self.assertFalse(state.exists())
        self.assertEqual(plan['downloadBytes'], 52740142)
        self.assertEqual(len(plan['artifacts']), 4)
        self.assertFalse(plan['changesGlobalPython'])

    def test_rejects_unreviewed_download_redirects(self):
        for url in ('http://huggingface.co/a', 'https://localhost/a', 'https://huggingface.co.evil.test/a',
                    'https://huggingface.co:8443/a', 'https://user:pass@huggingface.co/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                setup.safe_url(url)

    def test_official_huggingface_cdn_requires_exact_https_hostname(self):
        url = 'https://us.aws.cdn.hf.co/model.onnx?download=true'
        self.assertEqual(setup.safe_url(url), url)
        request = setup.urllib.request.Request('https://huggingface.co/model')
        redirected = setup.SafeRedirect().redirect_request(request, None, 302, 'Found', {}, url)
        self.assertEqual(redirected.full_url, url)
        for bad in ('http://us.aws.cdn.hf.co/model', 'https://us.aws.cdn.hf.co.evil.test/model',
                    'https://nested.us.aws.cdn.hf.co/model', 'https://us-aws.cdn.hf.co/model',
                    'https://eu.aws.cdn.hf.co/model', 'https://us.aws.cdn.hf.co:8443/model',
                    'https://user:password@us.aws.cdn.hf.co/model'):
            with self.subTest(url=bad), self.assertRaises(setup.SetupError):
                setup.SafeRedirect().redirect_request(request, None, 302, 'Found', {}, bad)

    def test_download_requires_exact_size_and_hash_and_cleans_own_partial(self):
        item = dict(url='https://huggingface.co/test', size=3, sha256=hashlib.sha256(b'abc').hexdigest())
        destination = self.root / 'model'
        for content in (b'ab', b'abd', b'abcd'):
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(content)
            with mock.patch.object(setup.urllib.request, 'build_opener', return_value=opener), self.assertRaises(setup.SetupError):
                setup.download(item, destination)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix('.part').exists())

    def test_download_preserves_preexisting_partial_file(self):
        destination = self.root / 'model'
        partial = destination.with_suffix('.part')
        partial.write_bytes(b'earlier-data')
        with mock.patch.object(setup.urllib.request, 'build_opener'), self.assertRaises(FileExistsError):
            setup.download(dict(url='https://huggingface.co/test'), destination)
        self.assertEqual(partial.read_bytes(), b'earlier-data')

    def test_existing_install_only_updates_three_fields(self):
        previous = dict(vault='/fixture/vault', ffmpeg='/fixture/ffmpeg', codex='/fixture/codex', allowFakeIpDns=True)
        (self.root / 'obsidian.json').write_text(json.dumps(previous))
        with mock.patch.object(setup.sys, 'platform', 'darwin'), mock.patch.object(setup.sys, 'version_info', (3, 14)), mock.patch.object(setup.platform, 'machine', return_value='arm64'), mock.patch.object(setup.importlib.util, 'find_spec', return_value=object()), mock.patch.object(setup, 'check', return_value=dict(installed=True)), mock.patch.object(setup, 'download') as download:
            setup.install(self.root)
        download.assert_not_called()
        updated = json.loads((self.root / 'obsidian.json').read_text())
        self.assertEqual({key: updated[key] for key in previous}, previous)
        self.assertEqual(set(updated) - set(previous), {'diarizationPython', 'segmentationModel', 'speakerEmbeddingModel'})

    def test_final_merge_rereads_settings_changed_during_check(self):
        config = self.root / 'obsidian.json'
        config.write_text(json.dumps(dict(vault='old-vault', codex='old-codex')))
        def changed_during_check(state):
            config.write_text(json.dumps(dict(vault='new-vault', codex='new-codex', allowFakeIpDns=True)))
            return dict(installed=True)
        with mock.patch.object(setup.sys, 'platform', 'darwin'), mock.patch.object(setup.sys, 'version_info', (3, 14)), mock.patch.object(setup.platform, 'machine', return_value='arm64'), mock.patch.object(setup.importlib.util, 'find_spec', return_value=object()), mock.patch.object(setup, 'check', side_effect=changed_during_check):
            setup.install(self.root)
        updated = json.loads(config.read_text())
        self.assertEqual(updated['vault'], 'new-vault')
        self.assertEqual(updated['codex'], 'new-codex')
        self.assertTrue(updated['allowFakeIpDns'])


if __name__ == '__main__':
    unittest.main()
