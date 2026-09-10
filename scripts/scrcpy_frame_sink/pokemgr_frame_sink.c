/* TRACEWEAVER: file-role=native-frame-export; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
#include "pokemgr_frame_sink.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
# include <mach/mach_time.h>
# include <Accelerate/Accelerate.h>
# include "pokemgr_activity.h"
#endif

#include <libavutil/pixdesc.h>
#include <libswscale/swscale.h>

#include "util/log.h"

#define DOWNCAST(SINK) container_of(SINK, struct sc_pk_frame_sink, frame_sink)
#define MAX_PIXELS (UINT64_C(4096) * 4096)

static void
end_activity(struct sc_pk_frame_sink *sink) {
#ifdef __APPLE__
    sc_pk_activity_end(sink->activity);
    sink->activity = NULL;
#else
    (void) sink;
#endif
}

static bool
fail(struct sc_pk_frame_sink *sink, const char *reason) {
    sink->failed = true;
    end_activity(sink);
    if (sink->header) {
        atomic_store_explicit(&sink->header->status, SC_PK_FRAME_ERROR,
                              memory_order_release);
    }
    LOGE("Pokemon frame buffer: %s", reason);
    return false;
}

static bool
sink_open(struct sc_frame_sink *frame_sink, const AVCodecContext *ctx,
          const struct sc_stream_session *session) {
    struct sc_pk_frame_sink *sink = DOWNCAST(frame_sink);
    if (sink->failed) {
        return false;
    }
    if (!session || !session->video.width || !session->video.height
            || ctx->width <= 0 || ctx->height <= 0
            || (uint32_t) ctx->width != session->video.width
            || (uint32_t) ctx->height != session->video.height) {
        return fail(sink, "missing or inconsistent source dimensions");
    }
    uint64_t width = session->video.width;
    uint64_t height = session->video.height;
    if (width > 16384 || height > 16384 || width * height > MAX_PIXELS) {
        return fail(sink, "source dimensions exceed bounded buffer limit");
    }
    /* Each RGB payload is a multiple of eight for this calibrated display.
     * Other dimensions need padding, which wire v1 intentionally does not add. */
    uint64_t payload_size = width * height * 3;
    if (payload_size % _Alignof(struct sc_pk_frame_slot)) {
        return fail(sink, "RGB dimensions do not align wire-v1 slot atomics");
    }
    uint64_t slot_size = SC_PK_FRAME_SLOT_HEADER_SIZE + payload_size;
    uint64_t mapping_size = SC_PK_FRAME_HEADER_SIZE + SC_PK_FRAME_CAPACITY * slot_size;
    if (mapping_size > SIZE_MAX) {
        return fail(sink, "buffer allocation size overflow");
    }
    sink->fd = open(sink->path, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (sink->fd < 0) {
        LOGE("Pokemon frame buffer: exclusive create failed: %s", strerror(errno));
        return fail(sink, "could not exclusively create buffer file");
    }
    if (ftruncate(sink->fd, (off_t) mapping_size)) {
        return fail(sink, "could not size buffer file");
    }
    sink->mapping = mmap(NULL, (size_t) mapping_size, PROT_READ | PROT_WRITE,
                         MAP_SHARED, sink->fd, 0);
    if (sink->mapping == MAP_FAILED) {
        sink->mapping = NULL;
        return fail(sink, "could not map buffer file");
    }
    sink->mapping_size = (size_t) mapping_size;
    sink->header = sink->mapping;
    struct sc_pk_frame_header *header = sink->header;
    header->version = 1;
    header->header_size = SC_PK_FRAME_HEADER_SIZE;
    header->capacity = SC_PK_FRAME_CAPACITY;
    header->width = (uint32_t) width;
    header->height = (uint32_t) height;
    header->channels = 3;
    header->slot_size = slot_size;
    header->writer_pid = (uint64_t) getpid();
    memcpy(header->nonce, sink->nonce, 32);
    atomic_init(&header->latest_seq, 0);
    atomic_init(&header->status, SC_PK_FRAME_INITIAL);
    if (!atomic_is_lock_free(&header->latest_seq)
            || !atomic_is_lock_free(&header->status)) {
        return fail(sink, "shared atomics are not lock-free on this platform");
    }
    for (uint32_t i = 0; i < SC_PK_FRAME_CAPACITY; ++i) {
        struct sc_pk_frame_slot *slot = (void *) ((uint8_t *) sink->mapping
            + SC_PK_FRAME_HEADER_SIZE + i * slot_size);
        atomic_init(&slot->guard, 0);
    }
    sink->source_colorspace = ctx->colorspace;
    sink->source_range = ctx->color_range;
    sink->source_transfer = ctx->color_trc;
    memcpy(header->magic, "PKFRM001", 8);
    LOGI("Pokemon frame buffer: %" PRIu64 "x%" PRIu64 ", %d RGB frames",
         width, height, SC_PK_FRAME_CAPACITY);
    return true;
}

