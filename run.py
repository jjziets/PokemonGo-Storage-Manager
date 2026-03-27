#!/usr/bin/env python3
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
    """Run calibration for the connected device."""
    from pokemgr.adb.controller import ADBController
    from pokemgr.calibration.profile import CalibrationProfile

    adb = ADBController()
    adb.connect()
    info = adb.get_device_info()
    print(f"Device: {info}")

    # Check for existing profile
    profile = CalibrationProfile.find_for_device(info)
    if profile:
        print(f"Found existing calibration from {profile.calibrated_at}")
        if not args.force:
            print("Use --force to recalibrate")
            return

    # Create default profile based on resolution
    profile = CalibrationProfile.create_default(info)
    profile.save()
    print(f"Default calibration saved for {info.resolution}")
    print("You'll need to tune the regions using the GUI or by editing the JSON.")
    print(f"Profile: calibrations/{profile.fingerprint}.json")


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
        print("\nTapping appraise...")
        adb.tap(*profile.regions.appraise_button)
        import time
        time.sleep(2)

        print("Reading appraisal screen...")
        img2 = adb.screencap()
        appraisal = reader.read_appraisal_screen(img2)
        print(f"  ATK: {appraisal['atk']}")
        print(f"  DEF: {appraisal['def_']}")
        print(f"  STA: {appraisal['sta']}")
        print(f"  Confidence: {appraisal['confidence']:.0%}")

        # Close appraisal
        adb.tap(*profile.regions.close_appraisal_target)


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
    run_gui()


def main():
    parser = argparse.ArgumentParser(description="Pokemon Go Storage Manager")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    # test-adb
    sub.add_parser("test-adb", help="Test ADB connection")

    # calibrate
    cal = sub.add_parser("calibrate", help="Calibrate for connected device")
    cal.add_argument("--force", action="store_true", help="Force recalibration")

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
    sub.add_parser("gui", help="Launch desktop GUI")

    args = parser.parse_args()

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
