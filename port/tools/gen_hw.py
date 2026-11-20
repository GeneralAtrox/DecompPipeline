"""gen_port's forwarders for game.json hardware functions that the registry leaves unnamed: readable C calls such a
function by its raw name fn_ADDR, and the forwarder passes the call on by argument words to the game's or kit's
definition, as the dispatch table calls it."""
import re

WIDE = re.compile(r"\b(float|double|u64|s64|f32|f64|long long)\b")


def hw_forwarders(readable, hw, names, protos):
    """For each game.json hardware function at an address the registry leaves unnamed but a readable file calls by its
    raw name fn_ADDR: fn_ADDR with its raw
    prototype, calling the game's (or kit's) definition by argument words, as the dispatch table calls it."""
    out = []
    for x, n in sorted(hw.items()):
        if names.get(x) != f"fn_{x}" or n == f"fn_{x}":
            continue
        ret, name, params = readable.parse_proto(protos[x])
        if WIDE.search(ret) or any(WIDE.search(t) for t, _ in params):
            raise SystemExit(f"game.json hardware {x} ({n}): readable C calls it as {protos[x]}; a forwarder by "
                             "argument words cannot pass a 64-bit or float value: name it in the registry")
        words = ", ".join("u32" for _ in params) or "void"
        args = ", ".join(f"(u32)(uintptr_t){a}" for _, a in params)
        call = f"((u32 (*)({words})){n})({args})"
        body = [f"u32 {n}(void); /* the game's definition, called by words */", f"{protos[x].strip().rstrip(';')}", "{"]
        body.append(f"    {call};" if ret.strip() == "void" else f"    return ({ret.strip()})(uintptr_t){call};")
        out.append(body + ["}"])
    return out
