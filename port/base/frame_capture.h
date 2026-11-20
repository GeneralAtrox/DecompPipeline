/* frame_capture.c: the frame after a scene dump, recorded draw by draw for an agent (Copy agent prompt: scene_dump.c arms it).
 *
 * Console-free. The console kit supplies the records: one JSON object a line (frame.jsonl) for each draw, skipped draw (with its reason),
 * clear, resolve, target switch and present, in the order they happen, and PNG pictures of render targets where it chooses (at each
 * resolve). The base numbers the records, keeps them in memory while the frame runs and writes them at the frame's end with the final
 * picture (console_capture_bmp) and summary.json; port/tools/frame_capture_view.py prints what drew, what was skipped and why.
 * Off (the normal case) a record costs the test of g_fcap_on. The folder is <dump>_frame beside the dump's .json, which names it.
 * Only a console with PORT_CONSOLE_FCAP in port_console.flags has it (the Xbox 360 kit): for any other, fcap_arm does nothing, so
 * nothing is allocated or written and scene_dump_poll pays one test of g_fcap_on a frame.
 * The final picture is the console's own capture (console_capture_bmp, as the dump's .bmp); the PNG writer is the stored-block
 * kind the Saturn kit's sat_shot.c writes, kept here because the base cannot call a kit's.
 *
 * Nearest existing: the console kits' frame dumps (--dump-frames, XB_DUMP, XB_DUMP_STAGES) - pictures only, every N frames, no draw records. */
#ifndef FRAME_CAPTURE_H
#define FRAME_CAPTURE_H

#include "base.h"

extern int g_fcap_on;   /* 1 while a frame is recorded */

void fcap_arm(const char *dir);   /* scene_dump.c: record the frame after this one into dir (made by the caller) */
void fcap_frame_end(u32 frame);   /* scene_dump_poll, once a frame: ends a recorded frame and writes it */
/* One record: fmt gives the object's members without braces ("\"k\":\"draw\",\"prim\":%u"); the base adds the sequence number "n" */
void fcap_rec(const char *fmt, ...);
/* A picture for the record that follows, w x h RGBA bytes rows pitch bytes apart, written now as dir\name.png. Returns 1 if written */
int fcap_png(const char *name, unsigned w, unsigned h, const u8 *rgba, unsigned pitch);
/* The number the next record gets (a kit choosing draws by number, as frame_capture_view.py prints them) */
unsigned fcap_next(void);
/* n raw bytes into dir\name now (a kit's own data for a chosen record: a draw's vertices and constants). Returns 1 if written; at most 200 a frame */
int fcap_blob(const char *name, const void *data, size_t n);
/* A PNG of w x h RGBA rows (stored, not compressed) into path; 1 if written. For tests and any kit */
int fcap_write_png(const char *path, unsigned w, unsigned h, const u8 *rgba, unsigned pitch);

#endif
