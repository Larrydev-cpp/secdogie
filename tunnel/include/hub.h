#ifndef SDTP_HUB_H
#define SDTP_HUB_H

#include <net/if.h>
#include <netinet/in.h>
#include <time.h>
#include <stdint.h>

#include "sdtp.h"

/* A hub is a reachable node that terminates many point-to-point tunnels at
 * once (one per client) and routes packets between them by inner destination
 * IP -- turning the 1:1 tunnel into a hub-and-spoke network so a controller
 * can reach many agent machines through a single public node.
 *
 * Two demux problems the point-to-point server never had:
 *   - handshake_init: which configured client sent it? -> try each client's
 *     static key with sdtp_handshake_respond (only the right one authenticates).
 *   - data/keepalive: which client's session? -> the 8-byte session_id in every
 *     data datagram header identifies the session (see sdtp_data_encrypt).
 * Neither needs any change to the wire protocol. */

#define SDTP_HUB_MAX_PEERS 64

typedef struct {
    /* Configured up front from the hub config file: */
    uint8_t static_pk[SDTP_KEY_LEN]; /* the client's static public key */
    uint32_t tunnel_ip;              /* the client's tunnel IP, network byte order */
    /* Filled in at runtime as the client handshakes and sends: */
    sdtp_session session;
    struct sockaddr_in addr;         /* last source address we saw from this client */
    int have_addr;
    int established;
    uint64_t last_peer_ts;           /* per-peer handshake replay guard */
    time_t last_recv;
    time_t last_send;
} sdtp_hub_peer;

typedef struct {
    sdtp_keypair my_static;
    char address[64];       /* hub's own TUN address, e.g. "10.66.0.1/24" */
    uint32_t self_ip;       /* hub's own tunnel IP (network byte order), parsed from address */
    uint16_t listen_port;
    int mtu;
    char ifname[IFNAMSIZ];
    sdtp_hub_peer peers[SDTP_HUB_MAX_PEERS];
    size_t peer_count;
} sdtp_hub_config;

/* --- pure helpers (no OS/networking; unit-tested in tests/) --- */

/* Parse the destination IPv4 address (network byte order) out of an inner IP
 * packet. Returns 0 on success, -1 if the buffer is too short or not IPv4. */
int sdtp_hub_parse_ipv4_dst(const uint8_t *pkt, size_t len, uint32_t *dst_out);

/* Index of the established peer whose session has this session_id, or -1. */
int sdtp_hub_find_peer_by_session_id(const sdtp_hub_peer *peers, size_t n,
                                     const uint8_t session_id[SDTP_SESSION_ID_LEN]);

/* Index of the peer configured with this tunnel IP (network byte order), or -1. */
int sdtp_hub_find_peer_by_ip(const sdtp_hub_peer *peers, size_t n, uint32_t ip);

/* --- config + run loop (in hub.c) --- */

/* Loads a hub config: private_key, address, listen_port, optional mtu/ifname,
 * and one `peer = <base64 public key> <tunnel ip>` line per client. Returns 0
 * on success, -1 on error (message printed to stderr). */
int sdtp_hub_config_load(const char *path, sdtp_hub_config *cfg);

/* Runs the hub event loop until signalled. Terminates many client tunnels and
 * routes decrypted packets between them / to the hub's own TUN. */
void sdtp_hub_run(int tun_fd, int udp_fd, sdtp_hub_config *cfg);

#endif /* SDTP_HUB_H */
