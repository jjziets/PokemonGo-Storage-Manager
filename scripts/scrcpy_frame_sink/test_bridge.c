/* Offline fixture bridge; never linked into the client. */
/* TRACEWEAVER: file-role=native-stream-test-bridge; verifies=VER-STREAM-ACTIVITY-001; req=REQ-STREAM-001; trace=TRACE-STREAM-001 */
#include "pokemgr_frame_sink.h"
#include <libavutil/frame.h>
#include <stdlib.h>
#include <string.h>

struct fixture {
    struct sc_pk_frame_sink sink;
    AVFrame *frame;
};

void *
pk_test_init_only(const char *path, const char *nonce) {
    struct fixture *f = calloc(1, sizeof(*f));
    if (!f) return NULL;
    if (!sc_pk_frame_sink_init(&f->sink, path, nonce)) {
        sc_pk_frame_sink_destroy(&f->sink);
        free(f);
        return NULL;
    }
    return f;
}

void
pk_test_close(void *handle) {
    struct fixture *f = handle;
    f->sink.frame_sink.ops->close(&f->sink.frame_sink);
}

void *
pk_test_create(const char *path, int width, int height, int rgb) {
    struct fixture *f = calloc(1, sizeof(*f));
    if (!f) return NULL;
    if (!sc_pk_frame_sink_init(&f->sink, path,
                               "0123456789abcdef0123456789abcdef")) {
        free(f);
        return NULL;
    }
    AVCodecContext *ctx = avcodec_alloc_context3(NULL);
    ctx->width = width;
    ctx->height = height;
    ctx->colorspace = AVCOL_SPC_BT709;
    ctx->color_range = AVCOL_RANGE_MPEG;
    struct sc_stream_session session = {.video = {.width = width, .height = height}};
    bool opened = f->sink.frame_sink.ops->open(&f->sink.frame_sink, ctx, &session);
    avcodec_free_context(&ctx);
    if (!opened) {
        sc_pk_frame_sink_destroy(&f->sink);
        free(f);
        return NULL;
    }
    f->frame = av_frame_alloc();
    f->frame->width = width;
    f->frame->height = height;
    f->frame->format = rgb ? AV_PIX_FMT_RGB24 : AV_PIX_FMT_YUV420P;
    if (av_frame_get_buffer(f->frame, 32)) abort();
    return f;
}

int
pk_test_push(void *handle, int64_t pts, int value, int chroma_u, int chroma_v,
             int colorspace, int full_range, int fault) {
    struct fixture *f = handle;
    AVFrame *frame = f->frame;
    frame->pts = pts;
    frame->colorspace = colorspace;
    frame->color_range = full_range ? AVCOL_RANGE_JPEG : AVCOL_RANGE_MPEG;
    frame->color_trc = fault == 2 ? AVCOL_TRC_SMPTE2084 : AVCOL_TRC_BT709;
    if (av_frame_make_writable(frame)) abort();
    for (int y = 0; y < frame->height; ++y) {
        memset(frame->data[0] + y * frame->linesize[0], value,
               frame->width * (frame->format == AV_PIX_FMT_RGB24 ? 3 : 1));
    }
    if (frame->format != AV_PIX_FMT_RGB24) {
        for (int y = 0; y < (frame->height + 1) / 2; ++y) {
            memset(frame->data[1] + y * frame->linesize[1], chroma_u, (frame->width + 1) / 2);
            memset(frame->data[2] + y * frame->linesize[2], chroma_v, (frame->width + 1) / 2);
        }
    }
    if (fault == 1) --frame->width;
    int result = f->sink.frame_sink.ops->push(&f->sink.frame_sink, frame);
    if (fault == 1) ++frame->width;
    return result;
}

void
pk_test_destroy(void *handle) {
    struct fixture *f = handle;
    sc_pk_frame_sink_destroy(&f->sink);
    av_frame_free(&f->frame);
    free(f);
}
