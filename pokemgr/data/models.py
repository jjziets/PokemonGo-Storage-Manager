"""Data models for Pokemon storage."""

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

    @property
    def star_rating(self) -> int:
        """Return 0-4 star rating based on IV percentage."""
        pct = self.iv_pct
        if pct >= 0.978:  # 44+/45 = 4 star (hundo or near)
            return 4
        elif pct >= 0.822:  # 37+/45 = 3 star
            return 3
        elif pct >= 0.667:  # 30+/45 = 2 star
            return 2
        elif pct >= 0.511:  # 23+/45 = 1 star
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
