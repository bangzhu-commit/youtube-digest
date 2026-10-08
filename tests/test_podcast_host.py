import copy
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
