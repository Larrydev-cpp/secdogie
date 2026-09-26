/* Confirm-before-swap responder state (see include/responder.h). */
#include <string.h>
#include <sodium.h>

#include "responder.h"
#include "handshake.h"
#include "data.h"
#include "util.h"

static int same_id(const uint8_t *a, const uint8_t *b) {
    return sodium_memcmp(a, b, SDTP_SESSION_ID_LEN) == 0;
}

int sdtp_responder_owns(const sdtp_responder *r, const uint8_t session_id[SDTP_SESSION_ID_LEN]) {
    return (r->current.established && same_id(session_id, r->current.session_id)) ||
           (r->has_pending && same_id(session_id, r->pending.session_id));
}

size_t sdtp_responder_on_init(sdtp_responder *r, const sdtp_keypair *my_static,
                              const uint8_t peer_static_pk[SDTP_KEY_LEN],
                              const uint8_t *msg1, size_t msg1_len, uint8_t msg2_out[SDTP_MSG2_LEN]) {
    if (msg1_len != SDTP_MSG1_LEN) return 0;
    /* A real initiator draws a fresh random session id for every attempt, so a
     * reused id is a replay (of the pending message 1, say) or a forgery trying
     * to shadow a live session. Checked before any DH work. */
    if (sdtp_responder_owns(r, msg1 + 1)) return 0;

    /* Validate against the COMMITTED timestamp only: a forged message 1 never
     * advances it, so it cannot lock the real initiator out. */
    uint64_t ts = r->committed_ts;
    sdtp_session fresh;
    uint8_t msg2[SDTP_MSG2_LEN];
    size_t n = sdtp_handshake_respond(msg1, msg1_len, my_static, peer_static_pk, &ts, msg2, &fresh);
    if (n == 0) return 0;

    sodium_memzero(&r->pending, sizeof(r->pending));
    r->pending = fresh;
    r->pending_ts = ts;
    r->has_pending = 1;
    sodium_memzero(&fresh, sizeof(fresh));
    memcpy(msg2_out, msg2, n);
    return n;
}

int sdtp_responder_on_data(sdtp_responder *r, const uint8_t *in, size_t in_len,
                           const struct sockaddr_in *src, uint8_t *pt, size_t pt_cap, size_t *pt_len) {
    if (in_len < 1 + SDTP_SESSION_ID_LEN) return SDTP_RESP_DROP;
    const uint8_t *sid = in + 1;

    if (r->current.established && same_id(sid, r->current.session_id)) {
        if (sdtp_data_decrypt(&r->current, in, in_len, pt, pt_cap, pt_len) != 0) return SDTP_RESP_DROP;
        r->addr = *src; /* roaming: only an authenticated packet may move the peer */
        r->have_addr = 1;
        return SDTP_RESP_OK;
    }

    if (r->has_pending && same_id(sid, r->pending.session_id)) {
        if (sdtp_data_decrypt(&r->pending, in, in_len, pt, pt_cap, pt_len) != 0) return SDTP_RESP_DROP;
        /* Key confirmation: only the holder of the initiator's static key could
         * have produced this packet. Promote the session. */
        sodium_memzero(&r->current, sizeof(r->current));
        r->current = r->pending;
        r->committed_ts = r->pending_ts;
        r->addr = *src;
        r->have_addr = 1;
        sodium_memzero(&r->pending, sizeof(r->pending));
        r->pending_ts = 0;
        r->has_pending = 0;
        return SDTP_RESP_PROMOTED;
    }
    return SDTP_RESP_DROP;
}

void sdtp_responder_reset(sdtp_responder *r) {
    sodium_memzero(&r->current, sizeof(r->current));
    sodium_memzero(&r->pending, sizeof(r->pending));
    r->pending_ts = 0;
    r->has_pending = 0;
}
