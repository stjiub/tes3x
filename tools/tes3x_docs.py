#!/usr/bin/env python3
"""Check the documentation and render its index.

docs/nav.toml lists every page in docs/ by section; docs/index.md is generated from it. The check
also requires one title per page and resolves every relative link and anchor in the README,
docs/ and patches/.
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
NAV = DOCS / "nav.toml"
INDEX = DOCS / "index.md"

LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def prose(text):
    """The lines outside fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def slug(heading):
    """The anchor a Markdown renderer gives a heading."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text.replace("`", ""))
    return text.replace(" ", "-")


def anchors(path):
    seen, found = {}, set()
    for line in prose(path.read_text(encoding="utf-8")):
        match = re.match(r"#{1,6} (.+)", line)
        if match:
            base = slug(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if not count else f"{base}-{count}")
    return found


def titles(path):
    return [line[2:].strip() for line in prose(path.read_text(encoding="utf-8"))
            if line.startswith("# ")]


def first_sentence(path):
    """The first sentence of the first paragraph after the title, links reduced to text."""
    paragraph, started = [], False
    for line in prose(path.read_text(encoding="utf-8")):
        if line.startswith("# "):
            started = True
            continue
        if not started:
            continue
        if not line.strip():
            if paragraph:
                break
            continue
        if line.startswith(("#", "|", "-", "*", ">", "<")) or re.match(r"\d+\. ", line):
            if paragraph:
                break
            continue
        paragraph.append(line.strip())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", " ".join(paragraph))
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text


def read_nav(path=NAV):
    with open(path, "rb") as stream:
        nav = tomllib.load(stream)
    return nav.get("section", []), nav.get("summary", {})


def render_index(nav_path=NAV, docs=DOCS):
    sections, summaries = read_nav(nav_path)
    lines = [
        "# TES3X documentation",
        "",
        "Generated from [`nav.toml`](nav.toml) by `tools/tes3x_docs.py --write`; edit that file, "
        "not this one.",
    ]
    for section in sections:
        lines += ["", f"## {section['title']}", ""]
        for page in section["pages"]:
            path = (docs / page).resolve()
            title = (titles(path) or [page])[0]
            summary = summaries.get(page) or first_sentence(path)
            lines.append(f"- [{title}]({page}): {summary}")
    return "\n".join(lines) + "\n"


def link_problems(files, root=ROOT):
    problems = []
    for path in files:
        where = path.relative_to(root).as_posix()
        for line in prose(path.read_text(encoding="utf-8")):
            for match in LINK.finditer(re.sub(r"`[^`]*`", "", line)):
                target = match.group(2)
                if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
                    continue
                file_part, _, anchor = target.partition("#")
                linked = (path.parent / file_part).resolve() if file_part else path
                if not linked.exists():
                    problems.append(f"{where}: link to missing {target}")
                elif anchor and linked.suffix == ".md" and anchor not in anchors(linked):
                    problems.append(f"{where}: no heading for #{anchor} in {file_part or where}")
    return problems


def problems(root=ROOT):
    docs = root / "docs"
    found = []
    sections, summaries = read_nav(docs / "nav.toml")
    listed = [page for section in sections for page in section.get("pages", [])]
    for page in sorted({p for p in listed if listed.count(p) > 1}):
        found.append(f"docs/nav.toml: {page} is listed more than once")
    for page in listed:
        if not (docs / page).is_file():
            found.append(f"docs/nav.toml: {page} does not exist")
    for page in set(summaries) - set(listed):
        found.append(f"docs/nav.toml: summary for unlisted {page}")
    pages = sorted(path.name for path in docs.glob("*.md") if path.name != "index.md")
    for page in pages:
        if page not in listed:
            found.append(f"docs/{page}: not listed in docs/nav.toml")
    files = [root / "README.md"] + sorted(docs.glob("*.md")) + sorted((root / "patches").glob("*.md"))
    for path in files:
        count = len(titles(path))
        if count != 1:
            found.append(f"{path.relative_to(root).as_posix()}: {count} titles, wants one")
    found += link_problems(files, root)
    index = docs / "index.md"
    if not index.is_file() or index.read_text(encoding="utf-8") != render_index(
            docs / "nav.toml", docs):
        found.append("docs/index.md: out of date; run tools/tes3x_docs.py --write")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="regenerate docs/index.md first")
    args = ap.parse_args(argv)
    if args.write:
        INDEX.write_text(render_index(), encoding="utf-8", newline="\n")
        print(f"wrote {INDEX.relative_to(ROOT).as_posix()}")
    found = problems()
    if found:
        sys.exit("\n".join(found))


if __name__ == "__main__":
    main()
