# Xbox 360 port: setjmp/longjmp and C++ exceptions

Written 2026-11-12 for Too Human (branch `x360-th-eh`). Parts (a) and (b) are implemented (b on branch `x360-th-eh-throw`); neither is in a build yet.

## What Too Human uses (measured from the retail image, names from the 2007 PDBs)

Counted by scanning `.text` for `b`/`bl` to each address, and `.pdata` for the exception flag; the scan script is
`C:\Projects\Xbox360\TooHuman\.work\tx1112c\scan\measure.py` (not committed). "Run" means listed in `run.log.touched` of the
user's 2026-11-09 play (217 s, 1,534 frames, 44,535 functions touched).

| What | Sites | Who calls it | Run on 2026-11-09 |
|---|---|---|---|
| setjmp `82ec5460` (LIBCMT) | 5 `bl` in 5 functions | Lua 5.0 `luaD_rawrunprotected` (every protected call); XDK shader compiler (xgraphics): `D3DXShader::CompileToMicrocode`, `OptimizeMicrocode`, `XGRAPHICS::Compiler::CompileShader`, `CompileShaderPair` | yes: `luaD_rawrunprotected` and `CompileToMicrocode` |
| longjmp `82a566a0` (LIBCMT) | 22 `bl` + 2 `b`, 17 functions | Lua errors (`luaD_call`, `luaD_precall`, `luaM_realloc`, `luaX_errorline`, `f_luaopen`; no `luaD_throw` of its own is among the callers); `luaX_syntaxerror` `82809ae8` (42 callers in the Lua parser) ends in it; xgraphics compiler fatal errors (`FatalErrorWorker`, `Compiler::Malloc`, `CFG::Assemble`, `LinkageInfo::*`, ...) | no: longjmp itself was never called |
| `_CxxThrowException` `82a4fcc0` | 110 `bl`, 76 functions | SpeedTree RT file parsing (`CSpeedTreeRT::Parse*`, `CTreeEngine::Parse*`, `CFrondEngine::Parse*`, ...: a bad tree file), `std::_String_base::_Xlen/_Xran`, and `82ed35f0` | no throw; 25 of its callers ran (SpeedTree loaded trees without one) |
| `82ed35f0` (unnamed; `std::vector` length error by its callers) | 23 `bl`, 23 functions | `std::vector<...>::_Buy` / `_Insert_n` in the SpeedTree library | no |
| RaiseException `82a6eef0` | 2 | `_CxxThrowException`, `_raise_exc_ex` | no |

- **C++ handler data:** 345 functions have the `.pdata` exception flag. 260 use handler `82a4ff28` (all 260 FuncInfo records
  carry magic `0x19930522`, MSVC's `__CxxFrameHandler` layout): 149 of them have try blocks, **185 try blocks, 260 catch
  handlers**; the named ones are SpeedTree and the CRT, 188 are unnamed. 81 use `__C_specific_handler` (xboxkrnl #421: SEH
  `__try`, mostly LIBCMT, xmedia, xapilib, xgraphics); 4 records are not readable.
- **On a normal path:** setjmp runs every time Lua makes a protected call and when the game compiles a shader. Nothing in
  the evidence uses longjmp or a throw for control flow: every longjmp site is an error (Lua `error()`, out of memory, a
  syntax error, a compiler failure), and every throw is a parse or length error. Lua 5.0's coroutines yield without longjmp
  (by the Lua 5.0.2 source, not checked against this binary). **Not known:** whether the game's scripts raise Lua errors
  that its own code catches as a normal event (none happened in the one run above), and whether a SpeedTree file on the
  disc makes the parser throw (none did in that run).
- Today, a throw stops the port (`xb_stop("import")` at RtlRaiseException), and a longjmp returns to its own caller, so a
  Lua error carries on in the wrong place (in `82c20f70` and `82c21580` the C falls through into the next function).

## How guest state is held in the generated C

Each translated function is its own C function, and every guest register it touches is a **C local** (`r14`, `f31`,
`c2eq`, `lr`, ...). Nothing holds r14..r31 between functions: a callee's prologue saves its own locals' values to the
guest stack and its epilogue loads them back into its own locals, so a caller's non-volatiles survive any call because
they are the caller's locals. What lives outside the locals, per thread (`runtime/xb_call.c`): `tc_sp` (the callee's
entry r1), `tc_r12` and `tc_nv` (a funclet's parent frame and the parent's r14..r31, `TcArgs.nv`), `tc_vr` (the whole
vector file, `fn_parts.vector_abi`), `tc_gr` / `tc_fr` (registers a caller hands on, `fn_parts.hand_over`) and the call
depth `s_depth` with its stack of targets.

