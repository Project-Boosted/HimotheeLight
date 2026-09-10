# HimotheeLight v0.8.0 — Philips Hue Integration

HimotheeLight can now run **Philips Hue lights and WLED controllers together** from the same Autodarts lighting engine. Hue is connected natively through a local Philips Hue Bridge; it is not emulated as WLED.

## New Philips Hue page

Open **Philips Hue** from the sidebar. From there you can:

- discover Hue Bridges using Philips Hue's discovery service;
- enter a Hue Bridge IP/hostname manually;
- pair HimotheeLight by pressing the **physical link button** on the Hue Bridge and then clicking **Pair After Button Press**;
- test the bridge;
- sync/rescan lights;
- enable/disable individual Hue lights;
- test a Hue light;
- configure each Hue light's Idle and Active state.

The generated Hue application key/client key are stored only in HimotheeLight's local config. Browser-facing config responses and shared profile exports do not expose those credentials.

## What Hue lights can do in HimotheeLight

Once paired, each Hue light becomes a normal HimotheeLight lighting endpoint alongside WLED. Hue lights can participate in:

- **Idle** lighting
- **Active/match** lighting
- individual dart triggers
- T20 / Bull / visit-score triggers
- 100+ / 140+ / 180 effects
- Bust
- Game Shot
- Match Shot
- player-turn/target triggers
- fixed takeout sequence
- device groups
- multi-device scenes
- scene choreography
- imported/exported lighting profiles

You can mix both technologies in one scene. For example:

- Dartboard WLED → fireworks
- Ceiling WLED → chase
- Hue room bulb → purple
- Hue lamp → white flash

They are dispatched by the same trigger engine.

## Hue colour behaviour

HimotheeLight uses the common lighting fields for Hue:

- On / Off
- Brightness
- **Colour 1**
- Transition/fade duration

For a **colour-capable Hue light**, Colour 1 is converted to Hue API CIE xy colour.

For a **white/dimming-only Hue light**, HimotheeLight still controls on/off and brightness, but it cannot make that lamp physically display yellow/red/purple.

WLED-only fields such as WLED effect ID, palette, speed, intensity and presets continue to apply to WLED endpoints. In a mixed scene, Hue simply ignores those WLED-specific fields.

## Takeout flow with Hue

The existing v0.7.4 takeout/high-score sequence now works across enabled WLED and Hue endpoints:

**Dart 3 lands**
→ best matching T20/high-score/180 effect plays first
→ **YELLOW** while the darts remain in the board
→ player removes darts
→ **RED for 500 ms**
→ restore each endpoint's configured Active/match state

Colour-capable Hue bulbs display the yellow/red colours. White-only Hue bulbs follow brightness/on-off but cannot reproduce those colours.

Game Shot and Match Shot protection remains unchanged.

## Pairing a Hue Bridge

1. Make sure the PC running HimotheeLight and the Hue Bridge are on the same local network.
2. Start HimotheeLight v0.8.0.
3. Open **Philips Hue**.
4. Click **Discover Bridges**, or type the bridge IP manually.
5. Press the **physical button on top of the Hue Bridge**.
6. Immediately click **Pair After Button Press**.
7. HimotheeLight creates a local Hue application key and retrieves the Hue lights.
8. Use **Sync Lights** later if you add/rename/remove Hue lights.

Bare bridge IPs default to HTTPS. Manual `http://` remains accepted for legacy/testing only; current Hue API v2 communication uses HTTPS.

## Profile sharing with Hue

v0.8.0 extends the v0.7.4 portable-profile system:

- Hue lights appear as **Hue slots** in exported profiles.
- Hue Bridge IP/hostname, application key and client key are **never exported**.
- On import, a Hue slot can only map to a local Hue light.
- WLED slots can only map to WLED controllers.
- Name matching still suggests compatible local endpoints automatically.

This lets the community share a mixed WLED + Hue setup without sharing network or bridge credentials.

## Upgrade from v0.7.4

1. Extract `HimotheeLight-v0.8.0.zip`.
2. Run **Setup HimotheeLight.bat** once in the new folder.
3. Run **Run HimotheeLight.bat**.
4. Your existing `%APPDATA%\\HimotheeLight\\config.json` is migrated from schema 7 to **schema 8**.
5. Existing WLED devices, profiles, triggers, scenes, groups, takeout logic and Autodarts settings are preserved.
6. Open **Philips Hue** to pair a bridge.

The Autodarts browser Game Bridge protocol has not changed, so if your existing v0.7.4 browser bridge is working you do **not** need to reinstall it for Hue support. The included bridge files are simply versioned v0.8.0 for package consistency.

## Hue vs WLED effects

Philips Hue's normal REST API is well suited to event-driven Autodarts changes such as T20, takeout, 180, Bust or Match Shot. It is not intended to be a high-frequency continuous LED streaming engine. WLED remains the better endpoint for fast addressable animations; HimotheeLight uses Hue for synchronized colour/brightness/transition changes alongside those WLED effects.

## Existing functionality retained

v0.8.0 keeps:

- WLED device support
- Idle/Active modes
- Board Manager connection
- authenticated Autodarts Game Bridge
- trigger priority and duration
- score-effect-before-takeout ordering
- yellow → red → Active takeout flow
- 60-second latched inactivity Idle
- device groups
- multi-device scenes
- timed scene choreography/repeats
- lighting profiles
- safe profile import/export and endpoint mapping

## Repository quick start

The exact tested public package is stored at **`releases/HimotheeLight-v0.8.0.zip`**.

On Windows:

1. Clone or download this repository.
2. Extract `releases/HimotheeLight-v0.8.0.zip`.
3. Open the extracted `HimotheeLight-v0.8.0` folder.
4. Run **Setup HimotheeLight.bat** once.
5. Run **Run HimotheeLight.bat**.

The release archive contains the complete Python backend, browser dashboard, Autodarts browser bridge, Windows helper scripts, PyInstaller specification and the full automated test suite. Local configuration is stored under `%APPDATA%\\HimotheeLight`; config, logs, Hue credentials and build output are excluded by `.gitignore`.

GitHub Actions expands the exact release archive, runs the Python tests, and validates the browser JavaScript on every push and pull request.
