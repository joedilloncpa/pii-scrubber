"""
Shared detection rules, data model, masking and key-file logic.

Everything here works on plain strings. The per-format handlers (tabular, docx,
pdf) find text, hand it to scan_text(), then apply the approved replacements
back into the document.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

SURE = "sure"
UNSURE = "unsure"


@dataclass
class Finding:
    """One piece of text to replace."""
    id: int
    category: str          # human label, e.g. "SSN", "Name", "Possible name"
    original: str          # exact text as it appears
    replacement: str       # what it becomes
    where: str             # "Sheet1!C5", "Page 3", "Paragraph 12"
    confidence: str        # SURE or UNSURE
    context: str = ""      # short snippet around the match (review list only)
    approved: bool = True  # review decision; SURE findings are always applied
    # internal: handler-specific locator (sheet index, row, col, paragraph ref)
    loc: tuple = field(default_factory=tuple, repr=False)
    start: int = -1        # char offsets within the located text
    end: int = -1


@dataclass
class Settings:
    name_label: str = "Person"
    company_label: str = "Company"
    always_scrub: list = field(default_factory=list)   # user-typed terms
    mask_char: str = "X"


class LabelBook:
    """Assigns stable labels: the same person is always Person 3."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.people: dict[tuple, str] = {}      # name key -> label
        self.people_display: dict[str, str] = {}  # label -> first seen original
        self.companies: dict[str, str] = {}
        self.companies_display: dict[str, str] = {}

    @staticmethod
    def name_key(s: str) -> tuple:
        toks = [t.lower() for t in NAME_TOKEN_RE.findall(s)]
        return tuple(sorted(t for t in toks if len(t) > 1)) or tuple(toks)

    def person(self, name: str) -> str:
        key = self.name_key(name)
        if key not in self.people and len(key) == 1:
            # "Raman" alone: reuse the one person whose name contains it
            hits = {lbl for k, lbl in self.people.items() if key[0] in k}
            if len(hits) == 1:
                return hits.pop()
        if key not in self.people and len(key) > 1:
            # "Priya Raman" when only "Raman" was known: same person, fuller name
            hits = {lbl for k, lbl in self.people.items() if len(k) == 1 and k[0] in key}
            if len(hits) == 1:
                label = hits.pop()
                self.people[key] = label
                if len(clean_ws(name)) > len(self.people_display[label]):
                    self.people_display[label] = clean_ws(name)
                return label
        if key not in self.people:
            label = f"{self.settings.name_label} {len(self.people) + 1}"
            self.people[key] = label
            self.people_display[label] = clean_ws(name)
        return self.people[key]

    def compact(self, findings: list, scanner=None):
        """Renumber labels to 1..N in order of first appearance in the findings."""
        for kind, prefix, store, display in (("Name", self.settings.name_label, self.people, self.people_display),
                                             ("Company", self.settings.company_label, self.companies, self.companies_display)):
            order = []
            for f in sorted(findings, key=lambda f: f.id):
                if f.replacement in display and f.replacement not in order:
                    order.append(f.replacement)
            for lbl in display:
                if lbl not in order:
                    order.append(lbl)
            remap = {old: f"{prefix} {i + 1}" for i, old in enumerate(order)}
            if all(k == v for k, v in remap.items()):
                continue
            for k in list(store):
                store[k] = remap[store[k]]
            new_display = {remap[k]: v for k, v in display.items()}
            display.clear()
            display.update(new_display)
            for f in findings:
                if f.replacement in remap:
                    f.replacement = remap[f.replacement]
            if scanner is not None:
                scanner.custom_rules = [(rx, kind_, remap.get(payload, payload)) for rx, kind_, payload in scanner.custom_rules]

    def company(self, name: str) -> str:
        key = re.sub(r"[^a-z0-9]", "", STRIP_SUFFIX_RE.sub("", name).lower())
        if key not in self.companies:
            label = f"{self.settings.company_label} {len(self.companies) + 1}"
            self.companies[key] = label
            self.companies_display[label] = clean_ws(name)
        return self.companies[key]


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

NAME_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")

