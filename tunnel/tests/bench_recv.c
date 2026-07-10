/* Micro-benchmark: receiving UDP datagrams one recvfrom-per-packet vs one
 * recvmmsg-per-batch, over loopback (no TUN, no root). Prints the syscall count
 * and wall time for each so you can see where the batched drain (net.c's
 * sdtp_udp_recv_batch, used by both run loops) actually helps: fewer syscalls
 * per packet, which is the per-packet cost that dominates latency under load.
 *
 * Not a hard ctest assertion -- timing is machine-dependent -- it just runs and
 * reports. Usage: bench_recv [rounds]   (default 20000 rounds x SDTP_RECV_BATCH). */
#define _GNU_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#include "net.h"

static uint64_t now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000ULL + (uint64_t)t.tv_nsec / 1000ULL;
}

/* Send `b` small datagrams from tx to raddr. */
static void send_burst(int tx, const struct sockaddr_in *raddr, int b) {
    uint8_t payload[100];
    memset(payload, 0x5a, sizeof(payload));
    for (int i = 0; i < b; i++) {
        payload[0] = (uint8_t)i;
        sendto(tx, payload, sizeof(payload), 0, (const struct sockaddr *)raddr, sizeof(*raddr));
    }
}

int main(int argc, char **argv) {
    int rounds = argc > 1 ? atoi(argv[1]) : 20000;
    if (rounds < 1) rounds = 1;
    const int B = SDTP_RECV_BATCH;

    int rx = sdtp_udp_bind(0);
    if (rx < 0) { perror("bind"); return 1; }
    struct sockaddr_in raddr;
    socklen_t rlen = sizeof(raddr);
    getsockname(rx, (struct sockaddr *)&raddr, &rlen);
    raddr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    int tx = socket(AF_INET, SOCK_DGRAM, 0);
    if (tx < 0) { perror("socket"); return 1; }

    /* --- one recvfrom per datagram --- */
    long single_syscalls = 0;
    uint64_t t0 = now_us();
    for (int r = 0; r < rounds; r++) {
        send_burst(tx, &raddr, B);
        uint8_t buf[SDTP_MAX_DATAGRAM];
        struct sockaddr_in src;
        socklen_t sl = sizeof(src);
        for (int i = 0; i < B; i++) {
            recvfrom(rx, buf, sizeof(buf), 0, (struct sockaddr *)&src, &sl);
            single_syscalls++;
        }
    }
    uint64_t t1 = now_us();

    /* --- one recvmmsg per batch --- */
    long batch_syscalls = 0;
    sdtp_udp_msg batch[SDTP_RECV_BATCH];
    for (int r = 0; r < rounds; r++) {
        send_burst(tx, &raddr, B);
        int got = 0;
        while (got < B) {
            int c = sdtp_udp_recv_batch(rx, batch, B);
            batch_syscalls++;
            if (c <= 0) break;
            got += c;
        }
    }
    uint64_t t2 = now_us();

    long datagrams = (long)rounds * B;
    double single_ms = (t1 - t0) / 1000.0;
    double batch_ms = (t2 - t1) / 1000.0;
    printf("received %ld datagrams (%d per burst, %d rounds)\n", datagrams, B, rounds);
    printf("  one recvfrom/pkt : %8ld recv syscalls, %8.1f ms, %8.0f kpps\n",
           single_syscalls, single_ms, datagrams / single_ms);
    printf("  recvmmsg batches : %8ld recv syscalls, %8.1f ms, %8.0f kpps\n",
           batch_syscalls, batch_ms, datagrams / batch_ms);
    printf("  -> %.1fx fewer receive syscalls, %.2fx wall-time\n",
           (double)single_syscalls / (double)batch_syscalls,
           single_ms / batch_ms);

    close(tx);
    close(rx);
    return 0;
}
