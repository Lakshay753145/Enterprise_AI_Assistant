"""ORM model registry.

Importing this package registers every table on ``Base.metadata``, which is
what alembic autogenerate reads. Keep every model imported here.
"""

from backend.models.audit import AuditLog
from backend.models.chat import Conversation, Message
from backend.models.documents import Document, DocumentChunk
from backend.models.employees import Employee
from backend.models.users import User

__all__ = [
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "Employee",
    "Message",
    "User",
]