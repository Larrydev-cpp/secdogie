#ifndef SDTP_CONFIG_H
#define SDTP_CONFIG_H

#include <net/if.h>
#include <stdint.h>

#include "sdtp.h"

typedef struct {
    sdtp_keypair my_static;
    uint8_t peer_static_pk[SDTP_KEY_LEN];
    char address[64];       /* TUN address, e.g. "10.66.0.1/24" */
    uint16_t listen_port;   /* server: UDP port to bind. client: 0 = ephemeral */
    char endpoint_host[256]; /* client only: server hostname/IP */
    uint16_t endpoint_port;  /* client only: server UDP port */
    int mtu;
    char ifname[IFNAMSIZ];
    /* Optional `peer_address = <ipv4>`: the peer's tunnel IP. When set, every
     * decrypted inner packet must carry it as its source (cryptokey routing,
     * as the hub does); when unset the check is off and a warning is printed. */
    uint32_t peer_ip;        /* network byte order */
    int has_peer_ip;
} sdtp_config;

/* Parses a simple `key = value` config file (# comments, blank lines
 * ignored). Returns 0 on success, -1 on error (message printed to stderr). */
int sdtp_config_load(const char *path, sdtp_config *cfg);

#endif /* SDTP_CONFIG_H */
