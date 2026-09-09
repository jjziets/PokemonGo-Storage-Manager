# Phone scan and recalibration runbook

Use this procedure after a call, screen lock, ADB disconnect, wrong first rows,
or a change of phone, tablet, resolution, display mode, or game layout. Stop at
the first mismatch. A two-item test is the gate for a longer scan.

## Safety boundary

- This runbook reads storage; it does not authorize transfers.
- Leave **Unfavorite during scan** off.
- Unresolved Pokemon are favorited for later review after bounded CP recovery
  fails. The scanner verifies the same appraisal and an enabled favorite star
  before skipping; an existing favorite is left on.
- Do not use **Clear Database** as a recovery step.
- A screenshot overlay proves what coordinates are configured. It does not
  prove that the game accepted a tap or that OCR read the right Pokemon.
- A generated profile starts as `coordinate_source: template` and
  `verification_status: unverified`. Only a successful known-truth test should
  change it to `verified`.

## Open the Mac app

The local launcher is installed at:

```text
~/Applications/Pokemon GO Storage Manager.app
```

Find **Pokémon GO Storage Manager** in Launchpad, or open Finder and choose
**Go → Applications**. The launcher uses the project's `.venv`, supplies the
Homebrew paths needed by ADB and Tesseract, and keeps the Mac awake while the
GUI is open. It starts the app-only stream and refuses to start a second copy
while the manager or stream launcher is already running. Launcher output is
written to `logs/macos_launcher.log`; its source is `scripts/macos_launcher.zsh`.

The launcher points to this project's current absolute path. If the project
folder is moved or renamed, update or reinstall the launcher before using it.

### Stream only the game with the phone display off

Close the existing manager and scrcpy windows, connect the phone by USB, then
run this from the project directory:

```bash
.venv/bin/python scripts/stream_pokemon.py
```

This opens Pokemon GO on its own fixed-size display with Android system
decorations disabled, keeps that display active, and switches off the physical
phone display. The separate game view avoids normal notification banners from
the phone's main display. Connect in the newly opened manager; its device label
must include **App-only stream**. Screenshots, taps, keyboard input and game
launches all target that same display. The scanner stops if its display is
removed or recreated; it must not fall back to tapping the phone's main screen.

Keep both windows open while scanning. Closing either ends this paired session
and moves the game back to the phone. Click **Turn phone screen on** beside
**Connect** whenever you want to see the physical phone display; the game
stream and scanner keep their existing display. **Turn phone screen off**
darkens it again. Both controls stay available during scanning in app-only
mode. Each new scan automatically darkens the physical panel before starting;
the On button can still restore it during that scan. You can also use the
phone's power button after closing the session. Start this script again for a
new stream; display IDs are temporary and must not be copied between sessions.

The Fold6's normal physical-screen screenshots became black with the physical
display off. Its app-only capture stayed readable at 968x2376/420 dpi. Merely
opening a normal whole-phone mirror is therefore insufficient for dark-screen
scanning on this device.

### Stream frame capture and native Mac OCR

