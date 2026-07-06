#ifndef SDTP_DATA_H
#define SDTP_DATA_H

#include "sdtp.h"

/* Encrypts `pt` (an IP packet read from TUN, or empty for a keepalive) into
 * `out` as a full SDTP datagram (type + session_id + counter + AEAD
 * ciphertext). `out` must have room for SDTP_DATA_HDR_LEN + pt_len +
 * SDTP_AEAD_TAG_LEN bytes. Returns the datagram length, or 0 if the send
 * counter has been exhausted (must not happen in practice: 2^64 packets). */
size_t sdtp_data_encrypt(sdtp_session *s, uint8_t type, uint8_t *out,
                          const uint8_t *pt, size_t pt_len);

/* Decrypts and replay-checks an incoming datagram. Writes up to `pt_cap`
 * bytes to `pt` and returns the plaintext length via *pt_len (0 for a
 * keepalive). Returns 0 on success, -1 on any failure (wrong session,
 * bad type, auth failure, or replay). */
int sdtp_data_decrypt(sdtp_session *s, const uint8_t *in, size_t in_len,
                       uint8_t *pt, size_t pt_cap, size_t *pt_len);

#endif /* SDTP_DATA_H */
