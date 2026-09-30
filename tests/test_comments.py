import tempfile
import unittest
from pathlib import Path

from behancer_bot.comments import COMMENTS, pick_comment


class CommentBankTests(unittest.TestCase):
    def test_bank_size_uniqueness_and_minimum_length(self):
        self.assertGreaterEqual(len(COMMENTS), 100)
        self.assertLessEqual(len(COMMENTS), 500)
        self.assertEqual(len(COMMENTS), len(set(COMMENTS)))
        self.assertTrue(all(len(comment) >= 10 for comment in COMMENTS))
        self.assertFalse(any("а" <= char.lower() <= "я" or char.lower() == "ё" for comment in COMMENTS for char in comment))

    def test_pick_comment_does_not_repeat_until_the_bank_is_exhausted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "used.json"
            seen = [pick_comment(path) for _ in range(len(COMMENTS))]
            self.assertEqual(set(seen), set(COMMENTS))
            self.assertIn(pick_comment(path), COMMENTS)


if __name__ == "__main__":
    unittest.main()
