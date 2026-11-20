/* present_overlay.c: pictures over a CPU-buffer frame at the window's resolution (present_cpu.c draws them). */
#ifndef PRESENT_OVERLAY_H
#define PRESENT_OVERLAY_H

#include "present_cpu.h"

int pov_init(void *compile_fn); /* D3DCompile: the overlay's shader, sampler and blend; 0 none (nothing is drawn over) */
void pov_draw(const PresentOverlay *o, unsigned n, const void *frame_vp, unsigned fw, unsigned fh); /* fv: D3D11_VIEWPORT */

#endif
