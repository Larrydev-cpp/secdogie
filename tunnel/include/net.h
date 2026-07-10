#ifndef SDTP_NET_H
#define SDTP_NET_H

#include <netinet/in.h>
#include <stdint.h>
#include <stddef.h>

#include "sdtp.h"

/* Binds a UDP socket on 0.0.0.0:port (port 0 lets the kernel pick, used by
 * clients that don't need a fixed source port). Returns the fd, or -1. */
int sdtp_udp_bind(uint16_t port);

/* Resolves host:port (IPv4 only, dotted-quad or hostname) into `out`.
 * Returns 0 on success, -1 on failure. */
int sdtp_resolve(const char *host, uint16_t port, struct sockaddr_in *out);

/* How many queued datagrams to drain from the UDP socket per poll wake-up. One
 * recvmmsg reads the whole batch in a single syscall instead of one recvfrom
 * (and one poll round-trip) per packet, which is where the per-packet syscall
 * overhead -- and latency under load -- actually lives. */
#define SDTP_RECV_BATCH 16

typedef struct {
    uint8_t buf[SDTP_MAX_DATAGRAM];
    size_t len;
    struct sockaddr_in src;
    socklen_t src_len;
} sdtp_udp_msg;

/* Drain up to `max` (<= SDTP_RECV_BATCH) queued datagrams into `msgs` in one
 * syscall (recvmmsg on Linux; a bounded recvfrom loop elsewhere). Non-blocking:
 * returns only what is already queued -- call it after poll reports POLLIN.
 * Returns the count read (>= 0), or -1 on a real error. */
int sdtp_udp_recv_batch(int fd, sdtp_udp_msg *msgs, int max);

#endif /* SDTP_NET_H */
