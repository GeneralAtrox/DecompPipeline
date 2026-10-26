"""AES-128 decryption (FIPS-197) for XEX2 payloads: one ECB block (the file key) and CBC (the payload).

The S-boxes are computed from their definition (the multiplicative inverse in GF(2^8), then the affine transform),
not copied from a table. decrypt_cbc uses Windows' own AES (CNG, bcrypt.dll) when it is there, because a 20 MB
payload takes minutes in pure Python; tests check that both paths agree.
"""
import ctypes
import sys


def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) & 0xFF if a & 0x100 else a


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _sboxes():
    inv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if _mul(a, b) == 1:
                inv[a] = b
                break
    sbox = []
    for a in range(256):
        x = inv[a]
        s = x
        for k in range(1, 5):
            s ^= ((x << k) | (x >> (8 - k))) & 0xFF
        sbox.append(s ^ 0x63)
    isbox = [0] * 256
    for i, s in enumerate(sbox):
        isbox[s] = i
    return sbox, isbox


SBOX, INV_SBOX = _sboxes()
_M9, _M11, _M13, _M14 = ([_mul(a, k) for a in range(256)] for k in (9, 11, 13, 14))


def expand_key(key):
    """The 11 round keys of an AES-128 key, each a list of 16 bytes."""
    if len(key) != 16:
        raise ValueError("AES-128 needs a 16-byte key")
    w = [list(key[4 * i:4 * i + 4]) for i in range(4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]


def _decrypt_block(rk, block):
    s = [b ^ k for b, k in zip(block, rk[10])]
    for rnd in range(9, -1, -1):
        # inverse ShiftRows (state is column-major: byte i is row i % 4, column i // 4), inverse SubBytes
        s = [INV_SBOX[s[i % 4 + 4 * ((i // 4 - i % 4) % 4)]] for i in range(16)]
        s = [b ^ k for b, k in zip(s, rk[rnd])]
        if rnd:
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                out += [_M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3], _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                        _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3], _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]]
            s = out
    return bytes(s)


def decrypt_ecb_block(key, block):
    return _decrypt_block(expand_key(key), block)


def decrypt_cbc_python(key, iv, data):
    if len(data) % 16:
        raise ValueError("CBC data must be a whole number of blocks")
    rk = expand_key(key)
    out, prev = bytearray(), iv
    for o in range(0, len(data), 16):
        c = data[o:o + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(rk, c), prev))
        prev = c
    return bytes(out)


def decrypt_cbc_windows(key, iv, data):
    """AES-128-CBC through bcrypt.dll; None when it is not available."""
    if sys.platform != "win32":
        return None
    try:
        bc = ctypes.WinDLL("bcrypt.dll")
    except OSError:
        return None
    alg, hkey = ctypes.c_void_p(), ctypes.c_void_p()
    if bc.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0):
        return None
    try:
        mode = ctypes.create_unicode_buffer("ChainingModeCBC")
        if bc.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), mode, ctypes.sizeof(mode), 0):
            return None
        kb = ctypes.create_string_buffer(bytes(key), len(key))
        if bc.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0, kb, len(key), 0):
            return None
        try:
            ivb = ctypes.create_string_buffer(bytes(iv), 16)
            inb = ctypes.create_string_buffer(bytes(data), len(data))
            outb = ctypes.create_string_buffer(len(data))
            got = ctypes.c_ulong()
            if bc.BCryptDecrypt(hkey, inb, len(data), None, ivb, 16, outb, len(data), ctypes.byref(got), 0):
                return None
            return outb.raw[:got.value]
        finally:
            bc.BCryptDestroyKey(hkey)
    finally:
        bc.BCryptCloseAlgorithmProvider(alg, 0)


def decrypt_cbc(key, iv, data):
    if len(data) % 16:
        raise ValueError("CBC data must be a whole number of blocks")
    fast = decrypt_cbc_windows(key, iv, data) if len(data) > 4096 else None
    return fast if fast is not None else decrypt_cbc_python(key, iv, data)
