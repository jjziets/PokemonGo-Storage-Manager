"""Data models for Pokemon storage."""

# TRACEWEAVER: file-role=stored-pokemon-appraisal; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001

from dataclasses import dataclass


@dataclass
class Pokemon:
    """A single Pokemon record from the database."""
    id: int
    species: str                            # real species (from fingerprint)
    cp: int
    atk: int
    def_: int
    sta: int
    iv_total: int
    iv_pct: float
    shiny: bool
    shadow: bool
    lucky: bool
    favorited: bool
    position: int
    decision: str | None = None         # KEEP or TRANSFER
    decision_reason: str | None = None   # BEST_OVERALL, BEST_SHINY, etc.
    display_name: str = ""                  # what's shown on screen (nickname)
    gender: str = "none"                    # "male", "female", or "none"
    weight_tag: str = ""                    # "LIGHTEST", "HEAVIEST", or ""
    height_tag: str = ""                    # "SHORTEST", "TALLEST", or ""
    is_dynamax: bool = False                # True if Dynamax/Gmax
    hp: int = -1                            # max HP from screen
    pvp_rank_gl: int | None = None
    pvp_rank_ul: int | None = None
    screenshot_path: str = ""
    confidence: float = 0.0
    scan_session_id: str = ""

    # TRACEWEAVER: entrypoint=star_rating; req=REQ-MASS-001; trace=TRACE-MASS-001; ver=VER-SCAN-001
    @property
    def star_rating(self) -> int:
        """Return the exact IV star rating, or -1 for incomplete/invalid IVs."""
        ivs = (self.atk, self.def_, self.sta)
        if any(type(value) is not int or not 0 <= value <= 15 for value in ivs):
            return -1
        total = sum(ivs)
        if total == 45:
            return 4
        elif total >= 37:
            return 3
        elif total >= 30:
            return 2
        elif total >= 23:
            return 1
        return 0

    def summary(self) -> str:
        decision_str = ""
        if self.decision:
            reason = f" ({self.decision_reason})" if self.decision_reason else ""
            decision_str = f" → {self.decision}{reason}"
        return (
            f"{self.species} CP{self.cp} "
            f"{self.atk}/{self.def_}/{self.sta} "
            f"({self.iv_pct:.0%}){decision_str}"
        )
