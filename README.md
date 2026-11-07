# prompt-template

Templates for prompts that fail loudly on a missing variable.

```console
$ prompt-template render prompt.txt --var name=Ada --var plan=pro
Hi Ada, plan PRO
$ prompt-template render prompt.txt --var name=Ada
error: 1 missing variable: plan (line 1)
$ echo $?
1
```

## The bug this prevents

Prompts live in files, get `.format()`-ed at runtime, and quietly lose their
variables in a refactor:

```python
PROMPT.format(**payload)   # KeyError: 'plan' — or worse, no error at all
```

Strict `.format` also forces you to escape every JSON brace in every few-shot
example, and the escaping is *wrong* often enough that the model sees `{{` in
production. `prompt-template` takes a different position:

1. **Only `{{name}}` is syntax.** `{"ok": true}`, `{}` in prose, and Python code
   blocks pass through byte-for-byte. No escaping tax, no doubled braces.
2. **Missing variables are aggregated.** You get all of them with line numbers in
   one exception, so a rename is one pass of work rather than one pass per field.
3. **Rendering is pure.** Same template plus same values always produces the same
   string, with no locale or hash-order dependence (dict values are serialised
   with sorted keys).

## Install

```console
git clone https://github.com/aiplusplus360/prompt-template
cd prompt-template
python -m pip install -e .
```

Python 3.9+. Zero dependencies.

## Template syntax

| Syntax | Meaning |
| --- | --- |
| `{{name}}` | required variable |
| `{{name?}}` | optional; renders as `""` when absent |
| `{{user.name}}` | dotted lookup into nested mappings |
| `{{name \| upper}}` | filter chain, left to right |

Filters: `upper`, `lower`, `trim`, `json`, `quote`, `truncate:N`, `default:VALUE`.

```text
Summarise this ticket in {{words | default:40}} words.

Customer: {{customer.name}}
Plan: {{order.plan | upper}}
Metadata: {{order.meta | json}}
```

```console
$ prompt-template render prompt.txt \
    --var 'customer={"name":"Ada"}' \
    --var order.plan=pro \
    --var 'order.meta={"seats":3}'
Summarise this ticket in 40 words.

Customer: Ada
Plan: PRO
Metadata: "{\"seats\": 3}"
```

## CLI

```console
prompt-template variables prompt.txt --json   # declare the contract for CI
prompt-template check prompt.txt --var-file ci-vars.json
prompt-template render prompt.txt --partial   # leave {{name}} in place
prompt-template lint prompts/*.txt --json
```

`check` is the one to run in CI: it exits `1` when the documented variable set no
longer covers the template, which is the earliest point a rename can be caught.

```json
{ "ok": false, "missing": [{ "name": "plan", "line": 1 }] }
```

`lint` never raises, so it can be pointed at a directory of templates that are
still being written.

## Library

```python
from prompttemplate import Template, lint

template = Template("Hi {{name}}, plan {{plan | upper}}")
template.variables        # ('name', 'plan')
template.required         # ('name', 'plan')
template.render({"name": "Ada", "plan": "pro"})
# 'Hi Ada, plan PRO'

text, missing = template.render_partial({"name": "Ada"})
# ('Hi Ada, plan {{plan | upper}}', ['plan'])
```

Compile once, render many:

```python
compiled = [Template(path.read_text()) for path in sorted(prompts.glob("*.txt"))]
for row in dataset:
    prompt = compiled[0].render(row)
```

## Errors

| Exception | When | Carries |
| --- | --- | --- |
| `TemplateSyntaxError` | unclosed / empty / invalid name / unknown filter | `line`, `column` |
| `MissingVariables` | strict render without a required value | `missing` as `[(name, line)]` |
| `TemplateError` | base class for both | — |

The message is designed to be read in a log line:

```text
2 missing variables: plan (line 1), owner (line 4)
```

## Why not Jinja2

Jinja2 is excellent and does a hundred things this does not. In exchange it is a
dependency, it has its own escaping rules for JSON-ish prompt content, and its
error for a missing variable is often a silent `Undefined`. For prompt files
managed alongside evaluation datasets, a 300-line strict renderer is enough and
is auditable in one sitting.

## Performance

Compilation is a single left-to-right scan. Rendering builds one list and calls
`"".join` once, so a 4 KB prompt with 20 placeholders renders in microseconds —
the reason prompt assembly never shows up in a latency profile.

## Testing

```console
python -m unittest discover -s tests -v   # 41 tests
python -m doctest prompttemplate/core.py
```

## License

MIT.
