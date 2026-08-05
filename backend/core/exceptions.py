"""Typed application errors.

Each maps to an HTTP status via the handlers registered in main.py, so route
code can raise a domain error and never think about status codes.
"""

from typing import Any


class AppError(Exception):
    """Base class for every expected (non-bug) failure."""

    status_code: int = 500
    error_code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.error_code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


# --- Authentication / authorisation -----------------------------------------


class AuthenticationError(AppError):
    status_code = 401
    error_code = "authentication_failed"
    default_message = "Invalid username, password, or department."


class TokenError(AppError):
    status_code = 401
    error_code = "invalid_token"
    default_message = "Your session has expired. Please sign in again."


class AccountLockedError(AppError):
    status_code = 423
    error_code = "account_locked"
    default_message = "Account temporarily locked after too many failed sign-in attempts."


class AccountInactiveError(AppError):
    status_code = 403
    error_code = "account_inactive"
    default_message = "This account has been deactivated. Contact your administrator."


class PermissionDeniedError(AppError):
    status_code = 403
    error_code = "permission_denied"
    default_message = "You do not have permission to perform this action."


class DepartmentIsolationError(AppError):
    """Raised when a request would cross the department boundary.

    This should never surface in normal operation. If it does, it means a
    filter was missed upstream and the defence-in-depth guard caught it -
    which is always logged at CRITICAL as a potential security incident.
    """

    status_code = 403
    error_code = "department_isolation_violation"
    default_message = "Access denied: this data belongs to another department."


# --- Resources ---------------------------------------------------------------


class NotFoundError(AppError):
    status_code = 404
    error_code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    error_code = "conflict"
    default_message = "That resource already exists."


class ValidationError(AppError):
    status_code = 422
    error_code = "validation_error"
    default_message = "The submitted data is invalid."


# --- Documents / ingestion ---------------------------------------------------


class UnsupportedFileTypeError(AppError):
    status_code = 415
    error_code = "unsupported_file_type"
    default_message = "That file type cannot be ingested."


class FileTooLargeError(AppError):
    status_code = 413
    error_code = "file_too_large"
    default_message = "The uploaded file exceeds the maximum allowed size."


class IngestionError(AppError):
    status_code = 500
    error_code = "ingestion_failed"
    default_message = "The document could not be processed."


# --- LLM / retrieval ---------------------------------------------------------


class LLMUnavailableError(AppError):
    status_code = 503
    error_code = "llm_unavailable"
    default_message = (
        "The AI service is temporarily unavailable. Please try again shortly."
    )


class RetrievalError(AppError):
    status_code = 500
    error_code = "retrieval_failed"
    default_message = "Knowledge base search failed."
