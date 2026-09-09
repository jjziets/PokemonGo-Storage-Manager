/* Read-only wire-v1 copying API. No scrcpy or FFmpeg runtime dependency. */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <sys/mman.h>

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
# error "Pokemon frame buffer wire v1 requires little-endian storage"
#endif

void *
pk_frame_open(int fd, size_t size) {
    if (fd < 0 || size < 128) {
        errno = EINVAL;
        return NULL;
    }
    void *mapping = mmap(NULL, size, PROT_READ, MAP_SHARED, fd, 0);
    return mapping == MAP_FAILED ? NULL : mapping;
}

void
pk_frame_close(void *base, size_t size) {
    if (base && size) {
        munmap(base, size);
    }
}

/* 1 = stable copy, 0 = slot overwritten/in flight, -1 = invalid data/request.
 * The caller supplies an exclusive destination and validates session identity. */
int
pk_frame_copy(void *base, size_t map_size, size_t offset, uint64_t seq,
              uint8_t *destination, size_t payload_size, int64_t *out_pts,
              uint64_t *out_received) {
    if (!base || !destination || !out_pts || !out_received || !seq
            || seq > UINT64_MAX / 2 || offset < 128 || offset % 8
            || map_size < 128 || offset > map_size
            || map_size - offset < 64 || !payload_size
            || payload_size > map_size - offset - 64) {
        return -1;
    }
    const uint8_t *slot = (const uint8_t *) base + offset;
    const _Atomic(uint64_t) *guard = (const void *) slot;
    uint64_t expected = seq * 2;
    if (atomic_load_explicit(guard, memory_order_acquire) != expected) {
        return 0;
    }
    uint64_t actual_seq, payload_bytes, received;
    int64_t pts;
    memcpy(&actual_seq, slot + 8, sizeof(actual_seq));
    memcpy(&pts, slot + 16, sizeof(pts));
    memcpy(&received, slot + 24, sizeof(received));
    memcpy(&payload_bytes, slot + 32, sizeof(payload_bytes));
    memcpy(destination, slot + 64, payload_size);
    /* The payload read must complete before verifying the publication guard.
     * Python's GIL alone is not an interprocess barrier on Apple Silicon. */
    atomic_thread_fence(memory_order_seq_cst);
    if (atomic_load_explicit(guard, memory_order_acquire) != expected) {
        return 0;
    }
    if (actual_seq != seq || payload_bytes != payload_size || pts < 0 || !received) {
        return -1;
    }
    *out_pts = pts;
    *out_received = received;
    return 1;
}
