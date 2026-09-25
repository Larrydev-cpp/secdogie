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
#include <sodium.h>

#include "sdtp.h"
#include "crypto.h"
#include "handshake.h"
#include "data.h"
#include "hub.h"
#include "net.h"
#include "peer_state.h"
#include "util.h"

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

static void test_hub_parse_ipv4_src(void) {
    uint8_t pkt[20];
    memset(pkt, 0, sizeof(pkt));
    pkt[0] = 0x45; /* version 4, IHL 5 */
    pkt[12] = 10; pkt[13] = 66; pkt[14] = 0; pkt[15] = 2; /* source 10.66.0.2 */
    uint32_t src = 0;
    CHECK(sdtp_hub_parse_ipv4_src(pkt, sizeof(pkt), &src) == 0, "parse_ipv4_src accepts a v4 packet");
    uint8_t *b = (uint8_t *)&src;
    CHECK(b[0] == 10 && b[1] == 66 && b[2] == 0 && b[3] == 2, "parsed source is network-order 10.66.0.2");

    CHECK(sdtp_hub_parse_ipv4_src(pkt, 19, &src) == -1, "parse_ipv4_src rejects a too-short buffer");
    pkt[0] = 0x60; /* version 6 */
    CHECK(sdtp_hub_parse_ipv4_src(pkt, sizeof(pkt), &src) == -1, "parse_ipv4_src rejects a non-IPv4 packet");

    /* Cryptokey routing: a source that isn't the sender's tunnel IP is spoofed;
     * the hub compares this parsed value to peers[idx].tunnel_ip and drops the
     * packet on a mismatch. */
    pkt[0] = 0x45; pkt[15] = 9; /* source now 10.66.0.9, not the sender's 10.66.0.2 */
    CHECK(sdtp_hub_parse_ipv4_src(pkt, sizeof(pkt), &src) == 0 && ((uint8_t *)&src)[3] == 9,
          "a spoofed source (not the sender's tunnel IP) is parsed for the hub's check");
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
    memcpy(peers[1].ps.current.session_id, sid, SDTP_SESSION_ID_LEN);
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == -1, "a slot without a session is not matched by session_id");
    peers[1].ps.has_current = 1;
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == 1, "a peer is matched by its current session_id");
    peers[1].ps.has_current = 0;
    memcpy(peers[1].ps.pending.session_id, sid, SDTP_SESSION_ID_LEN);
    peers[1].ps.has_pending = 1;
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == 1, "a peer is matched by its pending session_id");
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
        for (size_t i = 0; i < 2; i++) {
            if (sdtp_peer_respond(&peers[i].ps, msg1, sizeof(msg1), &hub_kp, peers[i].static_pk, msg2) > 0) {
                matched = (int)i;
                break;
            }
        }
        CHECK(matched == which, "hub matches each client handshake to its own peer slot");
        sdtp_session *cs = which == 0 ? &c1_sess : &c2_sess;
        sdtp_handshake_finish(&hs, msg2, sizeof(msg2), ckp, hub_kp.pk, cs);
        /* the client's confirming keepalive promotes the hub's pending session */
        uint8_t ka[SDTP_MAX_DATAGRAM];
        size_t kal = sdtp_data_encrypt(cs, SDTP_MSG_KEEPALIVE, ka, NULL, 0);
        uint8_t scratch[SDTP_MTU];
        size_t sl = 0;
        CHECK(sdtp_peer_decrypt(&peers[matched].ps, ka, kal, NULL, scratch, sizeof(scratch), &sl)
                  == SDTP_PEER_OK_PROMOTED, "client keepalive confirms and promotes the hub session");
    }

    /* Client 1 sends a packet; the hub must route it to peer slot 0 and decrypt. */
    const char *p1 = "packet from client 1";
    uint8_t dg1[SDTP_MAX_DATAGRAM];
    size_t dl1 = sdtp_data_encrypt(&c1_sess, SDTP_MSG_DATA, dg1, (const uint8_t *)p1, strlen(p1));
    int idx1 = sdtp_hub_find_peer_by_session_id(peers, 2, dg1 + 1);
    CHECK(idx1 == 0, "client 1's datagram demuxes to peer slot 0 by session_id");

    uint8_t out[SDTP_MTU];
    size_t out_len = 0;
    CHECK(sdtp_data_decrypt(&peers[idx1].ps.current, dg1, dl1, out, sizeof(out), &out_len) == 0
              && out_len == strlen(p1) && memcmp(out, p1, out_len) == 0,
          "hub decrypts client 1's packet with the matched session");

    /* Client 2's packet demuxes to slot 1, and must NOT decrypt under slot 0. */
    const char *p2 = "packet from client 2";
    uint8_t dg2[SDTP_MAX_DATAGRAM];
    size_t dl2 = sdtp_data_encrypt(&c2_sess, SDTP_MSG_DATA, dg2, (const uint8_t *)p2, strlen(p2));
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, dg2 + 1) == 1,
          "client 2's datagram demuxes to peer slot 1 by session_id");
    CHECK(sdtp_data_decrypt(&peers[0].ps.current, dg2, dl2, out, sizeof(out), &out_len) != 0,
          "client 2's packet does not decrypt under client 1's session");
}


