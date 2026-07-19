#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <poll.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sodium.h>

#include "hub.h"
#include "sdtp.h"
#include "handshake.h"
#include "data.h"
#include "net.h"
#include "util.h"

static volatile sig_atomic_t g_hub_should_exit = 0;
static void on_signal(int sig) {
    (void)sig;
    g_hub_should_exit = 1;
}

/* --- config parsing (small, self-contained; the point-to-point loader in
 * config.c uses a different single-peer shape) --- */

static char *trim(char *s) {
    while (*s == ' ' || *s == '\t') s++;
    char *end = s + strlen(s);
    while (end > s && (end[-1] == ' ' || end[-1] == '\t' || end[-1] == '\n' || end[-1] == '\r')) {
        *--end = '\0';
    }
    return s;
}

static int decode_key(const char *b64, uint8_t out[SDTP_KEY_LEN]) {
    size_t decoded_len = 0;
    if (sodium_base642bin(out, SDTP_KEY_LEN, b64, strlen(b64), NULL, &decoded_len, NULL,
                           sodium_base64_VARIANT_ORIGINAL) != 0) {
        return -1;
    }
    return decoded_len == SDTP_KEY_LEN ? 0 : -1;
}

/* Parse the address before the '/' of "10.66.0.1/24" into a network-order IP. */
static int parse_self_ip(const char *address, uint32_t *out) {
    char tmp[64];
    strncpy(tmp, address, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    char *slash = strchr(tmp, '/');
    if (slash) *slash = '\0';
    struct in_addr in;
    if (inet_pton(AF_INET, tmp, &in) != 1) return -1;
    *out = in.s_addr;
    return 0;
}

/* Parse a `peer = <base64 pubkey> <tunnel ip>` value into a peer slot. */
static int parse_peer_line(const char *val, sdtp_hub_peer *peer) {
    char tmp[512];
    strncpy(tmp, val, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';

    char *sp = tmp;
    while (*sp && *sp != ' ' && *sp != '\t') sp++;
    if (!*sp) return -1; /* no second token */
    *sp = '\0';
    char *ip_str = sp + 1;
    while (*ip_str == ' ' || *ip_str == '\t') ip_str++;

    if (decode_key(tmp, peer->static_pk) != 0) return -1;
    struct in_addr in;
    if (inet_pton(AF_INET, ip_str, &in) != 1) return -1;
    peer->tunnel_ip = in.s_addr;
    return 0;
}

int sdtp_hub_config_load(const char *path, sdtp_hub_config *cfg) {
    memset(cfg, 0, sizeof(*cfg));
    cfg->mtu = SDTP_MTU;

    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "cannot open config '%s': %s\n", path, strerror(errno));
        return -1;
    }

    int have_priv = 0, have_address = 0;
    char line[1024];
    int lineno = 0;
    while (fgets(line, sizeof(line), f)) {
        lineno++;
        char *s = trim(line);
        if (*s == '\0' || *s == '#') continue;

        char *eq = strchr(s, '=');
        if (!eq) {
            fprintf(stderr, "%s:%d: expected 'key = value'\n", path, lineno);
            fclose(f);
            return -1;
        }
        *eq = '\0';
        char *key = trim(s);
        char *val = trim(eq + 1);

        if (strcmp(key, "private_key") == 0) {
            uint8_t sk[SDTP_KEY_LEN];
            if (decode_key(val, sk) != 0) {
                fprintf(stderr, "%s:%d: invalid private_key (expected base64 32 bytes)\n", path, lineno);
                fclose(f);
                return -1;
            }
            memcpy(cfg->my_static.sk, sk, SDTP_KEY_LEN);
            crypto_scalarmult_base(cfg->my_static.pk, sk);
            sodium_memzero(sk, sizeof(sk));
            have_priv = 1;
        } else if (strcmp(key, "address") == 0) {
            strncpy(cfg->address, val, sizeof(cfg->address) - 1);
            if (parse_self_ip(cfg->address, &cfg->self_ip) != 0) {
                fprintf(stderr, "%s:%d: address must be a CIDR like 10.66.0.1/24\n", path, lineno);
                fclose(f);
                return -1;
            }
            have_address = 1;
        } else if (strcmp(key, "listen_port") == 0) {
            cfg->listen_port = (uint16_t)atoi(val);
        } else if (strcmp(key, "mtu") == 0) {
            cfg->mtu = atoi(val);
        } else if (strcmp(key, "ifname") == 0) {
            strncpy(cfg->ifname, val, sizeof(cfg->ifname) - 1);
        } else if (strcmp(key, "peer") == 0) {
            if (cfg->peer_count >= SDTP_HUB_MAX_PEERS) {
                fprintf(stderr, "%s:%d: too many peers (max %d)\n", path, lineno, SDTP_HUB_MAX_PEERS);
                fclose(f);
                return -1;
            }
            if (parse_peer_line(val, &cfg->peers[cfg->peer_count]) != 0) {
                fprintf(stderr, "%s:%d: peer must be '<base64 public key> <tunnel ip>'\n", path, lineno);
                fclose(f);
                return -1;
            }
            cfg->peer_count++;
        } else {
            fprintf(stderr, "%s:%d: unknown key '%s'\n", path, lineno, key);
            fclose(f);
            return -1;
        }
    }
    fclose(f);

    if (!have_priv || !have_address) {
        fprintf(stderr, "%s: missing required key(s): private_key, address\n", path);
        return -1;
    }
    if (cfg->listen_port == 0) {
        fprintf(stderr, "%s: hub config must set listen_port\n", path);
        return -1;
    }
    if (cfg->peer_count == 0) {
        fprintf(stderr, "%s: hub config must list at least one peer\n", path);
        return -1;
    }
    /* See config.c: an MTU above SDTP_MTU silently truncates oversized inner
     * packets (reads cap at SDTP_MTU), and a non-positive MTU makes the
     * interface ioctl fail obscurely. */
    if (cfg->mtu <= 0 || cfg->mtu > SDTP_MTU) {
        fprintf(stderr, "%s: mtu must be between 1 and %d\n", path, SDTP_MTU);
        return -1;
    }
    return 0;
}

