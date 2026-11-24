"""Builds the fake sample files in this folder. Every name, number and address is invented."""

import csv
from pathlib import Path

import openpyxl
import pymupdf
from docx import Document

HERE = Path(__file__).parent

PEOPLE = [
    ("Raman", "Priya", "10421", "912-34-5678", "03/12/1988", "48 Linden Ave", "Fairport", "NY", "14450", "(585) 555-0101", "priya.raman@example.com", "Engineering", 62.50),
    ("Okafor", "Michael", "10422", "913-45-6789", "11/30/1992", "1207 Elm Street Apt 3", "Rochester", "NY", "14607", "585-555-0102", "mokafor@example.com", "Sales", 38.00),
    ("Delgado", "Sofia", "10423", "914-56-7890", "07/04/1985", "9 Harbor Rd", "Pittsford", "NY", "14534", "585.555.0103", "sofia.delgado@example.com", "Finance", 71.25),
    ("Brennan", "Michael", "10424", "915-67-8901", "01/22/1979", "330 Oak Hill Dr", "Penfield", "NY", "14526", "585-555-0104", "mike.brennan@example.com", "Operations", 55.00),
]
COMPANY = "Riverbend Analytics LLC"


def make_csv():
    header = ["Last Name", "First Name", "Employee ID", "SSN", "Date of Birth", "Street Address", "City", "State", "Zip",
              "Phone", "Email", "Department", "Pay Rate", "Regular Hours", "Gross Pay", "Net Pay", "Memo"]
    memos = ["Referral bonus for Michael Okafor", "Approved by Priya Raman", "Per Riverbend travel policy", "Call 585-555-0199 re garnishment"]
    rows = [[COMPANY], ["Payroll Register"], ["Pay Period: 11/01/2026 - 11/15/2026"], [], header]
    for p, memo in zip(PEOPLE, memos):
        gross = p[12] * 80
        rows.append(list(p[:12]) + [f"{p[12]:.2f}", "80.00", f"{gross:.2f}", f"{gross * 0.72:.2f}", memo])
    rows.append(["Totals"] + [""] * 13 + [f"{sum(p[12] * 80 for p in PEOPLE):.2f}", f"{sum(p[12] * 80 * 0.72 for p in PEOPLE):.2f}", ""])
    with open(HERE / "sample_payroll_register.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)


def make_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customers"
    ws.append(["Customer Name", "Company", "Contact Email", "Phone", "Billing Address", "Account Number", "Balance", "Notes"])
    data = [
        ("Priya Raman", "Riverbend Analytics LLC", "priya.raman@example.com", "(585) 555-0101", "48 Linden Ave, Fairport, NY 14450", "4410023391", 1250.00, "Prefers email. Spoke with Michael Okafor on 10/3."),
        ("Michael Okafor", "Harbor Point Dental PC", "mokafor@example.com", "585-555-0102", "1207 Elm Street Apt 3, Rochester, NY 14607", "5521039920", 0.00, "Card on file 4111 1111 1111 1111"),
        ("Sofia Delgado", "Delgado & Sons Inc.", "sofia.delgado@example.com", "585.555.0103", "9 Harbor Rd, Pittsford, NY 14534", "7781230044", 430.10, "DOB 07/04/1985 per application"),
    ]
    for r in data:
        ws.append(list(r))
    ws["G2"].number_format = "#,##0.00"
    ws2 = wb.create_sheet("Notes")
    ws2.append(["Account review prepared by Dana Whitfield for Riverbend Analytics LLC"])
    ws2.append(["Escalations go to Sofia Delgado, ext 4410"])
    wb.properties.creator = "Dana Whitfield"
    wb.save(HERE / "sample_customer_list.xlsx")


def make_docx():
    d = Document()
    d.core_properties.author = "Dana Whitfield"
    d.add_heading("Engagement Letter", 1)
    d.add_paragraph("Riverbend Analytics LLC\n48 Linden Ave\nFairport, NY 14450")
    d.add_paragraph("November 16, 2026")
    d.add_paragraph("Dear Ms. Raman,")
    p = d.add_paragraph("Thank you for engaging Harbor Point Advisory Inc. to prepare the 2026 financial statements for ")
    r = p.add_run("Riverbend Analytics LLC")
    r.bold = True
    p.add_run(". Our primary contact will be Priya Raman (priya.raman@example.com, (585) 555-0101). "
              "Please remit the retainer to account number 4410023391, routing 021000021. "
              "The engagement partner is Michael Okafor, SSN on file 913-45-6789 for the state registration. "
              "Invoices are due net 30. The Balance Sheet and Income Statement will be delivered quarterly.")
    d.add_paragraph("Your date of birth 03/12/1988 was used to verify identity with the Department of State.")
    t = d.add_table(rows=3, cols=3)
    t.style = "Table Grid"
    for i, row in enumerate([("Name", "Role", "Phone"), ("Sofia Delgado", "Controller", "585.555.0103"), ("Michael Brennan", "Ops Manager", "585-555-0104")]):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    d.add_paragraph("Sincerely,")
    d.add_paragraph("Dana Whitfield\nHarbor Point Advisory Inc.")
    d.sections[0].footer.paragraphs[0].text = "Riverbend Analytics LLC | Confidential | dana@example.com"
    d.save(HERE / "sample_engagement_letter.docx")


def make_pdf():
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    lines = [
        ("Riverbend Analytics LLC", 16),
        ("Employee Information Form", 13),
        ("", 10),
        ("Employee: Priya Raman", 11),
        ("Employee ID: 10421        SSN: 912-34-5678        DOB: 03/12/1988", 11),
        ("Address: 48 Linden Ave, Fairport, NY 14450", 11),
        ("Phone: (585) 555-0101    Email: priya.raman@example.com", 11),
        ("Emergency contact: Michael Okafor, 585-555-0102", 11),
        ("Direct deposit: routing 021000021, account 4410023391", 11),
        ("", 10),
        ("Approved by Dana Whitfield, HR Manager, Harbor Point Advisory Inc.", 11),
        ("Annual salary: $130,000.00    Start date: 02/01/2021", 11),
    ]
    for text, size in lines:
        page.insert_text((72, y), text, fontsize=size, fontname="helv")
        y += size * 1.8
    doc.set_metadata({"author": "Dana Whitfield", "title": "Employee form - Priya Raman"})
    doc.save(HERE / "sample_employee_form.pdf")


if __name__ == "__main__":
    make_csv(); make_xlsx(); make_docx(); make_pdf()
    print("wrote 4 sample files to", HERE)
