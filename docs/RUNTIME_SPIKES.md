# Spikes against the runtime target

Split from `docs/RUNTIME_TARGET.md`, which has a 400-line cap.

## Spikes (checked on whole replays, thread-CPU clock, no dumps; `port-perf`)

| Where | What | Loading screen? |
|---|---|---|
| Spawn vblank 4540, Power Stone 2 vblank 7881 (the "0.3 s hitch", the "worst tick" of the replay windows) | "frame done (capture, log)" of 298-317 ms: the proof's own `--dump-frames` PNG | No: an artefact of the proof; the game's work at that blank is 3-4 ms. Without the dump flag it is not there |
| Power Stone 2 vblank 1 | 374 ms of game code: boot | Yes (start-up) |
| Spawn vblank 1 | 414 ms of game code: boot | Yes |
| Spawn vblanks 335 and 1900 | 53 and 207 ms of game code at `sofdec: movie EIDOS.SFD / DEMO2.SFD` start: `gdfs` reads the whole movie file (20.8 MB, 92.8 MB) at once | A movie start between logos: a loading moment, but the cost grows with the file, so it is avoidable (stream the file); counts against the target if the user sees it |
| Spawn vblank 2944 | 36 ms in Direct3D (and the GPU stamp): the first stage scene (`NewYork_*` files just read) | Yes (stage load) |
| Power Stone 2, no other blank over 30 ms in the whole replay | The 84.72 ms worst tick of dp-land's launcher run is not in this replay | Not reproduced: not a replay-visible load; a host stall (other processes) or something the launcher path adds. The ARM7 spikes listed above were stalls too |
