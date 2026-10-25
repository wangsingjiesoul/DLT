import unittest

from dlt.updater import merge_draws, parse_history_html


SAMPLE_HTML = """
<table><tbody>
<tr class="t_tr1"><!--<td>2</td>--><td class="t_tr1">26121</td><td class="cfont2">06</td><td class="cfont2">08</td><td class="cfont2">19</td><td class="cfont2">24</td><td class="cfont2">25</td><td class="cfont4">01</td><td class="cfont4">08</td><td>804,119,354</td><td>6</td><td>7,741,823</td><td>150</td><td>96,316</td><td>337,292,145</td><td>2026-10-24</td></tr>
<tr class="t_tr1"><td class="t_tr1">26120</td><td class="cfont2">01</td><td class="cfont2">06</td><td class="cfont2">10</td><td class="cfont2">15</td><td class="cfont2">32</td><td class="cfont4">03</td><td class="cfont4">04</td><td>825,893,884</td><td>3</td><td>10,000,000</td><td>73</td><td>303,750</td><td>314,954,142</td><td>2026-10-21</td></tr>
</tbody></table>
"""


class UpdaterTests(unittest.TestCase):
    def test_parse_history_html(self):
        draws = parse_history_html(SAMPLE_HTML)
        self.assertEqual(len(draws), 2)
        self.assertEqual(draws[0]["issue"], "26121")
        self.assertEqual(draws[0]["front"], ["06", "08", "19", "24", "25"])
        self.assertEqual(draws[0]["back"], ["01", "08"])

    def test_merge_draws_deduplicates_and_sorts_newest_first(self):
        existing = parse_history_html(SAMPLE_HTML)[1:]
        merged = merge_draws(existing, parse_history_html(SAMPLE_HTML))
        self.assertEqual([row["issue"] for row in merged], ["26121", "26120"])


if __name__ == "__main__":
    unittest.main()

