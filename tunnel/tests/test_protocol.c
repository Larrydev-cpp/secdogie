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
#include "responder.h"
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

    /* session_id lookup only considers live (confirmed or pending) sessions. */
    uint8_t sid[SDTP_SESSION_ID_LEN] = {1, 2, 3, 4, 5, 6, 7, 8};
    memcpy(peers[1].rs.current.session_id, sid, SDTP_SESSION_ID_LEN);
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == -1, "an idle slot is not matched by session_id");
    peers[1].rs.current.established = 1;
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid) == 1, "a confirmed session is matched by its session_id");
    uint8_t sid2[SDTP_SESSION_ID_LEN] = {9, 9, 9, 9, 9, 9, 9, 9};
    memcpy(peers[0].rs.pending.session_id, sid2, SDTP_SESSION_ID_LEN);
    peers[0].rs.has_pending = 1;
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, sid2) == 0, "a pending session is matched by its session_id");
}

/* --- confirm-before-swap: the handshake_init forgery fix --------------------
 *
 * A v1 message 1 does not prove possession of the initiator's static private
 * key, so anyone who knows both public keys (the initiator's travels in clear
 * in every message 1) can build one that passes validation. These tests play
 * that attacker and check the responder contains it: the forged message only
 * parks a pending session nobody can confirm. */

static struct sockaddr_in addr_of(uint8_t last_octet, uint16_t port) {
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(0xC0000200u | last_octet); /* 192.0.2.x (TEST-NET-1) */
    return a;
}

static int same_addr(const struct sockaddr_in *a, const struct sockaddr_in *b) {
    return a->sin_addr.s_addr == b->sin_addr.s_addr && a->sin_port == b->sin_port;
}

/* The attacker claims the victim's identity without its private key. `ts` != 0
 * overrides the timestamp (mac1 is keyed by the responder's PUBLIC key, so the
 * attacker can recompute it). */
static void forge_msg1(uint8_t out[SDTP_MSG1_LEN], sdtp_handshake_state *hs, sdtp_keypair *fake,
                       const uint8_t victim_pk[SDTP_KEY_LEN], const uint8_t responder_pk[SDTP_KEY_LEN],
                       uint64_t ts) {
    sdtp_keypair_generate(fake);
    memcpy(fake->pk, victim_pk, SDTP_KEY_LEN);
    sdtp_handshake_init_create(hs, out, fake, responder_pk);
    if (ts) {
        sdtp_put_u64be(out + 73, ts);
        sdtp_mac1(out + 81, responder_pk, out, 81);
    }
}

/* One datagram from `sess` into the responder; returns the SDTP_RESP_* code,
 * or SDTP_RESP_DROP if the plaintext did not survive intact. */
static int send_to_responder(sdtp_responder *r, sdtp_session *sess, const struct sockaddr_in *from,
                             uint8_t type, const char *text) {
    uint8_t dg[SDTP_MAX_DATAGRAM];
    size_t tl = text ? strlen(text) : 0;
    size_t dl = sdtp_data_encrypt(sess, type, dg, (const uint8_t *)text, tl);
    uint8_t pt[SDTP_MTU];
    size_t pl = 0;
    int res = sdtp_responder_on_data(r, dg, dl, from, pt, sizeof(pt), &pl);
    if (res != SDTP_RESP_DROP && (pl != tl || (tl && memcmp(pt, text, tl) != 0))) return SDTP_RESP_DROP;
    return res;
}

/* A real client: handshake through the responder, then confirm with the first
 * keepalive, as the client loop does right after message 2. */
static int responder_connect(sdtp_responder *r, const sdtp_keypair *srv, const sdtp_keypair *cli,
                             sdtp_session *cli_sess, const struct sockaddr_in *cli_addr, uint8_t msg1_out[SDTP_MSG1_LEN]) {
    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_init_create(&hs, msg1, cli, srv->pk);
    if (msg1_out) memcpy(msg1_out, msg1, sizeof(msg1));
    if (sdtp_responder_on_init(r, srv, cli->pk, msg1, sizeof(msg1), msg2) != SDTP_MSG2_LEN) return -1;
    if (!sdtp_handshake_finish(&hs, msg2, sizeof(msg2), cli, srv->pk, cli_sess)) return -2;
    return send_to_responder(r, cli_sess, cli_addr, SDTP_MSG_KEEPALIVE, NULL);
}

