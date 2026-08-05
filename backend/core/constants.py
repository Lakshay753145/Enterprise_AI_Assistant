"""Domain constants: departments, roles, and the vocabulary the guardrails use.

Departments are the security boundary of this system. Adding one here is a
deliberate act: it must also get a knowledge_base/<Name>/ folder and a scoped
SQL view (see scripts/setup_db_roles.sql).
"""

from enum import StrEnum

APP_DESCRIPTION = "PTC Industries Limited & Aerolloy Technologies Limited - Enterprise AI Assistant"
API_PREFIX = "/api/v1"


class Department(StrEnum):
    """The isolated data domains. A user belongs to exactly one."""

    FINANCE = "Finance"
    HR = "HR"
    IT = "IT"
    LEGAL = "Legal"
    OPERATIONS = "Operations"
    PRODUCTION = "Production"
    PURCHASE = "Purchase"
    SALES = "Sales"

    @classmethod
    def values(cls) -> list[str]:
        return [d.value for d in cls]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.values()


class Role(StrEnum):
    """What a user may do *within* their department.

    ADMIN is deliberately NOT a cross-department escape hatch: an admin
    administers their own department. Only SUPER_ADMIN sees everything, and
    that role is intended for one or two IT custodians.
    """

    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

    @classmethod
    def values(cls) -> list[str]:
        return [r.value for r in cls]


#: Roles allowed to upload/delete knowledge-base documents.
DOCUMENT_MANAGER_ROLES = frozenset({Role.ADMIN, Role.SUPER_ADMIN})

#: The only role permitted to read across department boundaries.
CROSS_DEPARTMENT_ROLES = frozenset({Role.SUPER_ADMIN})


# ---------------------------------------------------------------------------
# Human-readable department descriptions.
# Injected into the relevance-gate prompt so the model knows what "in scope"
# means for the signed-in user, without leaking anything about other departments.
# ---------------------------------------------------------------------------
DEPARTMENT_SCOPE: dict[str, str] = {
    Department.FINANCE: (
        "Finance and Accounts: budgets, costing, invoices, payments, taxation "
        "(GST/TDS), audits, financial reporting, expense claims, capex/opex "
        "approvals, and vendor payment terms."
    ),
    Department.HR: (
        "Human Resources: recruitment, onboarding, employee policies, leave and "
        "attendance, payroll rules, appraisals, training and certification, "
        "grievance redressal, code of conduct, and workplace safety training."
    ),
    Department.IT: (
        "Information Technology: IT infrastructure, network and server "
        "administration, ERP and application support, cybersecurity policy, "
        "access control, software licensing, AI knowledge assistant, enterprise "
        "document management, engineering standards, quality manuals, technical "
        "specifications, manufacturing procedures, work instructions, drawings, "
        "customer specifications, and all documents uploaded into the IT knowledge base."
    ),
    Department.LEGAL: (
        "Legal and Compliance: non-disclosure agreements (NDAs), master services "
        "agreements, statutory compliance, intellectual property, regulatory approvals, "
        "litigation, corporate governance, and contract terms."
    ),
    Department.OPERATIONS: (
        "Operations and Maintenance: plant maintenance, equipment calibration, "
        "utility management, facility safety, operational efficiency, EHS compliance, "
        "and logistics scheduling."
    ),
    Department.PRODUCTION: (
        "Production and Manufacturing: investment casting, machining, alloy "
        "melting, heat treatment, shop-floor operations, process routing, work "
        "instructions, tooling, production planning, quality inspection, NDT, "
        "non-conformance handling, and aerospace/defence manufacturing "
        "standards such as AS9100 and NADCAP."
    ),
    Department.PURCHASE: (
        "Purchase and Supply Chain: procurement policy, vendor registration and "
        "evaluation, RFQ and tendering, purchase orders, material receipt and "
        "inspection, inventory and stores, import/export documentation, and "
        "supplier contracts."
    ),
    Department.SALES: (
        "Sales and Business Development: customer tenders, client proposals, "
        "pricing sheets, export orders, aerospace client specifications, market "
        "intelligence, CRM lead tracking, and commercial terms."
    ),
}


# ---------------------------------------------------------------------------
# Refusal messages. Centralised so tone stays consistent and tests can assert
# on them.
# ---------------------------------------------------------------------------
REFUSAL_OUT_OF_SCOPE = (
    "That question falls outside the {department} knowledge base I am "
    "authorised to answer from. I can only help with topics documented in "
    "{department}'s approved documentation. Please rephrase your question, or "
    "contact the relevant department directly."
)

REFUSAL_NO_EVIDENCE = (
    "I could not find this information in the {department} knowledge base. I "
    "will not guess at an answer, because an unverified figure or procedure "
    "could be worse than none at all. If you believe this should be documented, "
    "please raise it with your department administrator."
)

REFUSAL_CROSS_DEPARTMENT = (
    "That information belongs to another department and is outside your access "
    "scope. Your account is granted access to {department} data only. Please "
    "route this request through the owning department."
)

REFUSAL_UNSAFE = (
    "I cannot help with that request. If you believe this is an error, please "
    "contact your IT administrator."
)


# ---------------------------------------------------------------------------
# Ingestion formats. Docling handles all of these; PDF is the primary format
# for Aerolloy's knowledge base.
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".html", ".htm", ".txt"}
)

ALLOWED_UPLOAD_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/markdown",
        "text/html",
        "text/plain",
    }
)


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AnswerSource(StrEnum):
    """Where the final answer came from. Surfaced in the UI for transparency."""

    KNOWLEDGE_BASE = "knowledge_base"
    SQL_AGENT = "sql_agent"
    REFUSED_OUT_OF_SCOPE = "refused_out_of_scope"
    REFUSED_NO_EVIDENCE = "refused_no_evidence"
    ERROR = "error"