SSN_RE = re.compile(r"(?<![\d-])\d{3}[- ]\d{2}[- ]\d{4}(?![\d-])")
EIN_RE = re.compile(r"(?<![\d-])\d{2}-\d{7}(?![\d-])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\d-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
URL_PERSONAL_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+/?", re.I)

STREET_SUFFIX = (
    r"(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl|"
    r"terrace|ter|circle|cir|parkway|pkwy|highway|hwy|trail|trl|square|sq|loop|run|path|row)"
)
STREET_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9][\w.'-]*\s+){0,4}" + STREET_SUFFIX + r"\b\.?"
    r"(?:,?\s*(?:apt|apartment|suite|ste|unit|#|floor|fl)\.?\s*[\w-]+)?"
    r"(?:,?\s*[A-Z][a-zA-Z.\s]{1,30}?,?\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?",
    re.I,
)
PO_BOX_RE = re.compile(r"\bP\.?\s?O\.?\s?Box\s+\d+\b(?:,?\s*[A-Z][a-zA-Z.\s]{1,30}?,?\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?", re.I)
CITY_STATE_ZIP_RE = re.compile(r"\b[A-Z][a-zA-Z.]+(?:\s[A-Z][a-zA-Z.]+){0,2},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")

DATE_RE = r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})"
DOB_RE = re.compile(r"(?:\bDOB\b|date\s+of\s+birth|birth\s*date|\bborn\b)\s*(?:is|on|:|-)?\s*(" + DATE_RE + ")", re.I)

LABELED_NUMBER_RE = re.compile(
    r"(?:account|acct|a/c|routing|aba|member|policy|license|licence|dl|passport|employee|emp|"
    r"customer|client|patient|case|claim|loan|card|medicare|medicaid|taxpayer|tin|itin|ein|id)"
    r"\s*(?:no\.?|number|num|#|id)?\s*(?:is|:|-|#)?\s*([A-Z]{0,3}\d[\d -]{3,}\d)",
    re.I,
)
BARE_NUMBER_RE = re.compile(r"(?<![\d.,$-])\d{7,16}(?![\d.,%-])")

HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Rev|Hon)\.?[ \t]+[A-Z][a-z]+(?:[ \t]+(?:[A-Z]\.?[ \t]+)?[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?)?"
)
CAP_NAME_RE = re.compile(
    r"\b[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?"
    r"(?:[ \t]+(?:[A-Z]\.?[ \t]+)?[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?){1,2}\b"
)
LAST_FIRST_RE = re.compile(r"^\s*[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?,\s*[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s*$")

LEGAL_SUFFIX = (
    r"(?:LLC|L\.L\.C\.|Inc\.?|Incorporated|Corp\.?|Corporation|Co\.|Company|Ltd\.?|Limited|"
    r"LLP|L\.L\.P\.|LP|L\.P\.|PLLC|P\.C\.|PC|PBC|GmbH|S\.A\.|Pty\.?|plc|PLC)"
)
COMPANY_RE = re.compile(
    r"(?<![\w&])([A-Z][\w&'.-]*(?:[ \t]+(?:[A-Z0-9&][\w&'.-]*|of|&)){0,5}),?[ \t]+"
    + LEGAL_SUFFIX + r"(?![A-Za-z])"
)
STRIP_SUFFIX_RE = re.compile(r"[ ,]+" + LEGAL_SUFFIX + r"\s*$", re.I)

MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
          "sept", "oct", "nov", "dec"}
DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}

