"""Xbox 360 port kit: translate what a run stops on, build the runtime, run it, and loop until the next stop is not a missing
game function (an import, a library function to replace, a fault).

usage: python gen_xbox.py bringup --ws WORKSPACE --out DIR [--entry HEX] [--rounds N] [--depth D] [--seconds S]
       python gen_xbox.py translate --ws WORKSPACE --out DIR ADDR...     (translate, write the tables; no build)
       python gen_xbox.py build --ws WORKSPACE --out DIR [--xma-dll DLL] (compile what changed, link DIR/xbox_run.exe)
       python gen_xbox.py run --ws WORKSPACE --out DIR [--seconds S]     (one bounded run, prints the stop)

Nothing is translated ahead of a run: the first round has no game function and stops at the entry point. A stop on a missing
function translates that function and the functions it calls directly (--depth, default 1; library ranges in
port/boundary.json are never translated, they are replaced); a stop on an import, a fault or a function that does not
translate ends the loop with the evidence, because the next piece of kit is written by hand from it.
Output (DIR): gen/fn_ADDR.c (the translator's port form + the thunk x360_check.gen_thunk makes, renamed xg_ADDR), gen/tables.c,
obj/, xbox_run.exe, run.log (+ run.log.stop), state.json (one row per round: the stop, what was translated), touched.txt.
Nearest existing: port/tools/gen_port.py (the Dreamcast kit's generator, which translates the whole game up front and links
one binary) - this one starts with nothing and translates what a run reaches; the build environment is core.check.msvc_env.
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
import reached  # noqa: E402
import x360_check  # noqa: E402
sys.path.insert(0, str(REPO / "port" / "tools"))
import build_saturn, scene_map  # noqa: E402
import shader_convert  # noqa: E402
from xenontranslate import keeps, returns, workspace  # noqa: E402
from xenontranslate.translate import Signature, TranslateError, translate_function  # noqa: E402

BASE = REPO / "port" / "base"                 # the port base every kit stands on (window, device, log, pads, pacing)
KIT_RUNTIME = [KIT / "runtime" / n for n in ("xb_mem.c", "xb_call.c", "xb_imports.c", "xb_file.c", "xb_xact.c", "xb_sound.c", "xb_sound_out.c", "xb_xma_dll.c", "xb_state.c", "xb_content.c", "xb_net.c", "xb_net_sock.c", "xb_net_game.c", "xb_net_stage.c", "xb_net_check.c", "xb_net_session.c", "xb_d3d.c", "xb_gpu.c", "xb_main.c", "console_x360.c")]
SHARED_RUNTIME = [X360 / "runtime" / "tc_ppc.c"]
BASE_LIBS = ["user32.lib", "gdi32.lib", "winmm.lib", "kernel32.lib", "shell32.lib", "advapi32.lib", "ole32.lib", "d3d11.lib", "dxgi.lib", "dxguid.lib",
             "comctl32.lib", "bcrypt.lib", "dbghelp.lib", "ws2_32.lib"]
CXX_FLAGS = ["/std:c++20", "/EHsc", "/permissive-", "/utf-8", "/DNOMINMAX", "/DWIN32_LEAN_AND_MEAN"]   # build_saturn's: for the base's C++ files


def idle_priority():
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x40)   # IDLE_PRIORITY_CLASS


class Kit:
    def __init__(self, ws, out):
        self.wsdir = pathlib.Path(ws)
        self.out = pathlib.Path(out)
        self.gen = self.out / "gen"
        self.obj = self.out / "obj"
        for d in (self.gen, self.obj):
            d.mkdir(parents=True, exist_ok=True)
        self.state_path = self.out / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"rounds": [], "failed": {}}
        self.ws = None
        self.kinds = None
        boundary = KIT / "boundary.json"
        self.boundary = [(int(a, 16), int(b, 16), why) for a, b, why in json.loads(boundary.read_text())] \
            if boundary.exists() else []
        # Functions inside the library range that touch no device, ring or GPU address (texture layout arithmetic): kept.json lists
        # them, and they are cut out of the replaced ranges so they translate like game code.
        kept = KIT / "kept.json"
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
        cache = self.out / "cache"
        cache.mkdir(exist_ok=True)
        self.keeps = keeps.analyse(self.ws, cache, self.body_of)
        self.kinds = returns.cached_kinds(self.ws, cache, meta=False)
        if self.kinds is None:
            self.kinds = returns.analyse(self.ws, cache, meta=False, keeps=self.keeps)
        self.ws.facts.port = True        # after the return kinds: the summaries are keyed by the judge's facts

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
        s = ft.sig
        meta = {"name": s.name, "signature": s.text, "ret": s.ret,
                "params": [{"name": n, "kind": k, "storage": stg, "stack_offset": off} for n, k, stg, off in s.params]}
        thunk = x360_check.gen_thunk(meta, stack_base="tc_sp").replace("void tc_thunk(", f"void xg_{addr:08x}(")
        thunk = thunk.replace("#include \"tc_rt.h\"\n", "", 1)
        (self.gen / f"fn_{addr:08x}.c").write_text(src + "\n" + thunk, encoding="utf-8")
        return None

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
        """A call target the catalog has no entry for: a leaf the compiler gave no unwind record (pdata lists only functions that
        save registers or use a frame). The run called it, so it is code; its body ends at the first unconditional return or backward tail jump past every
        forward branch inside it. Returns [(addr, end)] or None when no such return is found in 256 instructions."""
        if not hasattr(self, "_image"):
            self._image = (self.wsdir / "image.bin").read_bytes()
            self._base = json.loads((self.wsdir / "image.json").read_text()).get("load_address", 0x82000000)
        off = addr - self._base
        if off < 0 or off + 4 > len(self._image):
            return None
        reach = addr
        for i in range(256):
            pc = addr + 4 * i
            if pc - self._base + 4 > len(self._image):
                return None
            w = int.from_bytes(self._image[pc - self._base:pc - self._base + 4], "big")
            op = w >> 26
            if op in (16, 18) and not w & 1 and not w & 2:      # b / bc, relative, not a call
                d = (w & 0xFFFC) - ((w & 0x8000) << 1) if op == 16 else (w & 0x03FFFFFC) - ((w & 0x02000000) << 1)
                if 0 < d <= 0x400:
                    reach = max(reach, pc + d)
                if op == 18 and pc >= reach and pc + d < addr:      # or a tail jump out of it: 820a0130
                    return [(addr, pc + 4)]
            if w == 0x4E800020 and pc >= reach:
                return [(addr, pc + 4)]
        return None

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
        flags = json.loads((self.wsdir / "image.json").read_text()).get("system_flags", 0)
        out += [f"const u32 xb_xex_system_flags = 0x{flags:x}u;", ""]
        text = "\n".join(out)
        p = self.gen / "tables.c"
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
        # the image's shaders, converted offline (tools/shader_convert.py): the HLSL the host compiles when a draw uses them
        base = json.loads((self.wsdir / "image.json").read_text()).get("load_address", 0x82000000)
        text = shader_convert.c_table(self.wsdir / "image.bin", base)
        p = self.gen / "shaders.c"
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")

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
        (self.gen / "scene_map.c").write_text(scene_map.scene_map_c({"dir": self.wsdir}, "", "", header=""), encoding="utf-8")
        sdl = build_saturn.sdl3(self.out / "xbox_run.exe", "x64-windows")
        base = sorted(BASE.glob("*.c")) + sorted(BASE.glob("*.cpp")) + sorted(BASE.glob("third_party/*.cpp"))
        srcs = sorted(self.gen.glob("*.c")) + KIT_RUNTIME + SHARED_RUNTIME + base
        incs = [X360 / "runtime", KIT / "runtime", BASE, sdl / "include"]
        flags = ["/nologo", "/c", "/O1", "/fp:strict", "/W3", "/MP2", "/DTC_PORT", x360_check.fma_define(cfg),
                 *(f"/I{i}" for i in incs), *CXX_FLAGS, f"/Fo{self.obj}\\"]
        hdrs = [X360 / "runtime" / "tc_rt.h", *(KIT / "runtime").glob("*.h")]
        newest_h = max(p.stat().st_mtime for p in hdrs)
        newest_base_h, kit_side = max(p.stat().st_mtime for p in BASE.glob("*.h*")), set(KIT_RUNTIME) | set(base)
        stale = []
        for s in srcs:
            o = self.obj / (s.stem + ".obj")
            if not o.exists() or o.stat().st_mtime < max(s.stat().st_mtime, newest_h, newest_base_h if s in kit_side else 0):
                stale.append(s)
        if stale:
            rsp = self.out / "cl.rsp"
            rsp.write_text("\n".join(flags + [str(s) for s in stale]), encoding="utf-8")
            rc, log, reason = paths.run_bounded([cl, f"@{rsp}"], 1500, cwd=self.out, env=env)
            errs = [ln for ln in log.splitlines() if re.search(r"error C\d+|fatal error", ln)]
            if rc != 0 or reason:
                return False, errs[:20] or log.splitlines()[-15:], len(stale)
        objs = [self.obj / (s.stem + ".obj") for s in srcs]
        rsp = self.out / "link.rsp"
        rsp.write_text("\n".join(["/nologo", f"/Fe{self.out / 'xbox_run.exe'}", *map(str, objs), "/link", "/LARGEADDRESSAWARE", "/STACK:67108864", *BASE_LIBS, str(sdl / "lib" / "SDL3.lib")]),
                       encoding="utf-8")
        rc, log, reason = paths.run_bounded([cl, f"@{rsp}"], 600, cwd=self.out, env=env)
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
        return {"kind": kind, "value": val, "rc": rc, "seconds": round(time.time() - t0, 1),
                "log": [ln for ln in txt.splitlines() if ln.startswith(("STOP", "touched", "call stack", "first calls", "last calls", "import ", "start"))]}

    def save(self):
        self.state_path.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def entry(self, arg):
        self.open()
        return int(arg, 16) if arg else int(json.loads((self.wsdir / "image.json").read_text())["entry_point"])


def bringup(k, a):
    entry = k.entry(a.entry)
    last = None
    for n in range(a.rounds):
        k.write_tables()
        ok, errs, nstale = k.build()
        if not ok:
            print(f"round {n}: build failed ({nstale} files compiled)")
            print("\n".join(errs))
            return 1
        r = k.run(entry, a.seconds)
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
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--xma-dll", help="xma_decoder.dll to put beside the exe (a separate work, not in this repository)")
    ap.add_argument("addrs", nargs="*")
    a = ap.parse_args()
    k = Kit(a.ws, a.out)
    if a.cmd == "bringup":
        return bringup(k, a)
    if a.cmd == "translate":
        written, failed = k.want([int(x, 16) for x in a.addrs], a.depth)
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
    sys.exit(main())
