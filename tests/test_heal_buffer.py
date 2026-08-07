import tempfile
import unittest
from pathlib import Path

import heal_buffer


class MergeLinesTests(unittest.TestCase):
    def _run(self, existing, incoming):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "mercado.jsonl"
            if existing is not None:
                dest.write_text("\n".join(existing) + "\n", encoding="utf-8")
            added = heal_buffer._merge_lines(dest, incoming)
            out = [l for l in dest.read_text().splitlines() if l.strip()]
            return added, out

    def test_restores_into_empty_buffer(self):
        added, out = self._run(None, ['{"a":1}', '{"a":2}'])
        self.assertEqual(added, 2)
        self.assertEqual(out, ['{"a":1}', '{"a":2}'])

    def test_union_keeps_local_and_adds_missing(self):
        # Local tem 2 linhas; release traz 1 repetida + 1 nova.
        added, out = self._run(['{"a":1}', '{"a":2}'],
                               ['{"a":1}', '{"a":3}'])
        self.assertEqual(added, 1)                 # só {"a":3} é nova
        self.assertEqual(set(out), {'{"a":1}', '{"a":2}', '{"a":3}'})
        self.assertEqual(len(out), 3)              # sem duplicatas

    def test_no_duplicates_when_fully_overlapping(self):
        added, out = self._run(['{"a":1}'], ['{"a":1}'])
        self.assertEqual(added, 0)
        self.assertEqual(out, ['{"a":1}'])


if __name__ == "__main__":
    unittest.main()
