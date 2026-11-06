from pathlib import Path
import sys
import unittest
from unittest import mock

try:
    import numpy as np
except ImportError:
    np = None

sys.path.insert(0, str(Path(__file__).parents[1] / 'native'))
import speaker_refinement as refinement


class RefinementTests(unittest.TestCase):
    def test_brief_overlap_does_not_discard_long_clean_turn(self):
        turns = [dict(start=0, end=43, speaker=0), dict(start=20, end=20.4, speaker=1)]
        records = refinement.select_windows(turns)
        self.assertTrue(records)
        self.assertTrue(all(row['turnIndex'] == 0 for row in records))
        self.assertTrue(all(row['end'] <= 19.9 or row['start'] >= 20.5 for row in records))
        self.assertTrue(all(row['end'] - row['start'] <= 8 for row in records))

    def test_short_voices_are_not_training_evidence_by_accumulation(self):
        turns = [dict(start=i, end=i + 0.4, speaker=0) for i in range(30)]
        self.assertEqual(refinement.select_windows(turns), [])
        refined, _ = refinement.assign_turns(turns, [], [])
        self.assertTrue(all(row['unassigned'] for row in refined))
        self.assertEqual(len({row['speaker'] for row in refined}), len(turns))

    def test_long_turn_uses_separated_windows(self):
        records = refinement.select_windows([dict(start=0, end=40, speaker=0)])
        self.assertEqual(len(records), 2)
        self.assertLess(records[0]['end'], records[1]['start'])

    def test_disagreeing_long_turn_windows_stay_unknown(self):
        turns = [dict(start=0, end=40, speaker=9)]
        records = [dict(turnIndex=0), dict(turnIndex=0)]
        result, _ = refinement.assign_turns(turns, records, [0, 1])
        self.assertTrue(result[0]['unassigned'])
        self.assertEqual(result[0]['originalSpeaker'], 9)
        self.assertEqual((result[0]['start'], result[0]['end']), (0, 40))

    def test_rejected_window_is_not_overruled_by_accepted_window(self):
        result, _ = refinement.assign_turns([dict(start=0, end=40, speaker=0)],
                                           [dict(turnIndex=0), dict(turnIndex=0)], [0, None])
        self.assertTrue(result[0]['unassigned'])

    def test_same_group_overlap_keeps_shorter_original_turn_unknown(self):
        turns = [dict(start=0, end=10, speaker=3), dict(start=9, end=13, speaker=7)]
        result, reasons = refinement.assign_turns(turns, [dict(turnIndex=0), dict(turnIndex=1)], [0, 0])
        self.assertFalse(result[0]['unassigned'])
        self.assertTrue(result[1]['unassigned'])
        self.assertEqual(result[1]['originalSpeaker'], 7)
        self.assertEqual((result[1]['start'], result[1]['end']), (9, 13))
        self.assertEqual(reasons['same-group-overlap-uncertain'], 1)

    @unittest.skipUnless(np is not None, 'optional numpy is not installed')
    def test_audio_similarity_discovers_groups_without_number_argument(self):
        rng = np.random.default_rng(44)
        vectors = []
        records = []
        for speaker in range(3):
            for item in range(24):
                vector = rng.normal(0, 0.015, 12)
                vector[speaker] += 1
                vectors.append(vector / np.linalg.norm(vector))
                records.append(dict(turnIndex=len(records), start=len(records) * 10, end=len(records) * 10 + 6))
        cores, assignments, _ = refinement.acoustic_groups(np.asarray(vectors, dtype=np.float32), records)
        self.assertEqual(len(cores), 3)
        self.assertEqual(len(set(assignments)), 3)
        for start in (0, 24, 48):
            self.assertEqual(len(set(assignments[start:start + 24])), 1)

    @unittest.skipUnless(np is not None, 'optional numpy is not installed')
    def test_bridge_embedding_is_not_forced_into_either_core(self):
        rng = np.random.default_rng(7)
        vectors = []
        records = []
        for speaker in range(2):
            for _ in range(25):
                vector = rng.normal(0, 0.01, 8)
                vector[speaker] += 1
                vectors.append(vector / np.linalg.norm(vector))
                records.append(dict(turnIndex=len(records), start=len(records) * 10, end=len(records) * 10 + 6))
        bridge = np.array([1, 1, 0, 0, 0, 0, 0, 0], dtype=np.float32) / np.sqrt(2)
        vectors.append(bridge)
        records.append(dict(turnIndex=50, start=500, end=506))
        cores, assignments, _ = refinement.acoustic_groups(np.asarray(vectors, dtype=np.float32), records)
        self.assertEqual(len(cores), 2)
        self.assertIsNone(assignments[-1])

    @unittest.skipUnless(np is not None, 'optional numpy is not installed')
    def test_insufficient_core_evidence_remains_unassigned(self):
        vectors = np.tile(np.array([1, 0], dtype=np.float32), (6, 1))
        records = [dict(turnIndex=i, start=i * 10, end=i * 10 + 6) for i in range(6)]
        cores, assignments, _ = refinement.acoustic_groups(vectors, records)
        self.assertEqual(cores, [])
        self.assertEqual(assignments, [None] * 6)

    def test_invalid_turns_and_excess_windows_fail_without_truncation(self):
        with self.assertRaises(ValueError):
            refinement.validate_turns([dict(start=0, end=float('nan'), speaker=0)], 100)
        with mock.patch.object(refinement, 'MAX_WINDOWS', 1), self.assertRaisesRegex(ValueError, '未截断'):
            refinement.select_windows([dict(start=0, end=40, speaker=0)])


if __name__ == '__main__':
    unittest.main()
