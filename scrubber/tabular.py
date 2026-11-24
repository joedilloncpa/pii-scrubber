"""CSV and Excel handling."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

import openpyxl
from openpyxl.utils import get_column_letter

from .core import (SURE, Finding, LabelBook, LAST_FIRST_RE, NAME_TOKEN_RE, Scanner, Settings,
                   apply_to_text, mask, to_text, verify_text)

# ---------------------------------------------------------------------------
# Header classification
# ---------------------------------------------------------------------------

B1, B2 = r"(?<![a-z0-9])", r"(?![a-z0-9])"

NAME_HEADERS = {
    "name": "full", "employee": "full", "employee name": "full", "full name": "full", "legal name": "full",
    "employee full name": "full", "worker": "full", "worker name": "full", "person": "full",
    "contractor": "full", "contractor name": "full", "team member": "full", "customer": "full",
    "customer name": "full", "client": "full", "client name": "full", "contact": "full", "contact name": "full",
    "patient": "full", "patient name": "full", "member": "full", "member name": "full", "payee": "full",
    "payee name": "full", "tenant": "full", "tenant name": "full", "owner": "full", "owner name": "full",
    "applicant": "full", "applicant name": "full", "borrower": "full", "guarantor": "full",
    "primary contact": "full", "account holder": "full", "cardholder": "full", "cardholder name": "full",
    "first name": "first", "first": "first", "employee first name": "first", "legal first name": "first",
    "given name": "first", "fname": "first",
    "last name": "last", "last": "last", "employee last name": "last", "legal last name": "last",
    "surname": "last", "family name": "last", "lname": "last",
    "middle name": "middle", "middle initial": "middle", "middle": "middle", "mi": "middle",
    "preferred name": "preferred", "preferred first name": "preferred", "nickname": "preferred",
}
COMPANY_HEADERS = {
    "company", "company name", "company legal name", "employer", "employer name", "employer legal name",
    "business name", "legal entity", "entity", "entity name", "dba", "doing business as", "organization",
    "organization name", "organisation", "firm", "firm name", "vendor name", "customer company",
}
STATE_HEADERS = {"state", "home state", "state province", "province", "residence state", "st"}

SSN_HEADER_RE = re.compile(B1 + r"(ssn|ss ?#|social security (number|no|#)|soc sec (no|number|#)|tin|itin|tax ?id|taxpayer id|ein|fein|national id|nin|sin)" + B2)

PROTECTED_RE = re.compile(
    B1 + r"(tax|taxes|taxable|wage|wages|gross|net|pay|amount|amt|deduction|deductions|contribution|"
    r"contributions|hours|hrs|rate|earnings|reimbursement|reimbursements|withholding|withheld|medicare|"
    r"social security|oasdi|fica|futa|suta|sui|sdi|pfl|fli|401 k|401k|403 b|403b|roth|hsa|fsa|total|totals|"
    r"bonus|commission|overtime|ot|salary|tips|benefit|benefits|premium|garnishment|period|payroll|cost|"
    r"debit|credit|liability|check date|ytd|cash|match|pto|holiday|sick|vacation|allowance|stipend|"
    r"insurance|dental|vision|medical|pension|retirement|levy|support|reemployment|unemployment|"
    r"disability|surcharge|assessment|workforce|donation|donations|charity|gift|balance|price|qty|quantity|"
    r"subtotal|discount|fee|fees|invoice date|due date|posted|cleared|units|shares|value|fmv|basis|gain|loss|"
    r"proceeds|interest|dividend|dividends|principal)" + B2
)

PII_HEADER_RULES = [
    ("dob", r"(dob|birth|birthday|birthdate|age)"),
    ("hire", r"(hire|hired|rehire|employment start|original start|start date)"),
    ("term", r"(termination|terminated|term date|separation|dismissal|end date)"),
    ("email", r"(e ?mail)"),
    ("phone", r"(phone|mobile|cell|telephone|tel|fax)"),
    ("bank", r"(account number|account no|account #|acct|routing|aba|bank|iban|swift|card number|card no|last 4|last four)"),
    ("license", r"(license|licence|passport|driver|dl)"),
    ("address", r"(address|addr|street|city|town|zip|zipcode|zip code|postal|postcode|apt|apartment|suite|county|country)"),
    ("empid", r"((employee|emp|worker|staff|badge|personnel|contractor|member|customer|client|patient|policy|loan|case) ?(id|#|no|number|num)|id|#)"),
    ("url", r"(linkedin|website|url|profile)"),
    ("ip", r"(ip address|ip addr)"),
]
PII_HEADER_RULES = [(cat, re.compile(B1 + pat + B2)) for cat, pat in PII_HEADER_RULES]

MASK_CATS = {"ssn", "dob", "hire", "term", "email", "phone", "bank", "license", "address", "state", "empid", "url", "ip"}

CATEGORY_LABELS = {
    "name_full": "Name", "name_first": "Name", "name_last": "Name", "name_middle": "Name", "name_preferred": "Name",
    "ssn": "SSN / tax ID", "dob": "Date of birth / age", "hire": "Hire date", "term": "Termination date",
    "email": "Email", "phone": "Phone", "bank": "Bank / card info", "license": "License / passport",
    "address": "Address", "state": "Address", "empid": "ID number", "url": "Personal URL", "ip": "IP address",
    "company": "Company", "protected": "Kept (financial data)", "text": "Scanned as free text",
}

SKIP_NAME_VALUES = {"total", "totals", "grand total", "subtotal", "sub total", "company total", "summary", "payroll totals"}


def norm_header(h) -> str:
    h = str(h if h is not None else "").strip().lower()
    h = re.sub(r"[^a-z0-9#/]+", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def classify_header(h, has_address: bool = True) -> str:
    n = norm_header(h)
    if not n:
        return "text"
    if SSN_HEADER_RE.search(n):
        return "ssn"
    if n in COMPANY_HEADERS:
        return "company"
    if n in NAME_HEADERS:
        return "name_" + NAME_HEADERS[n]
    if PROTECTED_RE.search(n):
        return "protected"
    for cat, rx in PII_HEADER_RULES:
        if rx.search(n):
            return cat
    if n in STATE_HEADERS and (has_address or n != "state"):
        return "state"
    return "text"


def classify_row(header) -> list:
    first = [classify_header(h) for h in header]
    has_address = "address" in first
    return [classify_header(h, has_address) for h in header]


def detect_header(rows) -> int | None:
    best, best_score = None, 1
    for i, row in enumerate(rows[:40]):
        score = sum(1 for c in classify_row(row) if c != "text")
        if score > best_score:
            best, best_score = i, score
    return best


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

@dataclass
class Sheet:
    name: str
    rows: list
    formats: list | None = None
    col_widths: dict = field(default_factory=dict)


def read_csv(data: bytes) -> list[Sheet]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return [Sheet("Sheet1", [list(r) for r in csv.reader(io.StringIO(text))])]


def read_xlsx(data: bytes) -> list[Sheet]:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows, fmts = [], []
        for row in ws.iter_rows():
            rows.append([c.value for c in row])
            fmts.append([c.number_format for c in row])
        widths = {k: v.width for k, v in ws.column_dimensions.items() if v.width}
        sheets.append(Sheet(ws.title, rows, fmts, widths))
    return sheets


def write_csv(sheet: Sheet) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for row in sheet.rows:
        w.writerow(["" if v is None else v for v in row])
    return buf.getvalue().encode("utf-8-sig")


def write_xlsx(sheets: list[Sheet]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(title=sh.name[:31])
        for r, row in enumerate(sh.rows, 1):
            for c, v in enumerate(row, 1):
                if v is None or v == "":
                    continue
                cell = ws.cell(row=r, column=c, value=v)
                if sh.formats and not isinstance(v, str):
                    try:
                        cell.number_format = sh.formats[r - 1][c - 1]
                    except IndexError:
                        pass
        for col, width in sh.col_widths.items():
            ws.column_dimensions[col].width = width
    wb.properties.creator = ""
    wb.properties.lastModifiedBy = ""
    wb.properties.title = ""
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Detect / apply
# ---------------------------------------------------------------------------

class TabularDoc:
    def __init__(self, data: bytes, is_xlsx: bool, settings: Settings):
        self.is_xlsx = is_xlsx
        self.settings = settings
        self.sheets = read_xlsx(data) if is_xlsx else read_csv(data)
        self.book = LabelBook(settings)
        self.scanner = Scanner(settings, self.book)
        self.plans: list[tuple[int | None, list[str]]] = []
        self.findings: list[Finding] = []
        self.column_report: list[dict] = []
        self._rules_added = False

    # -- pass 1 -----------------------------------------------------------

    def _register_names_first(self):
        """Walk name columns first so labels follow first appearance order."""
        self.plans, self.column_report = [], []
        column_people, column_companies = set(), set()
        for si, sheet in enumerate(self.sheets):
            hdr = detect_header(sheet.rows)
            cats = classify_row(sheet.rows[hdr]) if hdr is not None else []
            self.plans.append((hdr, cats))
            for c, cat in enumerate(cats):
                self.column_report.append({"Sheet": sheet.name, "Column": to_text(sheet.rows[hdr][c]) or get_column_letter(c + 1),
                                           "Treatment": CATEGORY_LABELS.get(cat, cat)})
            if hdr is None:
                continue
            name_cols = [c for c, k in enumerate(cats) if k.startswith("name_")]
            for r in range(hdr + 1, len(sheet.rows)):
                display = self._row_name(sheet.rows[r], cats, name_cols)
                if display:
                    column_people.add(self.book.person(display))
            for c, cat in enumerate(cats):
                if cat == "company":
                    for r in range(hdr + 1, len(sheet.rows)):
                        v = to_text(sheet.rows[r][c]).strip() if c < len(sheet.rows[r]) else ""
                        if v and v.lower() not in SKIP_NAME_VALUES:
                            column_companies.add(self.book.company(v))
        # names and companies that came from a column are also caught in free text
        if not self._rules_added:
            for label, orig in list(self.book.people_display.items()):
                if label in column_people:
                    self.scanner.add_name_rule(orig, label)
            for label, orig in list(self.book.companies_display.items()):
                if label in column_companies:
                    self.scanner.add_company_rule(orig, label)
            self._rules_added = True

    @staticmethod
    def _row_name(row, cats, name_cols) -> str:
        parts = {}
        for c in name_cols:
            t = to_text(row[c]).strip() if c < len(row) else ""
            if t:
                k = cats[c][5:]
                parts[k] = (parts[k] + " " + t) if k in parts else t
        first, last, full = parts.get("first", ""), parts.get("last", ""), parts.get("full", "")
        display = " ".join(p for p in (first, last) if p) or full
        if not display or display.strip().lower() in SKIP_NAME_VALUES:
            return ""
        toks = [t.lower() for t in NAME_TOKEN_RE.findall(display)]
        if any(t in ("total", "totals", "subtotal", "subtotals") for t in toks):
            return ""
        if LAST_FIRST_RE.match(display):
            last_, first_ = [p.strip() for p in display.split(",", 1)]
            display = f"{first_} {last_}"
        return display

    def units_for_scan(self):
        """Free-text cells only (used when approved names are expanded after review)."""
        for si, sheet in enumerate(self.sheets):
            hdr, cats = self.plans[si]
            for r, row in enumerate(sheet.rows):
                if r == hdr:
                    continue
                for c, v in enumerate(row):
                    t = to_text(v)
                    if not t.strip():
                        continue
                    cat = cats[c] if (hdr is not None and r > hdr and c < len(cats)) else "text"
                    if cat == "text":
                        yield t, f"{sheet.name}!{get_column_letter(c + 1)}{r + 1}", ("cell", si, r, c), True

    def detect(self) -> list[Finding]:
        self._register_names_first()
        findings: list[Finding] = []
        for si, sheet in enumerate(self.sheets):
            hdr, cats = self.plans[si]
            name_cols = [c for c, k in enumerate(cats) if k.startswith("name_")]
            label_col = name_cols[0] if name_cols else None
            # sheet name
            for f in self.scanner.scan(sheet.name, f"Sheet name '{sheet.name}'", ("sheetname", si), include_unsure=False):
                findings.append(f)
            for r, row in enumerate(sheet.rows):
                for c, v in enumerate(row):
                    t = to_text(v)
                    if not t.strip():
                        continue
                    where = f"{sheet.name}!{get_column_letter(c + 1)}{r + 1}"
                    loc = ("cell", si, r, c)
                    if hdr is None or r < hdr:
                        findings += self.scanner.scan(t, where, loc, include_unsure=True)
                        continue
                    if r == hdr:
                        findings += self.scanner.scan(t, where, loc, include_unsure=False)
                        continue
                    cat = cats[c] if c < len(cats) else "text"
                    if cat == "protected":
                        continue
                    if cat.startswith("name_"):
                        display = self._row_name(row, cats, name_cols)
                        if display:
                            label = self.book.person(display)
                            rep = label if c == label_col else ""
                            findings.append(Finding(self.scanner.new_id(), "Name", t, rep, where, SURE, "", True, loc, 0, len(t)))
                        else:
                            findings += self.scanner.scan(t, where, loc, include_unsure=False)
                        continue
                    if cat == "company":
                        if t.strip().lower() in SKIP_NAME_VALUES:
                            continue
                        findings.append(Finding(self.scanner.new_id(), "Company", t, self.book.company(t), where, SURE, "", True, loc, 0, len(t)))
                        continue
                    if cat in MASK_CATS:
                        findings.append(Finding(self.scanner.new_id(), CATEGORY_LABELS[cat], t, mask(t, self.settings.mask_char), where, SURE, "", True, loc, 0, len(t)))
                        continue
                    findings += self.scanner.scan(t, where, loc, include_unsure=True)
        self.findings = findings
        return findings

    # -- pass 2 -----------------------------------------------------------

    def apply(self, findings: list[Finding]) -> tuple[bytes, str, list[dict]]:
        by_cell: dict[tuple, list[Finding]] = {}
        sheet_renames = set()
        for f in findings:
            if not f.approved:
                continue
            if f.loc and f.loc[0] == "cell":
                by_cell.setdefault(f.loc[1:], []).append(f)
            elif f.loc and f.loc[0] == "sheetname":
                sheet_renames.add(f.loc[1])
        out_sheets = []
        for si, sheet in enumerate(self.sheets):
            rows = []
            for r, row in enumerate(sheet.rows):
                new = list(row)
                for c, v in enumerate(row):
                    fs = by_cell.get((si, r, c))
                    if fs:
                        new[c] = apply_to_text(to_text(v), fs)
                rows.append(new)
            name = f"Sheet{si + 1}" if si in sheet_renames else sheet.name
            out_sheets.append(Sheet(name, rows, sheet.formats, sheet.col_widths))
        if self.is_xlsx:
            data, ext = write_xlsx(out_sheets), "xlsx"
        else:
            data, ext = write_csv(out_sheets[0]), "csv"
        issues = self._verify(out_sheets, findings)
        return data, ext, issues

    def _verify(self, out_sheets, findings) -> list[dict]:
        originals = [f.original for f in findings if f.approved and len(f.original) >= 4
                     and not (f.original.replace("-", "").replace(" ", "").isdigit() and len(f.original) < 6)]
        issues = []
        for si, sh in enumerate(out_sheets):
            hdr, cats = self.plans[si]
            for r, row in enumerate(sh.rows):
                for c, v in enumerate(row):
                    cat = cats[c] if (hdr is not None and r > hdr and c < len(cats)) else "text"
                    if cat == "protected":
                        continue
                    t = to_text(v)
                    if t.strip():
                        issues += verify_text(t, originals if r != hdr else [], f"{sh.name}!{get_column_letter(c + 1)}{r + 1}")
        return issues

    def preview_rows(self, findings: list[Finding], limit: int = 30) -> list[tuple[str, list[list[str]]]]:
        """Scrubbed preview per sheet (header + first rows)."""
        by_cell: dict[tuple, list[Finding]] = {}
        for f in findings:
            if f.approved and f.loc and f.loc[0] == "cell":
                by_cell.setdefault(f.loc[1:], []).append(f)
        out = []
        for si, sheet in enumerate(self.sheets):
            rows = []
            for r, row in enumerate(sheet.rows[:limit + (self.plans[si][0] or 0) + 1]):
                rows.append([apply_to_text(to_text(v), by_cell.get((si, r, c), [])) for c, v in enumerate(row)])
            out.append((sheet.name, rows))
        return out
