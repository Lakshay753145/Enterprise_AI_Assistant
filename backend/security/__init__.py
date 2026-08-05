from backend.security.dependencies import (
    AdminUser,
    CurrentUser,
    DbSession,
    SuperAdminUser,
    audit_context,
    can_manage_documents,
    get_current_user,
    require_admin,
    require_roles,
    require_super_admin,
)
from backend.security.hash import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from backend.security.isolation import (
    assert_department_match,
    assert_dicts_in_scope,
    assert_record_in_scope,
    filter_records_in_scope,
)
from backend.security.jwt_handler import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_token,
)

__all__ = [
    "AdminUser",
    "CurrentUser",
    "DbSession",
    "SuperAdminUser",
    "TokenPayload",
    "assert_department_match",
    "assert_dicts_in_scope",
    "assert_record_in_scope",
    "audit_context",
    "can_manage_documents",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "filter_records_in_scope",
    "get_current_user",
    "hash_password",
    "require_admin",
    "require_roles",
    "require_super_admin",
    "validate_password_strength",
    "verify_password",
]
