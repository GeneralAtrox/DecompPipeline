"""gc-tx: pick a stratified sample of unprepared Skies functions (one address per line) to widen the translator's test set.

Nearest existing: tools/prepare_list.py - it prepares a given list; it does not choose one."""
import json, os, random
ws = os.path.expanduser('~/dpwork/gc/skies/decomp-gekko')
fs = json.load(open(ws + '/state/catalog.json'))['functions']
have = set(os.listdir(ws + '/harness'))
pool = [f for f in fs if f['addr'] not in have and 6 <= f['insns'] <= 700 and f['callees'] <= 30]
rng = random.Random(20261030)
strata = [
 ('fpu_special', lambda f: f['fpu_special'] > 0, 25), ('fp_mode', lambda f: f['fp_mode'] > 0, 15),
 ('jump_tables', lambda f: f['jump_tables'] > 0, 25), ('indirect', lambda f: f['indirect_calls'] > 0, 25),
 ('loops_fpu', lambda f: f['loops'] > 0 and f['fpu'] > 0, 25), ('hw', lambda f: f['hw_refs'] > 0, 15),
 ('sys_ops', lambda f: f['sys_ops'] > 0, 15), ('big', lambda f: f['insns'] >= 250, 25),
 ('plain', lambda f: True, 30)]
seen, out = set(), []
for name, pred, n in strata:
    c = [f['addr'] for f in pool if pred(f) and f['addr'] not in seen]
    rng.shuffle(c)
    for a in c[:n]:
        seen.add(a); out.append(a)
    print(name, len(c), min(n, len(c)))
open(os.path.expanduser('~/dpwork/land/gc-tx/l/sample.txt'), 'w').write('\n'.join(sorted(out)) + '\n')
print(len(out))
