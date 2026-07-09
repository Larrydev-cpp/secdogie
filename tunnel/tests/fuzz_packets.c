/* Adversarial-packet fuzz harness for the SDTP parsing/crypto/routing surface.
 *
 * The tunnel takes bytes straight off a public UDP socket, so the real memory
 * risk is a malformed datagram driving an out-of-bounds read/write in the
 * parsers -- there is no malloc/free or threading to race (the peer table is a
 * fixed array and the loop is single-threaded poll). This harness blasts every
 * function that touches attacker-controlled bytes with:
 *   - fully random datagrams of every length from 0 up past the max,
 *   - a valid encrypted datagram with random single/multi-byte corruption,
 *   - random handshake-length blobs,
 *   - random inner IP packets for the hub router.
 * Built under -fsanitize=address,undefined,leak it turns any overread, overflow,
 * UB, or leak into a hard failure. Run it via `ctest` when SDTP_SANITIZE=ON, or
 * standalone: it exits non-zero only if a sanitizer trips.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sdtp.h"
#include "crypto.h"
#include "handshake.h"
#include "data.h"
#include "hub.h"

/* Small deterministic PRNG so a failing run reproduces exactly. */
static uint64_t g_rng = 0x9e3779b97f4a7c15ULL;
static uint32_t xrand(void) {
    g_rng ^= g_rng << 13;
    g_rng ^= g_rng >> 7;
    g_rng ^= g_rng << 17;
    return (uint32_t)(g_rng >> 32);
}
static void fill_random(uint8_t *buf, size_t n) {
    for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)xrand();
}

#define MAX_FUZZ_LEN 2048

static void establish(sdtp_session *i_session, sdtp_session *r_session,
                      sdtp_keypair *i_kp, sdtp_keypair *r_kp) {
    sdtp_keypair_generate(i_kp);
    sdtp_keypair_generate(r_kp);
    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    uint64_t last_ts = 0;
    sdtp_handshake_init_create(&hs, msg1, i_kp, r_kp->pk);
    sdtp_handshake_respond(msg1, sizeof(msg1), r_kp, i_kp->pk, &last_ts, msg2, r_session);
    sdtp_handshake_finish(&hs, msg2, sizeof(msg2), i_kp, r_kp->pk, i_session);
}

int main(int argc, char **argv) {
    if (sdtp_crypto_init() != 0) {
        fprintf(stderr, "crypto init failed\n");
        return 1;
    }
    long iterations = (argc > 1) ? strtol(argv[1], NULL, 10) : 300000;

    sdtp_session i_session, r_session;
    sdtp_keypair i_kp, r_kp;
    establish(&i_session, &r_session, &i_kp, &r_kp);

    /* One genuinely valid data datagram to corrupt later. */
    uint8_t valid[SDTP_MAX_DATAGRAM];
    const uint8_t payload[64] = {0};
    size_t valid_len = sdtp_data_encrypt(&i_session, SDTP_MSG_DATA, valid, payload, sizeof(payload));

    uint8_t buf[MAX_FUZZ_LEN];
    uint8_t pt[SDTP_MTU];
    uint8_t msg2[SDTP_MSG2_LEN];

    sdtp_hub_peer peers[4];
    memset(peers, 0, sizeof(peers));

    for (long it = 0; it < iterations; it++) {
        size_t len = xrand() % (MAX_FUZZ_LEN + 1);
        fill_random(buf, len);

        /* 1) fully random bytes into the data decryptor -- must reject, never crash. */
        size_t pt_len = 0;
        /* fresh receiver each time so replay-window state can't reject early and
         * skip the parse we want to exercise. */
        sdtp_session rs = r_session;
        rs.recv_initialized = 0;
        sdtp_data_decrypt(&rs, buf, len, pt, sizeof(pt), &pt_len);

        /* 2) a valid datagram with random corruption. */
        if (valid_len > 0) {
            uint8_t corrupt[SDTP_MAX_DATAGRAM];
            memcpy(corrupt, valid, valid_len);
            int flips = 1 + (xrand() % 8);
            for (int f = 0; f < flips; f++) corrupt[xrand() % valid_len] ^= (uint8_t)(1u << (xrand() % 8));
            size_t clen = valid_len - (xrand() % 3); /* sometimes truncate too */
            sdtp_session rs2 = r_session;
            rs2.recv_initialized = 0;
            sdtp_data_decrypt(&rs2, corrupt, clen, pt, sizeof(pt), &pt_len);
        }

        /* 3) random blob into the handshake responder. */
        uint64_t last_ts = xrand();
        sdtp_session hsess;
        sdtp_handshake_respond(buf, len, &r_kp, i_kp.pk, &last_ts, msg2, &hsess);

        /* 4) random inner IP packet into the hub router + lookups. */
        uint32_t dst = 0;
        sdtp_hub_parse_ipv4_dst(buf, len, &dst);
        if (len >= SDTP_SESSION_ID_LEN) {
            sdtp_hub_find_peer_by_session_id(peers, 4, buf);
        }
        sdtp_hub_find_peer_by_ip(peers, 4, dst);
    }

    fprintf(stderr, "fuzz ok: %ld iterations, no sanitizer fault\n", iterations);
    return 0;
}