/* ---- v2 handshake authentication + pending/confirm (the T1 fix) ---------- */

/* What an attacker who knows both PUBLIC keys could build before v2: a msg1
 * naming the victim's static key, with mac1 keyed only by the responder's
 * public key (the v1 scheme). */
static void forge_v1_style_msg1(uint8_t msg1[SDTP_MSG1_LEN], const uint8_t victim_pk[SDTP_KEY_LEN],
                                const uint8_t responder_pk[SDTP_KEY_LEN]) {
    sdtp_keypair eph;
    sdtp_keypair_generate(&eph);
    msg1[0] = SDTP_MSG_HANDSHAKE_INIT;
    randombytes_buf(msg1 + 1, SDTP_SESSION_ID_LEN);
    memcpy(msg1 + 9, victim_pk, SDTP_KEY_LEN);
    memcpy(msg1 + 41, eph.pk, SDTP_KEY_LEN);
    sdtp_put_u64be(msg1 + 73, sdtp_now_ns() + 50ULL * 1000000000ULL); /* far ahead, still in window */
    static const char label[] = "SDTP-mac1";
    uint8_t buf[sizeof(label) - 1 + SDTP_KEY_LEN], key[SDTP_KEY_LEN];
    memcpy(buf, label, sizeof(label) - 1);
    memcpy(buf + sizeof(label) - 1, responder_pk, SDTP_KEY_LEN);
    crypto_generichash(key, sizeof(key), buf, sizeof(buf), NULL, 0);
    crypto_generichash(msg1 + 81, SDTP_MAC_LEN, msg1, 81, key, sizeof(key));
}

static void test_forged_handshake_init_rejected(void) {
    sdtp_keypair i_kp, r_kp, mallory;
    sdtp_keypair_generate(&i_kp);
    sdtp_keypair_generate(&r_kp);
    sdtp_keypair_generate(&mallory);

    uint64_t last_ts = 0;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_session s;

    forge_v1_style_msg1(msg1, i_kp.pk, r_kp.pk);
    CHECK(sdtp_handshake_respond(msg1, sizeof(msg1), &r_kp, i_kp.pk, &last_ts, msg2, &s) == 0,
          "msg1 forged from public keys alone (v1-style mac1) is rejected");
    CHECK(last_ts == 0, "a forged msg1 cannot advance the replay guard");

    /* Mallory uses her own static key for the static-static MAC but claims to be I. */
    sdtp_handshake_state hs;
    sdtp_handshake_init_create(&hs, msg1, &mallory, r_kp.pk);
    memcpy(msg1 + 9, i_kp.pk, SDTP_KEY_LEN);
    CHECK(sdtp_handshake_respond(msg1, sizeof(msg1), &r_kp, i_kp.pk, &last_ts, msg2, &s) == 0,
          "msg1 whose mac1 was keyed with another static key is rejected");

    /* A degenerate (low-order) peer key cannot even produce a msg1. */
    sdtp_keypair zero_peer;
    memset(&zero_peer, 0, sizeof(zero_peer));
    CHECK(sdtp_handshake_init_create(&hs, msg1, &i_kp, zero_peer.pk) == 0,
          "init_create refuses a degenerate peer public key");
}

