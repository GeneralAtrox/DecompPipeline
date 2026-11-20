"""Xbox 360 port kit: translate the game, build the runtime, run it, and loop until the next stop is not a missing game function
(an import, a library function to replace, a fault).

usage: python gen_xbox.py bringup --ws WORKSPACE --out DIR [--entry HEX] [--rounds N] [--depth D] [--seconds S]
       python gen_xbox.py translate --ws WORKSPACE --out DIR ADDR...     (translate, write the tables; no build)
       python gen_xbox.py translate --all [--jobs N] --ws WORKSPACE --out DIR   (every catalogue function, in worker processes; no build)
       python gen_xbox.py translate --again REGEX --ws WORKSPACE --out DIR   (the gen/ functions whose C matches, after a translator change)
       python gen_xbox.py build --ws WORKSPACE --out DIR [--xma-dll DLL] (compile what changed, link DIR/xbox_run.exe)
       python gen_xbox.py run --ws WORKSPACE --out DIR [--seconds S]     (one bounded run, prints the stop)

translate --all takes every catalogue function ahead of a run and records those that do not translate in state.json. Library
ranges in port/games/<game>/boundary.json are never translated, they are replaced. A stop on a missing function (one the catalogue
lacks, or any function without --all) translates it and what it calls directly (--depth, default 1); a stop on an import, a fault or
a function that does not translate ends the loop with the evidence, because the next piece of kit is written by hand from it.
Output (DIR): gen/fn_ADDR.c (the translator's port form + the thunk x360_check.gen_thunk makes, renamed xg_ADDR), gen/tables.c,
obj/, xbox_run.exe, run.log (+ run.log.stop), state.json (one row per round: the stop, what was translated), touched.txt.
Nearest existing: port/tools/gen_port.py (the Dreamcast kit's generator, which translates the whole game up front and links
one binary) - this one also translates what a run reaches, and stops on the kit it lacks; the build environment is core.check.msvc_env.
"""
import argparse
import ctypes
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent
X360 = KIT.parent
REPO = X360.parent.parent
sys.path[:0] = [str(REPO), str(X360), str(X360 / "translator")]


def _own_project():
    """core.paths wants a project folder; the kit's build state lives in OUT/project, never in the judged workspace."""
    a = sys.argv
    if "DECOMP_PROJECT" in os.environ or "--out" not in a or "--ws" not in a:
        return
    out = pathlib.Path(a[a.index("--out") + 1]) / "project"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pathlib.Path(a[a.index("--ws") + 1]) / "project.json", out / "project.json")
    os.environ["DECOMP_PROJECT"] = str(out)


_own_project()
from core import check as core_check  # noqa: E402
from core import paths  # noqa: E402
import reached, read_first  # noqa: E402
import xb_join  # noqa: E402
import xb_leaf  # noqa: E402
import xb_pool  # noqa: E402
import x360_check  # noqa: E402
sys.path.insert(0, str(REPO / "port" / "tools"))
import build_saturn, scene_map  # noqa: E402
import shader_convert  # noqa: E402
from xenontranslate import keeps, returns, workspace  # noqa: E402
from xenontranslate.translate import Signature, TranslateError, translate_function  # noqa: E402

BASE = REPO / "port" / "base"                 # the port base every kit stands on (window, device, log, pads, pacing)
KIT_RUNTIME = [KIT / "runtime" / n for n in ("xb_mem.c", "xb_call.c", "xb_imports.c", "xb_file.c", "xb_flow.c", "xb_prof.c", "xb_xact.c", "xb_sound.c", "xb_sound_out.c", "xb_xma_dll.c", "xb_xma_hw.c", "xb_state.c", "xb_content.c", "xb_net.c", "xb_net_sock.c", "xb_net_game.c", "xb_net_stage.c", "xb_net_check.c", "xb_net_session.c", "xb_d3d.c", "xb_vconv.c", "xb_gpu.c", "xb_main.c", "console_x360.c")]
SHARED_RUNTIME = [X360 / "runtime" / "tc_ppc.c"]
# Kit files built /O2 (with /fp:strict like the rest): the vertex conversion's loop, 45 ms of a Too Human gameplay frame at /O1 (2026-11-14). The
# rest of the kit stays at the judge's /O1 (docs/RUNTIME_TARGET.md)
O2_FILES = {"xb_vconv.c"}
BASE_LIBS = ["user32.lib", "gdi32.lib", "winmm.lib", "kernel32.lib", "shell32.lib", "advapi32.lib", "ole32.lib", "d3d11.lib", "dxgi.lib", "dxguid.lib",
             "comctl32.lib", "bcrypt.lib", "dbghelp.lib", "ws2_32.lib"]