static int
sws_colorspace(int colorspace) {
    switch (colorspace) {
        case AVCOL_SPC_BT709:
            return SWS_CS_ITU709;
        case AVCOL_SPC_FCC:
            return SWS_CS_FCC;
        case AVCOL_SPC_BT470BG:
        case AVCOL_SPC_SMPTE170M:
            return SWS_CS_ITU601;
        case AVCOL_SPC_SMPTE240M:
            return SWS_CS_SMPTE240M;
        case AVCOL_SPC_BT2020_NCL:
        case AVCOL_SPC_BT2020_CL:
            return SWS_CS_BT2020;
        default:
            return SWS_CS_DEFAULT;
    }
}

static bool
can_accelerate(const AVFrame *frame, int colorspace) {
#ifdef __APPLE__
    return frame->format == AV_PIX_FMT_YUV420P
        && !(frame->width % 2) && !(frame->height % 2)
        && frame->linesize[0] >= frame->width
        && frame->linesize[1] >= frame->width / 2
        && frame->linesize[2] >= frame->width / 2
        && (colorspace == AVCOL_SPC_BT709 || colorspace == AVCOL_SPC_BT470BG
            || colorspace == AVCOL_SPC_SMPTE170M || colorspace == AVCOL_SPC_UNSPECIFIED);
#else
    (void) frame;
    (void) colorspace;
    return false;
#endif
}

static bool
accelerate_rgb(struct sc_pk_frame_sink *sink, const AVFrame *frame,
                int colorspace, int full_range, uint8_t *output) {
#ifdef __APPLE__
    if (!sink->argb_buffer) {
        sink->argb_buffer = malloc((size_t) frame->width * frame->height * 4);
        if (!sink->argb_buffer) {
            return fail(sink, "could not allocate bounded ARGB conversion buffer");
        }
    }
    const vImage_YpCbCrToARGBMatrix *matrix = colorspace == AVCOL_SPC_BT709
        ? kvImage_YpCbCrToARGBMatrix_ITU_R_709_2
        : kvImage_YpCbCrToARGBMatrix_ITU_R_601_4;
    vImage_YpCbCrPixelRange range = {
        .Yp_bias = full_range ? 0 : 16,
        .CbCr_bias = 128,
        .YpRangeMax = full_range ? 255 : 235,
        .CbCrRangeMax = full_range ? 255 : 240,
        .YpMax = 255, .YpMin = 0, .CbCrMax = 255, .CbCrMin = 0,
    };
    vImage_YpCbCrToARGB conversion;
    if (vImageConvert_YpCbCrToARGB_GenerateConversion(matrix, &range, &conversion,
            kvImage420Yp8_Cb8_Cr8, kvImageARGB8888, kvImageNoFlags)) {
        return fail(sink, "could not configure accelerated YUV conversion");
    }
    vImage_Buffer y = {frame->data[0], frame->height, frame->width, frame->linesize[0]};
    vImage_Buffer cb = {frame->data[1], frame->height / 2, frame->width / 2, frame->linesize[1]};
    vImage_Buffer cr = {frame->data[2], frame->height / 2, frame->width / 2, frame->linesize[2]};
    vImage_Buffer argb = {sink->argb_buffer, frame->height, frame->width, frame->width * 4};
    vImage_Buffer rgb = {output, frame->height, frame->width, frame->width * 3};
    if (vImageConvert_420Yp8_Cb8_Cr8ToARGB8888(&y, &cb, &cr, &argb, &conversion,
            NULL, 255, kvImageDoNotTile)
            || vImageConvert_ARGB8888toRGB888(&argb, &rgb, kvImageDoNotTile)) {
        return fail(sink, "accelerated RGB conversion failed");
    }
    return true;
#else
    (void) frame;
    (void) colorspace;
    (void) full_range;
    (void) output;
    return fail(sink, "accelerated RGB conversion requires macOS");
#endif
}

