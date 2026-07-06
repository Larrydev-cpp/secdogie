#ifndef SDTP_H
#define SDTP_H

#include <stdint.h>
#include <stddef.h>

#define SDTP_KEY_LEN 32
#define SDTP_SESSION_ID_LEN 8
#define SDTP_MAC_LEN 16
#define SDTP_NONCE_LEN 24 /* crypto_aead_xchacha20poly1305_ietf_NPUBBYTES */
#define SDTP_AEAD_TAG_LEN 16
#define SDTP_MTU 1400 /* payload budget for the inner IP packet */

#define SDTP_MSG_HANDSHAKE_INIT 1
#define SDTP_MSG_HANDSHAKE_RESP 2
#define SDTP_MSG_DATA 3
#define SDTP_MSG_KEEPALIVE 4

#define SDTP_HANDSHAKE_TIMEOUT_S 5
#define SDTP_HANDSHAKE_WINDOW_S 60
#define SDTP_KEEPALIVE_INTERVAL_S 25
#define SDTP_REPLAY_WINDOW_BITS 2048

/* Wire sizes (all fields packed big-endian, no compiler padding relied on --
 * these are serialized by hand in wire.c, never memcpy'd as structs). */
#define SDTP_MSG1_LEN (1 + SDTP_SESSION_ID_LEN + SDTP_KEY_LEN + SDTP_KEY_LEN + 8 + SDTP_MAC_LEN)
#define SDTP_MSG2_LEN (1 + SDTP_SESSION_ID_LEN + SDTP_KEY_LEN + 16 + SDTP_AEAD_TAG_LEN)
#define SDTP_DATA_HDR_LEN (1 + SDTP_SESSION_ID_LEN + 8)
#define SDTP_MAX_DATAGRAM (SDTP_DATA_HDR_LEN + SDTP_MTU + SDTP_AEAD_TAG_LEN)

typedef struct {
    uint8_t pk[SDTP_KEY_LEN];
    uint8_t sk[SDTP_KEY_LEN];
} sdtp_keypair;

typedef struct {
    uint8_t session_id[SDTP_SESSION_ID_LEN];
    uint8_t key_i2r[SDTP_KEY_LEN];
    uint8_t key_r2i[SDTP_KEY_LEN];
    uint64_t send_counter;   /* next counter value we will send with */
    uint64_t recv_highest;   /* highest counter accepted so far */
    int recv_initialized;    /* 0 until the first data/keepalive packet arrives */
    uint8_t replay_bitmap[SDTP_REPLAY_WINDOW_BITS / 8];
    int is_initiator;        /* determines which key is "our send key" */
    int established;
} sdtp_session;

#endif /* SDTP_H */
