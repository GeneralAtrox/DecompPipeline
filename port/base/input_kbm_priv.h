/* Between the keyboard and mouse layout's files: input_kbm.c (the layout, the mouse's buttons and the look, the read),
 * input_kbm_inject.c (--kbm-inject), input_kbm_steer.c (mouse steer), input_kbm_pointer.c (the menu pointer) and
 * input_kbm_mouse.c (the mouse given to the game). Base-internal; no SDL, so tests/kbm_test.c builds them against stubs. */
#ifndef PORT_INPUT_KBM_PRIV_H
#define PORT_INPUT_KBM_PRIV_H
#include "base.h"

int kbm_same(const char *a, const char *b); /* two names, case aside */
int kbm_window_ok(void);                    /* the window has the focus and the kit's menu is shut */

int kbm_inject_option(const char *value);        /* --kbm-inject's list */
int kbm_injected_held(const char *name, u32 vb); /* a key or button the injection holds at this blank */
void kbm_inject_mouse(u32 vb);                   /* its mouse entries, as the window's events would come; each read */

int kbm_steer_option(const char *arg, const char *value); /* --kbm-mouse-*; 0 if not ours */
int kbm_steer_active(void);                               /* the game asks, the setting is on, kbm_window_ok */
void kbm_steer_motion(float dx, float dy);                /* the mouse moved with the middle button up */
void kbm_steer_read(int *x, int *y);                      /* each read: the steer, -127..127 each way, 0 when off */

int kbm_pointer_on(void);               /* input_kbm_pointer.c: the game says a menu is up, with entries */
int kbm_pointer_wheel(int notches);     /* 1: the menu took the notches (steps); 0: they are the layout's */
int kbm_pointer_left(int down);         /* each read, the left button: 1 while the layout may have it */
u32 kbm_pointer_read(int keys_pressed); /* each read: the steps and the confirm, as the pad's bits */
void kbm_pointer_forget(void);          /* the focus lost */

int kbm_game_mouse(void);               /* input_kbm_mouse.c: the mouse is the game's (PG_MOUSE_POINTER or LOOK) */
void kbm_game_button(int which, int down); /* then the window's mouse goes here, not to the layout */
void kbm_game_motion(float dx, float dy);
void kbm_game_wheel(int notches);
void kbm_game_position(float x, float y);

#endif
