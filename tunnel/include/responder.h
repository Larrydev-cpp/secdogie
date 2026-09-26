#ifndef SDTP_RESPONDER_H
#define SDTP_RESPONDER_H

#include <netinet/in.h>
#include <stddef.h>
#include <stdint.h>

#include "sdtp.h"

/* Responder-side session state for one configured peer, shared by the
 * point-to-point server and every hub slot.
 *
 * Confirm before swap. Message 1 carries no proof that its sender holds the
 * initiator's static private key: anyone who knows both public keys can build
 * one that passes every check. So a valid handshake_init does NOT replace the
 * live session. The responder answers it and parks the new session in
 * `pending`; only when the initiator's first authenticated data or keepalive
 * packet decrypts under that session -- which only the real initiator can
 * produce -- is it promoted, together with the packet's source address and the
 * handshake timestamp. Until then the confirmed session keeps working and the
 * committed timestamp does not move, so a forged message 1 can neither tear the
 * tunnel down, redirect it, nor lock out re-handshakes with a future timestamp.
 *
 * Pure: no sockets, no clock, no logging -- unit-tested in tests/. */
typedef struct {
    sdtp_session current;      /* confirmed live session (current.established) */
    struct sockaddr_in addr;   /* where the confirmed peer was last seen */
    int have_addr;
    uint64_t committed_ts;     /* timestamp of the last CONFIRMED handshake */
    sdtp_session pending;      /* answered, awaiting the initiator's first packet */
    uint64_t pending_ts;
    int has_pending;
} sdtp_responder;

/* sdtp_responder_on_data results. */
#define SDTP_RESP_DROP 0     /* not for this peer, forged, tampered or replayed */
#define SDTP_RESP_OK 1       /* authenticated on the confirmed session */
#define SDTP_RESP_PROMOTED 2 /* first packet of the pending session: it is now current */

/* Handles a handshake_init. On success builds message 2 into `msg2_out`, parks
 * the new session in `pending` (replacing any older pending one) and returns
 * SDTP_MSG2_LEN. Returns 0 with the state untouched when the message fails
 * validation, is not newer than the committed handshake, or reuses a session id
 * this peer already has (which also catches a replay of the pending message 1). */
size_t sdtp_responder_on_init(sdtp_responder *r, const sdtp_keypair *my_static,
                              const uint8_t peer_static_pk[SDTP_KEY_LEN],
                              const uint8_t *msg1, size_t msg1_len, uint8_t msg2_out[SDTP_MSG2_LEN]);

/* Authenticates a data/keepalive datagram from `src` against the confirmed
 * session, then the pending one. Plaintext goes to `pt` (up to `pt_cap` bytes)
 * with its length in *pt_len. Returns one of the SDTP_RESP_* codes. */
int sdtp_responder_on_data(sdtp_responder *r, const uint8_t *in, size_t in_len,
                           const struct sockaddr_in *src, uint8_t *pt, size_t pt_cap, size_t *pt_len);

/* 1 if `session_id` belongs to this peer's confirmed or pending session. */
int sdtp_responder_owns(const sdtp_responder *r, const uint8_t session_id[SDTP_SESSION_ID_LEN]);

/* Forgets both sessions (keys wiped). The committed timestamp is kept, so an
 * old message 1 still cannot be replayed afterwards. */
void sdtp_responder_reset(sdtp_responder *r);

#endif /* SDTP_RESPONDER_H */
