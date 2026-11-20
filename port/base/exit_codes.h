/* The exit codes a port ends with, one set for every console. The comment above each code is its meaning in
 * port/CONTRACT.md "Exit codes", which port/tools/contract_doc.py writes from this file: change a meaning here, then
 * run it. tests/test_port_contract.py checks that every exit in the kits (runtime/, base/, pvr/, sdk/, saturn/) is one
 * of these and that each of these is used. base.h includes this file. */
#ifndef PORT_EXIT_CODES_H
#define PORT_EXIT_CODES_H

/* A normal close, or setup or verify succeeded, or a run reached `--stop-at` or the end of the frames a check replays
 * (`--lockstep-end exit`, hw_check, `--ram-frames`); also a Dreamcast game's own exit (its entry function returned),
 * and on Saturn `--frames N` reached or an online match ended or moved to a new copy of the game */
#define PORT_EXIT_OK 0

/* A crash or fault, or another failure (a copy that failed); on Saturn also the entry function returning (a Saturn
 * game never returns, so the recording is kept and the launcher pauses), the call depth bound and no memory for
 * online play */
#define PORT_EXIT_FAILED 1

/* The `--seconds` watchdog (test runs only); also a check-only option it could not read (`--lockstep*`, `--hw-*`) */
#define PORT_EXIT_WATCHDOG 2

/* A `must_work` stub was called, a checked build's run-time check failed, or a call into overlay RAM found no loaded
 * function; on Saturn the game halted (`tc_halt`) */
#define PORT_EXIT_HALTED 3

/* The disc image is missing or unreadable (also for a play run); also the display device was lost, the renderer ran
 * out of memory for a scene, or `--verify-assets` was given no `--assets` (KNOWN.md "Exit code 4") */
#define PORT_EXIT_DISC 4

/* The image is another game or region (also a play run's image with no boot file) */
#define PORT_EXIT_OTHER_GAME 5

/* Online play (Saturn): the two copies went out of step */
#define PORT_EXIT_NET_DESYNC 6

/* Online play (Saturn) could not start: the network did not start, no host answered in 60 s, or the host runs a
 * different copy of the game */
#define PORT_EXIT_NET_FAILED 7

#endif
