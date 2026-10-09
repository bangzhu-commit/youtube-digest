import copy
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import wave

spec = importlib.util.spec_from_file_location('podcast_host', Path(__file__).parents[1] / 'native/podcast_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
ID = 'a' * 24
URL = 'https://www.xiaoyuzhoufm.com/episode/' + ID
NOTE = {'id': 'test-note-01', 'quote': '原句。\n第二行', 'thought': '我还不确定。\n  保留缩进', 'start': 65.5, 'createdAt': '2026-10-08T11:00:00Z'}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'vault'
        self.root.mkdir()
        self.config = {'vault': str(self.root)}
        self.vault = host.Vault(self.config)
        self.payload = {'action': 'save', 'episodeId': ID, 'title': '测试节目', 'raw': '[1:05] 原句。\n第二行', 'body': '**[1:05]**\n\n原句。\n第二行', 'annotations': [copy.deepcopy(NOTE)]}

    def refined_source(self):
        self.vault.sources.mkdir(parents=True, exist_ok=True)
        source = self.vault.sources / 'original.md'
        raw = ('# 原稿\n\n来源：' + URL + '\n\n## 转写全文\n\n'
               '**[00:01:05] Speaker-00**\n\n原句。第二行。\n\n'
               '**[00:01:10] Speaker-01**\n\n另一个段落有 12 个，尚未确认。\n\n'
               '[^u1]: 识别疑点，不是发言。\n')
        source.write_text(raw)
        item = dict(id='p-0', sourceText='原句。第二行。', text='原句。第二行。', start=65,
                    speaker='Speaker-00', issues=[], provider='openrouter', model='deepseek/test',
                    updatedAt='2026-10-08T11:00:00Z')
        reading = dict(version='podcast-reading-v1', sourceHash=hashlib.sha256(raw.encode()).hexdigest(),
                       total=2, items=[item])
        return source, raw, reading

    def evidence(self, **changes):
        return dict(start=63, end=72, localText='本地识别文字。', heardText='云端识别文字。',
                    localModel='faster-whisper/medium/int8', audioModel='google/gemini-2.5-flash',
                    provider='openrouter', checkedAt='2026-10-08T11:00:00Z', **changes)

    def prepare_audio(self):
        audio = self.vault.sources / '_attachments/xiaoyuzhou' / ID / 'source.mp3'
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b'fixture audio')
        ffmpeg = self.root / 'ffmpeg'
        ffmpeg.write_text('fixture binary: never executed')
        self.config['ffmpeg'] = str(ffmpeg)
        return audio, ffmpeg

    def clip_process(self, *, candidate=None, duration=2, channels=1, rate=16000, mp3=b'encoded clip'):
        if candidate is None:
            candidate = {'text': '本地识别。', 'model': host.LOCAL_AUDIO_MODEL}

        def run(command, **options):
            self.assertTrue(options['check'])
            self.assertTrue(options['capture_output'])
            output = Path(command[-1])
            if output.suffix == '.wav' and command[0] == self.config['ffmpeg']:
                with wave.open(str(output), 'wb') as handle:
                    handle.setnchannels(channels)
                    handle.setsampwidth(2)
                    handle.setframerate(rate)
                    handle.writeframes(b'\0' * int(duration * rate) * channels * 2)
                result = b''
            elif output.suffix == '.mp3':
                output.write_bytes(mp3)
                result = b''
            else:
                result = json.dumps(candidate, ensure_ascii=False).encode()
            return subprocess.CompletedProcess(command, 0, stdout=result, stderr=b'')

        return run

    def test_import_archive_and_retry_preserve_text_without_duplicates(self):
        result = self.vault.handle(self.payload)
        source = self.root / result['sourcePath']
        source_copy = source.read_bytes()
        reading = self.root / result['readingPath']
        self.assertIn('![[知识库/原始素材库/', reading.read_text())
        self.assertEqual(self.vault.handle(self.payload)['added'], 0)
        self.assertEqual(source.read_bytes(), source_copy)
        loaded = self.vault.handle({'action': 'load', 'episodeId': ID})
        self.assertTrue(loaded['found'])
        self.assertEqual(loaded['annotations'], [NOTE])
        self.assertEqual(reading.read_text().count('<!-- digest-note:'), 1)
        original = self.vault.sources / '_attachments/xiaoyuzhou' / ID / 'digest-import-original.txt'
        self.assertEqual(original.read_text(), self.payload['raw'])

    def test_existing_raw_source_is_read_without_modification(self):
        self.vault.sources.mkdir(parents=True)
        source = self.vault.sources / 'already.md'
        raw = '# 现有稿\n\n来源：' + URL + '\n\n## 转写全文\n\n**[0:00] Speaker-00**\n\n不改写原话[^u1]\n\n[^u1]: 仍待核验。\n'
        source.write_text(raw)
        loaded = self.vault.load(ID)
        self.assertEqual(loaded['raw'], raw)
        self.vault.save(ID, {**self.payload, 'raw': raw})
        self.assertEqual(source.read_text(), raw)
        self.assertEqual(len(list(self.vault.sources.glob('*.md'))), 1)
        self.assertRaises(host.BridgeError, self.vault.save, ID, self.payload)

    def test_annotation_terminal_newlines_survive_load_and_retry(self):
        for ending in ['', '\n', '\n\n']:
            note = {**NOTE, 'id': 'newline-note-' + str(len(ending)), 'quote': '原句。' + ending, 'thought': '我的原话。' + ending}
            payload = {**self.payload, 'annotations': [note]}
            self.vault.handle(payload)
            self.assertIn(note, self.vault.load(ID)['annotations'])
            self.assertEqual(self.vault.handle(payload)['added'], 0)

    def test_manual_annotation_edit_is_not_overwritten(self):
        result = self.vault.handle(self.payload)
        reading = self.root / result['readingPath']
        content = reading.read_text().replace('我还不确定。', '我在 Obsidian 中重新想了一遍。')
        reading.write_text(content)
        self.assertRaises(host.BridgeError, self.vault.handle, self.payload)
        self.assertEqual(reading.read_text(), content)
        loaded = self.vault.load(ID)
        self.assertIn('重新想了一遍', loaded['annotations'][0]['thought'])

    def test_malformed_input_cannot_escape_the_vault(self):
        for message in [{'action':'delete','episodeId':ID}, {'action':'load','episodeId':'../../outside'}, {'action':'save','episodeId':ID,'title':'x','raw':'','body':'','annotations':[]}]:
            self.assertRaises(host.BridgeError, self.vault.handle, message)
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        self.vault.readings.parent.mkdir(parents=True)
        self.vault.readings.symlink_to(outside)
        self.assertRaises(host.BridgeError, self.vault.handle, self.payload)
        self.assertEqual(list(outside.iterdir()), [])

    def test_shownotes_only_and_ambiguous_sources_are_not_accepted(self):
        self.vault.sources.mkdir(parents=True)
        (self.vault.sources / 'summary.md').write_text('# 节目简介\n' + URL)
        self.assertFalse(self.vault.load(ID)['found'])
        for name in ['one.md', 'two.md']:
            (self.vault.sources / name).write_text('# 全文\n' + URL + '\n\n## 转写全文\n正文')
        self.assertRaises(host.BridgeError, self.vault.load, ID)

    def test_native_framing_roundtrip_and_partial_input(self):
        config = Path(self.temporary.name) / 'config.json'
        config.write_text(json.dumps(self.config))
        payload = json.dumps(self.payload, ensure_ascii=False).encode()
        incoming = io.BytesIO(struct.pack('<I', len(payload)) + payload)
        outgoing = io.BytesIO()
        host.serve(config, incoming, outgoing)
        data = outgoing.getvalue()
        length = struct.unpack('<I', data[:4])[0]
        result = json.loads(data[4:4+length])
        self.assertTrue(result['success'])
        self.assertEqual(result['added'], 1)
        incomplete = io.BytesIO(struct.pack('<I', 100) + b'{}')
        outgoing = io.BytesIO()
        host.serve(config, incomplete, outgoing)
        self.assertFalse(json.loads(outgoing.getvalue()[4:])['success'])

    def test_titles_cannot_create_paths_and_duplicate_note_ids_are_rejected(self):
        self.assertNotIn('/', host.safe_name('../../escape/filename'))
        message = {**self.payload, 'annotations': [NOTE, NOTE]}
        self.assertRaises(host.BridgeError, self.vault.handle, message)

    def test_refined_save_and_retry_preserve_source_and_model_metadata(self):
        source, raw, reading = self.refined_source()
        original = source.read_bytes()
        path = self.vault.save_refined(ID, '节目', raw, source, reading)
        document, data = self.vault.refined_paths(ID)
        first = document.read_bytes()
        self.assertEqual((self.root / path).resolve(), document)
        self.assertIn('精校 1/2 段', document.read_text())
        self.assertIn('文字精校', document.read_text())
        self.assertEqual(json.loads(data.read_text())['items'], reading['items'])
        self.vault.save_refined(ID, '节目', raw, source, reading)
        self.assertEqual(document.read_bytes(), first)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.vault.load(ID)['reading']['items'], reading['items'])

    def test_manually_edited_refined_document_is_never_overwritten(self):
        source, raw, reading = self.refined_source()
        self.vault.save_refined(ID, '节目', raw, source, reading)
        document, data = self.vault.refined_paths(ID)
        manual = document.read_text() + '\n我自己的补充，必须保留。\n'
        document.write_text(manual)
        saved_data = data.read_bytes()
        reading['items'][0]['text'] = '新的模型稿。'
        self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, reading)
        self.assertEqual(document.read_text(), manual)
        self.assertEqual(data.read_bytes(), saved_data)
        self.assertEqual(source.read_text(), raw)

    def test_refined_document_without_valid_sidecar_is_preserved(self):
        source, raw, reading = self.refined_source()
        document, data = self.vault.refined_paths(ID)
        document.parent.mkdir(parents=True)
        document.write_text('人的手稿。')
        for content in [None, '{bad json', '[]', '{}']:
            with self.subTest(content=content):
                if content is not None:
                    data.parent.mkdir(parents=True, exist_ok=True)
                    data.write_text(content)
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, reading)
                self.assertEqual(document.read_text(), '人的手稿。')
        self.assertEqual(source.read_text(), raw)

    def test_refined_hash_and_actual_source_version_both_must_match(self):
        source, raw, reading = self.refined_source()
        for changed in [dict(reading, sourceHash='0' * 64), dict(reading, version='other-v1')]:
            with self.subTest(changed=changed):
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, changed)
        source.write_text(raw + '\n手动追加。')
        self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, reading)
        self.assertEqual(source.read_text(), raw + '\n手动追加。')
        self.assertFalse(self.vault.refined_paths(ID)[0].exists())

    def test_refined_entries_require_exact_id_text_timestamp_and_speaker(self):
        source, raw, reading = self.refined_source()
        invalid = [{'id': 'p-99'}, {'sourceText': '原句。'}, {'start': 70}, {'start': True},
                   {'start': float('nan')}, {'speaker': 'Speaker-01'}, {'sourceText': '# 原稿'},
                   {'sourceText': '另一个段落有 12 个，尚未确认。'}, {'text': '   '}]
        for change in invalid:
            with self.subTest(change=change):
                altered = copy.deepcopy(reading)
                altered['items'][0].update(change)
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, altered)
                self.assertEqual(source.read_text(), raw)
                self.assertFalse(self.vault.refined_paths(ID)[0].exists())

    def test_refined_footnotes_cannot_be_claimed_as_speech(self):
        source, raw, reading = self.refined_source()
        reading['items'][0].update(id='p-2', sourceText='[^u1]: 识别疑点，不是发言。', start=None, speaker='转写说明')
        self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, reading)

    def test_refined_duplicate_and_reordered_ids_are_rejected(self):
        source, raw, reading = self.refined_source()
        second = dict(reading['items'][0], id='p-1', sourceText='另一个段落有 12 个，尚未确认。', start=70, speaker='Speaker-01')
        first = copy.deepcopy(reading['items'][0])
        for items in [[first, first], [second, first]]:
            with self.subTest(items=items):
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, dict(reading, items=items))
        self.vault.save_refined(ID, '节目', raw, source, dict(reading, items=[first, second]))

    def test_refined_total_cannot_overstate_or_understate_source_coverage(self):
        source, raw, reading = self.refined_source()
        for total in [0, 1, 3, True, 2.0, 501]:
            with self.subTest(total=total):
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, dict(reading, total=total))

    def test_refined_metadata_and_issues_are_bounded(self):
        source, raw, reading = self.refined_source()
        for change in [{'provider': 'unknown'}, {'model': 'x' * 201}, {'model': 'name\nsecret'},
                       {'updatedAt': 'yesterday'}, {'updatedAt': '2026-10-08T11:00:00'},
                       {'issues': ['x' * 601]}, {'issues': ['x'] * 21}, {'issues': [True]},
                       {'sourceText': 'x' * 20001}, {'text': 'x' * 20001}]:
            with self.subTest(change=change):
                altered = copy.deepcopy(reading)
                altered['items'][0].update(change)
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, altered)

    def test_first_import_refined_reading_binds_to_host_markdown_without_changing_raw(self):
        raw = self.payload['raw']
        item = dict(id='p-0', sourceText='原句。\n第二行', text='原句。\n第二行', start=65, speaker='', issues=[])
        reading = dict(version='podcast-reading-v1', sourceHash=hashlib.sha256(raw.encode()).hexdigest(), items=[item], total=1)
        result = self.vault.handle(dict(self.payload, reading=reading))
        canonical = (self.root / result['sourcePath']).read_text()
        loaded = self.vault.load(ID)
        self.assertEqual(loaded['reading']['sourceHash'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(loaded['raw'], raw)
        self.assertEqual(loaded['reading']['items'], [item])
        original = self.vault.sources / '_attachments/xiaoyuzhou' / ID / 'digest-import-original.txt'
        self.assertEqual(original.read_text(), raw)
        self.vault.handle(dict(self.payload, reading=reading))
        self.assertEqual((self.root / result['sourcePath']).read_text(), canonical)

    def test_first_import_json_and_srt_refined_entries_keep_original_anchors(self):
        variants = [('json', json.dumps([dict(text='有 12 个。', start=65, speaker='Speaker-00')], ensure_ascii=False), 'Speaker-00'),
                    ('srt', '1\n00:01:05,000 --> 00:01:07,000\n有 12 个。\n', '')]
        for index, (kind, raw, speaker) in enumerate(variants):
            with self.subTest(kind=kind):
                episode = ('b' if index == 0 else 'c') * 24
                item = dict(id='p-0', sourceText='有 12 个。', text='有 12 个。', start=65, speaker=speaker, issues=[])
                reading = dict(version='podcast-reading-v1', sourceHash=hashlib.sha256(raw.encode()).hexdigest(), items=[item], total=1)
                payload = dict(action='save', episodeId=episode, title=kind, raw=raw,
                               body=f'**[1:05] {speaker}**\n\n有 12 个。', annotations=[], reading=reading)
                result = self.vault.handle(payload)
                self.assertIsNotNone(result['refinedPath'])
                self.assertEqual(self.vault.load(episode)['reading']['items'], [item])

    def test_audio_evidence_is_saved_without_binary_or_unbounded_metadata(self):
        source, raw, reading = self.refined_source()
        reading['items'][0]['audioEvidence'] = self.evidence()
        self.vault.save_refined(ID, '节目', raw, source, reading)
        document, data = self.vault.refined_paths(ID)
        self.assertEqual(json.loads(data.read_text())['items'][0]['audioEvidence'], self.evidence())
        self.assertIn('本地＋云端听音片段复核（1:03–1:12）', document.read_text())
        self.assertEqual(source.read_text(), raw)

    def test_load_preserves_manually_edited_reading_and_does_not_apply_old_sidecar(self):
        source, raw, reading = self.refined_source()
        self.vault.save_refined(ID, '节目', raw, source, reading)
        document, _ = self.vault.refined_paths(ID)
        document.write_text(document.read_text() + '\n我在 Obsidian 中的人工修改。')
        loaded = self.vault.load(ID)
        self.assertTrue(loaded['readingManualEdited'])
        self.assertNotIn('reading', loaded)
        self.assertEqual(loaded['raw'], raw)

    def test_load_broken_reading_data_still_returns_source_and_annotations(self):
        source, raw, reading = self.refined_source()
        self.vault.save_refined(ID, '节目', raw, source, reading)
        _, data = self.vault.refined_paths(ID)
        for malformed in ['[1,2]', 'broken json']:
            data.write_text(malformed)
            loaded = self.vault.load(ID)
            self.assertTrue(loaded['success'])
            self.assertTrue(loaded['readingInvalid'])
            self.assertEqual(loaded['raw'], raw)
            self.assertNotIn('reading', loaded)

    def test_imported_fractional_times_and_multiline_json_reload_without_resegmentation(self):
        variants = [json.dumps([dict(text='第一部分。\n\n第二部分。', start=65.5)], ensure_ascii=False),
                    '1\n00:01:05,500 --> 00:01:07,000\n第一部分。\n第二部分。\n']
        for index, raw in enumerate(variants):
            episode = ('d' if index == 0 else 'e') * 24
            entry = host.source_entries(raw)[0]
            item = dict(entry, sourceText=entry['text'], issues=[])
            reading = dict(version='podcast-reading-v1', sourceHash=hashlib.sha256(raw.encode()).hexdigest(), items=[item], total=1)
            result = self.vault.handle(dict(action='save', episodeId=episode, title='导入', raw=raw,
                                           body='**[1:05]**\n\n' + entry['text'], annotations=[], reading=reading))
            loaded = self.vault.load(episode)
            self.assertEqual(loaded['raw'], raw)
            self.assertEqual(host.source_entries(loaded['raw']), host.source_entries(raw))
            self.assertEqual(loaded['reading']['items'], [item])
            document, data = self.vault.refined_paths(episode)
            stored = json.loads(data.read_text())
            canonical = (self.root / result['sourcePath']).read_text()
            self.assertEqual(stored['sourceHash'], hashlib.sha256(canonical.encode()).hexdigest())
            original = self.vault.sources / '_attachments/xiaoyuzhou' / episode / 'digest-import-original.txt'
            original.write_text(raw + ' changed')
            self.assertTrue(self.vault.load(episode)['readingStale'])

    def test_current_codex_review_can_be_archived_without_claiming_audio_evidence(self):
        source, raw, reading = self.refined_source()
        reading['items'][0]['provider'] = 'codex'
        self.vault.save_refined(ID, '节目', raw, source, reading)
        loaded = self.vault.load(ID)
        self.assertEqual(loaded['reading']['items'][0]['provider'], 'codex')
        self.assertNotIn('audioEvidence', loaded['reading']['items'][0])

    def test_audio_evidence_rejects_extra_missing_and_invalid_fields(self):
        source, raw, reading = self.refined_source()
        cases = [dict(self.evidence(), audio='unrequested binary'), {k: v for k, v in self.evidence().items() if k != 'heardText'},
                 None, True, [], dict(self.evidence(), localText=''), dict(self.evidence(), heardText=' '),
                 dict(self.evidence(), localText='x' * 30001), dict(self.evidence(), heardText='x' * 30001),
                 dict(self.evidence(), localModel='other'), dict(self.evidence(), audioModel='unknown'),
                 dict(self.evidence(), audioModel={}), dict(self.evidence(), provider='302ai'),
                 dict(self.evidence(), checkedAt='not-a-date'), dict(self.evidence(), checkedAt='2026-10-08T11:00:00'),
                 dict(self.evidence(), start=float('nan')), dict(self.evidence(), end=float('inf')),
                 dict(self.evidence(), start=True), dict(self.evidence(), end=160), dict(self.evidence(), end=63)]
        for evidence in cases:
            with self.subTest(evidence=evidence):
                altered = copy.deepcopy(reading)
                altered['items'][0]['audioEvidence'] = evidence
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, altered)
                self.assertEqual(source.read_text(), raw)
                self.assertFalse(self.vault.refined_paths(ID)[0].exists())

    def test_audio_evidence_window_must_include_the_associated_entry(self):
        source, raw, reading = self.refined_source()
        for evidence in [dict(self.evidence(), start=66), dict(self.evidence(), end=65)]:
            with self.subTest(evidence=evidence):
                altered = copy.deepcopy(reading)
                altered['items'][0]['audioEvidence'] = evidence
                self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, altered)

    def test_shared_timestamps_use_the_later_paragraph_window_instead_of_rechecking_the_start(self):
        source, raw, reading = self.refined_source()
        first, second = '甲' * 100, '乙' * 100
        raw = '# 原稿\n\n来源：' + URL + '\n\n## 转写全文\n\n**[1:05] Speaker-00**\n\n' + first + '\n\n' + second + '\n\n**[4:25] Speaker-01**\n\n下一段。\n'
        source.write_text(raw)
        reading['sourceHash'] = hashlib.sha256(raw.encode()).hexdigest()
        reading['total'] = 3
        reading['items'][0].update(id='p-1', sourceText=second, text=second,
                                  audioEvidence=dict(self.evidence(), start=157, end=247))
        self.vault.save_refined(ID, '节目', raw, source, reading)
        self.assertEqual(self.vault.load(ID)['reading']['items'][0]['audioEvidence']['start'], 157)
        reading['items'][0]['audioEvidence'] = dict(self.evidence(), start=63, end=153)
        self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, reading)

    def test_refined_markdown_keeps_referenced_source_uncertainty_footnotes(self):
        source, raw, reading = self.refined_source()
        raw = raw.replace('原句。第二行。', '原句[^u1]。第二行。')
        source.write_text(raw)
        reading['sourceHash'] = hashlib.sha256(raw.encode()).hexdigest()
        reading['items'][0].update(sourceText='原句[^u1]。第二行。', text='原句[^u1]。第二行。')
        self.vault.save_refined(ID, '节目', raw, source, reading)
        document = self.vault.refined_paths(ID)[0].read_text()
        self.assertEqual(document.count('[^u1]: 识别疑点，不是发言。'), 1)
        self.assertEqual(source.read_text(), raw)

    def test_untimed_entry_cannot_be_marked_as_audio_checked(self):
        source, raw, reading = self.refined_source()
        raw = raw.replace('**[00:01:05] Speaker-00**\n\n', '')
        source.write_text(raw)
        reading['sourceHash'] = hashlib.sha256(raw.encode()).hexdigest()
        reading['items'][0].update(start=None, speaker='', audioEvidence=self.evidence())
        self.assertRaises(host.BridgeError, self.vault.save_refined, ID, '节目', raw, source, reading)

    def test_audio_clip_only_runs_fixed_local_commands_and_reports_actual_tail(self):
        audio, ffmpeg = self.prepare_audio()
        before = audio.read_bytes()
        with mock.patch.object(host.subprocess, 'run', side_effect=self.clip_process(duration=1.5)) as run:
            result = self.vault.handle(dict(action='audioClip', episodeId=ID, start=65, end=70))
        self.assertEqual(result['start'], 65)
        self.assertEqual(result['end'], 66.5)
        self.assertEqual(result['localModel'], host.LOCAL_AUDIO_MODEL)
        self.assertEqual(result['localText'], '本地识别。')
        self.assertEqual(base64.b64decode(result['audio']), b'encoded clip')
        self.assertEqual(run.call_count, 3)
        first, local, encoded = [call.args[0] for call in run.call_args_list]
        self.assertEqual(first[0], str(ffmpeg))
        self.assertEqual(first[first.index('-i') + 1], str(audio))
        self.assertEqual(local[0], host.sys.executable)
        self.assertEqual(Path(local[1]).name, 'local_whisper.py')
        self.assertEqual(encoded[0], str(ffmpeg))
        self.assertFalse(Path(first[-1]).parent.exists())
        self.assertEqual(audio.read_bytes(), before)

    def test_invalid_audio_windows_never_start_local_processes(self):
        self.prepare_audio()
        pairs = [(0, 91), (-1, 1), (1, 1), (2, 1), (True, 2), (0, float('inf')),
                 (float('nan'), 2), ('0', 2), (604790, 604801)]
        with mock.patch.object(host.subprocess, 'run') as run:
            for start, end in pairs:
                with self.subTest(start=start, end=end):
                    self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=start, end=end))
            run.assert_not_called()

    def test_audio_clip_requires_existing_audio_and_configured_ffmpeg(self):
        with mock.patch.object(host.subprocess, 'run') as run:
            self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))
            self.prepare_audio()
            self.config.pop('ffmpeg')
            self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))
            run.assert_not_called()

    def test_audio_clip_cannot_follow_a_source_link_outside_the_vault(self):
        audio, _ = self.prepare_audio()
        outside = Path(self.temporary.name) / 'private.mp3'
        outside.write_bytes(b'private content')
        audio.unlink()
        audio.symlink_to(outside)
        with mock.patch.object(host.subprocess, 'run') as run:
            self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))
            run.assert_not_called()
        self.assertEqual(outside.read_bytes(), b'private content')

    def test_audio_clip_rejects_empty_or_invalid_wave_before_asr(self):
        self.prepare_audio()
        for options in [dict(duration=0), dict(duration=3), dict(channels=2), dict(rate=8000)]:
            with self.subTest(options=options):
                with mock.patch.object(host.subprocess, 'run', side_effect=self.clip_process(**options)) as run:
                    self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))
                    self.assertEqual(run.call_count, 1)

    def test_audio_clip_rejects_invalid_local_asr_results(self):
        self.prepare_audio()
        for candidate in [[], {}, {'text': '', 'model': host.LOCAL_AUDIO_MODEL},
                          {'text': 'x' * 30001, 'model': host.LOCAL_AUDIO_MODEL},
                          {'text': '候选', 'model': 'another-model'},
                          {'text': '候选', 'model': host.LOCAL_AUDIO_MODEL, 'audio': 'extra'}]:
            with self.subTest(candidate=candidate):
                with mock.patch.object(host.subprocess, 'run', side_effect=self.clip_process(candidate=candidate)):
                    self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))

    def test_audio_clip_rejects_empty_and_oversized_encoded_audio(self):
        self.prepare_audio()
        for content in [b'', b'x' * 525001]:
            with self.subTest(bytes=len(content)):
                with mock.patch.object(host.subprocess, 'run', side_effect=self.clip_process(mp3=content)):
                    self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))

    def test_audio_clip_process_failure_or_timeout_cleans_up_temporary_directory(self):
        self.prepare_audio()
        for error in [subprocess.CalledProcessError(1, 'fixed-command'), subprocess.TimeoutExpired('fixed-command', 150)]:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(host.subprocess, 'run', side_effect=error) as run:
                    self.assertRaises(host.BridgeError, self.vault.audio_clip, ID, dict(start=0, end=2))
                self.assertFalse(Path(run.call_args.args[0][-1]).parent.exists())


if __name__ == '__main__':
    unittest.main()