# Title-case phrases made only of these words are not flagged as possible names.
COMMON_WORDS = MONTHS | DAYS | set("""
a an the and or of for to in on at by with from as is are was were be been being this that these those
it its into over under per via not no yes all any each every both few more most other some such than
too very can will just should would could may might must shall do does did done have has had having
balance sheet income statement cash flow flows statements financial financials report reports summary
detail details total totals subtotal gross net revenue revenues expense expenses cost costs profit loss
assets liabilities equity capital retained earnings payroll journal register period periods pay day
date dates check checks deposit deposits withdrawal transfer transfers payment payments invoice invoices
bill bills due amount amounts rate rates hours regular overtime bonus commission salary salaries wage
wages tax taxes federal state local city county withholding medicare social security unemployment
insurance benefit benefits deduction deductions contribution contributions employer employee employees
department departments division team member members staff contractor contractors vendor vendors customer
customers client clients account accounts receivable payable accrued prepaid deferred fixed current
long term short notes note loan loans interest principal depreciation amortization inventory
operating investing financing activities activity quarter quarterly month monthly year yearly annual
annually week weekly fiscal calendar ending ended beginning opening closing adjusted unadjusted
trial general ledger entry entries debit debits credit credits reconciliation reconciled outstanding
cleared pending approved approval review reviewed prepared reviewer preparer manager director officer
president chief executive finance accounting controller treasurer secretary board chair owner partner
agreement contract terms conditions policy policies procedure procedures schedule schedules exhibit
appendix section article page table figure chart form forms letter memo memorandum notice proposal
plan plans project projects task tasks status update updates meeting minutes agenda action items item
north south east west central new old first second third last next previous main street avenue road
united states america american national international global company corporation group holdings
partners services solutions technologies systems consulting management capital ventures labs digital
software data cloud network networks analytics media marketing sales support operations product products
human resources legal compliance risk audit internal external quality engineering research development
limited liability professional public private trust foundation association institute university college
school hospital medical health care center centre office offices bank credit union fund funds
dear sincerely regards thanks thank you please hello hi attention re subject cc bcc from sent received
mr mrs ms miss mx dr prof rev hon sir madam per via c/o attn
monday tuesday wednesday thursday friday saturday sunday am pm est edt pst pdt cst cdt mst mdt utc
employee id number name address phone email ssn dob hire termination start end type class location
work home mobile cell fax ext description memo notes comment comments reference ref code codes
usd eur gbp cad aud dollars dollar cents percent percentage basis points
january february march april may june july august september october november december
""".split())

CURRENCY_NEAR_RE = re.compile(r"[$€£]\s*$")


def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def mask(s: str, ch: str = "X") -> str:
    """Replace letters and digits with X, keep punctuation so the shape survives."""
    return re.sub(r"[A-Za-z0-9]", ch, s)


def luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13:
        return False
    total, parity = 0, len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%m/%d/%Y") if v.time() == time(0) else v.strftime("%m/%d/%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%m/%d/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def flex_pattern(tokens) -> str:
    body = r"[\s,]+".join(re.escape(t) for t in tokens)
    return r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])"


