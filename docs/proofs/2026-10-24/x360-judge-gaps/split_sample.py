"""split_sample.py WS N SEED KEYS OUT: mutation_audit's own draw (n of the workspace's matched, seeded), split into KEYS keys th0.. for runs that can stop between keys."""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))
import mutation_audit as ma
ws, n, seed, k, out = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
d = ma.draw(ws, n, seed)
pathlib.Path(out).write_text(json.dumps({f"th{i}": d[i::k] for i in range(k)}, indent=1, sort_keys=True) + "\n")
print(len(d), "drawn of", len(ma.matched(ws)))