/* --- event loop --- */

/* Encrypt one inner packet to a peer and send it, if that peer is reachable. */
static void forward_to_peer(int udp_fd, sdtp_hub_peer *peer, const uint8_t *pt, size_t pt_len,
                            uint8_t *out_buf, time_t now) {
    if (!peer->established || !peer->have_addr) return;
    size_t len = sdtp_data_encrypt(&peer->session, SDTP_MSG_DATA, out_buf, pt, pt_len);
    if (len > 0) {
        sendto(udp_fd, out_buf, len, 0, (struct sockaddr *)&peer->addr, sizeof(peer->addr));
        peer->last_send = now;
    }
}

/* Handle one received UDP datagram: a handshake_init (demux by trying each
 * configured peer's static key -- only the right one authenticates), or
 * data/keepalive (demux by the 8-byte session id, then route the decrypted
 * inner packet by destination IP). Returns early on any malformed or
 * unauthenticated input (exercised by the fuzz harness); factored out of the
 * run loop so a whole recvmmsg batch can be handled one datagram at a time. */
static void hub_handle_datagram(sdtp_hub_config *cfg, int tun_fd, int udp_fd,
                                const uint8_t *buf, ssize_t n, struct sockaddr_in src_addr,
                                socklen_t src_len, time_t now, uint8_t *out_buf, uint8_t *pt_buf) {
    if (n <= 0) return;
    uint8_t type = buf[0];

    if (type == SDTP_MSG_HANDSHAKE_INIT) {
        int matched = -1;
        uint8_t msg2[SDTP_MSG2_LEN];
        sdtp_session new_session;
        for (size_t i = 0; i < cfg->peer_count; i++) {
            sdtp_hub_peer *p = &cfg->peers[i];
            size_t rlen = sdtp_handshake_respond(buf, (size_t)n, &cfg->my_static, p->static_pk,
                                                  &p->last_peer_ts, msg2, &new_session);
            if (rlen > 0) {
                p->session = new_session;
                p->established = 1;
                p->addr = src_addr;
                p->have_addr = 1;
                p->last_recv = now;
                p->last_send = 0;
                sendto(udp_fd, msg2, rlen, 0, (struct sockaddr *)&src_addr, src_len);
                sdtp_log("hub: handshake completed with peer %zu (%s:%u)", i,
                         inet_ntoa(src_addr.sin_addr), ntohs(src_addr.sin_port));
                matched = (int)i;
                break;
            }
        }
        if (matched < 0) {
            sdtp_log("hub: rejected handshake_init from %s:%u (no configured peer matched)",
                     inet_ntoa(src_addr.sin_addr), ntohs(src_addr.sin_port));
        }
    } else if (type == SDTP_MSG_DATA || type == SDTP_MSG_KEEPALIVE) {
        if ((size_t)n < 1 + SDTP_SESSION_ID_LEN) return;
        int idx = sdtp_hub_find_peer_by_session_id(cfg->peers, cfg->peer_count, buf + 1);
        if (idx < 0) return;
        sdtp_hub_peer *p = &cfg->peers[idx];
        size_t pt_len = 0;
        if (sdtp_data_decrypt(&p->session, buf, (size_t)n, pt_buf, SDTP_MTU, &pt_len) != 0) {
            return;
        }
        p->last_recv = now;
        p->addr = src_addr; /* adopt the current source addr (NAT rebind) */
        p->have_addr = 1;
        if (type != SDTP_MSG_DATA || pt_len == 0) return;

        /* Route the decrypted inner packet by its destination IP. */
        uint32_t dst;
        if (sdtp_hub_parse_ipv4_dst(pt_buf, pt_len, &dst) != 0 || dst == cfg->self_ip) {
            /* For the hub itself, or anything we can't parse, hand it to the
             * local TUN and let the hub's kernel route it further. */
            write(tun_fd, pt_buf, pt_len);
            return;
        }
        int j = sdtp_hub_find_peer_by_ip(cfg->peers, cfg->peer_count, dst);
        if (j >= 0 && j != idx) {
            forward_to_peer(udp_fd, &cfg->peers[j], pt_buf, pt_len, out_buf, now);
        } else {
            write(tun_fd, pt_buf, pt_len);
        }
    }
}

