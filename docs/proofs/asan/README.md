# AddressSanitizer: the first runs (2026-11-09)

The ASan build (`build.py --asan`, Saturn `DP_ASAN=1`, c8ce911e) was tried on build box A for one build each of Toy Commander
(live 2b2ddec on kit 35112d00 plus the ASan patch) and Robo Pit (a4fffdc), and on build box B for Power Stone 2. Each ran
headless on a fixed clock with `ASAN_OPTIONS=continue_on_error=1`. Runner: `asan_m.py` here, with its machine paths as they
were (TC and Robo Pit on build box A; PS2 came built from build box B). It is the reference for the runs every kit landing now
owes (AGENTS.md "Test rules", rule 3); its result lines are the format `tools/asan_landing.py trailer` reads.

| Run | Exit | ASan errors | What it shows |
|---|---|---|---|
| plant_tc | 1 | 1 | heap-buffer-overflow at host_main.cpp:80, the plant: the TC build catches it |
| plant_robopit | 0 | 1 | the same, Robo Pit's build |
| plant_ps2 | 1 | 1 | the same, PS2's build |
| tc_frontend (replay to 3,000 blanks) | 0 | 0 | |
| tc_mission (replay to 6,000 blanks) | 0 | 0 | |
| robopit_route (3,000 frames of input) | 0 | 0 | |
| ps2_111340 (replay to 3,061 blanks) | 0 | 0 | |

No host heap, stack or global error in any game run. Spawn was not run then; the rule now asks for it on every Dreamcast
kit landing. Guest RAM is a mapped section, not the heap, so ASan sees only the host's own memory.
