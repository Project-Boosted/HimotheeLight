HimotheeLight Autodarts Bridge v0.8.0
====================================

This small Chrome/Edge extension lets the local HimotheeLight app receive
Autodarts match state for accurate Bust, Game Shot, Match Shot, player-turn and
training-target lighting.

Install / update:
1. Open chrome://extensions (or edge://extensions).
2. Enable Developer mode.
3. If an older HimotheeLight bridge is loaded, remove it or press Reload after
   replacing its files.
4. Click Load unpacked and choose this browser-extension folder.
5. Refresh your Autodarts tab.

No Autodarts username, password or token is sent to HimotheeLight. The bridge
only forwards normalized match/game fields to http://127.0.0.1:8765.
