/* Pure hub routing/lookup logic: no sockets, no TUN, no clock -- so it links
 * into the unit-test binary and can be checked without any networking. The
 * OS-facing config loader and event loop live in hub.c. */
#include <string.h>
#include <sodium.h>

#include "hub.h"

int sdtp_hub_parse_ipv4_dst(const uint8_t *pkt, size_t len, uint32_t *dst_out) {
    /* Minimum IPv4 header is 20 bytes; the destination address is the last 4
     * of those. We only route IPv4 here (the version nibble must be 4). */
    if (len < 20) return -1;
    if ((pkt[0] >> 4) != 4) return -1;
    memcpy(dst_out, pkt + 16, 4); /* copy, not cast: the packet buffer is unaligned */
    return 0;
}

int sdtp_hub_find_peer_by_session_id(const sdtp_hub_peer *peers, size_t n,
                                     const uint8_t session_id[SDTP_SESSION_ID_LEN]) {
    for (size_t i = 0; i < n; i++) {
        /* Only established peers have a real session_id; skip the rest so an
         * all-zero id on an unconfigured slot can't false-match. */
        if (!peers[i].established) continue;
        if (sodium_memcmp(peers[i].session.session_id, session_id, SDTP_SESSION_ID_LEN) == 0) {
            return (int)i;
        }
    }
    return -1;
}

int sdtp_hub_find_peer_by_ip(const sdtp_hub_peer *peers, size_t n, uint32_t ip) {
    for (size_t i = 0; i < n; i++) {
        if (peers[i].tunnel_ip == ip) return (int)i;
    }
    return -1;
}
