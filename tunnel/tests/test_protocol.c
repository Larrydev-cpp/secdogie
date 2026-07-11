/* Direct unit tests for the SDTP crypto/handshake/data-channel logic,
 * independent of TUN/UDP/OS networking (which is exercised separately via a
 * real client/server run -- see tunnel/README.md). */
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <unistd.h>
#include <poll.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#include "sdtp.h"
#include "crypto.h"
#include "handshake.h"
#include "data.h"
#include "hub.h"
#include "net.h"

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

/* Build a minimal IPv4 header with the given destination address (dotted
 * bytes a.b.c.d). Only the fields the hub router reads are set. */
static void make_ipv4(uint8_t *pkt, uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
    memset(pkt, 0, 20);
    pkt[0] = 0x45; /* version 4, IHL 5 */
    pkt[16] = a; pkt[17] = b; pkt[18] = c; pkt[19] = d;
}

static void test_hub_parse_ipv4_dst(void) {
    uint8_t pkt[20];
    make_ipv4(pkt, 10, 66, 0, 3);
    uint32_t dst = 0;
    CHECK(sdtp_hub_parse_ipv4_dst(pkt, sizeof(pkt), &dst) == 0, "parse_ipv4_dst accepts a v4 packet");
    uint8_t *b = (uint8_t *)&dst;
    CHECK(b[0] == 10 && b[1] == 66 && b[2] == 0 && b[3] == 3, "parsed destination is network-order 10.66.0.3");

    CHECK(sdtp_hub_parse_ipv4_dst(pkt, 19, &dst) == -1, "parse_ipv4_dst rejects a too-short buffer");
    pkt[0] = 0x60; /* version 6 */
    CHECK(sdtp_hub_parse_ipv4_dst(pkt, sizeof(pkt), &dst) == -1, "parse_ipv4_dst rejects a non-IPv4 packet");
}

static uint32_t ip_be(uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
    uint8_t bytes[4] = {a, b, c, d};
    uint32_t v;
    memcpy(&v, bytes, 4);
    return v;
}

static void test_hub_peer_lookup(void) {
    sdtp_hub_peer peers[2];
    memset(peers, 0, sizeof(peers));
    peers[0].tunnel_ip = ip_be(10, 66, 0, 2);
    peers[1].tunnel_ip = ip_be(10, 66, 0, 3);

    CHECK(sdtp_hub_find_peer_by_ip(peers, 2, ip_be(10, 66, 0, 3)) == 1, "find_peer_by_ip finds the right slot");
    CHECK(sdtp_hub_find_peer_by_ip(peers, 2, ip_be(10, 66, 0, 9)) == -1, "find_peer_by_ip returns -1 for an unknown IP");

    /* session_id lookup only considers established peers. */
    uint8_t sid[SDTP_SESSION_ID_LEN] = {1, 2, 3, 4, 5, 6, 7, 8};
    memcpy(peers[1].session.session_id, sid, SDTP_SESSION_ID_LEN);
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == -1, "unestablished peer is not matched by session_id");
    peers[1].established = 1;
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == 1, "established peer is matched by its session_id");
}

/* The core hub property: two clients terminate independent tunnels on one hub,
 * and the hub demuxes each client's data packet to the correct session purely
 * by the session_id in the datagram header. */
