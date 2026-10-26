"""The C compiler's identity (the second waste audit's findings B and D):
1. config "vcvars_args" (vcvars' own arguments, e.g. "10.0.28000.0 -vcvars_ver=14.44": the Windows SDK and the
   toolset) goes after the quoted script at every Visual Studio environment load (core/toolcfg.vcvars_command: the
   judge's 64-bit and 32-bit environments, core/check and core/readable_check, and the port kit's build). Without it
   the command is byte for byte the one before; anything but an SDK version, -vcvars_ver= or the Spectre libraries is
   refused. toolcfg.pinned checks a cache holds the SDK and toolset named.
2. Each cached environment has a key (toolcfg.cache_current, finding D): the vcvars script, "vcvars_args" and cl's
   full version. An unchanged setting is a cache hit with the same environment; another vcvars, other arguments,
   another cl (the same toolset folder on another machine), a missing key or a PATH that is gone loads it again. The
   key's version is taken without running cl while cl.exe's path, size and mtime are unchanged.
3. A vcvars run that exits nonzero loads nothing and caches nothing, even when the variables it printed look right
   (a pinned SDK that is not fully installed); nor does one whose environment misses the pin (toolcfg.loaded).
4. The judge stamp (core/verdict_memo.judge_stamp) holds each cached environment's compiler: cl's full version line,
   WindowsSDKVersion, the content hash of the SDK's headers and libraries, and VCToolsVersion. Another cl or SDK gives
   another stamp, so no verdict made with one is reused under another.
5. The SDK's content hash (toolcfg.sdk_hash) covers Include/<version>/ucrt, um and shared and Lib/<version>/ucrt and
   um for the target, as INCLUDE and LIB name them: two builds of one SDK version (10.0.28000.1839 and .2114 both
   say 10.0.28000.0) differ in it, two install folders with the same files do not. A key keeps it while the folders'
   listing (sdk_scan) is unchanged; a changed file loads the cache again.
The live check, cl and the SDK reporting what was pinned, is proof M (docs/proofs/2026-10-23/PROOFS.md): it needs
Visual Studio.
Run: python -W error tests/test_compiler_identity.py (also from test_tooling.py).
"""
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOL = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL / "tests"))
sys.path.insert(0, str(TOOL))
from test_sync import TMP  # noqa: E402  (sets DECOMP_PROJECT when run alone)
from core import check, paths, readable_check, toolcfg, verdict_memo  # noqa: E402

BAT = r"C:\VS\VC\Auxiliary\Build\vcvars64.bat"  # an example path, not a machine's


class Pin(unittest.TestCase):
    def test_command(self):
        self.assertEqual(toolcfg.vcvars_command(BAT, {}), f'cmd /d /s /c ""{BAT}" >nul 2>&1 && set"')  # as before
        for none in (None, "", "  "):
            self.assertEqual(toolcfg.vcvars_command(BAT, {"vcvars_args": none}), toolcfg.vcvars_command(BAT, {}))
        self.assertEqual(toolcfg.vcvars_command(BAT, {"vcvars_args": "10.0.28000.0 -vcvars_ver=14.44"}),
                         f'cmd /d /s /c ""{BAT}" 10.0.28000.0 -vcvars_ver=14.44 >nul 2>&1 && set"')
        self.assertIn(" -vcvars_ver=14.44.35207 ",
                      toolcfg.vcvars_command(BAT, {"vcvars_args": "-vcvars_ver=14.44.35207"}))
        for bad in ("14.44", "latest", "-vcvars_ver=14", "-vcvars_ver=14.44 & calc", '10.0.28000.0"', "x86",
                    "10.0.28000.0 > out"):
            with self.assertRaises(ValueError):
                toolcfg.vcvars_command(BAT, {"vcvars_args": bad})

    def test_stock_warning(self):  # vcvars_args with a wrapper script: a warning, the same command
        wrapper = r"C:\Tools\vcvars_v143\vcvars64.bat"  # an example wrapper path
        pin = {"vcvars_args": "10.0.28000.0 -vcvars_ver=14.44"}
        for bat, cfg, warned in ((wrapper, pin, True), (BAT, pin, False), (wrapper, {}, False),
                                 (BAT.replace("vcvars64", "vcvars32"), pin, False)):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertTrue(toolcfg.vcvars_command(bat, cfg).startswith(f'cmd /d /s /c ""{bat}"'))
            self.assertEqual("not Visual Studio's own" in err.getvalue(), warned, (bat, cfg))

    def test_pinned(self):
        env = {"VCToolsVersion": "14.44.35207", "WindowsSDKVersion": "10.0.28000.0\\", "PATH": "x"}
        self.assertTrue(toolcfg.pinned(env, {}))
        self.assertTrue(toolcfg.pinned(env, {"vcvars_args": "-vcvars_ver=14.44"}))
        self.assertTrue(toolcfg.pinned(env, {"vcvars_args": "10.0.28000.0 -vcvars_ver=14.44.35207"}))
        self.assertFalse(toolcfg.pinned(env, {"vcvars_args": "-vcvars_ver=14.38"}))
        self.assertFalse(toolcfg.pinned(env, {"vcvars_args": "-vcvars_ver=14.4"}))  # 14.4 is not 14.44
        self.assertFalse(toolcfg.pinned(env, {"vcvars_args": "10.0.26100.0 -vcvars_ver=14.44"}))  # another SDK
        self.assertFalse(toolcfg.pinned({"PATH": "x"}, {"vcvars_args": "-vcvars_ver=14.44"}))  # before the pin

    def test_identity(self):  # the stamp's compiler line: cl, the SDK and the toolset of the environment
        env = {"VCToolsVersion": "14.44.35207", "WindowsSDKVersion": "10.0.28000.0\\", "PATH": ""}
        with mock.patch.object(toolcfg, "cl_version", lambda env, known=None: "cl 19.44.35229"):
            self.assertEqual(toolcfg.compiler_identity(env),
                             "cl 19.44.35229 sdk 10.0.28000.0 files - tools 14.44.35207")
            self.assertNotEqual(toolcfg.compiler_identity({**env, "WindowsSDKVersion": "10.0.26100.0\\"}),
                                toolcfg.compiler_identity(env))
            self.assertEqual(toolcfg.compiler_identity({"PATH": ""}), "cl 19.44.35229 sdk - files - tools -")


