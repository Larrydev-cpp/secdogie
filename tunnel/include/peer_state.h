#ifndef SDTP_PEER_STATE_H
#define SDTP_PEER_STATE_H

#include <netinet/in.h>
#include <stdint.h>

#include "sdtp.h"

/* Per-peer session state shared by the point-to-point server/client and the
 * hub. Pure: no sockets, no TUN, no clock -- unit-tested in tests/.
 *
 * Why three slots (the WireGuard current/next/previous pattern):
 *   - `pending`: a session the RESPONDER derived from a valid msg1. It is not
 *     used for sending and does not move the peer's address; it becomes
 *     `current` only when the first data/keepalive packet decrypts under it --
 *     proof that the initiator really derived the same keys.
 *   - `current`: the session we send with.
 *   - `previous`: the session `current` replaced, kept receive-only so packets
 *     already in flight on the old keys are not dropped at the switch.
 * The peer's address is adopted only from an authenticated data/keepalive
 * packet, never from a handshake message. */
typedef struct {
    sdtp_session current;
    sdtp_session pending;
    sdtp_session previous;
    int has_current;
    int has_pending;
    int has_previous;
    struct sockaddr_in addr;
    int have_addr;
    uint64_t last_peer_ts; /* handshake replay guard (msg1 timestamps must increase) */
} sdtp_peer_state;

/* Result of sdtp_peer_decrypt. */
#define SDTP_PEER_FAIL -1
#define SDTP_PEER_OK_CURRENT 0
#define SDTP_PEER_OK_PROMOTED 1  /* first packet under `pending`: now `current` */
#define SDTP_PEER_OK_PREVIOUS 2  /* a late packet on the replaced session */

/* Responder: validate msg1 for the configured peer and, on success, store the
 * derived session as `pending` and build msg2 into `out`. `current`, the peer
 * address and anything already in flight are untouched. Returns the msg2
 * length, or 0 on any validation failure. */
size_t sdtp_peer_respond(sdtp_peer_state *ps, const uint8_t *msg1, size_t msg1_len,
                         const sdtp_keypair *my_static, const uint8_t peer_static_pk[SDTP_KEY_LEN],
                         uint8_t out[SDTP_MSG2_LEN]);

/* Initiator: install the session proven by a verified msg2 (the AEAD key
 * confirmation already proves the responder). The old `current`, if any,
 * becomes receive-only `previous`. The caller should send a keepalive right
 * away so the responder can promote its `pending`. */
void sdtp_peer_install(sdtp_peer_state *ps, const sdtp_session *s);

/* Decrypt a data/keepalive datagram against current, pending, then previous.
 * On success adopts `src` as the peer's address and returns one of the
 * SDTP_PEER_OK_* codes; returns SDTP_PEER_FAIL otherwise (nothing changes). */
int sdtp_peer_decrypt(sdtp_peer_state *ps, const uint8_t *in, size_t in_len,
                      const struct sockaddr_in *src,
                      uint8_t *pt, size_t pt_cap, size_t *pt_len);

/* Whether a datagram's session id belongs to any of this peer's sessions. */
int sdtp_peer_owns(const sdtp_peer_state *ps, const uint8_t session_id[SDTP_SESSION_ID_LEN]);

/* Drop all sessions (keys wiped); keeps the address and the replay guard. */
void sdtp_peer_reset(sdtp_peer_state *ps);

#endif /* SDTP_PEER_STATE_H */
