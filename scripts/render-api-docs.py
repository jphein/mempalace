#!/usr/bin/env python3
"""Render Python API reference from mempalace source into markdown.

Walks the ``mempalace`` package with ``ast``, extracts signatures and
docstrings for public modules / classes / functions, and writes one
markdown file per module under ``website/reference/python-api/`` that
VitePress consumes directly — no plugin, no MkDocs migration.

Why custom over handsdown / pdoc / pydoc-markdown:

  - **No new deps.** Pure stdlib (``ast``, ``inspect``, ``pathlib``).
    handsdown would pull ``black``, ``jinja2``, ``typed-ast``.
  - **AST not import.** Reading source means we don't execute the
    package to generate docs — safer for CI, no side effects from
    module-level palace probes, no chromadb import storms.
  - **Markdown-native.** Output is plain GFM; VitePress consumes it
    without a plugin and the existing markdownlint job lints it.

Usage:

    scripts/render-api-docs.py            # regenerate the tree
    scripts/render-api-docs.py --check    # exit 1 if anything would change
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "mempalace"
OUTPUT_ROOT = REPO_ROOT / "website" / "reference" / "python-api"

# Skip modules that aren't part of the public Python surface integrators
# would call. Internal CLI plumbing, data files, and translation bundles
# add noise without adding value.
SKIP_DIRS = {"data", "i18n", "instructions"}
SKIP_MODULES = {
    "_stdio",
    "__main__",
}


@dataclass
class FunctionDoc:
    name: str
    signature: str
    docstring: Optional[str]
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)


@dataclass
class ClassDoc:
    name: str
    bases: list[str]
    docstring: Optional[str]
    methods: list[FunctionDoc] = field(default_factory=list)


@dataclass
class ModuleDoc:
    qualname: str
    relpath: str
    docstring: Optional[str]
    classes: list[ClassDoc] = field(default_factory=list)
    functions: list[FunctionDoc] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.classes and not self.functions and not self.docstring


_HTMLISH_RE = re.compile(r"<(?=[A-Za-z/])")


def _escape_for_vitepress(text: str) -> str:
    """Defuse Vue-template-syntax collisions in docstring prose.

    VitePress runs every ``.md`` page through Vue's template compiler.
    Two classes of docstring content crash that compiler:

      * ``<UUID>`` / ``<relative-path>`` style placeholders parse as
        opening Vue elements ("Element is missing end tag").
      * ``{"wings": N, "rooms": N}`` style example dicts parse as
        attributes with duplicate keys ("Duplicate attribute").

    We can't blanket-escape these characters because docstrings
    legitimately contain fenced code blocks and inline code that should
    render verbatim. So this function walks lines, tracks whether we're
    inside a triple-backtick fence or an inline-code span, and only
    rewrites characters in prose:

      * ``<`` followed by a letter or slash → ``&lt;``
      * ``{`` → ``&#123;`` (cheap defuse of Vue attribute parsing —
        the rendered glyph is identical to ``{``)

    Fenced code blocks and inline ``code`` spans pass through untouched.
    """
    out_lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        rebuilt: list[str] = []
        i = 0
        in_inline_code = False
        while i < len(line):
            ch = line[i]
            if ch == "`":
                in_inline_code = not in_inline_code
                rebuilt.append(ch)
                i += 1
                continue
            if in_inline_code:
                rebuilt.append(ch)
                i += 1
                continue
            if ch == "<" and i + 1 < len(line) and (
                line[i + 1].isalpha() or line[i + 1] == "/"
            ):
                rebuilt.append("&lt;")
                i += 1
                continue
            if ch == "{":
                rebuilt.append("&#123;")
                i += 1
                continue
            rebuilt.append(ch)
            i += 1
        out_lines.append("".join(rebuilt))
    return "\n".join(out_lines)


def _format_arg(arg: ast.arg) -> str:
    if arg.annotation is not None:
        return f"{arg.arg}: {ast.unparse(arg.annotation)}"
    return arg.arg


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    parts: list[str] = []

    posonly = list(args.posonlyargs)
    regular = list(args.args)
    defaults = list(args.defaults)

    all_positional = posonly + regular
    n_with_defaults = len(defaults)
    n_without_defaults = len(all_positional) - n_with_defaults

    for i, arg in enumerate(all_positional):
        rendered = _format_arg(arg)
        if i >= n_without_defaults:
            default = defaults[i - n_without_defaults]
            rendered += f" = {ast.unparse(default)}"
        parts.append(rendered)
        if posonly and i == len(posonly) - 1:
            parts.append("/")

    if args.vararg:
        parts.append(f"*{_format_arg(args.vararg)}")
    elif args.kwonlyargs:
        parts.append("*")

    for i, arg in enumerate(args.kwonlyargs):
        rendered = _format_arg(arg)
        default = args.kw_defaults[i]
        if default is not None:
            rendered += f" = {ast.unparse(default)}"
        parts.append(rendered)

    if args.kwarg:
        parts.append(f"**{_format_arg(args.kwarg)}")

    sig = f"({', '.join(parts)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    return sig


def _extract_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionDoc:
    return FunctionDoc(
        name=node.name,
        signature=_format_signature(node),
        docstring=ast.get_docstring(node),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        decorators=[ast.unparse(d) for d in node.decorator_list],
    )


def _extract_class(node: ast.ClassDef) -> ClassDoc:
    methods: list[FunctionDoc] = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name.startswith("_") and child.name != "__init__":
                continue
            methods.append(_extract_function(child))
    return ClassDoc(
        name=node.name,
        bases=[ast.unparse(b) for b in node.bases],
        docstring=ast.get_docstring(node),
        methods=methods,
    )


def _module_qualname(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def parse_module(path: Path) -> Optional[ModuleDoc]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None

    classes: list[ClassDoc] = []
    functions: list[FunctionDoc] = []

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            classes.append(_extract_class(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            functions.append(_extract_function(node))

    return ModuleDoc(
        qualname=_module_qualname(path),
        relpath=str(path.relative_to(REPO_ROOT)),
        docstring=ast.get_docstring(tree),
        classes=classes,
        functions=functions,
    )


def _render_function(fn: FunctionDoc, heading_level: int) -> str:
    h = "#" * heading_level
    prefix = "async def" if fn.is_async else "def"
    parts = [f"{h} `{fn.name}`", ""]
    parts.append("```python")
    parts.append(f"{prefix} {fn.name}{fn.signature}")
    parts.append("```")
    parts.append("")
    if fn.docstring:
        parts.append(_escape_for_vitepress(dedent(fn.docstring).strip()))
        parts.append("")
    return "\n".join(parts)


def _render_class(cls: ClassDoc) -> str:
    base_str = f"({', '.join(cls.bases)})" if cls.bases else ""
    parts = [f"### `class {cls.name}{base_str}`", ""]
    if cls.docstring:
        parts.append(_escape_for_vitepress(dedent(cls.docstring).strip()))
        parts.append("")
    for method in cls.methods:
        parts.append(_render_function(method, heading_level=4))
    return "\n".join(parts)


def _edit_link(relpath: str) -> str:
    return f"https://github.com/techempower-org/mempalace/blob/main/{relpath}"


def render_module(mod: ModuleDoc) -> str:
    title = mod.qualname
    lines = [f"# `{title}`", ""]
    lines.append(f"Source: [`{mod.relpath}`]({_edit_link(mod.relpath)})")
    lines.append("")
    if mod.docstring:
        lines.append(_escape_for_vitepress(dedent(mod.docstring).strip()))
        lines.append("")
    if mod.classes:
        lines.append("## Classes")
        lines.append("")
        for cls in mod.classes:
            lines.append(_render_class(cls))
    if mod.functions:
        lines.append("## Functions")
        lines.append("")
        for fn in mod.functions:
            lines.append(_render_function(fn, heading_level=3))
    return "\n".join(lines).rstrip() + "\n"


def collect_modules() -> list[ModuleDoc]:
    modules: list[ModuleDoc] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel_parts = path.relative_to(PACKAGE_ROOT).parts
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        stem = path.stem
        if stem in SKIP_MODULES:
            continue
        if stem == "__init__":
            # Skip the root mempalace/__init__.py entirely — its docstring
            # already lives at the top of the index page, and it exposes
            # nothing through public symbols.
            if path.parent == PACKAGE_ROOT:
                continue
            mod = parse_module(path)
            if mod and not mod.is_empty:
                modules.append(mod)
            continue
        mod = parse_module(path)
        if mod is None:
            continue
        if mod.is_empty:
            continue
        modules.append(mod)
    return modules


def _output_path_for(mod: ModuleDoc) -> Path:
    qualname = mod.qualname
    if qualname == "mempalace":
        return OUTPUT_ROOT / "index.md"
    rel = qualname.removeprefix("mempalace.").replace(".", "/")
    return OUTPUT_ROOT / f"{rel}.md"


def render_index(modules: list[ModuleDoc]) -> str:
    lines = [
        "# Python API",
        "",
        "Auto-generated reference for the `mempalace` Python package.",
        "Source of truth lives in the docstrings under "
        "[`mempalace/`](https://github.com/techempower-org/mempalace/tree/main/mempalace) — "
        "edit there, not here. Regenerate with `scripts/render-api-docs.py`.",
        "",
        "For task-oriented overviews of the main interfaces (search, memory stack, "
        "knowledge graph, palace graph, AAAK dialect, configuration), see "
        "[Python API Overview](/reference/python-api).",
        "",
        "## Modules",
        "",
    ]

    grouped: dict[str, list[ModuleDoc]] = {}
    for mod in modules:
        if mod.qualname == "mempalace":
            continue
        parts = mod.qualname.split(".")
        group = parts[1] if len(parts) > 2 else "Top-level modules"
        grouped.setdefault(group, []).append(mod)

    for group in sorted(grouped, key=lambda g: (g != "Top-level modules", g)):
        lines.append(f"### {group}")
        lines.append("")
        for mod in grouped[group]:
            link = _output_path_for(mod).relative_to(OUTPUT_ROOT).with_suffix("")
            summary = ""
            if mod.docstring:
                first_line = dedent(mod.docstring).strip().split("\n", 1)[0]
                summary = f" — {_escape_for_vitepress(first_line)}"
            lines.append(f"- [`{mod.qualname}`](./{link}){summary}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_sidebar(modules: list[ModuleDoc]) -> list[dict]:
    """Build a VitePress sidebar fragment as plain Python data.

    Consumed by ``website/.vitepress/api-sidebar.json`` which is imported
    into ``config.mts``. Keeps the hand-maintained sidebar separate from
    the generated one.
    """
    grouped: dict[str, list[ModuleDoc]] = {}
    for mod in modules:
        if mod.qualname == "mempalace":
            continue
        parts = mod.qualname.split(".")
        group = parts[1] if len(parts) > 2 else "Top-level modules"
        grouped.setdefault(group, []).append(mod)

    items: list[dict] = []
    for group in sorted(grouped, key=lambda g: (g != "Top-level modules", g)):
        children = []
        for mod in grouped[group]:
            link = _output_path_for(mod).relative_to(OUTPUT_ROOT).with_suffix("")
            children.append({
                "text": mod.qualname,
                "link": f"/reference/python-api/{link}",
            })
        items.append({
            "text": group,
            "collapsed": True,
            "items": children,
        })
    return items


def write_outputs(modules: list[ModuleDoc]) -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    outputs[OUTPUT_ROOT / "index.md"] = render_index(modules)
    for mod in modules:
        if mod.qualname == "mempalace":
            continue
        outputs[_output_path_for(mod)] = render_module(mod)

    import json

    sidebar_path = REPO_ROOT / "website" / ".vitepress" / "api-sidebar.json"
    outputs[sidebar_path] = json.dumps(render_sidebar(modules), indent=2) + "\n"
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any rendered file differs from disk (CI idempotency).",
    )
    args = parser.parse_args()

    modules = collect_modules()
    outputs = write_outputs(modules)

    drift: list[Path] = []
    for path, content in outputs.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing == content:
            continue
        if args.check:
            drift.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    if not args.check:
        existing_files = {
            p for p in OUTPUT_ROOT.rglob("*.md") if p.is_file()
        }
        expected_files = {
            p for p in outputs if p.suffix == ".md" and OUTPUT_ROOT in p.parents
        } | {OUTPUT_ROOT / "index.md"}
        for stale in existing_files - expected_files:
            stale.unlink()

    if args.check:
        if drift:
            print("API doc drift detected:", file=sys.stderr)
            for path in drift:
                print(f"  {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            print(
                "\nRun `scripts/render-api-docs.py` to regenerate.",
                file=sys.stderr,
            )
            return 1
        print(f"OK — {len(outputs)} files in sync.")
        return 0

    print(f"Rendered {len(outputs)} files into {OUTPUT_ROOT.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
