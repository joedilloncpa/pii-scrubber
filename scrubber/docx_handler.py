"""Word (.docx) handling.

Works at the XML level so text in tables, text boxes, headers, footers,
footnotes, endnotes, comments and tracked changes is all covered. Replacement
is done per run so formatting survives, including matches that span runs.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from .core import Finding, LabelBook, Scanner, Settings, apply_to_text, verify_text

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
TEXT_TAGS = {qn("w:t"), qn("w:delText")}
BREAK_TAGS = {qn("w:tab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n", qn("w:noBreakHyphen"): "-", qn("w:softHyphen"): ""}


class Unit:
    """One paragraph: ordered segments of (node_or_None, text)."""

    def __init__(self, where: str, para):
        self.where = where
        self.segments: list[tuple] = []
        for node in para.iter():
            if node.tag in TEXT_TAGS:
                self.segments.append((node, node.text or ""))
            elif node.tag in BREAK_TAGS:
                self.segments.append((None, BREAK_TAGS[node.tag]))

    @property
    def text(self) -> str:
        return "".join(t for _, t in self.segments)

    def apply(self, findings: list[Finding]):
        active = sorted((f for f in findings if f.approved and f.start >= 0), key=lambda f: -f.start)
        texts = [t for _, t in self.segments]
        bounds, pos = [], 0
        for t in texts:
            bounds.append((pos, pos + len(t)))
            pos += len(t)
        last_end = None
        for f in active:
            if last_end is not None and f.end > last_end:
                continue  # overlap guard
            last_end = f.start
            first = True
            for i, (bs, be) in enumerate(bounds):
                if be <= f.start or bs >= f.end or self.segments[i][0] is None:
                    if not (be <= f.start or bs >= f.end) and self.segments[i][0] is None:
                        pass  # a tab/break inside the match stays; harmless
                    continue
                ls, le = max(f.start, bs) - bs, min(f.end, be) - bs
                if first:
                    texts[i] = texts[i][:ls] + f.replacement + texts[i][le:]
                    first = False
                else:
                    texts[i] = texts[i][:ls] + texts[i][le:]
        for (node, _), new in zip(self.segments, texts):
            if node is not None:
                node.text = new
                if new != new.strip():
                    node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


class DocxDoc:
    def __init__(self, data: bytes, settings: Settings):
        self.settings = settings
        self.doc = Document(io.BytesIO(data))
        self.book = LabelBook(settings)
        self.scanner = Scanner(settings, self.book)
        self.units: list[Unit] = []
        self.blob_parts: list = []       # (part, root) for parts we edit as raw XML
        self.findings: list[Finding] = []
        self._collect()

    def _collect(self):
        body = self.doc.element.body
        n = 0
        for p in body.iter(qn("w:p")):
            n += 1
            in_table = any(a.tag == qn("w:tc") for a in p.iterancestors())
            self.units.append(Unit(f"{'Table cell, paragraph' if in_table else 'Paragraph'} {n}", p))
        for si, section in enumerate(self.doc.sections, 1):
            for kind, getter in (("Header", lambda s: [s.header, s.first_page_header, s.even_page_header]),
                                 ("Footer", lambda s: [s.footer, s.first_page_footer, s.even_page_footer])):
                seen = set()
                for hf in getter(section):
                    try:
                        el = hf._element
                    except Exception:
                        continue
                    if id(el) in seen or el is None:
                        continue
                    seen.add(id(el))
                    for p in el.iter(qn("w:p")):
                        self.units.append(Unit(f"{kind} (section {si})", p))
        for part in self.doc.part.package.iter_parts():
            name = str(part.partname)
            if name.endswith(("footnotes.xml", "endnotes.xml", "comments.xml")):
                label = name.rsplit("/", 1)[-1].replace(".xml", "").capitalize()
                if hasattr(part, "_element") and part._element is not None:
                    root = part._element
                    self.blob_parts.append((part, root, False))
                else:
                    root = etree.fromstring(part._blob)
                    self.blob_parts.append((part, root, True))
                for p in root.iter(qn("w:p")):
                    self.units.append(Unit(label, p))

    def units_for_scan(self):
        for i, u in enumerate(self.units):
            yield u.text, u.where, ("para", i), True

    def detect(self) -> list[Finding]:
        findings = []
        for text, where, loc, unsure in self.units_for_scan():
            findings += self.scanner.scan(text, where, loc, include_unsure=unsure)
        self.findings = findings
        return findings

    def apply(self, findings: list[Finding]) -> tuple[bytes, str, list[dict]]:
        by_unit: dict[int, list[Finding]] = {}
        for f in findings:
            if f.approved and f.loc and f.loc[0] == "para":
                by_unit.setdefault(f.loc[1], []).append(f)
        for i, fs in by_unit.items():
            self.units[i].apply(fs)
        self._scrub_metadata()
        for part, root, is_blob in self.blob_parts:
            if is_blob:
                part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        buf = io.BytesIO()
        self.doc.save(buf)
        data = buf.getvalue()
        return data, "docx", self._verify(data, findings)

    def _scrub_metadata(self):
        cp = self.doc.core_properties
        for attr in ("author", "last_modified_by", "title", "subject", "keywords", "comments", "category", "content_status", "identifier"):
            try:
                setattr(cp, attr, "")
            except Exception:
                pass
        # author/initials on comments and tracked changes
        roots = [self.doc.element] + [r for _, r, _ in self.blob_parts]
        for section in self.doc.sections:
            for hf in (section.header, section.footer, section.first_page_header, section.first_page_footer,
                       section.even_page_header, section.even_page_footer):
                try:
                    roots.append(hf._element)
                except Exception:
                    pass
        for root in roots:
            if root is None:
                continue
            for el in root.iter():
                if el.get(qn("w:author")) is not None:
                    el.set(qn("w:author"), "Author")
                if el.get(qn("w:initials")) is not None:
                    el.set(qn("w:initials"), "A")
        # app.xml company / manager
        for part in self.doc.part.package.iter_parts():
            if str(part.partname).endswith("docProps/app.xml") and hasattr(part, "_blob"):
                blob = part._blob.decode("utf-8", "ignore")
                blob = re.sub(r"<(Company|Manager)>.*?</\1>", r"<\1></\1>", blob, flags=re.S)
                part._blob = blob.encode("utf-8")

    def _verify(self, data: bytes, findings) -> list[dict]:
        originals = [f.original for f in findings if f.approved and len(f.original) >= 4
                     and not (f.original.replace("-", "").replace(" ", "").isdigit() and len(f.original) < 6)]
        check = DocxDoc(data, self.settings)
        issues = []
        for text, where, _, _ in check.units_for_scan():
            if text.strip():
                issues += verify_text(text, originals, where)
        return issues

    def preview_text(self, findings: list[Finding], limit: int = 60) -> list[str]:
        by_unit: dict[int, list[Finding]] = {}
        for f in findings:
            if f.approved and f.loc and f.loc[0] == "para":
                by_unit.setdefault(f.loc[1], []).append(f)
        out = []
        for i, u in enumerate(self.units[:limit]):
            t = apply_to_text(u.text, by_unit.get(i, []))
            if t.strip():
                out.append(t)
        return out
