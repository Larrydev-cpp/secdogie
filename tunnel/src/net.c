#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <errno.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>

#include "net.h"

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
