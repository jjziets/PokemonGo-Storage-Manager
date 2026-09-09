"""Immutable appraisal snapshots and exact acceptance policy.

The scanner must never assemble one Pokemon from fields read at different
times.  This module keeps the complete observation from one screenshot and
turns it into a validated record only when GameMaster evidence agrees exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PIL import Image


# These regions deliberately avoid the animated Pokemon model.  They cover the
# name/HP card, appraisal bars, and the professor's caught-species bubble.
_STABILITY_ROIS = (
    (0.18, 0.35, 0.82, 0.43),
    (0.05, 0.71, 0.54, 0.90),
    (0.02, 0.84, 0.98, 0.985),
)


@dataclass(frozen=True)
class AppraisalSnapshot:
    """All scan evidence read from one and only one screenshot."""

    display_name: str
    detected_species: str
    caught_species: str
    cp: int
    hp: int
    atk: int
    def_: int
    sta: int
    shiny: bool
    shadow: bool
    favorited: bool
    lucky: bool
    gender: str
    weight_tag: str
    height_tag: str
    is_dynamax: bool
    detail_confidence: float
    appraisal_confidence: float
    read_complete: bool = True
    candy_family: str = ""

    @classmethod
    def from_reads(cls, detail: dict, appraisal: dict) -> "AppraisalSnapshot":
        return cls(
            display_name=detail.get("display_name", detail.get("species", "")),
            detected_species=detail.get("species", ""),
            caught_species=detail.get("caught_species", ""),
            cp=int(detail.get("cp", -1)),
            hp=int(detail.get("hp", -1)),
            atk=int(appraisal.get("atk", -1)),
            def_=int(appraisal.get("def_", -1)),
            sta=int(appraisal.get("sta", -1)),
            shiny=bool(detail.get("shiny", False)),
            shadow=bool(detail.get("shadow", False)),
            favorited=bool(detail.get("favorited", False)),
            lucky=bool(detail.get("lucky", False)),
            gender=detail.get("gender", "none"),
            weight_tag=detail.get("weight_tag", ""),
            height_tag=detail.get("height_tag", ""),
            is_dynamax=bool(detail.get("is_dynamax", False)),
            detail_confidence=float(detail.get("confidence", 0.0)),
            appraisal_confidence=float(appraisal.get("confidence", 0.0)),
            read_complete=bool(detail.get("snapshot_read_complete", True)),
            candy_family=detail.get("candy_family", ""),
        )

    @property
    def ivs(self) -> tuple[int, int, int]:
        return self.atk, self.def_, self.sta

    @property
    def identity_key(self) -> tuple:
        return self.detected_species, self.cp, self.hp, *self.ivs

    def as_detail(self) -> dict:
        return {
            "species": self.detected_species,
            "display_name": self.display_name,
            "caught_species": self.caught_species,
            "candy_family": self.candy_family,
            "species_source": "caught" if self.caught_species else "resolved",
            "cp": self.cp,
            "hp": self.hp,
            "shiny": self.shiny,
            "shadow": self.shadow,
            "favorited": self.favorited,
            "lucky": self.lucky,
            "gender": self.gender,
            "weight_tag": self.weight_tag,
            "height_tag": self.height_tag,
            "is_dynamax": self.is_dynamax,
            "confidence": self.detail_confidence,
        }

    def as_appraisal(self) -> dict:
        return {
            "atk": self.atk,
            "def_": self.def_,
            "sta": self.sta,
            "confidence": self.appraisal_confidence,
        }


@dataclass(frozen=True)
class SnapshotDecision:
    accepted: bool
    reason: str
    snapshot: AppraisalSnapshot | None = None
    level: float | None = None
    cp_source: str = "screen"
    exact_form: bool = True


def validate_snapshot(snapshot: AppraisalSnapshot,
                      allow_calculated_cp: bool) -> SnapshotDecision:
    """Accept exact visible evidence or an unambiguous hidden-CP result."""
    if not snapshot.read_complete:
        return SnapshotDecision(False, "snapshot OCR did not finish")
    if any(iv < 0 or iv > 15 for iv in snapshot.ivs):
        return SnapshotDecision(False, f"invalid IVs {snapshot.ivs}")

    from ..pvp.resolver import candidate_species_family, resolve_candidates

    family = snapshot.caught_species.strip() or None
    hp = snapshot.hp if snapshot.hp > 0 else None
    candy = snapshot.candy_family.strip() or None
    candy_kwargs = {"candy_family": candy} if candy else {}

    if snapshot.cp > 0:
        # Without the caught bubble, the visible name may be a nickname.  The
        # global exact signature therefore has to be unique.
        result = resolve_candidates(
            snapshot.atk,
            snapshot.def_,
            snapshot.sta,
            hp=hp,
            cp=snapshot.cp,
            caught_family=family,
            **candy_kwargs,
        )
        resolved = result.resolved
        active_family = family or (candidate_species_family(result.candidates) if candy else None)
        if resolved is None and active_family and result.candidates:
            # Several regional/forms can be numerically identical (for
            # example normal and Galarian Zigzagoon).  The caught bubble still
            # proves the family and every candidate exactly agrees with the
            # visible CP/HP/IV evidence.  Preserve the authoritative generic
            # family instead of inventing a form or skipping a valid row.
            levels = {candidate.level for candidate in result.candidates}
            accepted = replace(snapshot, detected_species=active_family)
            return SnapshotDecision(
                True,
                "visible CP agrees; equivalent form stored as caught family",
                accepted,
                level=levels.pop() if len(levels) == 1 else None,
                cp_source="screen",
                exact_form=False,
            )
        if resolved is None:
            # A bright background can insert an OCR digit into otherwise
            # visible text (live Yungoos CP369 became CP3869). Never replace a
            # positive value that exactly matches GameMaster evidence. When it
            # matches nothing, allow recovery only if species, HP, and IVs
            # leave one exact CP, retaining the caught family when forms agree.
            if allow_calculated_cp and (family or candy) and hp is not None and not result.candidates:
                recovery = resolve_candidates(
                    snapshot.atk,
                    snapshot.def_,
                    snapshot.sta,
                    hp=hp,
                    cp=None,
                    caught_family=family,
                    **candy_kwargs,
                )
                if recovery.resolved is not None:
                    accepted = replace(
                        snapshot,
                        detected_species=recovery.resolved.species,
                        cp=recovery.resolved.expected_cp,
                    )
                    return SnapshotDecision(
                        True,
                        "impossible visible OCR recovered by one unique exact CP",
                        accepted,
                        level=recovery.resolved.level,
                        cp_source="calculated_after_invalid_ocr",
                    )
                calculated_values = {
                    candidate.expected_cp for candidate in recovery.candidates
                }
                active_family = family or (candidate_species_family(recovery.candidates) if candy else None)
                if len(calculated_values) == 1 and active_family:
                    # Match the hidden-CP policy: equivalent forms may leave
                    # one numerical CP without establishing which form it is.
                    levels = {candidate.level for candidate in recovery.candidates}
                    accepted = replace(
                        snapshot,
                        detected_species=active_family,
                        cp=calculated_values.pop(),
                    )
                    return SnapshotDecision(
                        True,
                        "impossible visible OCR is form-ambiguous but numerically unique",
                        accepted,
                        level=levels.pop() if len(levels) == 1 else None,
                        cp_source="calculated_after_invalid_ocr",
                        exact_form=False,
                    )
            return SnapshotDecision(
                False,
                f"visible CP{snapshot.cp} is not an exact unique form/HP/IV match",
            )
        accepted = replace(snapshot, detected_species=resolved.species)
        return SnapshotDecision(
            True,
            "visible CP and snapshot evidence agree",
            accepted,
            level=resolved.level,
            cp_source="screen",
        )

    if not allow_calculated_cp:
        return SnapshotDecision(False, "CP is hidden and calculated CP is disabled")
    if family is None and candy is None:
        return SnapshotDecision(False, "hidden CP has no caught-species evidence")
    if hp is None:
        return SnapshotDecision(False, "hidden CP has no readable HP")

    result = resolve_candidates(
        snapshot.atk,
        snapshot.def_,
        snapshot.sta,
        hp=hp,
        cp=None,
        caught_family=family,
        **candy_kwargs,
    )
    resolved = result.resolved
    if resolved is None and result.candidates:
        calculated_values = {
            candidate.expected_cp for candidate in result.candidates
        }
        active_family = family or (candidate_species_family(result.candidates) if candy else None)
        if len(calculated_values) == 1 and active_family:
            levels = {candidate.level for candidate in result.candidates}
            accepted = replace(
                snapshot,
                detected_species=active_family,
                cp=calculated_values.pop(),
            )
            return SnapshotDecision(
                True,
                "hidden CP is form-ambiguous but numerically unique",
                accepted,
                level=levels.pop() if len(levels) == 1 else None,
                cp_source="calculated",
                exact_form=False,
            )
    if resolved is None:
        return SnapshotDecision(
            False,
            "hidden CP is ambiguous across forms or levels",
        )

    accepted = replace(
        snapshot,
        detected_species=resolved.species,
        cp=resolved.expected_cp,
    )
    return SnapshotDecision(
        True,
        ("hidden CP uniquely derived from candy family, HP, and IVs" if candy and not family
         else "hidden CP uniquely derived from caught species, HP, and IVs"),
        accepted,
        level=resolved.level,
        cp_source="calculated",
    )


def appraisal_region_diffs(first: Image.Image,
                           second: Image.Image) -> tuple[float, ...]:
    """Return mean pixel differences for static appraisal identity regions."""
    a = np.asarray(first.convert("RGB"), dtype=np.int16)
    b = np.asarray(second.convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        return (float("inf"),)

    h, w = a.shape[:2]
    diffs = []
    for x1, y1, x2, y2 in _STABILITY_ROIS:
        a_crop = a[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        b_crop = b[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]
        if not a_crop.size or a_crop.shape != b_crop.shape:
            diffs.append(float("inf"))
            continue
        diffs.append(float(np.mean(np.abs(a_crop - b_crop))))
    return tuple(diffs)


def appraisal_frames_stable(first: Image.Image, second: Image.Image,
                            threshold: float = 1.5) -> bool:
    return max(appraisal_region_diffs(first, second)) <= threshold


def appraisal_transition_observed(before: Image.Image, after: Image.Image,
                                  threshold: float = 3.0) -> bool:
    return max(appraisal_region_diffs(before, after)) >= threshold
