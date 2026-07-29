#ifndef MQTT_NET_H
#define MQTT_NET_H

#include <stddef.h>
#include <stdint.h>

#include "mqtt/mqtt_session.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum mqtt_net_event_kind {
    MQTT_NET_CONNECTED = 1,
    MQTT_NET_BYTES = 2,
    MQTT_NET_DISCONNECTED = 3,
    MQTT_NET_TICK = 4
} mqtt_net_event_kind_t;

typedef struct mqtt_net_event {
    mqtt_net_event_kind_t kind;
    mqtt_conn_id_t conn_id;
    const uint8_t *bytes;
    size_t len;
    uint64_t now_ms;
} mqtt_net_event_t;

typedef int (*mqtt_net_write_fn)(
    void *context,
    mqtt_conn_id_t conn_id,
    const uint8_t *bytes,
    size_t len,
    uint8_t close
);

/*
 * Apply a complete broker batch in array order. The net layer performs only
 * conn_id lookup, socket writes, and close; it must not inspect MQTT bytes or
 * maintain subscription state.
 */
int mqtt_net_apply_batch(
    const mqtt_out_batch_t *batch,
    mqtt_net_write_fn write_fn,
    void *context
);

#ifdef __cplusplus
}
#endif

#endif