static void test_forged_msg1_cannot_displace_session(void) {
    sdtp_keypair srv, cli;
    sdtp_keypair_generate(&srv);
    sdtp_keypair_generate(&cli);
    sdtp_responder r;
    memset(&r, 0, sizeof(r));
    struct sockaddr_in home = addr_of(10, 40000), evil = addr_of(66, 6666);
    sdtp_session cs;

    CHECK(responder_connect(&r, &srv, &cli, &cs, &home, NULL) == SDTP_RESP_PROMOTED,
          "a real handshake is promoted by the client's first keepalive");
    uint64_t committed = r.committed_ts;

    uint8_t forged[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_state fhs;
    sdtp_keypair fake;
    forge_msg1(forged, &fhs, &fake, cli.pk, srv.pk, 0);
    CHECK(sdtp_responder_on_init(&r, &srv, cli.pk, forged, sizeof(forged), msg2) == SDTP_MSG2_LEN,
          "a forged handshake_init still passes v1 validation (the flaw being contained)");
    CHECK(r.current.established && r.has_pending && r.committed_ts == committed,
          "it only parks a pending session: the confirmed one and the committed timestamp are untouched");
    CHECK(send_to_responder(&r, &cs, &home, SDTP_MSG_DATA, "still here") == SDTP_RESP_OK,
          "the real client's traffic keeps decrypting on the confirmed session");
    CHECK(same_addr(&r.addr, &home), "the peer address is not redirected to the forger");

    sdtp_session evil_sess;
    CHECK(!sdtp_handshake_finish(&fhs, msg2, sizeof(msg2), &fake, srv.pk, &evil_sess),
          "the forger cannot complete the handshake without the initiator's private key");
    uint8_t junk[SDTP_DATA_HDR_LEN + 32];
    memset(junk, 0x5a, sizeof(junk));
    junk[0] = SDTP_MSG_KEEPALIVE;
    memcpy(junk + 1, r.pending.session_id, SDTP_SESSION_ID_LEN);
    uint8_t pt[SDTP_MTU];
    size_t pl = 0;
    CHECK(sdtp_responder_on_data(&r, junk, sizeof(junk), &evil, pt, sizeof(pt), &pl) == SDTP_RESP_DROP &&
              r.has_pending && same_addr(&r.addr, &home),
          "a packet on the pending session id that does not authenticate cannot promote it");
}

static void test_forged_future_timestamp_cannot_lock_out(void) {
    sdtp_keypair srv, cli;
    sdtp_keypair_generate(&srv);
    sdtp_keypair_generate(&cli);
    sdtp_responder r;
    memset(&r, 0, sizeof(r));
    struct sockaddr_in home = addr_of(10, 40000);
    sdtp_session cs;
    responder_connect(&r, &srv, &cli, &cs, &home, NULL);

    uint8_t forged[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_state fhs;
    sdtp_keypair fake;
    forge_msg1(forged, &fhs, &fake, cli.pk, srv.pk, sdtp_now_ns() + 59ULL * 1000000000ULL);
    CHECK(sdtp_responder_on_init(&r, &srv, cli.pk, forged, sizeof(forged), msg2) == SDTP_MSG2_LEN,
          "a forged handshake_init dated 59 s ahead is answered");

    sdtp_session cs2;
    CHECK(responder_connect(&r, &srv, &cli, &cs2, &home, NULL) == SDTP_RESP_PROMOTED,
          "the real client can still re-handshake right after it (v1 locked it out for ~60 s)");
    CHECK(send_to_responder(&r, &cs2, &home, SDTP_MSG_DATA, "fresh session") == SDTP_RESP_OK,
          "traffic flows on the re-handshaked session");
}

static void test_responder_rejects_replays(void) {
    sdtp_keypair srv, cli;
    sdtp_keypair_generate(&srv);
    sdtp_keypair_generate(&cli);
    sdtp_responder r;
    memset(&r, 0, sizeof(r));
    struct sockaddr_in home = addr_of(10, 40000), evil = addr_of(66, 6666);

    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN], msg2b[SDTP_MSG2_LEN];
    sdtp_handshake_init_create(&hs, msg1, &cli, srv.pk);
    CHECK(sdtp_responder_on_init(&r, &srv, cli.pk, msg1, sizeof(msg1), msg2) == SDTP_MSG2_LEN,
          "handshake_init is answered");
    CHECK(sdtp_responder_on_init(&r, &srv, cli.pk, msg1, sizeof(msg1), msg2b) == 0,
          "replaying it before confirmation cannot swap the pending session");
    sdtp_session cs;
    CHECK(sdtp_handshake_finish(&hs, msg2, sizeof(msg2), &cli, srv.pk, &cs) &&
              send_to_responder(&r, &cs, &home, SDTP_MSG_KEEPALIVE, NULL) == SDTP_RESP_PROMOTED,
          "so the real client's confirmation still lands");
    CHECK(sdtp_responder_on_init(&r, &srv, cli.pk, msg1, sizeof(msg1), msg2b) == 0,
          "replaying it after confirmation is rejected by the committed timestamp");

    uint8_t forged[SDTP_MSG1_LEN];
    sdtp_handshake_state fhs;
    sdtp_keypair fake;
    forge_msg1(forged, &fhs, &fake, cli.pk, srv.pk, 0);
    memcpy(forged + 1, r.current.session_id, SDTP_SESSION_ID_LEN);
    sdtp_mac1(forged + 81, srv.pk, forged, 81);
    CHECK(sdtp_responder_on_init(&r, &srv, cli.pk, forged, sizeof(forged), msg2b) == 0 && !r.has_pending,
          "a handshake_init reusing the live session id is rejected outright");
    CHECK(send_to_responder(&r, &cs, &evil, SDTP_MSG_DATA, "roamed") == SDTP_RESP_OK && same_addr(&r.addr, &evil),
          "an authenticated packet from a new address still roams the peer (NAT rebind)");
}

/* A client handshake + confirmation through the hub's own demux; returns the
 * slot it landed in, or -1. */
static int hub_connect(sdtp_hub_peer *peers, size_t n, const sdtp_keypair *hub, const sdtp_keypair *cli,
                       sdtp_session *cs) {
    sdtp_handshake_state hs;
    uint8_t msg1[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_init_create(&hs, msg1, cli, hub->pk);
    int slot = sdtp_hub_respond_init(peers, n, hub, msg1, sizeof(msg1), msg2);
    if (slot < 0 || !sdtp_handshake_finish(&hs, msg2, sizeof(msg2), cli, hub->pk, cs)) return -1;
    uint8_t ka[SDTP_MAX_DATAGRAM];
    size_t kl = sdtp_data_encrypt(cs, SDTP_MSG_KEEPALIVE, ka, NULL, 0);
    int idx = sdtp_hub_find_peer_by_session_id(peers, n, ka + 1);
    if (idx != slot) return -1;
    uint8_t pt[SDTP_MTU];
    size_t pl = 0;
    struct sockaddr_in from = addr_of((uint8_t)(20 + slot), 40000);
    return sdtp_responder_on_data(&peers[idx].rs, ka, kl, &from, pt, sizeof(pt), &pl) == SDTP_RESP_PROMOTED ? slot : -1;
}

static void test_hub_session_id_cannot_be_shadowed(void) {
    sdtp_keypair hub_kp, c1_kp, c2_kp;
    sdtp_keypair_generate(&hub_kp);
    sdtp_keypair_generate(&c1_kp);
    sdtp_keypair_generate(&c2_kp);
    sdtp_hub_peer peers[2];
    memset(peers, 0, sizeof(peers));
    memcpy(peers[0].static_pk, c1_kp.pk, SDTP_KEY_LEN);
    memcpy(peers[1].static_pk, c2_kp.pk, SDTP_KEY_LEN);
    sdtp_session c1_sess, c2_sess;
    CHECK(hub_connect(peers, 2, &hub_kp, &c1_kp, &c1_sess) == 0 && hub_connect(peers, 2, &hub_kp, &c2_kp, &c2_sess) == 1,
          "two clients connect to their own hub slots");

    /* Forged for client 1's slot, but carrying client 2's live session id (it
     * is visible in every one of client 2's data packets). */
    uint8_t forged[SDTP_MSG1_LEN], msg2[SDTP_MSG2_LEN];
    sdtp_handshake_state fhs;
    sdtp_keypair fake;
    forge_msg1(forged, &fhs, &fake, c1_kp.pk, hub_kp.pk, 0);
    memcpy(forged + 1, c2_sess.session_id, SDTP_SESSION_ID_LEN);
    sdtp_mac1(forged + 81, hub_kp.pk, forged, 81);
    CHECK(sdtp_hub_respond_init(peers, 2, &hub_kp, forged, sizeof(forged), msg2) == -1 && !peers[0].rs.has_pending,
          "a handshake_init reusing another slot's live session id is dropped");

    /* A forgery with a fresh id lands as slot 0's pending session, and both
     * clients' traffic still demuxes and decrypts on their confirmed sessions. */
    forge_msg1(forged, &fhs, &fake, c1_kp.pk, hub_kp.pk, 0);
    CHECK(sdtp_hub_respond_init(peers, 2, &hub_kp, forged, sizeof(forged), msg2) == 0 && peers[0].rs.has_pending,
          "a fresh forged handshake_init only parks a pending session in its slot");
    struct sockaddr_in a1 = addr_of(20, 40000), a2 = addr_of(21, 40000);
    CHECK(send_to_responder(&peers[0].rs, &c1_sess, &a1, SDTP_MSG_DATA, "c1") == SDTP_RESP_OK &&
              send_to_responder(&peers[1].rs, &c2_sess, &a2, SDTP_MSG_DATA, "c2") == SDTP_RESP_OK,
          "both clients keep working on their confirmed sessions");
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

    /* Each client handshakes and confirms with its first keepalive, through
     * the same demux sdtp_hub_run uses. */
    sdtp_session c1_sess, c2_sess;
    for (int which = 0; which < 2; which++) {
        sdtp_keypair *ckp = which == 0 ? &c1_kp : &c2_kp;
        sdtp_session *cs = which == 0 ? &c1_sess : &c2_sess;
        CHECK(hub_connect(peers, 2, &hub_kp, ckp, cs) == which,
              "hub matches each client handshake to its own peer slot and confirms it");
    }

    /* Client 1 sends a packet; the hub must route it to peer slot 0 and decrypt. */
    const char *p1 = "packet from client 1";
    uint8_t dg1[SDTP_MAX_DATAGRAM];
    size_t dl1 = sdtp_data_encrypt(&c1_sess, SDTP_MSG_DATA, dg1, (const uint8_t *)p1, strlen(p1));
    int idx1 = sdtp_hub_find_peer_by_session_id(peers, 2, dg1 + 1);
    CHECK(idx1 == 0, "client 1's datagram demuxes to peer slot 0 by session_id");

    uint8_t out[SDTP_MTU];
    size_t out_len = 0;
    CHECK(sdtp_data_decrypt(&peers[idx1].rs.current, dg1, dl1, out, sizeof(out), &out_len) == 0
              && out_len == strlen(p1) && memcmp(out, p1, out_len) == 0,
          "hub decrypts client 1's packet with the matched session");

    /* Client 2's packet demuxes to slot 1, and must NOT decrypt under slot 0. */
    const char *p2 = "packet from client 2";
    uint8_t dg2[SDTP_MAX_DATAGRAM];
    size_t dl2 = sdtp_data_encrypt(&c2_sess, SDTP_MSG_DATA, dg2, (const uint8_t *)p2, strlen(p2));
    CHECK(sdtp_hub_find_peer_by_session_id(peers, 2, dg2 + 1) == 1,
          "client 2's datagram demuxes to peer slot 1 by session_id");
    CHECK(sdtp_data_decrypt(&peers[0].rs.current, dg2, dl2, out, sizeof(out), &out_len) != 0,
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
    test_hub_parse_ipv4_src();
    test_hub_peer_lookup();
    test_hub_two_client_session_demux();
    test_forged_msg1_cannot_displace_session();
    test_forged_future_timestamp_cannot_lock_out();
    test_responder_rejects_replays();
    test_hub_session_id_cannot_be_shadowed();
    test_udp_recv_batch();

    if (failures) {
        fprintf(stderr, "\n%d check(s) FAILED\n", failures);
        return 1;
    }
    fprintf(stderr, "\nall checks passed\n");
    return 0;
}
