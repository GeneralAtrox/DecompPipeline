/* xb_eh.h: included by xb_call.c after xb_jmp.h, and by tests/test_x360_eh.py's driver on its own stubs. It needs jmp_buf (setjmp.h),
 * TcArgs, rd32 / wr32 (tc_rt.h), memcpy / memset / calloc, xb_stop, tc_call, the thread variables tc_sp, tc_r12, tc_nv, tc_vr, s_depth
 * (this thread's tc_call depth), s_stack (each live call's target) and s_frame (each live call's TcArgs), and gen_xbox.py's table
 * xb_eh_fn / xb_eh_fn_count (port/tools/eh_tables.py: every function sharing a FuncInfo of the game's frame handler, eh.json) and
 * xb_eh_seh / xb_eh_seh_count (every __C_specific_handler function, with its scope table).
 *
 * The game's C++ throw and catch (docs/x360/EXCEPTIONS_AND_LONGJMP.md, part (b)): _CxxThrowException calls RaiseException, which calls
 * RtlRaiseException with an EXCEPTION_RECORD {code 0xE06D7363, flags, record, address, nparams, info[]: magic, object, ThrowInfo}. Its
 * import (krnl_call, ordinal 310) calls xb_eh_raise. That walks the live tc_call frames from the innermost, as MSVC's frame handler
 * does on the console with the same tables: a frame whose function has a FuncInfo has the state its IP-to-state map gives the call's
 * own address (TcArgs.ip, which every such function's calls record); the first try block holding that state with a catch the throw
 * matches (by type descriptor address, or catch(...)) takes it. Nothing has run yet when a table proves wrong (every frame's tables
 * are checked first), so a wrong state stops instead of running the wrong destructors. Then: the catch object is built, the unwind
 * map's actions (funclets, called with r12 = their frame's entry r1, as the console's unwinder does) run for every frame in between
 * down to -1 and for the catching frame down to the try's state, and the catch funclet runs and returns the continuation. The
 * exception object is destroyed (ThrowInfo.pmfnUnwind), and a host longjmp lands on the catching function's slot (xb_eh_slot, a host
 * setjmp at its entry), which puts back what the skipped tc_call frames would have (as xb_jmp.h does) and branches to the continuation.
 * A throw inside a running catch funclet (a rethrow, `throw;`, takes the exception that catch has) is walked the same way: the
 * funclet's frame stands for its parent at the catch's state, and the frames between it and the parent were unwound already.
 * Returns 0 for a raise that is not a C++ throw (the caller stops as before); a throw no catch takes is a stop, as on the console
 * (terminate). The guest's own __CxxFrameHandler (82a4ff28 in Too Human) never runs.
 * A __C_specific_handler frame on the way (__try / __finally; its calls record their IP too) is checked with the others before anything
 * runs, and unwound in its turn: each __finally whose scope holds the call runs, in the scope table's order (innermost first), as that
 * handler's unwind pass runs them, with AbnormalTermination 1 in r3 and the frame's entry r1 in r4 and r12. An __except scope holding
 * the call is a stop: the console would call its filter with the C++ exception, and the kit does not.
 * The vector file: the slot puts back tc_vr as at the parent's entry. The console's unwinder restores the non-volatile vectors the
 * skipped frames saved, i.e. their values at the parent's call; in the port form a function keeps the vectors it writes in its own
 * (volatile) locals and stores them before each call and return, so for every vector the parent does not write the entry value is
 * its value at the call, and the parent's own come from its locals. The two agree on everything code that keeps the ABI can read. */
typedef struct { jmp_buf host; u32 fn, sp, r12; const u64 *nv; unsigned depth; const u32 *conts; unsigned n; u32 cont, r1; u32 vr[128][4]; } XbEhSlot;
typedef struct { unsigned fe, pe; u32 est, funcinfo, obj, ti; } XbEhCatch;   /* a catch funclet running: its tc_call entry, its parent's */
typedef struct { unsigned e, land; int rec; u32 fi, est, ip; s32 state; const u64 *nv; const XbSeh *seh; } XbEhFrame;
#define XB_EH_SLOTS 64
#define XB_EH_CATCHES 16
static __declspec(thread) XbEhSlot *s_eh;
static __declspec(thread) unsigned s_eh_n;
static __declspec(thread) XbEhSlot *s_eh_landed;
static __declspec(thread) XbEhCatch s_eh_catch[XB_EH_CATCHES];
static __declspec(thread) unsigned s_eh_catch_n;
static __declspec(thread) int s_eh_unwinding;

