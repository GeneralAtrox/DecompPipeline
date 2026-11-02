/* The kit's sound: the game's wave banks and sound banks read from guest memory, cues that last as long as their waves. A cue's position moves
 * with the tick and nothing else, so what the game reads back (a cue still playing, a cue that stopped) is the same on a fixed clock as on the
 * real one. A wave in XMA is decoded as it plays by a separate library, xb_xma_dll.c. The output is xb_sound_out.c.
 *
 * Nearest existing: port/saturn/sat_scsp.c (a console's sound voices), which mixes its chip's registers rather than a sound engine's cues. */
#include <windows.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "xb.h"
#include "xb_xma_dll.h"

enum { RATE = XB_SOUND_RATE, MAX_BANKS = 8, MAX_CUES = 64, MAX_NOTES = 32, MAX_CATEGORIES = 16 };
enum { TAG_PCM = 0, TAG_XMA = 1, INFINITE_LOOP = 0xFF, NOTE_CUE_STOP = 3 };

typedef struct { u32 obj, data, count, meta, entry, waves; int live; char name[64]; } WaveBank;
typedef struct { u32 obj, data, count, cues, names, sounds; int live; } SoundBank;
typedef struct {
    u32 obj, state, index, data, frames, loop_start, loop_len, loops, rate;
    int live, owned, tag, category, sound_bank, wave_bank;
    double pos;
    float gain;
} Cue;
typedef struct { u32 type, sound_bank; u16 cue; int live; } Note;

static CRITICAL_SECTION s_lock;
static WaveBank s_wave[MAX_BANKS];
static SoundBank s_bank[MAX_BANKS];
static Cue s_cue[MAX_CUES];
static void *s_stream[MAX_CUES];
static Note s_note[MAX_NOTES];
static struct { u32 cue, sound_bank; u16 index; } s_fire[MAX_CUES];
static unsigned s_fires;
static char s_cat_name[MAX_CATEGORIES][32];
static float s_cat_vol[MAX_CATEGORIES];
static unsigned s_cats;
static u32 s_callback, s_note_block;
static u32 s_xma_cues, s_pcm_cues;

static void locked(int on) { (on ? EnterCriticalSection : LeaveCriticalSection)(&s_lock); }

/* The byte the XACT tool stores for a volume, as a ratio: 180 is 0 dB */
static float volume_of(u8 v)
{
    double db = (-96.0 - 67.7385212334047) / (1.0 + pow(v / 80.1748600297963, 0.432254984608615)) + 67.7385212334047;
    return (float)pow(10.0, db / 20.0);
}

/* The global settings file (rm10.xgs): the category names, in the order a sound's category number counts them */
void xb_sound_settings(u32 xgs, u32 callback)
{
    u32 p;
    unsigned i, n;
    InitializeCriticalSection(&s_lock);
    s_callback = callback;
    s_note_block = xb_alloc(64);
    n = (u32)rd8(xgs + 0x13) << 8 | rd8(xgs + 0x14);
    p = rd32(xgs + 0x39);
    if (n > MAX_CATEGORIES) xb_stop("xact", "more categories than the kit holds", n);
    for (i = 0; i < n; i++) {
        unsigned k = 0;
        for (; rd8(xgs + p); p++)
            if (k < 31) s_cat_name[i][k++] = (char)rd8(xgs + p);
        s_cat_name[i][k] = 0;
        p++;
        s_cat_vol[i] = 1.0f;
        port_log("xact category %u %s\n", i, s_cat_name[i]);
    }
    s_cats = n;
}

u32 xb_sound_category(u32 name)
{
    char s[32];
    unsigned i;
    for (i = 0; i < 31 && rd8(name + i); i++) s[i] = (char)rd8(name + i);
    s[i] = 0;
    for (i = 0; i < s_cats; i++)
        if (!strcmp(s_cat_name[i], s)) return i;
    port_log("xact no category named %s\n", s);
    return 0xFFFFu;   /* XACTCATEGORY_INVALID */
}

void xb_sound_category_volume(u32 category, float volume)
{
    if (category < s_cats) s_cat_vol[category] = volume;
}

