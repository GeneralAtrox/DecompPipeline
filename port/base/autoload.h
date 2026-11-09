/* autoload.c: a game's own load path driven at boot when its save is there (Game > Auto-load save, --auto-load on|off). */
#ifndef AUTOLOAD_H
#define AUTOLOAD_H

#include "base.h"

#ifdef __cplusplus
extern "C" {
#endif

/* One step: it ends when the game enters the guest function fn (or, with leave, returns from it); until then it presses
 * press (a button name, as --input takes it; NULL: none) every few blanks; when it ends, a 16-bit word value is put at
 * guest address poke (0: none) before fn runs. With call (0: none), the game's own function call then runs once with
 * arg as its first argument (the game's load, called the way its menu calls it, so no button is pressed); the poke is
 * then only for that call (a wait the call would make, set to 0) and the word's old value is put back after it. */
typedef struct AutoLoadStep {
    u32 fn;
    int leave;
    const char *press;
    u32 poke, value;
    u32 call, arg;
} AutoLoadStep;

extern u32 autoload_fn;  /* the function the step waits on (0: none): the console calls autoload_hit when it is entered */
extern int autoload_leave; /* ... or, when set, when it returns */

int autoload_option(const char *arg, const char *value); /* --auto-load on|off; 0 if not ours */
/* The console's side: has_save says the game's file is there; poke16 / peek16 write and read a guest word; call runs a
 * guest function with one argument and gives its result. */
typedef struct AutoLoadConsole {
    int (*has_save)(void);
    void (*poke16)(u32 addr, u32 value);
    u32 (*peek16)(u32 addr);
    u32 (*call)(u32 fn, u32 arg);
} AutoLoadConsole;
/* At the start, after the save's own notes: the replay's choice, else the option's (on by default in an unscripted play
 * run) when the game's file is there. */
void autoload_start(const AutoLoadStep *steps, unsigned n, int play_run, const AutoLoadConsole *con);
void autoload_hit(void);
/* more steps, driven the same way once the load's steps are done (Play of an edited level: the menu's screens to a fight),
 * and lead (a button, as a step's press; NULL: none) pressed while a step of the load waits with no press of its own (the
 * title before the menu the load waits for). None of it runs when the auto-load is off. steps and lead outlive the run. */
void autoload_then(const char *lead, const AutoLoadStep *steps, unsigned n);
/* autoload_buttons (host.h, for input.c): player 1's presses now; *own 1 while a step holds the pad */
int autoload_available(void);   /* the game has a load path to drive (the menu entry) */
int autoload_get(void);         /* the setting, for the menu */
void autoload_set(int on);      /* from the next start */

#ifdef __cplusplus
}
#endif

#endif
