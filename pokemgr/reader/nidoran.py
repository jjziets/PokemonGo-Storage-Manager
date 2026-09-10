"""Resolve only anchored caught-species Nidoran text and strict same-frame sex.

The caller must supply the professor's caught text, never an editable nickname
or a fuzzy display-name match. This helper does not infer species from stats.
"""

# TRACEWEAVER: file-role=caught-nidoran-identity; req=REQ-IDENTITY-001; trace=TRACE-IDENTITY-001; ver=VER-SCAN-001
# TRACEWEAVER: file-role=caught-nidoran-cp-scope; req=REQ-SCAN-001; trace=TRACE-SCAN-001; ver=VER-SCAN-001
# TRACEWEAVER: file-role=caught-nidoran-review-evidence; req=REQ-SCAN-004; trace=TRACE-SCAN-004; ver=VER-SCAN-001
import re


_EXPLICIT = re.compile(r"nidoran(?:\s*([♂♀])|\s+(male|female))")
_AMBIGUOUS = re.compile(r"nidoran\s*(?:[o0-9]['’]?|['’])?")


# TRACEWEAVER: entrypoint=is_nidoran_read; req=REQ-IDENTITY-001; trace=TRACE-IDENTITY-001; ver=VER-SCAN-001
def is_nidoran_read(caught_name: str) -> bool:
    """Recognize the literal stem; unknown suffixes must remain unresolved."""
    return isinstance(caught_name, str) and caught_name.strip().casefold().startswith("nidoran")


# TRACEWEAVER: entrypoint=resolve_caught_nidoran; req=REQ-IDENTITY-001; trace=TRACE-IDENTITY-001; ver=VER-SCAN-001
# TRACEWEAVER: entrypoint=resolve_caught_nidoran; req=REQ-SCAN-001; trace=TRACE-SCAN-001; ver=VER-SCAN-001
# TRACEWEAVER: entrypoint=resolve_caught_nidoran; req=REQ-SCAN-004; trace=TRACE-SCAN-004; ver=VER-SCAN-001
def resolve_caught_nidoran(caught_name: str, observed_gender: str) -> str | None:
    """Return an exact canonical species, unchanged unrelated text, or None.

    An actual sex symbol/word supplies its own evidence. A missing symbol or
    one OCR glyph artifact (o, ASCII digit, optional quote) loses that distinction
    and requires a strict same-frame sex icon. The artifact never determines sex.
    Contradictions are never repaired using a prior frame, nickname, CP, or a
    more plausible candidate.
    """
    if not isinstance(caught_name, str):
        return None
    if not is_nidoran_read(caught_name):
        return caught_name
    name = " ".join(caught_name.split()).casefold()
    gender = (observed_gender if type(observed_gender) is str
              and observed_gender in ("male", "female") else None)
    explicit = _EXPLICIT.fullmatch(name)
    if explicit:
        sex = explicit.group(2) or {"♂": "male", "♀": "female"}[explicit.group(1)]
        if gender is not None and gender != sex:
            return None
        return f"Nidoran {sex.title()}"
    if _AMBIGUOUS.fullmatch(name) and gender is not None:
        return f"Nidoran {gender.title()}"
    return None
