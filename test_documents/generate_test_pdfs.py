from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import os

styles = getSampleStyleSheet()


def create_pdf(filename, title, sections):
    doc = SimpleDocTemplate(filename)

    story = []
    story.append(Paragraph(f"<b><font size='18'>{title}</font></b>", styles["Title"]))

    for heading, text in sections:
        story.append(Paragraph(f"<br/><b>{heading}</b>", styles["Heading2"]))
        story.append(Paragraph(text, styles["BodyText"]))

    doc.build(story)


finance_sections = [
    ("Expense Reimbursement",
     "Employees may claim hotel expenses up to ₹3000 per night.<br/>"
     "Meal allowance is ₹600 per day.<br/>"
     "Taxi reimbursement requires original bills.<br/>"
     "Claims must be submitted within 15 days."),

    ("Travel Policy",
     "Economy class should be used for domestic flights.<br/>"
     "Train travel should be AC 2 Tier.<br/>"
     "Company cabs should be preferred."),

    ("Vendor Payments",
     "Invoices are processed every Friday.<br/>"
     "Payment cycle is 30 days.<br/>"
     "Purchase Order is mandatory."),

    ("Budget Approval",
     "Budgets above ₹5 lakh require CFO approval.<br/>"
     "Quarterly reviews are mandatory.")
]


hr_sections = [
    ("Leave Policy",
     "Employees receive 18 paid leaves annually.<br/>"
     "Medical leave requires doctor's certificate."),

    ("Attendance",
     "Office timing is 9 AM to 6 PM.<br/>"
     "Grace time is 15 minutes."),

    ("Recruitment",
     "Technical interview has two rounds.<br/>"
     "HR conducts final discussion."),

    ("Performance Review",
     "Annual appraisal happens in April.<br/>"
     "KPIs reviewed quarterly.")
]


production_sections = [
    ("Heat Treatment",
     "Titanium forgings shall be solution treated at 955°C ±10°C.<br/>"
     "Cooling shall be performed in still air.<br/>"
     "Hardness inspection is mandatory."),

    ("Machine Operation",
     "Inspect CNC machines before every shift.<br/>"
     "Lubrication checked daily."),

    ("Safety",
     "Helmet, gloves and safety shoes are mandatory."),

    ("Quality",
     "Dimensional inspection required for every batch.")
]


purchase_sections = [
    ("Vendor Selection",
     "Minimum three quotations are required."),

    ("Purchase Orders",
     "PO above ₹2 lakh require GM approval."),

    ("Material Receipt",
     "Stores verifies quantity.<br/>"
     "Quality verifies specifications."),

    ("Inventory",
     "Monthly stock verification is mandatory.")
]


os.makedirs("knowledge_base/Finance", exist_ok=True)
os.makedirs("knowledge_base/HR", exist_ok=True)
os.makedirs("knowledge_base/Production", exist_ok=True)
os.makedirs("knowledge_base/Purchase", exist_ok=True)

create_pdf(
    "knowledge_base/Finance/finance.pdf",
    "Finance Department Knowledge Base",
    finance_sections,
)

create_pdf(
    "knowledge_base/HR/hr.pdf",
    "HR Department Knowledge Base",
    hr_sections,
)

create_pdf(
    "knowledge_base/Production/production.pdf",
    "Production Department Knowledge Base",
    production_sections,
)

create_pdf(
    "knowledge_base/Purchase/purchase.pdf",
    "Purchase Department Knowledge Base",
    purchase_sections,
)

print("All PDFs created successfully.")