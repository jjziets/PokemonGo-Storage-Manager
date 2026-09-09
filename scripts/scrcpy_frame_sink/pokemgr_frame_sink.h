#ifndef SC_POKEMGR_FRAME_SINK_H
#define SC_POKEMGR_FRAME_SINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>

#include "trait/frame_sink.h"

/* Wire v1 is explicitly little-endian and uses naturally aligned atomics. */
#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
# error "Pokemon frame buffer wire v1 requires a little-endian host"
#endif

#define SC_PK_FRAME_CAPACITY 30
#define SC_PK_FRAME_HEADER_SIZE 128
#define SC_PK_FRAME_SLOT_HEADER_SIZE 64

enum sc_pk_frame_status {
    SC_PK_FRAME_INITIAL = 0,
    SC_PK_FRAME_READY = 1,
    SC_PK_FRAME_CLOSED = 2,
    SC_PK_FRAME_ERROR = 3,
};

struct sc_pk_frame_header {
    char magic[8];
    uint32_t version;
    uint32_t header_size;
    uint32_t capacity;
    uint32_t width;
    uint32_t height;
    uint32_t channels;
    uint64_t slot_size;
    _Atomic(uint64_t) latest_seq;
    uint64_t writer_pid;
    _Atomic(uint32_t) status;
    uint32_t reserved;
    char nonce[32];
    uint8_t padding[32];
};

struct sc_pk_frame_slot {
    _Atomic(uint64_t) guard;
    uint64_t seq;
    int64_t pts_us;
    uint64_t sink_entry_ns;
    uint64_t payload_bytes;
    uint8_t padding[24];
};

_Static_assert(sizeof(struct sc_pk_frame_header) == SC_PK_FRAME_HEADER_SIZE, "frame header layout");
_Static_assert(offsetof(struct sc_pk_frame_header, latest_seq) == 40, "latest sequence offset");
_Static_assert(offsetof(struct sc_pk_frame_header, status) == 56, "status offset");
_Static_assert(offsetof(struct sc_pk_frame_header, nonce) == 64, "nonce offset");
_Static_assert(sizeof(struct sc_pk_frame_slot) == SC_PK_FRAME_SLOT_HEADER_SIZE, "slot header layout");
_Static_assert(offsetof(struct sc_pk_frame_slot, pts_us) == 16, "source PTS offset");

struct SwsContext;

struct sc_pk_frame_sink {
    struct sc_frame_sink frame_sink;
    char *path;
    char nonce[33];
    int fd;
    void *mapping;
    size_t mapping_size;
    struct sc_pk_frame_header *header;
    struct SwsContext *converter;
    void *argb_buffer;
    uint64_t sequence;
    int source_colorspace;
    int source_range;
    int source_transfer;
    bool failed;
};

bool
sc_pk_frame_sink_init(struct sc_pk_frame_sink *sink, const char *path,
                      const char *nonce);

void
sc_pk_frame_sink_destroy(struct sc_pk_frame_sink *sink);

#endif
