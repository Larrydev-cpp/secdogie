#ifndef SDTP_NET_H
#define SDTP_NET_H

#include <netinet/in.h>

/* Binds a UDP socket on 0.0.0.0:port (port 0 lets the kernel pick, used by
 * clients that don't need a fixed source port). Returns the fd, or -1. */
int sdtp_udp_bind(uint16_t port);

/* Applies best-effort low-latency socket options: larger receive/send
 * buffers (absorb scheduling-gap bursts without dropping), a DSCP/ToS class
 * on the outgoing packets so the network can prioritise them, and optional
 * SO_BUSY_POLL. `dscp` < 0 selects the legacy IPTOS_LOWDELAY bit; otherwise
 * the value (0..63) is placed in the DSCP field. `busy_poll_us` == 0 leaves
 * busy polling off. Failures are ignored -- these are optimisations, not
 * correctness requirements. */
void sdtp_udp_tune(int fd, int dscp, int busy_poll_us);

/* Resolves host:port (IPv4 only, dotted-quad or hostname) into `out`.
 * Returns 0 on success, -1 on failure. */
int sdtp_resolve(const char *host, uint16_t port, struct sockaddr_in *out);

#endif /* SDTP_NET_H */
