# TRACEWEAVER: file-role=stable-scan-transition; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""Stable, evidence-gated scanning of Pokemon Go appraisal screens.

Each position is acquired from settled frames and validated exactly before
being committed at most once. Hidden CP can be revealed by a bounded model
animation with full identity checks before and after. A calibrated swipe
advances only after a different settled visual identity is confirmed.
"""

import time
import logging
import uuid
import random
import math
from typing import Callable

from ..adb.controller import ADBController, StreamCaptureInvalidated
from ..calibration.profile import CalibrationProfile
from ..calibration.regions import is_tablet_layout
from ..reader.screen import ScreenReader, PokemonRead
from ..data.database import PokemonDatabase
from .. import config
from .. import timing
from ..config import (
    human_delay,
    maybe_micro_break,
    MIN_BATTERY_LEVEL,
    BATTERY_CHECK_INTERVAL,
    MICRO_BREAK_INTERVAL,
)

log = logging.getLogger(__name__)
human_delay = timing.timed("wait.human_delay")(human_delay)
maybe_micro_break = timing.timed("wait.micro_break")(maybe_micro_break)


class _UnresolvedAppraisalName(RuntimeError):
    """Read-only recovery exhausted; the current appraisal can be reviewed."""

    def __init__(self, frame):
        super().__init__("name OCR remained inconsistent after 3 reads; CP recovery skipped")
        self.frame = frame


class _AppraisalConfirmationInvalidated(RuntimeError):
    """A pause discarded the read-only confirmation before any recovery input."""


class _PreInputIVConflict(RuntimeError):
    """Only an IV read changed before any CP recovery input was sent."""

    def __init__(self, before, frame, anchor, pause_generation):
        super().__init__("CP recovery appraisal identity changed before closing: IV reads disagree")
        self.frame = frame
        self.discarded_frames = (before, frame)
        self.anchor = anchor
        self.pause_generation = pause_generation


class IndexingStateMachine:
    """Acquire → validate → commit → verified-transition scan loop."""

    def __init__(self, adb: ADBController, profile: CalibrationProfile,
                 db: PokemonDatabase):
        self.adb = adb
        self.profile = profile
        self.capture_size_tags = bool(config.CAPTURE_SIZE_TAGS)
        self.reader = ScreenReader(
            profile, fast_cp=True, read_size_tags=self.capture_size_tags,
        )
        self.reader._save_screenshots = False
        self.regions = profile.regions
        self.db = db

        from ..adb.navigator import GameNavigator
        self.nav = GameNavigator(adb, profile.regions)

        self.session_id = str(uuid.uuid4())
        self.count = 0
        self.visited_count = 0
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5
        self.max_count = 0              # 0 = unlimited
        self._start_time = 0.0

        self._first_pokemon_key: tuple | None = None
        self._last_pokemon_key: tuple | None = None
        self._same_count = 0            # how many times the same Pokemon appeared in a row
        self._paused = False
        self._abort = False
        self.unfavorite_all = False
        self._next_break_at = 0
        self.skipped_count = 0
        self.skip_first_n = 0          # skip this many Pokemon before scanning (for resume)
        self.skip_delay = 0.05         # delay between swipes when skipping
        self.resume_target_species = "" # optional: verify we landed on this species
        self.resume_target_cp = 0      # optional: verify we landed on this CP
        # Freeze correctness options for this worker.  The GUI refresh timer may
        # continue updating global speed settings while a scan is in progress.
        self.use_calculated_cp = config.calculated_cp_recovery_enabled()
        self.use_cp_animation = config.cp_animation_recovery_enabled()
        self._last_stable_image = None
        self._last_accepted_image = None
        self._last_accepted_pause_generation = None
        self._last_validated_identity_key: tuple | None = None
        self._previous_validated_identity_key: tuple | None = None
        self._transition_required = False
        self._settled_frame_pair = None
        self._pause_generation = 0
        self._reader_threads = []

        # Callbacks
        self.on_progress: Callable[[int, PokemonRead], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_finished: Callable[[int], None] | None = None
        self.on_paused: Callable[[], None] | None = None

    def start(self, expected_total: int | None = None):
        """Scan using stable, single-frame observations.

        Acquire a settled frame, validate exact evidence, commit once, and
        advance from a confirmed appraisal screen. Model-animation CP recovery
        requires the same complete identity before and after the detail visit.
        """
        try:
            return self._start_stable_scan(expected_total)
        finally:
            # Setup can fail before a database session exists (for example,
            # after OCR warmup but during device validation).
            self._close_reader()

    def _start_stable_scan(self, expected_total: int | None = None):
        """Run the snapshot loop, favoriting unresolved positions for review."""
        self.reader.prepare_native_ocr()
        try:
            from ..reader.ocr_engine import _get_paddle
            _get_paddle()
        except Exception:
            # Tesseract remains available and Paddle can retry lazily.
            pass

        log.info(
            "Starting stable scan session: %s (calculated hidden CP: %s; slow CP recovery: %s)",
            self.session_id,
            self.use_calculated_cp,
            self.use_cp_animation,
        )
        self.db.create_session(
            self.session_id,
            self.adb.get_device_info().fingerprint,
        )
        self._start_time = time.time()
        self._next_break_at = random.randint(*MICRO_BREAK_INTERVAL)

        # The target is the number of *new* positions to inspect after any
        # resume skips.  Stored rows and visited positions are deliberately
        # separate because an ambiguous Pokemon may be skipped.
        targets = [value for value in (expected_total, self.max_count)
                   if value is not None and value > 0]
        target_positions = min(targets) if targets else 0
        if target_positions:
            log.info("Stable scan target: %d new storage positions", target_positions)

        resume_offset = max(0, int(self.skip_first_n))
        resume_target_pending = bool(
            self.resume_target_species.strip() or self.resume_target_cp > 0
        )

        try:
            # Resume is correctness-first.  Every skip starts from a confirmed
            # appraisal and must settle on visual identity evidence that differs
            # from the prior accepted screen.  Resume skips establish a position
            # offset; they do not consume the target of new positions to scan.
            resume_frame = None
            resume_frame_ready_at = 0.0
            for index in range(resume_offset):
                while not self._abort:
                    while self._paused and not self._abort:
                        resume_frame = None
                        with timing.span("wait.pause", session_id=self.session_id):
                            time.sleep(0.25)
                    if self._abort:
                        break
                    # A just-settled resume frame can immediately authorize the
                    # next swipe: no OCR or other input intervened. Longer gaps
                    # and pauses require a fresh capture, as normal scanning does.
                    current = (resume_frame if resume_frame is not None
                               and time.monotonic() - resume_frame_ready_at <= 0.25
                               else self._fast_screencap())
                    resume_frame = None
                    if self._paused or self._abort:
                        continue
                    screen = self.nav.detect_screen(current)
                    # Pause can arrive during capture or screen recognition.
                    # Retry this same skip with fresh evidence after resuming.
                    if self._paused or self._abort:
                        continue
                    if screen != "appraisal":
                        raise RuntimeError(
                            f"Resume stopped at {index}: appraisal screen not confirmed"
                        )
                    self._last_stable_image = current
                    if not self._fast_swipe():
                        if self._abort:
                            break
                        raise RuntimeError(
                            f"Resume stopped at {index}: swipe was not sent"
                        )
                    break
                if self._abort:
                    break
                # This setting is user-facing and intentionally applies even
                # though stable-frame acquisition adds its own observation wait.
                with timing.span("wait.resume", session_id=self.session_id):
                    time.sleep(max(0.0, float(self.skip_delay)))
                stable, status = self._wait_for_stable_appraisal(
                    previous_accepted=current,
                    require_transition=True,
                )
                if stable is None:
                    raise RuntimeError(f"Resume stopped at {index}: {status}")
                self._last_stable_image = stable
                self._transition_required = False
                resume_frame = stable
                resume_frame_ready_at = stable.info.get(
                    "pokemgr_capture_finished_at", float("-inf"),
                )

            while not self._abort:
                if self._paused:
                    with timing.span("wait.pause", session_id=self.session_id):
                        time.sleep(0.25)
                    continue

                self._next_break_at = maybe_micro_break(
                    self.visited_count,
                    self._next_break_at,
                )
                if (self.visited_count > 0 and
                        self.visited_count % BATTERY_CHECK_INTERVAL == 0):
                    self._check_battery()

                accepted_generation = self._pause_generation
                decision, frame, failure_kind, reason = (
                    self._acquire_validated_snapshot(
                        previous_accepted=self._last_stable_image,
                        require_transition=self._transition_required,
                    )
                )
                if failure_kind == "reacquire":
                    # Pause invalidated a read-only confirmation. Retain the
                    # pending transition and restart without counting/input.
                    continue
                self._transition_required = False
                if decision is None and failure_kind == "transition_returned_to_previous":
                    accepted_generation = self._pause_generation
                    decision, frame, failure_kind, reason = (
                        self._recover_failed_transition(frame)
                    )

                if decision is None:
                    if failure_kind != "invalid" or frame is None:
                        message = f"Stopped: {reason}"
                        log.error(message)
                        if self.on_error:
                            self.on_error(message)
                        if failure_kind == "aborted" and self._abort:
                            break
                        raise RuntimeError(message)

                    if resume_target_pending:
                        raise RuntimeError(
                            "Resume target could not be verified from the first "
                            f"new position: {reason}"
                        )

                    # Preserve unresolved Pokemon for review before moving on.
                    # The favorite action is idempotent and must be observed
                    # on this same appraisal before the position is counted.
                    frame = self._favorite_unresolved_snapshot(frame)
                    if self._abort or frame is None:
                        break
                    self.skipped_count += 1
                    self.visited_count += 1
                    self._last_stable_image = frame
                    # This position did not yield a trustworthy identity.  Do
                    # not let a later visually-inconclusive transition compare
                    # against an older Pokemon and skip over the ambiguity.
                    self._last_validated_identity_key = None
                    self._previous_validated_identity_key = None
                    self._last_accepted_image = None
                    self._last_accepted_pause_generation = None
                    message = (
                        f"Skipped position {self.visited_count}: {reason}; "
                        "favorited for review"
                    )
                    log.warning(message)
                    if self.on_error:
                        self.on_error(message)
                else:
                    snapshot = decision.snapshot
                    if snapshot is None:
                        raise RuntimeError("accepted snapshot had no record")

                    if resume_target_pending:
                        self._validate_resume_target(snapshot)
                        resume_target_pending = False

                    # Unfavorite applies only to accepted rows when explicitly
                    # enabled. Unresolved rows keep their review favorite.
                    unfavorite_verified = False
                    if self.unfavorite_all and snapshot.favorited:
                        decision, frame = self._unfavorite_accepted_snapshot(decision, frame)
                        if decision is None:
                            break
                        snapshot = decision.snapshot
                        unfavorite_verified = True

                    detail = snapshot.as_detail()
                    appraisal = snapshot.as_appraisal()
                    self._store_pokemon(
                        detail,
                        appraisal,
                        position=resume_offset + self.visited_count,
                    )
                    if unfavorite_verified:
                        # Persist a confirmed star change before navigating,
                        # even when the normal scan batch is not yet full.
                        self.db.flush()
                    # A visible CP can be accepted even when HP was unreadable,
                    # but an incomplete tuple must never authorize the raw-
                    # identity fallback for the next carousel position.
                    self._previous_validated_identity_key = self._last_validated_identity_key
                    self._last_validated_identity_key = (
                        self._complete_identity_key(snapshot)
                    )
                    self.visited_count += 1
                    self._last_stable_image = frame
                    self._last_accepted_image = (
                        frame if not self._paused and self._pause_generation == accepted_generation
                        else None
                    )
                    self._last_accepted_pause_generation = accepted_generation
                    self.consecutive_failures = 0
                    log.info(
                        "Accepted %s CP%d from %s evidence at L%s",
                        snapshot.detected_species,
                        snapshot.cp,
                        decision.cp_source,
                        decision.level if decision.level is not None else "?",
                    )

                if target_positions and self.visited_count >= target_positions:
                    log.info("Reached %d verified storage positions", target_positions)
                    break

                if self._abort:
                    break
                if not self._advance_from_confirmed_appraisal():
                    message = "Stopped: could not verify and advance appraisal"
                    log.error(message)
                    if self.on_error:
                        self.on_error(message)
                    raise RuntimeError(message)
                self._transition_required = True
        finally:
            self.db.complete_session(self.session_id, self.count)
            elapsed = time.time() - self._start_time
            rate = self.count / elapsed if elapsed > 0 else 0
            log.info(
                "Stable scan ended: %d stored, %d visited, %d skipped in %.1fs (%.1f/min)",
                self.count,
                self.visited_count,
                self.skipped_count,
                elapsed,
                rate * 60,
            )

        if self.on_finished:
            self.on_finished(self.count)

    def _validate_resume_target(self, snapshot) -> None:
        """Fail unless the first post-skip snapshot matches the resume target."""
        import unicodedata

        def _normalise(value: str) -> str:
            return unicodedata.normalize("NFKC", value).strip().casefold()

        expected_species = _normalise(self.resume_target_species)
        if expected_species:
            species_names = {
                _normalise(snapshot.detected_species),
                _normalise(snapshot.caught_species),
            }
            # A family target such as "Zorua" intentionally accepts an exact
            # resolved form such as "Zorua (Hisuian)"; arbitrary prefixes and
            # substrings remain disallowed.
            detected = snapshot.detected_species.split(" (", 1)[0]
            species_names.add(_normalise(detected))
            species_names.discard("")
            if expected_species not in species_names:
                raise RuntimeError(
                    "Resume target mismatch: expected species "
                    f"{self.resume_target_species}, got {snapshot.detected_species}"
                )

        if self.resume_target_cp > 0 and snapshot.cp != self.resume_target_cp:
            raise RuntimeError(
                "Resume target mismatch: expected "
                f"CP{self.resume_target_cp}, got CP{snapshot.cp}"
            )

# TRACEWEAVER: entrypoint=_acquire_validated_snapshot; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
    @timing.timed("scan.acquire", scan=True)
    def _acquire_validated_snapshot(self, previous_accepted=None,
                                    require_transition: bool = False,
                                    save_failure_evidence: bool = True):
        """Return one validated decision or a typed acquisition failure."""
        from dataclasses import replace
        from .snapshot import (
            AppraisalSnapshot,
            appraisal_frames_stable,
            validate_snapshot,
        )

        last_frame = None
        last_reason = "snapshot did not validate"
        transition_pending = require_transition
        cp_recovery_attempted = False
        iv_reacquisition = None
        iv_pair_pending = False

        for attempt in range(3):
            self._settled_frame_pair = None
            if self._abort:
                return None, last_frame, "aborted", "abort requested"

            pair_pause_generation = self._pause_generation
            frame, status = self._wait_for_stable_appraisal(
                previous_accepted=previous_accepted,
                require_transition=transition_pending,
                allow_structured_fallback=transition_pending,
            )
            settled_pair = self._settled_frame_pair
            acquisition_frames = settled_pair if isinstance(settled_pair, tuple) else (frame,)
            transition_pair = settled_pair
            self._settled_frame_pair = None
            if frame is None:
                return None, last_frame, status, status.replace("_", " ")
            if iv_reacquisition is not None:
                if self._abort:
                    return None, frame, "aborted", "abort requested"
                if (self._paused or self._pause_generation != iv_reacquisition.pause_generation):
                    return None, None, "reacquire", "pause invalidated IV reacquisition"
                if status != "stable":
                    return None, frame, "cp_recovery_failed", "IV reacquisition could not confirm the witnessed position"
            transition_pending = False
            last_frame = frame

            detail, appraisal = self._read_appraisal_snapshot(frame)
            if self._abort:
                return None, frame, "aborted", "abort requested"
            snapshot = AppraisalSnapshot.from_reads(detail, appraisal)
            renewed_confirmation = None
            if iv_reacquisition is not None:
                # A conflicting pair is discarded in full. Neither image may
                # count toward the replacement proof, even if its IVs recur.
                if (not isinstance(settled_pair, tuple) or len(settled_pair) != 2
                        or settled_pair[1] is not frame
                        or settled_pair[0] is frame
                        or any(image is discarded for image in settled_pair
                               for discarded in iv_reacquisition.discarded_frames)
                        or not self._specimen_frame_sources_ordered(iv_reacquisition.frame, settled_pair[0])
                        or not self._independent_frame_sources(*settled_pair)
                        or not appraisal_frames_stable(*settled_pair)):
                    return None, frame, "cp_recovery_failed", "IV reacquisition needs two new independent settled frames"
                capture_bounds = tuple(image.info.get(key)
                                       for image in (iv_reacquisition.frame, *settled_pair)
                                       for key in ("pokemgr_capture_started_at", "pokemgr_capture_finished_at"))
                if (not all(type(value) in (int, float) and math.isfinite(value)
                            for value in capture_bounds)
                        or not (0 < capture_bounds[0] <= capture_bounds[1] < capture_bounds[2]
                                <= capture_bounds[3] < capture_bounds[4] <= capture_bounds[5])):
                    return None, frame, "cp_recovery_failed", "IV reacquisition capture times are not independently fresh"
                older = settled_pair[0]
                if (self.nav.detect_screen(older) != "appraisal"
                        or not self.reader.are_bars_visible(older)
                        or self.nav.detect_screen(frame) != "appraisal"
                        or not self.reader.are_bars_visible(frame)):
                    return None, frame, "cp_recovery_failed", "IV reacquisition lost appraisal"
                older_detail, older_appraisal = self._read_appraisal_snapshot(older)
                if self._abort:
                    return None, frame, "aborted", "abort requested"
                if (self._paused or self._pause_generation != iv_reacquisition.pause_generation):
                    return None, None, "reacquire", "pause invalidated IV reacquisition"
                renewed_confirmation = AppraisalSnapshot.from_reads(older_detail, older_appraisal)
                if any(self._non_iv_recovery_identity(read) != iv_reacquisition.anchor
                       for read in (snapshot, renewed_confirmation)):
                    return None, frame, "cp_recovery_failed", "IV reacquisition changed or lost non-IV identity"
                if self._cp_recovery_identity(snapshot) != self._cp_recovery_identity(renewed_confirmation):
                    iv_reacquisition.discarded_frames += settled_pair
                    iv_reacquisition.frame = frame
                    iv_pair_pending = True
                    transition_pending = require_transition
                    cp_recovery_attempted = False
                    last_reason = "IV reads still disagree after bounded reacquisition"
                    log.info("IV reacquisition pair disagreed (%d/3); discarding both reads", attempt + 1)
                    continue
                iv_pair_pending = False
            decision = self._validate_appraisal_snapshot(snapshot, frame)
            if self._abort:
                return None, frame, "aborted", "abort requested"
            recovery_identity = self._cp_recovery_identity(snapshot)
            force_confirmation = False
            if renewed_confirmation is not None:
                renewed_decision = self._validate_appraisal_snapshot(renewed_confirmation, settled_pair[0])
                if self._abort:
                    return None, frame, "aborted", "abort requested"
                if (self._paused or self._pause_generation != iv_reacquisition.pause_generation):
                    return None, None, "reacquire", "pause invalidated IV reacquisition"
                both_exact = decision.accepted and renewed_decision.accepted
                if (both_exact and self._complete_identity_key(decision.snapshot)
                        != self._complete_identity_key(renewed_decision.snapshot)):
                    return None, frame, "cp_recovery_failed", "IV reacquisition has conflicting exact CP results"
                calculated_pair_confirmed = bool(both_exact)
                force_confirmation = decision.accepted and not renewed_decision.accepted
            else:
                calculated_pair_confirmed = self._calculated_cp_confirmed_by_pair(
                    snapshot, decision, frame, settled_pair, pair_pause_generation,
                )
            # The pair is local to this acquisition and never survives recovery
            # input or a retry. Only its completed independent proof is retained.
            settled_pair = None
            if self._abort:
                return None, frame, "aborted", "abort requested"
            if force_confirmation and cp_recovery_attempted:
                return None, frame, "cp_recovery_failed", "IV reacquisition did not independently confirm CP"
            if (
                not cp_recovery_attempted
                and not calculated_pair_confirmed
                and recovery_identity is not None
                and (not decision.accepted
                     or decision.cp_source.startswith("calculated")
                     or force_confirmation)
            ):
                cp_recovery_attempted = True
                # Recovery may move through detail/preview screens. Its old
                # settled pair cannot prove the subsequent carousel position.
                transition_pair = None
                try:
                    decision, frame = self._recover_cp_with_model_taps(
                        snapshot, frame, save_failure_evidence=save_failure_evidence,
                    )
                except _AppraisalConfirmationInvalidated as exc:
                    return None, None, "reacquire", str(exc)
                except _PreInputIVConflict as exc:
                    if (self._paused or self._pause_generation != pair_pause_generation):
                        return None, None, "reacquire", "pause invalidated IV reacquisition"
                    # An unobserved carousel move must not become a witnessed
                    # position merely because another read was requested.
                    if status != "stable":
                        return None, exc.frame, "cp_recovery_failed", str(exc)
                    discarded = iv_reacquisition.discarded_frames if iv_reacquisition is not None else ()
                    exc.discarded_frames += (*discarded, *acquisition_frames)
                    iv_reacquisition = exc
                    iv_pair_pending = True
                    cp_recovery_attempted = False
                    transition_pending = require_transition
                    last_frame = exc.frame
                    last_reason = "IV reads still disagree after bounded reacquisition"
                    log.info("IVs changed before recovery input; discarding the acquisition (%d/3)", attempt + 1)
                    continue
                except _UnresolvedAppraisalName as exc:
                    # Only a witnessed transition (or the initial position)
                    # permits skipping. A name reread cannot prove that an
                    # otherwise unobserved carousel move actually happened.
                    if status != "stable":
                        return None, exc.frame, "transition_identity_incomplete", str(exc)
                    return None, exc.frame, "invalid", str(exc)
                except RuntimeError as exc:
                    return None, frame, "cp_recovery_failed", str(exc)
                finally:
                    self._settled_frame_pair = None
                last_frame = frame
                if self._abort or decision is None:
                    return None, frame, "aborted", "abort requested"
                if (iv_reacquisition is not None
                        and (self._paused or self._pause_generation != iv_reacquisition.pause_generation)):
                    return None, None, "reacquire", "pause invalidated IV reacquisition"
            log.info(
                "Snapshot %d/3: %s CP%d %d/%d/%d HP%d caught='%s' -> %s",
                attempt + 1,
                snapshot.detected_species or snapshot.display_name or "?",
                snapshot.cp,
                snapshot.atk,
                snapshot.def_,
                snapshot.sta,
                snapshot.hp,
                snapshot.caught_species,
                decision.reason,
            )
            if status in {
                "stable_raw_identity_unchanged",
                "stable_transition_unobserved",
            }:
                if not decision.accepted or decision.snapshot is None:
                    return (
                        None,
                        frame,
                        "transition_identity_incomplete",
                        "transition could not be confirmed from one complete "
                        f"validated tuple: {decision.reason}",
                    )

                current_key = self._complete_identity_key(decision.snapshot)
                previous_key = self._last_validated_identity_key
                if current_key is None:
                    return (
                        None,
                        frame,
                        "transition_identity_incomplete",
                        "transition requires a complete validated species/form, "
                        "CP, HP, and IV tuple",
                    )
                if previous_key is None:
                    return (
                        None,
                        frame,
                        "transition_identity_incomplete",
                        "transition has no complete prior validated tuple",
                    )

                if (current_key == previous_key and transition_pair is not None
                        and transition_pair[-1] is frame):
                    # Repeated specimen details can prove the move even when
                    # every captured frame arrived after the animation ended.
                    if self._same_stats_specimen_advanced(
                        previous_accepted, transition_pair, current_key,
                        pair_pause_generation,
                    ):
                        return decision, frame, "ok", "same stats; distinct specimen details confirmed"
                    if self._abort:
                        return None, frame, "aborted", "abort requested"
                    if self._paused or self._pause_generation != pair_pause_generation:
                        return None, None, "reacquire", "pause invalidated specimen confirmation"

                # ADB screencap can take long enough that the carousel animation
                # has already finished before the first post-swipe frame arrives.
                # In that case the broad pixel ROIs never witness motion even
                # though the phone has advanced.  Confirm the new identity from
                # a second settled immutable frame before trusting structured
                # evidence without an observed animation.
                transition_pair_confirmed = calculated_pair_confirmed
                if (status == "stable_transition_unobserved"
                        and not transition_pair_confirmed
                        and current_key != previous_key):
                    transition_pair_confirmed = self._visible_cp_confirmed_by_pair(
                        decision, frame, transition_pair, pair_pause_generation,
                    )
                    if self._abort:
                        return None, frame, "aborted", "abort requested"
                    if self._paused or self._pause_generation != pair_pause_generation:
                        return None, None, "reacquire", "pause invalidated transition confirmation"
                if (status == "stable_transition_unobserved"
                        and not transition_pair_confirmed):
                    human_delay(0.2, 0.2)
                    if self._abort:
                        return None, frame, "aborted", "abort requested"
                    confirmation_frame = self._fast_screencap()
                    if self._abort:
                        return None, confirmation_frame, "aborted", "abort requested"
                    if (
                        self.nav.detect_screen(confirmation_frame) != "appraisal"
                        or not self.reader.are_bars_visible(confirmation_frame)
                    ):
                        return (
                            None,
                            confirmation_frame,
                            "transition_identity_incomplete",
                            "unobserved transition confirmation lost the "
                            "appraisal screen",
                        )
                    if not appraisal_frames_stable(frame, confirmation_frame):
                        return (
                            None,
                            confirmation_frame,
                            "transition_identity_inconsistent",
                            "unobserved transition confirmation frames did not "
                            "stay settled",
                        )

                    confirmation_detail, confirmation_appraisal = (
                        self._read_appraisal_snapshot(confirmation_frame)
                    )
                    if self._abort:
                        return None, confirmation_frame, "aborted", "abort requested"
                    confirmation_snapshot = AppraisalSnapshot.from_reads(
                        confirmation_detail,
                        confirmation_appraisal,
                    )
                    if decision.cp_source in {
                        "screen_after_animation", "screen_after_powerup_preview",
                    }:
                        try:
                            confirmation_decision = self._apply_animation_cp(
                                confirmation_snapshot,
                                decision.snapshot.cp,
                                recovery_identity,
                                frame=confirmation_frame,
                            )
                            if confirmation_decision.accepted:
                                confirmation_decision = replace(
                                    confirmation_decision,
                                    cp_source=decision.cp_source,
                                    reason=decision.reason,
                                )
                        except RuntimeError as exc:
                            return (
                                None,
                                confirmation_frame,
                                "transition_identity_inconsistent",
                                str(exc),
                            )
                    else:
                        confirmation_decision = self._validate_appraisal_snapshot(
                            confirmation_snapshot, confirmation_frame,
                        )
                    if self._abort:
                        return None, confirmation_frame, "aborted", "abort requested"
                    log.info(
                        "Unobserved transition confirmation: %s CP%d "
                        "%d/%d/%d HP%d -> %s",
                        confirmation_snapshot.detected_species
                        or confirmation_snapshot.display_name
                        or "?",
                        confirmation_snapshot.cp,
                        confirmation_snapshot.atk,
                        confirmation_snapshot.def_,
                        confirmation_snapshot.sta,
                        confirmation_snapshot.hp,
                        confirmation_decision.reason,
                    )
                    if (
                        not confirmation_decision.accepted
                        or confirmation_decision.snapshot is None
                    ):
                        return (
                            None,
                            confirmation_frame,
                            "transition_identity_incomplete",
                            "unobserved transition confirmation was not exact: "
                            f"{confirmation_decision.reason}",
                        )

                    confirmation_key = self._complete_identity_key(
                        confirmation_decision.snapshot
                    )
                    if confirmation_key is None:
                        return (
                            None,
                            confirmation_frame,
                            "transition_identity_incomplete",
                            "unobserved transition confirmation requires a "
                            "complete validated tuple",
                        )
                    if confirmation_key != current_key:
                        return (
                            None,
                            confirmation_frame,
                            "transition_identity_inconsistent",
                            "two exact unobserved-transition tuples did not "
                            "agree",
                        )

                    decision = confirmation_decision
                    frame = confirmation_frame
                    current_key = confirmation_key

                if current_key == previous_key:
                    if self._abort:
                        return None, frame, "aborted", "abort requested"
                    if self._paused or self._pause_generation != pair_pause_generation:
                        return None, None, "reacquire", "pause invalidated specimen confirmation"
                    return (
                        None,
                        frame,
                        "transition_returned_to_previous",
                        "transition returned to the same validated tuple",
                    )

                log.info(
                    "Raw appraisal identity was unchanged, but validated "
                    "identity advanced: %s -> %s",
                    previous_key,
                    current_key,
                )
                return decision, frame, "ok", decision.reason

            if decision.accepted:
                return decision, frame, "ok", decision.reason
            last_reason = decision.reason

        if save_failure_evidence:
            self._save_failed_appraisal(last_frame, last_reason)
        if iv_pair_pending:
            return None, last_frame, "cp_recovery_failed", last_reason
        return None, last_frame, "invalid", last_reason

    @staticmethod
    def _independent_frame_sources(older, newest):
        """Legacy captures are independent; stream pairs need distinct source evidence."""
        keys = ("pokemgr_stream_session", "pokemgr_stream_sequence",
                "pokemgr_stream_pts_us", "pokemgr_source_clock_generation")
        if not any(key in image.info for image in (older, newest) for key in keys):
            return True
        first = tuple(older.info.get(key) for key in keys)
        second = tuple(newest.info.get(key) for key in keys)
        return (isinstance(first[0], str) and bool(first[0]) and first[0] == second[0]
                and all(type(value) is int and value > 0
                        for value in (*first[1:], *second[1:]))
                and first[3] == second[3]
                and second[1] > first[1] and second[2] > first[2])

    @staticmethod
    def _specimen_frame_sources_ordered(older, newest):
        """Link independently fresh captures across compatible clock refreshes.

        A refresh changes the mapping revision, but not its validated clock
        continuity. Reconnects, invalidations and expired coverage change that
        token. Old captures without tokens retain the strict revision rule.
        """
        token_key = "pokemgr_source_clock_continuity"
        tokens = (older.info.get(token_key), newest.info.get(token_key))
        if not any(token_key in image.info for image in (older, newest)):
            return IndexingStateMachine._independent_frame_sources(older, newest)
        if (not all(isinstance(token, str) and len(token) == 32
                    and all(char in "0123456789abcdef" for char in token)
                    for token in tokens) or tokens[0] != tokens[1]):
            return False
        keys = ("pokemgr_stream_session", "pokemgr_stream_sequence",
                "pokemgr_stream_pts_us", "pokemgr_source_clock_generation")
        first = tuple(older.info.get(key) for key in keys)
        second = tuple(newest.info.get(key) for key in keys)
        if (not isinstance(first[0], str) or not first[0] or first[0] != second[0]
                or not all(type(value) is int and value > 0
                           for value in (*first[1:], *second[1:]))
                or second[1] <= first[1] or second[2] <= first[2]
                or second[3] < first[3]):
            return False
        times = tuple(image.info.get(key) for image in (older, newest)
                      for key in ("pokemgr_capture_started_at", "pokemgr_capture_finished_at"))
        return (all(type(value) in (int, float) and math.isfinite(value) for value in times)
                and 0 < times[0] <= times[1] < times[2] <= times[3])

    # TRACEWEAVER: entrypoint=_same_stats_specimen_advanced; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
    @timing.timed("scan.specimen_confirmation")
    def _same_stats_specimen_advanced(self, previous_accepted, current_pair,
                                      key, pause_generation):
        """Prove a same-stat neighbour using repeated, frame-local details.

        Two independent settled captures on each side of the swipe must agree
        on the complete battle tuple. A confident weight, height, sex or catch date
        must agree within each side and differ across it. This proof neither
        issues input nor changes position/history; missing details fail closed.
        """
        from .snapshot import AppraisalSnapshot, appraisal_frames_stable

        def interrupted():
            return (self._abort or self._paused
                    or self._pause_generation != pause_generation)

        def rejected(reason):
            log.info("Same-stat specimen confirmation unavailable: %s", reason)
            return False

        if (interrupted() or key is None
                or key != self._last_validated_identity_key
                or self._last_accepted_pause_generation != pause_generation
                or not isinstance(current_pair, tuple) or len(current_pair) != 2):
            return rejected("accepted reference or pause generation unavailable")
        frames = (self._last_accepted_image, previous_accepted, *current_pair)
        if (any(image is None for image in frames)
                or len({id(image) for image in frames}) != 4
                or len({image.size for image in frames}) != 1):
            return rejected("four distinct same-size captures unavailable")
        stream_keys = ("pokemgr_stream_session", "pokemgr_stream_sequence",
                       "pokemgr_stream_pts_us", "pokemgr_source_clock_generation")
        if any(name in image.info for image in frames
               for name in (*stream_keys, "pokemgr_source_clock_continuity")):
            if not all(self._specimen_frame_sources_ordered(a, b)
                       for a, b in zip(frames, frames[1:])):
                log.info("Specimen source evidence: %s", [
                    tuple(image.info.get(name) for name in (*stream_keys,
                        "pokemgr_source_clock_continuity", "pokemgr_capture_started_at",
                        "pokemgr_capture_finished_at")) for image in frames
                ])
                return rejected("capture chronology or clock continuity did not agree")
        else:
            # Bare images and Pillow copies have no independent-capture proof.
            previous_finish = float("-inf")
            for image in frames:
                start = image.info.get("pokemgr_capture_started_at")
                finish = image.info.get("pokemgr_capture_finished_at")
                if (type(start) not in (int, float) or type(finish) not in (int, float)
                        or not math.isfinite(start) or not math.isfinite(finish)
                        or start <= previous_finish or finish < start):
                    return rejected("legacy capture times were missing or unordered")
                previous_finish = finish
        if (not appraisal_frames_stable(*frames[:2])
                or not appraisal_frames_stable(*frames[2:])):
            return rejected("before/after appraisal frames did not stay settled")
        markers = []
        for image in frames:
            if (interrupted() or self.nav.detect_screen(image) != "appraisal"
                    or not self.reader.are_bars_visible(image)):
                return rejected("appraisal screen or visible bars unavailable")
            detail, appraisal = self._read_appraisal_snapshot(image)
            if interrupted():
                return rejected("pause or abort during appraisal read")
            decision = self._validate_appraisal_snapshot(
                AppraisalSnapshot.from_reads(detail, appraisal), image,
            )
            if (interrupted() or not decision.accepted or decision.snapshot is None
                    or self._complete_identity_key(decision.snapshot) != key):
                return rejected("one frame did not validate the complete matching tuple")
            values = self.reader.specimen_markers(image)
            if interrupted():
                return rejected("pause or abort during specimen text read")
            gender = self.reader.specimen_gender(image)
            if interrupted():
                return rejected("pause or abort during sex read")
            markers.append((*values, gender if gender in ("male", "female") else ""))
        for name, values in zip(("weight", "height", "caught date", "sex"), zip(*markers)):
            before, before_repeat, after, after_repeat = values
            if before and after and before == before_repeat and after == after_repeat and before != after:
                log.info("Same-stat neighbour confirmed by repeated %s: %s -> %s",
                         name, before, after)
                return True
        log.info("Specimen details before/after: %s", markers)
        return rejected("no confident repeated detail changed")

    @timing.timed("scan.visible_pair_confirmation")
    def _visible_cp_confirmed_by_pair(self, decision, frame,
                                      settled_pair, pause_generation):
        """Reuse an independent settled image to confirm an exact new tuple.

        The pair belongs to this acquisition and is discarded by any recovery
        input. Missing or conflicting evidence retains the fresh third-frame
        confirmation; this read-only shortcut never proves same-stat movement.
        """
        from .snapshot import AppraisalSnapshot, appraisal_frames_stable

        def interrupted():
            return (self._abort or self._paused
                    or self._pause_generation != pause_generation)

        if (interrupted() or not decision.accepted or decision.snapshot is None
                or decision.cp_source not in {"screen", "screen_alternate_ocr"}
                or not isinstance(settled_pair, tuple) or len(settled_pair) != 2):
            return False
        older, newest = settled_pair
        key = self._complete_identity_key(decision.snapshot)
        if (key is None or newest is not frame or older is None or older is newest
                or older.size != newest.size
                or not self._independent_frame_sources(older, newest)):
            return False
        # Capture times also exclude bare images and Pillow copies on legacy
        # capture paths, where no source sequence is available.
        bounds = tuple(image.info.get(name) for image in settled_pair
                       for name in ("pokemgr_capture_started_at", "pokemgr_capture_finished_at"))
        if (not all(type(value) in (int, float) and math.isfinite(value) for value in bounds)
                or not (0 < bounds[0] <= bounds[1] < bounds[2] <= bounds[3])):
            return False
        for image in settled_pair:
            if (interrupted() or self.nav.detect_screen(image) != "appraisal"
                    or not self.reader.are_bars_visible(image)):
                return False
        if (interrupted() or not appraisal_frames_stable(older, newest)
                or not self.reader.appraisal_bars_stable(older, newest)):
            return False
        if interrupted():
            return False
        detail, appraisal = self._read_appraisal_snapshot(older)
        if interrupted():
            return False
        confirmation = self._validate_appraisal_snapshot(
            AppraisalSnapshot.from_reads(detail, appraisal), older,
        )
        return (not interrupted() and confirmation.accepted
                and confirmation.snapshot is not None
                and self._complete_identity_key(confirmation.snapshot) == key)

    @timing.timed("scan.pair_confirmation")
    def _calculated_cp_confirmed_by_pair(self, snapshot, decision, frame,
                                         settled_pair, pause_generation):
        """Confirm a calculated result from the other independently captured image."""
        from .snapshot import AppraisalSnapshot, validate_snapshot

        def interrupted():
            return (self._abort or self._paused
                    or self._pause_generation != pause_generation)

        if (interrupted() or not decision.accepted
                or not decision.cp_source.startswith("calculated")
                or decision.snapshot is None
                or not isinstance(settled_pair, tuple)
                or len(settled_pair) != 2):
            return False
        older, newest = settled_pair
        if (newest is not frame or older is newest
                or not self._independent_frame_sources(older, newest)):
            return False
        identity = self._cp_recovery_identity(snapshot)
        current_key = self._complete_identity_key(decision.snapshot)
        if identity is None or current_key is None:
            return False
        for image in settled_pair:
            if (interrupted() or self.nav.detect_screen(image) != "appraisal"
                    or not self.reader.are_bars_visible(image)):
                return False
        if interrupted():
            return False
        detail, appraisal = self._read_appraisal_snapshot(older)
        if interrupted():
            return False
        confirmation = AppraisalSnapshot.from_reads(detail, appraisal)
        if self._cp_recovery_identity(confirmation) != identity:
            return False
        confirmed = validate_snapshot(confirmation, self.use_calculated_cp)
        if (interrupted() or not confirmed.accepted or confirmed.snapshot is None
                or self._complete_identity_key(confirmed.snapshot) != current_key):
            return False
        log.debug("Calculated CP confirmed from both settled appraisal frames")
        return True

    @timing.timed("scan.validate")
    def _validate_appraisal_snapshot(self, snapshot, frame):
        """Calculate from HP/IVs first, then read CP only if still unresolved."""
        from dataclasses import replace
        from .snapshot import validate_snapshot
        from ..pvp.resolver import resolve_candidates

        primary = validate_snapshot(snapshot, self.use_calculated_cp)
        if snapshot.in_gym:
            return primary
        # Unique HP/IV calculations are confirmed from a separate fresh frame
        # by the acquisition loop; alternate OCR adds no evidence they need.
        if primary.accepted:
            return primary
        if (self._abort or not snapshot.read_complete
                or any(iv < 0 or iv > 15 for iv in snapshot.ivs)):
            return primary
        recovery_identity = self._cp_recovery_identity(snapshot)
        expected_cps = None
        if (recovery_identity is not None
                and getattr(self.reader, "_native_enabled", False) is True):
            expected_cps = {
                candidate.expected_cp
                for candidate in resolve_candidates(
                    snapshot.atk, snapshot.def_, snapshot.sta,
                    hp=snapshot.hp, caught_family=snapshot.caught_species,
                    **({"candy_family": snapshot.candy_family} if snapshot.candy_family else {}),
                ).candidates
            }
            if expected_cps:
                # Try the same-frame native CP crop before starting legacy
                # OCR. Unique HP/IV calculations already returned above.
                observed, confidence = self.reader.native_cp(
                    frame, expected_cps=expected_cps,
                )
                if self._abort or confidence < 0:
                    return primary
                if observed > 0:
                    exact = validate_snapshot(replace(snapshot, cp=observed), False)
                    if exact.accepted:
                        return replace(
                            exact, cp_source="screen_alternate_ocr",
                            reason="exact CP observed by native OCR on the same appraisal",
                        )
        if snapshot.cp <= 0:
            # Visible CP can still resolve an exact species/level when HP or
            # caught-name OCR is unavailable. Preserve the full legacy CP
            # reader in that case; only model recovery needs complete HP/name.
            observed, _confidence = self.reader.read_cp(
                frame, fast=recovery_identity is not None,
            )
            if self._abort:
                return primary
            if observed > 0:
                quick = validate_snapshot(replace(snapshot, cp=observed), False)
                if quick.accepted:
                    return quick
        if recovery_identity is None:
            return primary
        if expected_cps is None:
            expected_cps = {
                candidate.expected_cp
                for candidate in resolve_candidates(
                    snapshot.atk, snapshot.def_, snapshot.sta,
                    hp=snapshot.hp, caught_family=snapshot.caught_species,
                    **({"candy_family": snapshot.candy_family} if snapshot.candy_family else {}),
                ).candidates
            }
        if not expected_cps:
            return primary
        observed, _confidence = self.reader.read_cp(frame, expected_cps=expected_cps)
        if observed <= 0 or self._abort:
            return primary
        exact = validate_snapshot(replace(snapshot, cp=observed), False)
        if exact.accepted:
            return replace(
                exact, cp_source="screen_alternate_ocr",
                reason="exact CP observed by alternate OCR on the same appraisal",
            )
        return primary

    # TRACEWEAVER: entrypoint=_save_failed_appraisal; req=REQ-MASS-001,REQ-SCAN-003; trace=TRACE-MASS-001,TRACE-SCAN-003; ver=VER-SCAN-001
    def _save_failed_appraisal(self, frame, reason, phase="appraisal", *, position=None):
        """Keep the actual skipped pixels so the failure can be reproduced."""
        if frame is None:
            return
        import json
        try:
            destination = config.CACHE_DIR / "scan_failures" / self.session_id
            destination.mkdir(parents=True, exist_ok=True)
            if position is None:
                position = max(0, int(self.skip_first_n)) + self.visited_count + 1
            prefix = destination / f"position_{position:05d}"
            frame.save(f"{prefix}_{phase}.png")
            frame.crop(self.regions.cp_region.as_tuple()).save(f"{prefix}_cp.png")
            prefix.with_suffix(".json").write_text(json.dumps({
                "position": position, "reason": reason, "phase": phase,
            }, indent=2), encoding="utf-8")
            log.info("Saved %s failure evidence: %s", phase, prefix)
        except (OSError, ValueError):
            log.exception("Could not save skipped-position evidence")

    @staticmethod
    def _cp_recovery_identity(snapshot) -> tuple | None:
        """Complete non-CP evidence required for a temporary detail visit."""
        if (
            snapshot.in_gym
            or not snapshot.read_complete
            or not snapshot.detected_species.strip()
            or not (snapshot.caught_species.strip() or snapshot.candy_family.strip())
            or not snapshot.display_name.strip()
            or snapshot.hp <= 0
            or any(iv < 0 or iv > 15 for iv in snapshot.ivs)
        ):
            return None
        return (
            snapshot.detected_species,
            snapshot.caught_species or f"candy:{snapshot.candy_family}",
            snapshot.display_name,
            snapshot.hp,
            *snapshot.ivs,
        )

    @staticmethod
    def _non_iv_recovery_identity(snapshot) -> tuple | None:
        """Fixed complete evidence around an IV-only pre-input disagreement."""
        identity = IndexingStateMachine._cp_recovery_identity(snapshot)
        if identity is None:
            return None
        return (*identity[:4], snapshot.cp, snapshot.gender, snapshot.shiny,
                snapshot.shadow, snapshot.favorited, snapshot.lucky, snapshot.is_dynamax,
                snapshot.weight_tag, snapshot.height_tag, snapshot.candy_family)

    def _review_caught_name_drift(self, original, current, before, after, *, allow_exact=False):
        """Match quote-only caught-name uncertainty for review, never CP.

        Keep letters, gender symbols and form words intact. This recognizes
        the same anchored text without assigning either spelling to a species.
        """
        import unicodedata
        from .snapshot import appraisal_frames_stable

        if (self._cp_recovery_identity(original) is None
                or self._cp_recovery_identity(current) is None
                or not original.caught_species.strip() or not current.caught_species.strip()
                or original.detected_species != original.caught_species
                or current.detected_species != current.caught_species
                or original.display_name != current.display_name
                or (original.hp, original.ivs) != (current.hp, current.ivs)
                or any(getattr(original, field) != getattr(current, field)
                       for field in ("gender", "shiny", "shadow", "lucky",
                                     "is_dynamax", "candy_family"))
                or (not allow_exact and original.caught_species == current.caught_species)
                or before is after):
            return False

        def anchor(name):
            name = unicodedata.normalize("NFC", name).casefold()
            return " ".join(name.split()).rstrip("'\u2018\u2019\"\u201c\u201d").rstrip()

        first, second = anchor(original.caught_species), anchor(current.caught_species)
        return (len(first) >= 3 and first == second
                and appraisal_frames_stable(before, after))

    def _review_name_drift(self, original, current, before, after):
        """Allow bounded name OCR drift only for favorite-and-skip.

        This never authorizes calculated CP or associates a detail CP with an
        appraisal. All other identity fields and the static pixels must agree.
        Display-name drift still requires a default name; changed stats and
        moving screens stay strict.
        """
        from difflib import SequenceMatcher
        from .snapshot import appraisal_frames_stable

        if self._review_caught_name_drift(original, current, before, after):
            return True
        first = self._cp_recovery_identity(original)
        second = self._cp_recovery_identity(current)
        if (first is None or second is None or first == second
                or first[:2] != second[:2] or first[3:] != second[3:]):
            return False
        family = original.caught_species.casefold().strip()
        for name in (original.display_name, current.display_name):
            name = name.casefold().strip()
            if (len(name) < 4 or any(char.isdigit() for char in name)
                    or SequenceMatcher(None, name, family).ratio() < 0.8):
                return False
        return appraisal_frames_stable(before, after)

    def _review_identity(self, snapshot):
        """A gym review can preserve a star without inventing hidden HP/CP."""
        if snapshot.in_gym:
            if (snapshot.in_gym is not True
                    or not snapshot.read_complete or not snapshot.display_name.strip()
                    or not snapshot.caught_species.strip()
                    or snapshot.detected_species != snapshot.caught_species
                    or any(iv < 0 or iv > 15 for iv in snapshot.ivs)):
                return None
            return ("gym", snapshot.detected_species, snapshot.caught_species,
                    snapshot.display_name, *snapshot.ivs)
        return self._cp_recovery_identity(snapshot)

    def _record_recovery_identity_change(self, original, current, before, after,
                                         *, save_evidence):
        fields = ("display_name", "detected_species", "caught_species", "hp",
                  "atk", "def_", "sta", "read_complete")
        changes = {field: (getattr(original, field), getattr(current, field))
                   for field in fields
                   if getattr(original, field) != getattr(current, field)}
        reason = f"CP recovery appraisal identity changed before closing: {changes}"
        log.warning(reason)
        if save_evidence:
            self._save_failed_appraisal(before, reason, phase="recovery_before")
            self._save_failed_appraisal(after, reason, phase="recovery_reread")

    @timing.timed("scan.unfavorite", scan=True)
    def _unfavorite_accepted_snapshot(self, decision, frame):
        """Confirm one optional star removal before storing its OFF state."""
        from dataclasses import replace
        from .snapshot import AppraisalSnapshot, appraisal_frames_stable
        from ..reader.icons import favorite_state

        original = decision.snapshot
        expected_key = self._complete_identity_key(original)
        if expected_key is None:
            raise RuntimeError("Could not unfavorite scanned Pokemon: complete identity is required")
        reference = frame

        def observe():
            nonlocal frame
            while self._paused and not self._abort:
                with timing.span("wait.pause", session_id=self.session_id):
                    time.sleep(.25)
            if self._abort:
                return None
            generation = self._pause_generation

            def interrupted():
                return self._abort or self._paused or self._pause_generation != generation

            frame = self._fast_screencap()
            if interrupted():
                return None
            if (self.nav.detect_screen(frame) != "appraisal"
                    or not self.reader.are_bars_visible(frame)):
                if interrupted():
                    return None
                raise RuntimeError("Could not unfavorite scanned Pokemon: appraisal not confirmed")
            if interrupted():
                return None
            detail, appraisal = self._read_appraisal_snapshot(frame)
            if interrupted():
                return None
            current = self._validate_appraisal_snapshot(
                AppraisalSnapshot.from_reads(detail, appraisal), frame,
            )
            if interrupted():
                return None
            fields = ("display_name", "caught_species", "gender", "shiny", "shadow", "lucky", "is_dynamax")
            if (not current.accepted or current.snapshot is None
                    or self._complete_identity_key(current.snapshot) != expected_key
                    or any(getattr(current.snapshot, name) != getattr(original, name) for name in fields)
                    or not appraisal_frames_stable(reference, frame)
                    or not self.reader.appraisal_bars_stable(reference, frame)):
                if interrupted():
                    return None
                raise RuntimeError("Could not unfavorite scanned Pokemon: identity changed or was unreadable")
            state = favorite_state(frame, self.regions.favorite_star_region)
            return None if interrupted() else (state, generation)

        def confirmed_off():
            return replace(decision, snapshot=replace(original, favorited=False)), frame

        for attempt in range(3):
            if attempt:
                human_delay(.1, 0)
            observed = observe()
            if self._abort:
                return None, frame
            if observed is None:
                continue
            state, generation = observed
            if state == "off":
                return confirmed_off()
            if state == "on":
                if self._paused or self._pause_generation != generation:
                    continue
                if not self._safe_tap(*self.regions.favorite_star_region.center, jitter=0):
                    return None, frame
                break
        else:
            raise RuntimeError("Could not unfavorite scanned Pokemon: star was not confirmed before input")

        # Input may already have reached the game. Remain in readback even
        # after pause/resume; uncertain or unchanged state must never retap.
        for attempt in range(3):
            if attempt or getattr(self.adb, "has_stream_frames", False) is not True:
                human_delay(.1, 0)
            observed = observe()
            if self._abort:
                return None, frame
            if observed is not None and observed[0] == "off":
                log.info("Removed scanned Pokemon favorite; OFF verified before saving")
                return confirmed_off()
        raise RuntimeError("Could not confirm unfavorite after one tap; Pokemon was not saved")

    @timing.timed("recovery.favorite")
    def _favorite_unresolved_snapshot(self, frame):
        """Set the review favorite once and verify it on the same appraisal."""
        from .snapshot import AppraisalSnapshot, appraisal_frames_stable
        from ..reader.icons import favorite_state

        if self._abort:
            return None
        generation = self._pause_generation
        caught_review_only = frame.info.get("pokemgr_review_caught_name_drift") is True

        def check_review_generation(snapshot):
            if ((snapshot.in_gym or caught_review_only)
                    and (self._paused or self._pause_generation != generation)):
                raise _AppraisalConfirmationInvalidated("pause invalidated favorite review")

        def read_snapshot(image, *, guard_pause=False):
            is_appraisal = self.nav.detect_screen(image) == "appraisal"
            bars_visible = is_appraisal and self.reader.are_bars_visible(image)
            if guard_pause:
                check_review_generation(original)
            if not bars_visible:
                raise RuntimeError("Could not favorite unresolved Pokemon: appraisal not confirmed")
            detail, appraisal = self._read_appraisal_snapshot(image)
            if self._abort:
                return None
            if guard_pause:
                check_review_generation(original)
            snapshot = AppraisalSnapshot.from_reads(detail, appraisal)
            if self._review_identity(snapshot) is None:
                raise RuntimeError("Could not favorite unresolved Pokemon: incomplete identity")
            return snapshot

        original_frame = frame
        original = read_snapshot(frame)
        if self._abort:
            return None

        def read_current():
            nonlocal generation, caught_review_only
            if original.in_gym or caught_review_only:
                while self._paused and not self._abort:
                    with timing.span("wait.pause", session_id=self.session_id):
                        time.sleep(0.25)
                if self._abort:
                    return None, None
                generation = self._pause_generation
            image = self._fast_screencap()
            if self._abort:
                return image, None
            check_review_generation(original)
            current = read_snapshot(image, guard_pause=True)
            if self._abort:
                return image, None
            check_review_generation(original)
            if original.in_gym or current.in_gym:
                matches = (self._review_identity(current) == self._review_identity(original)
                           and appraisal_frames_stable(original_frame, image))
            else:
                caught_drift = self._review_caught_name_drift(
                    original, current, original_frame, image,
                    allow_exact=caught_review_only,
                )
                if caught_review_only:
                    matches = caught_drift
                else:
                    matches = (caught_drift
                               or self._review_identity(current) == self._review_identity(original)
                               or self._review_name_drift(original, current, original_frame, image))
                caught_review_only = caught_review_only or caught_drift
            check_review_generation(original)
            if not matches:
                if self._abort:
                    return image, None
                raise RuntimeError("Could not favorite unresolved Pokemon: identity changed")
            if self._abort:
                return image, None
            state = favorite_state(image, self.regions.favorite_star_region)
            check_review_generation(original)
            return image, state

        for attempt in range(3):
            try:
                frame, state = read_current()
                if self._abort:
                    return None
                if state == "on":
                    log.info("Unresolved Pokemon is already favorited for review")
                    return frame
                if state == "off":
                    check_review_generation(original)
                    if not self._safe_tap(*self.regions.favorite_star_region.center, jitter=0):
                        return None
                    # A pause during the tap stays in the confirmation phase.
                    # Never return to this toggle after it may have been sent.
                    break
            except _AppraisalConfirmationInvalidated:
                log.info("Favorite review invalidated; rereading before any star input")
                continue
            if attempt < 2:
                human_delay(0.2, 1.0)
                if self._abort:
                    return None
        else:
            raise RuntimeError("Could not favorite unresolved Pokemon: star not recognized")

        for _attempt in range(3):
            human_delay(0.2, 1.0)
            if self._abort:
                return None
            try:
                frame, state = read_current()
            except _AppraisalConfirmationInvalidated:
                log.info("Favorite confirmation invalidated; rereading without another tap")
                continue
            if self._abort:
                return None
            if state == "on":
                log.info("Favorited unresolved Pokemon for review; star verified")
                return frame
        # Never blindly repeat a toggle: a delayed first tap could otherwise
        # remove the favorite we just set.
        raise RuntimeError("Could not confirm favorite after one tap; review current Pokemon")

    def _apply_animation_cp(self, snapshot, cp, expected_identity, *, frame=None):
        """Attach witnessed CP only to a fresh, matching complete appraisal."""
        from dataclasses import replace
        from .snapshot import SnapshotDecision, validate_snapshot

        if (expected_identity is None
                or self._cp_recovery_identity(snapshot) != expected_identity):
            raise RuntimeError("CP recovery appraisal identity changed or is incomplete")
        # Appraisal acquisition defers CP for the HP/IV-first path. After a
        # separate model/preview observation, still check this fresh frame for
        # a contradictory visible CP before attaching the witnessed value.
        if snapshot.cp <= 0 and frame is not None:
            observed, _confidence = self.reader.read_cp(frame)
            if self._abort:
                return SnapshotDecision(False, "abort requested")
            snapshot = replace(snapshot, cp=observed)
        visible = validate_snapshot(snapshot, allow_calculated_cp=False)
        if visible.accepted and visible.snapshot.cp != cp:
            raise RuntimeError("CP recovery conflicts with a valid final visible CP")
        decision = validate_snapshot(
            replace(snapshot, cp=cp), allow_calculated_cp=False,
        )
        return replace(
            decision,
            cp_source="screen_after_animation",
            reason="CP revealed by model animation; full appraisal identity rechecked",
        ) if decision.accepted else decision

    @timing.timed("scan.cp_window", scan=True)
    def _read_cp_motion_window(self, snapshot, expected_cps, *, after_ns,
                               expected_session=None):
        """Require two exact source frames from this gesture's bounded window."""
        from contextlib import closing
        from dataclasses import replace
        import math
        from .snapshot import validate_snapshot

        generation = self._pause_generation
        last_frame = None
        observed = {}
        examined = 0
        session = expected_session
        source_generation = None
        last_sequence = last_pts = 0
        accepted = None

        def interrupted():
            return (self._abort or self._paused
                    or self._pause_generation != generation)

        stream = self.adb.stream_frames(
            after_ns=after_ns, timeout=1.2, max_frames=30,
            should_stop=interrupted,
        )
        with closing(stream):
            for image in stream:
                last_frame = image
                if interrupted() or examined >= 30:
                    break
                examined += 1
                source = (image.info.get("pokemgr_stream_session"),
                          image.info.get("pokemgr_stream_sequence"),
                          image.info.get("pokemgr_stream_pts_us"))
                started = image.info.get("pokemgr_capture_started_at")
                clock_generation = image.info.get("pokemgr_source_clock_generation")
                if (not isinstance(source[0], str) or not source[0]
                        or type(source[1]) is not int or source[1] <= 0
                        or type(source[2]) is not int or source[2] <= 0
                        or type(clock_generation) is not int or clock_generation <= 0
                        or type(started) not in (int, float)
                        or not math.isfinite(started)
                        or started <= after_ns / 1e9):
                    continue
                if session is None:
                    session = source[0]
                elif source[0] != session:
                    break
                if source_generation is None:
                    source_generation = clock_generation
                elif clock_generation != source_generation:
                    break
                # A duplicated encoder PTS is one observation even if another
                # ring slot/sequence contains it. Never combine reordered data.
                if source[1] <= last_sequence or source[2] <= last_pts:
                    continue
                last_sequence, last_pts = source[1:]
                if self.nav.detect_screen(image) != "detail":
                    continue
                fields = self.reader.native_fields(image)
                if interrupted():
                    break
                if (fields is None or fields.display_name != snapshot.display_name
                        or fields.hp != snapshot.hp):
                    continue
                cp, confidence = self.reader.native_cp(image, expected_cps=expected_cps)
                if interrupted():
                    break
                if confidence < 0:
                    break  # Do not let another engine select one conflicting value.
                if cp <= 0:
                    continue
                decision = validate_snapshot(replace(snapshot, cp=cp), False)
                if not decision.accepted:
                    continue
                observed.setdefault(cp, set()).add(source[2])
                if len(observed) > 1:
                    break
                if len(observed[cp]) >= 2:
                    accepted = cp, decision, image
                    break
        # Closing performs the controller's final target validation. A pause or
        # abort during that validation also invalidates the window's evidence.
        if accepted is not None and not interrupted():
            log.info("CP stream window: CP%d agreed on two source frames (%d examined)",
                     accepted[0], examined)
            return accepted
        log.info("CP stream window: no exact two-frame agreement (%d examined)", examined)
        return None, None, last_frame

    @timing.timed("recovery.model")
    def _recover_cp_with_model_taps(self, snapshot, frame, *, save_failure_evidence=True):
        """Confirm unique calculated CP, or reveal CP and recheck appraisal.

        Appraisal consumes model taps by closing its overlay. Close its actual
        X first, confirm detail identity, and only then tap the model. A CP read
        from detail is evidence only when it exactly fits the original species,
        HP and IVs and a fresh full appraisal confirms those fields afterward.
        """
        from dataclasses import replace
        from .snapshot import AppraisalSnapshot, validate_snapshot
        from ..pvp.resolver import resolve_candidates

        if self._abort:
            return None, frame
        identity = self._cp_recovery_identity(snapshot)
        if identity is None:
            return validate_snapshot(snapshot, self.use_calculated_cp), frame
        original_frame = frame
        generation = self._pause_generation
        caught_review_only = False

        def check_confirmation_generation():
            if (self._pause_generation != generation
                    or (caught_review_only and self._paused)):
                raise _AppraisalConfirmationInvalidated("pause invalidated appraisal confirmation")

        for attempt in range(3):
            frame = self._fast_screencap()
            if self._abort:
                return None, frame
            if (self.nav.detect_screen(frame) != "appraisal"
                    or not self.reader.are_bars_visible(frame)):
                raise RuntimeError("CP recovery lost appraisal before closing")
            detail, appraisal = self._read_appraisal_snapshot(frame)
            if self._abort:
                return None, frame
            check_confirmation_generation()
            fresh = AppraisalSnapshot.from_reads(detail, appraisal)
            exact_identity = self._cp_recovery_identity(fresh) == identity
            if exact_identity and not caught_review_only:
                break
            if not exact_identity:
                self._record_recovery_identity_change(
                    snapshot, fresh, original_frame, frame,
                    save_evidence=save_failure_evidence,
                )
            anchor = self._non_iv_recovery_identity(snapshot)
            if (not caught_review_only and anchor is not None
                    and self._non_iv_recovery_identity(fresh) == anchor
                    and snapshot.ivs != fresh.ivs):
                check_confirmation_generation()
                raise _PreInputIVConflict(original_frame, frame, anchor, generation)
            caught_drift = self._review_caught_name_drift(
                snapshot, fresh, original_frame, frame,
                allow_exact=caught_review_only,
            )
            name_drift = (caught_drift if caught_review_only else
                          caught_drift or self._review_name_drift(
                              snapshot, fresh, original_frame, frame,
                          ))
            # Once the caught anchor is uncertain, a later return to its first
            # spelling does not turn this review transaction into CP evidence.
            caught_review_only = caught_review_only or caught_drift
            check_confirmation_generation()
            if not name_drift:
                raise RuntimeError("CP recovery appraisal identity changed before closing")
            if attempt < 2:
                log.info("Name OCR disagreed on a settled appraisal; rereading (%d/3)", attempt + 2)
                human_delay(0.1, 0.0)
                if self._abort:
                    return None, frame
        else:
            check_confirmation_generation()
            if caught_review_only:
                # This only enables stricter favorite-review checks. It does
                # not authorize a match, a CP result, or a stored data row.
                frame.info["pokemgr_review_caught_name_drift"] = True
            raise _UnresolvedAppraisalName(frame)
        fresh_decision = self._validate_appraisal_snapshot(fresh, frame)
        if self._abort:
            return None, frame
        check_confirmation_generation()
        # Two independently read appraisals now agree on the full non-CP
        # identity. A unique exact calculation is sufficient; a model that
        # covers CP does not need to be moved or taken into another dialog.
        if fresh_decision.accepted:
            return fresh_decision, frame
        if not self.use_cp_animation:
            log.info("CP remains unresolved after appraisal reread; model recovery is disabled")
            return fresh_decision, frame

        expected_cps = {
            candidate.expected_cp
            for candidate in resolve_candidates(
                snapshot.atk, snapshot.def_, snapshot.sta,
                hp=snapshot.hp, caught_family=snapshot.caught_species,
                **({"candy_family": snapshot.candy_family} if snapshot.candy_family else {}),
            ).candidates
        }
        close_target = self.regions.appraisal_close_x
        if close_target is None:
            raise RuntimeError("CP recovery has no calibrated appraisal close target")

        def read_confirmed_detail(image):
            if self.nav.detect_screen(image) != "detail":
                raise RuntimeError("CP recovery lost the detail screen")
            detail_read = self.reader.read_detail_screen(image)
            hp = self.reader.read_hp(image)
            if self._abort:
                return detail_read
            if (detail_read.get("display_name", detail_read.get("species", ""))
                    != snapshot.display_name or hp != snapshot.hp):
                raise RuntimeError("CP recovery detail name or HP identity changed")
            return detail_read

        def read_exact_cp(image, detail_read):
            cp = int(detail_read.get("cp", -1))
            decision = validate_snapshot(
                replace(snapshot, cp=cp), allow_calculated_cp=False,
            )
            # Preserve an already exact visible value. An impossible first
            # OCR result must not suppress another pass on the same pixels.
            if not decision.accepted and expected_cps:
                observed, _confidence = self.reader.read_cp(
                    image, expected_cps=expected_cps,
                )
                if observed > 0:
                    cp = observed
                    decision = validate_snapshot(
                        replace(snapshot, cp=cp), allow_calculated_cp=False,
                    )
            return cp, decision

        if not self._safe_tap(*close_target, jitter=0):
            return None, frame
        human_delay(0.2, 0.2)
        if self._abort:
            return None, frame
        frame = self._fast_screencap()
        if self._abort:
            return None, frame
        detail = read_confirmed_detail(frame)
        if self._abort:
            return None, frame
        # Closing the overlay can itself uncover CP. Preserve that exact
        # observation before another animation has a chance to obscure it.
        visible_cp, candidate = read_exact_cp(frame, detail)
        witnessed_cp = visible_cp if candidate.accepted else None
        used_powerup_preview = False
        stream_motion = (getattr(self.adb, "has_stream_frames", False) is True
                         and getattr(self.reader, "_native_enabled", False) is True)
        motion_session = frame.info.get("pokemgr_stream_session")
        stream_witness_generation = None

        def read_after_motion(after_ns):
            nonlocal stream_witness_generation
            if stream_motion and getattr(self.reader, "_native_enabled", False) is True:
                generation = self._pause_generation
                cp, decision, image = self._read_cp_motion_window(
                    snapshot, expected_cps, after_ns=after_ns,
                    expected_session=motion_session,
                )
                if cp is not None:
                    stream_witness_generation = generation
                if cp is not None or self._abort:
                    return cp, decision, image
                # Window observations are discarded before the one fresh
                # same-target fallback capture. Pause still finishes this
                # transaction, as in the existing recovery path.
            else:
                human_delay(0.2, 1.0)
            if self._abort:
                return None, None, None
            image = self._fast_screencap()
            if self._abort:
                return None, None, image
            detail_read = read_confirmed_detail(image)
            if self._abort:
                return None, None, image
            cp, decision = read_exact_cp(image, detail_read)
            return cp, decision, image

        model_target = (
            self.regions.screen_width // 2,
            (self.regions.cp_region.y2 + self.regions.name_region.y) // 2,
        )
        for attempt in range(4):
            if witnessed_cp is not None:
                break
            if not self._safe_tap(*model_target, jitter=0):
                return None, frame
            tapped_at = time.monotonic()
            visible_cp, candidate, observed_frame = read_after_motion(time.monotonic_ns())
            if observed_frame is not None:
                frame = observed_frame
            captured_after = time.monotonic() - tapped_at
            if self._abort:
                return None, frame
            log.info(
                "CP animation attempt %d/4: capture returned %.3fs after tap, "
                "CP%d -> %s",
                attempt + 1, captured_after, visible_cp, candidate.reason,
            )
            if candidate.accepted:
                witnessed_cp = visible_cp
                break

        # A large head can cover CP throughout the tap animation. A short
        # drag through the upper model rotates it without using the lower
        # carousel swipe. If that angle still hides CP, tap the rotated model
        # to animate it before trying another angle. Verify name/HP after every
        # gesture, and all appraisal fields again before accepting exposed CP.
        rotation_end_x = max(1, model_target[0] - round(self.regions.screen_width * 0.20))
        for attempt in range(4):
            if witnessed_cp is not None:
                break
            if not self._safe_swipe(
                *model_target, rotation_end_x, model_target[1],
                duration_ms=500, jitter=0,
            ):
                return None, frame
            visible_cp, candidate, observed_frame = read_after_motion(time.monotonic_ns())
            if observed_frame is not None:
                frame = observed_frame
            if self._abort:
                return None, frame
            log.info(
                "CP rotation attempt %d/4: CP%d -> %s",
                attempt + 1, visible_cp, candidate.reason,
            )
            if candidate.accepted:
                witnessed_cp = visible_cp
                break

            if not self._safe_tap(*model_target, jitter=0):
                return None, frame
            tapped_at = time.monotonic()
            visible_cp, candidate, observed_frame = read_after_motion(time.monotonic_ns())
            if observed_frame is not None:
                frame = observed_frame
            captured_after = time.monotonic() - tapped_at
            if self._abort:
                return None, frame
            log.info(
                "CP rotated animation attempt %d/4: capture returned %.3fs "
                "after tap, CP%d -> %s",
                attempt + 1, captured_after, visible_cp, candidate.reason,
            )
            if candidate.accepted:
                witnessed_cp = visible_cp
                break

        if witnessed_cp is None and expected_cps:
            witnessed_cp, frame = self._read_cp_from_powerup_preview(
                snapshot, frame, save_failure_evidence=save_failure_evidence,
            )
            used_powerup_preview = witnessed_cp is not None
        if self._abort:
            return None, frame
        if not self._reopen_appraisal():
            if self._abort:
                return None, frame
            raise RuntimeError("CP recovery could not reopen appraisal")
        final_frame, status = self._wait_for_stable_appraisal()
        if self._abort:
            return None, final_frame if final_frame is not None else frame
        if final_frame is None:
            raise RuntimeError(f"CP recovery did not settle on appraisal: {status}")
        frame = final_frame
        if (self.nav.detect_screen(frame) != "appraisal"
                or not self.reader.are_bars_visible(frame)):
            raise RuntimeError("CP recovery lost the final appraisal screen")
        detail, appraisal = self._read_appraisal_snapshot(frame)
        if self._abort:
            return None, frame
        final = AppraisalSnapshot.from_reads(detail, appraisal)
        if self._cp_recovery_identity(final) != identity:
            raise RuntimeError("CP recovery final appraisal identity changed or is incomplete")
        if (stream_witness_generation is not None
                and self._pause_generation != stream_witness_generation):
            witnessed_cp = None
        if witnessed_cp is not None:
            decision = self._apply_animation_cp(final, witnessed_cp, identity, frame=frame)
            if decision.accepted and used_powerup_preview:
                decision = replace(
                    decision, cp_source="screen_after_powerup_preview",
                    reason="current CP read from cancelled power-up preview; "
                    "full appraisal identity rechecked",
                )
        else:
            decision = self._validate_appraisal_snapshot(final, frame)
        if (stream_witness_generation is not None
                and self._pause_generation != stream_witness_generation
                and witnessed_cp is not None):
            # The final CP read can itself take time; do not attach window
            # evidence if a pause occurred while that OCR was in flight.
            decision = self._validate_appraisal_snapshot(final, frame)
        return (None if self._abort else decision), frame

    @timing.timed("recovery.preview")
    def _read_cp_from_powerup_preview(self, snapshot, frame, *, save_failure_evidence=True):
        """Read the current CP in one preview, then cancel without powering up.

        The reader exposes only the detail opener and an explicitly recognized
        Cancel target. It never exposes the preview's purchase/confirm button.
        Abort stops all further input, even when a preview remains open.
        """
        from dataclasses import replace
        from .snapshot import validate_snapshot
        from ..reader.powerup import (
            find_powerup_button, has_detail_menu,
            read_powerup_preview, read_powerup_resource_prompt,
        )

        if self._abort or self._cp_recovery_identity(snapshot) is None:
            return None, frame

        def confirm_detail(image):
            if self.nav.detect_screen(image) != "detail":
                raise RuntimeError("CP preview recovery lost the detail screen")
            detail = self.reader.read_detail_screen(image)
            if self._abort:
                return
            hp = self.reader.read_hp(image)
            if self._abort:
                return
            if (detail.get("display_name", detail.get("species", ""))
                    != snapshot.display_name or hp != snapshot.hp):
                raise RuntimeError("CP preview detail name or HP identity changed")
            return detail

        frame = self._fast_screencap()
        if self._abort:
            return None, frame
        confirm_detail(frame)
        if self._abort:
            return None, frame
        opener = find_powerup_button(frame)
        if self._abort:
            return None, frame
        if opener is None:
            existing = read_powerup_preview(
                frame, (snapshot.display_name, snapshot.caught_species),
            )
            if self._abort:
                return None, frame
            resource = read_powerup_resource_prompt(
                frame, (snapshot.display_name, snapshot.caught_species),
            ) if existing is None else None
            if self._abort:
                return None, frame
            if existing is not None or resource is not None:
                raise RuntimeError("CP preview was already open; cancel it before restarting")
            if not has_detail_menu(frame, self.regions.menu_button):
                raise RuntimeError("CP preview opener unavailable and detail menu not confirmed")
            return None, frame
        if not self._safe_tap(*opener, jitter=0):
            return None, frame
        human_delay(0.35, 0.15)
        if self._abort:
            return None, frame
        preview = None
        cancel_target = None
        for attempt in range(3):
            frame = self._fast_screencap()
            if self._abort:
                return None, frame
            preview = read_powerup_preview(
                frame, (snapshot.display_name, snapshot.caught_species),
            )
            if self._abort:
                return None, frame
            cancel_target = (preview.cancel_target if preview is not None else
                             read_powerup_resource_prompt(
                                 frame, (snapshot.display_name, snapshot.caught_species),
                             ))
            if self._abort:
                return None, frame
            if cancel_target is not None:
                break
            if attempt < 2:
                log.info("CP dialog not yet recognized; retrying capture %d/3", attempt + 2)
                human_delay(0.2, 1.0)
                if self._abort:
                    return None, frame
        if cancel_target is None:
            reason = "CP dialog was not recognized after 3 captures; scan stopped without further input"
            if save_failure_evidence:
                self._save_failed_appraisal(frame, reason, phase="cp_preview")
            raise RuntimeError(reason)

        # Cancel even if the CP text was unreadable or did not fit this exact
        # HP/IV tuple. Never pass this dialog to generic navigation recovery.
        if not self._safe_tap(*cancel_target, jitter=0):
            return None, frame
        human_delay(0.35, 0.15)
        if self._abort:
            return None, frame
        frame = self._fast_screencap()
        if self._abort:
            return None, frame
        returned_detail = confirm_detail(frame)
        if self._abort:
            return None, frame
        # A missed Cancel must stop here. The underlying name/HP can remain
        # visible while the preview still covers the lower detail screen.
        if not has_detail_menu(frame, self.regions.menu_button):
            raise RuntimeError("CP preview did not close after Cancel")
        if self._abort:
            return None, frame
        if preview is None:
            log.info("Cancelled Rare Candy prompt; CP preview unavailable, continuing appraisal recovery")
            return None, frame
        if preview.current_cp is None:
            return None, frame
        post_cancel = validate_snapshot(
            replace(snapshot, cp=int(returned_detail.get("cp", -1))),
            allow_calculated_cp=False,
        )
        if (post_cancel.accepted
                and post_cancel.snapshot.cp != preview.current_cp):
            raise RuntimeError("CP preview conflicts with a valid post-cancel visible CP")
        decision = validate_snapshot(
            replace(snapshot, cp=preview.current_cp), allow_calculated_cp=False,
        )
        if not decision.accepted:
            log.info("Cancelled CP preview rejected: %s", decision.reason)
            return None, frame
        log.info("Current CP%d read from cancelled power-up preview", preview.current_cp)
        return preview.current_cp, frame

    @staticmethod
    def _complete_identity_key(snapshot) -> tuple | None:
        """Return a complete validated identity tuple, or fail closed."""
        if not snapshot.detected_species.strip():
            return None
        if snapshot.cp <= 0 or snapshot.hp <= 0:
            return None
        if any(iv < 0 or iv > 15 for iv in snapshot.ivs):
            return None
        return snapshot.identity_key

    @timing.timed("scan.settle", scan=True)
    def _wait_for_stable_appraisal(self, previous_accepted=None,
                                   require_transition: bool = False,
                                   allow_structured_fallback: bool = False):
        """Compare up to six spaced pairs, preserving exact transition gates.

        A paired stream brackets each bounded observation window with display
        validation. Legacy captures retain their existing transport-aware wait.
        The reusable pair is published only after the window closes successfully.
        """
        from contextlib import closing
        import math
        from .snapshot import (
            appraisal_region_diffs,
            appraisal_transition_observed,
        )

        self._settled_frame_pair = None
        pair_pause_generation = self._pause_generation
        previous = current = None
        transition_seen = not require_transition
        returned_to_previous = False
        last_screen = "unknown"
        attempts = 0

        def interrupted():
            return (self._abort or self._paused
                    or self._pause_generation != pair_pause_generation)

        def settled(status):
            if (not interrupted()
                    and self._independent_frame_sources(previous, current)):
                self._settled_frame_pair = (previous, current)
            return current, status

        def observe_transition(image):
            nonlocal transition_seen
            if require_transition and not transition_seen:
                transition_seen = appraisal_transition_observed(previous_accepted, image)

        def compare_pair(attempt):
            nonlocal returned_to_previous
            previous_has_bars = self.reader.are_bars_visible(previous)
            current_has_bars = self.reader.are_bars_visible(current)
            if previous_has_bars and current_has_bars:
                with timing.span("screen.region_diffs"):
                    diffs = appraisal_region_diffs(previous, current)
                log.debug(
                    "Appraisal settle %d/6: diffs=%s transition=%s",
                    attempt, ",".join(f"{value:.2f}" for value in diffs), transition_seen,
                )
                with timing.span("screen.stability"):
                    frames_stable = (
                        max(diffs) <= 1.5
                        and self.reader.appraisal_bars_stable(previous, current)
                    )
                if frames_stable:
                    settled_identity_changed = (
                        not require_transition or
                        appraisal_transition_observed(previous_accepted, current)
                    )
                    if transition_seen and settled_identity_changed:
                        return "stable"
                    if require_transition:
                        if allow_structured_fallback and transition_seen:
                            return "stable_raw_identity_unchanged"
                        if allow_structured_fallback and not transition_seen:
                            return "stable_transition_unobserved"
                        if transition_seen:
                            returned_to_previous = True
            return None

        def failure():
            if self._abort:
                return None, "aborted"
            if last_screen != "appraisal":
                return None, f"lost_appraisal_{last_screen}"
            if require_transition and not transition_seen:
                return None, "transition_not_observed"
            if returned_to_previous:
                return None, "transition_returned_to_previous"
            return None, "appraisal_not_stable"

        if require_transition and previous_accepted is None:
            return None, "transition_reference_missing"

        if getattr(self.adb, "has_stream_frames", False) is True and not interrupted():
            # At most six windows and six comparisons in total. An empty or
            # invalidated window falls back to fresh captures, never its pixels.
            for _window in range(6):
                previous = current = None
                last_source_image = None
                status = None
                valid_window = True
                window_attempts = attempts
                after_ns = time.monotonic_ns()
                base, jitter = config.STABLE_FRAME_INTERVAL
                interval_us = 0
                stream = self.adb.stream_frames(
                    after_ns=after_ns, timeout=1.2, max_frames=30,
                    should_stop=interrupted,
                )
                try:
                    with closing(stream):
                        for image in stream:
                            if interrupted():
                                break
                            source = (image.info.get("pokemgr_stream_session"),
                                      image.info.get("pokemgr_stream_sequence"),
                                      image.info.get("pokemgr_stream_pts_us"),
                                      image.info.get("pokemgr_source_clock_generation"))
                            started = image.info.get("pokemgr_capture_started_at")
                            if (not isinstance(source[0], str) or not source[0]
                                    or not all(type(value) is int and value > 0 for value in source[1:])
                                    or type(started) not in (int, float) or not math.isfinite(started)
                                    or started <= after_ns / 1e9):
                                valid_window = False
                                break
                            if last_source_image is not None:
                                if not self._independent_frame_sources(last_source_image, image):
                                    valid_window = False
                                    break
                            last_source_image = image
                            if previous is not None:
                                if source[2] - previous.info["pokemgr_stream_pts_us"] < interval_us:
                                    continue
                            current = image
                            observe_transition(current)
                            with timing.span("screen.detect"):
                                last_screen = self.nav.detect_screen(current)
                            if previous is not None:
                                attempts += 1
                                if last_screen == "appraisal" and self.nav.detect_screen(previous) == "appraisal":
                                    status = compare_pair(attempts)
                                if status is not None or attempts >= 6:
                                    break
                            previous = current
                            interval_us = math.ceil(random.uniform(base, base + jitter) * 1e6)
                except StreamCaptureInvalidated:
                    # Includes invalidation during the generator's final target
                    # check. Keep finish-current-transaction pause behavior, but
                    # authorize it only from the fresh fallback below.
                    valid_window = False
                    self._settled_frame_pair = None
                # Generator close runs the controller's final target check.
                # A pause during that check discards the entire window proof.
                if self._abort:
                    return None, "aborted"
                if interrupted() or not valid_window:
                    break
                if status is not None:
                    return settled(status)
                if attempts >= 6:
                    return failure()
                if attempts == window_attempts:
                    break
            self._settled_frame_pair = None

        # Fresh legacy fallback gets only the comparisons left in the same
        # six-attempt budget. Discard all stream transition/pair evidence first.
        if self._abort:
            return None, "aborted"
        previous = self._fast_screencap()
        transition_seen = not require_transition
        observe_transition(previous)
        with timing.span("screen.detect"):
            last_screen = self.nav.detect_screen(previous)
        returned_to_previous = False
        for attempt in range(attempts, 6):
            if self._abort:
                return None, "aborted"
            # Capture transport counts toward the configured observation gap.
            base_interval, interval_jitter = config.STABLE_FRAME_INTERVAL
            captured_at = previous.info.get("pokemgr_capture_started_at")
            if isinstance(captured_at, (int, float)):
                elapsed = max(0.0, time.monotonic() - captured_at)
                interval = random.uniform(base_interval, base_interval + interval_jitter)
                human_delay(max(0.0, interval - elapsed), 0.0)
            else:
                human_delay(base_interval, interval_jitter)
            current = self._fast_screencap()
            observe_transition(current)
            status = compare_pair(attempt + 1)
            if status is not None:
                return settled(status)
            previous = current
            with timing.span("screen.detect"):
                last_screen = self.nav.detect_screen(current)
        return failure()

    @timing.timed("recovery.transition", scan=True)
    def _recover_failed_transition(self, frame):
        """Prove the carousel position before retrying one failed forward swipe.

        Reversing must reach the distinct previous accepted checkpoint, then
        moving forward must restore the last accepted checkpoint. An unseen
        identical neighbour instead reverses to the last checkpoint and fails
        this proof. A delayed carousel response gets bounded fresh reads, never
        another checkpoint swipe. Probe reads never create rows or update
        identity history.
        """
        def stopped(reason):
            if self._abort:
                return None, frame, "aborted", "abort requested"
            return None, frame, "transition_retry_failed", reason

        def complete(key):
            return (
                isinstance(key, tuple) and len(key) == 6
                and isinstance(key[0], str) and bool(key[0].strip())
                and all(isinstance(value, int) and value > 0 for value in key[1:3])
                and all(isinstance(value, int) and 0 <= value <= 15 for value in key[3:])
            )

        confirmed_generation = None

        def describe(key):
            return f"{key[0]} CP{key[1]} HP{key[2]} IV{key[3]}/{key[4]}/{key[5]}"

        def acquire_phase(checkpoint, expected_key=None, **kwargs):
            nonlocal confirmed_generation
            # Retain the current checkpoint/forward phase. Returning reacquire
            # to the scan loop would lose which recovery gesture already ran.
            for attempt in range(3):
                while self._paused and not self._abort:
                    with timing.span("wait.pause", session_id=self.session_id):
                        time.sleep(0.25)
                if self._abort:
                    return None, None, "aborted", "abort requested"
                generation = self._pause_generation
                result = self._acquire_validated_snapshot(**kwargs)
                if self._abort:
                    return result
                decision, read_frame, kind, _reason = result
                if (kind == "reacquire" or self._paused
                        or self._pause_generation != generation):
                    result = (
                        None, None, "reacquire",
                        "pause invalidated failed-swipe confirmation",
                    )
                    log.info(
                        "Failed-swipe %s confirmation invalidated; retaining recovery "
                        "phase without repeating its swipe (read %d/3)",
                        checkpoint, attempt + 1,
                    )
                    continue
                if (expected_key is None or kind != "ok" or decision is None
                        or not decision.accepted or decision.snapshot is None):
                    return result
                observed_key = self._complete_identity_key(decision.snapshot)
                if observed_key is None:
                    return (
                        None, read_frame, "checkpoint_incomplete",
                        "complete species, CP, HP and IV identity was unavailable",
                    )
                if observed_key == expected_key:
                    confirmed_generation = generation
                    return result
                log.warning(
                    "Failed-swipe %s checkpoint read %d/3: expected %s, observed %s",
                    checkpoint, attempt + 1, expected_key, observed_key,
                )
                result = (
                    None, read_frame, "checkpoint_mismatch",
                    f"{checkpoint} checkpoint did not match after 3 read attempts "
                    f"(expected {describe(expected_key)}, saw {describe(observed_key)}); "
                    "position is uncertain; failed-swipe retry held",
                )
            return result

        previous_key = self._previous_validated_identity_key
        last_key = self._last_validated_identity_key
        if self._abort:
            return stopped("abort requested")
        if not complete(previous_key) or not complete(last_key) or previous_key == last_key:
            return stopped(
                "could not confirm advance; no distinct complete previous "
                "checkpoint for retry"
            )

        log.info("Verifying previous storage checkpoint before one failed-swipe retry")
        for reverse, expected_key, checkpoint in (
            (True, previous_key, "previous"),
            (False, last_key, "restored"),
        ):
            if self._abort:
                return stopped("abort requested")
            frame = self._fast_screencap()
            if self._abort:
                return stopped("abort requested")
            if self.nav.detect_screen(frame) != "appraisal":
                return stopped(f"failed-swipe recovery lost appraisal before {checkpoint} checkpoint")
            if reverse:
                moved = self._safe_swipe(
                    *self.regions.swipe_end, *self.regions.swipe_start,
                    self.regions.swipe_duration_ms, jitter=0,
                )
            else:
                moved = self._fast_swipe()
            if not moved:
                return stopped(f"failed-swipe recovery could not reach {checkpoint} checkpoint")

            decision, frame, kind, reason = acquire_phase(
                checkpoint, expected_key,
                require_transition=False,
                save_failure_evidence=False,
            )
            if self._abort:
                return stopped("abort requested")
            if kind == "checkpoint_mismatch":
                return stopped(reason)
            if (kind != "ok" or decision is None or not decision.accepted
                    or decision.snapshot is None):
                return stopped(f"{checkpoint} checkpoint was not exact: {reason}")
            log.info("Failed-swipe %s checkpoint confirmed: %s", checkpoint, expected_key)

        # The final transition must be measured from the restored last row,
        # never from the temporary reverse checkpoint.
        self._last_stable_image = frame
        self._last_accepted_image = (
            frame if not self._paused and self._pause_generation == confirmed_generation
            else None
        )
        self._last_accepted_pause_generation = confirmed_generation
        if not self._advance_from_confirmed_appraisal():
            return stopped("failed-swipe retry could not advance from restored appraisal")
        decision, frame, kind, reason = acquire_phase(
            "forward retry",
            previous_accepted=self._last_stable_image,
            require_transition=True,
        )
        if self._abort:
            return stopped("abort requested")
        if (kind != "ok" or decision is None or not decision.accepted
                or decision.snapshot is None):
            return stopped(f"single failed-swipe retry was not verified: {reason}")
        return decision, frame, kind, reason

    @timing.timed("scan.advance", scan=True)
    def _advance_from_confirmed_appraisal(self) -> bool:
        """Advance once only after a fresh screenshot confirms appraisal."""
        while not self._abort:
            while self._paused and not self._abort:
                with timing.span("wait.pause", session_id=self.session_id):
                    time.sleep(.25)
            if self._abort:
                return False
            generation = self._pause_generation
            frame = self._fast_screencap()
            with timing.span("screen.detect"):
                screen = self.nav.detect_screen(frame)
            if self._paused or self._pause_generation != generation:
                continue
            if screen != "appraisal":
                return False
            if self._advance_appraisal(frame, pause_generation=generation):
                return True
            if self._paused or self._pause_generation != generation:
                continue
            return False
        return False

    @timing.timed("scan.forward_input", scan=True)
    def _advance_appraisal(self, frame, *, pause_generation=None) -> bool:
        """Send one observed-arrow tap or one calibrated fallback swipe.

        False on a pause/generation change means no input was sent. A pause
        after sending input retains the completed operation's result so callers
        cannot replay a forward move. Existing abort semantics remain intact.
        """
        from ..reader.appraisal_navigation import next_appraisal_target

        generation = self._pause_generation if pause_generation is None else pause_generation

        def interrupted():
            return (self._abort or self._paused or self._pause_generation != generation)

        if interrupted():
            return False
        target = next_appraisal_target(frame)
        if interrupted():
            return False
        self._last_stable_image = frame
        if target is not None:
            log.debug("Advancing appraisal through observed right arrow at %s", target)
            return bool(self._safe_tap(*target, jitter=0))
        return bool(self._fast_swipe())

    # ── Helpers ───────────────────────────────────────────────────────

    # TRACEWEAVER: entrypoint=IndexingStateMachine._read_appraisal_snapshot; req=REQ-SCAN-001; trace=TRACE-SCAN-001; ver=VER-SCAN-001
    @timing.timed("reader.snapshot")
    def _read_appraisal_snapshot(self, image):
        """Read IVs, HP, and identity from one frame before considering CP OCR."""
        import threading
        from ..reader.ocr import is_in_gym, read_caught_species
        from ..reader.nidoran import is_nidoran_read, resolve_caught_nidoran

        # One native request supplies every text field from this exact image.
        # Complete it before starting workers so neither can duplicate the IPC.
        native = self.reader.native_fields(image)
        hp_result = [-1]
        gym_result = [False]
        caught_species_result = [""]
        nidoran_gender_result = [""]
        background_error = [None]

        @timing.timed("reader.hp_gym_caught")
        def _read_hp_gym_species():
            try:
                w = self.regions.screen_width
                h = self.regions.screen_height
                gym_result[0] = (
                    getattr(native, "in_gym", False) is True
                    or is_in_gym(image, w, h)
                )
                if not gym_result[0]:
                    hp_result[0] = self.reader.read_hp(image)
                caught_species_result[0] = (
                    native.caught_species
                    if native is not None and native.caught_species
                    else read_caught_species(image, w, h, density=self.profile.density)
                )
                if is_nidoran_read(caught_species_result[0]):
                    nidoran_gender_result[0] = self.reader.specimen_gender(image)
                    resolved = resolve_caught_nidoran(
                        caught_species_result[0], nidoran_gender_result[0],
                    )
                    if resolved is None:
                        raise ValueError("Nidoran caught name and sex evidence are unresolved or conflicting")
                    caught_species_result[0] = resolved
            except Exception as exc:
                background_error[0] = exc

        hp_thread = threading.Thread(target=timing.bind_context(_read_hp_gym_species))
        hp_thread.start()
        self._reader_threads.append(hp_thread)
        detail = self.reader.read_detail_screen(image, include_cp=False)
        appraisal = self.reader.read_appraisal_screen(image)
        with timing.span("reader.join"):
            hp_thread.join(timeout=15)  # PaddleOCR first load can take 10s+
        if not hp_thread.is_alive():
            self._reader_threads.remove(hp_thread)
        detail["snapshot_read_complete"] = (
            not hp_thread.is_alive() and background_error[0] is None
        )
        if hp_thread.is_alive():
            log.warning("Timed out reading HP/species from appraisal frame")
        elif background_error[0] is not None:
            log.warning(
                "Failed reading HP/species from appraisal frame: %s",
                background_error[0],
            )

        detail["hp"] = hp_result[0]
        detail["in_gym"] = gym_result[0]
        detail["caught_species"] = caught_species_result[0]
        if (detail["snapshot_read_complete"]
                and nidoran_gender_result[0] in ("male", "female")):
            detail["gender"] = nidoran_gender_result[0]

        # Candy identifies an evolution family, not the active species. Read
        # its label only as a fallback when the professor text is unavailable;
        # native text already includes any visible candy label at no extra cost.
        if detail.get("candy_conflict") is True:
            detail["snapshot_read_complete"] = False
        elif (detail["snapshot_read_complete"] and not caught_species_result[0]
              and not detail.get("candy_family") and not self._abort):
            candy, confidence = self.reader.read_candy_family(image)
            if confidence > 0:
                detail["candy_family"] = candy
            elif confidence < 0:
                detail["snapshot_read_complete"] = False

        if caught_species_result[0]:
            log.info("Caught species from bubble: %s (OCR name: %s)",
                     caught_species_result[0], detail.get("species", "?"))
            detail["species"] = caught_species_result[0]
            detail["species_source"] = "caught"

        return detail, appraisal

    def _is_tablet_layout(self) -> bool:
        """Whether Pokemon Go is using its wide portrait/tablet layout."""
        return is_tablet_layout(
            self.regions.screen_width,
            self.regions.screen_height,
            self.profile.density,
        )

    def _should_retry_hp_on_appraisal(self) -> bool:
        """Tablet HP OCR gets one attempt; repeated overlay reads add no value."""
        return not self._is_tablet_layout()

    def _detail_cp_retry_limit(self) -> int:
        # Tablet CP is unobstructed, while narrow phones can briefly need the
        # Pokemon moved away from the text.  Both paths stay safely bounded.
        return 3 if self._is_tablet_layout() else 8

    def _should_animate_detail_cp(self) -> bool:
        return not self._is_tablet_layout()

    def _wait_after_swipe(self):
        """Apply the live GUI delay plus a minimum tablet animation settle."""
        base, jitter = config.DELAY_AFTER_SWIPE
        if self._is_tablet_layout():
            base = max(base, 0.25)
        human_delay(base, jitter)

    def _wait_for_bars(self, max_wait: float = 3.0):
        """Poll until bars are visible. Returns image or None."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            img = self._fast_screencap()
            if self.reader.are_bars_visible(img):
                return img
            time.sleep(0.3)
        return None

    @timing.timed("scan.store", scan=True)
    def _store_pokemon(self, detail: dict, appraisal: dict,
                       position: int | None = None):
        pokemon = PokemonRead(
            species=detail["species"],
            display_name=detail.get("display_name", detail["species"]),
            cp=detail["cp"],
            atk=appraisal["atk"],
            def_=appraisal["def_"],
            sta=appraisal["sta"],
            shiny=detail["shiny"],
            shadow=detail["shadow"],
            favorited=detail["favorited"],
            lucky=detail["lucky"],
            gender=detail.get("gender", "none"),
            weight_tag=detail.get("weight_tag", ""),
            height_tag=detail.get("height_tag", ""),
            is_dynamax=detail.get("is_dynamax", False),
            hp=detail.get("hp", -1),
            confidence=(detail["confidence"] + appraisal["confidence"]) / 2,
        )

        if position is None:
            position = self.count
        with timing.span("store.insert"):
            self.db.insert_pokemon(pokemon, self.session_id, position)
        self.count += 1

        if self.count == 1:
            self._first_pokemon_key = (pokemon.species, pokemon.cp, pokemon.atk, pokemon.def_, pokemon.sta)

        if self.on_progress:
            with timing.span("store.progress"):
                self.on_progress(self.count, pokemon)

        elapsed = time.time() - self._start_time
        rate = self.count / elapsed if elapsed > 0 else 0
        log.info("#%d %s (%.1f/min)", self.count, pokemon.summary(), rate * 60)

    def _is_wraparound(self) -> bool:
        if not self._first_pokemon_key:
            return False
        all_p = self.db.get_all(self.session_id)
        # Need at least 20 Pokemon before checking (avoid false positives from duplicates)
        if len(all_p) < 20:
            return False
        last = all_p[-1]
        last_key = (last.species, last.cp, last.atk, last.def_, last.sta)
        if last_key == self._first_pokemon_key:
            # Double check: also verify the second Pokemon matches
            if len(all_p) >= 2:
                # The first pokemon key should also not have CP=-1 (failed read)
                if self._first_pokemon_key[1] == -1:
                    return False  # can't trust wrap-around with failed CP
            log.info("Wrap-around: last=%s matches first=%s", last_key, self._first_pokemon_key)
            return True
        return False

    def _remove_last_n(self, n: int):
        """Remove the last N entries from the database (duplicate cleanup)."""
        all_p = self.db.get_all(self.session_id)
        if len(all_p) >= n:
            for p in all_p[-n:]:
                self.db.conn.execute("DELETE FROM pokemon WHERE id = ?", (p.id,))
            self.db.conn.commit()
            log.info("Removed %d duplicate entries", n)

    @timing.timed("recovery.reopen_appraisal")
    def _reopen_appraisal(self):
        """Open appraisal only after confirming a detail screen."""
        if self._abort:
            return False

        try:
            screen = self._retry_stream_observation(self.nav.detect_screen)
        except Exception as exc:
            log.warning("Recovery held: could not confirm detail screen: %s", exc)
            return False

        if screen == "appraisal":
            log.info("Recovery: appraisal already open")
            return True
        if screen != "detail":
            log.warning("Recovery held: expected detail screen, got %s", screen)
            return False

        log.info("Recovery: opening appraisal via menu")
        if not self._safe_tap(*self.regions.menu_button):
            return False
        human_delay(0.8, 0.15)
        if self._abort:
            return False
        if not self._safe_tap(*self.regions.appraise_menu_item):
            return False
        human_delay(1.0, 0.2)
        if self._abort:
            return False
        if not self._safe_tap(*self.regions.dismiss_professor):
            return False
        human_delay(1.5, 0.3)
        return not self._abort

    def _fast_screencap(self):
        return self._retry_stream_observation(self.adb.screencap)

    def _retry_stream_observation(self, observe):
        """Discard an invalidated capture and retry only this read operation."""
        while True:
            try:
                return observe()
            except StreamCaptureInvalidated:
                self._settled_frame_pair = None
                if self._abort:
                    raise
                # Pause retains finish-current-transaction semantics. Its
                # generation change invalidates old pixels, not the source.
                log.debug("Stream observation invalidated; retrying with fresh evidence")

    def _safe_tap(self, *args, **kwargs) -> bool:
        """Tap only while the scan is active; report whether it may continue."""
        if self._abort:
            return False
        self.adb.tap(*args, **kwargs)
        return not self._abort

    def _safe_swipe(self, *args, **kwargs) -> bool:
        """Swipe only while the scan is active; report whether it may continue."""
        if self._abort:
            return False
        self.adb.swipe(*args, **kwargs)
        return not self._abort

    @timing.timed("scan.swipe", scan=True)
    def _fast_swipe(self):
        """Send the calibrated next-Pokemon swipe without coordinate jitter."""
        return self._safe_swipe(
            *self.regions.swipe_start,
            *self.regions.swipe_end,
            self.regions.swipe_duration_ms,
            jitter=0,
        )

    def _check_battery(self):
        level = self.adb.get_battery_level()
        if 0 <= level < MIN_BATTERY_LEVEL:
            self._paused = True
            if self.on_error:
                self.on_error(f"Battery low ({level}%). Plug in and click Resume.")

    def _close_reader(self):
        """Finish owned reads before releasing a shared native OCR worker."""
        for thread in self._reader_threads:
            thread.join()
        self._reader_threads.clear()
        self.reader.close()

    def pause(self):
        self._paused = True
        self._pause_generation += 1
        self._settled_frame_pair = None
        if getattr(self.adb, "has_stream_frames", False) is True:
            self.adb.invalidate_stream_frames()
        log.info("Paused at #%d", self.count)

    def resume(self):
        self._paused = False
        if getattr(self.adb, "has_stream_frames", False) is True:
            self.adb.invalidate_stream_frames()
        log.info("Resumed")

    def abort(self):
        self._abort = True
        self._settled_frame_pair = None
        log.info("Abort requested")
