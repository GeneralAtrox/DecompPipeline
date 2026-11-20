/* xb_jmp.h: included by xb_call.c, and by tests/test_x360_setjmp.py's driver on its own stubs. It needs jmp_buf (setjmp.h), TcArgs,
 * memcpy and calloc, xb_stop, and these thread variables: tc_sp, tc_r12, tc_nv, tc_vr and s_depth (this thread's tc_call depth). */
/* The game's setjmp and longjmp (port/games/<game>/jumps.json; docs/x360/EXCEPTIONS_AND_LONGJMP.md). The translator makes each call of
 * the game's setjmp a host setjmp in the calling function's own C frame, on a slot from here for the guest jmp_buf at r3, and each call
 * of its longjmp a call of xb_longjmp. That puts back what the tc_call frames it skips would have (this thread's call depth, tc_sp,
 * tc_r12, tc_nv and the vector file) and lands at the setjmp with r4 (1 for 0), as the console's longjmp does. The caller's r14..r31,
 * f14..f31 and CR fields are its own C locals, unchanged since the setjmp (volatile in such a function), which is what the console's
 * restore from the jmp_buf gives. The guest's jmp_buf itself is never written: only its own setjmp and longjmp read it. */
#define XB_JMP_SLOTS 32
typedef struct { jmp_buf host; u32 buf, sp, tc_sp, r12; const u64 *nv; unsigned depth; u64 val; u32 vr[128][4]; } XbJmp;
static __declspec(thread) XbJmp *s_jmp;
static __declspec(thread) unsigned s_jmp_n;
static __declspec(thread) XbJmp *s_landed;

jmp_buf *xb_setjmp_slot(const TcArgs *a)
{
    u32 buf = (u32)a->r[0], sp = a->sp[0];
    XbJmp *j;
    if (!s_jmp && (s_jmp = (XbJmp *)calloc(XB_JMP_SLOTS, sizeof *s_jmp)) == NULL) xb_stop("out_of_memory", "setjmp slots", buf);
    /* a slot deeper on the guest stack than this call is a frame that has returned; one at this depth for this buffer is this frame's earlier setjmp */
    while (s_jmp_n && (s_jmp[s_jmp_n - 1].sp < sp || (s_jmp[s_jmp_n - 1].sp == sp && s_jmp[s_jmp_n - 1].buf == buf))) s_jmp_n--;
    if (s_jmp_n == XB_JMP_SLOTS) { xb_stop("setjmp", "more setjmp frames live on one thread than the kit keeps", buf); s_jmp_n--; }
    j = &s_jmp[s_jmp_n++];
    j->buf = buf, j->sp = sp, j->tc_sp = tc_sp, j->r12 = tc_r12, j->nv = tc_nv, j->depth = s_depth, j->val = 0;
    memcpy(j->vr, tc_vr, sizeof j->vr);
    return &j->host;
}

u64 xb_setjmp_landed(void) { return s_landed->val; }

void xb_longjmp(const TcArgs *a)
{
    u32 buf = (u32)a->r[0];
    unsigned k = s_jmp_n;
    /* the newest live slot for this buffer: a slot deeper on the guest stack than the longjmp's caller has returned */
    while (k && !(s_jmp[k - 1].buf == buf && s_jmp[k - 1].sp >= a->sp[0])) k--;
    if (!k) { xb_stop("longjmp", "no live setjmp for this jmp_buf", buf); return; }
    s_jmp_n = k;
    s_landed = &s_jmp[k - 1];
    s_landed->val = a->r[1] ? a->r[1] : 1;
    {   /* a longjmp is an error in the games this serves (Lua's error(), a compiler failure): each is worth a line */
        static volatile LONG said;
        if (InterlockedIncrement(&said) <= 64)
            port_log("longjmp: buf %08x value %llu from %08x lands in %08x (depth %u -> %u, frame %u)\n", buf, s_landed->val, xb_caller(0),
                     s_stack[(s_landed->depth - 1) & 255], s_depth, s_landed->depth, xb_frame_number());
    }
    tc_sp = s_landed->tc_sp, tc_r12 = s_landed->r12, tc_nv = s_landed->nv, s_depth = s_landed->depth;
    memcpy(tc_vr, s_landed->vr, sizeof s_landed->vr);
    longjmp(s_landed->host, 1);
}
