# HimotheeLight Changelog

## v0.8.0 — Philips Hue Integration

### Added
- Native local Philips Hue Bridge integration.
- Hue Bridge discovery through Philips Hue discovery service plus manual IP/hostname entry.
- Physical push-link pairing and locally stored Hue application/client keys.
- Hue API v2 bridge/light discovery.
- Individual Hue lights as HimotheeLight lighting endpoints.
- Hue Idle/Active configuration using on/off, brightness, Colour 1 and transition.
- Colour conversion from HimotheeLight RGB to Hue CIE xy.
- Mixed WLED + Hue base-state dispatch.
- Mixed WLED + Hue triggers, groups, scenes and choreography.
- Existing yellow/red takeout system now reaches compatible Hue lights.
- Hue support in portable profile exports/imports using type-safe endpoint slots.
- Hue Bridge credential redaction from browser-facing config and profile-sharing bundles.

### Security / portability
- Hue application/client keys remain local and are never included in shareable profiles.
- Hue import slots can only map to Hue lights; WLED slots can only map to WLED.
- Bridge sync and profile-apply API responses are sanitized so Hue keys are not exposed to the dashboard.

### Compatibility
- Configuration schema upgraded from 7 to 8.
- Existing WLED devices, profiles, triggers, groups, scenes, takeout logic and Autodarts configuration are preserved.
- Autodarts Game Bridge protocol is unchanged.

### Validation
- Existing Stage 1–7.4 regression coverage retained.
- Added Hue pairing, schema-7 migration, API-v2 payload, mixed transport, base mode, secret-redaction, type-safe import and credential-safe profile-sharing tests.

## v0.7.4 — Shareable Profiles

### Added
- Portable profile export as versioned HimotheeLight JSON.
- Per-profile **Export** button plus **Export Selected**.
- Import file preview and validation before changing local configuration.
- WLED device mapping screen for shared profiles.
- Automatic exact-name mapping suggestions.
- Required-device validation for targeted triggers/scenes.
- Portable export of referenced scenes and device groups.
- Safe remapping to new local scene/group IDs on import.
- Optional **Apply after import** control.
- Duplicate imported profile/scene/group names receive safe suffixes.

### Privacy & safety
- WLED IP addresses and hostnames are never included in exported profiles.
- Autodarts connection/account information is not exported.
- Import never modifies local WLED host/IP settings.
- Imported profiles are inactive by default.
- Invalid/future share formats and malformed dependencies are rejected.
- WLED effect/palette/preset compatibility warning is shown during import.

### Compatibility
- Configuration schema remains 7.
- Existing v0.7.3 takeout/high-score ordering, scenes, choreography, triggers, profiles and inactivity logic are preserved.
- Autodarts Game Bridge protocol is unchanged.

### Validation
- 54 automated tests passing, including six new portable-profile sharing tests.
- Export privacy, name mapping, required mapping, dependency remapping, duplicate names and round-trip apply behaviour covered.

## v0.7.3 — Score Effect Before Takeout

### Changed
- Dart-3 scoring effects now play before the fixed yellow takeout state.
- Applies to individual dart, visit score/range and combination triggers, including T20, 100+, 140+ and 180.
- Normal trigger priority decides which matching scoring effect is allowed to finish first.
- If no scoring trigger is accepted on dart 3, yellow still begins immediately.
- If darts are removed while a score effect is still running, the score effect finishes first, yellow is skipped, then red runs for the configured acknowledgement time before Active is restored.
- Game Shot and Match Shot replacement cancels any queued takeout follow-up so a late yellow/red state cannot overwrite a winning celebration.

### Runtime
- Added one-shot trigger completion follow-ups inside the lighting engine.
- Follow-ups are generation-bound and are automatically discarded when a trigger is cancelled/replaced.
- Active trigger runtime can expose the queued follow-up (`Takeout -> Yellow` or `Takeout removed -> Red`).

### Compatibility
- Configuration schema remains 7.
- Existing v0.7.2 devices, profiles, triggers, scenes, groups and settings are preserved.
- Browser Game Bridge protocol is unchanged.