/* A wave bank in memory, big-endian "WBND": the segments from 12, the bank's data block, its name at +8, the entry size at +72 */
int xb_sound_wave_bank(u32 data, u32 size, u32 *obj)
{
    int i;
    u32 head;
    (void)size;
    locked(1);
    for (i = 0; i < MAX_BANKS && s_wave[i].live; i++) {}
    if (i == MAX_BANKS) xb_stop("xact", "more wave banks than the kit holds", i);
    head = rd32(data + 12);
    s_wave[i].data = data;
    s_wave[i].count = rd32(data + head + 4);
    s_wave[i].entry = rd32(data + head + 72);
    s_wave[i].meta = data + rd32(data + 20);
    s_wave[i].waves = data + rd32(data + 44);
    for (u32 k = 0; k < 63; k++) s_wave[i].name[k] = (char)rd8(data + head + 8 + k);
    s_wave[i].live = 1;
    *obj = s_wave[i].obj;   /* 0 the first time: the caller makes the object */
    locked(0);
    return i;
}

void xb_sound_wave_bank_object(int i, u32 obj) { s_wave[i].obj = obj; }

/* A sound bank, big-endian "SDBK": the count of simple cues at 0x13, the cue table at 0x22, the wave bank names at 0x3A */
int xb_sound_sound_bank(u32 data, u32 *obj)
{
    int i;
    locked(1);
    for (i = 0; i < MAX_BANKS && s_bank[i].live; i++) {}
    if (i == MAX_BANKS) xb_stop("xact", "more sound banks than the kit holds", i);
    if (rd8(data + 0x15) || rd8(data + 0x16)) xb_stop("xact", "a sound bank with complex cues", data);
    s_bank[i].data = data;
    s_bank[i].count = (u32)rd8(data + 0x13) << 8 | rd8(data + 0x14);
    s_bank[i].cues = data + rd32(data + 0x22);
    s_bank[i].names = data + rd32(data + 0x3A);
    s_bank[i].live = 1;
    *obj = s_bank[i].obj;   /* 0 the first time: the caller makes the object */
    locked(0);
    return i;
}

void xb_sound_sound_bank_object(int i, u32 obj) { s_bank[i].obj = obj; }

static int wave_bank_named(u32 name)
{
    for (int i = 0; i < MAX_BANKS; i++) {
        u32 k = 0;
        if (!s_wave[i].live) continue;
        while (k < 63 && s_wave[i].name[k] && (u8)s_wave[i].name[k] == rd8(name + k)) k++;
        if (!s_wave[i].name[k] && !rd8(name + k)) return i;
    }
    return -1;
}

static void fire_stop(Cue *c)
{
    const SoundBank *b = &s_bank[c->sound_bank];
    for (unsigned i = 0; i < MAX_NOTES; i++) {
        Note *n = &s_note[i];
        if (!n->live || n->type != NOTE_CUE_STOP || n->sound_bank != b->obj || n->cue != c->index) continue;
        n->live = 0;   /* no XACT_FLAG_NOTIFICATION_PERSIST: one firing */
        if (s_fires < MAX_CUES) {
            s_fire[s_fires].cue = c->obj;
            s_fire[s_fires].sound_bank = b->obj;
            s_fire[s_fires++].index = (u16)c->index;
        }
    }
}

static void stop_cue(Cue *c)
{
    if (c->state != XB_CUE_STOPPED) {
        c->state = XB_CUE_STOPPED;
        fire_stop(c);
    }
    if (!c->owned) c->live = 0;
}

/* SoundBank::Play. Every sound the game ships is one wave (flag 0) or one clip with one play event (flag 1); anything else stops the run.
 * Returns the cue's slot, or -1 with nothing to play */
