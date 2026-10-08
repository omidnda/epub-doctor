"""Process-local, nonblocking admission for synchronous EPUB operations."""

import os
from contextlib import contextmanager
from functools import wraps
from threading import BoundedSemaphore, local

MAX_CONCURRENT_OPERATIONS = int(os.environ.get('EPUB_DOCTOR_MAX_CONCURRENT_OPERATIONS', '1'))
if MAX_CONCURRENT_OPERATIONS < 1:
    raise ValueError('EPUB_DOCTOR_MAX_CONCURRENT_OPERATIONS must be a positive integer')

_slots = BoundedSemaphore(MAX_CONCURRENT_OPERATIONS)
_owned = local()


class ServerBusyError(RuntimeError):
    """Capacity is exhausted; no input processing has started."""


@contextmanager
def operation_slot():
    """Hold capacity across a synchronous call stack, including nested service calls.

    Thread ownership permits adapter -> service nesting without taking two slots.
    Never hold this context across await or transfer ownership to another thread.
    """
    if getattr(_owned, 'active', False):
        yield
        return
    if not _slots.acquire(blocking=False):
        raise ServerBusyError('server_busy: EPUB processing capacity is full; retry later')
    _owned.active = True
    try:
        yield
    finally:
        _owned.active = False
        _slots.release()


def limited_operation(operation):
    @wraps(operation)
    def limited(*args, **kwargs):
        with operation_slot():
            return operation(*args, **kwargs)
    return limited
