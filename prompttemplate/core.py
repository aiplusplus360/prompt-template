"""Templates for prompts that fail loudly on a missing variable.

The failure mode this library exists to prevent: a prompt is rendered with
``str.format``, a variable is renamed three files away, and production starts
sending the literal text ``Hello {user_name}`` to a model. Nothing raises, the
model answers confidently, and the bug is discovered from a support ticket.

Design choices that follow from that:

* **Only ``{{name}}`` is a placeholder.** JSON payloads, code samples, and
  ``{}`` in prose pass through untouched. There is no escape-the-braces tax.
* **Every missing variable is reported at once, with a line number.** Fixing
  prompts one ``KeyError`` at a time is not a workflow.
* **Rendering is strict by default.** ``{{name?}}`` opts a variable out.

    >>> t = Template("Hi {{name}}, your plan is {{plan | upper}}.")
    >>> t.render({"name": "Ada", "plan": "pro"})
    'Hi Ada, your plan is PRO.'
    >>> t.variables
    ('name', 'plan')
    >>> t.required
    ('name', 'plan')

Braces in the payload are not syntax:

    >>> t = Template('Return JSON like {"ok": true} for {{subject}}.')
    >>> t.render({"subject": "orders"})
    'Return JSON like {"ok": true} for orders.'

A missing variable is a hard error that lists the whole set:

    >>> try:
    ...     Template("{{a}} and {{b}}").render({"a": 1})
    ... except MissingVariables as err:
    ...     print(err)
    1 missing variable: b (line 1)
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "Template",
    "Issue",
    "TemplateError",
    "TemplateSyntaxError",
    "MissingVariables",
    "compile_template",
    "render",
    "lint",
]

_OPEN = "{{"
_CLOSE = "}}"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)


class TemplateError(ValueError):
    """Base class for every prompt-template failure."""


class TemplateSyntaxError(TemplateError):
    """A placeholder could not be parsed. Carries a 1-based position."""

    def __init__(self, message: str, line: int = 1, column: int = 1) -> None:
        super().__init__("%s (line %d, column %d)" % (message, line, column))
        self.message = message
        self.line = line
        self.column = column


class MissingVariables(TemplateError):
    """One or more required variables were not supplied.

    >>> err = MissingVariables([("plan", 3)])
    >>> err.missing
    [('plan', 3)]
    >>> str(err)
    '1 missing variable: plan (line 3)'
    """

    def __init__(self, missing: Sequence[Tuple[str, int]]) -> None:
        self.missing = list(missing)
        count = len(self.missing)
        noun = "variable" if count == 1 else "variables"
        detail = ", ".join(
            "%s (line %d)" % (name, line) if line else name
            for name, line in self.missing
        )
        super().__init__("%d missing %s: %s" % (count, noun, detail))


class Issue:
    """A lint finding. ``code`` is stable, ``message`` is for humans."""

    __slots__ = ("code", "line", "column", "message")

    def __init__(self, code: str, message: str, line: int = 1, column: int = 1) -> None:
        self.code = code
        self.line = line
        self.column = column
        self.message = message

    def __repr__(self) -> str:
        return "Issue(%r, line=%d, column=%d)" % (self.code, self.line, self.column)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Issue):
            return NotImplemented
        return (self.code, self.line, self.column) == (other.code, other.line, other.column)

    def __hash__(self) -> int:
        return hash((self.code, self.line, self.column))

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "line": self.line, "column": self.column,
                "message": self.message}


def _position(text: str, index: int) -> Tuple[int, int]:
    """Return the 1-based (line, column) of ``index`` in ``text``."""
    line = text.count("\n", 0, index) + 1
    last_newline = text.rfind("\n", 0, index)
    return line, index - last_newline


def _apply(name: str, value: Any, filters: Sequence[str]) -> str:
    text = value if isinstance(value, str) else _stringify(value)
    for spec in filters:
        head, _, argument = spec.partition(":")
        head = head.strip()
        if head == "upper":
            text = text.upper()
        elif head == "lower":
            text = text.lower()
        elif head == "trim":
            text = text.strip()
        elif head == "json":
            text = json.dumps(text, ensure_ascii=False)
        elif head == "quote":
            text = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
        elif head == "default":
            if text == "":
                text = argument
        elif head == "truncate":
            limit = int(argument or 80)
            if len(text) > limit:
                text = text[: limit - 1].rstrip() + "\u2026"
        else:  # pragma: no cover - guarded during compilation
            raise TemplateSyntaxError("unknown filter %r" % head)
    return text


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=isinstance(value, dict))
    return str(value)


def _lookup(mapping: Mapping[str, Any], dotted: str) -> Any:
    current: Any = mapping
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(dotted)
        current = current[part]
    return current


class Template:
    """A parsed prompt template.

    Compilation happens once; rendering is then allocation-only.

        >>> t = Template("{{greeting}}, {{name}}!")
        >>> t.optional
        ()
        >>> t.render({"greeting": "Hello", "name": "world"})
        'Hello, world!'
    """

    def __init__(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("template text must be str, not %s" % type(text).__name__)
        self.text = text
        self._parts: List[Tuple[str, str, str, List[str], int]] = []
        self._names: List[Tuple[str, int]] = []
        self._optional: List[str] = []
        self._compile()

    def _compile(self) -> None:
        text = self.text
        cursor = 0
        literal: List[str] = []
        while cursor < len(text):
            start = text.find(_OPEN, cursor)
            if start == -1:
                literal.append(text[cursor:])
                break
            literal.append(text[cursor:start])
            end = text.find(_CLOSE, start + 2)
            if end == -1:
                line, column = _position(text, start)
                raise TemplateSyntaxError("unclosed placeholder", line, column)
            body = text[start + 2 : end]
            line, column = _position(text, start)
            name, filters, optional = self._parse_body(body, line, column)
            self._parts.append(("".join(literal), name, body, filters, line))
            literal = []
            self._names.append((name, line))
            if optional and name not in self._optional:
                self._optional.append(name)
            cursor = end + 2
        self._tail = "".join(literal)
        if not self._parts:
            self._tail = text

    def _parse_body(self, body: str, line: int, column: int) -> Tuple[str, List[str], bool]:
        chunks = [chunk.strip() for chunk in body.split("|")]
        head = chunks[0]
        optional = head.endswith("?")
        if optional:
            head = head[:-1].strip()
        if not head:
            raise TemplateSyntaxError("empty placeholder", line, column)
        if not _NAME.match(head):
            raise TemplateSyntaxError("invalid variable name %r" % head, line, column)
        filters = chunks[1:]
        for spec in filters:
            filter_name = spec.partition(":")[0].strip()
            if filter_name not in {"upper", "lower", "trim", "json", "quote",
                                   "default", "truncate"}:
                raise TemplateSyntaxError("unknown filter %r" % filter_name, line, column)
        return head, filters, optional

    @property
    def variables(self) -> Tuple[str, ...]:
        """Every placeholder, in first-appearance order, without duplicates."""
        seen: List[str] = []
        for name, _ in self._names:
            if name not in seen:
                seen.append(name)
        return tuple(seen)

    @property
    def required(self) -> Tuple[str, ...]:
        """Placeholders that must be supplied."""
        return tuple(name for name in self.variables if name not in self._optional)

    @property
    def optional(self) -> Tuple[str, ...]:
        """Placeholders marked with a trailing ``?``."""
        return tuple(self._optional)

    def missing(self, values: Mapping[str, Any]) -> List[Tuple[str, int]]:
        """Return ``(name, line)`` for each required variable that is absent."""
        found: List[Tuple[str, int]] = []
        seen = set()
        for name, line in self._names:
            if name in seen or name in self._optional:
                continue
            seen.add(name)
            try:
                _lookup(values, name)
            except KeyError:
                found.append((name, line))
        return found

    def render(self, values: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> str:
        """Render strictly; raise :class:`MissingVariables` if anything is absent."""
        merged = dict(values or {})
        merged.update(kwargs)
        absent = self.missing(merged)
        if absent:
            raise MissingVariables(absent)
        out: List[str] = []
        for literal, name, _body, filters, _line in self._parts:
            out.append(literal)
            try:
                raw = _lookup(merged, name)
            except KeyError:
                raw = ""
            out.append(_apply(name, raw, filters))
        out.append(self._tail)
        return "".join(out)

    def render_partial(self, values: Optional[Mapping[str, Any]] = None,
                       **kwargs: Any) -> Tuple[str, List[str]]:
        """Render with missing values left as ``{{name}}`` and report them.

        >>> Template("{{a}}/{{b}}").render_partial({"a": "x"})
        ('x/{{b}}', ['b'])
        """
        merged = dict(values or {})
        merged.update(kwargs)
        absent = [name for name, _ in self.missing(merged)]
        out: List[str] = []
        for literal, name, body, filters, _line in self._parts:
            out.append(literal)
            try:
                raw = _lookup(merged, name)
            except KeyError:
                out.append(_OPEN + body + _CLOSE)
                continue
            out.append(_apply(name, raw, filters))
        out.append(self._tail)
        return "".join(out), absent

    def render_map(self, values: Iterable[Mapping[str, Any]]) -> List[str]:
        """Render many rows, reporting the first row that is missing a value.

        >>> Template("{{n}}").render_map([{"n": 1}, {"n": 2}])
        ['1', '2']
        """
        return [self.render(row) for row in values]

    def __repr__(self) -> str:
        return "Template(%r)" % self.text


def compile_template(text: str) -> Template:
    """Parse ``text`` once and reuse the result.

    >>> compile_template("{{x}}").variables
    ('x',)
    """
    return Template(text)


def render(text: str, values: Optional[Mapping[str, Any]] = None,
           **kwargs: Any) -> str:
    """One-shot render for scripts that do not cache templates."""
    return Template(text).render(values, **kwargs)


def lint(text: str) -> List[Issue]:
    """Report problems a strict compile would raise, without raising.

    >>> [issue.code for issue in lint("Hi {{user_name}}")]
    []
    >>> [issue.code for issue in lint("Hi {{ user name }}")]
    ['invalid-name']
    """
    issues: List[Issue] = []
    cursor = 0
    while True:
        start = text.find(_OPEN, cursor)
        if start == -1:
            break
        end = text.find(_CLOSE, start + 2)
        line, column = _position(text, start)
        if end == -1:
            issues.append(Issue("unclosed", "placeholder is never closed", line, column))
            break
        body = text[start + 2 : end]
        head = body.split("|")[0].strip().rstrip("?").strip()
        if not head:
            issues.append(Issue("empty", "placeholder has no variable name", line, column))
        elif not _NAME.match(head):
            issues.append(Issue("invalid-name",
                                "variable name %r cannot be looked up" % head,
                                line, column))
        for spec in body.split("|")[1:]:
            filter_name = spec.partition(":")[0].strip()
            if filter_name not in {"upper", "lower", "trim", "json", "quote",
                                   "default", "truncate"}:
                issues.append(Issue("unknown-filter",
                                    "no such filter %r" % filter_name, line, column))
        cursor = end + 2
    return issues