int xb_sound_play(int sound_bank, u32 index, int owned)
{
    const SoundBank *b = &s_bank[sound_bank];
    u32 sound, p, m, fmt;
    u32 wave, bank, loops = 0;
    u8 flags;
    int i, wb;
    Cue *c;
    float gain;
    if (index >= b->count) xb_stop("xact", "a cue past the bank's end", index);
    sound = b->data + rd32(b->cues + 5 * index + 1);
    flags = rd8(sound);
    gain = volume_of(rd8(sound + 3));
    p = sound + 9;
    if (flags == 1) {
        u32 clip;
        if (rd8(p) != 1) xb_stop("xact", "a sound of several clips", sound);
        gain *= volume_of(rd8(p + 1));
        clip = b->data + rd32(p + 2);
        if (rd8(clip) != 1 || (rd8(clip + 4) & 31) != 1) xb_stop("xact", "a clip that is not one play event", clip);
        loops = rd8(clip + 7);
        wave = (u32)rd8(clip + 9) << 8 | rd8(clip + 10);
        bank = rd8(clip + 11);
    } else if (flags == 0) {
        wave = (u32)rd8(p) << 8 | rd8(p + 1);
        bank = rd8(p + 2);
    } else {
        xb_stop("xact", "a sound of an unknown form", flags);
        return -1;
    }
    locked(1);
    wb = wave_bank_named(b->names + 64 * bank);
    if (wb < 0 || wave >= s_wave[wb].count) {
        port_log("xact cue %u: wave %u of bank %u is not loaded, nothing plays\n", index, wave, bank);
        locked(0);
        return -1;
    }
    for (i = 0; i < MAX_CUES && s_cue[i].live; i++) {}
    if (i == MAX_CUES) xb_stop("xact", "more cues than the kit holds", i);
    c = &s_cue[i];
    m = s_wave[wb].meta + wave * s_wave[wb].entry;
    fmt = rd32(m + 4);
    c->state = XB_CUE_PLAYING;
    c->index = index;
    c->frames = rd32(m) >> 4;
    c->tag = fmt & 3;
    c->rate = fmt >> 5 & 0x3FFFFu;
    c->data = s_wave[wb].waves + rd32(m + 8);
    c->loop_start = rd32(m + 16);
    c->loop_len = rd32(m + 20);
    c->loops = c->loop_len ? loops : 0;
    if ((fmt >> 2 & 7) != 1 || (c->tag == TAG_PCM && !(fmt >> 31))) xb_stop("xact", "a wave that is not mono 16-bit", fmt);
    if (c->tag != TAG_PCM && c->tag != TAG_XMA) xb_stop("xact", "a wave neither PCM nor XMA", fmt);
    c->tag == TAG_PCM ? s_pcm_cues++ : s_xma_cues++;
    c->category = rd8(sound + 1) << 8 | rd8(sound + 2);
    c->sound_bank = sound_bank;
    c->wave_bank = wb;
    c->gain = gain;
    c->pos = 0.0;
    if (c->tag == TAG_XMA) s_stream[i] = xb_xma_open(s_stream[i], (const u8 *)tc_base + c->data, rd32(m + 12), c->rate);
    c->owned = owned;
    c->live = 1;
    locked(0);
    return i;
}

u32 xb_sound_cue_object(int i) { return s_cue[i].obj; }
void xb_sound_cue_set_object(int i, u32 obj) { s_cue[i].obj = obj; }
u32 xb_sound_cue_state(int i) { return s_cue[i].state; }

void xb_sound_cue_destroy(int i)
{
    locked(1);
    s_cue[i].owned = 0;
    stop_cue(&s_cue[i]);
    locked(0);
}

/* Bank destruction takes down what plays from it: the memory behind it goes back to the game */
static void stop_cues_of(int sound_bank, int wave_bank)
{
    locked(1);
    for (int k = 0; k < MAX_CUES; k++)
        if (s_cue[k].live && (s_cue[k].sound_bank == sound_bank || s_cue[k].wave_bank == wave_bank)) {
            s_cue[k].owned = 0;
            stop_cue(&s_cue[k]);
        }
    locked(0);
}

void xb_sound_sound_bank_destroy(int i)
{
    stop_cues_of(i, -1);
    s_bank[i].live = 0;
}

void xb_sound_wave_bank_destroy(int i)
{
    stop_cues_of(-1, i);
    s_wave[i].live = 0;
}