static struct sockaddr_in addr_of(uint8_t last_octet, uint16_t port) {
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    uint8_t ip[4] = {192, 0, 2, last_octet};
    memcpy(&a.sin_addr, ip, 4);
    return a;
}

/* Run a full initiator handshake against a responder peer_state, WITHOUT the
 * confirming keepalive. Returns the initiator's new session in *out. */
static void handshake_into(sdtp_peer_state *responder, const sdtp_keypair *i_kp,
                           const sdtp_keypair *r_kp, sdtp_session *out) {
    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_init_create(&hs, msg1, i_kp, r_kp->pk);
    CHECK(sdtp_peer_respond(responder, msg1, sizeof(msg1), r_kp, i_kp->pk, msg2) == SDTP_MSG2_LEN,
          "responder accepts msg1 into a pending session");
    CHECK(sdtp_handshake_finish(&hs, msg2, sizeof(msg2), i_kp, r_kp->pk, out), "initiator finishes");
}

static int send_and_recv(sdtp_session *from, sdtp_peer_state *to, const struct sockaddr_in *src,
                         const char *text) {
    uint8_t dg[SDTP_MAX_DATAGRAM], pt[SDTP_MTU];
    size_t len = sdtp_data_encrypt(from, SDTP_MSG_DATA, dg, (const uint8_t *)text, strlen(text));
    size_t pt_len = 0;
    int rc = sdtp_peer_decrypt(to, dg, len, src, pt, sizeof(pt), &pt_len);
    if (rc != SDTP_PEER_FAIL && (pt_len != strlen(text) || memcmp(pt, text, pt_len) != 0)) return -99;
    return rc;
}

static void test_pending_session_does_not_displace_live_one(void) {
    sdtp_keypair i_kp, r_kp;
    sdtp_keypair_generate(&i_kp);
    sdtp_keypair_generate(&r_kp);
    sdtp_peer_state r;
    memset(&r, 0, sizeof(r));
    struct sockaddr_in home = addr_of(10, 5000), roam = addr_of(20, 6000);

    /* first handshake: nothing is live until the initiator confirms */
    sdtp_session a;
    handshake_into(&r, &i_kp, &r_kp, &a);
    CHECK(!r.has_current && r.has_pending && !r.have_addr,
          "after msg1 only a pending session exists and no address is adopted");
    CHECK(send_and_recv(&a, &r, &home, "first") == SDTP_PEER_OK_PROMOTED,
          "first packet under the new keys promotes pending -> current");
    CHECK(r.has_current && !r.has_pending && r.have_addr
              && memcmp(&r.addr, &home, sizeof(home)) == 0,
          "the peer address is adopted from the authenticated packet");

    /* re-handshake: the live session keeps working until the new one is confirmed */
    sdtp_session b;
    handshake_into(&r, &i_kp, &r_kp, &b);
    CHECK(send_and_recv(&a, &r, &home, "still-a") == SDTP_PEER_OK_CURRENT,
          "the live session keeps decrypting while the new one is pending");
    uint8_t ka[SDTP_MAX_DATAGRAM];
    size_t kal = sdtp_data_encrypt(&a, SDTP_MSG_DATA, ka, (const uint8_t *)"late-a", 6); /* in flight */
    CHECK(send_and_recv(&b, &r, &roam, "b") == SDTP_PEER_OK_PROMOTED, "confirmation switches to the new session");
    CHECK(memcmp(&r.addr, &roam, sizeof(roam)) == 0, "roaming follows the authenticated packet");
    uint8_t pt[SDTP_MTU];
    size_t pl = 0;
    CHECK(sdtp_peer_decrypt(&r, ka, kal, &home, pt, sizeof(pt), &pl) == SDTP_PEER_OK_PREVIOUS,
          "a packet already in flight on the old session is still accepted (previous slot)");
    CHECK(memcmp(&r.addr, &roam, sizeof(roam)) == 0, "a late old-session packet does not move the address back");
}