### Validation
- 48 automated tests passing, including T20-before-yellow, high-visit-before-yellow, 180-before-yellow, early-removal deferral and no-score immediate-yellow cases.

## v0.7.2 — Fixed Takeout Colour Flow

### Changed
- Automatic Takeout is now a fixed system sequence: **third dart → solid yellow → darts removed → solid red → Active/match colour**.
- Takeout always starts after exactly 3 darts.
- Yellow is persistent and has no timeout while darts remain in the board.
- The configurable Takeout Started trigger effect is no longer used as the automatic takeout colour.
- An enabled Takeout Started rule can still provide its WLED device selection; if no devices are selected, all enabled WLEDs participate.
- Takeout Finished red acknowledgement remains configurable in duration and defaults to 500 ms.
- Game Shot / Match Shot celebrations remain protected from the red acknowledgement.

### Preserved
- 60-second inactivity Idle latch from v0.7.1.
- Existing devices, profiles, trigger rules, scenes, groups, Idle/Active modes and Autodarts settings.
- Configuration schema remains 7.

### Validation
- 43/43 automated tests passing.

## v0.7.1 — Takeout & Inactivity Logic

### Added
- Automatic Takeout Started trigger immediately after the configured dart count; default is 3 darts.
- De-duplication of the later real Board Manager `Takeout started` frame.
- Built-in solid-red Takeout Finished acknowledgement; default duration 500 ms.
- Automatic restoration to the current base lighting after the red acknowledgement.
- Protection for Game Shot / Match Shot winner celebrations so the red acknowledgement does not replace them.
- No-dart inactivity watchdog; default timeout 60 seconds.
- Latched Idle behaviour: a later dart in the same stalled game does not wake Active lighting.
- Latch reset on a genuine new game/match activation or new Board Manager running session.
- Autodarts UI settings for takeout flow, dart count, red-flash duration, inactivity enable and timeout.
- Live inactivity state/countdown on the Autodarts page.
- `Simulate 3 Darts → Takeout` test control.

### Compatibility
- Configuration schema remains 7; existing v0.7.0 settings migrate by adding the new Autodarts defaults.
- Existing WLED devices, Idle/Active modes, profiles, triggers, device groups, scenes and choreography are preserved.
- Game Bridge protocol is unchanged; an already-working v0.7.0 browser bridge does not need to be reinstalled.

### Validation
- 43 automated behavioural tests passing, including new three-dart takeout, red restoration and inactivity-latch regression tests.
- Python compilation, browser/dashboard JavaScript syntax and UI control-reference checks pass.

## v0.7.0 — Scene Choreography

### Added
- Ordered scene choreography with numbered Steps.
- Parallel scene actions when multiple actions share the same Step.
- Per-action delay in milliseconds.
- Per-action hold duration in milliseconds.
- Per-action WLED transition/fade timing in the scene editor.
- Scene repeat count from 1–20.
- Repeat count `0` for loop-until-interrupted scenes.
- Calculated choreography timeline duration.
- Live choreography Step and Repeat information in trigger runtime status.
- `LOOP` runtime state for infinite scenes.
- Scene action Up/Down reordering controls.

### Runtime
- Choreography runs in a cancellable background scheduler in the HimotheeLight backend.
- Same-delay scene actions retain parallel multi-WLED dispatch.
- Higher/equal-priority triggers cancel an active choreography before replacement.
- Lower-priority triggers cannot interrupt a higher-priority choreography.
- Manual base-state changes cancel active choreography cleanly.
- Finite scenes restore the current Active/Idle base state after the final step.
- Match Shot can still preserve its celebration while the underlying base becomes Idle.
- Infinite-loop scene tests are automatically limited to one repeat when using the manual Test Scene button.

### Compatibility
- Configuration schema updated from 6 to 7.
- Existing v0.6.x scenes migrate to Repeat 1, Step 1, Delay 0, Hold 0 and therefore retain their original parallel/timed-trigger behaviour.
- Existing devices, modes, profiles, triggers, device groups and scenes are preserved.
- No Autodarts Game Bridge protocol change is required for this stage.

### Validation
- Stage 1–6 regression coverage retained.
- Added migration, ordered-step, repeat, infinite-loop interruption and priority-blocking tests for Stage 7.