static bool
sink_push(struct sc_frame_sink *frame_sink, const AVFrame *frame) {
#ifdef __APPLE__
    /* CPython's time.monotonic_ns() uses mach_absolute_time on Darwin.
     * CLOCK_MONOTONIC has different suspend semantics on this platform. */
    uint64_t ticks = mach_absolute_time();
    mach_timebase_info_data_t timebase;
    if (mach_timebase_info(&timebase) != KERN_SUCCESS || !timebase.denom) {
        return fail(DOWNCAST(frame_sink), "could not read monotonic timebase");
    }
    uint64_t entered_ns = (uint64_t) (((__uint128_t) ticks * timebase.numer)
                                    / timebase.denom);
#else
    struct timespec entered;
    if (clock_gettime(CLOCK_MONOTONIC, &entered)) {
        return fail(DOWNCAST(frame_sink), "could not read monotonic clock");
    }
    uint64_t entered_ns = (uint64_t) entered.tv_sec * UINT64_C(1000000000)
                       + (uint64_t) entered.tv_nsec;
#endif
    struct sc_pk_frame_sink *sink = DOWNCAST(frame_sink);
    if (sink->failed) {
        return false;
    }
    struct sc_pk_frame_header *header = sink->header;
    if (!header || frame->width != (int) header->width
            || frame->height != (int) header->height) {
        return fail(sink, "source shape changed; new stream session required");
    }
    if (frame->pts == AV_NOPTS_VALUE || frame->pts < 0
            || sink->sequence >= UINT64_MAX / 2 - 1) {
        return fail(sink, "source frame has no valid PTS or sequence overflowed");
    }
    int colorspace = frame->colorspace != AVCOL_SPC_UNSPECIFIED
                   ? frame->colorspace : sink->source_colorspace;
    int range = frame->color_range != AVCOL_RANGE_UNSPECIFIED
              ? frame->color_range : sink->source_range;
    int transfer = frame->color_trc != AVCOL_TRC_UNSPECIFIED
                 ? frame->color_trc : sink->source_transfer;
    if (transfer == AVCOL_TRC_SMPTE2084 || transfer == AVCOL_TRC_ARIB_STD_B67) {
        return fail(sink, "HDR transfer requires explicit tone mapping");
    }
    const AVPixFmtDescriptor *pixel = av_pix_fmt_desc_get(frame->format);
    if (!pixel || (pixel->flags & AV_PIX_FMT_FLAG_HWACCEL)) {
        return fail(sink, "unsupported source pixel storage");
    }
    bool accelerated = can_accelerate(frame, colorspace);
    int full_range = range == AVCOL_RANGE_JPEG || (pixel->flags & AV_PIX_FMT_FLAG_RGB);
    if (!sink->sequence) {
        LOGI("Pokemon RGB conversion: %s, source=%s matrix=%d range=%s",
             accelerated ? "Apple vImage" : "libswscale", pixel->name,
             colorspace, full_range ? "full" : "limited");
    }
    if (!accelerated) {
        sink->converter = sws_getCachedContext(sink->converter,
            frame->width, frame->height, frame->format,
            frame->width, frame->height, AV_PIX_FMT_RGB24,
            SWS_BILINEAR, NULL, NULL, NULL);
        if (!sink->converter) {
            return fail(sink, "could not create RGB converter");
        }
        const int *coefficients = sws_getCoefficients(sws_colorspace(colorspace));
        if (sws_setColorspaceDetails(sink->converter, coefficients, full_range,
                                    coefficients, 1, 0, 1 << 16, 1 << 16) < 0) {
            return fail(sink, "could not configure source color conversion");
        }
    }

    uint64_t seq = sink->sequence + 1;
    uint64_t index = (seq - 1) % header->capacity;
    struct sc_pk_frame_slot *slot = (void *) ((uint8_t *) sink->mapping
        + SC_PK_FRAME_HEADER_SIZE + index * header->slot_size);
    /* Invalidate the old slot before overwriting any metadata or RGB bytes. */
    atomic_store_explicit(&slot->guard, seq * 2 + 1, memory_order_seq_cst);
    atomic_thread_fence(memory_order_seq_cst);
    slot->seq = seq;
    slot->pts_us = frame->pts; /* Exact encoder PTS; no host-clock rebasing. */
    slot->sink_entry_ns = entered_ns;
    slot->payload_bytes = (uint64_t) frame->width * frame->height * 3;
    uint8_t *output[4] = {(uint8_t *) slot + SC_PK_FRAME_SLOT_HEADER_SIZE, NULL, NULL, NULL};
    int strides[4] = {frame->width * 3, 0, 0, 0};
    if (accelerated) {
        if (!accelerate_rgb(sink, frame, colorspace, full_range, output[0])) {
            return false;
        }
    } else if (sws_scale(sink->converter, (const uint8_t *const *) frame->data,
                  frame->linesize, 0, frame->height, output, strides) != frame->height) {
        return fail(sink, "incomplete RGB conversion");
    }
    atomic_store_explicit(&slot->guard, seq * 2, memory_order_release);
    atomic_store_explicit(&header->latest_seq, seq, memory_order_release);
    atomic_store_explicit(&header->status, SC_PK_FRAME_READY, memory_order_release);
    sink->sequence = seq;
    return true;
}

