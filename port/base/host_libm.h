/* Forced into every host C++ file (CMakeLists.txt /FI, before any header): keeps the 32-bit C runtime's float maths
 * apart from the game's. On x86 the CRT has no sinf, cosf, ... to link: <math.h> / <cmath> define them inline, and a
 * C++ file that calls one without inlining it (the level editor, Dear ImGui) emits it as a COMDAT under the plain
 * name, which clashes with runtime/port_libm.c's definitions, or a game's own judged ones (PORT_OWN_CRT_name):
 * LNK2005. These names make the CRT's inline copies the host's own, so the game's C keeps calling port_libm.c's
 * (or its own) exactly as before and host C++ keeps the CRT's (same arithmetic: each is (float)f((double)x)).
 * tests/test_host_libm.py links a C++ file that uses each beside port_libm.c, with and without this file. */
#ifndef PORT_HOST_LIBM_H
#define PORT_HOST_LIBM_H
#if defined _M_IX86 && defined __cplusplus
#define sinf host_crt_sinf
#define cosf host_crt_cosf
#define tanf host_crt_tanf
#define acosf host_crt_acosf
#define atan2f host_crt_atan2f
#define log10f host_crt_log10f
#define powf host_crt_powf
#define floorf host_crt_floorf
#define ceilf host_crt_ceilf
#define sqrtf host_crt_sqrtf
#define ldexpf host_crt_ldexpf
#endif
#endif
