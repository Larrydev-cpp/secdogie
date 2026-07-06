#include <string.h>
#include <sodium.h>

#include "crypto.h"

int sdtp_crypto_init(void) {
    return sodium_init() < 0 ? -1 : 0;
}

void sdtp_keypair_generate(sdtp_keypair *kp) {
    randombytes_buf(kp->sk, SDTP_KEY_LEN);
    crypto_scalarmult_base(kp->pk, kp->sk);
}

int sdtp_dh(uint8_t out[SDTP_KEY_LEN], const uint8_t sk[SDTP_KEY_LEN], const uint8_t pk[SDTP_KEY_LEN]) {
    return crypto_scalarmult(out, sk, pk);
}

void sdtp_mac1(uint8_t out[SDTP_MAC_LEN], const uint8_t peer_static_pk[SDTP_KEY_LEN],
               const uint8_t *data, size_t data_len) {
    static const char label[] = "SDTP-mac1";
    uint8_t buf[sizeof(label) - 1 + SDTP_KEY_LEN];
    uint8_t mac_key[SDTP_KEY_LEN];

    memcpy(buf, label, sizeof(label) - 1);
    memcpy(buf + sizeof(label) - 1, peer_static_pk, SDTP_KEY_LEN);
    crypto_generichash(mac_key, sizeof(mac_key), buf, sizeof(buf), NULL, 0);

    crypto_generichash(out, SDTP_MAC_LEN, data, data_len, mac_key, sizeof(mac_key));

    sodium_memzero(mac_key, sizeof(mac_key));
}

static void hash32(uint8_t out[32], const uint8_t *a, size_t a_len, const uint8_t *b, size_t b_len) {
    crypto_generichash_state st;
    crypto_generichash_init(&st, NULL, 0, 32);
    if (a && a_len) crypto_generichash_update(&st, a, a_len);
    if (b && b_len) crypto_generichash_update(&st, b, b_len);
    crypto_generichash_final(&st, out, 32);
}

void sdtp_derive_transport_keys(uint8_t key_i2r[SDTP_KEY_LEN], uint8_t key_r2i[SDTP_KEY_LEN],
                                 const uint8_t dh1[SDTP_KEY_LEN], const uint8_t dh2[SDTP_KEY_LEN],
                                 const uint8_t dh3[SDTP_KEY_LEN],
                                 const uint8_t i_static_pk[SDTP_KEY_LEN],
                                 const uint8_t r_static_pk[SDTP_KEY_LEN]) {
    static const char label[] = "SDTP-v1-chaining-key";
    uint8_t ck[32];
    uint8_t tmp[32 + 1 + SDTP_KEY_LEN + SDTP_KEY_LEN];

    crypto_generichash(ck, sizeof(ck), (const unsigned char *)label, sizeof(label) - 1, NULL, 0);
    hash32(ck, ck, sizeof(ck), dh1, SDTP_KEY_LEN);
    hash32(ck, ck, sizeof(ck), dh2, SDTP_KEY_LEN);
    hash32(ck, ck, sizeof(ck), dh3, SDTP_KEY_LEN);

    memcpy(tmp, ck, 32);
    tmp[32] = 0x01;
    memcpy(tmp + 33, i_static_pk, SDTP_KEY_LEN);
    memcpy(tmp + 33 + SDTP_KEY_LEN, r_static_pk, SDTP_KEY_LEN);
    crypto_generichash(key_i2r, SDTP_KEY_LEN, tmp, sizeof(tmp), NULL, 0);

    tmp[32] = 0x02;
    crypto_generichash(key_r2i, SDTP_KEY_LEN, tmp, sizeof(tmp), NULL, 0);

    sodium_memzero(ck, sizeof(ck));
    sodium_memzero(tmp, sizeof(tmp));
}

int sdtp_aead_encrypt(uint8_t *ct, const uint8_t *pt, size_t pt_len,
                       const uint8_t *aad, size_t aad_len,
                       const uint8_t nonce[SDTP_NONCE_LEN], const uint8_t key[SDTP_KEY_LEN]) {
    unsigned long long ct_len = 0;
    int rc = crypto_aead_xchacha20poly1305_ietf_encrypt(
        ct, &ct_len, pt, pt_len, aad, aad_len, NULL, nonce, key);
    return rc == 0 ? 0 : -1;
}

int sdtp_aead_decrypt(uint8_t *pt, const uint8_t *ct, size_t ct_len,
                       const uint8_t *aad, size_t aad_len,
                       const uint8_t nonce[SDTP_NONCE_LEN], const uint8_t key[SDTP_KEY_LEN]) {
    unsigned long long pt_len = 0;
    int rc = crypto_aead_xchacha20poly1305_ietf_decrypt(
        pt, &pt_len, NULL, ct, ct_len, aad, aad_len, nonce, key);
    return rc == 0 ? 0 : -1;
}
