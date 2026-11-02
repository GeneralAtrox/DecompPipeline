/* The AICA ARM7's measures and proof tools (runtime/aica_arm_probe.c), all off unless an environment variable asks:
 * DP_ARM_TRACE=FILE (cost per vertical blank), DP_ARM_LOG=FILE (the proof log), DP_ARM_PCS=BASE (the recorder). */
#ifndef AICA_ARM_PROBE_H
#define AICA_ARM_PROBE_H

#include "arm7.h"

void arm7_probe_start(Arm7 *a);          /* opens what the environment asks for (once); sets a->pcs for the recorder */
int arm7_probe_recording(void);          /* the recorder is on: the interpreter alone runs the driver */
u64 arm7_probe_now_ns(void);
void arm7_probe_sample(const Arm7 *a);   /* one chip sample run: the hot block of the driver's code */
void arm7_probe_blank(u64 samples);      /* before a run of samples: the proof log's digest and the trace's row of a new blank */
u64 arm7_probe_run_begin(void);          /* around a run of n samples: its host time and instructions, for the trace */
void arm7_probe_run_end(u64 begin, u64 executed0, u32 n, const Arm7 *a);
void arm7_probe_poll(void);              /* the SH-4 polled the driver ... */
void arm7_probe_wait(void);              /* ... and waited for the next blank */
void arm7_probe_write(u64 samples, u32 addr, u32 value, u32 size); /* an ARM write beyond the sound RAM */

#endif
