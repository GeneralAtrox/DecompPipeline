/* sat_part.c: a model part the player picks (game.json part_choice; Display menu). */
#ifndef SAT_PART_H
#define SAT_PART_H

#include "console.h"

void sat_part_option(const char *value);   /* --part-choice NAME (the settings' part-choice) */
void sat_part_start(void);                 /* at start, after the options: the replay's choice or the one asked for */
void sat_part_frame(void);                 /* each vertical blank: puts the chosen part in once the game has loaded its own */
const PortMenuItem *sat_part_menu(void);   /* the menu entry, NULL when the game offers no part */

#endif
