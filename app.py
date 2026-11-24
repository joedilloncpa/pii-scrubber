"""
PII Scrubber: local Streamlit app.

Run from this folder:
    streamlit run app.py
or double-click "Start PII Scrubber" (Mac: .command, Windows: .bat).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from collections import Counter

import pandas as pd
import streamlit as st

from scrubber import Settings, apply, detect
from scrubber.core import SURE, UNSURE
from scrubber.engine import SUPPORTED_TYPES, ScannedPdfError, file_type_for

st.set_page_config(page_title="PII Scrubber", page_icon="🔒", layout="wide")

st.markdown(
    """
    <style>
    [data-testid="stFileChipName"], [data-testid="stFileUploaderFileName"] { visibility: hidden !important; }
    .block-container { padding-top: 2.2rem; max-width: 1150px; }
    div[data-testid="stMetric"] { background: #F2F6F3; border-radius: 10px; padding: 10px 14px; }
    </style>
    """,
    unsafe_allow_html=True,
)

TYPE_HELP = {
    "CSV": "A plain spreadsheet export (.csv)",
    "Excel": "An Excel workbook (.xlsx). All sheets are scrubbed",
    "Word": "A Word document (.docx). Body, tables, headers, footers, footnotes and comments are scrubbed",
    "PDF": "A text PDF. Scanned image PDFs are not supported",
}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Options")
    name_label = st.text_input("Replace people's names with", value="Person", help="Each person becomes Person 1, Person 2, ... Change the word if you prefer Employee, Client, etc.")
    company_label = st.text_input("Replace company names with", value="Company")
    always = st.text_area("Always scrub these words", placeholder="One per line. Company nicknames, project codenames, anything the detector might not catch.", height=110)
    st.caption("Everything runs on this computer. Nothing is uploaded anywhere.")

settings = Settings(
    name_label=(name_label.strip() or "Person"),
    company_label=(company_label.strip() or "Company"),
    always_scrub=[l for l in always.splitlines() if l.strip()],
)

# ---------------------------------------------------------------------------
# Step 1: choose type and upload
# ---------------------------------------------------------------------------

st.title("PII Scrubber")
st.caption("Remove names, SSNs, addresses, phone numbers, emails, account numbers and company names from a file before you share it or paste it into an AI tool.")

c1, c2 = st.columns([1, 2.4])
with c1:
    file_type = st.selectbox("1. What kind of file?", list(SUPPORTED_TYPES), help="\n".join(f"{k}: {v}" for k, v in TYPE_HELP.items()))
with c2:
    up = st.file_uploader("2. Choose the file", type=list(SUPPORTED_TYPES[file_type]), key=f"up_{file_type}")

SAMPLES = {
    "Payroll register (CSV)": "sample_payroll_register.csv",
    "Customer list (Excel)": "sample_customer_list.xlsx",
    "Engagement letter (Word)": "sample_engagement_letter.docx",
    "Employee form (PDF)": "sample_employee_form.pdf",
}
sample_dir = Path(__file__).parent / "samples"
sample_choice = None
if up is None:
    st.info("Pick the file type, then drop a file in. You'll see exactly what gets scrubbed before anything is downloaded.")
    if sample_dir.exists():
        sample_choice = st.selectbox("No file handy? Try one of the fake samples", ["(choose)"] + list(SAMPLES), index=0)
        if sample_choice == "(choose)":
            sample_choice = None
    if sample_choice is None:
        st.stop()
    sample_path = sample_dir / SAMPLES[sample_choice]
    data = sample_path.read_bytes()
    file_type = file_type_for(sample_path.name)
    st.caption(f"Using the sample file. Every name and number in it is invented.")
else:
    detected = file_type_for(up.name)
    if detected and detected != file_type:
        st.error(f"That looks like a {detected} file. Change the file type above to {detected} and upload it again.")
        st.stop()
    data = up.getvalue()
job_key = hashlib.sha256(data + repr((file_type, settings.name_label, settings.company_label, tuple(settings.always_scrub))).encode()).hexdigest()

if st.session_state.get("job_key") != job_key:
    try:
        with st.spinner("Scanning..."):
            doc = detect(data, file_type, settings)
    except ScannedPdfError as e:
        st.error(str(e))
        st.stop()
    except Exception as e:  # never echo content from the file
        st.error(f"Could not read this file ({type(e).__name__}). Check that it opens normally and is the type you selected.")
        st.stop()
    st.session_state.update({"job_key": job_key, "doc": doc, "findings": doc.findings, "result": None,
                             "review_version": 0, "review_default": True})

doc = st.session_state["doc"]
findings = st.session_state["findings"]
sure = [f for f in findings if f.confidence == SURE]
unsure = [f for f in findings if f.confidence == UNSURE]

# ---------------------------------------------------------------------------
# Step 2: what gets scrubbed automatically
# ---------------------------------------------------------------------------

st.subheader("What will be scrubbed")
people = len({f.replacement for f in sure if f.category == "Name" and f.replacement})
companies = len({f.replacement for f in sure if f.category == "Company"})
other = sum(1 for f in sure if f.category not in ("Name", "Company"))
m1, m2, m3, m4 = st.columns(4)
m1.metric("People found", people)
m2.metric("Companies found", companies)
m3.metric("Other PII items", other)
m4.metric("Needs your review", len(unsure))

counts = Counter(f.category for f in sure)
if counts:
    summary = pd.DataFrame({"Type": list(counts.keys()), "Items": list(counts.values())}).sort_values("Items", ascending=False)
    left, right = st.columns([1, 1.6])
    with left:
        st.dataframe(summary, hide_index=True, width="stretch")
    with right:
        show_orig = st.toggle("Show the original values in this list", value=False, help="Off by default so nothing sensitive is on screen while you work.")
        detail = pd.DataFrame([{"Type": f.category, "Where": f.where,
                                **({"Original": f.original} if show_orig else {}),
                                "Replaced with": f.replacement or "(blank)"} for f in sure])
        st.dataframe(detail, hide_index=True, width="stretch", height=min(400, 38 + 35 * len(detail)))
else:
    st.write("No confident PII matches. Check the review list below.")

if hasattr(doc, "column_report") and doc.column_report:
    with st.expander("How each column was handled"):
        st.dataframe(pd.DataFrame(doc.column_report), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Step 3: review the unsure items
# ---------------------------------------------------------------------------

st.subheader("Needs your review")
decisions: dict[int, tuple[bool, str]] = {}
if unsure:
    st.write("These might be personal information. Tick the ones to scrub, untick anything that isn't a name or ID. You can change the replacement text too.")
    b1, b2, _ = st.columns([1, 1, 5])
    if b1.button("Scrub all", width="stretch"):
        st.session_state["review_default"] = True
        st.session_state["review_version"] += 1
    if b2.button("Keep all", width="stretch"):
        st.session_state["review_default"] = False
        st.session_state["review_version"] += 1
    default = st.session_state["review_default"]
    review_df = pd.DataFrame([{
        "Scrub": default, "Found": f.original, "Looks like": f.category, "Where": f.where,
        "Context": f.context, "Replace with": f.replacement, "_id": f.id,
    } for f in unsure])
    edited = st.data_editor(
        review_df,
        key=f"review_{st.session_state['review_version']}",
        hide_index=True,
        width="stretch",
        disabled=["Found", "Looks like", "Where", "Context"],
        column_config={
            "Scrub": st.column_config.CheckboxColumn("Scrub?", width="small"),
            "Found": st.column_config.TextColumn("Found", width="medium"),
            "Looks like": st.column_config.TextColumn("Looks like", width="small"),
            "Where": st.column_config.TextColumn("Where", width="small"),
            "Context": st.column_config.TextColumn("Context", width="medium"),
            "Replace with": st.column_config.TextColumn("Replace with", width="medium"),
            "_id": None,
        },
        height=min(520, 38 + 35 * len(review_df)),
    )
    for _, row in edited.iterrows():
        decisions[int(row["_id"])] = (bool(row["Scrub"]), str(row["Replace with"]).strip() or "XXXXX")
    go = st.button("Apply and build the scrubbed file", type="primary")
else:
    st.success("Nothing uncertain. Everything found was a clear match.")
    go = st.session_state.get("result") is None or st.button("Rebuild")

# ---------------------------------------------------------------------------
# Step 4: apply, verify, download
# ---------------------------------------------------------------------------

if go:
    for f in unsure:
        ok, rep = decisions.get(f.id, (False, f.replacement))
        f.approved = ok
        f.replacement = rep
    with st.spinner("Scrubbing and verifying..."):
        out_bytes, ext, issues, key_bytes, all_findings = apply(doc, findings)
    st.session_state["result"] = (out_bytes, ext, issues, key_bytes, all_findings)

res = st.session_state.get("result")
if not res:
    st.stop()
out_bytes, ext, issues, key_bytes, all_findings = res

st.subheader("Result")
applied = sum(1 for f in all_findings if f.approved)
if issues:
    st.warning(f"{applied} items replaced, but the final check found {len(issues)} spot(s) to look at. The file is still available; review these before sharing.")
    st.dataframe(pd.DataFrame(issues), hide_index=True, width="stretch")
else:
    st.success(f"{applied} items replaced. Final check passed: no names, SSNs, emails, phone numbers or original values found in the output.")

base = "scrubbed"
d1, d2 = st.columns(2)
d1.download_button(f"Download scrubbed file (.{ext})", out_bytes, file_name=f"{base}.{ext}", type="primary", width="stretch")
d2.download_button("Download key file (keep private)", key_bytes, file_name="scrub_key_DO_NOT_SHARE.csv", width="stretch")
st.caption("The key file maps Person 1, Company 1 and every masked value back to the original. Store it somewhere private and never send it with the scrubbed file.")

with st.expander("Preview of the scrubbed file", expanded=True):
    if file_type in ("CSV", "Excel"):
        for name, rows in doc.preview_rows(all_findings):
            if file_type == "Excel":
                st.write(f"Sheet: {name}")
            width = max((len(r) for r in rows), default=0)
            st.dataframe(pd.DataFrame([r + [""] * (width - len(r)) for r in rows]), hide_index=True, width="stretch")
    elif file_type == "Word":
        for para in doc.preview_text(all_findings):
            st.text(para)
    else:
        for img in doc.render_pages(out_bytes, limit_pages=2):
            st.image(img, width="stretch")
