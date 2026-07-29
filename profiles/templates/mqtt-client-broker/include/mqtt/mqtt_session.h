#ifndef MQTT_SESSION_H
#define MQTT_SESSION_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* O-18 frozen resource ABI. conn_id zero is always invalid. */
#define MQTT_MAX_CONNECTIONS 16u
#define MQTT_OUT_BATCH_MAX_ITEMS 16u
#define MQTT_OUT_ITEM_MAX_BYTES 4096u
#define MQTT_OUT_BATCH_MAX_BYTES 65536u
#define MQTT_CONN_ID_INVALID UINT32_C(0)

typedef uint32_t mqtt_conn_id_t;

typedef enum mqtt_session_result {
    MQTT_SESSION_OK = 0,
    MQTT_ERR_INVALID_ARGUMENT = -1,
    MQTT_ERR_UNKNOWN_CONNECTION = -2,
    MQTT_ERR_CAPACITY = -3,
    MQTT_ERR_RESOURCE_LIMIT = -4,
    MQTT_ERR_PROTOCOL = -5
} mqtt_session_result_t;

typedef struct mqtt_out_item {
    mqtt_conn_id_t conn_id;
    size_t len;
    uint8_t bytes[MQTT_OUT_ITEM_MAX_BYTES];
    uint8_t close;
} mqtt_out_item_t;

typedef struct mqtt_out_batch {
    size_t count;
    size_t total_bytes;
    mqtt_out_item_t items[MQTT_OUT_BATCH_MAX_ITEMS];
} mqtt_out_batch_t;

typedef struct mqtt_client_session mqtt_client_session_t;
typedef struct mqtt_broker mqtt_broker_t;

/*
 * Opaque objects use caller-provided storage. Implementations return the exact
 * required size; init performs no allocation and rejects undersized storage.
 */
size_t mqtt_client_session_storage_size(void);
int mqtt_client_session_init(
    void *storage,
    size_t storage_len,
    mqtt_client_session_t **out_session
);
size_t mqtt_broker_storage_size(void);
int mqtt_broker_init(void *storage, size_t storage_len, mqtt_broker_t **out_broker);

/*
 * Client session represents exactly one network connection, so its output has
 * no target conn_id. out_len is set only on success; close is always written.
 */
int mqtt_client_session_on_bytes(
    mqtt_client_session_t *session,
    const uint8_t *input,
    size_t input_len,
    uint8_t *output,
    size_t output_cap,
    size_t *out_len,
    uint8_t *close
);
int mqtt_client_session_on_tick(
    mqtt_client_session_t *session,
    uint64_t now_ms,
    uint8_t *output,
    size_t output_cap,
    size_t *out_len,
    uint8_t *close
);

/*
 * Broker operations require an empty batch on entry. Success produces a complete
 * atomic batch. Any capacity/resource failure returns count=total_bytes=0; no
 * partial fanout may be exposed. Oversized source input returns
 * MQTT_ERR_RESOURCE_LIMIT with one close item targeting that source connection.
 */
int mqtt_broker_on_connect(
    mqtt_broker_t *broker,
    mqtt_conn_id_t conn_id,
    uint64_t now_ms,
    mqtt_out_batch_t *out_batch
);
int mqtt_broker_on_bytes(
    mqtt_broker_t *broker,
    mqtt_conn_id_t conn_id,
    const uint8_t *input,
    size_t input_len,
    uint64_t now_ms,
    mqtt_out_batch_t *out_batch
);
int mqtt_broker_on_disconnect(
    mqtt_broker_t *broker,
    mqtt_conn_id_t conn_id,
    mqtt_out_batch_t *out_batch
);
int mqtt_broker_on_tick(
    mqtt_broker_t *broker,
    uint64_t now_ms,
    mqtt_out_batch_t *out_batch
);

#ifdef __cplusplus
}
#endif

#endif
