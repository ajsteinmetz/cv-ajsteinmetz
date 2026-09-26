#!/usr/bin/env python3
"""Render the CV, website pages, and GitHub profile README from data/*.yml.

    python build.py            # write every output
    python build.py --pdf      # ...and compile cv_ajsteinmetz.pdf
    python build.py --check    # exit 1 if any output is out of date (writes nothing)

Outputs (sibling repos are skipped with a warning if missing):
    cv_ajsteinmetz.tex                          <- templates/cv.tex
    ../ajsteinmetz.github.io/{cv,publications,talks,teaching}.md  <- templates/web/*.md
    ../ajsteinmetz.github.io/index.md           marker block "recent-publications"
    ../ajsteinmetz/README.md                    marker block "papers"

Fully generated files are overwritten; marker files only have the text between
<!-- BEGIN GENERATED: name --> and <!-- END GENERATED: name --> replaced, using
templates/blocks/<name>.md.

All text in data/ is plain Unicode with *italic* as the only inline markup. The
renderer for each format applies the style conventions (en dashes, HTML entities
vs. LaTeX accent macros, ACT\\slash HEBUT, bolding "Steinmetz, A.", ...).
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TEMPLATES = ROOT / "templates"
SELF = "Steinmetz, A."

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
SEASONS = {"Spring": 0, "Summer": 1, "Fall": 2}

TEX_CHARS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''",
    "¥": r"\textyen{}", "á": r"\'{a}", "é": r"\'{e}", "í": r"\'{i}", "ó": r"\'{o}",
    "ö": r"\"{o}", "ő": r"\H{o}", "ü": r"\"{u}", "ł": r"\l{}",
}
HTML_NAMED = {"–": "&ndash;", "—": "&mdash;", "¥": "&yen;", "‘": "'", "’": "'", "“": '"', "”": '"'}


# --------------------------------------------------------------------------- dates

def ym(value):
    """Parse YYYY, YYYY-MM, YYYY-MM-DD (string or YAML date) -> (year, month|None, day|None)."""
    if isinstance(value, datetime.date):
        return value.year, value.month, value.day
    parts = [int(p) for p in str(value).split("-")]
    return tuple(parts + [None] * (3 - len(parts)))


def term_key(term):
    season, year = term.split()
    return int(year), SEASONS[season]


def compress_years(years):
    """{2020, 2021, 2023} -> [(2020, 2021), (2023, 2023)]"""
    runs = []
    for y in sorted(set(years)):
        if runs and y == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], y)
        else:
            runs.append((y, y))
    return runs


# --------------------------------------------------------------------------- renderer

class Renderer:
    """Format-specific text rendering. kind is 'tex', 'md' (Markdown), or 'html' (HTML fragments)."""

    def __init__(self, kind, data):
        self.kind = kind
        self.data = data
        self.fn_letters = {}
        self.fn_pending = []

    # ---- primitives

    def esc(self, s):
        s = str(s)
        if self.kind == "tex":
            out = "".join(TEX_CHARS.get(c, c) for c in s)
            bad = sorted({c for c in out if ord(c) > 127})
            if bad:
                raise ValueError(f"no LaTeX mapping for {bad!r} in {s!r}; add it to TEX_CHARS")
            out = out.replace("ACT/HEBUT", r"ACT\slash HEBUT")
            return re.sub(r"\bProf\. ", "Prof.~", out)
        out = []
        for c in s:
            if c in HTML_NAMED:
                out.append(HTML_NAMED[c])
            elif ord(c) > 127:
                out.append(f"&#{ord(c)};")
            elif c == "&" and self.kind == "html":
                out.append("&amp;")
            else:
                out.append(c)
        return "".join(out)

    def text(self, s):
        """Escape s, converting *italic* markup."""
        pieces = re.split(r"\*([^*]+)\*", str(s))
        return "".join(self.it(p) if i % 2 else self.esc(p) for i, p in enumerate(pieces))

    def it(self, s):
        s = self.esc(s)
        return {"tex": r"\textit{%s}", "md": "*%s*", "html": "<em>%s</em>"}[self.kind] % s

    def bold(self, rendered):
        return {"tex": r"\textbf{%s}", "md": "**%s**", "html": "<strong>%s</strong>"}[self.kind] % rendered

    def link(self, rendered, url, breakable=False):
        if self.kind == "tex":
            u = url.replace("%", r"\%").replace("#", r"\#")
            return rf"\href{{{u}}}{{{rendered}}}"
        if self.kind == "md":
            return f"[{rendered}]({url})"
        return f'<a href="{url.replace("&", "&amp;")}">{rendered}</a>'

    def ident(self, label, url):
        """Identifier link (DOI, arXiv, ...). In LaTeX, \\nolinkurl lets long ones break at punctuation."""
        if self.kind == "tex":
            u = url.replace("%", r"\%").replace("#", r"\#")
            return rf"\href{{{u}}}{{\nolinkurl{{{label}}}}}"
        return self.link(self.esc(label), url)

    def ndash(self):
        return {"tex": "--", "md": "&ndash;", "html": "&ndash;"}[self.kind]

    def nbsp(self):
        return "~" if self.kind == "tex" else " "

    # ---- sentences

    @staticmethod
    def _plain(rendered):
        s = re.sub(r"\]\([^)]*\)", "]", rendered)           # md link targets
        s = re.sub(r"<[^>]+>|\\[a-zA-Z]+|[{}*\[\]]", "", s)  # tags, macros, braces, md emphasis
        return s.rstrip()

    def sentences(self, parts):
        """Join rendered parts, ending each with a period unless it already ends in . ? !"""
        out = []
        for p in parts:
            if not p:
                continue
            out.append(p if self._plain(p)[-1:] in ".?!" else p + ".")
        return " ".join(out)

    # ---- dates

    def date_long(self, d):
        y, m, day = ym(d)
        return f"{MONTHS[m - 1]} {day}, {y}" if day else f"{MONTHS[m - 1]} {y}"

    def date_apa(self, d):
        y, m, day = ym(d)
        return f"({y}, {MONTHS[m - 1]} {day})"

    def mon_year(self, d):
        y, m, _ = ym(d)
        return f"{MONTHS[m - 1][:3]}{self.nbsp()}{y}"

    def span(self, start, end=None, fmt=None):
        """Year or month span: 2024--2025, Jan~2026--present, 2025 (start == end)."""
        fmt = fmt or (lambda v: str(v))
        if end is not None and str(end) == str(start):
            return fmt(start)
        return f"{fmt(start)}{self.ndash()}{fmt(end) if end is not None else 'present'}"

    def year_runs(self, years):
        return ", ".join(str(a) if a == b else f"{a}{self.ndash()}{b}" for a, b in compress_years(years))

    # ---- footnotes

    def fn(self, ids):
        """Superscript letter(s) for footnote id(s); assigns letters on first use."""
        if not ids:
            return ""
        ids = [ids] if isinstance(ids, str) else ids
        letters = []
        for i in ids:
            if i not in self.data["footnotes"]:
                raise KeyError(f"unknown footnote id {i!r}")
            if i not in self.fn_letters:
                self.fn_letters[i] = chr(ord("a") + len(self.fn_letters))
                self.fn_pending.append(i)
            letters.append(self.fn_letters[i])
        mark = ",".join(sorted(letters))
        return rf"\(^{{\rm {mark}}}\)" if self.kind == "tex" else f"<sup>{mark}</sup>"

    def fnlist(self):
        """Footnotes first used since the previous fnlist() call."""
        pending = list(self.fn_pending)
        self.fn_pending.clear()  # in place: the md and html renderers of a page share this list
        if not pending:
            return ""
        notes = [self.text(self.data["footnotes"][i]) for i in pending]
        if self.kind == "tex":
            items = "\n".join(rf"    \item[\(^{{\rm {self.fn_letters[i]}}}\)] {{\small {n}}}" for i, n in zip(pending, notes))
            return "\\begin{itemize}[leftmargin=*,nosep]\n" + items + "\n\\end{itemize}"
        start = ord(self.fn_letters[pending[0]]) - ord("a") + 1
        items = "\n".join(f"  <li>{n}</li>" for n in notes)
        return f'<ol type="a" start="{start}">\n{items}\n</ol>'

    # ---- people

    def authors(self, s, presenter=None, coauthor=False):
        names = [n.strip() for n in s.split(";")]
        out = []
        for n in names:
            r = self.esc(n)
            if self.kind == "tex":
                r = re.sub(r"(?<=[A-Z]\.) (?=[A-Z]\.)", "~", r)
            if n == SELF:
                r = self.bold(r)
            if n == presenter:
                r += " (presenter)"
            out.append(r)
        joined = ", ".join(out)
        if coauthor:
            joined += f" (coauthor: {self.bold(self.esc(SELF))})"
        return joined

    # ---- publications

    def pub_ids(self, p):
        ids = []
        if p.get("doi"):
            ids.append(self.ident(p["doi"], f"https://doi.org/{p['doi']}"))
        if p.get("hdl"):
            ids.append(self.ident(f"hdl:{p['hdl']}", f"http://hdl.handle.net/{p['hdl']}"))
        if p.get("url"):
            ids.append(self.ident(p["url"], p["url"]))
        if p.get("arxiv"):
            ids.append(self.ident(f"arXiv:{p['arxiv']}", f"https://arxiv.org/abs/{p['arxiv']}"))
        if p.get("repo"):
            ids.append(self.ident(f"github:{p['repo']}", f"https://github.com/ajsteinmetz/{p['repo']}"))
        return ids

    def pub_source(self, p):
        """'*Phys. Rev. D* 108, 123522 (2023)' and friends."""
        t, y = p["type"], p["year"]
        if t == "in-prep":
            return f"In preparation ({y})"
        if t in ("report", "dissertation"):
            return f"{self.text(p['venue'])}, {y}"
        venue = self.it(p["venue"])
        if p.get("status") == "submitted":
            return f"Submitted to {venue} ({y})"
        loc = self.esc(p.get("pages") or p.get("article") or "")
        if p.get("volume") is not None:
            return f"{venue} {p['volume']}, {loc} ({y})"
        if p.get("pages"):
            return f"{venue}, pp.{self.nbsp()}{loc} ({y})"
        return f"{venue} ({y})"

    def cite(self, p):
        head = self.authors(p["authors"], coauthor=p.get("coauthor"))
        if self._plain(head)[-1:] != ".":
            head += "."
        return head + " " + self.sentences([self.text(p["title"]), self.pub_source(p), *self.pub_ids(p)])

    def pub_short_venue(self, p):
        """Venue only, for the README list: '*Phys. Rev. D* (2023)'."""
        if p["type"] == "dissertation":
            return f"Ph.D. dissertation ({p['year']})"
        if p.get("status") == "submitted":
            return f"Submitted to {self.it(p['venue'])} ({p['year']})"
        return f"{self.it(p['venue'])} ({p['year']})"

    def badge_doi(self, p):
        return p.get("doi") or (f"10.48550/arXiv.{p['arxiv']}" if p.get("arxiv") else None)

    # ---- talks

    def talk(self, t):
        head = self.authors(t["authors"], presenter=t.get("presenter"))
        where = ", ".join(x for x in [
            self.it(t["event"]) if t.get("event") else None,
            self.text(t["host"]) if t.get("host") else None,
            self.text(t["place"]) if t.get("place") else None,
            self.date_long(t["date"]),
        ] if x)
        doi = t.get("doi")
        parts = [self.text(t["title"]), where, self.text(t["note"]) if t.get("note") else None,
                 self.ident(doi, f"https://doi.org/{doi}") if doi else None]
        return head + " " + self.sentences(parts)

    # ---- teaching

    def students(self, c):
        return f"Est. {c['students']}" if c.get("estimated") else str(c["students"])

    def teaching_summary(self, group):
        """[(label, 'years')] for a group, ordered by first term taught."""
        by_label = {}
        chronological = sorted(enumerate(reversed(group["courses"])), key=lambda ic: (term_key(ic[1]["term"]), ic[0]))
        for _, c in chronological:
            label = c.get("summary", c["title"])
            by_label.setdefault(label, []).append(term_key(c["term"])[0])
        return [(self.text(label), self.year_runs(years)) for label, years in by_label.items()]

    @staticmethod
    def md_table(headers, rows):
        """Markdown table with padded columns (cells are already rendered)."""
        rows = [[str(c) for c in row] for row in rows]
        widths = [max(len(x) for x in col) for col in zip(headers, *rows)]
        line = lambda cells: "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"
        return "\n".join([line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|", *map(line, rows)])

    def course_rows(self, courses, columns):
        cell = {
            "code": lambda c: c["code"],
            "title": lambda c: self.text(c["title"]) + self.fn(c.get("footnotes")),
            "delivery": lambda c: c["delivery"],
            "sections": lambda c: c["sections"],
            "students": self.students,
            "term": lambda c: c["term"],
            "inst": lambda c: c["inst"],
        }
        return [[cell[k](c) for k in columns] for c in courses]

    # ---- misc entries

    def service(self, s):
        name = ", ".join(self.text(x) for x in [s["role"], s["body"], s.get("unit")] if x)
        return f"{name} ({self.span(s['start'], s.get('end'))})"

    def award(self, a):
        s = f"{a['year']} {self.text(a['name'])}, {self.text(a['organization'])}"
        extra = ", ".join(self.text(x) for x in [a.get("amount"), a.get("purpose")] if x)
        return f"{s} ({extra})" if extra else s

    def outreach(self, o):
        return (f"{self.bold(self.esc(SELF))} {self.date_apa(o['date'])}. "
                f"{self.sentences([self.text(o['title'])])} {self.link('Link', o['url'])}")

    def press(self, p):
        author = self.esc(p["author"])
        if not author.endswith("."):
            author += "."
        links = ", ".join(self.link(self.esc(l["label"]), l["url"]) for l in p["links"])
        if p.get("also"):
            links += f"; also featured in {self.text(p['also'])}"
        return f"{author} {self.date_apa(p['date'])}. {self.text(p['title'])}, {self.it(p['outlet'])}. {links}."

    def reviewer(self, journals):
        names = [self.it(j) for j in journals]
        return "Reviewer for " + ", ".join(names[:-1]) + ", and " + names[-1] + "."


# --------------------------------------------------------------------------- data

def load_data():
    data = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("*.yml"))}
    pubs = [p for p in data["publications"] if p.get("show", True)]
    data["pubs"] = {t: [p for p in pubs if p["type"] == t]
                    for t in ("journal", "chapter", "report", "in-prep", "dissertation")}
    data["pubs_all"] = pubs
    data["teaching_groups"] = {g["id"]: g for g in data["teaching"]["groups"]}
    for g in data["teaching"]["groups"]:
        for c in g["courses"]:
            c["inst"] = g["key"]
    return data


def make_env():
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        block_start_string="<%", block_end_string="%>",
        variable_start_string="<<", variable_end_string=">>",
        comment_start_string="<#", comment_end_string="#>",
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined, extensions=["jinja2.ext.do"],
    )


def render(env, template, kind, data):
    r = Renderer(kind, data)
    # Web pages mix Markdown with HTML tables; `h` renders HTML fragments with a shared footnote counter.
    h = Renderer("html", data)
    h.fn_letters, h.fn_pending = r.fn_letters, r.fn_pending
    return env.get_template(template).render(r=r, h=h, d=data, **data)


# --------------------------------------------------------------------------- outputs

GENERATED_NOTE_MD = "\n<!-- Generated by cv-ajsteinmetz/build.py from data/*.yml and templates/{t}. Edit those, not this file. -->\n"
MARKER = re.compile(r"(<!-- BEGIN GENERATED: ([\w-]+) -->\n).*?(<!-- END GENERATED: \2 -->)", re.S)


def outputs(web, profile):
    """(path, producer) pairs; producer(env, data, current_text) -> new text."""
    def full(template, kind, note=None):
        def produce(env, data, _):
            body = render(env, template, kind, data)
            return body + (note.format(t=template) if note else "")
        return produce

    def blocks(env, data, current):
        def sub(m):
            body = render(env, f"blocks/{m.group(2)}.md", "md", data)
            return m.group(1) + body.rstrip("\n") + "\n" + m.group(3)
        new, n = MARKER.subn(sub, current)
        if not n:
            raise ValueError("no <!-- BEGIN GENERATED: name --> markers found")
        return new

    outs = [(ROOT / "cv_ajsteinmetz.tex", full("cv.tex", "tex"))]
    if web.is_dir():
        for page in ("cv", "publications", "talks", "teaching"):
            outs.append((web / f"{page}.md", full(f"web/{page}.md", "md", GENERATED_NOTE_MD)))
        outs.append((web / "index.md", blocks))
    else:
        print(f"warning: {web} not found, skipping website", file=sys.stderr)
    if profile.is_dir():
        outs.append((profile / "README.md", blocks))
    else:
        print(f"warning: {profile} not found, skipping profile README", file=sys.stderr)
    return outs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--web", type=Path, default=ROOT.parent / "ajsteinmetz.github.io")
    ap.add_argument("--profile", type=Path, default=ROOT.parent / "ajsteinmetz")
    ap.add_argument("--pdf", action="store_true", help="compile cv_ajsteinmetz.pdf with pdflatex")
    ap.add_argument("--check", action="store_true", help="report stale outputs and exit 1; write nothing")
    args = ap.parse_args()

    data, env = load_data(), make_env()
    stale = []
    for path, produce in outputs(args.web, args.profile):
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        new = produce(env, data, current)
        if new != current:
            stale.append(path)
            if not args.check:
                path.write_text(new, encoding="utf-8", newline="\n")
        print(f"{'changed' if new != current else 'ok     '}  {path}")

    if args.check:
        sys.exit(1 if stale else 0)
    if args.pdf:
        for _ in range(2):  # second pass settles PDF bookmarks
            subprocess.run(["pdflatex", "-synctex=1", "-interaction=nonstopmode", "cv_ajsteinmetz.tex"],
                           cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        print("compiled cv_ajsteinmetz.pdf")


if __name__ == "__main__":
    main()
