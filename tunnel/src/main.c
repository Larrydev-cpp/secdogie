#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <poll.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sodium.h>

#include "sdtp.h"
#include "crypto.h"
#include "handshake.h"
#include "data.h"
#include "tun.h"
#include "net.h"
#include "config.h"
#include "util.h"

/* Upper bound on packets drained from one fd per poll() wakeup. Draining a
 * burst in a single wakeup (instead of one packet then re-poll) removes a
 * poll() round-trip from the 2nd..Nth packet's latency; the cap keeps a
 * saturating flood from starving the keepalive / handshake timers below. */
#define SDTP_DRAIN_BURST 64

static volatile sig_atomic_t g_should_exit = 0;
static void on_signal(int sig) {
    (void)sig;
    g_should_exit = 1;
}

static void set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static void print_usage(const char *argv0) {
    fprintf(stderr,
        "usage:\n"
        "  %s genkey [private_key_outfile]\n"
        "  %s server <config_file>\n"
        "  %s client <config_file>\n",
        argv0, argv0, argv0);
}

static int cmd_genkey(int argc, char **argv) {
    sdtp_keypair kp;
    sdtp_keypair_generate(&kp);

    char priv_b64[sodium_base64_ENCODED_LEN(SDTP_KEY_LEN, sodium_base64_VARIANT_ORIGINAL)];
    char pub_b64[sodium_base64_ENCODED_LEN(SDTP_KEY_LEN, sodium_base64_VARIANT_ORIGINAL)];
    sodium_bin2base64(priv_b64, sizeof(priv_b64), kp.sk, SDTP_KEY_LEN, sodium_base64_VARIANT_ORIGINAL);
    sodium_bin2base64(pub_b64, sizeof(pub_b64), kp.pk, SDTP_KEY_LEN, sodium_base64_VARIANT_ORIGINAL);

    if (argc > 0) {
        int fd = open(argv[0], O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (fd < 0) {
            fprintf(stderr, "cannot create '%s': %s\n", argv[0], strerror(errno));
            sodium_memzero(&kp, sizeof(kp));
            return 1;
        }
        FILE *f = fdopen(fd, "w");
        fprintf(f, "private_key = %s\n", priv_b64);
        fclose(f);
        fprintf(stderr, "wrote private key to %s (mode 0600)\n", argv[0]);
    } else {
        printf("private_key = %s\n", priv_b64);
    }
    fprintf(stderr, "public_key  = %s   (share this with your peer)\n", pub_b64);

    sodium_memzero(&kp, sizeof(kp));
    sodium_memzero(priv_b64, sizeof(priv_b64));
    return 0;
}

static ssize_t read_tun_packet(int tun_fd, uint8_t *buf, size_t cap) {
    ssize_t n = read(tun_fd, buf, cap);
    if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
        sdtp_log("tun read error: %s", strerror(errno));
    }
    return n;
}

