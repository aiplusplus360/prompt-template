import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from prompttemplate.cli import main


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.template = Path(self.tmp.name) / "prompt.txt"
        self.template.write_text("Hi {{name}}, plan {{plan | upper}}\n", encoding="utf-8")

    def test_variables_json(self):
        code, out, _ = run(["variables", str(self.template), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["required"], ["name", "plan"])

    def test_render_with_vars(self):
        code, out, _ = run(["render", str(self.template), "--var", "name=Ada",
                            "--var", "plan=\"pro\""])
        self.assertEqual(code, 0)
        self.assertEqual(out, "Hi Ada, plan PRO\n")

    def test_render_missing_exits_one(self):
        code, out, err = run(["render", str(self.template), "--var", "name=Ada"])
        self.assertEqual(code, 1)
        self.assertIn("1 missing variable", err)
        self.assertEqual(out, "")

    def test_render_partial_keeps_braces(self):
        code, out, err = run(["render", str(self.template), "--partial"])
        self.assertEqual(code, 0)
        self.assertIn("{{name}}", out)
        self.assertIn("left unrendered", err)

    def test_var_file(self):
        values = Path(self.tmp.name) / "vals.json"
        values.write_text('{"name": "Ada", "plan": "team"}', encoding="utf-8")
        code, out, _ = run(["render", str(self.template), "--var-file", str(values)])
        self.assertEqual(code, 0)
        self.assertIn("TEAM", out)

    def test_check_reports_missing(self):
        code, out, _ = run(["check", str(self.template), "--var", "name=Ada", "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual([row["name"] for row in payload["missing"]], ["plan"])

    def test_check_ok(self):
        code, out, _ = run(["check", str(self.template), "--var", "name=Ada",
                            "--var", "plan=pro"])
        self.assertEqual(code, 0)
        self.assertIn("ok:", out)

    def test_lint_reports_problem(self):
        broken = Path(self.tmp.name) / "broken.txt"
        broken.write_text("{{a | nope}}\n", encoding="utf-8")
        code, out, _ = run(["lint", str(broken)])
        self.assertEqual(code, 1)
        self.assertIn("unknown-filter", out)

    def test_lint_json_clean(self):
        code, out, _ = run(["lint", str(self.template), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["issues"], [])

    def test_output_file(self):
        target = Path(self.tmp.name) / "out.txt"
        code, _, err = run(["render", str(self.template), "--var", "name=Ada",
                            "--var", "plan=pro", "-o", str(target)])
        self.assertEqual(code, 0)
        self.assertIn("wrote", err)
        self.assertEqual(target.read_text(encoding="utf-8"), "Hi Ada, plan PRO\n")

    def test_bad_var_syntax(self):
        with self.assertRaises(SystemExit):
            run(["render", str(self.template), "--var", "novalue"])


if __name__ == "__main__":
    unittest.main()
