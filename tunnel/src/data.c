#include <string.h>
#include <sodium.h>

#include "data.h"
#include "crypto.h"
#include "util.h"

#define REPLAY_BYTES (SDTP_REPLAY_WINDOW_BITS / 8)

static void build_nonce(uint8_t nonce[SDTP_NONCE_LEN], uint64_t counter) {
    memset(nonce, 0, SDTP_NONCE_LEN);
    sdtp_put_u64be(nonce + (SDTP_NONCE_LEN - 8), counter);
}

/* Cheap pre-decrypt rejection of definitely-too-old or definitely-duplicate
 * counters. A packet passing this check still must pass AEAD auth before
 * being treated as genuinely received -- see replay_accept(). */
static int replay_precheck(const sdtp_session *s, uint64_t counter) {
    if (!s->recv_initialized) return 1;
    if (counter > s->recv_highest) return 1;
    uint64_t diff = s->recv_highest - counter;
    if (diff >= SDTP_REPLAY_WINDOW_BITS) return 0;
    size_t byte = (size_t)(diff / 8);
    int bit = (int)(diff % 8);
    return (s->replay_bitmap[byte] & (1u << bit)) ? 0 : 1;
}

static void bitmap_shift_left(uint8_t *bm, uint64_t shift) {
    if (shift >= SDTP_REPLAY_WINDOW_BITS) {
        memset(bm, 0, REPLAY_BYTES);
        return;
    }
    size_t byte_shift = (size_t)(shift / 8);
    int bit_shift = (int)(shift % 8);
    if (byte_shift > 0) {
        memmove(bm + byte_shift, bm, REPLAY_BYTES - byte_shift);
        memset(bm, 0, byte_shift);
    }
    if (bit_shift > 0) {
        for (int i = REPLAY_BYTES - 1; i >= 0; i--) {
            uint8_t lo = (i > 0) ? bm[i - 1] : 0;
            bm[i] = (uint8_t)((bm[i] << bit_shift) | (lo >> (8 - bit_shift)));
        }
    }
}

/* Only called after AEAD authentication of `counter` has succeeded. */
static void replay_accept(sdtp_session *s, uint64_t counter) {
    if (!s->recv_initialized) {
        s->recv_initialized = 1;
        s->recv_highest = counter;
        memset(s->replay_bitmap, 0, REPLAY_BYTES);
        s->replay_bitmap[0] |= 1u;
        return;
    }
    if (counter > s->recv_highest) {
        bitmap_shift_left(s->replay_bitmap, counter - s->recv_highest);
        s->recv_highest = counter;
        s->replay_bitmap[0] |= 1u;
    } else {
        uint64_t diff = s->recv_highest - counter;
        size_t byte = (size_t)(diff / 8);
        int bit = (int)(diff % 8);
        s->replay_bitmap[byte] |= (uint8_t)(1u << bit);
    }
}

size_t sdtp_data_encrypt(sdtp_session *s, uint8_t type, uint8_t *out,
                          const uint8_t *pt, size_t pt_len) {
    if (s->send_counter == UINT64_MAX) return 0;
    uint64_t counter = s->send_counter++;

    const uint8_t *key = s->is_initiator ? s->key_i2r : s->key_r2i;

    out[0] = type;
    memcpy(out + 1, s->session_id, SDTP_SESSION_ID_LEN);
    sdtp_put_u64be(out + 1 + SDTP_SESSION_ID_LEN, counter);

    uint8_t nonce[SDTP_NONCE_LEN];
    build_nonce(nonce, counter);

    if (sdtp_aead_encrypt(out + SDTP_DATA_HDR_LEN, pt, pt_len, out, SDTP_DATA_HDR_LEN, nonce, key) != 0) {
        return 0;
    }
    return SDTP_DATA_HDR_LEN + pt_len + SDTP_AEAD_TAG_LEN;
}

int sdtp_data_decrypt(sdtp_session *s, const uint8_t *in, size_t in_len,
                       uint8_t *pt, size_t pt_cap, size_t *pt_len) {
    if (in_len < SDTP_DATA_HDR_LEN + SDTP_AEAD_TAG_LEN) return -1;
    uint8_t type = in[0];
    if (type != SDTP_MSG_DATA && type != SDTP_MSG_KEEPALIVE) return -1;
    if (sodium_memcmp(in + 1, s->session_id, SDTP_SESSION_ID_LEN) != 0) return -1;

    uint64_t counter = sdtp_get_u64be(in + 1 + SDTP_SESSION_ID_LEN);
    if (!replay_precheck(s, counter)) return -1;

    const uint8_t *key = s->is_initiator ? s->key_r2i : s->key_i2r;
    uint8_t nonce[SDTP_NONCE_LEN];
    build_nonce(nonce, counter);

    size_t ct_len = in_len - SDTP_DATA_HDR_LEN;
    size_t out_len = ct_len - SDTP_AEAD_TAG_LEN;
    if (out_len > pt_cap) return -1;

    if (sdtp_aead_decrypt(pt, in + SDTP_DATA_HDR_LEN, ct_len, in, SDTP_DATA_HDR_LEN, nonce, key) != 0) {
        return -1;
    }

    replay_accept(s, counter);
    *pt_len = out_len;
    return 0;
}
