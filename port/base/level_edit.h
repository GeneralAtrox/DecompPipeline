/* level_edit.cpp: the port base's level editor, laid out and driven after Unreal Engine's level editor (its conventions
 * only: no code or art of it). A console that can edit a level registers a provider; Game > Level editor opens it on
 * the game's thread, with the game paused, and draws it on the base's window until it is closed or Play is pressed.
 *
 * What the editor does is generic: a 3D viewport of the level's meshes (its own small Direct3D 11 pipeline, textures
 * packed into one atlas), the Outliner (every entity, by kind) and Details (type, exact position), selecting (click,
 * Ctrl+click, Esc), moving with a translate gizmo (X/Y/Z arrows and the three planes, grid snap), typed positions, undo
 * and redo, save and Play. What a level holds and how it is saved is the console's: it hands over a LevelScene and is
 * called back to save it or to start a game in it.
 *
 * Positions are the game's own numbers (exact integers in its units); the editor only shows them as floats. An entity
 * that is not moved keeps its numbers, so saving an untouched level writes the original values back.
 *
 * A console may let the editor place entities (LevelScene.can_place: the toolbar's Add blocker): copies of its
 * template, numbered, placed where the view meets the floor; undo takes a placed one away.
 *
 * Options (tests): --level-edit-at VB opens the editor at the first window pump at or after vertical blank VB;
 * --level-edit-select NAME selects that entity (at its 3rd frame); --level-edit-place 1 places one entity (Add
 * blocker) at its 2nd frame; --level-edit-shot FILE writes the editor's picture (BMP) after its 20th frame and
 * closes it; --level-edit-play N presses Play at its Nth frame. The game's start-again drops them all. */
#ifndef LEVEL_EDIT_H
#define LEVEL_EDIT_H

#include <stddef.h>
#include "base.h"

#ifdef __cplusplus
extern "C" {
#endif

enum LevelKind { LEVEL_SPAWN, LEVEL_MESH, LEVEL_OBJECT, LEVEL_BLOCKER, LEVEL_LIGHT, LEVEL_KINDS };

/* a collision or volume shape, drawn as a wire: a cylinder (radius, from lo[1] to hi[1] around the drawn position), or an
 * octagon (a square of half-size radius with its corners cut where |x| + |z| > radius * sqrt 2, lo[1] to hi[1]) */
enum LevelShape { LEVEL_SHAPE_NONE, LEVEL_SHAPE_BOX, LEVEL_SHAPE_CYLINDER, LEVEL_SHAPE_OCTAGON };

typedef struct LevelVert {
    f32 x, y, z;  /* the mesh's own space, in game units (the game's axes) */
    f32 u, v;     /* 0..1 across the texture */
    u32 rgba;     /* 0xAABBGGRR: the polygon's colour (white under a texture) */
    s32 tex;      /* the texture's index, -1 none */
} LevelVert;

typedef struct LevelMesh {
    const LevelVert *v; /* a triangle list */
    u32 nv;
} LevelMesh;

typedef struct LevelTex {
    u32 w, h;
    const u32 *rgba; /* w*h, 0xAABBGGRR; alpha 0 is a see-through texel */
} LevelTex;

typedef struct LevelEnt {
    char id[32];     /* the level file's id (the console's own; the editor numbers placed ones) */
    char name[64];   /* the Outliner's line */
    char type[64];   /* the Details panel's type: the game's own word for it */
    u32 kind;        /* LevelKind: the Outliner's folder and how the viewport draws it */
    u32 axes;        /* the axes it may move along: bit 0 X, 1 Y, 2 Z (0: shown, not movable) */
    s32 pos[3];      /* the game's own position, exact */
    s32 orig[3];     /* the game's original position (Details offers to put it back) */
    s32 lo[3], hi[3];/* its box around the drawn position, game units: a mesh's bounds, a marker's size, a volume */
    s32 add[3];      /* added to pos where it is drawn: the game puts it there (an object stands on the floor) */
    s32 quantum;     /* it moves in whole multiples of this, game units (0 or 1: any) */
    s32 mesh;        /* the mesh drawn at pos + add, -1 none */
    s32 collides;    /* its collision (shape) moves with it */
    u32 shape;       /* LevelShape: its collision or volume */
    s32 radius;      /* the shape's size, game units */
    f32 dir[3];      /* LEVEL_LIGHT: the direction the light shines, game axes (the editor keeps it up to date) */
    char info[400];  /* more lines for Details ('\n' between) */
} LevelEnt;

typedef struct LevelScene {
    char title[64];  /* the level's name (title bar, Outliner's root) */
    f32 unit;        /* game units in one editor unit (an SGL FIXED: 65536) */
    s32 y_down;      /* the game's +Y points down (SGL): the editor shows it flipped, up as up */
    s32 grid[4];     /* the snap steps offered, game units */
    s32 snap;        /* the step offered first (an index into grid) */
    s32 light_target[3]; /* a LEVEL_LIGHT is a handle aimed at this point: its direction is target - handle */
    LevelEnt *ent;
    u32 nent;
    const LevelMesh *mesh;
    u32 nmesh;
    const LevelTex *tex;
    u32 ntex;
    char saved_to[260]; /* where the console saved it last (shown in the status bar) */
    u32 cap;            /* room in ent[]: Add blocker places entities up to cap */
    s32 can_place;      /* the level takes placed entities (the toolbar's Add blocker) */
    LevelEnt place_tmpl; /* what Add blocker places: its id and name are prefixes, numbered from 01 ("blocker-01") */
} LevelScene;

typedef struct LevelEdProvider {
    const char *label;                                  /* the level's name for the log, e.g. "Studium" */
    int (*open)(LevelScene *scene);                     /* reads the level; 0 if it cannot now (logged) */
    int (*save)(LevelScene *scene, char *msg, size_t cap); /* writes it; 0 on failure, msg says what happened */
    void (*play)(LevelScene *scene);                    /* turns the edited level on and starts a game in it */
    void (*close)(LevelScene *scene);                   /* frees what open made */
} LevelEdProvider;

void level_edit_provide(const LevelEdProvider *p); /* a console's, at start-up; none: the menu entry is greyed */
const LevelEdProvider *level_edit_provider(void);
void level_edit_request(void);  /* the menu: open the editor at the game thread's next window pump */
int level_edit_wanted(void);
int level_edit_option(const char *arg, const char *value); /* --level-edit-*: the arguments taken, or 0 */
/* host_window.cpp's pump, on the game's thread with the device locked: runs the editor until it is closed. The host's
 * own handler gets the window's events the editor does not take (closing, resizing, full screen). */
void level_edit_run(void *sdl_window, void (*host_event)(const void *sdl_event));

#ifdef __cplusplus
}
#endif

#endif
