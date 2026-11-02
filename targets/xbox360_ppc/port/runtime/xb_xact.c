/* The kit's XACT engine. The game reaches sound through one direct call (8209dc6c to 821e5990, XACT3CreateEngine) and then only through the COM methods of the objects it
 * gets back, so like the Direct3D library the XDK's XACT and the XAudio2 under it are replaced at that call, and the wave banks' sound goes to the kit's mixer (xb_sound.c). Each
 * method is an address the game can call through a guest vtable (port/boundary.json makes the range a host call) and is written when a run stops on it. */
#include <windows.h>
#include "xb.h"

/* A method's address: the class in bits 8..11, the slot below. */
enum { XACT_ENGINE, XACT_SOUNDBANK, XACT_WAVEBANK, XACT_CUE, XAUDIO2, XAUDIO2_VOICE };
#define METHOD(cls, slot) (XB_XACT_BASE + (((u32)(cls) << 8 | (u32)(slot)) << 2))

static u32 new_object(unsigned cls, unsigned slots, u32 size)
{
    static u32 vt[6];
    u32 o = xb_alloc(size);
    if (!vt[cls]) {
        unsigned i;
        vt[cls] = xb_alloc(slots * 4);
        for (i = 0; i < slots; i++) wr32(vt[cls] + 4 * i, METHOD(cls, i));
    }
    wr32(o, vt[cls]);
    return o;
}

/* XACT3CreateEngine(flags, IXACT3Engine **) */
int xb_xact_create(const TcArgs *a, u64 *r3)
{
    wr32((u32)a->r[1], new_object(XACT_ENGINE, 21, 0x40));
    *r3 = 0;
    return 1;
}

/* XAudio2Create(IXAudio2 **, flags, processor): the UI library's own sounds (82240670) */
int xb_xaudio2_create(const TcArgs *a, u64 *r3)
{
    wr32((u32)a->r[0], new_object(XAUDIO2, 32, 0x40));
    *r3 = 0;
    return 1;
}

/* An object's slot in xb_sound.c is the word after its vtable */
#define SLOT(this_) ((int)rd32((u32)(this_) + 4))
static u32 slot_object(unsigned cls, unsigned slots, int slot)
{
    u32 o = new_object(cls, slots, 0x40);
    wr32(o + 4, (u32)slot);
    return o;
}

