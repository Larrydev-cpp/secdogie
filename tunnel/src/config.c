#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <sodium.h>

#include "config.h"

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

int sdtp_config_load(const char *path, sdtp_config *cfg) {
    memset(cfg, 0, sizeof(*cfg));
    cfg->mtu = SDTP_MTU;

    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "cannot open config '%s': %s\n", path, strerror(errno));
        return -1;
    }

    int have_priv = 0, have_peer_pub = 0, have_address = 0;
    char line[512];
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
        } else if (strcmp(key, "peer_public_key") == 0) {
            if (decode_key(val, cfg->peer_static_pk) != 0) {
                fprintf(stderr, "%s:%d: invalid peer_public_key (expected base64 32 bytes)\n", path, lineno);
                fclose(f);
                return -1;
            }
            have_peer_pub = 1;
        } else if (strcmp(key, "address") == 0) {
            strncpy(cfg->address, val, sizeof(cfg->address) - 1);
            have_address = 1;
        } else if (strcmp(key, "listen_port") == 0) {
            cfg->listen_port = (uint16_t)atoi(val);
        } else if (strcmp(key, "endpoint") == 0) {
            char *colon = strrchr(val, ':');
            if (!colon) {
                fprintf(stderr, "%s:%d: endpoint must be host:port\n", path, lineno);
                fclose(f);
                return -1;
            }
            *colon = '\0';
            strncpy(cfg->endpoint_host, val, sizeof(cfg->endpoint_host) - 1);
            cfg->endpoint_port = (uint16_t)atoi(colon + 1);
        } else if (strcmp(key, "mtu") == 0) {
            cfg->mtu = atoi(val);
        } else if (strcmp(key, "ifname") == 0) {
            strncpy(cfg->ifname, val, sizeof(cfg->ifname) - 1);
        } else {
            fprintf(stderr, "%s:%d: unknown key '%s'\n", path, lineno, key);
            fclose(f);
            return -1;
        }
    }
    fclose(f);

    if (!have_priv || !have_peer_pub || !have_address) {
        fprintf(stderr, "%s: missing required key(s): private_key, peer_public_key, address\n", path);
        return -1;
    }
    return 0;
}