def stand_in(d):
    """A folder with a stand-in cl.cmd (cl(version) rewrites it) and the `set` output a vcvars run would print."""
    b = d / "bin"
    b.mkdir()
    keep = "".join(f"{k}={os.environ[k]}\n" for k in ("SYSTEMROOT", "COMSPEC", "PATHEXT") if k in os.environ)

    def cl(version):
        (b / "cl.cmd").write_text(f"@echo Microsoft (R) C/C++ Optimizing Compiler Version {version} for x86 1>&2\r\n",
                                  encoding="utf-8")
    cl("19.44.35219")
    return b, cl, lambda tools="14.44.35207": f"{keep}PATH={b}\nVCToolsVersion={tools}\nINCLUDE=i\nSECRET=no\n"


@unittest.skipUnless(os.name == "nt", "a stand-in cl.cmd")
class Cache(unittest.TestCase):  # finding D: each cached environment is keyed on vcvars, vcvars_args and cl's version
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="vc_", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_judge_and_readable_loads(self):
        for name, load, x86 in (("msvc_env.json", check.msvc_env, False),
                                ("msvc_env_x86.json", readable_check.msvc_env_x86, True)):
            d = self.d / name.split(".")[0]
            d.mkdir()
            path, (bindir, cl, out) = d / name, stand_in(d)
            calls, tools = [], ["14.44.35207"]

            def run(cmd, timeout, **kw):
                calls.append(cmd)
                return 0, out(tools[0]), None

            def bat(b):
                return b.replace("vcvars64", "vcvars32") if x86 else b
            other = BAT.replace(r"C:\VS", r"D:\VS2")  # another example install
            with mock.patch.object(paths, "run_bounded", run), mock.patch.object(paths, "MSVC_ENV", path), \
                    mock.patch.object(readable_check, "MSVC_ENV_X86", path):
                env = load({"vcvars": BAT})  # no cache: loaded, the command as before, the key written
                self.assertEqual(calls, [f'cmd /d /s /c ""{bat(BAT)}" >nul 2>&1 && set"'])
                self.assertNotIn("SECRET", env)
                key = json.loads(toolcfg.key_path(path).read_text(encoding="utf-8"))
                self.assertEqual((key["vcvars"], key["vcvars_args"]), (bat(BAT), ""))
                self.assertIn("19.44.35219", key["cl"])
                self.assertEqual(load({"vcvars": BAT}), env)  # an unchanged setting: the cache, the same env
                self.assertEqual(len(calls), 1)
                load({"vcvars": other})  # another vcvars: loaded again
                self.assertEqual(calls[-1], f'cmd /d /s /c ""{bat(other)}" >nul 2>&1 && set"')
                load({"vcvars": other})
                self.assertEqual(len(calls), 2)
                tools[0] = "14.38.33130"
                got = load({"vcvars": other, "vcvars_args": "-vcvars_ver=14.38"})  # a pin: loaded again, pinned
                self.assertEqual(calls[-1], f'cmd /d /s /c ""{bat(other)}" -vcvars_ver=14.38 >nul 2>&1 && set"')
                self.assertEqual(got["VCToolsVersion"], "14.38.33130")
                load({"vcvars": other, "vcvars_args": "-vcvars_ver=14.38"})
                self.assertEqual(len(calls), 3)
                load({"vcvars": other})  # the pin removed: loaded again, as before
                self.assertEqual((len(calls), calls[-1]), (4, f'cmd /d /s /c ""{bat(other)}" >nul 2>&1 && set"'))
                cl("19.44.35228")  # the same folder, another cl (a second machine's 14.44): loaded again
                load({"vcvars": other})
                self.assertEqual(len(calls), 5)
                self.assertIn("19.44.35228", json.loads(toolcfg.key_path(path).read_text(encoding="utf-8"))["cl"])
                toolcfg.key_path(path).unlink()  # a cache from before the key, or copied without it
                load({"vcvars": other})
                self.assertEqual(len(calls), 6)
                cache = json.loads(path.read_text(encoding="utf-8"))  # copied from a machine whose PATH is gone
                path.write_text(json.dumps({**cache, "PATH": str(d / "gone")}), encoding="utf-8")
                load({"vcvars": other})
                self.assertEqual(len(calls), 7)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["PATH"], str(bindir))

    def test_kept_cl_not_run_again(self):  # the key's version is taken while cl.exe is unchanged
        bindir, cl, out = stand_in(self.d)
        env = check.captured(out())
        key = toolcfg.env_key(BAT, {}, env)
        with mock.patch.object(toolcfg.subprocess, "run", side_effect=AssertionError("cl ran")):
            self.assertEqual(toolcfg.cl_version(env, key), key["cl"])
        cl("19.44.35228")
        self.assertIn("19.44.35228", toolcfg.cl_version(env, key))


