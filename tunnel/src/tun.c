#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <linux/if_tun.h>

#include "tun.h"

int sdtp_tun_create(char ifname[IFNAMSIZ]) {
    int fd = open("/dev/net/tun", O_RDWR);
    if (fd < 0) return -1;

    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    ifr.ifr_flags = IFF_TUN | IFF_NO_PI;
    if (ifname[0] != '\0') {
        strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);
    }

    if (ioctl(fd, TUNSETIFF, &ifr) < 0) {
        int saved_errno = errno;
        close(fd);
        errno = saved_errno;
        return -1;
    }

    strncpy(ifname, ifr.ifr_name, IFNAMSIZ - 1);
    ifname[IFNAMSIZ - 1] = '\0';
    return fd;
}

static int parse_cidr(const char *cidr, struct in_addr *addr, struct in_addr *mask) {
    char buf[64];
    strncpy(buf, cidr, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char *slash = strchr(buf, '/');
    int prefix = 32;
    if (slash) {
        *slash = '\0';
        prefix = atoi(slash + 1);
    }
    if (prefix < 0 || prefix > 32) {
        errno = EINVAL;
        return -1;
    }
    if (inet_pton(AF_INET, buf, addr) != 1) {
        errno = EINVAL;
        return -1;
    }
    uint32_t mask_host = prefix == 0 ? 0 : (~(uint32_t)0) << (32 - prefix);
    mask->s_addr = htonl(mask_host);
    return 0;
}

int sdtp_tun_configure(const char *ifname, const char *cidr, int mtu) {
    struct in_addr addr, mask;
    if (parse_cidr(cidr, &addr, &mask) < 0) return -1;

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) return -1;

    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);

    struct sockaddr_in *sin = (struct sockaddr_in *)&ifr.ifr_addr;
    sin->sin_family = AF_INET;
    sin->sin_addr = addr;
    if (ioctl(sock, SIOCSIFADDR, &ifr) < 0) goto fail;

    sin->sin_addr = mask;
    if (ioctl(sock, SIOCSIFNETMASK, &ifr) < 0) goto fail;

    ifr.ifr_mtu = mtu;
    if (ioctl(sock, SIOCSIFMTU, &ifr) < 0) goto fail;

    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);
    if (ioctl(sock, SIOCGIFFLAGS, &ifr) < 0) goto fail;
    ifr.ifr_flags |= (IFF_UP | IFF_RUNNING);
    if (ioctl(sock, SIOCSIFFLAGS, &ifr) < 0) goto fail;

    close(sock);
    return 0;

fail:
    {
        int saved_errno = errno;
        close(sock);
        errno = saved_errno;
        return -1;
    }
}
