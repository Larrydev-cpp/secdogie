#ifndef SDTP_UTIL_H
#define SDTP_UTIL_H

#include <stdint.h>
#include <stddef.h>

void sdtp_put_u64be(uint8_t *out, uint64_t v);
uint64_t sdtp_get_u64be(const uint8_t *in);

/* Monotonic-ish wall clock in nanoseconds (CLOCK_REALTIME), used for
 * handshake freshness only -- not security critical beyond coarse replay
 * filtering, so clock_gettime is sufficient (no need for monotonic clock). */
uint64_t sdtp_now_ns(void);

void sdtp_log(const char *fmt, ...);
void sdtp_die(const char *fmt, ...);

#endif /* SDTP_UTIL_H */