CXX_FLAGS = ["/std:c++20", "/EHsc", "/permissive-", "/utf-8", "/DNOMINMAX", "/DWIN32_LEAN_AND_MEAN"]   # build_saturn's: for the base's C++ files


def idle_priority():
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x40)   # IDLE_PRIORITY_CLASS


def jumps(game):
    """port/games/<game>/jumps.json: {address: "setjmp" / "longjmp"} for the game's own setjmp and longjmp, which the translator
    turns into a host setjmp at each call site and a call of the runtime's xb_longjmp (docs/x360/EXCEPTIONS_AND_LONGJMP.md)."""
    p = pathlib.Path(game) / "jumps.json"
    t = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {int(a, 16): k for k in ("setjmp", "longjmp") for a in t.get(k, [])}


def eh(game, wsdir):
    """port/games/<game>/eh.json and the image's FuncInfo tables (eh_tables.py): the groups, or [] without the file."""
    import eh_tables
    t = eh_tables.load(game)
    if t is None:
        return []
    meta = json.loads((pathlib.Path(wsdir) / "image.json").read_text())
    return eh_tables.scan((pathlib.Path(wsdir) / "image.bin").read_bytes(), meta, t["frame_handler"], t["throw"],
                          seh=eh_tables.seh_handler(meta))   # and the __C_specific_handler functions: their __finally run on a throw


