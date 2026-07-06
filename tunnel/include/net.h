#ifndef SDTP_NET_H
#define SDTP_NET_H

#include <netinet/in.h>

/* Binds a UDP socket on 0.0.0.0:port (port 0 lets the kernel pick, used by
 * clients that don't need a fixed source port). Returns the fd, or -1. */
int sdtp_udp_bind(uint16_t port);

/* Resolves host:port (IPv4 only, dotted-quad or hostname) into `out`.
 * Returns 0 on success, -1 on failure. */
int sdtp_resolve(const char *host, uint16_t port, struct sockaddr_in *out);

#endif /* SDTP_NET_H */
