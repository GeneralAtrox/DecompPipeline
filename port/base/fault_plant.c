/* --fault-at V (game_log.c): the test fault itself. An integer divide by zero, done by the processor: every console
 * kit's exception handler passes it to the crash path (port/runtime/dispatch.c fatal and the like), and none emulates
 * it. A write to a low address was used before and never faulted: the Dreamcast kit's boot ROM model (rom_read.c)
 * took it as a ROM write and went on, and the other kits model their own low memory. tests/test_fault_plant.py runs this
 * under a handler that swallows every access violation, as those do. */

static volatile int g_zero;

int port_plant_fault(void)
{
    volatile int one = 1;
    return one / g_zero;
}
