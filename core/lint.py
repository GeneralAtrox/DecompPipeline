"""Anti-cheat rules for the agent's C file (rules R1-R10 in TASK.md).

The agent may only write plain C against tc_rt.h: no other includes, no inline asm, no globals,
no runtime internals, no hard-coded test data. Any violation fails the check before it is built.
"""
import re


BANNED_WORDS = {
    "asm": "inline assembly", "__asm": "inline assembly", "_asm": "inline assembly", "__asm__": "inline assembly",
    "extern": "extern declarations", "volatile": "volatile", "__declspec": "__declspec", "__pragma": "pragmas",
    "_Pragma": "pragmas", "__try": "SEH", "__except": "SEH", "__finally": "SEH", "__leave": "SEH",
    "setjmp": "setjmp", "longjmp": "longjmp", "_Thread_local": "thread-local storage", "__thread": "thread-local storage",
    "double": "double precision (use f32 only)", "__LINE__": "__LINE__", "__COUNTER__": "__COUNTER__",
    "__FILE__": "__FILE__", "__DATE__": "__DATE__", "__TIME__": "__TIME__",
}
BANNED_PREFIXES = ("tcrt_", "__builtin", "_Interlocked", "__readgs", "__readfs")


def strip_comments(src):
    """Remove comments; keep string/char literals (they are linted separately)."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def lint(src, meta):
    errors = []
    if len(src) > 60000:
        errors.append("work.c is larger than 60 KB")
    code = strip_comments(src)
    lines = code.splitlines()
    includes = 0
    for ln in lines:
        s = ln.strip()
        if not s.startswith("#"):
            continue
        if re.fullmatch(r'#\s*include\s+"tc_rt\.h"', s):
            includes += 1
        else:
            errors.append(f"R2: forbidden preprocessor line: {s[:60]}")
    if includes != 1:
        errors.append('R2: work.c must contain exactly one line #include "tc_rt.h"')
    if any('"' in ln for ln in lines if not ln.strip().startswith("#")):
        errors.append("R6: string literals are not allowed")
    for word, why in BANNED_WORDS.items():
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", code):
            errors.append(f"R6/R9: '{word}' is not allowed ({why})")
    for ident in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)):
        if ident.startswith(BANNED_PREFIXES):
            errors.append(f"R6: identifier '{ident}' is not allowed")
        if ident.startswith("fn_") and ident != meta["name"]:
            errors.append(f"R5: '{ident}' - call other functions only through tc_call/tc_callf")

    # Top-level structure: no global variables, only static helpers plus the one exported function.
    depth, stmt, top = 0, [], []
    for ch in code:
        if ch == "{":
            if depth == 0:
                top.append(("open", "".join(stmt).strip()))
                stmt = []
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                stmt = []
                top.append(("close", ""))
        elif depth == 0:
            if ch == ";":
                top.append(("decl", "".join(stmt).strip()))
                stmt = []
            else:
                stmt.append(ch)
        if depth < 0:
            errors.append("unbalanced braces")
            break
    exported = 0
    last_open = None  # text of the most recent top-level "{" opener
    prev_kind = None
    for kind, text in top:
        text = re.sub(r"#.*", "", text).strip()
        after_close = prev_kind == "close"
        prev_kind = kind
        if kind == "close":
            continue
        if kind == "open":
            last_open = text
            if text.startswith(("typedef", "struct", "union", "enum")):
                continue
            m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
            if not m or "=" in text.split("(")[0]:
                errors.append(f"R6: global variable or initializer at top level: {text[:60]}")
                continue
            if m.group(1) == meta["name"]:
                exported += 1
                if norm_sig(text) != norm_sig(meta["signature"]):
                    errors.append(f"R3: signature must be exactly: {meta['signature']}")
            elif not re.match(r"static\b", text):
                errors.append(f"R3: helper function '{m.group(1)}' must be static")
            continue
        if after_close and last_open is not None:
            if last_open.startswith("typedef"):
                continue  # the name of a typedef'd struct/union/enum
            if last_open.startswith(("struct", "union", "enum")):
                if text:
                    errors.append(f"R6: global variable of struct type: {text[:60]}")
                continue
        if not text or re.match(r"typedef\b", text):
            continue
        if re.search(r"\(\s*\*", text):
            errors.append(f"R6: global function-pointer variable: {text[:60]}")
            continue
        if "(" in text and "=" not in text:
            continue  # function prototype
        if re.fullmatch(r"(struct|union|enum)\s+[A-Za-z_][A-Za-z0-9_]*", text):
            continue  # forward declaration
        errors.append(f"R6: global/static variable at top level: {text[:60]}")
    if exported != 1:
        errors.append(f"R3: work.c must define {meta['name']} exactly once")
    # No static storage inside functions.
    for m in re.finditer(r"(?<![A-Za-z0-9_])static(?![A-Za-z0-9_])", code):
        if _depth_at(code, m.start()) > 0:
            errors.append("R6: static local variables are not allowed")
            break
    for m in re.finditer(r"=\s*\{", code):
        end = _matching_brace(code, m.end() - 1)
        if end > 0 and code[m.end():end].count(",") >= 32:
            errors.append("R6: initializer list with more than 32 elements (lookup tables are not allowed)")
            break
    return sorted(set(errors))


def _depth_at(code, pos):
    return code[:pos].count("{") - code[:pos].count("}")


def _matching_brace(code, start):
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def norm_sig(s):
    s = re.sub(r"\s+", " ", s.strip())
    return re.sub(r"\s*([(),*])\s*", r"\1", s)