class PortBuild(unittest.TestCase):  # port/tools/build.py: the kit's 32-bit environment, the same rule
    @unittest.skipUnless(os.name == "nt", "a stand-in cl.cmd")
    def test_cache(self):
        self.addCleanup(sys.path.__setitem__, slice(None), list(sys.path))  # build.py adds port/tools: restored, else
        sys.path.insert(0, str(TOOL / "port" / "tools"))                    # its regress.py shadows tools/regress.py
        import build
        import game_config
        d = pathlib.Path(tempfile.mkdtemp(prefix="vcp_", dir=TMP))
        self.addCleanup(shutil.rmtree, d, True)
        cache, bat, calls, cfg = d / "msvc_env_x86.json", d / "vcvars32.bat", [], {}
        bat.write_text("@rem\r\n", encoding="utf-8")
        _, cl, out = stand_in(d)

        def run(cmd, timeout, **kw):
            calls.append(cmd)
            return 0, out("14.38.33130")
        with mock.patch.object(build, "run", run), mock.patch.object(game_config, "toolchain", lambda: cfg), \
                mock.patch.object(game_config, "vcvars32", lambda: bat):
            build.msvc_env(cache)
            self.assertEqual(calls, [f'cmd /d /s /c ""{bat}" >nul 2>&1 && set"'])  # no pin: as before
            build.msvc_env(cache)
            self.assertEqual(len(calls), 1)  # the cache
            cfg["vcvars_args"] = "-vcvars_ver=14.38"
            build.msvc_env(cache)
            self.assertEqual(calls[-1], f'cmd /d /s /c ""{bat}" -vcvars_ver=14.38 >nul 2>&1 && set"')
            build.msvc_env(cache)
            self.assertEqual(len(calls), 2)
            cl("19.38.33146")  # another compiler: loaded again
            build.msvc_env(cache)
            self.assertEqual(len(calls), 3)


