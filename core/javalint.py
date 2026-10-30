"""Rules for the only oracle code the watcher agent may change: ghidra_scripts/TcInputs.java, plus hint files.

TcInputs runs inside Ghidra with full Java privileges, so it is checked with allow-lists (not deny-lists)
before it is ever compiled: it may only compute input values. It cannot do I/O, reach Ghidra or script state,
use reflection, threads or lambdas, reseed the random generator, or shadow anything the locked oracle uses.
Style constraints that keep the check sound: one variable per declaration; library calls are written qualified
(x.name(...)); unqualified calls reach only methods declared here; nested classes hold fields only.
"""
import re

FIXED_MEMBERS = {"Vec", "niceFloat", "scrPtr", "word", "intArg", "scratchBytes", "makeVec", "applyHints",
                 "repairVec"}
NEW_MEMBER_RE = re.compile(r"(gen|Gen)[A-Z0-9]\w*|GEN_[A-Z0-9_]+")
TYPES = {"TcInputs", "TcBase", "Vec", "Proto", "Param", "List", "ArrayList", "Map", "HashMap", "TreeMap",
         "LinkedHashMap", "Set", "HashSet", "TreeSet", "Arrays", "Collections", "Math", "Float", "Integer", "Long",
         "Short", "Byte", "Double", "Boolean", "Character", "String", "StringBuilder", "Iterator", "IllegalArgumentException"}
BASE_CONSTANTS = {"RAM_LO", "RAM_HI", "SCR", "SCR_SIZE", "SP", "SENTINEL", "STACK_LO", "STEP_LIMIT"}
CALLS = {
    # random numbers (rnd is seeded by TcPrepare)
    "nextInt", "nextFloat", "nextLong", "nextBoolean", "nextDouble",
    # numbers and strings
    "floatToIntBits", "intBitsToFloat", "floatToRawIntBits", "doubleToLongBits", "longBitsToDouble",
    "doubleToRawLongBits", "abs", "min", "max", "floorMod", "floorDiv",
    "parseInt", "parseLong", "parseUnsignedLong", "decode", "valueOf", "intValue", "longValue", "toHexString",
    "charAt", "length", "matches", "substring", "startsWith", "endsWith", "split", "trim", "isEmpty", "equals",
    "toLowerCase", "indexOf", "append", "toString", "format",
    # collections and arrays
    "add", "get", "set", "size", "put", "remove", "contains", "containsKey", "getOrDefault", "keySet", "values",
    "entrySet", "getKey", "getValue", "addAll", "clear", "clone", "copyOf", "copyOfRange", "fill", "asList", "sort",
}
KEYWORDS = {"abstract", "class", "extends", "static", "final", "int", "long", "byte", "short", "boolean", "float",
            "double", "char", "void", "return", "if", "else", "for", "while", "do", "switch", "case", "default",
            "break", "continue", "new", "null", "true", "false", "public", "private", "protected", "throw",
            "instanceof"}
FORBIDDEN_WORDS = {"currentProgram", "currentAddress", "currentLocation", "currentSelection", "currentHighlight",
                   "state", "monitor", "writer", "errorWriter", "sourceFile", "mem", "listing", "fm", "fn", "body",
                   "fscaSin", "fscaCos", "angleToFraction", "blockIndex", "stepLoads", "super", "this",
                   "java", "javax", "ghidra", "sun", "jdk", "org", "com", "native", "synchronized", "volatile",
                   "transient", "interface", "enum", "implements", "try", "catch", "finally", "throws", "assert",
                   "goto", "const", "strictfp", "var", "record", "yield"}
HEADER_IMPORTS = ["import java.util.*;"]
DECL_RE = re.compile(r"\b(?:int|long|byte|short|boolean|float|double|char|[A-Z]\w*(?:<[^<>]*(?:<[^<>]*>)?[^<>]*>)?)"
                     r"((?:\s*\[\s*\])*)\s+([A-Za-z_]\w*)\s*(?=[=;:,)])")