def is_plausible_name(phrase: str) -> bool:
    toks = NAME_TOKEN_RE.findall(phrase)
    if len(toks) < 2:
        return False
    low = [t.lower() for t in toks]
    if all(t in COMMON_WORDS or len(t) == 1 for t in low):
        return False
    if low[0] in MONTHS or low[0] in DAYS:
        return False
    # at least two tokens that are not common words
    return sum(1 for t in low if t not in COMMON_WORDS and len(t) > 1) >= 2 or \
        (len(low) == 2 and sum(1 for t in low if t not in COMMON_WORDS) >= 1 and low[-1] not in COMMON_WORDS)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class Scanner:
    """Finds PII in free text. One instance per document so labels stay stable."""

    def __init__(self, settings: Settings, book: LabelBook):
        self.settings = settings
        self.book = book
        self._next_id = 1
        self.custom_rules = []     # (regex, kind) from user-typed terms and approved names
        for term in settings.always_scrub:
            term = term.strip()
            if term:
                self.custom_rules.append((re.compile(flex_pattern(term.split()), re.I), "custom", term))

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def add_name_rule(self, name: str, label: str):
        """After a name is approved, also catch its parts (John, Smith) elsewhere."""
        toks = [t for t in NAME_TOKEN_RE.findall(name) if len(t) > 1]
        seqs = [toks]
        if len(toks) >= 2:
            seqs.append(list(reversed(toks)))
            seqs.append([toks[0], toks[-1]])
        for seq in seqs:
            self.custom_rules.append((re.compile(flex_pattern(seq), re.I), "name_part", label))
        for t in toks:
            if t.lower() not in COMMON_WORDS and len(t) > 2:
                self.custom_rules.append((re.compile(flex_pattern([t]), re.I), "name_part", label))

    def add_company_rule(self, name: str, label: str):
        core = STRIP_SUFFIX_RE.sub("", name).strip(" ,")
        for variant in {name, core}:
            if len(variant) >= 3:
                self.custom_rules.append((re.compile(flex_pattern(variant.split()), re.I), "company_part", label))
        first = NAME_TOKEN_RE.findall(core)
        if first and len(first[0]) >= 5 and first[0].lower() not in COMMON_WORDS:
            self.custom_rules.append((re.compile(flex_pattern([first[0]]), re.I), "company_part", label))

    # -- main entry --------------------------------------------------------

    def scan(self, text: str, where: str, loc: tuple = (), include_unsure: bool = True) -> list[Finding]:
        if not text or not text.strip():
            return []
        spans: list[tuple[int, int, str, str, str]] = []   # start, end, category, replacement, confidence

        def add(m_start, m_end, category, replacement, conf):
            spans.append((m_start, m_end, category, replacement, conf))

        ch = self.settings.mask_char

        # custom / approved rules first (highest priority)
        for rx, kind, payload in self.custom_rules:
            for m in rx.finditer(text):
                if kind == "custom":
                    add(m.start(), m.end(), "Term you listed", mask(m.group(0), ch), SURE)
                else:
                    add(m.start(), m.end(), "Name" if kind == "name_part" else "Company", payload, SURE)

        for m in COMPANY_RE.finditer(text):
            add(m.start(), m.end(), "Company", self.book.company(m.group(0)), SURE)
        for m in EMAIL_RE.finditer(text):
            add(m.start(), m.end(), "Email", mask(m.group(0), ch), SURE)
        for m in URL_PERSONAL_RE.finditer(text):
            add(m.start(), m.end(), "Personal URL", mask(m.group(0), ch), SURE)
        for m in SSN_RE.finditer(text):
            add(m.start(), m.end(), "SSN", mask(m.group(0), ch), SURE)
        for m in EIN_RE.finditer(text):
            add(m.start(), m.end(), "EIN / tax ID", mask(m.group(0), ch), SURE)
        for m in CARD_RE.finditer(text):
            if luhn_ok(m.group(0)):
                add(m.start(), m.end(), "Card number", mask(m.group(0), ch), SURE)
        for m in PHONE_RE.finditer(text):
            add(m.start(), m.end(), "Phone", mask(m.group(0), ch), SURE)
        for m in DOB_RE.finditer(text):
            add(m.start(1), m.end(1), "Date of birth", mask(m.group(1), ch), SURE)
        for m in LABELED_NUMBER_RE.finditer(text):
            add(m.start(1), m.end(1), "Account / ID number", mask(m.group(1), ch), SURE)
        for m in STREET_RE.finditer(text):
            add(m.start(), m.end(), "Address", mask(m.group(0), ch), SURE)
        for m in PO_BOX_RE.finditer(text):
            add(m.start(), m.end(), "Address", mask(m.group(0), ch), SURE)
        for m in CITY_STATE_ZIP_RE.finditer(text):
            add(m.start(), m.end(), "Address", mask(m.group(0), ch), SURE)
        for m in IP_RE.finditer(text):
            add(m.start(), m.end(), "IP address", mask(m.group(0), ch), SURE)
        for m in HONORIFIC_NAME_RE.finditer(text):
            name = re.sub(r"^(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Rev|Hon)\.?\s+", "", m.group(0))
            add(m.start(), m.end(), "Name", self.book.person(name), SURE)

        if include_unsure:
            for m in CAP_NAME_RE.finditer(text):
                phrase = m.group(0)
                if is_plausible_name(phrase):
                    add(m.start(), m.end(), "Possible name", "<name>", UNSURE)
            for m in BARE_NUMBER_RE.finditer(text):
                if CURRENCY_NEAR_RE.search(text[max(0, m.start() - 3):m.start()]):
                    continue
                add(m.start(), m.end(), "Possible account or ID number", mask(m.group(0), ch), UNSURE)

        return self._resolve(spans, text, where, loc)

    def _resolve(self, spans, text, where, loc) -> list[Finding]:
        # A "Possible name" that fully contains a confident Name match (e.g. "Priya Raman"
        # around a known "Raman") becomes a confident Name with that label.
        sure_names = [(s, e, rep) for (s, e, cat, rep, conf) in spans if conf == SURE and cat == "Name"]
        merged = []
        absorbed = set()
        for sp in spans:
            s, e, cat, rep, conf = sp
            if conf == UNSURE and cat == "Possible name":
                inner = {(is_, ie, r) for (is_, ie, r) in sure_names if is_ >= s and ie <= e and (ie - is_) < (e - s)}
                labels = {r for _, _, r in inner}
                if len(labels) == 1:
                    merged.append((s, e, "Name", labels.pop(), SURE))
                    absorbed |= inner
                    continue
            merged.append(sp)
        spans = [sp for sp in merged if not (sp[4] == SURE and sp[2] == "Name" and (sp[0], sp[1], sp[3]) in absorbed)]
        # SURE first, then by position, longest match wins, earlier rule wins after that.
        order = sorted(enumerate(spans), key=lambda x: (x[1][4] != SURE, x[1][0], -(x[1][1] - x[1][0]), x[0]))
        taken: list[tuple[int, int]] = []
        out: list[Finding] = []
        for _, (s, e, cat, rep, conf) in order:
            if any(s < te and e > ts for ts, te in taken):
                continue
            taken.append((s, e))
            original = text[s:e]
            if rep == "<name>":
                rep = self.book.person(original)
            ctx_s, ctx_e = max(0, s - 40), min(len(text), e + 40)
            context = ("..." if ctx_s > 0 else "") + text[ctx_s:s] + "[" + original + "]" + text[e:ctx_e] + ("..." if ctx_e < len(text) else "")
            out.append(Finding(self.new_id(), cat, original, rep, where, conf,
                               clean_ws(context), conf == SURE, loc, s, e))
        out.sort(key=lambda f: f.start)
        return out


