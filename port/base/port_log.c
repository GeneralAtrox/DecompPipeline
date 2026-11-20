/* The port's log: every line goes to stdout and to port.log. Other processes may read port.log while the port
 * writes it (Phangrade sends new lines to co-op partners), so it is shared for reading and flushed each line. */
#include <share.h>
#include <stdarg.h>
#include <string.h>
#include "base.h"
#include "console.h"

FILE *g_port_log;
static char g_path[520];
static char g_early[2048]; /* port_log_early's lines, written again at the top of each log opened after them */
static size_t g_early_n;

const char *port_log_path(void) { return g_path; }

void port_log_open(const char *path)
{
    strncpy_s(g_path, sizeof g_path, path, _TRUNCATE);
    g_port_log = _fsopen(path, "w", _SH_DENYWR); /* fopen_s would open it exclusively */
    if (g_port_log && g_early_n) {
        fputs(g_early, g_port_log);
        fflush(g_port_log);
    }
}

void port_log_early(const char *fmt, ...)
{
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    port_log("%s", line);
    size_t n = strlen(line);
    if (g_early_n + n < sizeof g_early) {
        memcpy(g_early + g_early_n, line, n + 1);
        g_early_n += n;
    }
}

void port_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
    if (g_port_log) {
        va_start(ap, fmt);
        vfprintf(g_port_log, fmt, ap);
        va_end(ap);
        fflush(g_port_log);
    }
}

void port_log_flush(void)
{
    fflush(stdout);
    if (g_port_log) fflush(g_port_log);
}