class Fails(unittest.TestCase):  # a vcvars run that exits nonzero, or misses the pin, loads nothing and caches nothing
    LOOKS_RIGHT = ("PATH=C:\\VS\\bin\nINCLUDE=i\nLIB=l\nVCToolsVersion=14.44.35207\n"  # an example environment
                   "WindowsSDKVersion=10.0.28000.0\\\n")

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="vcf_", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_judge_loads(self):
        for name, load in (("msvc_env.json", check.msvc_env), ("msvc_env_x86.json", readable_check.msvc_env_x86)):
            path = self.d / name
            for rc, args, says in ((1, None, "exited 1"),  # an incomplete pinned SDK: plausible variables, rc 1
                                   (1, "10.0.28000.0 -vcvars_ver=14.44", "fully installed"),
                                   (0, "-vcvars_ver=14.38", "not config"),  # rc 0, but another toolset loaded
                                   (0, "10.0.26100.0", "not config")):  # rc 0, but another SDK loaded
                cfg = {"vcvars": BAT, **({"vcvars_args": args} if args else {})}
                with mock.patch.object(paths, "run_bounded", lambda cmd, timeout, **kw: (rc, self.LOOKS_RIGHT, None)), \
                        mock.patch.object(paths, "MSVC_ENV", path), \
                        mock.patch.object(readable_check, "MSVC_ENV_X86", path):
                    with self.assertRaises(RuntimeError) as e:
                        load(cfg)
                self.assertIn(says, str(e.exception))
                self.assertFalse(path.exists() or toolcfg.key_path(path).exists())  # nothing cached

    def test_port_build(self):
        self.addCleanup(sys.path.__setitem__, slice(None), list(sys.path))  # build.py adds port/tools: restored
        sys.path.insert(0, str(TOOL / "port" / "tools"))
        import build
        import game_config
        cache, bat = self.d / "msvc_env_x86.json", self.d / "vcvars32.bat"
        bat.write_text("@rem\r\n", encoding="utf-8")
        with mock.patch.object(build, "run", lambda cmd, timeout, **kw: (1, self.LOOKS_RIGHT)), \
                mock.patch.object(game_config, "toolchain", lambda: {}), \
                mock.patch.object(game_config, "vcvars32", lambda: bat):
            with self.assertRaises(SystemExit) as e:
                build.msvc_env(cache)
        self.assertIn("exited 1", str(e.exception))
        self.assertFalse(cache.exists() or toolcfg.key_path(cache).exists())

    def test_ok(self):  # rc 0 and the pin held: the environment as printed
        env = check.captured(self.LOOKS_RIGHT)
        self.assertEqual(toolcfg.loaded(0, env, BAT, {"vcvars_args": "10.0.28000.0 -vcvars_ver=14.44"}), env)
        self.assertEqual(toolcfg.loaded(0, env, BAT, {}), env)


def kit(root, um=b"// um 1839\n"):
    """A stand-in Windows Kits/10 tree under root (the folders the compiler reads, and two it does not), and the INCLUDE
    and LIB a 64-bit vcvars run would set for it."""
    k = root / "Windows Kits" / "10"
    files = {"include/10.0.28000.0/ucrt/stdio.h": b"// ucrt\n", "include/10.0.28000.0/um/windows.h": um,
             "include/10.0.28000.0/shared/sdkddkver.h": b"// shared\n", "include/10.0.28000.0/winrt/x.h": b"x",
             "lib/10.0.28000.0/ucrt/x64/ucrt.lib": b"lib", "lib/10.0.28000.0/um/x64/kernel32.lib": b"k32",
             "lib/10.0.28000.0/um/x86/kernel32.lib": b"k32 x86"}
    for rel, data in files.items():
        f = k / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    inc = ";".join(str(k / "include" / "10.0.28000.0" / d) for d in ("ucrt", "um", "shared", "winrt"))
    lib = ";".join([str(root / "Windows Kits" / "NETFXSDK" / "4.8" / "lib" / "um" / "x64")] +
                   [str(k / "lib" / "10.0.28000.0" / d / "x64") for d in ("ucrt", "um")])
    return {"INCLUDE": inc + ";", "LIB": lib, "WindowsSDKVersion": "10.0.28000.0\\", "PATH": ""}


