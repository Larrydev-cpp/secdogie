#ifndef SDTP_TUN_H
#define SDTP_TUN_H

#include <net/if.h>

/* Opens /dev/net/tun and creates (or attaches to) a TUN interface.
 * `ifname` is an IFNAMSIZ buffer: pass an empty string to let the kernel
 * assign a name (e.g. "tun0"), or a specific name to request it; on
 * success `ifname` holds the name actually assigned. Returns the fd, or -1
 * on error (errno set). */
int sdtp_tun_create(char ifname[IFNAMSIZ]);

/* Assigns `cidr` (e.g. "10.66.0.1/24") as the interface's address+netmask,
 * sets `mtu`, and brings the interface up (IFF_UP|IFF_RUNNING). Returns 0
 * on success, -1 on error (errno set). Requires CAP_NET_ADMIN (i.e. root,
 * or an appropriate capability grant on the binary). */
int sdtp_tun_configure(const char *ifname, const char *cidr, int mtu);

#endif /* SDTP_TUN_H */
