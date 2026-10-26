"""Translate Too Human's judge set with xenon-translate (the live workspace's harness signatures, the same call as its
tools/dp_judge.py: structured, optimised, return kinds), C into th_c/ADDR.c. Nothing is judged here. Read only:
the workspace and xenon-translate are not written (its returns cache is a copy, th_cache/).
    python gen_th.py XENON_TRANSLATE WORKSPACE JUDGE_SET
(xenon-translate's folder, Too Human's decomp workspace, its translator_judge_set.txt)"""
import importlib, json, pathlib, sys, time
XT = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(XT))  # the user's own translator, from its folder (not a package to install)
returns, workspace = (importlib.import_module(f"xenontranslate.{m}") for m in ("returns", "workspace"))
tr = importlib.import_module("xenontranslate.translate")
Signature, TranslateError, translate_function = tr.Signature, tr.TranslateError, tr.translate_function
WS = pathlib.Path(sys.argv[2])
here = pathlib.Path(__file__).resolve().parent
addrs = [pathlib.Path(l.strip()).name for l in open(sys.argv[3])
         if l.strip() and not l.startswith("#")]
t0 = time.time()
w = workspace.Workspace(WS)
kinds = returns.analyse(w, here / "th_cache")
print(f"workspace and return kinds: {time.time() - t0:.0f} s", flush=True)
out = here / "th_c"
out.mkdir(exist_ok=True)
rows = {}
for a in addrs:
    meta = json.loads((WS / "harness" / a / "meta.json").read_text(encoding="utf-8"))
    try:
        ad = int(a, 16)
        src, ft = translate_function(w.dol, ad, Signature.from_meta(meta), w.facts, w.bodies[ad], comments=False,
                                     structured=True, optimise=True, returns=kinds)
        (out / f"{a}.c").write_text(src, encoding="utf-8")
        rows[a] = "ok"
    except TranslateError as e:
        rows[a] = f"NOTRANSLATE {e}"[:200]
    except Exception as e:
        rows[a] = f"CRASH {type(e).__name__}: {e}"[:200]
json.dump(rows, open(here / "th_translate.json", "w"), indent=1)
print(f"{sum(v == 'ok' for v in rows.values())} of {len(rows)} translated, {time.time() - t0:.0f} s")
