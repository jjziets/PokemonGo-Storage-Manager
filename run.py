#!/usr/bin/env python3
# TRACEWEAVER: file-role=validated-scan-entry; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
# TRACEWEAVER: entrypoint=validate_scan_start_arguments; req=REQ-SCAN-003; trace=TRACE-SCAN-003; ver=VER-SCAN-001
"""Pokemon Go Storage Manager — entry point."""

import sys
import logging
import argparse
from pathlib import Path

# Ensure project is on path
sys.path.insert(0, str(Path(__file__).parent))

from pokemgr.config import ensure_dirs


def cmd_test_adb(args):
    """Test ADB connection and take a screenshot."""
    from pokemgr.adb.controller import ADBController

    adb = ADBController()
    adb.connect()
    info = adb.get_device_info()
    print(f"Connected: {info}")
    print(f"Battery: {adb.get_battery_level()}%")
    print(f"Screen on: {adb.is_screen_on()}")

    if not adb.is_screen_on():
        print("Waking screen...")
        adb.wake_screen()
        import time
        time.sleep(1)

    img = adb.screencap()
    out = Path("test_screenshot.png")
    img.save(str(out))
    print(f"Screenshot saved: {out} ({img.size[0]}x{img.size[1]})")


def cmd_calibrate(args):
    """Create, capture, or explicitly verify calibration evidence."""
    from pokemgr.adb.controller import ADBController
    from pokemgr.calibration.profile import CalibrationProfile
    from pokemgr.calibration.evidence import (
        confirm_calibration_evidence,
        write_calibration_evidence,
    )

    adb = ADBController()
    adb.connect()
    info = adb.get_device_info()
    print(f"Device: {info}")

    # Check for an existing profile before any file is replaced.
    profile = CalibrationProfile.find_for_device(info)
    if args.confirm_verified:
        if not profile:
            print("No calibration found. Run 'calibrate' first.")
            return
        try:
            known_truth = _parse_known_truth(args.known_truth)
            confirm_calibration_evidence(
                args.confirm_verified,
                profile,
                known_truth,
                note=args.note,
            )
        except (OSError, ValueError) as exc:
            print(f"Could not verify calibration: {exc}")
            return
        profile.save()
        print("Calibration marked verified from known-truth evidence.")
        print(f"Manifest: {args.confirm_verified}")
        return

    if getattr(args, "capture_evidence", False):
        if not profile:
            print("No calibration found. Run 'calibrate' first.")
            return
        if args.screenshot:
            from PIL import Image
            with Image.open(args.screenshot) as source_image:
                image = source_image.copy()
            capture_source = f"file:{Path(args.screenshot).resolve()}"
        else:
            image = adb.screencap()
            capture_source = "adb_screencap"
        try:
            evidence = write_calibration_evidence(
                profile,
                image,
                source=capture_source,
                visible_screen=args.screen_label,
                note=args.note,
            )
        except (OSError, ValueError) as exc:
            print(f"Could not capture calibration evidence: {exc}")
            return
        profile.add_validation(evidence["validation_entry"])
        profile.save()
        print("Calibration evidence captured; verification status is unchanged.")
        print(f"Evidence: {evidence['manifest_path']}")
        return

    if profile:
        print(f"Found existing calibration from {profile.calibrated_at}")
        print(
            "Coordinates: "
            f"{profile.metadata.get('coordinate_source', 'unspecified')}; "
            "verification: "
            f"{profile.metadata.get('verification_status', 'unverified')}"
        )
        if not args.force:
            print("Use --force to back it up and create a fresh template.")
            return
        backup = CalibrationProfile.backup_for_device(info)
        if not backup:
            print("Could not back up the existing calibration; leaving it unchanged.")
            return
        print(f"Existing calibration backed up: {backup}")

    # Defaults are only templates.  A screenshot and explicit known-truth scan
    # are required before the profile can be called verified calibration.
    profile = CalibrationProfile.create_default(info)
    if args.screenshot:
        from PIL import Image
        with Image.open(args.screenshot) as source_image:
            image = source_image.copy()
        capture_source = f"file:{Path(args.screenshot).resolve()}"
    else:
        image = adb.screencap()
        capture_source = "adb_screencap"

    try:
        evidence = write_calibration_evidence(
            profile,
            image,
            source=capture_source,
            visible_screen=args.screen_label,
            note=args.note,
        )
    except (OSError, ValueError) as exc:
        print(f"Could not create calibration evidence: {exc}")
        print("The existing profile was not replaced.")
        return
    profile.add_validation(evidence["validation_entry"])
    profile_path = profile.save()
    print(f"Unverified {profile.layout} template saved for {info.resolution}.")
    print("The screenshot overlay is evidence, not proof that the targets work.")
    print(f"Profile: {profile_path}")
    print(f"Evidence: {evidence['manifest_path']}")


