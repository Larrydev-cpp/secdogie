#ifndef SDTP_CRYPTO_H
#define SDTP_CRYPTO_H

#include "sdtp.h"

/* Must be called once at process start (wraps sodium_init). Returns 0 on
 * success, -1 on failure. */
int sdtp_crypto_init(void);

/* Generates a fresh X25519 keypair into kp. */
void sdtp_keypair_generate(sdtp_keypair *kp);

/* mac1 = BLAKE2b-256("SDTP-mac1" || peer_static_pk)[0:32], used as a MAC key
 * via crypto_generichash keyed mode over `data`. Writes SDTP_MAC_LEN bytes. */
void sdtp_mac1(uint8_t out[SDTP_MAC_LEN], const uint8_t peer_static_pk[SDTP_KEY_LEN],
               const uint8_t *data, size_t data_len);

/* Derives the chaining-key + directional transport keys described in
 * PROTOCOL.md from three DH outputs and both parties' static public keys. */
void sdtp_derive_transport_keys(uint8_t key_i2r[SDTP_KEY_LEN], uint8_t key_r2i[SDTP_KEY_LEN],
                                 const uint8_t dh1[SDTP_KEY_LEN], const uint8_t dh2[SDTP_KEY_LEN],
                                 const uint8_t dh3[SDTP_KEY_LEN],
                                 const uint8_t i_static_pk[SDTP_KEY_LEN],
                                 const uint8_t r_static_pk[SDTP_KEY_LEN]);

/* X25519 scalar multiplication wrapper. Returns 0 on success, -1 if the
 * result is the all-zero point (degenerate public key, must be rejected). */
int sdtp_dh(uint8_t out[SDTP_KEY_LEN], const uint8_t sk[SDTP_KEY_LEN], const uint8_t pk[SDTP_KEY_LEN]);

/* AEAD encrypt/decrypt (XChaCha20-Poly1305-IETF). `nonce` is always
 * SDTP_NONCE_LEN bytes, caller-constructed per PROTOCOL.md. `ct` buffer must
 * have room for pt_len + SDTP_AEAD_TAG_LEN. Returns 0 on success. */
int sdtp_aead_encrypt(uint8_t *ct, const uint8_t *pt, size_t pt_len,
                       const uint8_t *aad, size_t aad_len,
                       const uint8_t nonce[SDTP_NONCE_LEN], const uint8_t key[SDTP_KEY_LEN]);

/* Returns 0 and writes pt_len bytes to pt on success, -1 on auth failure. */
int sdtp_aead_decrypt(uint8_t *pt, const uint8_t *ct, size_t ct_len,
                       const uint8_t *aad, size_t aad_len,
                       const uint8_t nonce[SDTP_NONCE_LEN], const uint8_t key[SDTP_KEY_LEN]);

#endif /* SDTP_CRYPTO_H */
