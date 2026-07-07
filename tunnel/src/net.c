#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <errno.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <netdb.h>
#include <sys/socket.h>

#include "net.h"

/* Big enough to ride out a scheduling gap at multi-Gbps without dropping;
 * receive buffering never adds latency here because the event loop drains
 * the socket on every wakeup. The kernel clamps these to net.core.{r,w}mem_max. */
#define SDTP_UDP_RCVBUF (4 * 1024 * 1024)
#define SDTP_UDP_SNDBUF (1 * 1024 * 1024)

int sdtp_udp_bind(uint16_t port) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -1;

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        int saved_errno_ = errno;
        close(fd);
        errno = saved_errno_;
        return -1;
    }
    return fd;
}

void sdtp_udp_tune(int fd, int dscp, int busy_poll_us) {
    int rcv = SDTP_UDP_RCVBUF;
    int snd = SDTP_UDP_SNDBUF;
    setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &rcv, sizeof(rcv));
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &snd, sizeof(snd));

    /* DSCP occupies the high 6 bits of the ToS byte; the low 2 (ECN) stay 0. */
    int tos = (dscp < 0) ? IPTOS_LOWDELAY : ((dscp & 0x3f) << 2);
    setsockopt(fd, IPPROTO_IP, IP_TOS, &tos, sizeof(tos));

#ifdef SO_BUSY_POLL
    if (busy_poll_us > 0) {
        setsockopt(fd, SOL_SOCKET, SO_BUSY_POLL, &busy_poll_us, sizeof(busy_poll_us));
    }
#else
    (void)busy_poll_us;
#endif
}

int sdtp_resolve(const char *host, uint16_t port, struct sockaddr_in *out) {
    memset(out, 0, sizeof(*out));
    out->sin_family = AF_INET;
    out->sin_port = htons(port);

    if (inet_pton(AF_INET, host, &out->sin_addr) == 1) {
        return 0;
    }

    struct addrinfo hints, *res;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    if (getaddrinfo(host, NULL, &hints, &res) != 0) return -1;

    struct sockaddr_in *sin = (struct sockaddr_in *)res->ai_addr;
    out->sin_addr = sin->sin_addr;
    freeaddrinfo(res);
    return 0;
}
