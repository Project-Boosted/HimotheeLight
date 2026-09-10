# HimotheeLight v0.8.0

![Tests](https://github.com/Project-Boosted/HimotheeLight/actions/workflows/tests.yml/badge.svg)

**Autodarts-reactive smart lighting for WLED and Philips Hue.**

HimotheeLight is a Windows-first local lighting controller that listens to Autodarts/Board Manager events and drives WLED controllers and Philips Hue lights from the same trigger, scene and profile engine.

## Highlights

- WLED device support with effects, palettes, speed, intensity, presets and segments
- Native Philips Hue Bridge support using the local Hue API
- Idle and Active/match lighting states
- Autodarts Board Manager integration
- Authenticated Autodarts Game Bridge for match-aware events
- Individual dart triggers such as T20, D20 and Bull
- Visit-score triggers including 100+, 140+ and 180
- Bust, Game Shot, Match Shot, turn and target triggers
- Device groups containing WLED and Hue endpoints
- Multi-device scenes
- Timed scene choreography with steps, delays, holds and repeats
- Lighting profiles
- Safe profile import/export with endpoint mapping for community sharing
- 60-second inactivity Idle latch

## Takeout flow

HimotheeLight follows the rhythm of a real visit:

**Dart 3 lands**
→ best matching T20/high-score/180 effect plays first
→ **YELLOW** while the darts remain in the board
→ player removes the darts
→ **RED for 500 ms**
→ restore the configured Active/match lighting

If the player removes the darts while a score animation is still running, HimotheeLight lets that score effect finish first, skips the no-longer-needed yellow wait, then performs the red acknowledgement and restores Active.

Game Shot and Match Shot celebrations are protected from the takeout sequence.

## Quick start — Windows

1. Clone or download this repository.
2. Run **`Setup HimotheeLight.bat`** once.
3. Run **`Run HimotheeLight.bat`**.
4. HimotheeLight opens its local dashboard in your browser.
5. Add your WLED devices and/or pair a Philips Hue Bridge.
6. Open **Autodarts** in HimotheeLight and configure the Board Manager connection.
7. Install the included browser bridge if you want full match-aware Autodarts events.

Local configuration is stored under:

`%APPDATA%\HimotheeLight`

That local state, logs and Hue credentials are excluded from Git.

## Autodarts browser bridge

For the full Game Bridge:

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select this repository's `browser-extension` folder.
5. Refresh the Autodarts web app.

See **`INSTALL AUTODARTS BRIDGE.txt`** for the short installation guide.

## Philips Hue

Open the **Philips Hue** page in HimotheeLight. You can discover bridges automatically or enter the bridge IP/hostname manually.

To pair:

1. Make sure the PC and Hue Bridge are on the same local network.
2. Press the physical link button on the Hue Bridge.
3. Click **Pair After Button Press** in HimotheeLight.
4. Sync the bridge lights.

Colour-capable Hue lights follow on/off, brightness, Colour 1 and transition timing. White/dimming-only Hue lights still follow on/off and brightness but cannot physically display red/yellow/purple colours.

WLED remains the preferred endpoint for fast addressable animations; Hue is used for synchronized event-driven colour/brightness changes.

## Profile sharing

Profiles can be exported as portable JSON and shared with other HimotheeLight users.

Exports can contain:

- Idle/Active modes
- triggers
- priorities and durations
- referenced scenes and choreography
- referenced device groups
- endpoint slots

They deliberately do **not** contain WLED IP addresses, Hue Bridge credentials, Autodarts connection details or logs.

When importing, the recipient maps shared endpoint slots to their own devices. WLED slots map only to WLED, and Hue slots map only to Hue.

## Reproducible v0.8.0 release

The exact tested v0.8.0 package is represented under `releases/v0.8.0/` as Git-safe Base64 chunks because the repository connector could not reliably upload the binary ZIP directly.

Run:

`Build Release Package.bat`

HimotheeLight concatenates those chunks, rebuilds `releases/HimotheeLight-v0.8.0.zip`, and refuses to keep the output unless its SHA-256 is exactly:

`4946e20d1e8620a3b7f42d72f395528e77ca79e60995e1a9dc36e7bc76cc8da1`

For normal development or use, you do **not** need to rebuild the archive—the complete readable v0.8.0 source is checked into the repository directly.

## Testing

GitHub Actions validates both forms of the project on every push and pull request:

- the checked-in readable source
- the SHA-256 verified reproducible v0.8.0 package

The workflow runs the Python test suite, compiles Python modules, validates browser JavaScript and checks the application version.

Run the Python suite locally with:

`python -m unittest discover -s tests -v`

## Building a Windows EXE

Run:

`Build Windows EXE.bat`

The repository includes `HimotheeLight.spec` for the PyInstaller build.

## Repository structure

- `himotheelight/` — backend, lighting engine, Autodarts, WLED and Hue integrations
- `web/` — local browser dashboard
- `browser-extension/` — Autodarts Game Bridge
- `tests/` — automated regression suite
- `.github/workflows/` — CI and verified-source synchronization
- `releases/v0.8.0/` — reproducible release data
- `app.py` — application entry point
- Windows `.bat` files — setup, run and build helpers

## Upgrade from v0.7.4

v0.8.0 migrates configuration schema 7 to schema 8 while retaining existing WLED devices, profiles, triggers, scenes, groups, takeout behavior and Autodarts settings. Open **Philips Hue** after upgrading to pair a bridge.

The Autodarts Game Bridge protocol did not change for v0.8.0, so an already-working v0.7.4 bridge does not need to be reinstalled solely for Hue support.

See **`CHANGELOG.md`** for the full version history.
