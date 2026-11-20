"""The port form's pieces for the game's C++ throw / catch (docs/x360/EXCEPTIONS_AND_LONGJMP.md, part (b)). Facts.eh, an attribute
gen_xbox.eh() sets from port/games/<game>/eh.json and the image's FuncInfo tables (port/tools/eh_tables.py), is
{"fns": {entry, ...}, "land": {entry: [continuation, ...]}}: every function sharing a FuncInfo of the game's frame handler (a parent,
its catch and unwind funclets) records each call's own address in TcArgs.ip, so the runtime's frame walk (runtime/xb_eh.h) knows the
state of every live frame; and a parent with catch blocks that return gets a landing slot: a host setjmp at entry and, after it, a
branch on the continuation address the catch funclet returned to that label. Without the attribute, and in the judge's form, nothing
changes.
Nearest existing: the setjmp sites of part (a) in emit.py / translate.py - one host setjmp per guest setjmp call that lands back at that
call; a catch lands at one of several labels the original reaches only through its unwinder, so the blocks and the branch are new."""
from . import expr as X
from .ir import Assign, Block, Goto, If

LAND, SP = "ehland", "ehsp"   # the continuation the runtime landed with (0 on entry) and the frame's r1 at the call that threw


def land_of(facts, entry):
    """The continuations of the function at entry when it gets a landing slot (port form with Facts.eh), else None."""
    eh = getattr(facts, "eh", None)
    if not facts.port or not eh:
        return None
    conts = eh.get("land", {}).get(entry)
    return sorted(conts) if conts else None


def records_ip(facts, entry):
    eh = getattr(facts, "eh", None)
    return bool(facts.port and eh and entry in eh.get("fns", ()))


def wire(ft, blocks, conts):
    """After the read-before-set scans (the continuations are reached only through the runtime): the entry goes on to a check of
    LAND; 0 is the normal path, else r1 is the frame's own (the entry block's r1 = sp0 ran again after the host longjmp) and the
    branch picks the continuation's label. The runtime has checked the value is one of conts (xb_eh_slot's list)."""
    for c in conts:
        if ft.label(c) not in {b.label for b in blocks}:
            raise ValueError(f"catch continuation {c:08x} is not a label of {ft.entry:08x}")
    entry = blocks[0]
    first = entry.term.label
    entry.term = Goto("L_eh_check")
    land = Var(LAND)
    chk = Block("L_eh_check")
    chk.term = If(X.compare("==", land, X.Const(0, "u32")), first, "L_eh_land")
    out = [chk]
    b = Block("L_eh_land")
    b.stmts.append(Assign("r1", X.cast("u64", Var(SP))))
    for k, c in enumerate(conts):
        if k == len(conts) - 1:
            b.term = Goto(ft.label(c))
        else:
            b.term = If(X.compare("==", land, X.Const(c, "u32")), ft.label(c), f"L_eh_land{k + 1}")
        out.append(b)
        b = Block(f"L_eh_land{k + 1}")
    blocks[1:1] = out
    ft.eh_land = list(conts)


def Var(name):
    return X.Var(name, "u32")


def landing_text(ft):
    """The C after the declarations: the host setjmp on the runtime's slot for this call of the function."""
    conts = ft.eh_land
    return [f"    static const u32 ehc_[] = {{{', '.join(f'0x{c:08x}u' for c in conts)}}};",
            f"    volatile u32 {LAND} = 0, {SP} = 0;",
            f"    if (setjmp(*xb_eh_slot(0x{ft.entry:08x}u, ehc_, {len(conts)}u))) {{ {LAND} = xb_eh_landed(); {SP} = xb_eh_sp(); }}"]


HEADER = ["#include <setjmp.h>", "jmp_buf *xb_eh_slot(u32 fn, const u32 *conts, unsigned n);", "u32 xb_eh_landed(void);",
          "u32 xb_eh_sp(void);", ""]
