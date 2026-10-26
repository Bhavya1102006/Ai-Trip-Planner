import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tripplanner.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class EndToEndTests(unittest.TestCase):
    def test_happy_path_text_output(self):
        code, out, _ = run("3 days in Jaipur, budget 3000 per day, heritage and food")
        self.assertEqual(code, 0)
        self.assertIn("DAY 1", out)
        self.assertIn("DAY 3", out)
        self.assertIn("TOTAL:", out)

    def test_json_output_is_valid(self):
        code, out, _ = run("2 days in Jaipur, budget 2500 per day, shopping", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(len(data["days"]), 2)

    def test_bad_brief_returns_error_code_not_traceback(self):
        code, _, err = run("hello")
        self.assertEqual(code, 2)
        self.assertIn("Could not plan trip", err)

    def test_unknown_city_returns_error_code(self):
        code, _, err = run("2 days in Atlantis, budget 3000 per day")
        self.assertEqual(code, 2)
        self.assertIn("No data for", err)

    def test_html_preview_is_written(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "plan.html")
        code, _, _ = run("2 days in Jaipur, budget 2500 per day, shopping", "--html", path)
        self.assertEqual(code, 0)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("<h2>Day 2</h2>", fh.read())

    def test_single_day_trip(self):
        code, out, _ = run("1 day in Jaipur, budget 1500 per day, food")
        self.assertEqual(code, 0)
        self.assertNotIn("DAY 2", out)


if __name__ == "__main__":
    unittest.main()