What follows: the guest jmp_buf's saved r14..r31 / f14..f31 are worth nothing to the port (they are another function's
locals; a translated setjmp reads its own, which start at 0), and a non-local return must land **in the C frame of the
function that called setjmp**, with that function's own locals. Only a host `setjmp` in that frame can do it.

## (a) setjmp / longjmp: implemented

- `port/games/too_human/jumps.json` names the game's setjmp and longjmp; `gen_xbox.jumps()` reads it into the
  translator's facts (an attribute, so the return-kind cache key is unchanged). Without the file nothing changes, and the
  judge's form never has it.
- Translator (`translate.py`, `emit.py`): a call of setjmp passes r3 and becomes
  `if (setjmp(*xb_setjmp_slot(&a))) r3 = xb_setjmp_landed(); else r3 = 0;` in the caller's own C; a call of longjmp passes
  r3 and r4 and becomes `xb_longjmp(&a);`. In a function with a setjmp site the scalar locals are `volatile` (C11
  7.13.2.1: a local changed after setjmp is otherwise indeterminate after the longjmp), and the TcArgs fields the emitter
  remembers are forgotten after the site.
- Runtime (`port/runtime/xb_jmp.h`, included by `xb_call.c`): `xb_setjmp_slot` keeps, per thread, a stack of slots: the
  guest jmp_buf address, the caller's guest r1, `tc_sp`, `tc_r12`, `tc_nv`, the call depth and a copy of `tc_vr`, plus
  the host `jmp_buf`. A slot deeper on the guest stack than a new setjmp has returned and is dropped. `xb_longjmp` finds
  the newest live slot for the buffer (no deeper than the longjmp's caller), puts those values back (what the skipped
  `tc_call` frames would have restored, and the vector file that the console's longjmp reloads), sets the value (r4, 1
  for 0) and does a host `longjmp`. No live slot is a stop (`xb_stop("longjmp")`).
- The guest's jmp_buf is not written (only the guest's setjmp and longjmp read it). Guest setjmp's first instructions
  jump through a hook word at `833904ec` when it is non-zero; it is 0 in the image, and whether anything sets it is not
  known. longjmp's other path (jmp_buf `+0x138` non-zero: `_NLG_Notify` and RtlUnwind) needs a setjmp that wrote that
  word; the plain setjmp writes 0.
- What it reuses: the Dreamcast and Saturn runtimes already leave a translated call chain with a host
  `setjmp`/`longjmp` (`tcrt_halt_jmp`, for TcExit and spins: `targets/dreamcast_sh4/runtime/tc_rt.c`). The GameCube kit
  has nothing on setjmp or exceptions. No kit in this repo translated a guest setjmp before.
- To use it: re-translate the 5 setjmp callers and the 17 longjmp callers (`gen_xbox.py translate <addrs>`), build, then
  the usual launcher check. Not built yet.
- Biggest risk: runtime state that a skipped `tc_call` frame would have restored and the slot does not hold. Today that is
  `tc_sp`, `tc_r12`, `tc_nv`, the depth and the vector file; a new per-call thread variable added to `tc_call_inner`
  later must be added to the slot too. Second: the host longjmp unwinds C++ frames between the two points with MSVC's
  unwinder; today only C (`tc_call_inner` and generated functions) lies between them.

## (b) C++ throw / catch: implemented (option 2 below)

