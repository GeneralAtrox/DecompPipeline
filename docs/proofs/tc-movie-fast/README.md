# Toy Commander: old against `sq_fast` + `game_o2` + `--pvr-yuv simd`, 2026-11-02

`replay_compare.py` runs two builds side by side and compares every presented frame and the sound dump, 640x480, fixed clock,
`--present tick`, no real input. Old: judge's /O1, translated store-queue helpers, `--pvr-yuv table`. New: game.json `sq_fast` and
`game_o2`, `--pvr-yuv simd`. It stops at the first difference. There was none.

| Run | Vertical blanks | Frames | Distinct | Differ | Sound | In step with its recording |
|---|---:|---:|---:|---:|---|---|
| Recording 20261019-221349 | 22450 | 15400 | 15033 | 0 | same | No, from pad read 885 |
| Recording 20261031-121005 | 20057 | 10648 | 8788 | 0 | same | Yes |
| Recording rc_f_scene | 11634 | 6873 | 6845 | 0 | same | No, from pad read 478 |
| Recording rc_d_2 | 11610 | 6861 | 6833 | 0 | same | No, from pad read 490 |
| Recording 20261017-235620 | 10440 | 6275 | 6239 | 0 | same | No, from pad read 446 |
| Recording rc_w_scene | 10092 | 6102 | 6074 | 0 | same | No, from pad read 500 |
| Recording rc_c_240b | 8805 | 5459 | 5429 | 0 | same | No, from pad read 538 |
| Recording rc_keys3 | 6947 | 6390 | 6325 | 0 | same | No, from pad read 559 |
| Script frontend | 7000 | 6023 | 5553 | 0 | same | scripted |
| Script mission1 | 13000 | 9455 | 9407 | 0 | same | scripted |
| Script novmu | 5000 | 3406 | 3379 | 0 | same | scripted |
| No input, attract loop | 30000 | 15922 | 13073 | 0 | same | none |
| **Total** | **157035** | **98814** | | **0** | | |

Ten more recordings are not run: each one's pad section is a prefix of a longer recording's. Out of step means the build now takes a
different path from the recording's own build, so that run still compares old and new, but not the session as it was played.

**What this does not cover:** only one session stays in step, and the play it reaches is the training mission, the attract demos and the
menus. No recording reaches a later mission or multiplayer. The in-between present path and 2560x1600 are not compared here.