static void run_loop(int tun_fd, int udp_fd, sdtp_config *cfg, int is_server) {
    /* Non-blocking so we can drain each fd to EAGAIN per wakeup without the
     * final read blocking the loop, and so a momentarily full send buffer
     * drops one datagram instead of stalling every other flow. */
    set_nonblocking(tun_fd);
    set_nonblocking(udp_fd);

    sdtp_session session;
    memset(&session, 0, sizeof(session));
    int established = 0;

    struct sockaddr_in peer_addr;
    memset(&peer_addr, 0, sizeof(peer_addr));
    int have_peer_addr = 0;

    uint64_t last_peer_ts = 0;
    sdtp_handshake_state hs;
    int handshake_pending = 0;
    time_t handshake_sent_at = 0;
    time_t last_recv = time(NULL);
    time_t last_send = 0;

    uint8_t buf[SDTP_MAX_DATAGRAM > SDTP_MTU + 64 ? SDTP_MAX_DATAGRAM : SDTP_MTU + 64];
    uint8_t out_buf[SDTP_MAX_DATAGRAM];
    uint8_t pt_buf[SDTP_MTU];

    if (!is_server) {
        if (sdtp_resolve(cfg->endpoint_host, cfg->endpoint_port, &peer_addr) != 0) {
            sdtp_die("cannot resolve endpoint '%s'", cfg->endpoint_host);
        }
        have_peer_addr = 1;

        uint8_t msg1[SDTP_MSG1_LEN];
        sdtp_handshake_init_create(&hs, msg1, &cfg->my_static, cfg->peer_static_pk);
        sendto(udp_fd, msg1, sizeof(msg1), 0, (struct sockaddr *)&peer_addr, sizeof(peer_addr));
        handshake_pending = 1;
        handshake_sent_at = time(NULL);
        sdtp_log("client: handshake initiated to %s:%u", cfg->endpoint_host, cfg->endpoint_port);
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    while (!g_should_exit) {
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

        if (!is_server && handshake_pending && now - handshake_sent_at >= SDTP_HANDSHAKE_TIMEOUT_S) {
            uint8_t msg1[SDTP_MSG1_LEN];
            sdtp_handshake_init_create(&hs, msg1, &cfg->my_static, cfg->peer_static_pk);
            sendto(udp_fd, msg1, sizeof(msg1), 0, (struct sockaddr *)&peer_addr, sizeof(peer_addr));
            handshake_sent_at = now;
            sdtp_log("client: retrying handshake");
        }

        if (!is_server && established && now - last_recv > 3 * SDTP_KEEPALIVE_INTERVAL_S) {
            sdtp_log("client: peer silent too long, re-handshaking");
            established = 0;
            memset(&session, 0, sizeof(session));
            uint8_t msg1[SDTP_MSG1_LEN];
            sdtp_handshake_init_create(&hs, msg1, &cfg->my_static, cfg->peer_static_pk);
            sendto(udp_fd, msg1, sizeof(msg1), 0, (struct sockaddr *)&peer_addr, sizeof(peer_addr));
            handshake_pending = 1;
            handshake_sent_at = now;
        }

        if (established && have_peer_addr && now - last_send >= SDTP_KEEPALIVE_INTERVAL_S) {
            size_t len = sdtp_data_encrypt(&session, SDTP_MSG_KEEPALIVE, out_buf, NULL, 0);
            if (len > 0) {
                sendto(udp_fd, out_buf, len, 0, (struct sockaddr *)&peer_addr, sizeof(peer_addr));
            }
            last_send = now;
        }

        if (pfds[0].revents & POLLIN) {
            for (int i = 0; i < SDTP_DRAIN_BURST; i++) {
                ssize_t n = read_tun_packet(tun_fd, buf, SDTP_MTU);
                if (n <= 0) break;  /* EAGAIN (drained) or error */
                if (established && have_peer_addr) {
                    size_t len = sdtp_data_encrypt(&session, SDTP_MSG_DATA, out_buf, buf, (size_t)n);
                    if (len > 0) {
                        sendto(udp_fd, out_buf, len, 0, (struct sockaddr *)&peer_addr, sizeof(peer_addr));
                        last_send = now;
                    }
                }
            }
        }

        if (pfds[1].revents & POLLIN) {
          for (int i = 0; i < SDTP_DRAIN_BURST; i++) {
            struct sockaddr_in src_addr;
            socklen_t src_len = sizeof(src_addr);
            ssize_t n = recvfrom(udp_fd, buf, sizeof(buf), 0, (struct sockaddr *)&src_addr, &src_len);
            if (n <= 0) break;  /* EAGAIN (drained) or error */
            {
                uint8_t type = buf[0];
                if (type == SDTP_MSG_HANDSHAKE_INIT && is_server) {
                    uint8_t msg2[SDTP_MSG2_LEN];
                    sdtp_session new_session;
                    size_t rlen = sdtp_handshake_respond(buf, (size_t)n, &cfg->my_static, cfg->peer_static_pk,
                                                          &last_peer_ts, msg2, &new_session);
                    if (rlen > 0) {
                        session = new_session;
                        established = 1;
                        peer_addr = src_addr;
                        have_peer_addr = 1;
                        last_recv = now;
                        sendto(udp_fd, msg2, rlen, 0, (struct sockaddr *)&src_addr, src_len);
                        sdtp_log("server: handshake completed with %s:%u", inet_ntoa(src_addr.sin_addr),
                                 ntohs(src_addr.sin_port));
                    } else {
                        sdtp_log("server: rejected handshake_init from %s:%u", inet_ntoa(src_addr.sin_addr),
                                 ntohs(src_addr.sin_port));
                    }
                } else if (type == SDTP_MSG_HANDSHAKE_RESP && !is_server && handshake_pending) {
                    if (sdtp_handshake_finish(&hs, buf, (size_t)n, &cfg->my_static, cfg->peer_static_pk, &session)) {
                        established = 1;
                        handshake_pending = 0;
                        last_recv = now;
                        sdtp_log("client: handshake completed");
                    } else {
                        sdtp_log("client: handshake_resp failed validation, ignoring");
                    }
                } else if ((type == SDTP_MSG_DATA || type == SDTP_MSG_KEEPALIVE) && established) {
                    size_t pt_len = 0;
                    if (sdtp_data_decrypt(&session, buf, (size_t)n, pt_buf, sizeof(pt_buf), &pt_len) == 0) {
                        last_recv = now;
                        peer_addr = src_addr;
                        have_peer_addr = 1;
                        if (type == SDTP_MSG_DATA && pt_len > 0) {
                            write(tun_fd, pt_buf, pt_len);
                        }
                    }
                }
            }
          }
        }
    }

    sdtp_log("shutting down");
}

static int cmd_server(int argc, char **argv) {
    if (argc < 1) {
        fprintf(stderr, "server requires a config file\n");
        return 1;
    }
    sdtp_config cfg;
    if (sdtp_config_load(argv[0], &cfg) != 0) return 1;
    if (cfg.listen_port == 0) {
        fprintf(stderr, "server config must set listen_port\n");
        return 1;
    }

    char ifname[IFNAMSIZ];
    memset(ifname, 0, sizeof(ifname));
    if (cfg.ifname[0]) strncpy(ifname, cfg.ifname, IFNAMSIZ - 1);

    int tun_fd = sdtp_tun_create(ifname);
    if (tun_fd < 0) sdtp_die("tun create failed: %s (are you root / CAP_NET_ADMIN?)", strerror(errno));
    if (sdtp_tun_configure(ifname, cfg.address, cfg.mtu) < 0) {
        sdtp_die("tun configure failed: %s", strerror(errno));
    }

    int udp_fd = sdtp_udp_bind(cfg.listen_port);
    if (udp_fd < 0) sdtp_die("udp bind failed: %s", strerror(errno));
    sdtp_udp_tune(udp_fd, cfg.dscp, cfg.busy_poll_us);

    sdtp_log("server: tun=%s address=%s udp_port=%u", ifname, cfg.address, cfg.listen_port);
    run_loop(tun_fd, udp_fd, &cfg, 1);

    close(tun_fd);
    close(udp_fd);
    return 0;
}

static int cmd_client(int argc, char **argv) {
    if (argc < 1) {
        fprintf(stderr, "client requires a config file\n");
        return 1;
    }
    sdtp_config cfg;
    if (sdtp_config_load(argv[0], &cfg) != 0) return 1;
    if (!cfg.endpoint_host[0] || cfg.endpoint_port == 0) {
        fprintf(stderr, "client config must set endpoint (host:port)\n");
        return 1;
    }

    char ifname[IFNAMSIZ];
    memset(ifname, 0, sizeof(ifname));
    if (cfg.ifname[0]) strncpy(ifname, cfg.ifname, IFNAMSIZ - 1);

    int tun_fd = sdtp_tun_create(ifname);
    if (tun_fd < 0) sdtp_die("tun create failed: %s (are you root / CAP_NET_ADMIN?)", strerror(errno));
    if (sdtp_tun_configure(ifname, cfg.address, cfg.mtu) < 0) {
        sdtp_die("tun configure failed: %s", strerror(errno));
    }

    int udp_fd = sdtp_udp_bind(cfg.listen_port);
    if (udp_fd < 0) sdtp_die("udp bind failed: %s", strerror(errno));
    sdtp_udp_tune(udp_fd, cfg.dscp, cfg.busy_poll_us);

    sdtp_log("client: tun=%s address=%s -> %s:%u", ifname, cfg.address, cfg.endpoint_host, cfg.endpoint_port);
    run_loop(tun_fd, udp_fd, &cfg, 0);

    close(tun_fd);
    close(udp_fd);
    return 0;
}

int main(int argc, char **argv) {
    if (sdtp_crypto_init() != 0) {
        fprintf(stderr, "libsodium init failed\n");
        return 1;
    }
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    const char *cmd = argv[1];
    if (strcmp(cmd, "genkey") == 0) {
        return cmd_genkey(argc - 2, argv + 2);
    } else if (strcmp(cmd, "server") == 0) {
        return cmd_server(argc - 2, argv + 2);
    } else if (strcmp(cmd, "client") == 0) {
        return cmd_client(argc - 2, argv + 2);
    }

    print_usage(argv[0]);
    return 1;
}
