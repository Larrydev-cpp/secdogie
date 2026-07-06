/* Direct unit tests for the SDTP crypto/handshake/data-channel logic,
 * independent of TUN/UDP/OS networking (which is exercised separately via a
 * real client/server run -- see tunnel/README.md). */
#include <stdio.h>
#include <string.h>
#include <assert.h>

#include "sdtp.h"
#include "crypto.h"
#include "handshake.h"
#include "data.h"

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s (%s:%d)\n", msg, __FILE__, __LINE__); failures++; } \
    else { fprintf(stderr, "ok:   %s\n", msg); } \
} while (0)

static void do_handshake(sdtp_keypair *i_kp, sdtp_keypair *r_kp,
                          sdtp_session *i_session, sdtp_session *r_session) {
    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN];
    size_t n1 = sdtp_handshake_init_create(&hs, msg1, i_kp, r_kp->pk);
    CHECK(n1 == SDTP_MSG1_LEN, "msg1 has expected length");

    uint64_t last_ts = 0;
    uint8_t msg2[SDTP_MSG2_LEN];
    size_t n2 = sdtp_handshake_respond(msg1, n1, r_kp, i_kp->pk, &last_ts, msg2, r_session);
    CHECK(n2 == SDTP_MSG2_LEN, "responder accepts msg1 and produces msg2");

    int ok = sdtp_handshake_finish(&hs, msg2, n2, i_kp, r_kp->pk, i_session);
    CHECK(ok, "initiator accepts msg2 and completes handshake");

    CHECK(memcmp(i_session->key_i2r, r_session->key_i2r, SDTP_KEY_LEN) == 0, "key_i2r matches on both sides");
    CHECK(memcmp(i_session->key_r2i, r_session->key_r2i, SDTP_KEY_LEN) == 0, "key_r2i matches on both sides");
}

static void test_happy_path(void) {
    sdtp_keypair i_kp, r_kp;
    sdtp_keypair_generate(&i_kp);
    sdtp_keypair_generate(&r_kp);
    sdtp_session i_session, r_session;

    do_handshake(&i_kp, &r_kp, &i_session, &r_session);

    const char *plaintext = "hello over the tunnel, this stands in for an IP packet";
    size_t pt_len = strlen(plaintext);
    uint8_t datagram[SDTP_MAX_DATAGRAM];
    size_t dlen = sdtp_data_encrypt(&i_session, SDTP_MSG_DATA, datagram, (const uint8_t *)plaintext, pt_len);
    CHECK(dlen == SDTP_DATA_HDR_LEN + pt_len + SDTP_AEAD_TAG_LEN, "data_encrypt returns expected length");

    uint8_t recovered[SDTP_MTU];
    size_t recovered_len = 0;
    int rc = sdtp_data_decrypt(&r_session, datagram, dlen, recovered, sizeof(recovered), &recovered_len);
    CHECK(rc == 0, "responder decrypts initiator's data packet");
    CHECK(recovered_len == pt_len && memcmp(recovered, plaintext, pt_len) == 0, "decrypted plaintext matches");

    /* Replay: sending the exact same datagram again must be rejected. */
    rc = sdtp_data_decrypt(&r_session, datagram, dlen, recovered, sizeof(recovered), &recovered_len);
    CHECK(rc != 0, "exact replay of a data packet is rejected");

    /* Tamper: flip a ciphertext bit, must fail AEAD auth. */
    uint8_t tampered[SDTP_MAX_DATAGRAM];
    memcpy(tampered, datagram, dlen);
    tampered[dlen - 1] ^= 0x01;
    rc = sdtp_data_decrypt(&r_session, tampered, dlen, recovered, sizeof(recovered), &recovered_len);
    CHECK(rc != 0, "tampered ciphertext fails authentication");

    /* Reply in the other direction too. */
    const char *reply = "ack";
    uint8_t datagram2[SDTP_MAX_DATAGRAM];
    size_t dlen2 = sdtp_data_encrypt(&r_session, SDTP_MSG_DATA, datagram2, (const uint8_t *)reply, strlen(reply));
    rc = sdtp_data_decrypt(&i_session, datagram2, dlen2, recovered, sizeof(recovered), &recovered_len);
    CHECK(rc == 0 && recovered_len == strlen(reply) && memcmp(recovered, reply, strlen(reply)) == 0,
          "responder->initiator data packet decrypts correctly");
}

static void test_wrong_peer_rejected(void) {
    sdtp_keypair i_kp, r_kp, mallory_kp;
    sdtp_keypair_generate(&i_kp);
    sdtp_keypair_generate(&r_kp);
    sdtp_keypair_generate(&mallory_kp);

    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN];
    sdtp_handshake_init_create(&hs, msg1, &i_kp, r_kp.pk);

    /* Responder configured to expect mallory's pubkey, not the initiator's. */
    uint64_t last_ts = 0;
    uint8_t msg2[SDTP_MSG2_LEN];
    sdtp_session r_session;
    size_t n2 = sdtp_handshake_respond(msg1, sizeof(msg1), &r_kp, mallory_kp.pk, &last_ts, msg2, &r_session);
    CHECK(n2 == 0, "handshake_respond rejects a peer pubkey that isn't the configured one");
}

static void test_replayed_handshake_rejected(void) {
    sdtp_keypair i_kp, r_kp;
    sdtp_keypair_generate(&i_kp);
    sdtp_keypair_generate(&r_kp);

    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN];
    sdtp_handshake_init_create(&hs, msg1, &i_kp, r_kp.pk);

    uint64_t last_ts = 0;
    uint8_t msg2[SDTP_MSG2_LEN];
    sdtp_session r_session;
    size_t n2 = sdtp_handshake_respond(msg1, sizeof(msg1), &r_kp, i_kp.pk, &last_ts, msg2, &r_session);
    CHECK(n2 == SDTP_MSG2_LEN, "first handshake_init is accepted");

    sdtp_session r_session2;
    size_t n2b = sdtp_handshake_respond(msg1, sizeof(msg1), &r_kp, i_kp.pk, &last_ts, msg2, &r_session2);
    CHECK(n2b == 0, "replaying the same handshake_init is rejected (timestamp not advancing)");
}

int main(void) {
    if (sdtp_crypto_init() != 0) {
        fprintf(stderr, "crypto init failed\n");
        return 1;
    }
    test_happy_path();
    test_wrong_peer_rejected();
    test_replayed_handshake_rejected();

    if (failures) {
        fprintf(stderr, "\n%d check(s) FAILED\n", failures);
        return 1;
    }
    fprintf(stderr, "\nall checks passed\n");
    return 0;
}
