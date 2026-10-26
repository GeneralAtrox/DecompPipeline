"""Make a private, frozen copy of one workspace for the compiler-update proof.

    python setup_mirror.py --ws LIVE --mirror DIR --sample sample.json [--copy dtk --copy readable/include ...]

sample.json: {"raw": [{"addr", "kind", "src"}], "readable": [...]} (readable optional). The copy holds the project
file, the binary, the state the judge reads (no ledgers, logs, backups or cached compiler environment), harness/ and
packets/ of the sampled functions, the --copy folders, and a snapshot of every sampled C file (src/ADDR.c, and
readable/src/ADDR.c for the readable judge). Both phases judge this same copy, so only the compiler can differ.
Refuses to overwrite an existing copy."""
import argparse
import json
import pathlib
import shutil

SKIP = ("ledger", "usage", "events", "msvc_env", ".before", "before-", ".lock", ".log")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True)
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--copy", action="append", default=[])
    a = ap.parse_args()
    ws, dst = pathlib.Path(a.ws), pathlib.Path(a.mirror)
    if dst.exists():
        raise SystemExit(f"{dst} exists: the copy is made once, before the baseline")
    sample = json.loads(pathlib.Path(a.sample).read_text(encoding="utf-8"))
    dst.mkdir(parents=True)
    proj = json.loads((ws / "project.json").read_text(encoding="utf-8"))
    binary = (ws / proj["binary"]).resolve()
    (dst / "bin").mkdir()
    shutil.copyfile(binary, dst / "bin" / binary.name)
    proj["binary"] = f"bin/{binary.name}"
    (dst / "project.json").write_text(json.dumps(proj, indent=1), encoding="utf-8")
    for f in ("image.json", "catalog_inputs.json", "code_ranges.txt"):
        if (ws / f).is_file():
            shutil.copyfile(ws / f, dst / f)
    st = dst / "state"
    st.mkdir()
    for f in (ws / "state").iterdir():
        if f.is_file() and not any(s in f.name for s in SKIP) and f.stat().st_size < 64 << 20:
            shutil.copyfile(f, st / f.name)
    for sub in ("fsca", "target_facts"):
        if (ws / "state" / sub).is_dir():
            shutil.copytree(ws / "state" / sub, st / sub)
    for c in a.copy:
        shutil.copytree(ws / c, dst / c)
    for kind, out in (("raw", dst / "src"), ("readable", dst / "readable" / "src")):
        out.mkdir(parents=True, exist_ok=True)
        for it in sample.get(kind, []):
            ad = it["addr"]
            shutil.copyfile(it["src"], out / f"{ad}.c")
            for sub in ("harness", "packets"):
                s, d = ws / sub / ad, dst / sub / ad
                if s.is_dir() and not d.exists():
                    shutil.copytree(s, d)
            it["live_src"], it["src"] = it["src"], str(out / f"{ad}.c")
    (dst / "sample.json").write_text(json.dumps(sample, indent=1), encoding="utf-8")
    print(f"{dst}: {len(sample.get('raw', []))} raw, {len(sample.get('readable', []))} readable")


if __name__ == "__main__":
    main()
