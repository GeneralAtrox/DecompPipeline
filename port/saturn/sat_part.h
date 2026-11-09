/* sat_part.c: a model part's versions (game.json part_choice): for every copy (--part-choice), or one robot's (sat_heads.c). */
#ifndef SAT_PART_H
#define SAT_PART_H

#include "console.h"

void sat_part_option(const char *value);   /* --part-choice NAME */
void sat_part_start(void);                 /* at start, after the options: the replay's choice or the one asked for */
void sat_part_frame(void);                 /* each vertical blank: puts the chosen part in once the game has loaded its own */
const u8 *sat_part_edit(const char *value); /* a version's edit by its name, NULL for the original or an unknown one */
u32 sat_part_compose(const u8 *edit, u8 *ram, u32 lo, u32 room); /* it put together at guest address lo; its size, 0 if not */

#endif
