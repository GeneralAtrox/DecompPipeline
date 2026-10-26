/* Saturn SH-2 readable-C runtime: the SH-2 has no P4 area (no operand-cache address array, no store queues), so
 * nothing is mapped and no store is logged outside work RAM; listed hardware writes are runtime/tc_p4.c's (TcHw).
 * These stubs keep rd_veh.c the Dreamcast runtime's. */
#include "rd_internal.h"

int rd_p4_init(void) { return 1; }

int rd_p4_fault(u32 addr, int is_write, void *context)
{
    (void)addr;
    (void)is_write;
    (void)context;
    return 0;
}

int rd_p4_step(void) { return 0; }

/* Each vector starts with an empty hardware-store log, as the Dreamcast runtime's rd_p4_reset: else a vector's report
 * keeps the listed register writes of the vectors before it. */
void rd_p4_reset(void) { tcrt_p4_reset(); }
