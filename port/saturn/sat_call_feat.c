/* The feature hooks sat_call.c's call runs around each game function, in the order they had there. Before the function:
 * the base's twin-stick fight and the save's auto-load step, the wide view's culling, a team fight (sat_team.c), an
 * edited arena (sat_level.c) and new head types (sat_heads.c), each of the last three able to hand the function other
 * arguments. After it, each able to change its result: the edited arena, the team fight, the new head types, the kit's
 * in-game menus (sat_menus.c) and the auto-load step that waits for a return. The menus' entry stays in sat_call.c: it can answer a call without running the function.
 *
 * Nearest existing: sat_call.c - the call these run around; it reached its 400-line limit, so its feature hooks are here. */
#include "sat.h"

const TcArgs *sat_feat_enter(u32 fn, const TcArgs *a, SatFeat *t)
{
    if (fn == port_twin_fn) port_twin_hit(); /* the game's fight (base pad_twin.c) */
    sat_wide_cull_at(fn); /* its culling, wide (sat_wide.c) */
    if (fn == autoload_fn && !autoload_leave) autoload_hit(); /* a step of the save's auto-load (base autoload.c) */
    if (sat_team_on) a = sat_team_enter(fn, a, &t->team); /* a team fight (sat_team.c) */
    if (sat_level_on) a = sat_level_enter(fn, a, &t->lvl); /* an edited arena (sat_level.c) */
    if (sat_heads_on && sat_heads_mark[(fn >> 1) & 255u]) a = sat_heads_enter(fn, a, &t->head); /* new head types (sat_heads.c) */
    return a;
}

u32 sat_feat_leave(u32 fn, const TcArgs *a, u32 r)
{
    if (sat_level_on) r = sat_level_leave(fn, a, r);
    if (sat_team_on) r = sat_team_leave(fn, a, r);
    if (sat_heads_on && sat_heads_mark[(fn >> 1) & 255u]) r = sat_heads_leave(fn, a, r);
    if (sat_menus_on) r = sat_menus_leave(fn, a, r);
    if (fn == autoload_fn && autoload_leave) autoload_hit();
    return r;
}
