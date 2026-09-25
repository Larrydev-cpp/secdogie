/* Per-peer session state machine -- see include/peer_state.h. */
#include <string.h>
#include <sodium.h>

#include "peer_state.h"
#include "handshake.h"
#include "data.h"

static void wipe(sdtp_session *s, int *flag) {
    sodium_memzero(s, sizeof(*s));
    *flag = 0;
}

static void retire_current(sdtp_peer_state *ps) {
    if (!ps->has_current) return;
    ps->previous = ps->current;
    ps->has_previous = 1;
    wipe(&ps->current, &ps->has_current);
}

size_t sdtp_peer_respond(sdtp_peer_state *ps, const uint8_t *msg1, size_t msg1_len,
                         const sdtp_keypair *my_static, const uint8_t peer_static_pk[SDTP_KEY_LEN],
                         uint8_t out[SDTP_MSG2_LEN]) {
    sdtp_session fresh;
    size_t n = sdtp_handshake_respond(msg1, msg1_len, my_static, peer_static_pk,
                                      &ps->last_peer_ts, out, &fresh);
    if (n == 0) return 0;
    ps->pending = fresh; /* a newer authentic msg1 replaces an unconfirmed one */
    ps->has_pending = 1;
    sodium_memzero(&fresh, sizeof(fresh));
    return n;
}

void sdtp_peer_install(sdtp_peer_state *ps, const sdtp_session *s) {
    retire_current(ps);
    ps->current = *s;
    ps->has_current = 1;
    if (ps->has_pending) wipe(&ps->pending, &ps->has_pending);
}

static int session_owns(const sdtp_session *s, int present, const uint8_t *sid) {
    return present && sodium_memcmp(s->session_id, sid, SDTP_SESSION_ID_LEN) == 0;
}

int sdtp_peer_owns(const sdtp_peer_state *ps, const uint8_t session_id[SDTP_SESSION_ID_LEN]) {
    return session_owns(&ps->current, ps->has_current, session_id)
        || session_owns(&ps->pending, ps->has_pending, session_id)
        || session_owns(&ps->previous, ps->has_previous, session_id);
}

int sdtp_peer_decrypt(sdtp_peer_state *ps, const uint8_t *in, size_t in_len,
                      const struct sockaddr_in *src,
                      uint8_t *pt, size_t pt_cap, size_t *pt_len) {
    if (in_len < 1 + SDTP_SESSION_ID_LEN) return SDTP_PEER_FAIL;
    const uint8_t *sid = in + 1;
    int rc = SDTP_PEER_FAIL;

    if (session_owns(&ps->current, ps->has_current, sid)) {
        if (sdtp_data_decrypt(&ps->current, in, in_len, pt, pt_cap, pt_len) == 0) rc = SDTP_PEER_OK_CURRENT;
    } else if (session_owns(&ps->pending, ps->has_pending, sid)) {
        if (sdtp_data_decrypt(&ps->pending, in, in_len, pt, pt_cap, pt_len) == 0) {
            /* The initiator proved it holds the same keys: promote. */
            retire_current(ps);
            ps->current = ps->pending;
            ps->has_current = 1;
            wipe(&ps->pending, &ps->has_pending);
            rc = SDTP_PEER_OK_PROMOTED;
        }
    } else if (session_owns(&ps->previous, ps->has_previous, sid)) {
        if (sdtp_data_decrypt(&ps->previous, in, in_len, pt, pt_cap, pt_len) == 0) rc = SDTP_PEER_OK_PREVIOUS;
    }

    if (rc != SDTP_PEER_FAIL && src != NULL && rc != SDTP_PEER_OK_PREVIOUS) {
        ps->addr = *src; /* roaming: only an authenticated packet on a live session moves the peer */
        ps->have_addr = 1;
    }
    return rc;
}

void sdtp_peer_reset(sdtp_peer_state *ps) {
    wipe(&ps->current, &ps->has_current);
    wipe(&ps->pending, &ps->has_pending);
    wipe(&ps->previous, &ps->has_previous);
}