void sdtp_hub_run(int tun_fd, int udp_fd, sdtp_hub_config *cfg) {
    uint8_t buf[SDTP_MAX_DATAGRAM];
    uint8_t out_buf[SDTP_MAX_DATAGRAM];
    uint8_t pt_buf[SDTP_MTU];

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    sdtp_log("hub: %zu configured peer(s), routing by inner destination IP", cfg->peer_count);

    while (!g_hub_should_exit) {
        struct pollfd pfds[2];
        pfds[0].fd = tun_fd;
        pfds[0].events = POLLIN;
        pfds[1].fd = udp_fd;
        pfds[1].events = POLLIN;

        int rc = poll(pfds, 2, 1000);
        if (rc < 0) {
            if (errno == EINTR) continue;
            sdtp_die("poll: %s", strerror(errno));
        }

        time_t now = time(NULL);

        /* Per-peer keepalive + liveness: keep established tunnels warm, and
         * drop a peer that has gone silent so its slot can re-handshake. */
        for (size_t i = 0; i < cfg->peer_count; i++) {
            sdtp_hub_peer *p = &cfg->peers[i];
            if (!p->established) continue;
            if (now - p->last_recv > 3 * SDTP_KEEPALIVE_INTERVAL_S) {
                sdtp_log("hub: peer %zu silent too long, dropping session", i);
                p->established = 0;
                memset(&p->session, 0, sizeof(p->session));
                continue;
            }
            if (p->have_addr && now - p->last_send >= SDTP_KEEPALIVE_INTERVAL_S) {
                size_t len = sdtp_data_encrypt(&p->session, SDTP_MSG_KEEPALIVE, out_buf, NULL, 0);
                if (len > 0) {
                    sendto(udp_fd, out_buf, len, 0, (struct sockaddr *)&p->addr, sizeof(p->addr));
                    p->last_send = now;
                }
            }
        }

        /* TUN -> the client that owns the destination IP. */
        if (pfds[0].revents & POLLIN) {
            ssize_t n = read(tun_fd, buf, SDTP_MTU);
            if (n > 0) {
                uint32_t dst;
                if (sdtp_hub_parse_ipv4_dst(buf, (size_t)n, &dst) == 0) {
                    int idx = sdtp_hub_find_peer_by_ip(cfg->peers, cfg->peer_count, dst);
                    if (idx >= 0) {
                        forward_to_peer(udp_fd, &cfg->peers[idx], buf, (size_t)n, out_buf, now);
                    }
                }
            }
        }

        /* UDP -> handshake (demux by static key) or data (demux by session id).
         * Drain every datagram queued on this wake in one recvmmsg, then handle
         * them one at a time -- one syscall for the batch, not one per packet. */
        if (pfds[1].revents & POLLIN) {
            sdtp_udp_msg batch[SDTP_RECV_BATCH];
            int count = sdtp_udp_recv_batch(udp_fd, batch, SDTP_RECV_BATCH);
            for (int bi = 0; bi < count; bi++) {
                hub_handle_datagram(cfg, tun_fd, udp_fd, batch[bi].buf, (ssize_t)batch[bi].len,
                                    batch[bi].src, batch[bi].src_len, now, out_buf, pt_buf);
            }
        }
    }

    sdtp_log("hub: shutting down");
}