def _parse_known_truth(values):
    """Parse repeated ``Species=CP`` calibration truth arguments."""
    parsed = []
    for value in values or []:
        species, separator, raw_cp = value.rpartition("=")
        if not separator or not species.strip():
            raise ValueError(f"Expected Species=CP, got {value!r}")
        try:
            cp = int(raw_cp)
        except ValueError as exc:
            raise ValueError(f"CP must be an integer in {value!r}") from exc
        if cp <= 0:
            raise ValueError(f"CP must be positive in {value!r}")
        parsed.append({"species": species.strip(), "cp": cp})
    return parsed


def cmd_test_read(args):
    """Test screen reading on the current screen."""
    from pokemgr.adb.controller import ADBController
    from pokemgr.calibration.profile import CalibrationProfile
    from pokemgr.reader.screen import ScreenReader

    adb = ADBController()
    adb.connect()
    info = adb.get_device_info()

    profile = CalibrationProfile.find_for_device(info)
    if not profile:
        print("No calibration found. Run 'calibrate' first.")
        return

    reader = ScreenReader(profile)

    print("Reading detail screen...")
    img = adb.screencap()
    detail = reader.read_detail_screen(img)
    print(f"  Species: {detail['species']}")
    print(f"  CP: {detail['cp']}")
    print(f"  Shiny: {detail['shiny']}")
    print(f"  Shadow: {detail['shadow']}")
    print(f"  Favorited: {detail['favorited']}")
    print(f"  Lucky: {detail['lucky']}")
    print(f"  Confidence: {detail['confidence']:.0%}")

    if args.appraise:
        from pokemgr.adb.navigator import GameNavigator
        navigator = GameNavigator(adb, profile.regions)
        screen = navigator.detect_screen(img)
        if screen != "appraisal":
            print("\nOpening appraisal...")
            if screen == "detail":
                navigator.open_first_appraisal()
            elif not navigator.navigate_to_appraisal():
                print(f"Could not reach appraisal from screen: {screen}")
                return

        print("Reading appraisal screen...")
        img2 = adb.screencap()
        appraisal = reader.read_appraisal_screen(img2)
        print(f"  ATK: {appraisal['atk']}")
        print(f"  DEF: {appraisal['def_']}")
        print(f"  STA: {appraisal['sta']}")
        print(f"  Confidence: {appraisal['confidence']:.0%}")

        # Close appraisal
        adb.tap(*navigator.appraisal_close_target())


def cmd_index(args):
    """Run the indexing state machine."""
    from pokemgr.adb.controller import ADBController
    from pokemgr.calibration.profile import CalibrationProfile
    from pokemgr.data.database import PokemonDatabase
    from pokemgr.indexer.state_machine import IndexingStateMachine

    adb = ADBController()
    adb.connect()
    info = adb.get_device_info()

    profile = CalibrationProfile.find_for_device(info)
    if not profile:
        print("No calibration found. Run 'calibrate' first.")
        return

    db = PokemonDatabase()

    sm = IndexingStateMachine(adb, profile, db)
    sm.unfavorite_all = getattr(args, 'unfavorite', False)

    def on_progress(count, pokemon):
        print(f"  #{count}: {pokemon.summary()}")

    sm.on_progress = on_progress

    print(f"Starting indexing... (session: {sm.session_id})")
    print("Make sure Pokemon Go is open on a Pokemon detail screen.")
    print("Press Ctrl+C to stop.\n")

    try:
        sm.start(expected_total=args.total)
    except KeyboardInterrupt:
        sm.abort()
        print(f"\nStopped. Scanned {sm.count} Pokemon.")

    # Export
    if sm.count > 0 and args.export:
        db.export_csv(args.export, sm.session_id)
        print(f"Exported to {args.export}")

    db.close()


def cmd_decide(args):
    """Run the decision engine."""
    from pokemgr.data.database import PokemonDatabase
    from pokemgr.pvp.rankings_db import PvPRankingsDB
    from pokemgr.decision.engine import DecisionEngine

    db = PokemonDatabase()
    pvp_db = PvPRankingsDB() if Path("data/pvp_rankings.db").exists() else None

    engine = DecisionEngine(db, pvp_db)
    result = engine.run(session_id=args.session)

    print(f"Decisions: {result['keep']} KEEP, {result['transfer']} TRANSFER")

    if args.export:
        db.export_csv(args.export, args.session)
        print(f"Exported to {args.export}")

    db.close()