static void test_hub_two_client_session_demux(void) {
    sdtp_keypair hub_kp, c1_kp, c2_kp;
    sdtp_keypair_generate(&hub_kp);
    sdtp_keypair_generate(&c1_kp);
    sdtp_keypair_generate(&c2_kp);

    sdtp_hub_peer peers[2];
    memset(peers, 0, sizeof(peers));
    memcpy(peers[0].static_pk, c1_kp.pk, SDTP_KEY_LEN);
    memcpy(peers[1].static_pk, c2_kp.pk, SDTP_KEY_LEN);

    /* Each client handshakes; the hub responds by trying each configured key,
     * exactly as sdtp_hub_run does. */
    sdtp_session c1_sess, c2_sess;
    for (int which = 0; which < 2; which++) {
        sdtp_keypair *ckp = which == 0 ? &c1_kp : &c2_kp;
        sdtp_handshake_state hs;
        uint8_t msg1[SDTP_MSG1_LEN];
        sdtp_handshake_init_create(&hs, msg1, ckp, hub_kp.pk);

        int matched = -1;
        uint8_t msg2[SDTP_MSG2_LEN];
        sdtp_session new_session;
        for (size_t i = 0; i < 2; i++) {
            if (sdtp_handshake_respond(msg1, sizeof(msg1), &hub_kp, peers[i].static_pk,
                                        &peers[i].last_peer_ts, msg2, &new_session) > 0) {
                peers[i].session = new_session;
                peers[i].established = 1;
                matched = (int)i;
                break;
            }
        }
        CHECK(matched == which, "hub matches each client handshake to its own peer slot");
        sdtp_session *cs = which == 0 ? &c1_sess : &c2_sess;
        sdtp_handshake_finish(&hs, msg2, sizeof(msg2), ckp, hub_kp.pk, cs);
    }

    /* Client 1 sends a packet; the hub must route it to peer slot 0 and decrypt. */
    const char *p1 = "packet from client 1";
    uint8_t dg1[SDTP_MAX_DATAGRAM];
    size_t dl1 = sdtp_data_encrypt(&c1_sess, SDTP_MSG_DATA, dg1, (const uint8_t *)p1, strlen(p1));
    int idx1 = sdtp_hub_find_peer_by_session_id(peers, 2, dg1 + 1);
    CHECK(idx1 == 0, "client 1's datagram demuxes to peer slot 0 by session_id");

    uint8_t out[SDTP_MTU];
    size_t out_len = 0;
    CHECK(sdtp_data_decrypt(&peers[idx1].session, dg1, dl1, out, sizeof(out), &out_len) == 0
              && out_len == strlen(p1) && memcmp(out, p1, out_len) == 0,
          "hub decrypts client 1's packet with the matched session");

    /* Client 2's packet demuxes to slot 1, and must NOT decrypt under slot 0. */
    const char *p2 = "packet from client 2";
    uint8_t dg2[SDTP_MAX_DATAGRAM];
    size_t dl2 = sdtp_data_encrypt(&c2_sess, SDTP_MSG_DATA, dg2, (const uint8_t *)p2, strlen(p2));
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, dg2 + 1) == 1,
          "client 2's datagram demuxes to peer slot 1 by session_id");
    CHECK(sdtp_data_decrypt(&peers[0].session, dg2, dl2, out, sizeof(out), &out_len) != 0,
          "client 2's packet does not decrypt under client 1's session");
}

/* Exercises the recvmmsg batch drain against real loopback UDP sockets (no TUN,
 * no root) -- the same call path the run loops use, so this proves the syscall
 * batching actually works on this OS, not just that the wrapper compiles. */
static void test_udp_recv_batch(void) {
    int rx = sdtp_udp_bind(0); /* ephemeral port */
    CHECK(rx >= 0, "udp bind for batch test");

    struct sockaddr_in raddr;
    socklen_t rlen = sizeof(raddr);
    getsockname(rx, (struct sockaddr *)&raddr, &rlen);
    raddr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    int tx = socket(AF_INET, SOCK_DGRAM, 0);
    CHECK(tx >= 0, "udp sender socket");

    const int N = 8;
    for (int i = 0; i < N; i++) {
        uint8_t payload[4] = {(uint8_t)i, 0xAB, 0xCD, (uint8_t)(i * 7)};
        sendto(tx, payload, sizeof(payload), 0, (struct sockaddr *)&raddr, sizeof(raddr));
    }

    struct pollfd pfd = {.fd = rx, .events = POLLIN};
    poll(&pfd, 1, 1000);

    sdtp_udp_msg batch[SDTP_RECV_BATCH];
    int count = sdtp_udp_recv_batch(rx, batch, SDTP_RECV_BATCH);
    CHECK(count == N, "recv_batch drained all N datagrams in a single call");

    int intact = (count == N);
    for (int i = 0; i < count && intact; i++) {
        if (batch[i].len != 4 || batch[i].buf[0] != (uint8_t)i || batch[i].buf[3] != (uint8_t)(i * 7)) {
            intact = 0;
        }
    }
    CHECK(intact, "batched datagrams arrive intact and in send order");

    int empty = sdtp_udp_recv_batch(rx, batch, SDTP_RECV_BATCH);
    CHECK(empty == 0, "recv_batch on a drained socket returns 0 without blocking");

    close(tx);
    close(rx);
}

int main(void) {
    if (sdtp_crypto_init() != 0) {
        fprintf(stderr, "crypto init failed\n");
        return 1;
    }
    test_happy_path();
    test_wrong_peer_rejected();
    test_replayed_handshake_rejected();
    test_hub_parse_ipv4_dst();
    test_hub_peer_lookup();
    test_hub_two_client_session_demux();
    test_udp_recv_batch();

    if (failures) {
        fprintf(stderr, "\n%d check(s) FAILED\n", failures);
        return 1;
    }
    fprintf(stderr, "\nall checks passed\n");
    return 0;
}