The default launcher now reads the existing app video stream directly. It
builds a project-local scrcpy client and keeps the latest 30 decoded frames in
a private buffer (about 197 MiB at this phone's resolution). Native macOS Vision
reads the name, HP, CP and caught-species text in one request per frame; IV bars
and icons are read from those same pixels. The system scrcpy installation is
unchanged.

An out-of-frame native text box rejects that frame's native results, with
legacy OCR available for those pixels. It no longer disables native OCR for
the remaining scan. The next independent image uses the same native worker;
protocol failures and timeouts still disable it.

The professor's caught-species sentence identifies the active species even
when the display name is a nickname. Visible candy labels provide a separate
evolution-family constraint: **Exeggcute Candy** includes Exeggcute and
Exeggutor forms, so it cannot establish Exeggutor on its own. The reader accepts
whole labels and aligned split lines such as `EXEGGCUTE` above `CANDY` or
`CANDY XL`. Conflicting labels remain unresolved. When the professor's text is
unavailable, candy plus exact HP/IV/CP evidence must resolve the active species;
the candy name is never copied into the species field.

The bottom status bar shows CPU and resident memory for the manager and its
attributable OCR, ADB, and paired stream processes. Hover over it for each
process's counters. Sampling runs off the UI thread every two seconds, with
100% CPU meaning one core. GPU reads **unavailable**: per-process GPU profiling
on this Mac requires privileges that the manager does not request. Shared
memory pages may appear in more than one process's resident memory total.

Scanning still starts with HP and IVs. A unique calculated CP needs two
independent appraisal observations. Ambiguous CP uses visible text, then model
animation. During animation, the scanner examines up to 30 source frames over
1.2 seconds and requires matching exact CP observations. Missing or conflicting
text never becomes a guessed digit. A fresh capture and the existing bounded
recovery remain available if the window does not resolve it.

The stream uses a new session identity on every launch. Android source
timestamps are mapped to the Mac clock; decoder arrival alone cannot make an
old frame fresh. Pauses discard pending evidence. A temporary absence of fresh
video frames permits a new capture of the same verified app display; changed
display/session identities and broken clock evidence stop the scan.

To compare against the earlier screenshot transport, start a separate session
after closing both windows:

```bash
.venv/bin/python scripts/stream_pokemon.py --capture-backend=jpeg
```

Enable action timings before launch when measuring a short scan:

```bash
POKEMGR_SCAN_TIMING=1 .venv/bin/python scripts/stream_pokemon.py
```

`SCAN_TIMING` events record capture, display validation, OCR, waits, recovery and
navigation spans. Parent and child spans overlap, as do parallel reader tasks;
their inclusive totals must not be added as elapsed scan time. Timings are off
by default. Summarize a completed short run with:

```bash
.venv/bin/python scripts/summarize_scan_spans.py logs/scan_YYYYMMDD_HHMMSS.log
```

The report subtracts the union of child spans to show exclusive costs and
flags incomplete evidence. `--format json` produces machine-readable output.
Build and timestamp details are in
[`scripts/scrcpy_frame_sink/README.md`](../../scripts/scrcpy_frame_sink/README.md)
and [`scripts/android_clock/README.md`](../../scripts/android_clock/README.md).

### Mass Actions

Mass Actions uses the same fresh stream frames, native text reader and
independent appraisal confirmation as scanning. Category favorites and
**Unfavorite ALL** confirm species/HP/IV identity and star state without CP
recovery. Keeper actions use the scanner's HP/IV-first CP resolution, then
require an exact species/form, CP, HP, IV and category match to the database.
Nicknames are not matching keys. Unresolved forms or conflicting keeper
records remain unmatched for review.

Searches stay unchanged when a star changes. Unfavorite therefore traverses
all storage with `cp0-`, including already-unstarred entries. An empty search
shows the game's suggestion tiles, so it is never used for an action pass.
Identical-stat neighbors are
separate positions; verified movement and the initial filtered count determine
progress. Before a star or swipe, the current appraisal is checked again.
Each star change uses one tap followed by readback, with no blind retoggle.

Before opening a filtered list, Mass Actions reads the full native Android
search editor, verifies that clearing succeeded, and checks the exact query
before applying it. It also confirms that the underlying screen is storage,
since nickname dialogs use the same kind of native editor. The full value is
available even when the visible search line is clipped. An unavailable or
mismatched read holds the action before any Pokemon is opened. A short-lived
display-bound observer is built with the existing Android/JDK tools; it
installs no APK or persistent accessibility service. See
[`scripts/android_search/README.md`](../../scripts/android_search/README.md).

Use **Fav Keepers (Dry Run)** to inspect proposed matches without star changes.
It still navigates the phone. Pause discards pending evidence and Stop waits
for the worker to finish; a held action shows the reason and preserves its
partial counts. Mass Actions does not transfer Pokemon or change database
decisions.

### Decisions

**Run Decision Engine** evaluates the saved collection; it performs no phone
scan or OCR. It groups the stored species/forms and applies the checked keep
rules to CP, IVs, category flags and available PvP/size data. PvP table lookup
uses the exact GameMaster species/form ID. A Pokemon already above the league
CP limit is ineligible for that PvP rule. Unchecking every optional rule leaves
only the existing last-of-species protection. Running the engine recalculates
recommendations; it does not transfer Pokemon or change their stars.

**All 100% IVs** is enabled by default and keeps every exact 15/15/15 specimen,
including identical copies, lower-CP copies and all scanned categories. It
checks the three IV values, not a rounded percentage or star label. These
keepers are included in **Fav Keepers** after rerunning the decision engine.

**Highest CP** is enabled by default and protects the highest current CP in
each stored species/form group alongside the best-IV and PvP picks. Equal CP
is broken by IV total; an exact tie selects one occurrence. If the same Pokemon
wins multiple rules, it is kept once and displays its first keep reason. The
new rule takes effect when the decision engine is rerun; favoriting remains
a separate action.

**Fav Keepers** and **Unfavorite All** in Decisions use the same verified
phone-reading and action path as Mass Actions. Their progress, Pause, Stop and
final results stay in Decisions. Dry runs report what would be favorited;
failed, stopped and unmatched results remain visible until another action
starts. Controls remain locked until the action worker has finished cleanup.
The engine cannot replace recommendations while a phone operation is active.

To start a requested full scan directly, including while the Mac display is
locked, close the existing paired session and run:

```bash
.venv/bin/python scripts/stream_pokemon.py --start-scan
```

This opens the normal manager with Pause/Stop controls, connects, and starts
Normal, Shiny, Shadow, Dynamax and Gigantamax passes with no per-pass limit,
no resume offset and Unfavorite off. Connection and physical-screen darkening
must succeed first. The flag starts scanning; database clearing is a separate
explicit action. Opening the app without the flag retains manual start.

To retain a saved prefix and resume the first pass, supply its number of visited
positions and the expected first new Pokemon. For the Zygarde failure at
position 79 (78 stored, no skips):

```bash
.venv/bin/python scripts/stream_pokemon.py --start-scan --skip-first 78 --resume-species Zygarde --resume-cp 2575
```

This reopens the filter, verifies each skipped transition, and checks the
resume target before storing new rows. The offset applies only to the first
pass; subsequent passes start normally. Existing rows remain in their original
session. Use visited positions, including skipped reads, rather than only the
stored count. Resume options require `--start-scan` and the same storage order.

## 1. Stop stale processes

If the GUI is responsive, click **Abort**, wait for **Stopped**, and close it.
If it is not responsive, find the exact process before stopping it:

```bash
ps -axo pid,ppid,etime,command | rg '[p]ython.*run.py gui|[c]affeinate -dims|[a]db'
kill <old-gui-pid>
```

Use `kill -TERM <pid>` only for the identified old GUI. Do not kill every
Python process on the Mac. Check again before starting another copy.

If an earlier maintenance session left `caffeinate` running, stop its listed
PID when the scan is over:

```bash
kill <caffeinate-pid>
```

## 2. Establish a clean phone and ADB state

1. Connect the phone by USB.
2. Turn on and unlock the phone screen.
3. Dismiss any call, notification shade, permission prompt, Samsung game
   overlay, or lock screen.
4. Open Pokemon Go and keep it in the foreground.
5. On Samsung, disable Game Booster **Touch Protection**. Its overlay can make
   screenshots look valid while consuming ADB taps. If it is stuck, run:

```bash
/opt/homebrew/bin/adb shell am force-stop --user 0 com.samsung.android.game.gametools
```

Check that exactly one authorized device is present:

```bash
/opt/homebrew/bin/adb devices -l
.venv/bin/python run.py test-adb
```

The device must say `device`, not `unauthorized` or `offline`. For
`unauthorized`, unlock the phone, accept the USB debugging prompt, and retry.
`test-adb` writes `test_screenshot.png`; inspect it before continuing.

On the 2026-08-24 Fold6 run, the unfiltered header was `2947/3250` and the
five-pass Normal filter matched `2622`. The older 30px-high count crop clipped
`Q(2622)` and misread it as `Q(9677)`; the active 50px crop reads the full
glyphs. If a count exceeds the visible owned total, stop immediately and check
the storage header rather than starting with the OCR value.

### Locks and sleep

- A locked **phone** blocks the scan because coordinates no longer refer to
  Pokemon Go. Keep it unlocked and awake.
- A locked **Mac display** does not by itself invalidate ADB, but Mac sleep can
  pause the GUI or disconnect USB. Keep the Mac awake for the bounded test or
  scan:

```bash
caffeinate -dims &
echo "caffeinate PID: $!"
```

Record that PID and stop it when finished. A phone call or full-screen call
overlay invalidates the current scanner state even if ADB remains connected.
Abort and restart this recovery procedure; do not let the old loop continue.

## 3. Inspect the existing calibration

The profile is selected by model, serial, and current resolution:

```bash
ls -lt calibrations/*.json
jq '{schema_version,layout,calibrated_at,metadata,validation_results}' \
  calibrations/YOUR_DEVICE_PROFILE.json
```

Schema v1 profiles load without data loss, but are treated as
`legacy_profile` and `unverified` until saved and checked. Schema v2 records:

- `layout`: `phone` or `tablet`;
- `metadata.coordinate_source`: `template`, `legacy_profile`, or `manual`;
- `metadata.verification_status`: `unverified` or `verified`;
- validation evidence and the manifest used for verification.

If the resolution, density, folded/unfolded mode, game layout, or screen targets
changed, recalibrate. A profile for the Fold6 cover screen must not be reused
for its inside display or for a tablet.

## 4. Capture evidence or create a fresh template

To annotate the current screen against an existing profile without replacing
anything:

```bash
.venv/bin/python run.py calibrate \
  --capture-evidence \
  --screen-label appraisal \
  --note "pre-scan appraisal target check"
```

To replace the profile with a newly scaled template:

```bash
.venv/bin/python run.py calibrate \
  --force \
  --screen-label appraisal \
  --note "recalibrate after interrupted phone session"
```

`--force` first copies the old JSON to `calibrations/backups/*.bak.json`. It
then saves a fresh **unverified template**. It never labels scaled template
coordinates as verified calibration.

Each capture creates an ignored local evidence bundle:

```text
cache/calibration/<device-fingerprint>/<timestamp>/
  screenshot.png
  annotated.png
  manifest.json
```

Open `annotated.png`. Red boxes are read regions, cyan crosshairs are tap
targets, and the yellow line is the next-Pokemon swipe. The black header states
the coordinate source and verification status. Confirm targets on separate
map, storage, detail, and appraisal captures; a map capture cannot validate IV
bars or CP/name regions. On appraisal, `appraisal_close_x` is the real bottom
centre X. The older `close_appraisal_target` name refers to the right-arrow/
next-Pokemon control and must not be used to close the screen.

On the Fold6 live gate, the edge arrow was not reliable enough for automated
advance and could dismiss appraisal. The active scanner uses the calibrated
leftward swipe instead: `(800,1188) -> (170,1188)` for 300 ms with no
coordinate jitter. A 200 ms swipe animated but returned to the same Pokemon.
The scanner must witness a different settled identity after every gesture.

If coordinates need manual tuning, edit only the matching profile, set
`metadata.coordinate_source` to `manual`, and leave
`metadata.verification_status` as `unverified`. Capture evidence again after
each adjustment.

## 5. Start the app cleanly

Check that no old GUI remains, then start one copy:

```bash
.venv/bin/python run.py gui
```

Click **Connect** and confirm the displayed model/resolution. For recovery from
an uncertain screen, use **Multi-Pass Scan** so navigation re-enters storage.
Use **Scan from Here** only when the phone is visibly on the intended Pokemon
detail or appraisal screen.

The scanner reads HP, IVs and species identity from a settled appraisal
screenshot first. When those stats allow one exact CP, a second independent
complete appraisal read must confirm the same species, display name, HP and
IVs. This path performs no CP OCR and stays on appraisal. HP rounding can
leave multiple levels and CP values; the scanner never chooses arbitrarily
among them.

For ambiguous results, it tries one quick CP OCR pass, followed if needed by
all alternate OCR variants on the same screenshot against the exact species,
HP and IV candidates. It accepts a fully observed exact value; it does not
join partial digits or choose between conflicting candidate matches. Only
unresolved results proceed to the optional gesture recovery below.

Normal stability observations remain separate captures. **Frame interval**
defaults to 0.15 seconds with up to 0.10 seconds of jitter. Capture and decoding
time count toward that interval, avoiding extra sleep when transport already
took long enough. The minimum interval can be adjusted live in Speed Tuning;
two settled observations are still required. The UI shows the calibrated
carousel swipe duration separately, since that verified gesture determines
whether the game advances reliably. The old Appraise delay, Swipe delay,
Bar wait and Swipe ms preferences did not control this stable scan loop and
are no longer presented as active tuning controls.

The scanner retains both settled images for independent HP/IV confirmation.
When their complete identity and exact calculated CP agree, it does not take
a third screenshot just to repeat that confirmation. Pause, abort, retry or
recovery input invalidates that pair. An incomplete or conflicting pair uses
the fresh-read recovery path. The pre-swipe screenshot remains fresh.

App streams request native quality-100 JPEG captures for faster transfer and
decoding. Unsupported JPEG falls back to a new PNG capture of the same
verified display. Malformed images, changed geometry or display identity still
stop the operation. Manual controller use defaults to PNG. Size tags are off
by default; enabling them adds OCR of visible size labels.

**Try taps and previews for unresolved CP (slower)** is enabled by default and
uses the additional recovery below only when calculation cannot resolve CP.
Its value is saved with Speed Tuning settings and is frozen for each worker.
It can be disabled to omit model/dialog attempts. After bounded recovery and
rereads still fail, the scanner saves evidence, favorites the unresolved
Pokemon for review, then skips that position and continues. The favorite
toggle is sent only for a positively recognized off star; an on star is kept.
Every favorite readback must retain the complete species, HP, and IV identity.
A small spelling variation in the default species name is allowed for review
only when the static appraisal pixels also agree. Before closing appraisal,
such a name mismatch gets at most three fresh reads. If none matches the
original name, the scanner saves the mismatching fields and both images, then
favorites and skips the unresolved position. This does not authorize a CP
calculation or attach CP from another screen. Changed stats, arbitrary
nicknames, unverified transitions, and unrecognized favorite states remain
stop conditions. A pause during the read-only confirmation discards it and
reacquires fresh evidence after resuming.

With that option enabled, unresolved complete evidence closes appraisal with
its actual X, confirms the detail screen,
and tries up to four
model taps. If the model still covers CP, it tries up to four short rotations
through the upper model area. After each rotation, it checks the detail
identity and CP; if CP is still unresolved, it taps the model to animate it
from that angle and reads again before the next rotation. CP exposed by the
rotation alone needs no extra tap. Each gesture is followed by a random 0.2-1.2
second wait before CP capture; screenshot transport adds latency. Every detail
frame must retain the same name and HP. An invalid first OCR result triggers
other OCR passes on that frame; recovery requires one fully observed CP that
exactly fits the original species family, HP and IVs. Conflicting exact values
are rejected. A valid first visible CP is preserved.

If both gesture sequences fail, recovery can read the current CP from the
**Power Up** preview. It requires an observed left-column detail button,
matching preview title and explicit **Cancel** label. It reads only the CP
before the arrow, then cancels; the confirmation button is never a target.
The detail name and HP must still match and the observed detail menu must
reappear before recovery continues. A named Rare Candy conversion prompt can
appear when the Pokemon lacks enough candy. Recovery recognizes and cancels
that prompt, then returns to appraisal; candy quantities never supply CP.
Dialog recognition gets three captures with 0.2–1.2 second retry waits. A
dialog that remains unrecognized stops the scan and saves its screenshot under
`cache/scan_failures/<session>/position_NNNNN_cp_preview.png` with phase/reason
metadata. It does not send a guessed cancel, confirmation, or carousel swipe.
Stopping during the preview leaves it open; dismiss **Cancel** in the stream
before restarting.

Recovery reopens appraisal and rereads all fields, accepting revealed CP only
when the complete identity still matches and exact validation succeeds. If
visible recovery fails, the configured calculated-CP fallback is applied to
the final appraisal snapshot. A
settled, confirmed appraisal that remains unreadable is recorded as a skip
and scanning continues; an unconfirmed screen cannot authorize the next swipe.
The verified Fold6 profile owns the 300 ms
carousel swipe duration; model rotations use a separate short, slow gesture.
Speed Tuning displays that calibrated carousel duration without overriding it.

### A forward swipe that does not confirm the next Pokemon

If a forward swipe still shows the last accepted tuple, the scanner may make
one bounded recovery attempt. It must first reverse to the distinct previous
accepted checkpoint, then move forward and verify that the last accepted
checkpoint has been restored. Only after both exact checks pass does it retry
the forward swipe once and require a verified next appraisal. Probe reads do
not add database rows.

There is no blind extra swipe. Missing checkpoints, a mismatched restore, an
abort or an unverified retry stops the scan. Identical adjacent species/CP/HP/IV
tuples remain ambiguous without observed motion or distinct checkpoint proof;
an unchanged tuple alone cannot establish whether the swipe failed or reached
another identical Pokemon.

### What checked scan-pass boxes mean

Each checked category creates a separate Pokemon Go search pass. It does not
mean “look for this label inside every already-open Pokemon.” For example,
checking **Shiny** runs a shiny-filtered pass. The **Normal** pass excludes
categories that have their own checked pass, so the same category is not meant
to be scanned twice. **Max per pass** applies separately to every checked pass.

For calibration, check **Normal** only, uncheck Shiny/Shadow/Lucky/Dynamax/
Gigantamax/Custom, and set **Max per pass** to `2`.

## 6. Run the two-item known-truth gate

Use these conservative settings:

- Normal pass only;
- Max per pass: `2`;
- Unfavorite during scan: off;
- Exact CP recovery: on; it tries unique HP/IV calculation before CP OCR and
  requires a second complete appraisal read;
- Size tags: off;
- no skip/resume target.

Arrange or filter two known visible-CP Pokemon, then start the scan. If an old
known-truth Pokemon no longer appears in search, do not force the expression;
use two available Pokemon and record the visible CP/HP/IV results. The current
verified Fold6 gate is:

| Position | Pokemon | Required CP |
|---:|---|---:|
| 1 | Yungoos | 369 |
| 2 | Yungoos | 287 |

Historical regression values remain useful when those Pokemon are available:
Zubat is CP10 and the known Hisuian Zorua is CP882. The combined expression
`zubat&cp10,zorua&cp882` returned zero on the live account, so it is not the
current calibration filter.

Both rows must also belong to the correct Pokemon: species, IVs, and HP must not
be a mixture of the previous and next screen. If either CP is missing or wrong,
if the first Pokemon is skipped, or if the second row repeats the first, abort.
Do not “see whether the third one works.”

CP cannot be derived from HP and IVs alone. The authoritative caught species
must also constrain the candidates, and every remaining form/level candidate
must give the same CP. Equivalent forms may share one numerical result: keep
the caught family name instead of guessing the form. A formula can be
mathematically correct while its inputs came from different transition frames.

After the visible two-item gate passes, test the special hidden-CP case by
itself with **Calc CP** enabled:

| Pokemon | Required result | Why |
|---|---:|---|
| Zygarde Complete Forme | 2575 | Its model can permanently cover the CP text; use authoritative species plus a unique IV/HP fingerprint. |

With **Exact CP recovery** enabled, calculation is attempted before CP OCR
and succeeds only when all exact candidates leave one numerical CP. For
example, Exeggutor HP158 with IV8/9/4 has CP2325 in both normal and Alolan forms;
the scanner records that CP with the generic Exeggutor name after independent
confirmation. Multiple possible CPs require visible OCR or further recovery.
Once recovery observes an exact CP, a conflicting final read is rejected.

## 7. Inspect logs and rows

The multi-pass scanner creates `logs/scan_YYYYMMDD_HHMMSS.log`:

```bash
LOG=$(ls -1t logs/scan_*.log | head -1)
echo "$LOG"
tail -n 160 "$LOG"
rg -n "ERROR|WARNING|mismatch|stale|skip|CP" "$LOG"
```

Inspect only the newest session in the local database:

```bash
sqlite3 -header -column data/pokemon.db <<'SQL'
WITH latest AS (
  SELECT id FROM scan_sessions ORDER BY started_at DESC LIMIT 1
)
SELECT position, species, cp, atk, def_, sta, hp, confidence
FROM pokemon
WHERE scan_session_id = (SELECT id FROM latest)
ORDER BY position;
SQL
```

Do not clear the database to hide a failed test. Keep the log and session as
diagnostic evidence, or remove a confirmed bad local row only as a separate,
explicit maintenance action.

### Failed-frame evidence

When a position exhausts all three appraisal-validation attempts, the scanner
tries to preserve the final frame under its session ID:

```text
cache/scan_failures/<session-id>/
  position_00142_appraisal.png
  position_00142_cp.png
  position_00142.json
```

The JSON records the position and rejection reason. The number is the pending
one-based storage position, including **Skip first** and positions already
visited; it is not the count of stored rows. Keep the full frame and CP crop
with the corresponding scan log when diagnosing a skipped read. Evidence
write failures are logged and do not interrupt scanning. Not every transition
stop reaches this evidence-saving path.

## 8. Mark a calibration verified

Only after two available visible-CP rows and the isolated Zygarde 2575 case all
pass, mark the matching evidence manifest verified. The Fold6 verification
performed on 2026-08-24 used:

```bash
.venv/bin/python run.py calibrate \
  --confirm-verified cache/calibration/<device>/<timestamp>/manifest.json \
  --known-truth "Yungoos=369" \
  --known-truth "Yungoos=287" \
  --known-truth "Zygarde Complete Forme=2575" \
  --note "two visible rows stable; hidden CP uniquely resolved"
```

This records the known truth in both the manifest and profile. It does not make
a profile portable to another display mode or resolution.

## 9. Finish or recover

For a successful gate, increase the limit gradually before a full scan. Keep
the first longer run attended.

For a failure or interruption:

1. click **Abort**;
2. save the newest log path;
3. capture the current screen with `calibrate --capture-evidence`;
4. close the GUI;
5. restore phone/ADB state from section 2;
6. rerun the two-item gate from the beginning.

When all work is done, close the GUI and stop the recorded `caffeinate` PID.
