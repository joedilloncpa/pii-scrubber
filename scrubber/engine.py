"""Dispatch by file type and the approve-then-apply flow."""

from __future__ import annotations

from .core import SURE, UNSURE, Finding, Scanner, Settings, build_key_csv
from .docx_handler import DocxDoc
from .pdf_handler import PdfDoc, ScannedPdfError
from .tabular import TabularDoc

SUPPORTED_TYPES = {
    "CSV": ("csv",),
    "Excel": ("xlsx", "xlsm"),
    "Word": ("docx",),
    "PDF": ("pdf",),
}


def file_type_for(filename: str) -> str | None:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    for t, exts in SUPPORTED_TYPES.items():
        if ext in exts:
            return t
    return None


def detect(data: bytes, file_type: str, settings: Settings):
    """Build the document handler and run detection. Returns the handler with .findings set."""
    if file_type == "CSV":
        doc = TabularDoc(data, is_xlsx=False, settings=settings)
    elif file_type == "Excel":
        doc = TabularDoc(data, is_xlsx=True, settings=settings)
    elif file_type == "Word":
        doc = DocxDoc(data, settings)
    elif file_type == "PDF":
        doc = PdfDoc(data, settings)
    else:
        raise ValueError(f"Unsupported file type {file_type}")
    doc.detect()
    # Second pass: anything found once by a strong signal (legal suffix, honorific,
    # name column) is turned into a rule so bare mentions elsewhere are caught too.
    added = False
    seen = set()
    for f in doc.findings:
        if f.confidence != SURE:
            continue
        key = (f.category, f.original.lower())
        if key in seen:
            continue
        seen.add(key)
        if f.category == "Company":
            doc.scanner.add_company_rule(f.original, f.replacement)
            added = True
        elif f.category == "Name" and f.replacement:
            doc.scanner.add_name_rule(f.original, f.replacement)
            added = True
    if added:
        doc.detect()
    doc.book.compact(doc.findings, doc.scanner)
    return doc


def expand_approved(doc, findings: list[Finding]) -> list[Finding]:
    """
    After review: a name the user approved should be caught everywhere it appears,
    including first-name-only or last-name-only mentions. Returns extra SURE findings
    that do not overlap anything already approved.
    """
    scanner = Scanner(doc.settings, doc.book)
    scanner.custom_rules = []
    scanner._next_id = max((f.id for f in findings), default=0) + 1
    added = 0
    for f in findings:
        if f.approved and f.confidence == UNSURE and f.category == "Possible name":
            scanner.add_name_rule(f.original, f.replacement)
            added += 1
    if not added:
        return []
    occupied: dict[tuple, list[tuple[int, int]]] = {}
    for f in findings:
        if f.approved and f.start >= 0:
            occupied.setdefault(f.loc, []).append((f.start, f.end))
    extra: list[Finding] = []
    for text, where, loc, _ in doc.units_for_scan():
        if not text.strip():
            continue
        # custom rules only
        spans = []
        for rx, kind, payload in scanner.custom_rules:
            for m in rx.finditer(text):
                spans.append((m.start(), m.end(), "Name", payload, SURE))
        new = scanner._resolve(spans, text, where, loc)
        for nf in new:
            if any(nf.start < e and nf.end > s for s, e in occupied.get(loc, [])):
                continue
            occupied.setdefault(loc, []).append((nf.start, nf.end))
            extra.append(nf)
    return extra


def apply(doc, findings: list[Finding]):
    """Apply approved findings. Returns (file_bytes, ext, issues, key_bytes, all_findings)."""
    extra = expand_approved(doc, findings)
    all_findings = findings + extra
    data, ext, issues = doc.apply(all_findings)
    key = build_key_csv(all_findings, doc.book)
    return data, ext, issues, key, all_findings


__all__ = ["detect", "apply", "expand_approved", "file_type_for", "SUPPORTED_TYPES", "ScannedPdfError"]
