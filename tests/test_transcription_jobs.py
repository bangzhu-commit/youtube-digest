import importlib.util
import io
import fcntl
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('transcription_jobs', Path(__file__).parents[1] / 'native/transcription_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
ID = 'a' * 24


def complete_metadata(duration):
    return dict(episodeId=ID, title='节目', audioUrl='https://media.xyzcdn.net/a.mp3',
                duration=duration, sourceMode='PUBLIC')


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'vault'
        self.root.mkdir()
        self.config = dict(vault=str(self.root), ffmpeg='/installed/ffmpeg')
        self.folder = jobs.paths(self.config, ID)[2]

    def test_only_observed_public_cdn_and_https_are_allowed(self):
        good = 'https://media.xyzcdn.net/podcast/episode.mp3'
        self.assertEqual(jobs.validate_url(good, media=True, resolve=False), good)
        for url in ('http://media.xyzcdn.net/a', 'https://media.xyzcdn.net.evil.test/a',
                    'https://evil.media.xyzcdn.net/a', 'https://media.xyzcdn.net:8443/a',
                    'https://user:pass@media.xyzcdn.net/a', 'https://127.0.0.1/a',
                    'file:///etc/passwd', 'https://media.xyzcdn.net/a#fragment'):
            with self.subTest(url=url), self.assertRaises(jobs.JobError):
                jobs.validate_url(url, media=True, resolve=False)

    def test_dns_to_private_network_is_rejected(self):
        for ip in ('127.0.0.1', '10.0.0.1', '192.168.1.1', '169.254.169.254', '::1', 'fc00::1'):
            with self.subTest(ip=ip), mock.patch.object(jobs.socket, 'getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', (ip, 443))]):
                with self.assertRaises(jobs.JobError):
                    jobs.validate_url('https://media.xyzcdn.net/a', media=True)
                with self.assertRaises(jobs.JobError):
                    jobs.validate_url('https://media.xyzcdn.net/a', media=True, allow_fake_ip=True)

    def test_fake_ip_compatibility_is_explicit_and_still_domain_bounded(self):
        for ip in ('198.18.0.10', '198.19.255.254'):
            with mock.patch.object(jobs.socket, 'getaddrinfo', return_value=[(socket.AF_INET, 1, 6, '', (ip, 443))]):
                with self.assertRaises(jobs.JobError):
                    jobs.validate_url('https://media.xyzcdn.net/a', media=True)
                jobs.validate_url('https://media.xyzcdn.net/a', media=True, allow_fake_ip=True)
                with self.assertRaises(jobs.JobError):
                    jobs.validate_url('https://untrusted.example/a', media=True, allow_fake_ip=True)
        mixed = [(socket.AF_INET, 1, 6, '', (ip, 443)) for ip in ('198.18.0.10', '127.0.0.1')]
        with mock.patch.object(jobs.socket, 'getaddrinfo', return_value=mixed), self.assertRaises(jobs.JobError):
            jobs.validate_url('https://media.xyzcdn.net/a', media=True, allow_fake_ip=True)

    def test_fake_ip_redirect_keeps_exact_host_restriction(self):
        redirect = jobs.SafeRedirect(media=True, allow_fake_ip=True)
        with self.assertRaises(jobs.JobError):
            redirect.redirect_request(None, None, 302, '', {}, 'https://127.0.0.1/private')

    def test_rejects_paths_and_symlinked_attachments(self):
        with self.assertRaises(jobs.JobError):
            jobs.paths(self.config, '../escape')
        self.folder.mkdir(parents=True)
        outside = Path(self.temporary.name) / 'private'
        outside.write_text('not audio')
        (self.folder / 'source.mp3').symlink_to(outside)
        with self.assertRaises(jobs.JobError):
            jobs.paths(self.config, ID)

    def test_reads_only_matching_episode_metadata(self):
        episode = dict(eid=ID, title='节目', duration=8030, enclosure=dict(url='https://media.xyzcdn.net/a.mp3'),
                       media=dict(source=dict(mode='PUBLIC')))
        data = dict(props=dict(pageProps=dict(episode=episode)))
        def page():
            return io.BytesIO(('<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>').encode())
        with mock.patch.object(jobs, 'open_public', side_effect=lambda *a, **k: page()), mock.patch.object(jobs, 'validate_url'):
            metadata = jobs.episode_metadata(ID)
            self.assertEqual(metadata['title'], '节目')
            self.assertEqual(metadata['duration'], 8030)
            self.assertEqual(metadata['sourceMode'], 'PUBLIC')
            self.assertTrue(jobs.metadata_complete(metadata, ID))
            episode['eid'] = 'b' * 24
            with self.assertRaises(jobs.JobError):
                jobs.episode_metadata(ID)

    def test_nonpublic_or_preview_audio_metadata_is_rejected(self):
        for mode in ('PREVIEW', 'PRIVATE', 'PAID', ''):
            episode = dict(eid=ID, title='节目', duration=8030,
                           enclosure=dict(url='https://media.xyzcdn.net/a.mp3'), media=dict(source=dict(mode=mode)))
            data = dict(props=dict(pageProps=dict(episode=episode)))
            page = ('<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>').encode()
            with self.subTest(mode=mode), mock.patch.object(jobs, 'open_public', return_value=io.BytesIO(page)):
                with self.assertRaisesRegex(jobs.JobError, '不是公开完整音频'):
                    jobs.episode_metadata(ID)

    def test_declared_duration_must_be_positive_finite_and_bounded(self):
        for duration in (None, True, '8030', 0, -1, float('nan'), float('inf'), jobs.MAX_DURATION + 1):
            with self.subTest(duration=duration), self.assertRaises(jobs.JobError):
                jobs.declared_duration(duration)

    def test_duration_agreement_accepts_padding_but_rejects_preview_or_wrong_file(self):
        self.assertEqual(jobs.verify_full_duration(8030.5061, 8030), 8030.5061)
        for actual in (60, 300, 7900, 8500):
            with self.subTest(actual=actual), self.assertRaisesRegex(jobs.JobError, '未作为整集转写'):
                jobs.verify_full_duration(actual, 8030)

    def test_grouping_retains_every_segment_and_tail(self):
        segments = [dict(start=i * 9, end=i * 9 + 8, text=f'第{i}句内容。') for i in range(1000)]
        groups = jobs.merged_segments(segments, 9000)
        self.assertLessEqual(len(groups), 480)
        self.assertEqual(' '.join(group['text'] for group in groups), ' '.join(segment['text'] for segment in segments))
        self.assertEqual(groups[-1]['end'], 8999)
        self.assertEqual(groups[0]['start'], 0)

    def test_invalid_segment_timestamps_are_rejected(self):
        for segment in (dict(start=-1, end=2, text='a'), dict(start=4, end=2, text='a'),
                        dict(start=0, end=float('nan'), text='a')):
            with self.assertRaises(jobs.JobError):
                jobs.merged_segments([segment], 10)

    def test_full_source_has_parseable_heading_and_no_claim_of_manual_verification(self):
        text = jobs.transcript_markdown(ID, '一集节目', [dict(start=3661, text='原始转写全文。')], 8030)
        self.assertIn('## 转写全文\n', text)
        self.assertIn('**[1:01:01]**', text)
        self.assertIn('尚未逐句人工听校', text)
        self.assertIn(ID, text[:20000])

    def test_atomic_publish_preserves_manual_source(self):
        path = self.root / 'source.md'
        jobs.publish_source(path, 'original')
        jobs.publish_source(path, 'original')
        with self.assertRaises(jobs.JobError):
            jobs.publish_source(path, 'replacement')
        self.assertEqual(path.read_text(), 'original')
        self.assertEqual(list(self.root.glob('.digest-source-*')), [])

    def test_start_is_idempotent_while_worker_is_queued(self):
        with mock.patch.object(jobs, 'require_tools'), mock.patch.object(jobs.subprocess, 'Popen') as spawn:
            first = jobs.start(self.config, ID, '标题')
            second = jobs.start(self.config, ID, '标题')
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(first['job']['status'], 'running')
        self.assertEqual(second['job']['status'], 'running')

    def test_cancel_sets_signal_without_erasing_checkpoint(self):
        self.folder.mkdir(parents=True)
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', phase='transcribing', heartbeat=time.time(), processedSeconds=300, duration=600, progress=0.5))
        checkpoint = dict(processedSeconds=300, segments=[dict(start=0, end=10, text='已完成')])
        jobs.atomic_json(self.folder / 'transcribe-segments.json', checkpoint)
        result = jobs.cancel(self.config, ID)
        self.assertTrue((self.folder / 'transcribe-cancel').exists())
        self.assertEqual(jobs.read_json(self.folder / 'transcribe-segments.json'), checkpoint)
        self.assertEqual(result['job']['progress'], 0.5)

    def test_interrupted_job_reports_resumable_failure(self):
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', phase='transcribing', heartbeat=time.time()-60, processedSeconds=300, duration=600, progress=0.5))
        result = jobs.status(self.config, ID)
        self.assertEqual(result['job']['status'], 'failed')
        self.assertIn('继续', result['job']['error'])

    def test_worker_resumes_at_saved_audio_position_and_publishes_tail(self):
        self.folder.mkdir(parents=True)
        (self.folder / 'source.mp3').write_bytes(b'cached audio')
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', heartbeat=time.time()))
        jobs.atomic_json(self.folder / 'transcribe-metadata.json', complete_metadata(323))
        jobs.atomic_json(self.folder / 'transcribe-segments.json', dict(model=jobs.MODEL, audioHash=jobs.digest(self.folder / 'source.mp3'),
                        processedSeconds=300, segments=[dict(start=0, end=299, text='第一段')]))
        from types import SimpleNamespace
        model = mock.Mock()
        model.transcribe.return_value = (iter([SimpleNamespace(start=0, end=23, text='末尾片段')]), None)
        fake = SimpleNamespace(WhisperModel=mock.Mock(return_value=model))
        publish = jobs.publish_source
        def publish_under_lock(destination, content):
            with (self.folder / 'source-publish.lock').open('a') as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return publish(destination, content)
        with mock.patch.dict('sys.modules', faster_whisper=fake), mock.patch.object(jobs, 'require_tools', return_value=('ffmpeg','ffprobe')), mock.patch.object(jobs, 'probe', return_value=323), mock.patch.object(jobs.subprocess, 'run') as execute, mock.patch.object(jobs, 'publish_source', side_effect=publish_under_lock):
            jobs.run_worker(self.config, ID)
        command = execute.call_args[0][0]
        self.assertEqual(command[command.index('-ss') + 1], '300')
        result = jobs.status(self.config, ID)['job']
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['processedSeconds'], 323)
        self.assertIn('末尾片段', (self.root / result['sourcePath']).read_text())

    def test_import_during_asr_preserves_one_source_and_keeps_checkpoint(self):
        self.folder.mkdir(parents=True)
        (self.folder / 'source.mp3').write_bytes(b'cached audio')
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', heartbeat=time.time()))
        jobs.atomic_json(self.folder / 'transcribe-metadata.json', complete_metadata(10))
        from types import SimpleNamespace
        imported = {}
        def recognize(*args, **kwargs):
            # This is a real native save between the worker's initial discovery
            # and its final publication, not a fake source lookup result.
            vault = jobs.source_vault(self.config)
            saved = vault.save(ID, dict(title='人工导入稿', raw='[00:00] 用户导入全文',
                                       body='[00:00] 用户导入全文', annotations=[]))
            imported['path'] = saved['sourcePath']
            return iter([SimpleNamespace(start=0, end=10, text='ASR 完整片段')]), None
        model = SimpleNamespace(transcribe=recognize)
        fake = SimpleNamespace(WhisperModel=mock.Mock(return_value=model))
        with mock.patch.dict('sys.modules', faster_whisper=fake), mock.patch.object(jobs, 'require_tools', return_value=('ffmpeg', 'ffprobe')), mock.patch.object(jobs, 'probe', return_value=10), mock.patch.object(jobs.subprocess, 'run'):
            jobs.run_worker(self.config, ID)
        result = jobs.status(self.config, ID)['job']
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['sourcePath'], imported['path'])
        self.assertIn('未发布第二份', result['message'])
        vault = jobs.source_vault(self.config)
        self.assertEqual(len(list(vault.sources.glob('*.md'))), 1)
        self.assertEqual(vault.load(ID)['sourcePath'], imported['path'])
        checkpoint = jobs.read_json(self.folder / 'transcribe-segments.json')
        self.assertEqual(checkpoint['processedSeconds'], 10)
        self.assertEqual(checkpoint['segments'][0]['text'], 'ASR 完整片段')

    def test_existing_local_preview_without_metadata_is_checked_and_preserved(self):
        self.folder.mkdir(parents=True)
        audio = self.folder / 'source.mp3'
        audio.write_bytes(b'local preview must be preserved')
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', heartbeat=time.time()))
        jobs.atomic_json(self.folder / 'transcribe-metadata.json', dict(title='旧缓存', audioUrl='https://media.xyzcdn.net/a.mp3'))
        with mock.patch.object(jobs, 'episode_metadata', return_value=complete_metadata(8030)) as metadata, mock.patch.object(jobs, 'require_tools', return_value=('ffmpeg','ffprobe')), mock.patch.object(jobs, 'probe', return_value=60), mock.patch.object(jobs, 'download') as download, mock.patch.object(jobs.traceback, 'print_exc'):
            jobs.run_worker(self.config, ID)
        metadata.assert_called_once_with(ID, allow_fake_ip=False)
        download.assert_not_called()
        self.assertEqual(audio.read_bytes(), b'local preview must be preserved')
        result = jobs.status(self.config, ID)['job']
        self.assertEqual(result['status'], 'failed')
        self.assertIn('未作为整集转写', result['error'])
        self.assertFalse((self.folder / 'transcribe-segments.json').exists())

    def test_legacy_metadata_is_refreshed_before_accepting_complete_local_audio(self):
        self.folder.mkdir(parents=True)
        audio = self.folder / 'source.mp3'
        audio.write_bytes(b'complete cached audio')
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', heartbeat=time.time()))
        jobs.atomic_json(self.folder / 'transcribe-metadata.json', dict(title='旧缓存', audioUrl='https://media.xyzcdn.net/a.mp3'))
        actual = 8030.5061
        jobs.atomic_json(self.folder / 'transcribe-segments.json', dict(model=jobs.MODEL, audioHash=jobs.digest(audio),
                        processedSeconds=actual, segments=[dict(start=0, end=actual, text='整集内容与尾声')]))
        from types import SimpleNamespace
        fake = SimpleNamespace(WhisperModel=mock.Mock(return_value=mock.Mock()))
        with mock.patch.dict('sys.modules', faster_whisper=fake), mock.patch.object(jobs, 'episode_metadata', return_value=complete_metadata(8030)) as metadata, mock.patch.object(jobs, 'require_tools', return_value=('ffmpeg','ffprobe')), mock.patch.object(jobs, 'probe', return_value=actual), mock.patch.object(jobs, 'download') as download:
            jobs.run_worker(self.config, ID)
        metadata.assert_called_once_with(ID, allow_fake_ip=False)
        download.assert_not_called()
        self.assertEqual(audio.read_bytes(), b'complete cached audio')
        self.assertEqual(jobs.status(self.config, ID)['job']['status'], 'completed')
        self.assertEqual(jobs.read_json(self.folder / 'transcribe-metadata.json')['duration'], 8030)

    def test_worker_does_not_claim_no_source_when_cloud_scan_is_incomplete(self):
        self.folder.mkdir(parents=True)
        (jobs.paths(self.config, ID)[1] / 'unreadable.md').write_bytes(b'\xff')
        jobs.atomic_json(self.folder / 'transcribe-job.json', dict(status='running', heartbeat=time.time()))
        with mock.patch.object(jobs, 'require_tools') as dependencies:
            jobs.run_worker(self.config, ID)
        result = jobs.status(self.config, ID)['job']
        self.assertEqual(result['status'], 'failed')
        self.assertIn('扫描不完整', result['error'])
        dependencies.assert_not_called()

    def test_installer_requires_explicit_fake_ip_opt_in_and_preserves_it(self):
        state = Path(self.temporary.name) / 'bridge'
        hosts = Path(self.temporary.name) / 'hosts'
        installer = Path(__file__).parents[1] / 'native/install_host.py'
        command = [sys.executable, str(installer), '--extension-id', 'a' * 32, '--vault', str(self.root),
                   '--config-dir', str(state), '--host-dir', str(hosts)]
        subprocess.run(command, check=True, capture_output=True)
        self.assertFalse(jobs.read_json(state / 'obsidian.json')['allowFakeIpDns'])
        subprocess.run(command + ['--allow-fake-ip-dns'], check=True, capture_output=True)
        subprocess.run(command, check=True, capture_output=True)
        self.assertTrue(jobs.read_json(state / 'obsidian.json')['allowFakeIpDns'])
        subprocess.run(command + ['--no-allow-fake-ip-dns'], check=True, capture_output=True)
        self.assertFalse(jobs.read_json(state / 'obsidian.json')['allowFakeIpDns'])


if __name__ == '__main__':
    unittest.main()
