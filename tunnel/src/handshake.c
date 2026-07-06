#include <string.h>
#include <sodium.h>

#include "handshake.h"
#include "crypto.h"
#include "util.h"

/* First byte 0x01 guarantees this can never collide with a data-channel
 * nonce, whose first 16 bytes are always zero (see PROTOCOL.md). */
static const uint8_t CONFIRM_NONCE[SDTP_NONCE_LEN] = {0x01};
static const uint8_t CONFIRM_PT[16] = {
    'S', 'D', 'T', 'P', '-', 'H', 'E', 'L', 'L', 'O', '-', 'R', '2', 'I', 'v', '1'};

static void init_session_common(sdtp_session *s, const uint8_t session_id[SDTP_SESSION_ID_LEN],
                                 const uint8_t key_i2r[SDTP_KEY_LEN], const uint8_t key_r2i[SDTP_KEY_LEN],
                                 int is_initiator) {
    memcpy(s->session_id, session_id, SDTP_SESSION_ID_LEN);
    memcpy(s->key_i2r, key_i2r, SDTP_KEY_LEN);
    memcpy(s->key_r2i, key_r2i, SDTP_KEY_LEN);
    s->send_counter = 0;
    s->recv_highest = 0;
    s->recv_initialized = 0;
    memset(s->replay_bitmap, 0, sizeof(s->replay_bitmap));
    s->is_initiator = is_initiator;
    s->established = 1;
}

size_t sdtp_handshake_init_create(sdtp_handshake_state *hs, uint8_t out[SDTP_MSG1_LEN],
                                   const sdtp_keypair *my_static, const uint8_t peer_static_pk[SDTP_KEY_LEN]) {
    sdtp_keypair_generate(&hs->eph);
    randombytes_buf(hs->session_id, SDTP_SESSION_ID_LEN);

    out[0] = SDTP_MSG_HANDSHAKE_INIT;
    memcpy(out + 1, hs->session_id, SDTP_SESSION_ID_LEN);
    memcpy(out + 9, my_static->pk, SDTP_KEY_LEN);
    memcpy(out + 41, hs->eph.pk, SDTP_KEY_LEN);
    sdtp_put_u64be(out + 73, sdtp_now_ns());

    sdtp_mac1(out + 81, peer_static_pk, out, 81);

    return SDTP_MSG1_LEN;
}

size_t sdtp_handshake_respond(const uint8_t *msg1, size_t msg1_len,
                               const sdtp_keypair *my_static, const uint8_t expected_peer_static_pk[SDTP_KEY_LEN],
                               uint64_t *last_peer_timestamp,
                               uint8_t out[SDTP_MSG2_LEN], sdtp_session *session) {
    if (msg1_len != SDTP_MSG1_LEN || msg1[0] != SDTP_MSG_HANDSHAKE_INIT) return 0;

    uint8_t expected_mac[SDTP_MAC_LEN];
    sdtp_mac1(expected_mac, my_static->pk, msg1, 81);
    if (sodium_memcmp(expected_mac, msg1 + 81, SDTP_MAC_LEN) != 0) return 0;

    const uint8_t *i_static_pk = msg1 + 9;
    const uint8_t *i_eph_pk = msg1 + 41;
    if (sodium_memcmp(i_static_pk, expected_peer_static_pk, SDTP_KEY_LEN) != 0) return 0;

    uint64_t timestamp = sdtp_get_u64be(msg1 + 73);
    uint64_t now = sdtp_now_ns();
    uint64_t window_ns = (uint64_t)SDTP_HANDSHAKE_WINDOW_S * 1000000000ULL;
    uint64_t delta = now > timestamp ? now - timestamp : timestamp - now;
    if (delta > window_ns) return 0;
    if (timestamp <= *last_peer_timestamp) return 0;

    sdtp_keypair r_eph;
    sdtp_keypair_generate(&r_eph);

    uint8_t dh1[SDTP_KEY_LEN], dh2[SDTP_KEY_LEN], dh3[SDTP_KEY_LEN];
    if (sdtp_dh(dh1, my_static->sk, i_eph_pk) != 0) return 0;
    if (sdtp_dh(dh2, r_eph.sk, i_static_pk) != 0) return 0;
    if (sdtp_dh(dh3, r_eph.sk, i_eph_pk) != 0) return 0;

    uint8_t key_i2r[SDTP_KEY_LEN], key_r2i[SDTP_KEY_LEN];
    sdtp_derive_transport_keys(key_i2r, key_r2i, dh1, dh2, dh3, i_static_pk, my_static->pk);

    out[0] = SDTP_MSG_HANDSHAKE_RESP;
    memcpy(out + 1, msg1 + 1, SDTP_SESSION_ID_LEN);
    memcpy(out + 9, r_eph.pk, SDTP_KEY_LEN);
    if (sdtp_aead_encrypt(out + 41, CONFIRM_PT, sizeof(CONFIRM_PT), out, 41, CONFIRM_NONCE, key_r2i) != 0) {
        return 0;
    }

    *last_peer_timestamp = timestamp;
    init_session_common(session, msg1 + 1, key_i2r, key_r2i, 0);

    sodium_memzero(dh1, sizeof(dh1));
    sodium_memzero(dh2, sizeof(dh2));
    sodium_memzero(dh3, sizeof(dh3));

    return SDTP_MSG2_LEN;
}

int sdtp_handshake_finish(const sdtp_handshake_state *hs, const uint8_t *msg2, size_t msg2_len,
                           const sdtp_keypair *my_static, const uint8_t peer_static_pk[SDTP_KEY_LEN],
                           sdtp_session *session) {
    if (msg2_len != SDTP_MSG2_LEN || msg2[0] != SDTP_MSG_HANDSHAKE_RESP) return 0;
    if (sodium_memcmp(msg2 + 1, hs->session_id, SDTP_SESSION_ID_LEN) != 0) return 0;

    const uint8_t *r_eph_pk = msg2 + 9;

    uint8_t dh1[SDTP_KEY_LEN], dh2[SDTP_KEY_LEN], dh3[SDTP_KEY_LEN];
    if (sdtp_dh(dh1, hs->eph.sk, peer_static_pk) != 0) return 0;
    if (sdtp_dh(dh2, my_static->sk, r_eph_pk) != 0) return 0;
    if (sdtp_dh(dh3, hs->eph.sk, r_eph_pk) != 0) return 0;

    uint8_t key_i2r[SDTP_KEY_LEN], key_r2i[SDTP_KEY_LEN];
    sdtp_derive_transport_keys(key_i2r, key_r2i, dh1, dh2, dh3, my_static->pk, peer_static_pk);

    uint8_t confirm_pt[16];
    int ok = sdtp_aead_decrypt(confirm_pt, msg2 + 41, 16 + SDTP_AEAD_TAG_LEN, msg2, 41, CONFIRM_NONCE, key_r2i) == 0;

    sodium_memzero(dh1, sizeof(dh1));
    sodium_memzero(dh2, sizeof(dh2));
    sodium_memzero(dh3, sizeof(dh3));

    if (!ok) return 0;

    init_session_common(session, hs->session_id, key_i2r, key_r2i, 1);
    return 1;
}
