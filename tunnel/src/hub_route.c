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

int sdtp_hub_parse_ipv4_src(const uint8_t *pkt, size_t len, uint32_t *src_out) {
    /* Source address is at offset 12 of the IPv4 header. Used for cryptokey
     * routing: the hub rejects a decrypted packet whose source is not the
     * sending peer's own tunnel IP, so an authenticated peer cannot spoof
     * another peer's address. */
    if (len < 20) return -1;
    if ((pkt[0] >> 4) != 4) return -1;
    memcpy(src_out, pkt + 12, 4); /* copy, not cast: the packet buffer is unaligned */
    return 0;
}

int sdtp_inner_src_ok(const uint8_t *pkt, size_t len, uint32_t expected_ip) {
    uint32_t src;
    return sdtp_hub_parse_ipv4_src(pkt, len, &src) == 0 && src == expected_ip;
}

int sdtp_hub_find_peer_by_session_id(const sdtp_hub_peer *peers, size_t n,
                                     const uint8_t session_id[SDTP_SESSION_ID_LEN]) {
    for (size_t i = 0; i < n; i++) {
        /* Only slots holding a session can match, so an all-zero id on an
         * unused slot never false-matches. */
        if (sdtp_peer_owns(&peers[i].ps, session_id)) return (int)i;
    }
    return -1;
}

int sdtp_hub_find_peer_by_ip(const sdtp_hub_peer *peers, size_t n, uint32_t ip) {
    for (size_t i = 0; i < n; i++) {
        if (peers[i].tunnel_ip == ip) return (int)i;
    }
    return -1;
}
