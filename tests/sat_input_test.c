/* The Saturn pad's layout (sat_input.c, included whole): prints each action and the host button it reads, as built with
 * the sat_game.h tests/test_sat_input.py writes (game.json "controller", "confirm", "cancel"). */
#define _CRT_SECURE_NO_WARNINGS
#include <stdio.h>
#include "sat_input.c"

/* a stand-in for each function sat_input.c calls */
u32 host_actions(const HostPad *pad) { (void)pad; return 0; }
u32 host_pads_read(HostPad *pad) { (void)pad; return 1; }
int sat_net_on(void) { return 0; }
u32 sat_net_pads(u32 data[2]) { (void)data; return 1; }

int main(void)
{
    unsigned n;
    const PortAction *t = console_actions(&n);
    printf("kit:");
    for (unsigned i = 0; i < n; i++) printf(" %s=%s", t[i].name, t[i].dflt);
    printf("\n");
    return 0;
}
