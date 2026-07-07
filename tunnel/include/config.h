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
    int dscp;                /* DSCP class 0..63 for the outer UDP packets,
                                or -1 (default) = legacy IPTOS_LOWDELAY */
    int busy_poll_us;        /* SO_BUSY_POLL microseconds, 0 = off */
} sdtp_config;

/* Parses a simple `key = value` config file (# comments, blank lines
 * ignored). Returns 0 on success, -1 on error (message printed to stderr). */
int sdtp_config_load(const char *path, sdtp_config *cfg);

#endif /* SDTP_CONFIG_H */
