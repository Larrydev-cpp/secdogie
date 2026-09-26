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

int sdtp_hub_find_peer_by_session_id(const sdtp_hub_peer *peers, size_t n,
                                     const uint8_t session_id[SDTP_SESSION_ID_LEN]) {
    for (size_t i = 0; i < n; i++) {
        /* Only live (confirmed or pending) sessions count, so the all-zero id
         * of an idle slot can't false-match. */
        if (sdtp_responder_owns(&peers[i].rs, session_id)) return (int)i;
    }
    return -1;
}

int sdtp_hub_respond_init(sdtp_hub_peer *peers, size_t n, const sdtp_keypair *hub_static,
                          const uint8_t *msg1, size_t msg1_len, uint8_t msg2_out[SDTP_MSG2_LEN]) {
    if (msg1_len != SDTP_MSG1_LEN) return -1;
    if (sdtp_hub_find_peer_by_session_id(peers, n, msg1 + 1) >= 0) return -1;
    for (size_t i = 0; i < n; i++) {
        /* Only the slot whose configured key the message claims can accept it. */
        if (sdtp_responder_on_init(&peers[i].rs, hub_static, peers[i].static_pk, msg1, msg1_len, msg2_out) > 0) {
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
