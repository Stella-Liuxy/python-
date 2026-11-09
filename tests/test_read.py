from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from agent.tools import read_file


class ReadTests(unittest.TestCase):
    def test_crlf_at_chunk_boundary_keeps_line_numbers(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_bytes(b"x" * 4095 + b"\r\nsecond\r\nthird")

            first_page = read_file(root, Event(), "notes.txt", limit=1)
            self.assertEqual(first_page["total_lines"], 3)
            self.assertEqual(first_page["remaining"], 2)
            self.assertIn("[长行已截断]", first_page["text"])

            next_page = read_file(root, Event(), "notes.txt", offset=first_page["next_offset"])
            self.assertEqual(next_page["text"], "   2  second\n   3  third")
            self.assertIsNone(next_page["next_offset"])

    def test_preview_marks_truncation_without_marking_exact_limit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("a" * 600 + "\n" + "b" * 601, encoding="utf-8")

            lines = read_file(root, Event(), "notes.txt")["text"].splitlines()
            self.assertEqual(lines[0], "   1  " + "a" * 600)
            self.assertEqual(lines[1], "   2  " + "b" * 600 + " …[长行已截断]")