/* At the entry of a function with catch blocks (the translator's landing, eh.py): this call's slot. A slot as deep as this call or deeper
 * belongs to a call that has returned. conts: the labels its catches may continue at. */
jmp_buf *xb_eh_slot(u32 fn, const u32 *conts, unsigned n)
{
    XbEhSlot *j;
    if (!s_eh && (s_eh = (XbEhSlot *)calloc(XB_EH_SLOTS, sizeof *s_eh)) == NULL) xb_stop("out_of_memory", "eh slots", fn);
    while (s_eh_n && s_eh[s_eh_n - 1].depth >= s_depth) s_eh_n--;
    if (s_eh_n == XB_EH_SLOTS) { xb_stop("eh", "more functions with catch blocks live on one thread than the kit keeps", fn); s_eh_n--; }
    j = &s_eh[s_eh_n++];
    j->fn = fn, j->sp = tc_sp, j->r12 = tc_r12, j->nv = tc_nv, j->depth = s_depth, j->conts = conts, j->n = n, j->cont = 0, j->r1 = 0;
    memcpy(j->vr, tc_vr, sizeof j->vr);
    return &j->host;
}

u32 xb_eh_landed(void) { return s_eh_landed->cont; }
u32 xb_eh_sp(void) { return s_eh_landed->r1; }

static const XbEhFn *eh_row(u32 entry)
{
    unsigned lo = 0, hi = xb_eh_fn_count;
    while (lo < hi) {
        unsigned m = (lo + hi) / 2;
        if (xb_eh_fn[m].begin == entry) return xb_eh_fn[m].end ? &xb_eh_fn[m] : NULL;
        if (xb_eh_fn[m].begin < entry) lo = m + 1; else hi = m;
    }
    return NULL;
}

static const XbSeh *eh_seh_row(u32 entry)
{
    unsigned lo = 0, hi = xb_eh_seh_count;
    while (lo < hi) {
        unsigned m = (lo + hi) / 2;
        if (xb_eh_seh[m].begin == entry) return xb_eh_seh[m].end ? &xb_eh_seh[m] : NULL;
        if (xb_eh_seh[m].begin < entry) lo = m + 1; else hi = m;
    }
    return NULL;
}

/* a __C_specific_handler frame's scope table in guest memory, as eh_tables.read_seh checks it at gen time: NULL when sound */
static const char *eh_seh_check(const XbSeh *s)
{
    u32 n = rd32(s->scope), k;
    if (n == 0 || n > 64) return "a __C_specific_handler scope table's count is out of range";
    for (k = 0; k < n; k++) {
        u32 at = s->scope + 4 + 16 * k, lo = rd32(at), hi = rd32(at + 4), h = rd32(at + 8), jt = rd32(at + 12);
        if (!(s->begin <= lo && lo < hi && hi <= s->end)) return "a __C_specific_handler scope is outside its function";
        if (!jt && !h) return "a __finally scope without a handler";
        if (jt && !(s->begin <= jt && jt < s->end)) return "an __except scope's target is outside its function";
    }
    return NULL;
}

static s32 eh_max(u32 fi) { return (s32)rd32(fi + 4); }
static s32 eh_to(u32 fi, s32 s) { return (s32)rd32(rd32(fi + 8) + 8 * (u32)s); }

/* whether the toState chain from s reaches to (-1: the end) inside the map, without a loop */
static int eh_chain(u32 fi, s32 s, s32 to)
{
    s32 k, max = eh_max(fi);
    for (k = 0; k <= max + 1; k++) {
        if (s == to) return 1;
        if (s < 0 || s >= max) return 0;
        s = eh_to(fi, s);
    }
    return 0;
}

