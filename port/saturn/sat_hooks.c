/* The switches a Saturn game's hooks (sat_hooks.h) set and the kit tests on its hot paths. They are the kit's, not the
 * game's, so the kit's calls test them without knowing whether a game has hooks.
 *
 * Nearest existing: sat_hooks.h (the table they go with). */
#include "sat.h"

int sat_team_on;        /* a team fight is in force: sat_call_feat.c calls the hooks around every game function while set */
int sat_heads_on;       /* new head types: the same, and only for a function sat_heads_mark marks */
u8 sat_heads_mark[256]; /* the hooked functions, by (address >> 1) & 255 (others may share a mark: they are let through) */
int sat_lobby_match;    /* --net-menu on: a match the game's own screens started (sat_net_setup.c) */