class SdkFiles(unittest.TestCase):  # finding B: the SDK's exact build, by its files
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="sdk_", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)
        toolcfg._SDK.clear()

    def test_folders(self):  # what the compiler reads: not winrt, not NETFXSDK, not another architecture's libraries
        self.assertEqual([n for n, _ in toolcfg.sdk_folders(kit(self.d))],
                         ["include/10.0.28000.0/shared", "include/10.0.28000.0/ucrt", "include/10.0.28000.0/um",
                          "lib/10.0.28000.0/ucrt/x64", "lib/10.0.28000.0/um/x64"])
        self.assertEqual((toolcfg.sdk_folders({"PATH": ""}), toolcfg.sdk_hash({"PATH": ""})), ([], "-"))

    def test_builds_differ(self):  # two builds of 10.0.28000.0; two install folders with the same files
        a, b, c = (kit(self.d / "a"), kit(self.d / "b", um=b"// um 2114\n"), kit(self.d / "c"))
        self.assertNotEqual(toolcfg.sdk_hash(a), toolcfg.sdk_hash(b))
        self.assertEqual(toolcfg.sdk_hash(a), toolcfg.sdk_hash(c))
        self.assertNotIn(str(self.d), json.dumps(toolcfg.sdk_scan(a)))
        with mock.patch.object(toolcfg, "cl_version", lambda env, known=None: "cl 19.44.35229"):
            self.assertNotEqual(toolcfg.compiler_identity(a), toolcfg.compiler_identity(b))
            self.assertIn(f" files {toolcfg.sdk_hash(a)} ", toolcfg.compiler_identity(a))

    def test_key_keeps_it(self):  # a key's hash is taken while the listing holds; a changed file loads again
        env = kit(self.d)
        cache = self.d / "msvc_env.json"
        cache.write_text(json.dumps(env), encoding="utf-8")
        with mock.patch.object(toolcfg, "cl_version", lambda env, known=None: "cl 19.44.35229"):
            toolcfg.write_key(cache, env, BAT, {})
            key = json.loads(toolcfg.key_path(cache).read_text(encoding="utf-8"))
            self.assertEqual(key["sdk"], toolcfg.sdk_hash(env))
            self.assertTrue(toolcfg.cache_current(cache, env, BAT, {}))
            toolcfg._SDK.clear()
            with mock.patch("builtins.open", side_effect=AssertionError("the SDK was read")):
                self.assertEqual(toolcfg.sdk_hash(env, key), key["sdk"])
            um = self.d / "Windows Kits" / "10" / "include" / "10.0.28000.0" / "um" / "windows.h"
            st = um.stat()
            um.write_bytes(b"// um 2114\n")  # updated in place: same name, same size
            os.utime(um, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
            self.assertFalse(toolcfg.cache_current(cache, env, BAT, {}))
            self.assertNotEqual(toolcfg.sdk_hash(env, key), key["sdk"])
            del key["sdk"]  # a key written before the SDK's hash: loaded again
            toolcfg.key_path(cache).write_text(json.dumps(key), encoding="utf-8")
            self.assertFalse(toolcfg.cache_current(cache, env, BAT, {}))


@unittest.skipUnless(os.name == "nt", "a stand-in cl.cmd")
class Stamp(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp(prefix="cl_", dir=TMP))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.env = self.d / "msvc_env.json"
        keep = {k: os.environ[k] for k in ("SYSTEMROOT", "COMSPEC", "PATHEXT") if k in os.environ}
        self.env.write_text(json.dumps({**keep, "PATH": str(self.bin)}), encoding="utf-8")
        p = mock.patch.object(paths, "MSVC_ENV", self.env)
        p.start()
        self.addCleanup(p.stop)

    def cl(self, version):
        (self.bin / "cl.cmd").write_text(
            f"@echo Microsoft (R) C/C++ Optimizing Compiler Version {version} for x64 1>&2\r\n"
            "@echo usage: cl [ option... ] filename... [ /link linkoption... ] 1>&2\r\n", encoding="utf-8")

    def test_identity_in_stamp(self):
        self.assertEqual(verdict_memo.compiler_identity(self.d / "none.json"), "-")
        self.assertEqual(verdict_memo.compiler_identity(self.env), "- sdk - files - tools -")  # no cl on its PATH
        self.cl("19.44.35207.1")
        env = json.loads(self.env.read_text(encoding="utf-8"))
        self.env.write_text(json.dumps({**env, "WindowsSDKVersion": "10.0.28000.0\\", "VCToolsVersion": "14.44.35207"}),
                            encoding="utf-8")
        self.assertEqual(verdict_memo.compiler_identity(self.env),
                         "Microsoft (R) C/C++ Optimizing Compiler Version 19.44.35207.1 for x64 sdk 10.0.28000.0 "
                         "files - tools 14.44.35207")
        before = verdict_memo.judge_stamp()
        self.assertEqual(verdict_memo.judge_stamp(), before)
        self.cl("19.38.33130.0")  # another compiler at the same path (its size and mtime change)
        self.assertIn("19.38.33130.0", verdict_memo.compiler_identity(self.env))
        self.assertNotEqual(verdict_memo.judge_stamp(), before)


if __name__ == "__main__":
    unittest.main()
