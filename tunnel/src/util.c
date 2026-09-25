#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <time.h>
#include <errno.h>
#include <sys/stat.h>

#include "util.h"

void sdtp_put_u64be(uint8_t *out, uint64_t v) {
    for (int i = 0; i < 8; i++) {
        out[i] = (uint8_t)(v >> (8 * (7 - i)));
    }
}

uint64_t sdtp_get_u64be(const uint8_t *in) {
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) {
        v = (v << 8) | in[i];
    }
    return v;
}

uint64_t sdtp_now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

void sdtp_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\n");
}

void sdtp_die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\n");
    exit(1);
}

void sdtp_log_limited(sdtp_ratelimit *rl, const char *fmt, ...) {
    time_t now = time(NULL);
    if (rl->last == now) {
        rl->suppressed++;
        return;
    }
    rl->last = now;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    if (rl->suppressed) fprintf(stderr, " (%lu similar suppressed)", rl->suppressed);
    fprintf(stderr, "\n");
    rl->suppressed = 0;
}

int sdtp_parse_port(const char *s, long min_port, uint16_t *out) {
    if (s == NULL || *s == '\0') return -1;
    char *end = NULL;
    errno = 0;
    long v = strtol(s, &end, 10);
    if (errno != 0 || end == s || *end != '\0' || v < min_port || v > 65535) return -1;
    *out = (uint16_t)v;
    return 0;
}

void sdtp_warn_if_key_file_open(const char *path) {
    struct stat st;
    if (stat(path, &st) != 0) return;
    if (st.st_mode & (S_IRWXG | S_IRWXO)) {
        fprintf(stderr, "warning: %s holds a private key but is accessible by group/other "
                        "(mode %03o); chmod 600 it\n", path, (unsigned)(st.st_mode & 0777));
    }
}