def strip(src):
    """Remove comments, string and char literals (they cannot hold code), keeping line structure."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            seg = src[i:n if j < 0 else j + 2]
            out.append("\n" * seg.count("\n"))
            i = n if j < 0 else j + 2
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out.append(" 0 ")
            i = j + 1
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def structure(code):
    """Class-level member names and structural problems (initializer blocks, methods in nested classes,
    several variables in one class-level declaration)."""
    names, problems, depth, seg, in_nested = [], [], 0, "", False
    for ch in code:
        if ch == "{":
            s = " ".join(seg.split())
            if depth == 1:
                m = re.search(r"\bclass\s+(\w+)", s)
                call = re.search(r"(\w+)\s*\(", s)
                if m:
                    names.append(m.group(1))
                    in_nested = True
                elif call:
                    names.append(call.group(1))
                    in_nested = False
                else:
                    problems.append(f"initializer block or unknown declaration at class level: '{s[:60]}'")
            elif depth == 2 and in_nested:
                problems.append(f"nested classes may only hold fields, found a block: '{s[:60]}'")
            seg = ""
            depth += 1
        elif ch == "}":
            depth -= 1
            seg = ""
        elif ch == ";" and depth == 1:
            s = " ".join(seg.split())
            head = s.split("=")[0]
            if "," in re.sub(r"<[^>]*>", "", head) or re.search(r"=[^=]*,\s*[A-Za-z_]\w*\s*=", s):
                problems.append(f"declare one field per statement: '{s[:60]}'")
            m = re.search(r"(\w+)\s*(\[\s*\])*\s*$", head)
            if m:
                names.append(m.group(1))
            seg = ""
        elif depth == 1:
            seg += ch
    return names, problems


def lint_inputs(src):
    """Violations in TcInputs.java (empty list = acceptable)."""
    errs = []
    if "\\u" in src:
        errs.append("unicode escapes (\\u) are not allowed anywhere: Java decodes them before parsing")
    if any(ord(c) > 126 or (ord(c) < 32 and c not in "\n\r\t") for c in src):
        errs.append("only printable ASCII is allowed")
    imports = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("import ")]
    if imports != HEADER_IMPORTS:
        errs.append(f"imports must be exactly {HEADER_IMPORTS}, found {imports}")
    code = "\n".join("" if ln.strip() in HEADER_IMPORTS else ln for ln in strip(src).split("\n"))
    if not re.search(r"public\s+abstract\s+class\s+TcInputs\s+extends\s+TcBase\s*\{", code):
        errs.append("the class must stay 'public abstract class TcInputs extends TcBase'")
    if len(re.findall(r"\bclass\b", code)) != len(re.findall(r"\bclass\s+\w+(\s+extends\s+TcBase)?\s*\{", code)):
        errs.append("'class' may only appear in class declarations")
    if len(re.findall(r"\bextends\b", code)) != 1:
        errs.append("only TcInputs itself may extend a class")
    for tok in ("->", "::", "@", "...", "\\"):
        if tok in code:
            errs.append(f"'{tok}' is not allowed (no lambdas, method references, annotations, varargs)")
    if re.search(r"\brnd\s*=[^=]", code) or re.search(r"\bRandom\b", code):
        errs.append("rnd must not be reassigned and Random must not be used: TcPrepare seeds rnd")
    names, problems = structure(code)
    errs += problems
    nested = set(re.findall(r"\bclass\s+(\w+)", code)) - {"TcInputs"}
    for nm in names:
        if nm != "TcInputs" and nm not in FIXED_MEMBERS and not NEW_MEMBER_RE.fullmatch(nm):
            errs.append(f"class-level member '{nm}' must be one of the fixed entry points or be named gen*/Gen*/GEN_*")
    for nm in nested:
        if nm != "Vec" and not re.fullmatch(r"Gen[A-Z0-9]\w*", nm):
            errs.append(f"nested class '{nm}' must be named Gen*")
    local = {m.group(2) for m in DECL_RE.finditer(code)}
    bare_ok = KEYWORDS | TYPES | BASE_CONSTANTS | {"rnd", "hex"} | set(names) | nested | local
    for m in re.finditer(r"(\.\s*)?\b([A-Za-z_]\w*)\b(\s*\()?", code):
        dotted, w, call = m.group(1), m.group(2), m.group(3)
        if w in FORBIDDEN_WORDS:
            errs.append(f"'{w}' is not allowed")
        elif dotted and call:
            if w not in CALLS:
                errs.append(f"call to '.{w}()' is not on the allow-list of library calls")
        elif dotted:
            continue  # field of a value this file declared (Vec, Proto, Param, arrays)
        elif call and w not in KEYWORDS and w not in TYPES and w not in nested:
            if w not in names and w != "hex":
                errs.append(f"call to '{w}()': unqualified calls may only reach methods declared here (or hex)")
        elif w not in bare_ok:
            errs.append(f"'{w}' is not declared in this file or on the allow-list")
    return sorted(set(errs))


HINT_RE = re.compile(r"#.*|ptr (r[4-7]|0x[0-9a-f]{8})(\+0x[0-9a-f]{1,5})*"
                     r"|range (r[0-9]|r1[0-4]|gbr) (0x[0-9a-f]+|-?\d+) (0x[0-9a-f]+|-?\d+)|gen[A-Za-z0-9]* [\w+ .x-]{0,120}")


def lint_hints(text):
    """Violations in an oracle/hints/<addr>.txt file: base hint forms, plus gen<name> lines for hint forms a
    promoted TcInputs adds itself. Hints are plain data; TcInputs decides what they mean."""
    errs = []
    if len(text) > 4000:
        errs.append("hints file is larger than 4000 bytes")
    if any(ord(c) > 126 or (ord(c) < 32 and c not in "\n\r\t") for c in text):
        errs.append("only printable ASCII is allowed")
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if s and not HINT_RE.fullmatch(s):
            errs.append(f"line {i}: '{s[:60]}' is not a hint (ptr LOC | range rN LO HI | gen<name> ARGS | # comment)")
    return errs
