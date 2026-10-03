#ifndef TES3X_NET_H
#define TES3X_NET_H

#include "tes3xlog.h"

#define TES3X_NET_PORT 26500u
#define TES3X_NET_CHANNELS 3u

struct tes3x_net_route {
    u32 hop;
    u32 known;
    u8 mac[6];
};

struct tes3x_net_channel {
    u32 port;
    struct tes3x_net_route route;
    void (*receive)(u32 source, u32 port, const u8 *data, u32 length);
    void (*tick)(void);       /* timer DPC, with interrupts masked */
    void (*sample)(void);     /* add interrupt/timer timing to an entropy pool */
    void (*frame)(void);      /* game thread, once per frame */
    int (*command)(const char *text);
    void (*closing)(void);    /* before transmit drains, with interrupts masked */
    void (*stopping)(void);   /* after the timer and receive path stop */
    void (*started)(void);    /* after an interrupt-driven start */
};

struct tes3x_net_info {
    u32 up, ip, mask, irqs, dpcs, rx, rx_errors, rx_nobuf, rx_peak, arp, echo;
    u32 tx, tx_full, tx_errors;
};

extern struct tes3x_net_info tes3x_net;

int tes3x_net_register(struct tes3x_net_channel *channel);
u32 tes3x_net_lock(void);
void tes3x_net_unlock(u32 flags);
u32 tes3x_net_now_us(void);
void tes3x_net_mac(u8 out[6]);
int tes3x_net_start(u32 ip, int irq);
void tes3x_net_stop(void);
void tes3x_net_set_mask(u32 mask);
void tes3x_net_route(struct tes3x_net_channel *channel, u32 target, u32 gateway);
void tes3x_net_arp(u32 target);
void tes3x_net_announce(void);
int tes3x_net_send(struct tes3x_net_channel *channel, u32 destination, u32 port,
                   const u8 *payload, u32 length);
int tes3x_net_send_broadcast(u32 source_port, u32 destination_port,
                             const u8 *payload, u32 length);
void tes3x_net_probe(u32 target);
void tes3x_net_broadcast(u32 count);
void tes3x_net_stat(void);

#endif