def cmd_gui(args):
    """Launch the desktop GUI."""
    from pokemgr.gui.app import run_gui
    queue_options = {"scan_queue": args.scan_queue} if getattr(args, "scan_queue", None) is not None else {}
    run_gui(
        start_scan=getattr(args, "start_scan", False),
        skip_first=getattr(args, "skip_first", None) or 0,
        resume_species=getattr(args, "resume_species", None) or "",
        resume_cp=getattr(args, "resume_cp", None) or 0,
        **queue_options,
    )


def add_scan_start_arguments(parser):
    """Share the explicit GUI start/resume options with the stream launcher."""
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--start-scan", action="store_true",
                        help="Connect and start all five scan passes with Unfavorite off")
    mode.add_argument("--scan-queue", metavar="LEDGER",
                      help="Start validated pending inventory partitions after Normal is complete")
    parser.add_argument("--skip-first", type=int, metavar="N",
                        help="Skip N saved positions on the first pass (requires --start-scan)")
    parser.add_argument("--resume-species", metavar="NAME",
                        help="Verify the first new Pokemon after --skip-first")
    parser.add_argument("--resume-cp", type=int, metavar="CP",
                        help="Verify the first new Pokemon's CP after --skip-first")


def validate_scan_start_arguments(parser, args):
    """Reject invalid resume intent before opening a GUI or touching a device."""
    if (any(getattr(args, name) is not None
            for name in ("skip_first", "resume_species", "resume_cp"))
            and not args.start_scan):
        parser.error("resume options require --start-scan")
    if args.skip_first is not None and not 0 <= args.skip_first <= 10000:
        parser.error("--skip-first must be between 0 and 10000")
    if args.resume_cp is not None and not 1 <= args.resume_cp <= 10000:
        parser.error("--resume-cp must be between 1 and 10000")
    if args.resume_species is not None:
        args.resume_species = args.resume_species.strip()
        if not args.resume_species:
            parser.error("--resume-species must not be empty")
    if ((args.resume_species is not None or args.resume_cp is not None)
            and not args.skip_first):
        parser.error("a resume target requires --skip-first greater than zero")
    if getattr(args, "scan_queue", None) is not None:
        from pokemgr.indexer.scan_queue import load_scan_queue
        try:
            args.scan_queue = str(Path(args.scan_queue).expanduser().resolve())
            load_scan_queue(args.scan_queue)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))


def main():
    parser = argparse.ArgumentParser(description="Pokemon Go Storage Manager")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    # test-adb
    sub.add_parser("test-adb", help="Test ADB connection")

    # calibrate
    cal = sub.add_parser("calibrate", help="Calibrate for connected device")
    calibration_action = cal.add_mutually_exclusive_group()
    calibration_action.add_argument(
        "--force", action="store_true",
        help="Back up the current profile and create a fresh template",
    )
    cal.add_argument(
        "--screenshot", type=str,
        help="Use this screenshot for evidence instead of a new ADB capture",
    )
    calibration_action.add_argument(
        "--capture-evidence", action="store_true",
        help="Capture an overlay for the existing profile without replacing it",
    )
    cal.add_argument(
        "--screen-label", type=str, default="unknown",
        help="Screen visible in the calibration capture (for example map or appraisal)",
    )
    cal.add_argument(
        "--note", type=str, default="",
        help="Operator note stored with calibration evidence",
    )
    calibration_action.add_argument(
        "--confirm-verified", metavar="MANIFEST",
        help="Mark existing calibration verified after a known-truth scan",
    )
    cal.add_argument(
        "--known-truth", action="append", default=[], metavar="SPECIES=CP",
        help="Known result from bounded validation; repeat at least twice",
    )

    # test-read
    tr = sub.add_parser("test-read", help="Test screen reading")
    tr.add_argument("--appraise", action="store_true", help="Also test appraisal")

    # index
    idx = sub.add_parser("index", help="Run indexing")
    idx.add_argument("--total", type=int, default=None, help="Expected total Pokemon")
    idx.add_argument("--export", type=str, default=None, help="Export CSV path")
    idx.add_argument("--unfavorite", action="store_true", help="Unfavorite all Pokemon during scan")

    # decide
    dec = sub.add_parser("decide", help="Run decision engine")
    dec.add_argument("--session", type=str, default=None, help="Session ID")
    dec.add_argument("--export", type=str, default=None, help="Export CSV path")

    # gui
    gui = sub.add_parser("gui", help="Launch desktop GUI")
    add_scan_start_arguments(gui)

    args = parser.parse_args()
    if args.command == "gui":
        validate_scan_start_arguments(parser, args)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    ensure_dirs()

    commands = {
        "test-adb": cmd_test_adb,
        "calibrate": cmd_calibrate,
        "test-read": cmd_test_read,
        "index": cmd_index,
        "decide": cmd_decide,
        "gui": cmd_gui,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
