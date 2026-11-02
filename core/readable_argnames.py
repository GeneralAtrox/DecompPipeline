"""The readable check's wording of its stack-word differences, kept apart because readable_check.py is over 400 lines,
so it may not grow. Nearest existing: readable_check.callee_storage, which it is handed, and which finds the parameter.
Text only: nothing here decides a verdict."""
import re


def stack_arg_names(meta, harness, diffs, callee_storage):
    """The readable agent's wording of a compared stack word: args.s[N] is the raw stage's. In readable C the word is
    a declared parameter of the callee (Table_BuildLookup's third is s0), and two functions spent their attempts adding
    arguments the check never reads. Only the text changes."""
    def named(m):
        callee = next((c for c in meta["callees"] if int(c["addr"], 16) == int(m[1], 16)), None)
        where = callee_storage(harness, callee) if callee and callee.get("signature") else None
        names = list(where or {})
        for k, name in enumerate(names):
            if where[name] == f"stack+{4 * int(m[2])}":
                return (f"{m[0][:m.start(3) - m.start(0)]}(stack word @({4 * int(m[2])},r15): parameter {k + 1} of the "
                        f"callee's prototype, so pass the value as that argument; no extra argument reaches it)")
        return m[0]
    return [re.sub(r"call #\d+ to 0x([0-9a-f]{8}): argument s(\d+) (\(stack word @\(\d+,r15\), args\.s\[\d+\]\))", named, d)
            for d in diffs]
