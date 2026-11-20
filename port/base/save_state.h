/* Save states ("save anywhere"): the whole machine at one moment, kept apart from the game's own save.
 * The user (2026-11-18): "As emulators suggest, dont rely on this 'Save States'. It should be kept separate to the
 * in-game save." So a state holds the guest machine only: memory, registers, devices and where each running function
 * resumes. The game's own save (a Saturn's backup RAM, a Dreamcast's VMU) is never in one; saving or loading a state
 * never writes it. File > Save state / Load state (8 slots) and F5 / F8 say so once, as a greyed note in the File menu.
 *
 * The file (<data>\states\slotN.state, little-endian):
 *   header   "PGSTATE\0", format version, the header's size, the console's id, the game, the build's version line, the
 *            kit's identity (its device layout and the code it runs), the slot, the vblank and the time it was made
 *   blocks   per device: tag (8 bytes), version, length, data (padded to 4)
 *   trailer  SHA-256 of everything before it
 * A state is read whole and checked whole (hash, header, every block's bounds) before the kit sees any of it, and the
 * kit checks every block it needs before it changes anything, so a bad or foreign state is refused and never half-loads.
 *
 * The kit registers a provider (save_state_provider) and calls save_state_poll at its safe point: a moment when the
 * guest machine can be written down whole (Saturn: a call's entry on the master, not in an interrupt, every open frame's
 * return address known). The menu and the keys only ask (save_state_pending); the request is done there, on the game's
 * thread. A load returns 1 from save_state_poll: the kit then leaves everything it was running and resumes the state.
 * A kit that finds no safe point for a long while drops the request (save_state_drop) and says why. Not offered in a
 * replay, a check (the fixed clock) or an online match, nor with Game > Save states off (the option "save-states": a
 * live option, on by default; off, nothing is ever asked and the game runs as the original). A console that registers
 * no provider shows the entries greyed. --state-save VB:SLOT and --state-load VB:SLOT (a test aid) ask at that vertical
 * blank in any run, the fixed clock's too, so a check can prove a round trip. */
#ifndef PORT_SAVE_STATE_H
#define PORT_SAVE_STATE_H
#include "base.h"
#include "console.h"
#ifdef __cplusplus
extern "C" {
#endif

#define SAVE_STATE_SLOTS 8
#define SAVE_STATE_FORMAT 1u
#define SAVE_STATE_NOTE "Save states are separate from the game's own save. Keep saving in the game too."

typedef struct SaveStateOut SaveStateOut; /* a state being written */
typedef struct SaveStateIn SaveStateIn;   /* a state read and checked whole */

typedef struct SaveStateProvider {
    const char *(*identity)(void);           /* the device layout and the code: a state made by another is refused */
    int (*save)(SaveStateOut *out);          /* writes its blocks (save_state_block); 0: cannot now (the log says why) */
    int (*load)(const SaveStateIn *in);      /* checks every block it needs, then applies them; 0: refused, unchanged */
} SaveStateProvider;
void save_state_provider(const SaveStateProvider *p);

/* Writing: a block whole, or in parts (begin, put..., end). tag: up to 8 characters. 0 on running out of memory. */
int save_state_block(SaveStateOut *o, const char *tag, u32 version, const void *data, u32 len);
int save_state_begin(SaveStateOut *o, const char *tag, u32 version);
int save_state_put(SaveStateOut *o, const void *data, u32 len);
int save_state_end(SaveStateOut *o);
/* Reading: the block with this tag, or NULL if there is none or its version differs (*len: its length). */
const u8 *save_state_find(const SaveStateIn *in, const char *tag, u32 version, u32 *len);

/* The slots, the menu and the keys (save_state_slots.c): done at the kit's next save_state_poll. slot 0..7. */
enum { SAVE_STATE_SAVE = 1, SAVE_STATE_LOAD = 2 };
int save_state_offered(void);              /* a provider, a play run (not a replay, check or online match) */
void save_state_request(int kind, int slot);
int save_state_slot(void);                 /* the slot last saved or loaded (0-based): F5 / F8's, a game's pause menu's */
void save_state_key(int load);             /* F5 (0) / F8 (1): the slot last used; nothing if the player's layout has the key */
extern volatile long save_state_pending;   /* kind << 8 | slot of the request waiting for the safe point, 0: none */
int save_state_poll(void);                 /* the kit's safe point: does a pending request; 1: a state was loaded */
void save_state_drop(const char *why);     /* the kit found no safe point: the request is dropped, the log says why */
int save_state_test_option(const char *arg, const char *value); /* --state-save / --state-load VB:SLOT; the arguments taken */
void save_state_tick(u32 vblank);          /* each vertical blank: a test's request comes due */
extern const PortOptions port_state_options; /* Game > Save states (game_options.c lists it) */
const PortMenuItem *save_state_menu(unsigned *count); /* File's entries: Save state, Load state, the note */
void save_state_labels(void);              /* each slot's label: its time, or "empty" (before the menu opens) */

/* The file's bytes, made and checked (save_state.c; also what a test drives). */
int save_state_encode(int slot, u8 **data, u32 *size);            /* 1: made (free it), 0: the provider declined */
SaveStateIn *save_state_decode(const u8 *data, u32 size, const char **why); /* NULL: refused, *why says why */
void save_state_free(SaveStateIn *in);
int save_state_has_provider(void);
int save_state_apply(const SaveStateIn *in);          /* the provider's load: 1 applied, 0 refused (unchanged) */
u32 save_state_vblank(const SaveStateIn *in);         /* the vblank it was made at */

#ifdef __cplusplus
}
#endif
#endif