def apply_to_text(text: str, findings: list[Finding]) -> str:
    """Rebuild a string with approved findings replaced. Findings must carry start/end for this text."""
    active = sorted((f for f in findings if f.approved and f.start >= 0), key=lambda f: f.start)
    out, pos = [], 0
    for f in active:
        if f.start < pos:
            continue
        out.append(text[pos:f.start])
        out.append(f.replacement)
        pos = f.end
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Key file and verification
# ---------------------------------------------------------------------------

def build_key_csv(findings: list[Finding], book: LabelBook) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Type", "Replaced with", "Original", "Where"])
    for label, orig in book.people_display.items():
        w.writerow(["Name", label, orig, ""])
    for label, orig in book.companies_display.items():
        w.writerow(["Company", label, orig, ""])
    seen = set()
    for f in sorted(findings, key=lambda f: (f.category, f.where)):
        if not f.approved or f.category in ("Name", "Possible name", "Company"):
            continue
        key = (f.category, f.original)
        if key in seen:
            continue
        seen.add(key)
        w.writerow([f.category, f.replacement, f.original, f.where])
    return buf.getvalue().encode("utf-8-sig")


VERIFY_PATTERNS = [("SSN", SSN_RE), ("Email", EMAIL_RE), ("Phone", PHONE_RE), ("EIN", EIN_RE)]


def verify_text(text: str, originals: list[str], where: str) -> list[dict]:
    """Rescan output text. Returns problems found (empty list = clean)."""
    issues = []
    for name, rx in VERIFY_PATTERNS:
        for m in rx.finditer(text):
            if re.search(r"[^X\W_]", m.group(0)):
                issues.append({"Where": where, "Issue": f"{name} pattern still present"})
                break
    if originals:
        rx = re.compile("|".join(re.escape(o) for o in sorted(set(originals), key=len, reverse=True)), re.I)
        if rx.search(text):
            issues.append({"Where": where, "Issue": "An original value is still present"})
    return issues