/* the tables at fi, as eh_tables.validate checks them at gen time: NULL when sound, else the reason */
static const char *eh_check(u32 fi)
{
    u32 m = rd32(fi) & 0x1FFFFFFFu, ntry = rd32(fi + 12), nip = rd32(fi + 20), ipm = rd32(fi + 24), k, last = 0;
    s32 max = eh_max(fi), s;
    if (m < 0x19930520u || m > 0x19930522u) return "FuncInfo magic is not 0x1993052x";
    if (max < 0 || max > 4096 || ntry > 4096 || nip > 4096) return "FuncInfo counts out of range";
    for (s = 0; s < max; s++)
        if (!eh_chain(fi, s, -1)) return "an unwind state's toState chain does not end at -1";
    for (k = 0; k < ntry; k++) {
        u32 t = rd32(fi + 16) + 20 * k;
        s32 lo = (s32)rd32(t), hi = (s32)rd32(t + 4), ch = (s32)rd32(t + 8);
        if (!(0 <= lo && lo <= hi && hi < ch && ch < max)) return "a try block's states are not inside the unwind map";
        for (s = lo; s <= hi; s++)
            if (!eh_chain(fi, s, lo)) return "a try block's state chain does not pass its first state";
        if (!rd32(t + 12)) return "a try block without a catch";
    }
    for (k = 0; k < nip; k++) {
        u32 ip = rd32(ipm + 8 * k);
        s = (s32)rd32(ipm + 8 * k + 4);
        if (k && ip <= last) return "the IP-to-state map is not in order";
        if (s < -1 || s >= max) return "an IP-to-state entry's state is outside the unwind map";
        last = ip;
    }
    return NULL;
}

static s32 eh_state(u32 fi, u32 ip)
{
    u32 n = rd32(fi + 20), ipm = rd32(fi + 24), k;
    s32 s = -1;
    for (k = 0; k < n && rd32(ipm + 8 * k) <= ip; k++) s = (s32)rd32(ipm + 8 * k + 4);
    return s;
}

/* the catchable type of the throw that a handler {adjectives, type} takes (catch(...): type 0), else 0 (1 for catch(...)) */
static u32 eh_match(u32 adj, u32 type, u32 ti)
{
    u32 cta, n, k;
    if (!type) return 1;
    cta = rd32(ti + 12), n = rd32(cta);
    for (k = 0; k < n && k < 64; k++) {
        u32 ct = rd32(cta + 4 + 4 * k);
        if (rd32(ct + 4) == type && !((rd32(ct) & 4u) && !(adj & 8u))) return ct;   /* CT_ByReferenceOnly needs a reference */
    }
    return 0;
}

static u64 eh_call(u32 fn, u32 est, const u64 *nv, u32 r3, u32 r4, u32 r5, u32 sp)
{
    TcArgs t;
    memset(&t, 0, sizeof t);
    t.r[0] = r3, t.r[1] = r4, t.r[2] = r5, t.sp[0] = sp, t.r12[0] = est;
    if (nv) memcpy(t.nv, nv, sizeof t.nv);
    return tc_call(fn, &t);
}

static void eh_unwind(const XbEhFrame *f, s32 to, u32 sp)
{
    s32 s = f->state;
    s_eh_unwinding++;
    if (f->seh) {   /* the __finally blocks whose scope holds the call, innermost first: AbnormalTermination 1, the frame in r4 and r12 */
        u32 n = rd32(f->seh->scope), at = f->seh->scope + 4;
        for (; n--; at += 16)
            if (!rd32(at + 12) && rd32(at) <= f->ip && f->ip < rd32(at + 4)) (void)eh_call(rd32(at + 8), f->est, f->nv, 1, f->est, 0, sp);
    }
    while (s != to && s >= 0) {
        u32 act = rd32(rd32(f->fi + 8) + 8 * (u32)s + 4);
        s = eh_to(f->fi, s);
        if (act) {
            static volatile LONG said;
            if (InterlockedIncrement(&said) <= 64) port_log("eh: unwind action %08x (to state %d, FuncInfo %08x)\n", act, (int)s, f->fi);
            (void)eh_call(act, f->est, f->nv, 0, 0, 0, sp);
        }
    }
    s_eh_unwinding--;
}

/* the exception object is destroyed when no catch still running has it */
static void eh_destroy(u32 obj, u32 ti, u32 sp)
{
    unsigned k;
    for (k = 0; k < s_eh_catch_n; k++)
        if (s_eh_catch[k].obj == obj) return;
    if (ti && rd32(ti + 4)) {
        s_eh_unwinding++;
        (void)eh_call(rd32(ti + 4), 0, NULL, obj, 0, 0, sp);
        s_eh_unwinding--;
    }
}

