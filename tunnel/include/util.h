#ifndef SDTP_UTIL_H
#define SDTP_UTIL_H

#include <stdint.h>
#include <stddef.h>
#include <time.h>

void sdtp_put_u64be(uint8_t *out, uint64_t v);
uint64_t sdtp_get_u64be(const uint8_t *in);

/* Monotonic-ish wall clock in nanoseconds (CLOCK_REALTIME), used for
 * handshake freshness only -- not security critical beyond coarse replay
 * filtering, so clock_gettime is sufficient (no need for monotonic clock). */
uint64_t sdtp_now_ns(void);

void sdtp_log(const char *fmt, ...);
void sdtp_die(const char *fmt, ...);

/* At most one log line per second per limiter; the next line that gets through
 * reports how many were suppressed. For messages an unauthenticated sender can
 * trigger (rejected handshakes) and for repeating I/O errors. */
typedef struct {
    time_t last;
    unsigned long suppressed;
} sdtp_ratelimit;
void sdtp_log_limited(sdtp_ratelimit *rl, const char *fmt, ...);

/* Parse a decimal port in [min_port, 65535]; the whole string must be digits.
 * Returns 0 on success, -1 otherwise (unlike atoi, "70000" is an error rather
 * than a silent wrap to 4464). */
int sdtp_parse_port(const char *s, long min_port, uint16_t *out);

/* Print a warning if `path` (a file holding a private key) is readable or
 * writable by group/other. */
void sdtp_warn_if_key_file_open(const char *path);

#endif /* SDTP_UTIL_H */
