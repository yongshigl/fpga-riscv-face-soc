#ifndef VISION_YUNET_H
#define VISION_YUNET_H

#include <stdint.h>

/* Faces a single frame may yield after non-maximum suppression. */
#define VISION_YUNET_MAX_FACES 16
/* Candidate table; the threshold keeps this far below the 1,260 grid cells. */
#define VISION_YUNET_MAX_CANDIDATES 96

/* 112x112 alignment target of the MobileFaceNet preprocessing, in Q16.16. */
#define VISION_YUNET_ALIGN_COEFFICIENTS 6

typedef struct {
    int32_t x, y, width, height;              /* Q8 pixels, requested image frame */
    int32_t keypoints[10];                    /* Q8 pixels, x0 y0 ... x4 y4 */
    int32_t score;                            /* Q16 in [0, 65536] */
    int32_t affine[VISION_YUNET_ALIGN_COEFFICIENTS];  /* Q16.16 destination->source */
} vision_yunet_face;

typedef struct {
    uint32_t candidates;   /* cells above the score threshold that the table holds */
    uint32_t dropped;      /* candidates lost because the table was full */
    uint32_t kept;         /* faces that survived suppression */
} vision_yunet_stats;

/* Decodes the YuNet output tensors of a PCNN model image and runs greedy NMS.
 *
 * model_base is the DDR address the model image was uploaded to, and the
 * descriptor block at model_base+64 holds one 64-byte record per output:
 * address, channels, height, width, row_stride, plane_stride, float32 scale
 * bits, zero point, sigmoid flag. Outputs 0..2 are classification, 3..5
 * objectness, 6..8 boxes and 9..11 keypoints at strides 8, 16 and 32.
 *
 * score_threshold and nms_threshold are Q16. Returns the number of faces, or a
 * negative error code (see the enum in vision_yunet.c). */
int vision_yunet_decode(uint32_t model_base, uint32_t image_width, uint32_t image_height,
                        int32_t score_threshold, int32_t nms_threshold,
                        vision_yunet_face *faces, uint32_t capacity,
                        vision_yunet_stats *stats);

/* Least-squares similarity fit from five landmarks (Q8 pixels) to the alignment
 * target, inverted so the result maps 112x112 destination pixels back to the
 * source image the way camera_affine_resampler expects. Returns 1 on success. */
int vision_yunet_align(const int32_t keypoints_q8[10], int32_t coefficients_q16[6]);

#endif