int xb_xact_call(u32 target, const TcArgs *a, u64 *r3)
{
    u32 m = (target - XB_XACT_BASE) >> 2, cls = m >> 8, slot = m & 255;
    switch (cls << 8 | slot) {
    case XACT_ENGINE << 8 | 3:   /* IXACT3Engine::Initialize(this, XACT_RUNTIME_PARAMETERS *): first stop 8209dbd8. The block holds the global settings the game read (rm10.xgs) and
                                  * its notification callback, 8209c490 */
        port_log("xact Initialize: settings %08x size %x, callback %08x\n", rd32((u32)a->r[1] + 4), rd32((u32)a->r[1] + 8), rd32((u32)a->r[1] + 28));
        xb_sound_settings(rd32((u32)a->r[1] + 4), rd32((u32)a->r[1] + 28));
        *r3 = 0;
        return 1;
    case XAUDIO2 << 8 | 10:      /* IXAudio2::CreateMasteringVoice(this, IXAudio2MasteringVoice **, ...): first stop 82240670, right after XAudio2Create */
        wr32((u32)a->r[1], new_object(XAUDIO2_VOICE, 32, 0x40));
        *r3 = 0;
        return 1;
    case XACT_ENGINE << 8 | 7: { /* CreateInMemoryWaveBank(this, data, size, flags, 0, IXACT3WaveBank **): first stop 8209cde8, SE.xwb read whole, the out pointer 8765122c */
        u32 obj;
        int i = xb_sound_wave_bank((u32)a->r[1], (u32)a->r[2], &obj);
        if (!obj) {
            obj = slot_object(XACT_WAVEBANK, 8, i);
            xb_sound_wave_bank_object(i, obj);
        }
        port_log("xact wave bank %d at %08x, %x bytes, frame %u\n", i, (u32)a->r[1], (u32)a->r[2], xb_frame_number());
        wr32((u32)a->r[5], obj);
        *r3 = 0;
        return 1;
    }
    case XACT_ENGINE << 8 | 6: { /* CreateSoundBank(this, data, size, flags, 0, IXACT3SoundBank **): 8209cde8 right after, the out pointer 87651230 */
        u32 obj;
        int i = xb_sound_sound_bank((u32)a->r[1], &obj);
        if (!obj) {
            obj = slot_object(XACT_SOUNDBANK, 8, i);
            xb_sound_sound_bank_object(i, obj);
        }
        port_log("xact sound bank %d at %08x, %x bytes\n", i, (u32)a->r[1], (u32)a->r[2]);
        wr32((u32)a->r[5], obj);
        *r3 = 0;
        return 1;
    }
    case XACT_ENGINE << 8 | 14:  /* GetCategory(this, name): 8209cde8 loops over the game's 207 names of categories, and hands each answer to SetVolume */
        *r3 = xb_sound_category((u32)a->r[1]);
        return 1;
    case XACT_ENGINE << 8 | 16:  /* SetVolume(this, category, f1 volume): 8209cde8, each category */
        xb_sound_category_volume((u32)a->r[1], (float)a->f[0]);
        *r3 = 0;
        return 1;
    case XACT_ENGINE << 8 | 12:  /* RegisterNotification(this, description): first stop 8209cb30 on the thread 8209db88, type 3 (a cue stopped) in the game's 26-byte record */
        xb_sound_notify((u32)a->r[1], 1);
        *r3 = 0;
        return 1;
    case XACT_ENGINE << 8 | 13:  /* UnRegisterNotification(this, description): first stop 8209c978 under 8209d5c8, the sound thread dropping a finished cue */
        xb_sound_notify((u32)a->r[1], 0);
        *r3 = 0;
        return 1;
    case XACT_SOUNDBANK << 8 | 4: { /* Play(this, cue index, flags, time offset, IXACT3Cue **): first stop 8209cb30 on the sound thread, cue 0x33, flags 8 */
        u32 out = (u32)a->r[4];
        int i = xb_sound_play(SLOT(a->r[0]), (u32)a->r[1], out != 0);
        port_log("xact play cue %u flags %x: slot %d, frame %u\n", (u32)a->r[1], (u32)a->r[2], i, xb_frame_number());
        if (i >= 0 && !xb_sound_cue_object(i)) xb_sound_cue_set_object(i, slot_object(XACT_CUE, 12, i));
        if (out) wr32(out, i >= 0 ? xb_sound_cue_object(i) : 0);
        *r3 = i >= 0 ? 0 : XB_STATUS(0x8AC70008u);   /* a failing HRESULT: the wave bank is not there */
        return 1;
    }
    case XACT_CUE << 8 | 2:      /* GetState(this, DWORD *): first stop 8209d2b0 under 8209e168, the game's "is this sound playing": bits 2 (preparing) or 8 (playing) mean yes */
        wr32((u32)a->r[1], xb_sound_cue_state(SLOT(a->r[0])));
        *r3 = 0;
        return 1;
    case XACT_CUE << 8 | 3:      /* Destroy(this): first stop 8209c978 under 8209d5c8 */
        xb_sound_cue_destroy(SLOT(a->r[0]));
        *r3 = 0;
        return 1;
    case XACT_WAVEBANK << 8 | 7: /* GetState(this, DWORD *): first stop 8209d740 on the sound thread, it waits for bit 4 (prepared) on the music bank. An in-memory bank is ready at once */
        wr32((u32)a->r[1], 4);
        *r3 = 0;
        return 1;
    case XACT_SOUNDBANK << 8 | 6: /* Destroy(this): first stop 8209d5c8, the sound thread unloading a music bank */
        xb_sound_sound_bank_destroy(SLOT(a->r[0]));
        *r3 = 0;
        return 1;
    case XACT_WAVEBANK << 8 | 0:  /* Destroy(this): the same function */
        xb_sound_wave_bank_destroy(SLOT(a->r[0]));
        *r3 = 0;
        return 1;
    case XACT_ENGINE << 8 | 4:   /* ShutDown(this): first stop 8209d050, the game on its way out */
        xb_sound_stop_all();
        *r3 = 0;
        return 1;
    case XACT_ENGINE << 8 | 5:   /* IXACT3Engine::DoWork(this): first stop 8209daa8, the game's sound thread 8209db88, over and over */
        xb_sound_do_work(a);
        *r3 = 0;
        return 1;
    }
    port_log("xact method not written: class %u slot %u\n", cls, slot);
    return 0;
}
