# Spawn: any aspect ratio, the proof

`docs/RUNTIME_TARGET.md`, "Aspect-ratio proof per game". Candidate build: kit 9c16aaa, game 827de2c, on build box B (16:10, 16:9, 21:9). 4:3, 32:9 and 3:4 ran on the build before it
with the same game code, kit db738a3 before the merge of live-Spawn.

The same scripted run to blank 7000, from boot through a skipped intro movie, the menus and character select into an
Arcade fight, on the fixed clock, at each shape (`--aspect` with `--size`; `--aspect fit --size 2560x1600` for 16:10, which
is the game's default):

| Shape | Size | Sound written (first 16 of SHA-256) |
|---|---|---|
| 4:3 | 1280x960 | db07d827ad7929fb |
| 16:10 (Fit window) | 2560x1600 | db07d827ad7929fb |
| 16:9 | 1280x720 | db07d827ad7929fb |
| 21:9 | 1680x720 | db07d827ad7929fb |
| 32:9 | 1920x540 | db07d827ad7929fb |
| 3:4 | 720x960 | db07d827ad7929fb |

The game logs of the 4:3, 32:9 and 3:4 runs match but for the arguments, the log's own cost and the end's seconds, so the
game ran the same. The frames checked by eye (kept out of this public copy): title, menu, character select and play at every
shape. Nothing is stretched. The fight fills every shape with no holes, and shows more at the sides or above and below.
That last sentence of the first proof read "needs no culling change". It was wrong: see "Across motion" below.

What stays as it is:

- Movies and the title are 4:3 pictures, centred with bars.
- 2D (the HUD, menu panels) stays 4:3 in the middle.
- At 32:9 the menus' wood backdrop, a model of the game's, ends about 1% short of each side, so black and a little of the
  green behind it show at the edges. 21:9 is filled.

## Across motion

The first proof looked at still frames. A still frame can't show a pop, which only exists between two frames, or a part
cut at the old edge, which only shows when a fighter stands there. Four things passed it and were wrong, all found in
play. Candidate exe: kit and game branches `spawn-logos`, on build box B.

| What the player saw | Cause | Fix |
|---|---|---|
| Fighters cut outside the old screen, whole pieces of the body missing (capture 20261102-202849, The Curse) | `Lib_RenderModelAlpha` 8c05fec2 and `Lib_RenderModelC` 8c064220 test each part of a model against `g_viewParams.screen`, 640x480, and skip what is outside | `port/src/widescreen.c` wraps both: the rectangle is the frame's while they draw and goes back after. No other reader is wrapped: `Sprite_Draw`, 8c058b70 and `Disp_BuildScreenMatrix` use it for mapping. `Lib_RenderModelB` 8c0639c0 has no read; 8c056220 and 8c0562f0 have no callers |
| Gallery's SPAWN logos pop away at the old edge (capture 20261102-201851) | A tiled backdrop of sprites that wraps just past the 640x480 screen | Kit `sprite_fields`, game.json names it |
| The boss intro's opening stops at 4:3 and the rest pops open (capture 20261102-204050) | `Wipe_Update` 8c01781c opens a clip from the centre to 640x480 | Kit `pvr_set_clip_opens_frame`: during a wipe the clip is a share of the frame, so it reaches the frame's edges as the wipe ends. The view's mapping is the game's |
| "Please remember..." starts and vanishes in view wider than about 1.86:1 | Drawn once at x = 760 - (t & 0x7ff), both ends off screen only at 4:3 | Its period is 2048, so `Port_TextDraw` (8c01d3f8) draws it a period to either side on a wide frame |

Proof, all headless on the fixed clock:

- **Fighters, whole fight.** The capture's recording (all of Stage 1 to The Curse, 46335 frames), sampled across the fight
  in 255 frames (pairs every 340 frames, and the last ten frames). Sound (888a76a5805c1780) and the 226 lines of the game's
  log are the same on the live build at 4:3 and 21:9, and the candidate at 4:3, 16:9, 21:9, 32:9 and 3:4. So gameplay does
  not change with the shape. The candidate at 4:3 matches the live build at 4:3 in all 255 frames.
- **Against a wider test.** A control build with one more screen each side of the rectangle: 7 frames of 255 differ at
  16:9, 16 at 21:9, 27 at 32:9, 5 at 3:4. The largest (34240, 34920, 41380 at 21:9; 5000, 34240, 34920, 40700 at 32:9) were
  looked at: the differences are scenery near the camera, the control's with holes and black where the wall is whole on the
  candidate. Fighters match. So the frame's own rectangle is the right one: a wider one is not better. The 3:4 frames and
  the smaller differences were not looked at. Frame 46335 shows The Curse whole at 21:9, where the live build shows a head,
  a gun and legs.
- **Gallery logos.** `motion_check --side-only`: live build 4 pops, worst tile 51%; candidate 0 at 16:9, 21:9, 32:9 and
  3:4, worst 4%. 4:3 byte-identical in 256 frames; sound 3587d532d96c92bc at every shape.
- **"Please remember".** 255 consecutive frames across the wrap (4080 to 4334): at 32:9 the live line's start pops in at
  frame 4206 (63% of a tile); the candidate shows the tail leaving and the next start arriving together, 0 pops. The
  check's entries at 16:9 and 21:9 (21 and 31, at most 40% of a tile) are the line's ends sliding past the window's edge
  at x -240 and 722, not pops: it can't tell small text moving over a still backdrop. 4:3 byte-identical in 256 frames;
  sound 131adc357e7bd8f6 at every shape.

- **Boss intro's wipe.** Replay of the user's capture, 268 frames across the moment they reported (frame 89428): the live
  build's wipe stops at the 4:3 rectangle and the sides pop in at the end; the candidate opens smoothly to the window's
  edges, and the letterbox bars reach the sides all the way through. Sound 026de9e8277c7917 and the game log the same as
  the live build's. The fight's replay at 4:3 is byte-identical to the live build in 255 frames, sound 888a76a5805c1780.

What stays as it is:

- **Two players' split view stays in the 4:3 middle** of a wide window, and a big line such as A BOSS APPEARED reaches past
  it. Not widened: each player's view is its own window into the frame, and the wrappers above don't reach it.
- A part too near the camera is skipped by the game's own test (w - r > limit), at 4:3 too: in the capture the player's
  own torso is missing while the fist and legs show, the same on the live build at 4:3. That is the original's behaviour as
  read from the code; Flycast can't replay the recording, so it isn't compared there.
- During the boss intro's wipe the letterbox bars cover only the 4:3 middle: for the user to judge.
- `motion_check` can't judge a moving camera (153 to 190 "pops" in a fight): that is what the control comparison is for.

The game's option Standard/Distant is not the cause: it is a field of view, the part test reads projected positions, and
the reported capture's own save has it at Standard (byte 0x8cfe0197 = 1 through the replay).
