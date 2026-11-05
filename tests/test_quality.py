import unittest
from core.quality import _seconds, _hamming


class QualityInspectorUnitTests(unittest.TestCase):
    def test_seconds_handles_bad_values(self):
        self.assertEqual(_seconds("12.5"), 12.5)
        self.assertEqual(_seconds(None), 0.0)
        self.assertEqual(_seconds("not-a-number"), 0.0)

    def test_hamming_distance_counts_changed_bits(self):
        self.assertEqual(_hamming(0b1010, 0b1001), 2)
        self.assertEqual(_hamming(255, 255), 0)

if __name__ == "__main__":
    unittest.main()
