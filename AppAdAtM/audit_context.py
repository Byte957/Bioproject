from contextlib import contextmanager
from contextvars import ContextVar


_audit_user_id = ContextVar('audit_user_id', default=None)


def current_audit_user_id():
    return _audit_user_id.get()


@contextmanager
def audit_user(user_id):
    token = _audit_user_id.set(user_id)
    try:
        yield
    finally:
        _audit_user_id.reset(token)
