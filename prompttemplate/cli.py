"""``prompt-template`` command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import (Issue, MissingVariables, Template, TemplateError, TemplateSyntaxError,
                   lint)

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompt-template",
        description="Render and lint prompts with strict, deterministic variables.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    variables = sub.add_parser("variables", help="list placeholders and whether they are required")
    variables.add_argument("template", help="template file; '-' reads stdin")
    variables.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="verify a variables file satisfies the template")
    check.add_argument("template", help="template file; '-' reads stdin")
    check.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    check.add_argument("--var-file", help="JSON object of variable values")
    check.add_argument("--json", action="store_true")

    render_cmd = sub.add_parser("render", help="render a template")
    render_cmd.add_argument("template", help="template file; '-' reads stdin")
    render_cmd.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    render_cmd.add_argument("--var-file", help="JSON object of variable values")
    render_cmd.add_argument("--partial", action="store_true",
                            help="leave missing placeholders intact instead of failing")
    render_cmd.add_argument("-o", "--output")

    lint_cmd = sub.add_parser("lint", help="report syntax problems without raising")
    lint_cmd.add_argument("templates", nargs="+", help="template files")
    lint_cmd.add_argument("--json", action="store_true")
    return parser


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("cannot read %s: %s" % (path, exc))


def _coerce(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value


def _values(args: argparse.Namespace) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    if args.var_file:
        try:
            loaded = json.loads(_read(args.var_file))
        except ValueError as exc:
            raise SystemExit("%s is not valid JSON: %s" % (args.var_file, exc))
        if not isinstance(loaded, dict):
            raise SystemExit("%s must contain a JSON object" % args.var_file)
        values.update(loaded)
    for item in args.var:
        key, sep, raw = item.partition("=")
        if not sep:
            raise SystemExit("--var expects KEY=VALUE, got %r" % item)
        values[key.strip()] = _coerce(raw)
    return values


def _print_issues(issues: List[Issue], source: str) -> None:
    for issue in issues:
        print("%s:%d:%d: %s [%s]" % (source, issue.line, issue.column,
                                     issue.message, issue.code))


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "variables":
        template = _read(args.template)
        try:
            compiled = Template(template)
        except TemplateSyntaxError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        payload = {
            "variables": list(compiled.variables),
            "required": list(compiled.required),
            "optional": list(compiled.optional),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for name in compiled.variables:
                marker = "required" if name in compiled.required else "optional"
                print("%-24s %s" % (name, marker))
        return 0

    if args.command == "lint":
        failed = 0
        report = []
        for path in args.templates:
            issues = lint(_read(path))
            if issues:
                failed = 1
            report.append({"source": path, "issues": [issue.as_dict() for issue in issues]})
            if not args.json:
                _print_issues(issues, path)
                if not issues:
                    print("%s: ok" % path)
        if args.json:
            print(json.dumps(report, indent=2))
        return failed

    template_text = _read(args.template)
    try:
        compiled = Template(template_text)
    except TemplateSyntaxError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    values = _values(args)

    if args.command == "check":
        absent = compiled.missing(values)
        if args.json:
            print(json.dumps({"ok": not absent,
                              "missing": [{"name": n, "line": l} for n, l in absent]},
                             indent=2))
        elif absent:
            for name, line in absent:
                print("%s:%d: missing %s" % (args.template, line, name))
        else:
            print("ok: %d variable(s) satisfied" % len(compiled.variables))
        return 1 if absent else 0

    try:
        if args.partial:
            output, absent = compiled.render_partial(values)
            for name in absent:
                print("warning: %s left unrendered" % name, file=sys.stderr)
        else:
            output = compiled.render(values)
    except MissingVariables as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except TemplateError as exc:  # pragma: no cover - defensive
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print("wrote %s" % args.output, file=sys.stderr)
    else:
        sys.stdout.write(output)
        if not output.endswith("\n"):
            sys.stdout.write("\n")
    return 0
