/* recvmmsg / struct mmsghdr are GNU extensions; MSG_DONTWAIT and getaddrinfo
 * also need the wider feature set. Must precede any libc header. */
#define _GNU_SOURCE

#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <errno.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>

#include "net.h"

int sdtp_udp_recv_batch(int fd, sdtp_udp_msg *msgs, int max) {
    if (max < 1) return 0;
    if (max > SDTP_RECV_BATCH) max = SDTP_RECV_BATCH;

#ifdef __linux__
    /* recvmmsg drains up to `max` queued datagrams in ONE syscall. Fixed-size
     * scratch (SDTP_RECV_BATCH) avoids a VLA; each header points at its own
     * message buffer and source-address slot. MSG_DONTWAIT so a spurious wake
     * never blocks the loop. */
    struct mmsghdr hdrs[SDTP_RECV_BATCH];
    struct iovec iovs[SDTP_RECV_BATCH];
    memset(hdrs, 0, sizeof(hdrs));
    for (int i = 0; i < max; i++) {
        iovs[i].iov_base = msgs[i].buf;
        iovs[i].iov_len = sizeof(msgs[i].buf);
        hdrs[i].msg_hdr.msg_iov = &iovs[i];
        hdrs[i].msg_hdr.msg_iovlen = 1;
        hdrs[i].msg_hdr.msg_name = &msgs[i].src;
        hdrs[i].msg_hdr.msg_namelen = sizeof(msgs[i].src);
    }
    int n = recvmmsg(fd, hdrs, (unsigned)max, MSG_DONTWAIT, NULL);
    if (n < 0) {
        return (errno == EAGAIN || errno == EWOULDBLOCK) ? 0 : -1;
    }
    for (int i = 0; i < n; i++) {
        msgs[i].len = hdrs[i].msg_len;
        msgs[i].src_len = hdrs[i].msg_hdr.msg_namelen;
    }
    return n;
#else
    /* Portable fallback: a bounded non-blocking recvfrom loop. Same behavior
     * (drain what's queued, stop at EAGAIN), one syscall per datagram. */
    int n = 0;
    for (; n < max; n++) {
        msgs[n].src_len = sizeof(msgs[n].src);
        ssize_t r = recvfrom(fd, msgs[n].buf, sizeof(msgs[n].buf), MSG_DONTWAIT,
                             (struct sockaddr *)&msgs[n].src, &msgs[n].src_len);
        if (r < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            return (n > 0) ? n : -1;
        }
        msgs[n].len = (size_t)r;
    }
    return n;
#endif
}

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
