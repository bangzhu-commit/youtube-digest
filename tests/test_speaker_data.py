import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'native'))
import podcast_host as host
import speaker_data as speakers
import transcription_jobs as jobs

ID = 'd' * 24


class SpeakerDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config = dict(vault=str(self.root))
        self.vault = host.Vault(self.config)
        self.folder = self.vault.sources / '_attachments/xiaoyuzhou' / ID
        self.folder.mkdir(parents=True)
        self.audio = self.folder / 'source.mp3'
        self.audio.write_bytes(b'fixture-original-audio')
        self.source = self.vault.sources / f'小宇宙-{ID}-转写全文.md'
        self.checkpoint = self.folder / 'transcribe-segments.json'
        self.segments = [dict(start=0.0, end=2.0, text='你好😀。'),
                         dict(start=2.0, end=4.0, text='第二句。'),
                         dict(start=4.0, end=6.0, text='这句跨越换人。'),
                         dict(start=36.0, end=38.0, text='最后一句。')]
        self.duration = 40.0
        self.write_source()
        self.turns = [dict(start=0, end=4, speaker='original-9'),
                      dict(start=4, end=5, speaker='original-9'),
                      dict(start=5, end=6, speaker='original-2'),
                      dict(start=36, end=38, speaker='original-2')]

    def write_source(self):
        state = dict(model=jobs.MODEL, audioHash=hashlib.sha256(self.audio.read_bytes()).hexdigest(),
                     duration=self.duration, processedSeconds=self.duration, segments=self.segments)
        self.checkpoint.write_text(json.dumps(state, ensure_ascii=False))
        self.source.write_text(jobs.transcript_markdown(ID, '测试节目',
                               jobs.merged_segments(self.segments, self.duration), self.duration))

    def metadata(self, inputs=None):
        inputs = inputs or speakers.load_inputs(self.config, ID)
        return {**{key: inputs[key] for key in speakers.HASH_FIELDS},
                'model': 'fixture-audio-diarization', 'params': {'num_clusters': -1}}

    def publish(self, turns=None):
        return speakers.publish(self.config, ID, turns or self.turns, self.metadata())

    def test_exact_text_unicode_offsets_and_join_spaces_survive(self):
        inputs = speakers.load_inputs(self.config, ID)
        spans = inputs['alignedSegments']
        self.assertEqual(spans[0]['sourceEnd'], 4)  # emoji is one Unicode code point
        self.assertEqual(spans[1]['sourceStart'], 4)
        self.assertEqual(spans[1]['text'], ' 第二句。')
        before = self.source.read_bytes()
        self.publish()
        result = speakers.load(self.config, ID)
        self.assertTrue(result['success'])
        data = result['data']
        self.assertFalse(data['wordAlignment'])
        self.assertEqual(data['offsetUnit'], 'unicode-code-point')
        self.assertNotIn('turns', data)
        for entry in inputs['sourceEntries']:
            rows = [row for row in data['segments'] if row['sourceEntryId'] == entry['id']]
            self.assertEqual(''.join(row['text'] for row in rows), entry['text'])
            for row in rows:
                self.assertEqual(entry['text'][row['sourceStart']:row['sourceEnd']], row['text'])
        self.assertEqual(self.source.read_bytes(), before)

    def test_cross_speaker_boundary_stays_whole_and_mixed(self):
        self.publish()
        rows = speakers.load(self.config, ID)['data']['segments']
        mixed = next(row for row in rows if row['status'] == 'mixed')
        self.assertEqual(mixed['text'], ' 这句跨越换人。')
        self.assertEqual(mixed['speakerIds'], ['S1', 'S2'])
        self.assertEqual(mixed['asrSegmentIndices'], [2])
        self.assertEqual(len(rows), 3)  # first two single-speaker ASR pieces merged

    def test_actual_simultaneous_turns_remain_overlap(self):
        turns = [*self.turns, dict(start=4.4, end=5.4, speaker='original-2')]
        self.publish(turns)
        row = next(row for row in speakers.load(self.config, ID)['data']['segments'] if 2 in row['asrSegmentIndices'])
        self.assertEqual(row['status'], 'overlap')

    def test_tiny_other_speaker_turn_is_not_majority_assigned(self):
        turns = [dict(start=0, end=1.999, speaker='A'), dict(start=1.999, end=2, speaker='B')]
        self.publish(turns)
        first = speakers.load(self.config, ID)['data']['segments'][0]
        self.assertEqual(first['status'], 'mixed')
        self.assertEqual(len(first['speakerIds']), 2)

    def test_missing_voice_coverage_is_uncertain(self):
        self.publish([dict(start=0, end=0.2, speaker='A')])
        rows = speakers.load(self.config, ID)['data']['segments']
        self.assertTrue(all(row['status'] == 'uncertain' for row in rows))
        self.assertEqual(rows[0]['coverageRatio'], 0.1)
        self.assertEqual(rows[-1]['speakerIds'], [])

    def test_numbering_uses_first_reliable_segment_not_cluster_name(self):
        self.publish()
        data = speakers.load(self.config, ID)['data']
        self.assertEqual(data['speakers'][0], dict(id='S1', label='说话人 1', reliableAppearance=True))
        self.assertEqual(data['segments'][0]['speakerIds'], ['S1'])
        self.assertEqual(data['segments'][-1]['speakerIds'], ['S2'])

    def build_with_minimum(self, turns, minimum=3.0):
        inputs = speakers.load_inputs(self.config, ID)
        metadata = self.metadata(inputs)
        metadata['params'].update(minSpeakerSeconds=minimum, threshold=0.8)
        return speakers.build_data(ID, inputs, turns, metadata)

    def test_short_cluster_touching_long_speaker_is_always_uncertain(self):
        turns = [dict(start=0, end=5, speaker='long-A'),
                 dict(start=4, end=8, speaker='long-B'),
                 dict(start=4.5, end=4.7, speaker='short')]
        data = self.build_with_minimum(turns)
        row = next(row for row in data['segments'] if 2 in row['asrSegmentIndices'])
        self.assertEqual(row['status'], 'uncertain')
        self.assertEqual(row['speakerIds'], ['S1', 'S2'])
        self.assertEqual(row['unassignedClusterIds'], ['short'])
        self.assertEqual(row['text'], ' 这句跨越换人。')
        self.assertEqual(len(data['speakers']), 2)
        short = next(turn for turn in data['turns'] if turn['clusterId'] == 'short')
        self.assertEqual((short['start'], short['end']), (4.5, 4.7))
        self.assertIsNone(short['speakerId'])
        self.assertTrue(short['unassigned'])

    def test_pure_short_segment_with_long_cluster_elsewhere_stays_unassigned(self):
        data = self.build_with_minimum([dict(start=0, end=1.5, speaker='short-first'),
                                       dict(start=2, end=6, speaker='long-later')])
        first = data['segments'][0]
        self.assertEqual(first['speakerIds'], [])
        self.assertEqual(first['status'], 'uncertain')
        self.assertEqual(data['speakers'][0]['id'], 'S1')
        assigned = [turn for turn in data['turns'] if not turn['unassigned']]
        self.assertEqual([turn['clusterId'] for turn in assigned], ['long-later'])
        self.assertEqual(data['minSpeakerSeconds'], 3.0)
        self.assertEqual(data['params']['threshold'], 0.8)

    def test_cluster_evidence_uses_interval_union_not_summed_overlap(self):
        data = self.build_with_minimum([dict(start=0, end=1.9, speaker='overlapping-short'),
                                       dict(start=0.4, end=2.2, speaker='overlapping-short'),
                                       dict(start=4, end=8, speaker='long')])
        self.assertEqual(data['unassignedClusters'], [dict(clusterId='overlapping-short',
                         durationSeconds=2.2, turnCount=2, reason='insufficient-speaker-duration')])
        self.assertEqual(data['unassignedTurnCount'], 2)
        self.assertEqual(len(data['speakers']), 1)
        self.assertEqual(data['segments'][0]['status'], 'uncertain')

    def test_exact_threshold_gets_number_and_default_zero_keeps_short_clusters(self):
        turns = [dict(start=0, end=1, speaker='A'), dict(start=2, end=4, speaker='A')]
        data = self.build_with_minimum(turns)
        self.assertEqual(len(data['speakers']), 1)
        self.assertEqual(data['unassignedClusters'], [])
        self.assertEqual(data['unassignedTurnCount'], 0)
        inputs = speakers.load_inputs(self.config, ID)
        default = speakers.build_data(ID, inputs, [dict(start=0, end=0.1, speaker='tiny')], self.metadata(inputs))
        self.assertEqual(default['minSpeakerSeconds'], 0)
        self.assertEqual(len(default['speakers']), 1)
        self.assertEqual(default['unassignedClusters'], [])

    def test_all_short_clusters_archive_uncertain_with_exact_source(self):
        turns = [dict(start=0, end=1, speaker='A'), dict(start=2, end=3, speaker='B'),
                 dict(start=4, end=5, speaker='C'), dict(start=36, end=37, speaker='D')]
        metadata = self.metadata()
        metadata['params']['minSpeakerSeconds'] = 3.0
        original = self.source.read_bytes()
        result = speakers.publish(self.config, ID, turns, metadata)
        self.assertEqual(result['speakerCount'], 0)
        loaded = speakers.load(self.config, ID)
        self.assertTrue(loaded['success'])
        data = loaded['data']
        self.assertEqual(data['speakers'], [])
        self.assertEqual(data['unassignedTurnCount'], 4)
        self.assertEqual(len(data['unassignedClusters']), 4)
        self.assertTrue(all(row['status'] == 'uncertain' and row['speakerIds'] == [] for row in data['segments']))
        for entry in host.source_entries(self.source.read_text()):
            rows = [row for row in data['segments'] if row['sourceEntryId'] == entry['id']]
            self.assertEqual(''.join(row['text'] for row in rows), entry['text'])
            for row in rows:
                self.assertEqual(entry['text'][row['sourceStart']:row['sourceEnd']], row['text'])
        self.assertEqual(self.source.read_bytes(), original)
        document, path = speakers.paths(self.config, ID)[2:]
        stored = json.loads(path.read_text())
        self.assertEqual(len(stored['turns']), len(turns))
        self.assertTrue(all(turn['speakerId'] is None and turn['unassigned'] for turn in stored['turns']))
        self.assertIn('不代表置信度或识别准确率', document.read_text())

    def test_minimum_evidence_threshold_is_bounded_and_finite(self):
        for minimum in (-1, 30.1, float('nan'), float('inf'), True, '3', None):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                self.build_with_minimum(self.turns, minimum)
        self.assertEqual(self.build_with_minimum(self.turns, 30)['speakers'], [])

    def test_long_explicit_unknown_never_gets_a_stable_number(self):
        turns = [dict(start=0, end=8, speaker='U0', unassigned=True, originalSpeaker=17),
                 dict(start=36, end=40, speaker='R0', unassigned=False, originalSpeaker=29)]
        for minimum in (0, 3):
            with self.subTest(minimum=minimum):
                data = self.build_with_minimum(turns, minimum)
                self.assertEqual(len(data['speakers']), 1)
                self.assertEqual(data['unassignedClusters'], [dict(clusterId='U0', durationSeconds=8,
                                 turnCount=1, reason='embedding-uncertain')])
                unknown = next(turn for turn in data['turns'] if turn['clusterId'] == 'U0')
                self.assertIsNone(unknown['speakerId'])
                self.assertTrue(unknown['unassigned'])
                self.assertEqual(unknown['unassignedReason'], 'embedding-uncertain')
                self.assertEqual(unknown['originalSpeaker'], 17)
                self.assertEqual((unknown['start'],unknown['end']), (0,8))
                self.assertTrue(all(row['status'] == 'uncertain' for row in data['segments'] if row['start'] < 8))

    def test_explicit_unknown_mixed_with_reliable_voice_marks_entire_asr_uncertain(self):
        data = self.build_with_minimum([dict(start=0, end=8, speaker='R0', unassigned=False, originalSpeaker='old-A'),
                                       dict(start=1, end=5, speaker='U1', unassigned=True, originalSpeaker='old-B')])
        self.assertEqual(len(data['speakers']), 1)
        for row in data['segments'][:3]:
            self.assertEqual(row['status'], 'uncertain')
            self.assertEqual(row['speakerIds'], ['S1'])
            self.assertEqual(row['unassignedClusterIds'], ['U1'])
        self.assertEqual([turn['originalSpeaker'] for turn in data['turns']], ['old-A','old-B'])
        self.assertEqual(''.join(row['text'] for row in data['segments'] if row['sourceEntryId']=='p-0'),
                         host.source_entries(self.source.read_text())[0]['text'])

    def test_same_cluster_cannot_mix_assigned_and_unassigned_flags(self):
        for trusted in (dict(unassigned=False), {}):
            with self.subTest(trusted=trusted), self.assertRaisesRegex(ValueError, '同时被标为可信和未分配'):
                self.build_with_minimum([dict(start=0, end=4, speaker='conflict', unassigned=True),
                                         dict(start=4, end=8, speaker='conflict', **trusted)])

    def test_embedding_and_duration_unknown_reasons_remain_distinct(self):
        data = self.build_with_minimum([dict(start=0, end=1, speaker='U0', unassigned=True, originalSpeaker=4),
                                       dict(start=2, end=3, speaker='short-R', unassigned=False, originalSpeaker=7)])
        self.assertEqual(data['speakers'], [])
        reasons = {item['clusterId']:item['reason'] for item in data['unassignedClusters']}
        self.assertEqual(reasons, {'U0':'embedding-uncertain','short-R':'insufficient-speaker-duration'})
        self.assertEqual({turn['clusterId']:turn['unassignedReason'] for turn in data['turns']}, reasons)
        self.assertTrue(all(row['status']=='uncertain' for row in data['segments']))

    def test_invalid_unknown_flags_and_provenance_are_rejected(self):
        for fields in [dict(unassigned='true'),dict(unassigned=1),dict(unassigned=None),
                       dict(originalSpeaker={}),dict(originalSpeaker=True)]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.build_with_minimum([dict(start=0,end=4,speaker='R0',**fields)])

    def test_explicit_unknown_provenance_survives_archive_and_load(self):
        metadata = self.metadata()
        metadata['params'].update(minSpeakerSeconds=3, refinement={'method':'fixture-only'})
        before = self.source.read_bytes()
        result = speakers.publish(self.config, ID,
                                  [dict(start=0,end=40,speaker='U0',unassigned=True,originalSpeaker=12)],metadata)
        self.assertEqual(result['speakerCount'], 0)
        loaded = speakers.load(self.config, ID)
        self.assertTrue(loaded['success'])
        self.assertEqual(loaded['data']['unassignedClusters'][0]['reason'], 'embedding-uncertain')
        self.assertEqual(loaded['data']['params']['refinement'], {'method':'fixture-only'})
        stored = json.loads(speakers.paths(self.config, ID)[3].read_text())
        self.assertEqual(stored['turns'][0]['originalSpeaker'], 12)
        self.assertIsNone(stored['turns'][0]['speakerId'])
        self.assertEqual(stored['turns'][0]['unassignedReason'], 'embedding-uncertain')
        self.assertEqual(self.source.read_bytes(), before)

    def test_empty_speakers_cannot_load_falsely_assigned_rows(self):
        turns = [dict(start=0, end=1, speaker='short')]
        metadata = self.metadata()
        metadata['params']['minSpeakerSeconds'] = 3
        speakers.publish(self.config, ID, turns, metadata)
        path = speakers.paths(self.config, ID)[3]
        data = json.loads(path.read_text())
        data['segments'][0]['status'] = 'single'
        path.write_text(json.dumps(data))
        self.assertFalse(speakers.load(self.config, ID)['success'])

    def test_source_reading_and_overview_are_unchanged(self):
        protected = [self.source]
        for _, path in (self.vault.refined_paths(ID), self.vault.overview_paths(ID)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"existing":"must remain byte-identical"}')
            protected.append(path)
        before = {path: path.read_bytes() for path in protected}
        self.publish()
        self.assertEqual(before, {path: path.read_bytes() for path in protected})

    def test_missing_audio_fails_without_writing(self):
        self.audio.unlink()
        with self.assertRaisesRegex(ValueError, '缺少本集原始音频'):
            speakers.load_inputs(self.config, ID)
        self.assertFalse(speakers.paths(self.config, ID)[3].exists())

    def test_checkpoint_audio_mismatch_and_incomplete_rejected(self):
        self.audio.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, '音频已变更'):
            speakers.load_inputs(self.config, ID)
        self.write_source()
        state = json.loads(self.checkpoint.read_text())
        state['processedSeconds'] = 10
        self.checkpoint.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            speakers.load_inputs(self.config, ID)

    def test_modified_source_text_never_fuzzy_matches(self):
        self.source.write_text(self.source.read_text().replace('第二句。', '第二句改过了。'))
        with self.assertRaisesRegex(ValueError, '不能逐字对应'):
            speakers.load_inputs(self.config, ID)

    def test_changed_source_hash_is_stale_and_cannot_publish_old_result(self):
        metadata = self.metadata()
        self.publish()
        self.source.write_text(self.source.read_text().replace('测试节目', '修改过的节目标题'))
        self.assertTrue(speakers.load(self.config, ID)['stale'])
        with self.assertRaisesRegex(ValueError, '已变更'):
            speakers.publish(self.config, ID, self.turns, metadata)

    def test_checkpoint_bytes_change_invalidates_result(self):
        self.publish()
        self.checkpoint.write_text(self.checkpoint.read_text() + '\n')
        result = speakers.load(self.config, ID)
        self.assertTrue(result['stale'])

    def test_manual_markdown_edits_are_not_overwritten(self):
        self.publish()
        document = speakers.paths(self.config, ID)[2]
        document.write_text(document.read_text() + '\n人工笔记，须保留。\n')
        before = document.read_bytes()
        self.assertTrue(speakers.load(self.config, ID)['manualEdited'])
        with self.assertRaisesRegex(ValueError, '未覆盖人工内容'):
            self.publish()
        self.assertEqual(before, document.read_bytes())

    def test_bad_spans_are_rejected_on_load(self):
        self.publish()
        path = speakers.paths(self.config, ID)[3]
        data = json.loads(path.read_text())
        data['segments'][0]['sourceEnd'] -= 1
        path.write_text(json.dumps(data))
        result = speakers.load(self.config, ID)
        self.assertFalse(result['success'])
        self.assertIn('字符范围', result['error'])

    def test_symlinks_and_invalid_episode_ids_are_rejected(self):
        self.audio.unlink()
        target = self.root / 'other.mp3'
        target.write_bytes(b'private')
        self.audio.symlink_to(target)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            speakers.load_inputs(self.config, ID)
        with self.assertRaisesRegex(ValueError, '节目编号'):
            speakers.load(self.config, '../escape')

    def test_invalid_turns_are_rejected_and_do_not_write(self):
        for turn in [dict(start=2, end=1, speaker='A'), dict(start=0, end=float('nan'), speaker='A'),
                     dict(start=0, end=2, speaker={}), dict(start=-1, end=1, speaker='A')]:
            with self.subTest(turn=turn), self.assertRaisesRegex(ValueError, '声段时间或标签'):
                self.publish([turn])
        self.assertFalse(speakers.paths(self.config, ID)[3].exists())

    def test_native_load_is_independent_of_normal_load(self):
        self.assertEqual(self.vault.handle(dict(action='speakerLoad', episodeId=ID)), dict(success=True, found=False))
        self.publish()
        self.assertTrue(self.vault.handle(dict(action='speakerLoad', episodeId=ID))['found'])
        normal = self.vault.load(ID)
        self.assertNotIn('speakers', normal)
        self.assertEqual(normal['raw'], self.source.read_text())


if __name__ == '__main__':
    unittest.main()
