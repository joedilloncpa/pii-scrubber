"""PDF handling with PyMuPDF. Text PDFs only; scanned image PDFs are refused."""

from __future__ import annotations

import re

import pymupdf

from .core import Finding, LabelBook, Scanner, Settings, apply_to_text, clean_ws, verify_text


class ScannedPdfError(Exception):
    pass


class PdfDoc:
    def __init__(self, data: bytes, settings: Settings):
        self.settings = settings
        self.data = data
        self.doc = pymupdf.open(stream=data, filetype="pdf")
        self.book = LabelBook(settings)
        self.scanner = Scanner(settings, self.book)
        self.page_texts = [page.get_text("text") for page in self.doc]
        self.findings: list[Finding] = []
        total = sum(len(t.strip()) for t in self.page_texts)
        has_images = any(page.get_images() for page in self.doc)
        if total < 20 and has_images:
            raise ScannedPdfError("This PDF has no selectable text (it looks scanned). Run it through OCR first, or export the original document instead.")
        if total == 0:
            raise ScannedPdfError("No text found in this PDF.")

    def units_for_scan(self):
        for i, t in enumerate(self.page_texts):
            yield t, f"Page {i + 1}", ("page", i), True
        meta = self.doc.metadata or {}
        for k in ("author", "title", "subject", "keywords", "creator", "producer"):
            v = meta.get(k) or ""
            if v.strip():
                yield v, f"Metadata ({k})", ("meta", k), False

    def detect(self) -> list[Finding]:
        findings = []
        for text, where, loc, unsure in self.units_for_scan():
            findings += self.scanner.scan(text, where, loc, include_unsure=unsure)
        self.findings = findings
        return findings

    def apply(self, findings: list[Finding]) -> tuple[bytes, str, list[dict]]:
        doc = pymupdf.open(stream=self.data, filetype="pdf")
        not_found = []
        by_page: dict[int, dict[str, str]] = {}
        for f in findings:
            if f.approved and f.loc and f.loc[0] == "page":
                by_page.setdefault(f.loc[1], {})[f.original] = f.replacement
        for pno, repl in by_page.items():
            page = doc[pno]
            for original, replacement in sorted(repl.items(), key=lambda kv: -len(kv[0])):
                needle = clean_ws(original)
                quads = page.search_for(needle, quads=True)
                if not quads and "\n" in original:
                    quads = page.search_for(original.replace("\n", " "), quads=True)
                if not quads:
                    not_found.append({"Where": f"Page {pno + 1}", "Issue": f"Could not locate '{original[:30]}' on the page to redact it"})
                    continue
                for q in quads:
                    rect = q.rect
                    fs = max(5.0, min(10.0, rect.height * 0.6))
                    page.add_redact_annot(q, text=replacement, fill=(0, 0, 0), text_color=(1, 1, 1), fontsize=fs, align=pymupdf.TEXT_ALIGN_CENTER)
            page.apply_redactions()
        doc.set_metadata({})
        try:
            doc.del_xml_metadata()
        except Exception:
            pass
        out = doc.tobytes(garbage=4, deflate=True, clean=True)
        doc.close()
        return out, "pdf", not_found + self._verify(out, findings)

    def _verify(self, data: bytes, findings) -> list[dict]:
        originals = [f.original for f in findings if f.approved and len(f.original) >= 4
                     and not (f.original.replace("-", "").replace(" ", "").isdigit() and len(f.original) < 6)]
        originals = [clean_ws(o) for o in originals]
        check = pymupdf.open(stream=data, filetype="pdf")
        issues = []
        for i, page in enumerate(check):
            t = clean_ws(page.get_text("text"))
            issues += verify_text(t, originals, f"Page {i + 1}")
        meta = " ".join(str(v) for v in (check.metadata or {}).values() if v)
        if meta.strip():
            issues += verify_text(meta, originals, "Metadata")
        check.close()
        return issues

    def preview_text(self, findings: list[Finding], limit_pages: int = 3) -> list[str]:
        by_page: dict[int, list[Finding]] = {}
        for f in findings:
            if f.approved and f.loc and f.loc[0] == "page":
                by_page.setdefault(f.loc[1], []).append(f)
        return [apply_to_text(self.page_texts[i], by_page.get(i, [])) for i in range(min(limit_pages, len(self.page_texts)))]

    def render_pages(self, data: bytes, limit_pages: int = 2, zoom: float = 1.3) -> list[bytes]:
        d = pymupdf.open(stream=data, filetype="pdf")
        imgs = [d[i].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png") for i in range(min(limit_pages, len(d)))]
        d.close()
        return imgs