int xb_eh_raise(const TcArgs *a)
{
    static const u64 zero_nv[18];
    u32 rec = (u32)a->r[0], obj, ti, sp = (a->sp[0] - 0x800u) & ~15u, h = 0, ct = 0, k;
    unsigned D = s_depth, nf = 0, i;
    int e, found = -1;
    s32 low = 0;
    XbEhFrame fr[256];
    XbEhSlot *slot = NULL;
    u64 cont;
    if (rd32(rec) != 0xE06D7363u || rd32(rec + 16) < 3 || (rd32(rec + 20) - 0x19930520u > 2u && rd32(rec + 20) != 0x01994000u)) return 0;
    obj = rd32(rec + 24), ti = rd32(rec + 28);
    if (s_eh_unwinding) { xb_stop("eh", "a throw while destructors run for another (the console's terminate)", ti); return 1; }
    if (!ti) {
        if (!s_eh_catch_n) { xb_stop("eh", "a rethrow (throw;) with no catch running (the console's terminate)", 0); return 1; }
        obj = s_eh_catch[s_eh_catch_n - 1].obj, ti = s_eh_catch[s_eh_catch_n - 1].ti;
    }
    /* first: find the catch, and check every frame's tables on the way, before anything runs */
    for (e = (int)D - 2; e >= 0 && found < 0;) {
        const TcArgs *in = s_frame[(e + 1) & 255];
        const XbEhFn *row = eh_row(s_stack[e & 255]);
        const char *why;
        XbEhFrame *f;
        int r = -1;
        for (k = s_eh_catch_n; k-- > 0;)
            if (s_eh_catch[k].fe == (unsigned)e) { r = (int)k; break; }
        if (e + 256 < (int)D) { xb_stop("eh", "the call stack is deeper than the kit keeps", ti); return 1; }
        if (!row) {
            const XbSeh *sr = eh_seh_row(s_stack[e & 255]);
            u32 n, at;
            if (r >= 0) { xb_stop("eh", "a catch funclet the runtime called has no row in xb_eh_fn", s_stack[e & 255]); return 1; }
            if (sr) {   /* a __C_specific_handler frame: checked now, its __finally blocks run in the unwind */
                if (sr->bad) { xb_stop("eh", sr->bad, sr->begin); return 1; }
                if (in->ip[0] < sr->begin || in->ip[0] >= sr->end) {
                    xb_stop("eh", "a frame's call-site IP is outside its function (not translated with eh.json, or a wrong table)", in->ip[0]);
                    return 1;
                }
                if ((why = eh_seh_check(sr)) != NULL) { xb_stop("eh", why, sr->scope); return 1; }
                for (n = rd32(sr->scope), at = sr->scope + 4; n--; at += 16)
                    if (rd32(at + 12) && rd32(at) <= in->ip[0] && in->ip[0] < rd32(at + 4)) {
                        xb_stop("eh", "an __except filter on the way of a C++ throw (the console would ask it; the kit does not)", rd32(at + 8));
                        return 1;
                    }
                f = &fr[nf++];
                f->e = (unsigned)e, f->land = (unsigned)e, f->rec = -1, f->fi = 0, f->ip = in->ip[0], f->state = -1;
                f->est = s_frame[e & 255]->sp[0], f->nv = in->nv, f->seh = sr;
            }
            e--;
            continue;
        }
        if (r < 0 && row->parent != row->begin) { xb_stop("eh", "a catch or unwind funclet on the stack that the runtime did not call", row->begin); return 1; }
        if (row->bad) { xb_stop("eh", row->bad, row->begin); return 1; }
        if (in->ip[0] < row->begin || in->ip[0] >= row->end) {
            xb_stop("eh", "a frame's call-site IP is outside its function (not translated with eh.json, or a wrong table)", in->ip[0]);
            return 1;
        }
        if (r >= 0 && row->funcinfo != s_eh_catch[r].funcinfo) { xb_stop("eh", "a catch funclet's FuncInfo is not its parent's", row->begin); return 1; }
        if ((why = eh_check(row->funcinfo)) != NULL) { xb_stop("eh", why, row->funcinfo); return 1; }
        f = &fr[nf++];
        f->seh = NULL;
        f->e = (unsigned)e, f->rec = r, f->fi = row->funcinfo, f->ip = in->ip[0], f->state = eh_state(row->funcinfo, in->ip[0]);
        f->est = r >= 0 ? s_eh_catch[r].est : s_frame[e & 255]->sp[0];
        f->land = r >= 0 ? s_eh_catch[r].pe : (unsigned)e;
        f->nv = r >= 0 ? s_frame[(s_eh_catch[r].pe + 1) & 255]->nv : in->nv;
        for (k = 0; k < rd32(f->fi + 12) && found < 0; k++) {
            u32 t = rd32(f->fi + 16) + 20 * k, n = rd32(t + 12), j;
            s32 lo = (s32)rd32(t), hi = (s32)rd32(t + 4);
            if (f->state < lo || f->state > hi) continue;
            for (j = 0; j < n && found < 0; j++) {
                u32 ha = rd32(t + 16) + 16 * j;
                if ((ct = eh_match(rd32(ha), rd32(ha + 4), ti)) != 0) found = (int)nf - 1, h = ha, low = lo;
            }
        }
        if (found < 0 && !eh_chain(f->fi, f->state, -1)) { xb_stop("eh", "a frame's state chain does not end at -1", f->ip); return 1; }
        e = r >= 0 ? (int)s_eh_catch[r].pe - 1 : e - 1;
    }
    if (found < 0) { xb_stop("eh", "a C++ throw no catch takes (the console's terminate)", ti); return 1; }
    /* the catch object (HandlerType.dispCatchObj from the establisher frame), as MSVC builds it before unwinding */
    if (rd32(h + 4) && rd32(h + 8)) {
        u32 at = fr[found].est + rd32(h + 8), src = obj + rd32(ct + 8);
        if ((s32)rd32(ct + 12) >= 0) {   /* a virtual base: the vbtable at obj + pdisp, entry vdisp */
            u32 p = obj + rd32(ct + 12);
            src = obj + rd32(ct + 8) + rd32(rd32(p) + rd32(ct + 16)) + rd32(ct + 12);
        }
        if (rd32(h) & 8u) wr32(at, src);
        else if (!(rd32(ct) & 1u) && rd32(ct + 24)) (void)eh_call(rd32(ct + 24), 0, NULL, at, src, 1, sp);
        else memcpy(tc_base + at, tc_base + src, rd32(ct + 20));
    }
    /* unwind: the frames in between to -1 (a running catch whose funclet frame is left goes with it), the catching frame to the try */
    for (i = 0; i <= (unsigned)found; i++) {
        eh_unwind(&fr[i], i == (unsigned)found ? low : -1, sp);
        if (fr[i].rec >= 0) {
            XbEhCatch c = s_eh_catch[fr[i].rec];
            s_eh_catch_n = (unsigned)fr[i].rec;
            if (c.obj != obj) eh_destroy(c.obj, c.ti, sp);
        }
    }
    /* the catch funclet, with its parent's frame in r12; it returns the continuation */
    if (s_eh_catch_n == XB_EH_CATCHES) { xb_stop("eh", "more catch blocks running at once than the kit keeps", ti); return 1; }
    {
        XbEhCatch *c = &s_eh_catch[s_eh_catch_n++];
        c->fe = s_depth, c->pe = fr[found].land, c->est = fr[found].est, c->funcinfo = fr[found].fi, c->obj = obj, c->ti = ti;
    }
    cont = eh_call(rd32(h + 12), fr[found].est, fr[found].nv ? fr[found].nv : zero_nv, 0, 0, 0, sp);
    s_eh_catch_n--;
    eh_destroy(obj, ti, sp);
    {   /* the landing slot of the catching function's call (a catch that rethrows never gets here: its function needs none) */
        unsigned L = fr[found].land;
        for (k = s_eh_n; k-- > 0;)
            if (s_eh[k].fn == s_stack[L & 255] && s_eh[k].depth == L + 1 && s_eh[k].sp == s_frame[L & 255]->sp[0]) { slot = &s_eh[k]; break; }
        if (!slot) { xb_stop("eh", "the catching function has no landing slot (not translated with eh.json)", s_stack[L & 255]); return 1; }
    }
    for (k = 0; k < slot->n && slot->conts[k] != (u32)cont; k++) {}
    if (k == slot->n) { xb_stop("eh", "a catch funclet's continuation is not a label of its function", (u32)cont); return 1; }
    slot->cont = (u32)cont, slot->r1 = s_frame[(fr[found].land + 1) & 255]->sp[0];
    s_eh_n = (unsigned)(slot - s_eh) + 1;
    s_eh_landed = slot;
    {   /* a throw is an error in the games this serves: each catch is worth a line */
        static volatile LONG said;
        if (InterlockedIncrement(&said) <= 64)
            port_log("eh: throw of ti %08x caught in %08x by funclet %08x, %u frame(s) unwound, continuation %08x (frame %u)\n", ti,
                     s_stack[fr[found].land & 255], rd32(h + 12), (unsigned)found + 1, (u32)cont, xb_frame_number());
    }
    tc_sp = slot->sp, tc_r12 = slot->r12, tc_nv = slot->nv, s_depth = slot->depth;
    memcpy(tc_vr, slot->vr, sizeof slot->vr);
    longjmp(slot->host, 1);
}