As built, and where the design below proved wrong:
- `port/games/too_human/eh.json` names the frame handler (`82a4ff28`) and the throws (`82a4fcc0`, `82ed35f0`); `port/tools/eh_tables.py`
  reads every `.pdata` function with that handler from the image at gen time, groups them by FuncInfo, reads the tables, finds each
  catch funclet's continuation (the constant r3 at its exits) and validates: every toState chain ends at -1, each try's states lie in
  the unwind map with chains through its first state, handlers are funclets of the group, one parent, the IP map in order and inside
  the group's functions, every continuation inside the parent. A failing group gets no landing and its reason goes into the runtime's
  table (`xb_eh_fn` in `tables.c`), so a throw through it stops with the reason. `python eh_tables.py <ws> <game>` prints the scan.
- Measured on the retail image (2026-11-12, build box A's workspace): **167 FuncInfo groups, 260 functions, all 167 validate.** The counts
  above were per function, funclets included: by group there are **56 parents with try blocks, 68 try blocks, 93 catches** (25 typed,
  68 `catch(...)`); **51 return** (51 continuations in **26 parents**, at most 2 each) and **42 rethrow** (`catch(...) { ...; throw; }`,
  STL growth), so the walker handles a rethrow from a running catch.
- Layouts, from the image: HandlerType is 16 bytes `{adjectives, pType, dispCatchObj, addressOfHandler}`; FuncInfo's 8th word is 0 and
  EHFlags 1. A funclet gets in r12 the parent's **entry** r1 (the establisher frame; it does `addi r31, r12, -frame`), not its r1, and
  `dispCatchObj` counts from it. The IP-to-state map gives a state to the **call instruction's own address** (`82ecabdc`, a `bl`, is state
  2; `82ecabe0` after it is 0), so TcArgs.ip is the `bl`'s address.
- Translator (`xenontranslate/eh.py`): every function of a group records `a.ip[0]` at its calls (TcArgs gained `ip`); a parent with
  returning catches gets `xb_eh_slot` (a host setjmp at entry), volatile locals, and a check of the landed value before its body
  (r1 from the frame, then the continuation's label, kept as a root). Such a parent is **not optimised**: a continuation is entered
  from every call in a try with the registers as they stood, which the passes' flow from the entry does not see.
- Runtime (`port/runtime/xb_eh.h`, RtlRaiseException ordinal 310): checks every frame's tables and finds the catch before anything
  runs, builds the catch object, unwinds, runs the catch, destroys the object, and lands. tc_call keeps each call's TcArgs (`s_frame`).
- `__finally` (2026-11-12): every `__C_specific_handler` function (xboxkrnl #421's thunk; Too Human has **81, with 53 `__finally` and
  35 `__except` scopes**, every handler a .pdata function) is read at gen time (`eh_tables.read_seh`: each scope inside its function,
  a `__finally` handler a function, an `__except` target inside) into `xb_eh_seh`, and records its call sites. A throw through one runs
  each `__finally` whose scope holds the call (`[begin, end)`, the call's own address, as the IP-to-state map), in table order, with r3 = 1
  (abnormal) and r4 = r12 = the frame's entry r1 (the funclets do `addi r31, r12, -frame` as the parent's `addi r31, r1, -frame`). An
  `__except` scope holding the call stops: its filter would be asked first on the console.
- The vector file, worked out (2026-11-12): the console restores non-volatile vectors (v14-v31, v64-v127) to their values **at the
  catching frame's call** (the unwinder replays the skipped frames' prologue saves: 29 functions call `__savevmx128_N`, 93 store
  vectors inline in their prologue); volatile ones are undefined. The slot's copy at the parent's entry gives the same values for
  every vector the parent does not write (it cannot change between its entry and its call), and the ones it writes live in its
  volatile locals, stored before each call and return (`fn_parts.vector_abi`). So the two agree on all that ABI-keeping code reads;
  the earlier note here that they differ was wrong, and no replay of the prologues was built.
- Tests: `tests/test_x360_eh.py`. Translated and compiled at b218fb45 on build box A: the 260 functions, no errors; all 51 continuations
  inside their parents' bodies. Not done: no Too Human build runs it yet.

