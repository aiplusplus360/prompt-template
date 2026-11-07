import unittest

from prompttemplate import Template, lint
from prompttemplate.core import MissingVariables, TemplateSyntaxError


class CompileTests(unittest.TestCase):
    def test_no_placeholders_is_a_literal(self):
        compiled = Template("nothing to substitute")
        self.assertEqual(compiled.variables, ())
        self.assertEqual(compiled.render({}), "nothing to substitute")

    def test_json_braces_survive(self):
        compiled = Template('Return {"ok": true, "items": []} for {{topic}}')
        self.assertEqual(compiled.variables, ("topic",))
        self.assertEqual(compiled.render({"topic": "orders"}),
                         'Return {"ok": true, "items": []} for orders')

    def test_duplicate_placeholder_is_reported_once(self):
        self.assertEqual(Template("{{a}}{{a}}").variables, ("a",))

    def test_optional_marker(self):
        compiled = Template("{{name}}{{suffix?}}")
        self.assertEqual(compiled.required, ("name",))
        self.assertEqual(compiled.optional, ("suffix",))
        self.assertEqual(compiled.render({"name": "x"}), "x")

    def test_unclosed_placeholder_has_position(self):
        with self.assertRaises(TemplateSyntaxError) as ctx:
            Template("line one\nhello {{name")
        self.assertEqual(ctx.exception.line, 2)
        self.assertIn("unclosed", str(ctx.exception))

    def test_empty_placeholder_rejected(self):
        with self.assertRaises(TemplateSyntaxError):
            Template("{{}}")

    def test_unknown_filter_rejected(self):
        with self.assertRaises(TemplateSyntaxError):
            Template("{{name | shout}}")

    def test_invalid_name_rejected(self):
        with self.assertRaises(TemplateSyntaxError):
            Template("{{user name}}")

    def test_non_string_template_rejected(self):
        with self.assertRaises(TypeError):
            Template(b"bytes")


class RenderTests(unittest.TestCase):
    def test_missing_reports_everything_at_once(self):
        with self.assertRaises(MissingVariables) as ctx:
            Template("{{a}}\n{{b}}\n{{c}}").render({"b": 1})
        self.assertEqual([name for name, _ in ctx.exception.missing], ["a", "c"])
        self.assertEqual([line for _, line in ctx.exception.missing], [1, 3])

    def test_kwargs_merge_with_mapping(self):
        self.assertEqual(Template("{{a}}-{{b}}").render({"a": 1}, b=2), "1-2")

    def test_false_renders_as_json_literal(self):
        self.assertEqual(Template("{{flag}}").render({"flag": False}), "false")

    def test_none_renders_empty(self):
        self.assertEqual(Template("[{{x}}]").render({"x": None}), "[]")

    def test_filters(self):
        compiled = Template("{{a | upper}}|{{b | lower}}|{{c | trim}}")
        self.assertEqual(compiled.render({"a": "x", "b": "Y", "c": "  z  "}), "X|y|z")

    def test_json_filter_quotes(self):
        self.assertEqual(Template("{{v | json}}").render({"v": 'a"b'}), '"a\\"b"')

    def test_truncate_filter(self):
        self.assertEqual(Template("{{v | truncate:5}}").render({"v": "abcdefgh"}),
                         "abcd\u2026")

    def test_default_filter_fills_empty(self):
        self.assertEqual(Template("{{v | default:none}}").render({"v": ""}), "none")

    def test_nested_lookup(self):
        self.assertEqual(Template("{{user.name}}").render({"user": {"name": "Ada"}}), "Ada")

    def test_nested_lookup_missing_path(self):
        with self.assertRaises(MissingVariables):
            Template("{{user.name}}").render({"user": {}})

    def test_dict_value_is_stable_json(self):
        self.assertEqual(Template("{{v}}").render({"v": {"b": 1, "a": 2}}),
                         '{"a": 2, "b": 1}')

    def test_render_partial_keeps_placeholder(self):
        text, absent = Template("{{a}}/{{b | upper}}").render_partial({"a": "x"})
        self.assertEqual(text, "x/{{b | upper}}")
        self.assertEqual(absent, ["b"])

    def test_render_partial_when_complete(self):
        text, absent = Template("{{a}}").render_partial({"a": 1})
        self.assertEqual((text, absent), ("1", []))

    def test_render_map(self):
        self.assertEqual(Template("{{n}}").render_map([{"n": 1}, {"n": 2}]), ["1", "2"])

    def test_text_is_preserved_verbatim(self):
        source = "a\n\n  b\t{{x}}\n"
        self.assertEqual(Template(source).render({"x": ""}), "a\n\n  b\t\n")


class LintTests(unittest.TestCase):
    def test_clean_template(self):
        self.assertEqual(lint("Hello {{name}}"), [])

    def test_unclosed(self):
        self.assertEqual([issue.code for issue in lint("a {{ b")], ["unclosed"])

    def test_empty(self):
        self.assertEqual([issue.code for issue in lint("{{ }}")], ["empty"])

    def test_unknown_filter(self):
        self.assertEqual([issue.code for issue in lint("{{a | nope}}")], ["unknown-filter"])

    def test_reports_line_numbers(self):
        issues = lint("ok\n{{a | nope}}\n{{b")
        self.assertEqual([(i.code, i.line) for i in issues],
                         [("unknown-filter", 2), ("unclosed", 3)])

    def test_issue_is_comparable(self):
        self.assertEqual(lint("{{a}}")[0] if lint("{{a}}") else None, None)


if __name__ == "__main__":
    unittest.main()
