import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'native'))
import podcast_host as host


class OverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vault = host.Vault({'vault': str(self.root)})
        self.episode = 'b' * 24
        self.raw = '[00:00] 开头的教育经历。\n\n[02:00] 结尾的工作经验。'
        self.overview = dict(version='podcast-overview-v1',
                             sourceHash=hashlib.sha256(self.raw.encode()).hexdigest(),
                             sourceEntryCount=2, provider='codex', model='test-model',
                             generatedAt='2026-11-06T08:00:00Z',
                             chapters=[dict(title='经历与工作', summary='先谈教育，再谈工作。', timestampSeconds=0)])
        self.payload = dict(action='save', episodeId=self.episode, title='测试播客', raw=self.raw,
                            body='**[00:00]**\n\n开头的教育经历。\n\n**[02:00]**\n\n结尾的工作经验。',
                            annotations=[], overview=self.overview)

    def test_round_trip_imported_overview_preserves_raw_and_coverage(self):
        saved = self.vault.handle(self.payload)
        self.assertTrue((self.root / saved['overviewPath']).is_file())
        loaded = self.vault.load(self.episode)
        self.assertEqual(loaded['raw'], self.raw)
        self.assertEqual(loaded['overview']['sourceHash'], self.overview['sourceHash'])
        self.assertEqual(loaded['overview']['sourceEntryCount'], 2)
        self.assertEqual(loaded['overview']['model'], 'test-model')

    def test_manual_overview_is_never_overwritten(self):
        saved = self.vault.handle(self.payload)
        document = self.root / saved['overviewPath']
        document.write_text('人工修改的概览')
        self.assertTrue(self.vault.load(self.episode)['overviewManualEdited'])
        with self.assertRaisesRegex(host.BridgeError, '人工修改'):
            self.vault.handle(self.payload)
        self.assertEqual(document.read_text(), '人工修改的概览')

    def test_changed_canonical_source_invalidates_overview(self):
        saved = self.vault.handle(self.payload)
        source = self.root / saved['sourcePath']
        source.write_text(source.read_text() + '\n补充的原文。\n')
        loaded = self.vault.load(self.episode)
        self.assertTrue(loaded['overviewStale'])
        self.assertNotIn('overview', loaded)

    def test_invalid_provenance_or_coverage_cannot_replace_saved_overview(self):
        saved = self.vault.handle(self.payload)
        path = self.root / saved['overviewPath']
        previous = path.read_bytes()
        for field, value in [('sourceEntryCount', 1), ('sourceHash', '0' * 64), ('provider', 'unknown')]:
            payload = copy.deepcopy(self.payload)
            payload['overview'][field] = value
            with self.assertRaises(host.BridgeError):
                self.vault.handle(payload)
            self.assertEqual(path.read_bytes(), previous)

    def test_nonfinite_or_out_of_order_timestamps_are_rejected(self):
        for starts in ([float('nan')], [60, 10]):
            payload = copy.deepcopy(self.payload)
            payload['overview']['chapters'] = [dict(title='章节', summary='说明', timestampSeconds=x) for x in starts]
            with self.assertRaises(host.BridgeError):
                self.vault.handle(payload)

    def test_corrupt_sidecar_is_preserved(self):
        self.vault.handle(self.payload)
        _, data = self.vault.overview_paths(self.episode)
        data.write_text('{broken')
        self.assertTrue(self.vault.load(self.episode)['overviewInvalid'])
        with self.assertRaisesRegex(host.BridgeError, '损坏'):
            self.vault.handle(self.payload)
        self.assertEqual(data.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