static bool
sink_push_session(struct sc_frame_sink *frame_sink,
                  const struct sc_stream_session *session) {
    (void) session;
    return fail(DOWNCAST(frame_sink), "video session changed; new buffer required");
}

static void
sink_close(struct sc_frame_sink *frame_sink) {
    struct sc_pk_frame_sink *sink = DOWNCAST(frame_sink);
    end_activity(sink);
    if (sink->header && atomic_load_explicit(&sink->header->status,
                                            memory_order_acquire) != SC_PK_FRAME_ERROR) {
        atomic_store_explicit(&sink->header->status, SC_PK_FRAME_CLOSED,
                              memory_order_release);
    }
}

/* TRACEWEAVER: entrypoint=sc_pk_frame_sink_init; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
bool
sc_pk_frame_sink_init(struct sc_pk_frame_sink *sink, const char *path,
                      const char *nonce) {
    memset(sink, 0, sizeof(*sink));
    sink->fd = -1;
    if (!path || path[0] != '/' || !nonce || strlen(nonce) != 32) {
        return fail(sink, "absolute path and 32 hexadecimal session characters required");
    }
    for (unsigned i = 0; i < 32; ++i) {
        if (!isxdigit((unsigned char) nonce[i])) {
            return fail(sink, "session nonce must contain only hexadecimal characters");
        }
    }
    sink->path = strdup(path);
    if (!sink->path) {
        return fail(sink, "could not allocate buffer path");
    }
    memcpy(sink->nonce, nonce, 33);
    static const struct sc_frame_sink_ops ops = {
        .open = sink_open,
        .push = sink_push,
        .push_session = sink_push_session,
        .close = sink_close,
    };
    sink->frame_sink.ops = &ops;
#ifdef __APPLE__
    /* TRACEWEAVER: req=REQ-STREAM-001; trace=TRACE-STREAM-001 */
    sink->activity = sc_pk_activity_begin();
    if (!sink->activity) {
        free(sink->path);
        sink->path = NULL;
        return fail(sink, "could not begin frame export activity");
    }
#endif
    return true;
}

/* TRACEWEAVER: entrypoint=sc_pk_frame_sink_destroy; req=REQ-STREAM-001; trace=TRACE-STREAM-001; ver=VER-STREAM-ACTIVITY-001 */
void
sc_pk_frame_sink_destroy(struct sc_pk_frame_sink *sink) {
    sink_close(&sink->frame_sink);
    sws_freeContext(sink->converter);
    free(sink->argb_buffer);
    if (sink->mapping) {
        munmap(sink->mapping, sink->mapping_size);
    }
    if (sink->fd >= 0) {
        close(sink->fd);
    }
    free(sink->path);
    memset(sink, 0, sizeof(*sink));
    sink->fd = -1;
}
