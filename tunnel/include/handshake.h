#ifndef SDTP_HANDSHAKE_H
#define SDTP_HANDSHAKE_H

#include "sdtp.h"

typedef struct {
    sdtp_keypair eph;
    uint8_t session_id[SDTP_SESSION_ID_LEN];
} sdtp_handshake_state;

/* Initiator step 1: builds message 1 into `out` (must have room for
 * SDTP_MSG1_LEN bytes). Fills `hs` with the ephemeral keypair and session id
 * to be used when processing message 2. Returns the message length. */
size_t sdtp_handshake_init_create(sdtp_handshake_state *hs, uint8_t out[SDTP_MSG1_LEN],
                                   const sdtp_keypair *my_static, const uint8_t peer_static_pk[SDTP_KEY_LEN]);

/* Responder step: validates and processes message 1. `last_peer_timestamp`
 * is an in/out per-peer replay guard (caller persists it across calls).
 * On success builds message 2 into `out` (room for SDTP_MSG2_LEN bytes),
 * fills `session` (established, is_initiator=0), and returns the message
 * length. Returns 0 and leaves `out`/`session` untouched on any validation
 * failure (bad mac1, stale/duplicate timestamp, wrong peer, degenerate DH). */
size_t sdtp_handshake_respond(const uint8_t *msg1, size_t msg1_len,
                               const sdtp_keypair *my_static, const uint8_t expected_peer_static_pk[SDTP_KEY_LEN],
                               uint64_t *last_peer_timestamp,
                               uint8_t out[SDTP_MSG2_LEN], sdtp_session *session);

/* Initiator step 2: validates and processes message 2, using the state `hs`
 * saved from step 1. On success fills `session` (established, is_initiator=1)
 * and returns 1. Returns 0 on any validation failure (session id mismatch,
 * failed key confirmation). */
int sdtp_handshake_finish(const sdtp_handshake_state *hs, const uint8_t *msg2, size_t msg2_len,
                           const sdtp_keypair *my_static, const uint8_t peer_static_pk[SDTP_KEY_LEN],
                           sdtp_session *session);

#endif /* SDTP_HANDSHAKE_H */
