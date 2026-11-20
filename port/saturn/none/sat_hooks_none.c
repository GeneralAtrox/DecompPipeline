/* The empty game table, for a Saturn game with no code of its own in the kit's menus and calls (build_saturn.py links
 * this only when it is given no --game-src).
 *
 * Nearest existing: sat_hooks.h. */
#include "sat.h"

const SatGameHooks sat_game_table = {0};
