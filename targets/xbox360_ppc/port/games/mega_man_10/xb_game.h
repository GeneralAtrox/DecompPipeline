/* What the kit knows of this game by address. Each game has its own folder, chosen by gen_xbox.py --game. */
#ifndef XB_GAME_H
#define XB_GAME_H
#define XB_GAME_NAME "Mega Man 10"
#define XB_GAME_NET 1            /* xb_net*.c are written against this game's session code and memory */
#define XB_RS_ROWS 0x824B0900u   /* the library's render state rows: xb_state.c */
#define XB_SS_ROWS 0x824B0DC0u   /* and its sampler state rows */
/* The case labels of xb_d3d_res.h and xb_d3d_draw.h are this game's addresses, so its calls need no table */
#define XB_LIB_CALLS_ARE_LABELS 1
#define XB_SECTIONS {0, 0, 0}   /* it asks for none */
#define XB_TLS_RAW 0u            /* it reads none through r13 */
#define XB_TLS_SIZE 0u
#endif
