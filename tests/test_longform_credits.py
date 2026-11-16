import unittest

from core.editor_longform import _paginate_credit_lines


class LongformCreditPaginationTests(unittest.TestCase):
    def test_all_credit_lines_are_preserved_across_pages(self):
        source = [f"Source {index}: " + ("attribution details " * 8) for index in range(31)]
        pages = _paginate_credit_lines(source, width=100, lines_per_page=14)
        flattened = [line for page in pages for line in page]
        self.assertEqual(len(pages), 3)
        self.assertTrue(all(page and len(page) <= 14 for page in pages))
        self.assertTrue(any("Source 0:" in line for line in flattened))
        self.assertTrue(any("Source 30:" in line for line in flattened))

    def test_empty_credit_register_produces_no_pages(self):
        self.assertEqual(_paginate_credit_lines(["", "   "]), [])

    def test_invalid_pagination_settings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "width"):
            _paginate_credit_lines(["Source"], width=10)
        with self.assertRaisesRegex(ValueError, "lines per page"):
            _paginate_credit_lines(["Source"], lines_per_page=0)


if __name__ == "__main__":
    unittest.main()
