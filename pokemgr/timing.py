"""Opt-in local scan timings; set POKEMGR_SCAN_TIMING=1 before launching.

Events contain phase/context/timing only, never function arguments or results.
Completed spans may overlap across threads; do not sum nested durations as if
they were sequential. The setting is frozen at import for the next app run.
"""

from contextvars import ContextVar, copy_context
from functools import partial, wraps
from itertools import count
import json
import logging
import os
import threading
from time import monotonic
from uuid import uuid4


ENABLED = os.environ.get("POKEMGR_SCAN_TIMING") == "1"
log = logging.getLogger("pokemgr.timing")
_parent = ContextVar("scan_timing_parent", default=None)
_fields = ContextVar("scan_timing_fields", default=None)
_ids = count(1)
_run_id = uuid4().hex


class _NoSpan:
    def __enter__(self):
        return None

    def __exit__(self, *_exception):
        return False


_NO_SPAN = _NoSpan()


class _Span:
    def __init__(self, phase, session_id, position):
        self.phase = phase
        self.fields = dict(_fields.get() or {})
        if isinstance(session_id, str):
            self.fields["session_id"] = session_id
        if isinstance(position, int):
            self.fields["position"] = position

    def __enter__(self):
        self.parent_id = _parent.get()
        self.span_id = f"{_run_id}:{next(_ids)}"
        self.parent_token = _parent.set(self.span_id)
        self.fields_token = _fields.set(self.fields)
        self.thread_id = threading.get_ident()
        self.thread_name = threading.current_thread().name
        self.start_s = monotonic()
        return None

    def __exit__(self, exception_type, _exception, _traceback):
        duration_s = monotonic() - self.start_s
        _fields.reset(self.fields_token)
        _parent.reset(self.parent_token)
        event = {
            "phase": self.phase,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_s": self.start_s,
            "duration_s": duration_s,
            "thread_id": self.thread_id,
            "thread_name": self.thread_name,
            "status": "error" if exception_type is not None else "ok",
            **self.fields,
        }
        if exception_type is not None:
            event["exception_type"] = exception_type.__name__
        try:
            log.info("SCAN_TIMING %s", json.dumps(event, separators=(",", ":")))
        except Exception:
            # Profiling must not change a scan's return value or mask its error
            # if a local logging handler fails.
            pass
        return False


def span(phase, *, session_id=None, position=None):
    """Time a synchronous block, or return a shared no-op when disabled."""
    return _Span(phase, session_id, position) if ENABLED else _NO_SPAN


def timed(phase, *, scan=False):
    """Decorate a synchronous operation; disabled decorators return it unchanged.

    ``scan`` adds the scanner's session and next 1-based storage position.
    An advance therefore belongs to the position it is about to acquire.
    """
    def decorate(function):
        if not ENABLED:
            return function

        @wraps(function)
        def measured(*args, **kwargs):
            session_id = position = None
            if scan and args:
                try:
                    owner = args[0]
                    session_id = getattr(owner, "session_id", None)
                    offset = getattr(owner, "skip_first_n", 0)
                    visited = getattr(owner, "visited_count", 0)
                    if isinstance(offset, int) and isinstance(visited, int):
                        position = max(0, offset) + visited + 1
                except Exception:
                    pass
            with span(phase, session_id=session_id, position=position):
                return function(*args, **kwargs)

        return measured
    return decorate


def bind_context(function):
    """Copy timing context for one worker thread; disabled targets are unchanged."""
    return partial(copy_context().run, function) if ENABLED else function