### The design, as written before it was built

The guest's own tables are enough to decide everything: FuncInfo (unwind map, try map, catch handlers), the
IP-to-state map, the throw info's catchable types. Two ways to run them:

1. **The guest's code all the way:** answer RtlRaiseException, RtlUnwind and `__C_specific_handler` in the runtime and
   let the guest's `__CxxFrameHandler` (`82a4ff28`) walk the guest stack. It cannot work as things are: the walk needs
   each frame's return address on the guest stack, and a translated function's `lr` starts as the judge's sentinel, so
   every saved LR word is that constant; and when the catch funclet returns its continuation address, nothing can
   resume a C function in the middle.
2. **Proposed: a runtime frame handler on the guest's tables, landing like longjmp.** The translator gives each function
   that has try blocks a landing slot (as setjmp above) and a `switch` on the continuation address just after it,
   covering every catch's continuation label (the catch funclets' return values are known from their code). Each call
   site records its guest address (a `TcArgs` field), so the runtime knows every live frame's IP. RtlRaiseException
   (from `_CxxThrowException`) then: finds the innermost live frame whose IP-to-state map puts the IP in a try block
   with a catch matching the throw info's types (by type descriptor address, as the console does); runs the unwind-map
   destructors of every frame in between (they are translated funclets, called with `r12` = that frame's r1, as
   `TcArgs.r12` already does); calls the catch funclet; and host-longjmps to the function's slot with the continuation
   address. No match: the stop the port has today. A `__finally` funclet on the way (`__C_specific_handler` functions)
   is called the same way.
   Cost: three translator pieces (call-site IP, landing slot plus continuation switch, the 260 functions' funclets kept
   callable), one runtime piece (a FuncInfo walker of about the size of MSVC's), nothing on the normal path but one
   more TcArgs field. Biggest risk: getting the state numbering right from the IP-to-state map at a call site, including
   the catch funclets' own states; a wrong state runs the wrong destructors and is silent.
   Priority: low while no throw happens on a normal path (above); a throw is an error the original would also be in.

An outside project, by what this repo holds: COGBox's build of the rexglue recompiler compiles with `/EHa` because "guest
SEH scopes emit `__try`/`__except`" (`.work\cogbox\Gears1-Native\cmake\rexglue.cmake`), and its scan notes that exception
scope tables point at resume points inside SEH functions. How it handles C++ throws or setjmp is not known from it.

## (c) What the judge can and cannot prove

The judge compares one function at a time on test vectors, with its callees mocked. It can prove: setjmp's stores of
the registers it is given into the jmp_buf, longjmp's loads from it (as a body), each Lua or compiler function's work up
to the call, and the zero-return path after a setjmp (the mock returns 0). It cannot prove anything about a non-local
transfer: a mocked longjmp returns, the landing path after setjmp is reached only with a non-zero mock, the guest's
longjmp ends in a `blr` to a loaded LR that the harness takes as a return, and with the port's change the setjmp and
longjmp sites are not the judged form at all. A throw is a kernel import, outside the judge.

What does prove them: `tests/test_x360_setjmp.py` builds the translated C of a made-up setjmp caller with the runtime's
`xb_jmp.h` on stubs and runs it (a longjmp from two frames below and one from the function itself land, return the
right register, call nothing twice, and put back depth, `tc_sp` and the vector file). In the game: a replay that makes a
Lua error and catches it (a script calling `error()` inside `pcall`), compared with Xenia's run of the same input: the
game log's script messages and the frames must match, where today the port carries on after the longjmp's own caller.
For (b), the same with a SpeedTree file made unreadable on purpose, once (b) exists.

## The hand-over check

`port/tools/handover.py` takes the stores of r14..r31 and f14..f31 by the setjmp in `jumps.json` as saves (its C no
longer runs in the port: each call of it is the host setjmp), so its callers' r14..r31 are no hand-over. Any other
function that stores a caller's non-volatile into memory outside its own frame is still a read
(`test_setjmp_declared_is_a_save_another_buffer_store_still_a_read`).