class Kit:
    def __init__(self, ws, out, game="mega_man_10"):
        self.game = KIT / "games" / game
        self.wsdir = pathlib.Path(ws)
        self.out = pathlib.Path(out)
        self.gen = self.out / "gen"
        self.obj = self.out / "obj"
        for d in (self.gen, self.obj): d.mkdir(parents=True, exist_ok=True)
        self.state_path = self.out / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"rounds": [], "failed": {}}
        self.ws = None
        self.kinds = None
        boundary = self.game / "boundary.json"
        self.boundary = [(int(a, 16), int(b, 16), why) for a, b, why in json.loads(boundary.read_text())] if boundary.exists() else []
        # Functions inside the library range that touch no device, ring or GPU address (texture layout arithmetic): kept.json lists
        # them, and they are cut out of the replaced ranges so they translate like game code.
        kept = self.game / "kept.json"
        for kl, kh, _why in ([(int(a, 16), int(b, 16), w) for a, b, w in json.loads(kept.read_text())] if kept.exists() else []):
            cut = []
            for lo, hi, why in self.boundary:
                if kh <= lo or hi <= kl:
                    cut.append((lo, hi, why))
                    continue
                if lo < kl:
                    cut.append((lo, kl, why))
                if kh < hi:
                    cut.append((kh, hi, why))
            self.boundary = cut

    # ---------------------------------------------------------------------------------------------- translation
    def open(self):
        if self.ws is not None:
            return
        self.ws = workspace.Workspace(self.wsdir, target=X360 / "target.json")
        # catalogue functions cut inside a loop are joined (xb_join) before any analysis, so keeps, return kinds and the C all see one body
        self.joined = xb_join.join(self.ws.bodies, (self.wsdir / "image.bin").read_bytes(),
                                   json.loads((self.wsdir / "image.json").read_text()).get("load_address", 0x82000000))
        for f, lohi in self.joined.items():
            self.ws.bodies[f] = [lohi]
        (cache := self.out / "cache").mkdir(exist_ok=True)
        self.keeps = keeps.analyse(self.ws, cache, self.body_of)
        self.kinds = returns.cached_kinds(self.ws, cache, meta=False)
        if self.kinds is None:
            self.kinds = returns.analyse(self.ws, cache, meta=False, keeps=self.keeps)
        self.ws.facts.port = True        # after the return kinds: the summaries are keyed by the judge's facts
        self.ws.facts.read_first_map = read_first.load(self.gen, cache)   # fn_parts.hand_over: what a call hands on
        self.ws.facts.jumps = jumps(self.game)   # an attribute, not a field: the return-kind cache key (repr of the facts) stays
        self.eh = eh(self.game, self.wsdir)      # C++ throw / catch (eh.json): call-site IPs, landing slots, xb_eh_fn in tables.c
        if self.eh:
            import eh_tables
            self.ws.facts.eh = eh_tables.facts(self.eh)

    def have(self):
        return {int(p.stem[3:], 16) for p in self.gen.glob("fn_*.c")}

    def replaced(self, addr):
        return next((why for lo, hi, why in self.boundary if lo <= addr < hi), None)

    def translate(self, addr):
        """Write gen/fn_ADDR.c. Returns None when written, else the reason (a library range or a translator refusal)."""
        self.open()
        why = self.replaced(addr)
        if why:
            return f"replaced, not translated: {why}"
        body = self.ws.bodies.get(addr)
        if body is None:
            body = self.inside(addr)
            if body is None:
                body = self.leaf(addr)
                if body is None:
                    return "not a function of catalog_inputs.json, and not inside one"
                self.state.setdefault("leaf_entries", {})[f"{addr:08x}"] = f"{body[0][1]:08x}"
            else:
                self.state.setdefault("mid_entries", {})[f"{addr:08x}"] = f"{body[0][1]:08x}"
        # The generic signature, never the harness meta: a meta lists the registers the judge compares at each call, from Ghidra's
        # prototypes, and drops arguments to a stub or an import it has no prototype for (the CRT's cookie code calls a stub
        # whose r3 the meta leaves out). A real run needs every argument register the code set.
        sig = Signature.generic_for(addr)
        sig.keeps = self.keeps
        try:
            src, ft = translate_function(self.ws.dol, addr, sig, self.ws.facts, body, returns=self.kinds)
        except TranslateError as e:
            return f"translator: {e}"
        except Exception as e:   # the translator's own bug on one function: for --all a record, not the end of the pass
            return f"translator crashed: {type(e).__name__}: {e}"
        s = ft.sig
        meta = {"name": s.name, "signature": s.text, "ret": s.ret,
                "params": [{"name": n, "kind": k, "storage": stg, "stack_offset": off} for n, k, stg, off in s.params]}
        thunk = x360_check.gen_thunk(meta, stack_base="tc_sp").replace("void tc_thunk(", f"void xg_{addr:08x}(")
        thunk = thunk.replace("#include \"tc_rt.h\"\n", "", 1)
        text, dst = src + "\n" + thunk, self.gen / f"fn_{addr:08x}.c"
        if getattr(ft, "read_first", None):   # the translator's check (fn_parts.uncarried): a scan of gen/ counts them by these lines
            text = f"/* read before set: {' '.join(ft.read_first)} */\n" + text
        if getattr(ft, "uncarried", None):
            text = f"/* uncarried: {' '.join(ft.uncarried)} (read before set, not handed over by the caller: taken as 0) */\n" + text
        if dst.exists() and dst.read_text(encoding="utf-8") == text:
            return None                              # the same bytes: not compiled again
        tmp = self.gen / f"fn_{addr:08x}.c.tmp"      # a killed pass leaves no half-written function
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dst)
        return None

    def refresh(self):
        """gen/ keeps every fn_*.c once written, but a function's C depends on its callees' keeps and return kinds. out/analysis.json
        holds the answers gen/ was written with; when they change (keeps.py, returns.py, the catalogue), every function in gen/ whose
        answer changed or that calls one is translated again, and only files whose bytes differ are rewritten. A function off the
        catalogue (a mid or leaf entry) has no callee list here, so it is not checked."""
        self.open()
        p = self.out / "analysis.json"
        now = {"keeps": {f"{a:08x}": sorted(r) for a, r in self.keeps.items()}, "kinds": {f"{a:08x}": k for a, k in self.kinds.items()},
               "joined": {f"{a:08x}": f"{e:08x}" for a, (_, e) in self.joined.items()}}
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        if old == now:
            return
        if old is not None:
            changed = {int(a, 16) for part in now for a in set(old.get(part, {})) | set(now[part]) if old.get(part, {}).get(a) != now[part].get(a)}
            redo = sorted(a for a in self.have() if a in changed or not changed.isdisjoint(self.callees(a)))
            refused = {f"{a:08x}": why for a in redo if (why := self.translate(a))}
            print(f"analysis changed for {len(changed)} functions: {len(redo)} in gen/ translated again, {len(refused)} refused (old C kept): "
                  f"{dict(list(refused.items())[:5])}", flush=True)
        p.write_text(json.dumps(now), encoding="utf-8")

    def body_of(self, addr):
        return self.inside(addr) or self.leaf(addr)

    def inside(self, addr):
        """A call target inside a catalog function (pdata sized two functions as one, or an entry the catalog lacks): the body
        from there to that function's end; the run found it, so it is a function."""
        if not hasattr(self, "_starts"):
            self._starts = sorted(self.ws.bodies)
        import bisect
        i = bisect.bisect_right(self._starts, addr) - 1
        if i < 0:
            return None
        lo, hi = self.ws.bodies[self._starts[i]][-1]
        return [(addr, hi)] if lo <= addr < hi else None

    def leaf(self, addr):
        """A call target the catalog has no entry for: xb_leaf.body."""
        if not hasattr(self, "_image"):
            self._image = (self.wsdir / "image.bin").read_bytes()
            self._base = json.loads((self.wsdir / "image.json").read_text()).get("load_address", 0x82000000)
        return xb_leaf.body(self._image, self._base, addr)

    def findable(self, addr):
        """A body translate() can take: a catalog function, inside one, or a leaf xb_leaf finds (a "0000" address is none)."""
        return addr in self.ws.bodies or self.inside(addr) is not None or self.leaf(addr) is not None

    def callees(self, addr):
        f = self.ws.catalog.get(addr)
        return [int(c, 16) for c in (f or {}).get("callees", [])]

    def want(self, seeds, depth):
        """Translate the seeds and what they call directly, to depth; returns (written, {addr: reason})."""
        self.open()
        written, failed = [], {}
        have = self.have()
        level = list(seeds)
        for d in range(depth + 1):
            nxt = []
            for a in level:
                if a in have or a in failed:
                    continue
                why = self.translate(a)
                if why is None:
                    written.append(a)
                    have.add(a)
                    nxt += self.callees(a)
                elif d == 0 or "replaced" not in why:
                    failed[a] = why
            level = nxt
        return written, failed

    # ---------------------------------------------------------------------------------------------- tables
    def write_tables(self):
        self.open()
        fns = sorted(self.have())
        imps = [i for i in self.ws.imports if i.get("thunk")]
        libs = sorted({i["library"] for i in self.ws.imports})
        out = ['#include "xb.h"', ""]
        out += [f"void xg_{a:08x}(const u64 *r, const f64 *f, u64 *out_r3, f64 *out_f1);" for a in fns]
        out += ["", "const XbFn xb_fn_table[] = {"]
        out += [f"    {{0x{a:08x}u, xg_{a:08x}}}," for a in fns] or ["    {0, 0}"]
        out += ["};", f"const unsigned xb_fn_count = {len(fns)};", ""]
        out += ["const char *const xb_lib_name[] = {" + ", ".join(f'"{n}"' for n in libs) + "};",
                f"const unsigned xb_lib_count = {len(libs)};", "", "const XbImp xb_imp_table[] = {"]
        out += [f"    {{0x{i['thunk']:08x}u, {libs.index(i['library'])}, {i['ordinal']}}},"
                for i in sorted(imps, key=lambda i: i["thunk"])] or ["    {0, 0, 0}"]
        out += ["};", f"const unsigned xb_imp_count = {len(imps)};", ""]
        vs = [i for i in self.ws.imports if i.get("kind") == "variable"]
        out += ["const XbVar xb_var_table[] = {"]
        out += [f"    {{0x{i['slot']:08x}u, 0x{i['value']:08x}u, {libs.index(i['library'])}, {i['ordinal']}}}," for i in vs] \
            or ["    {0, 0, 0, 0}"]
        out += ["};", f"const unsigned xb_var_count = {len(vs)};", ""]
        names = json.loads((KIT / "data" / "export_names.json").read_text())
        rows = sorted((lib, int(o), n[0]) for lib, d in names.items() for o, n in d.items())
        out += ["const XbName xb_name_table[] = {"] + [f'    {{"{l}.{"exe" if l == "xboxkrnl" else "xex"}", {o}, "{n}"}},' for l, o, n in rows]
        out += ["};", f"const unsigned xb_name_count = {len(rows)};", ""]
        out += ["const XbRange xb_boundary[] = {"] + [f"    {{0x{lo:08x}u, 0x{hi:08x}u}}," for lo, hi, _ in self.boundary]             + (["    {0, 0}"] if not self.boundary else [])
        out += ["};", f"const unsigned xb_boundary_count = {len(self.boundary)};", ""]
        import eh_tables
        out += eh_tables.c_table(self.eh)
        flags = json.loads((self.wsdir / "image.json").read_text()).get("system_flags", 0)
        out += [f"const u32 xb_xex_system_flags = 0x{flags:x}u;", ""]
        text = "\n".join(out)
        p = self.gen / "tables.c"
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
        # the image's shaders, converted offline (tools/shader_convert.py): the HLSL the host compiles when a draw uses them
        base = json.loads((self.wsdir / "image.json").read_text()).get("load_address", 0x82000000)
        # and each compiled at build time (shader_convert.Bytecode, cached in the output folder): the port makes its shaders from that bytecode before
        # play and never compiles during it. XB_SHADER_BYTECODE=0 leaves the bytecode out (the host compiles the HLSL at the first draw, as before)
        code = None if os.environ.get("XB_SHADER_BYTECODE") == "0" or os.name != "nt" else self.out / "shader_cache"
        # XB_SHADER_EXTRA: more folders of <key>.bin (;-separated), such as a run's shader_capture in its data folder: the shaders the game made
        # that the packs did not give (xb_d3d_th.h made_shader_key keeps them)
        blobs = [self.wsdir / "shaders"] + [pathlib.Path(x) for x in os.environ.get("XB_SHADER_EXTRA", "").split(";") if x.strip()]
        made = shader_convert.c_table(self.wsdir / "image.bin", base, blobs, code=code)   # and the packs, shader_convert.py --packs
        text, blob = made if code else (made, b"")
        p = self.gen / "shaders.c"
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
        data = shader_convert.coff_obj(blob, "xb_shader_dxbc")
        self.obj.mkdir(parents=True, exist_ok=True)
        p = self.obj / "shader_dxbc.obj"
        if not p.exists() or p.read_bytes() != data:
            p.write_bytes(data)

    # ---------------------------------------------------------------------------------------------- build
    def cfg_env(self):
        cfg = paths.config()
        env = core_check.msvc_env(cfg)
        cl = shutil.which("cl.exe", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
        if not cl:
            raise RuntimeError("cl.exe not found in the Visual Studio environment")
        return cfg, env, cl

    def build(self):
        cfg, env, cl = self.cfg_env()
        self.write_tables()
        text, p = scene_map.scene_map_c({"dir": self.wsdir}, "", "", header=""), self.gen / "scene_map.c"
        if not p.exists() or p.read_text(encoding="utf-8") != text:   # rewritten only when changed: else every round compiles and links
            p.write_text(text, encoding="utf-8")
        sdl = build_saturn.sdl3(self.out / "xbox_run.exe", "x64-windows")
        base = sorted(BASE.glob("*.c")) + sorted(BASE.glob("*.cpp")) + sorted(BASE.glob("third_party/*.cpp"))
        # native.json: game functions with a native rewrite in the game folder (games/<game>/*.c). Each one's gen/fn_ADDR.c is compiled as fn_ADDR_tx,
        # so its callers reach the game folder's fn_ADDR, which picks the native body or the translated one (a switch of its own)
        nat_p = self.game / "native.json"
        native = {self.gen / f"fn_{a}.c" for a in (json.loads(nat_p.read_text()) if nat_p.exists() else {})}
        game_c = sorted(self.game.glob("*.c"))
        srcs = sorted(self.gen.glob("*.c")) + KIT_RUNTIME + SHARED_RUNTIME + base + game_c
        incs = [X360 / "runtime", KIT / "runtime", self.game, BASE, sdl / "include"]
        flags = ["/nologo", "/c", "/O1", "/fp:strict", "/W3", "/MP","/DTC_PORT", x360_check.fma_define(cfg),
                 *(f"/I{i}" for i in incs), *CXX_FLAGS, f"/Fo{self.obj}\\"]
        hdrs = [X360 / "runtime" / "tc_rt.h", *(KIT / "runtime").glob("*.h"), *self.game.glob("*.h")]
        newest_h = max(p.stat().st_mtime for p in hdrs)
        newest_base_h, kit_side = max(p.stat().st_mtime for p in BASE.glob("*.h*")), set(KIT_RUNTIME) | set(base) | set(game_c)
        simd_h = X360 / "runtime" / "xv_simd.h"   # included only by the port-form files with a fast vector helper (vmx.SIMD)
        simd_t = simd_h.stat().st_mtime
        stale = []
        for s in srcs:
            o = self.obj / (s.stem + ".obj")
            ot = o.stat().st_mtime if o.exists() else 0
            if not ot or ot < max(s.stat().st_mtime, newest_h if s in kit_side else hdrs[0].stat().st_mtime, newest_base_h if s in kit_side else 0):
                stale.append(s)
            elif ot < simd_t and s not in kit_side and b"xv_simd.h" in s.read_bytes()[:600]:   # the include sits above the helpers
                stale.append(s)
            elif s in native and ot < nat_p.stat().st_mtime:   # listed after its last compile: compiled again under its _tx name
                stale.append(s)
        o2 =[s for s in stale if s.name in O2_FILES]
        groups = [(flags, "cl.rsp", [s for s in stale if s.name not in O2_FILES and s not in native])] + ([(["/O2" if f == "/O1" else f for f in flags], "cl_o2.rsp", o2)] if o2 else [])
        groups += [(flags + [f"/D{s.stem}={s.stem}_tx"], f"cl_{s.stem}.rsp", [s]) for s in stale if s in native]
        for fl, name, part, at in ((fl, name, part, at) for fl, name, part in groups for at in range(0, len(part), 3000)):   # a game can have tens of thousands of files, and each call has a time limit
            rsp = self.out / name
            rsp.write_text("\n".join(fl + [str(s) for s in part[at:at + 3000]]), encoding="utf-8")
            rc, log, reason = paths.run_bounded([cl, f"@{rsp}"], 1500, cwd=self.out, env=env)
            errs = [ln for ln in log.splitlines() if re.search(r"error C\d+|fatal error", ln)]
            if rc != 0 or reason:
                return False, errs[:20] or log.splitlines()[-15:], len(stale)
        objs, exe = [self.obj / (s.stem + ".obj") for s in srcs] + [self.obj / "shader_dxbc.obj"], self.out / "xbox_run.exe"   # write_tables' compiled shaders
        if exe.exists() and max(o.stat().st_mtime for o in objs) <= exe.stat().st_mtime:
            return True, [], len(stale)   # nothing newer than the exe: at 88,000 objects a link is worth skipping
        rsp = self.out / "link.rsp"
        rsp.write_text("\n".join(["/nologo", f"/Fe{self.out / 'xbox_run.exe'}", *map(str, objs), *BASE_LIBS, str(sdl / "lib" / "SDL3.lib")]),
                       encoding="utf-8")
        # The linker's options on the command line: inside the response file cl ignored /link and every option after it (D9002), so no
        # build had the map, the 64 MB stack or /LARGEADDRESSAWARE (build box A, 2026-11-07)
        rc, log, reason = paths.run_bounded([cl, f"@{rsp}", "/link", f"/MAP:{self.out / 'xbox_run.map'}", "/LARGEADDRESSAWARE", "/STACK:67108864"],
                                            int(os.environ.get("XB_LINK_SEC", "600")), cwd=self.out, env=env)   # XB_LINK_SEC: a loaded machine links 88,000 objects in over 600 s (build box A at 100% CPU, 2026-11-08)
        if rc != 0 or reason:
            return False, [ln for ln in log.splitlines() if "error" in ln][:20] or log.splitlines()[-15:], len(stale)
        shutil.copy2(sdl / "bin" / "SDL3.dll", self.out / "SDL3.dll")
        return True, [], len(stale)

    # ---------------------------------------------------------------------------------------------- run
    def run(self, entry, seconds):
        log = self.out / "run.log"
        stop = pathlib.Path(str(log) + ".stop")
        stop.unlink(missing_ok=True)
        t0 = time.time()
        rc, out, reason = paths.run_bounded([str(self.out / "xbox_run.exe"), "--headless", "--size", "2560x1600", "--image", str(self.wsdir / "image.bin"),
                                             "--entry", f"{entry:08x}", "--log", str(log), "--seconds", str(seconds),
                                             *(["--input", os.environ["XB_INPUT"]] if os.environ.get("XB_INPUT") else [])], seconds + 60, cwd=self.out)
        txt = log.read_text(encoding="utf-8", errors="replace") if log.exists() else out
        kind, val = (stop.read_text().split() + ["", ""])[:2] if stop.exists() else ("exit" if rc == 0 else f"rc{rc}", "")
        if kind == "rc4":   # the time limit, but a side thread stopped at a missing function first (Too Human's audio thread 82d42b78): that one
            miss = re.search(r"^STOP missing_function ([0-9a-f]{8})", txt, re.M)
            if miss:
                kind, val = "missing_function", miss.group(1)
        return {"kind": kind, "value": val, "rc": rc, "seconds": round(time.time() - t0, 1),
                "log": [ln for ln in txt.splitlines() if ln.startswith(("STOP", "touched", "call stack", "first calls", "last calls", "import ", "start"))]}

    def save(self):
        self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def entry(self, arg):
        self.open()
        return int(arg, 16) if arg else int(json.loads((self.wsdir / "image.json").read_text())["entry_point"])


def holes(k):
    """Starts of code the catalogue misses: the stretch between one function's end and the next one's start, from its first word that is not padding (0 or
    a nop) and after each blr, bctr or tail branch in it. Called through registers or computed addresses (Too Human's 82984788 calls 82986630, 82986660 and
    82984988, which no data word names), each one met at run time stopped a thread one per run. Those the kit can take as leaves and gen/ lacks"""
    import bisect
    import struct
    k.open()
    img = (k.wsdir / "image.bin").read_bytes()
    base = int(json.loads((k.wsdir / "image.json").read_text())["load_address"])
    have, failed, starts = k.have(), set(k.state["failed"]), sorted(k.ws.bodies)
    out = []
    for e in sorted({b[-1][1] for b in k.ws.bodies.values()}):
        j = bisect.bisect_left(starts, e)
        nxt = starts[j] if j < len(starts) else min(e + 0x1000, base + len(img))
        if nxt - e < 8 or k.inside(e) is not None:
            continue
        a, fresh = e, True
        while a < nxt and a + 4 - base <= len(img):
            w = struct.unpack(">I", img[a - base:a - base + 4])[0]
            if fresh and w not in (0, 0x60000000):
                out.append(a)
                fresh = False
                lb = k.leaf(a)   # a leaf runs on past a blr its earlier branches jump over: no new start inside it (82dea9ac in 82dea980's)
                if lb and lb[0][1] > a + 4:
                    a, fresh = min(lb[0][1], nxt), True
                    continue
            if w in (0x4e800020, 0x4e800420) or (w & 0xfc000003) == 0x48000000:
                fresh = True
            a += 4
    return [x for x in out if x not in have and f"{x:08x}" not in failed and k.replaced(x) is None and k.leaf(x) is not None]


def code_pointers(k):
    """Function addresses the code builds with lis + addi/ori (no data word names them): ppcscan.code_built_starts, the rule the judge
    catalogue's scan uses too (source "codeptr"), over the kit's xb_join-joined bodies and with the catch continuations of k.eh left
    out. Too Human's 82644cf0 (a vcall{64} thunk handed as a pointer-to-member to the delegate 82581e68) sat inside 82644ce8, which
    ppcscan had sized to the next entry, so holes() never saw it and the run stopped on missing_function at vblank 6380 (2026-11-14).
    Those not in gen/, failed or replaced (a library range)."""
    if str(X360 / "tools") not in sys.path:
        sys.path.append(str(X360 / "tools"))
    import eh_tables
    import ppcscan
    k.open()
    im = ppcscan.Image((k.wsdir / "image.bin").read_bytes(), json.loads((k.wsdir / "image.json").read_text()))
    found = ppcscan.code_built_starts(im, {s: b[-1][1] for s, b in k.ws.bodies.items()}, eh_tables.continuation_set(k.eh or []))
    have, failed = k.have(), set(k.state["failed"])
    return [t for t in found if t not in have and f"{t:08x}" not in failed and k.replaced(t) is None]


def bringup(k, a):
    entry = k.entry(a.entry)
    k.refresh()
    last = None
    for n in range(a.rounds):
        k.write_tables()
        ok, errs, nstale = k.build()
        if not ok:
            print(f"round {n}: build failed ({nstale} files compiled)")
            print("\n".join(errs))
            return 1
        for t in range(max(1, a.tries)):   # a race can stop some runs early (Too Human's "0000" fill): run again until a stop the loop can act on
            r = k.run(entry, a.seconds)
            if r["kind"] == "exit" or (r["kind"] == "missing_function" and k.findable(int(r["value"] or "0", 16))):
                break
            if r["kind"] in ("rc4", "library_entry", "import"):   # the time limit, or a call the kit has no case for: every run meets it
                break
            if t + 1 < a.tries:
                print(f"round {n} try {t + 1}: {r['kind']} {r['value']}, not one to translate: running again")
        counts = reached.update(k.out, k.wsdir, set(k.ws.bodies))
        print("reached so far:", counts)
        row = {"round": n, "stop": [r["kind"], r["value"]], "seconds": r["seconds"], "functions": len(k.have()),
               "compiled": nstale}
        print(f"round {n}: {r['kind']} {r['value']} ({r['seconds']} s, {len(k.have())} functions, {nstale} compiled)")
        for ln in r["log"]:
            print("   ", ln)
        k.state["rounds"].append(row)
        if r["kind"] != "missing_function":
            k.save()
            return 0
        addr = int(r["value"], 16)
        if (r["kind"], r["value"]) == last:
            print(f"stuck: {r['value']} stopped the run again after it was translated")
            k.save()
            return 1
        last = (r["kind"], r["value"])
        written, failed = k.want([addr], a.depth)
        row["translated"] = [f"{x:08x}" for x in written]
        k.state["failed"].update({f"{x:08x}": w for x, w in failed.items()})
        if addr in failed:
            print(f"{r['value']} was not translated: {failed[addr]}")
            k.save()
            return 1
        k.save()
    return 0


def main():
    idle_priority()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["bringup", "translate", "build", "run"])
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--entry")
    ap.add_argument("--game", default="mega_man_10", help="the folder in port/games")
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--tries", type=int, default=1, help="bringup: runs per round while the stop is a fault or an address that is no function")
    ap.add_argument("--xma-dll", help="xma_decoder.dll to put beside the exe (a separate work, not in this repository)")
    ap.add_argument("--all", action="store_true", help="translate: every catalogue function outside the boundary")
    ap.add_argument("--jobs", type=int, default=8, help="translate --all and --again: worker processes")
    ap.add_argument("--again", help="translate: the gen/ functions whose C matches this regex, again (after a translator change)")
    ap.add_argument("--holes", action="store_true", help="translate: the code between catalogue functions the kit can take as leaves, not yet in gen/")
    ap.add_argument("--code-pointers", action="store_true", help="translate: function addresses other functions build with lis+addi (ppcscan.code_built_starts), not yet in gen/")
    ap.add_argument("addrs", nargs="*")
    a = ap.parse_args()
    k = Kit(a.ws, a.out, a.game)
    if a.cmd == "bringup":
        return bringup(k, a)
    if a.cmd == "translate":
        k.refresh()
        if a.again:   # a translator change: the gen/ functions whose C shows the old form, translated again (unchanged bytes are not rewritten)
            pat = re.compile(a.again)
            replaced = {x for x, why in k.state["failed"].items() if str(why).startswith("replaced")}   # the kit's own code stands in: never translated
            redo = [x for x in sorted(k.have()) if f"{x:08x}" not in replaced
                    and pat.search((k.gen / f"fn_{x:08x}.c").read_text(encoding="utf-8", errors="replace"))]
            print(f"{len(redo)} functions in gen/ match {a.again!r}", flush=True)
            before = dict(k.state["failed"])
            xb_pool.translate_all(k, a.jobs, redo)   # --jobs workers, as --all
            bad = {x: k.state["failed"][f"{x:08x}"] for x in redo if f"{x:08x}" in k.state["failed"] and k.state["failed"][f"{x:08x}"] != before.get(f"{x:08x}")}
            k.write_tables()
            print(f"translated again {len(redo) - len(bad)} of {len(redo)} matching {a.again!r}; not translated: {bad}")
            return 1 if bad else 0
        if a.holes:   # code between catalogue functions: Too Human's render thread died on 82986630 (after a tail branch) and 82986660 (after a bctr), one per run
            a.addrs += [f"{x:08x}" for x in holes(k)]
            print(f"{len(a.addrs)} hole starts to translate", flush=True)
        if a.code_pointers:   # function addresses built by lis+addi from other functions, inside a catalogue body (Too Human's 82644cf0)
            cp = code_pointers(k)
            a.addrs += [f"{x:08x}" for x in cp if f"{x:08x}" not in a.addrs]
            print(f"{len(cp)} code-built function pointers to translate: {' '.join(f'{x:08x}' for x in cp[:8])}...", flush=True)
        written, failed = k.want([int(x, 16) for x in a.addrs], a.depth) if not a.all else (xb_pool.translate_all(k, a.jobs) or [], k.state["failed"])
        k.write_tables()
        print(f"translated {len(written)}; not translated: {failed}")
        return 0
    if a.cmd == "build":
        ok, errs, n = k.build()
        if ok and a.xma_dll:
            shutil.copy2(a.xma_dll, k.out / "xma_decoder.dll")
        print("built" if ok else "build failed", n, "files compiled")
        print("\n".join(errs))
        return 0 if ok else 1
    r = k.run(k.entry(a.entry), a.seconds)
    print(json.dumps(r, indent=1))
    return 0


if __name__ == "__main__":
    # Too Human's largest function (22,655 instructions) nests past Python's default depth: more of it, on a stack that can hold it
    sys.setrecursionlimit(100000)
    threading.stack_size(0xFF00000)     # Windows refuses 256 MB or more
    rc = []
    t = threading.Thread(target=lambda: rc.append(main()))
    t.start()
    t.join()
    sys.exit(rc[0] if rc else 1)