void xb_sound_notify(u32 desc, int on)
{
    u32 type = rd8(desc), bank = rd32(desc + 2);
    u16 cue = (u16)((u32)rd8(desc + 0x12) << 8 | rd8(desc + 0x13));
    unsigned free_slot = MAX_NOTES;
    locked(1);
    for (unsigned i = 0; i < MAX_NOTES; i++) {
        Note *n = &s_note[i];
        if (n->live && n->type == type && n->sound_bank == bank && n->cue == cue) n->live = 0;
        if (!n->live && free_slot == MAX_NOTES) free_slot = i;
    }
    if (on) {
        if (free_slot == MAX_NOTES) xb_stop("xact", "more notifications than the kit holds", type);
        s_note[free_slot].type = type;
        s_note[free_slot].sound_bank = bank;
        s_note[free_slot].cue = cue;
        s_note[free_slot].live = 1;
    }
    locked(0);
}

/* Engine::DoWork, on the game's sound thread: the callback hears of the cues that stopped there, as on the console.
 * The notification record is packed: type, time at 1, context at 5, then the cue's index at 9, its bank at 11, the cue at 15 */
void xb_sound_do_work(const TcArgs *a)
{
    for (;;) {
        TcArgs n;
        u32 b = s_note_block;
        locked(1);
        if (!s_fires || !s_callback) {
            locked(0);
            return;
        }
        s_fires--;
        wr8(b, NOTE_CUE_STOP);
        wr32(b + 1, 0);
        wr32(b + 5, 0);
        wr16(b + 9, s_fire[s_fires].index);
        wr32(b + 11, s_fire[s_fires].sound_bank);
        wr32(b + 15, s_fire[s_fires].cue);
        locked(0);
        memset(&n, 0, sizeof n);
        n.r[0] = b;
        n.sp[0] = a->sp[0];
        (void)tc_call(s_callback, &n);
    }
}

void xb_sound_stop_all(void)
{
    locked(1);
    for (int k = 0; k < MAX_CUES; k++)
        if (s_cue[k].live) stop_cue(&s_cue[k]);
    locked(0);
}

static void mix_cue(Cue *c, s32 *out, u32 frames)
{
    const double step = (double)c->rate / RATE;
    const float gain = c->gain * (c->category >= 0 && (unsigned)c->category < s_cats ? s_cat_vol[c->category] : 1.0f);
    const u8 *w = (const u8 *)tc_base + c->data;
    for (u32 f = 0; f < frames; f++) {
        u32 at = (u32)c->pos;
        if (c->loops && at >= c->loop_start + c->loop_len) {
            c->pos -= c->loop_len;
            at = (u32)c->pos;
            if (c->loops != INFINITE_LOOP) c->loops--;
        }
        if (at >= c->frames) {
            stop_cue(c);
            return;
        }
        {
            float s0, s1, t = (float)(c->pos - at);
            s32 v;
            if (c->tag == TAG_PCM) {
                s0 = (s16)(w[2 * at] << 8 | w[2 * at + 1]);
                s1 = at + 1 < c->frames ? (s16)(w[2 * at + 2] << 8 | w[2 * at + 3]) : s0;
            } else {
                void *x = s_stream[c - s_cue];
                s0 = xb_xma_at(x, at);
                s1 = at + 1 < c->frames ? xb_xma_at(x, at + 1) : s0;
            }
            v = (s32)((s0 + (s1 - s0) * t) * gain);
            out[2 * f] += v;
            out[2 * f + 1] += v;
        }
        c->pos += step;
    }
}

/* What plays now, added into one tick's stereo samples */
void xb_sound_mix(s32 *mix, u32 frames)
{
    locked(1);
    for (unsigned i = 0; i < MAX_CUES; i++)
        if (s_cue[i].live && s_cue[i].state == XB_CUE_PLAYING) mix_cue(&s_cue[i], mix, frames);
    locked(0);
}

/* The engine has been told its settings: there is something to mix */
int xb_sound_up(void) { return s_cats != 0; }

void xb_sound_counts(u32 *pcm, u32 *xma)
{
    *pcm = s_pcm_cues;
    *xma = s_xma_cues;
}
