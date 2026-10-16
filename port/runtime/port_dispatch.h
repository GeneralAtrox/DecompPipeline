/* The generated guest-address dispatch table: every original function's guest address and its host function. */
#ifndef PORT_DISPATCH_H
#define PORT_DISPATCH_H

#include "rd_rt.h"

typedef void (*PortFn)(void);

typedef struct {
    u32 addr;
    PortFn fn;
    const char *name;
} PortDispatch;

typedef struct {
    const PortDispatch *rows;
    const int *n;
} PortDispatchChunk;

extern const PortDispatchChunk port_dispatch_chunks[];
extern const int port_dispatch_nchunks;

const PortDispatch *port_dispatch_find(u32 addr);

#endif