static void test_replayed_msg1_cannot_touch_live_session(void) {
    sdtp_keypair i_kp, r_kp;
    sdtp_keypair_generate(&i_kp);
    sdtp_keypair_generate(&r_kp);
    sdtp_peer_state r;
    memset(&r, 0, sizeof(r));
    struct sockaddr_in home = addr_of(10, 5000);

    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_init_create(&hs, msg1, &i_kp, r_kp.pk);
    sdtp_peer_respond(&r, msg1, sizeof(msg1), &r_kp, i_kp.pk, msg2);
    sdtp_session a;
    sdtp_handshake_finish(&hs, msg2, sizeof(msg2), &i_kp, r_kp.pk, &a);
    send_and_recv(&a, &r, &home, "confirm");

    uint8_t forged[SDTP_MSG1_LEN];
    forge_v1_style_msg1(forged, i_kp.pk, r_kp.pk);
    CHECK(sdtp_peer_respond(&r, forged, sizeof(forged), &r_kp, i_kp.pk, msg2) == 0
              && sdtp_peer_respond(&r, msg1, sizeof(msg1), &r_kp, i_kp.pk, msg2) == 0,
          "forged and replayed msg1 are both rejected");
    CHECK(!r.has_pending && send_and_recv(&a, &r, &home, "alive") == SDTP_PEER_OK_CURRENT,
          "the live session is untouched and keeps working");
}

static void test_inner_source_check(void) {
    uint8_t pkt[20];
    memset(pkt, 0, sizeof(pkt));
    pkt[0] = 0x45;
    pkt[12] = 10; pkt[13] = 66; pkt[14] = 0; pkt[15] = 2;
    uint32_t peer_ip = ip_be(10, 66, 0, 2);
    CHECK(sdtp_inner_src_ok(pkt, sizeof(pkt), peer_ip), "inner packet from peer_address passes");
    pkt[15] = 99;
    CHECK(!sdtp_inner_src_ok(pkt, sizeof(pkt), peer_ip), "inner packet with a spoofed source is refused");
    pkt[0] = 0x60;
    CHECK(!sdtp_inner_src_ok(pkt, sizeof(pkt), peer_ip), "non-IPv4 inner packet is refused when enforcing");
}

static void test_parse_port(void) {
    uint16_t p = 0;
    CHECK(sdtp_parse_port("51820", 1, &p) == 0 && p == 51820, "parse_port accepts a normal port");
    CHECK(sdtp_parse_port("70000", 1, &p) != 0, "parse_port rejects 70000 instead of wrapping");
    CHECK(sdtp_parse_port("0", 1, &p) != 0 && sdtp_parse_port("0", 0, &p) == 0, "parse_port honours min");
    CHECK(sdtp_parse_port("12ab", 1, &p) != 0 && sdtp_parse_port("", 1, &p) != 0
              && sdtp_parse_port("-5", 0, &p) != 0, "parse_port rejects junk");
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
    test_hub_parse_ipv4_src();
    test_hub_peer_lookup();
    test_hub_two_client_session_demux();
    test_udp_recv_batch();
    test_forged_handshake_init_rejected();
    test_pending_session_does_not_displace_live_one();
    test_replayed_msg1_cannot_touch_live_session();
    test_inner_source_check();
    test_parse_port();

    if (failures) {
        fprintf(stderr, "\n%d check(s) FAILED\n", failures);
        return 1;
    }
    fprintf(stderr, "\nall checks passed\n");
    return 0;
}
