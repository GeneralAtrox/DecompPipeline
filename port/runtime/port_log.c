/* The port's log: every line goes to stdout and to port.log. */
#include <stdarg.h>
#include "port.h"

FILE *g_port_log;

void port_log_open(const char *path)
{
    if (fopen_s(&g_port_log, path, "w") != 0) g_port_log = NULL;
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
    }
}

void port_log_flush(void)
{
    fflush(stdout);
    if (g_port_log) fflush(g_port_log);
}
