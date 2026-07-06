#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <time.h>

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
