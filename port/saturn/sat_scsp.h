/* The SCSP's parts that share state: the register file and sound RAM (sat_scsp.c), the 32 voices and their mix
 * (sat_scsp_mix.c), and the wave out (sat_audio.c). */
#ifndef SAT_SCSP_H
#define SAT_SCSP_H
#include "sat.h"

#define SAT_SCSP_RATE 44100u

/* the register file, as the 68K and the SH-2 both see it: big-endian bytes at offsets 0..0xfff */
extern u8 sat_scsp_regs[0x1000];
extern u8 sat_sound_ram_bytes[0x80000];

/* the voices: a write to a slot register (offset < 0x400), the key-on strobe, and one output sample of all voices */
void sat_mix_slot_written(u32 reg);
void sat_mix_keyex(void);
void sat_mix_sample(s32 *left, s32 *right);
void sat_mix_reset(void);
u32 sat_mix_active(void);
u32 sat_mix_call_address(u32 slot); /* CA: the 4096-sample page a voice is reading, 0..15 */

/* the sound out */
void sat_audio_enable(int on);
void sat_audio_wav(const char *path);
void sat_audio_push(const s16 *stereo, u32 frames);
int sat_audio_silent(void); /* sat_audio.c: the last frame of mixed sound was all zero */
void sat_audio_close(void);

#endif
