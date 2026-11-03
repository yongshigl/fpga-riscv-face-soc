#include "vision_yunet.h"

/* YuNet post-processing for the PCNN model image, ported from the host
 * reference (vision/fpga_model.py:decode_yunet) so the RISC-V core can return
 * finished faces instead of raw tensors. Everything is integer fixed point:
 * Q16 for the arithmetic, Q8 for the pixel values handed back. */

#ifdef VISION_YUNET_HOST_TEST
#include <stddef.h>
extern const uint8_t *vision_yunet_heap;   /* host: the model DDR image */
extern uint32_t vision_yunet_heap_base;    /* host: its DDR address */
static uint8_t read8(uint32_t address) { return vision_yunet_heap[address - vision_yunet_heap_base]; }
static uint32_t read32(uint32_t address) {
    const uint8_t *bytes = vision_yunet_heap + (address - vision_yunet_heap_base);
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}
#else
static uint8_t read8(uint32_t address) { return *(volatile uint8_t *)address; }
static uint32_t read32(uint32_t address) { return *(volatile uint32_t *)address; }
#endif

#define Q16_ONE 65536

/* Output record layout written by export_int8.py. */
enum { T_ADDRESS = 0, T_CHANNELS, T_HEIGHT, T_WIDTH, T_ROW, T_PLANE,
       T_SCALE, T_ZERO, T_SIGMOID };

/* Model header words written by export_int8.py. */
enum { H_MAGIC = 0, H_VERSION, H_LAYERS, H_DESCRIPTORS, H_INPUT, H_INPUT_C,
       H_INPUT_H, H_INPUT_W, H_INPUT_ROW, H_INPUT_PLANE, H_OUTPUTS, H_OUTPUT_COUNT };

enum { OUTPUT_CLASS = 0, OUTPUT_OBJECT = 3, OUTPUT_BOX = 6, OUTPUT_KEYPOINTS = 9,
       OUTPUT_COUNT = 12 };

typedef struct {
    uint32_t address;
    int32_t channels, height, width;
    uint32_t row, plane;
    uint32_t scale_bits, zero, sigmoid;
} tensor;

typedef struct {
    int32_t x, y, width, height;   /* Q16, image frame */
    int32_t keypoints[10];         /* Q16, image frame */
    int32_t score;                 /* Q16 */
} candidate;

/* 112x112 MobileFaceNet alignment target, Q16. */
static const int32_t align_target[10] = {
    2509675, 3387969, 4818980, 3375196, 3671668, 4701330,
    2722975, 6053265, 4635355, 6042688,
};

static candidate candidates[VISION_YUNET_MAX_CANDIDATES];

static int64_t fx_mul(int64_t a, int64_t b) { return (a * b) >> 16; }

static int64_t fx_div(int64_t a, int64_t b) { return (a << 16) / b; }

static uint64_t isqrt64(uint64_t value) {
    uint64_t result = 0, bit = (uint64_t)1 << 62;
    if (value == 0) return 0;
    while (bit > value) bit >>= 2;
    while (bit) {
        if (value >= result + bit) {
            value -= result + bit;
            result = (result >> 1) + bit;
        } else {
            result >>= 1;
        }
        bit >>= 2;
    }
    return result;
}

static int64_t fx_sqrt(int64_t value) {
    return (int64_t)isqrt64((uint64_t)value << 16);
}

/* exp(x) in Q16 for x in Q16, clamped to [-20, 20] like the host does. */
static int64_t fx_exp(int64_t x) {
    /* log2(e), ln(2)^k/k! for k = 1..6, all in Q16. */
    const int64_t log2e = 94548;
    const int64_t limit = 20 * Q16_ONE;
    if (x > limit) x = limit;
    if (x < -limit) x = -limit;
    int64_t u = (x * log2e) >> 16;
    int64_t k = u >> 16;
    if (u < 0 && (u & 0xffff)) k -= 1;
    int64_t f = u - (k << 16);
    int64_t p = 10;
    p = fx_mul(p, f) + 87;
    p = fx_mul(p, f) + 630;
    p = fx_mul(p, f) + 3638;
    p = fx_mul(p, f) + 15744;
    p = fx_mul(p, f) + 45426;
    p = fx_mul(p, f) + Q16_ONE;
    if (k > 30) return (int64_t)1 << 40;
    if (k < -30) return 0;
    return k >= 0 ? (p << k) : (p >> (-k));
}

static int64_t fx_sigmoid(int64_t x) {
    int64_t exponential = fx_exp(-x);
    return (int64_t)(((int64_t)Q16_ONE << 16) / (Q16_ONE + exponential));
}

/* value = (byte - zero) * scale, scale held as an exact float exponent. */
static int64_t dequantize(uint8_t value, const tensor *source) {
    int64_t magnitude = (int64_t)(source->scale_bits & 0x7fffffu) | 0x800000;
    int32_t exponent = (int32_t)((source->scale_bits >> 23) & 0xffu) - 127 - 23;
    int64_t product = ((int64_t)value - (int64_t)source->zero) * magnitude;
    int32_t shift = exponent + 16;
    return shift >= 0 ? (product << shift) : (product >> (-shift));
}

static int load_tensor(uint32_t header, uint32_t index, tensor *record) {
    uint32_t base = header + index * 64u;   /* header points at output record 0 */
    record->address = read32(base + T_ADDRESS * 4u);
    record->channels = (int32_t)read32(base + T_CHANNELS * 4u);
    record->height = (int32_t)read32(base + T_HEIGHT * 4u);
    record->width = (int32_t)read32(base + T_WIDTH * 4u);
    record->row = read32(base + T_ROW * 4u);
    record->plane = read32(base + T_PLANE * 4u);
    record->scale_bits = read32(base + T_SCALE * 4u);
    record->zero = read32(base + T_ZERO * 4u);
    record->sigmoid = read32(base + T_SIGMOID * 4u);
    return record->address != 0 && record->channels > 0 && record->height > 0 &&
           record->width > 0 && record->row >= (uint32_t)record->width && record->plane > 0;
}

static uint8_t cell_byte(const tensor *source, int32_t channel, int32_t row, int32_t column) {
    return read8(source->address + (uint32_t)channel * source->plane +
                 (uint32_t)row * source->row + (uint32_t)column);
}

static int32_t to_q8(int64_t value, int64_t scale) {
    return (int32_t)(((value << 16) / scale + 128) >> 8);
}

/* Letterbox scale of the detector input, matching fpga_model.decode_yunet. */
static void letterbox_scale(uint32_t width, uint32_t height, int64_t *scale_x, int64_t *scale_y) {
    uint32_t scaled_width, scaled_height;
    if ((uint64_t)320u * height <= (uint64_t)192u * width) {
        scaled_width = 320u;
        scaled_height = (uint32_t)(((uint64_t)height * 320u + width / 2u) / width);
        if (scaled_height < 1u) scaled_height = 1u;
        if (scaled_height > 192u) scaled_height = 192u;
    } else {
        scaled_height = 192u;
        scaled_width = (uint32_t)(((uint64_t)width * 192u + height / 2u) / height);
        if (scaled_width < 1u) scaled_width = 1u;
        if (scaled_width > 320u) scaled_width = 320u;
    }
    *scale_x = fx_div((int64_t)scaled_width, (int64_t)width);
    *scale_y = fx_div((int64_t)scaled_height, (int64_t)height);
}

static int insert_candidate(const candidate *value, uint32_t *count, uint32_t *dropped) {
    if (*count < VISION_YUNET_MAX_CANDIDATES) {
        candidates[*count] = *value;
        *count += 1;
        return 1;
    }
    /* Keep the strongest candidates when the table fills up. */
    uint32_t weakest = 0;
    for (uint32_t index = 1; index < *count; ++index)
        if (candidates[index].score < candidates[weakest].score) weakest = index;
    if (value->score <= candidates[weakest].score) {
        *dropped += 1;
        return 0;
    }
    candidates[weakest] = *value;
    *dropped += 1;
    return 1;
}

static int64_t intersection_over_union(const candidate *a, const candidate *b) {
    int64_t left = a->x > b->x ? a->x : b->x;
    int64_t top = a->y > b->y ? a->y : b->y;
    int64_t right_a = a->x + a->width, right_b = b->x + b->width;
    int64_t bottom_a = a->y + a->height, bottom_b = b->y + b->height;
    int64_t right = right_a < right_b ? right_a : right_b;
    int64_t bottom = bottom_a < bottom_b ? bottom_a : bottom_b;
    if (right <= left || bottom <= top) return 0;
    int64_t overlap = (right - left) * (bottom - top);
    int64_t union_area = (int64_t)a->width * a->height + (int64_t)b->width * b->height - overlap;
    if (union_area <= 0) return 0;
    return (overlap << 16) / union_area;
}

int vision_yunet_align(const int32_t keypoints_q8[10], int32_t coefficients_q16[6]) {
    int64_t source[10];
    int64_t mean_source_x = 0, mean_source_y = 0, mean_target_x = 0, mean_target_y = 0;
    for (int index = 0; index < 5; ++index) {
        source[index * 2] = (int64_t)keypoints_q8[index * 2] << 8;
        source[index * 2 + 1] = (int64_t)keypoints_q8[index * 2 + 1] << 8;
        mean_source_x += source[index * 2];
        mean_source_y += source[index * 2 + 1];
        mean_target_x += align_target[index * 2];
        mean_target_y += align_target[index * 2 + 1];
    }
    mean_source_x /= 5; mean_source_y /= 5;
    mean_target_x /= 5; mean_target_y /= 5;
    int64_t denominator = 0, dot = 0, cross = 0;
    for (int index = 0; index < 5; ++index) {
        int64_t source_x = source[index * 2] - mean_source_x;
        int64_t source_y = source[index * 2 + 1] - mean_source_y;
        int64_t target_x = align_target[index * 2] - mean_target_x;
        int64_t target_y = align_target[index * 2 + 1] - mean_target_y;
        denominator += fx_mul(source_x, source_x) + fx_mul(source_y, source_y);
        dot += fx_mul(source_x, target_x) + fx_mul(source_y, target_y);
        cross += fx_mul(source_x, target_y) - fx_mul(source_y, target_x);
    }
    if (denominator <= 0) return 0;
    /* Forward similarity q = [[a,-b],[b,a]] p + t, then invert it. */
    int64_t a = fx_div(dot, denominator), b = fx_div(cross, denominator);
    int64_t translate_x = mean_target_x - (fx_mul(a, mean_source_x) - fx_mul(b, mean_source_y));
    int64_t translate_y = mean_target_y - (fx_mul(b, mean_source_x) + fx_mul(a, mean_source_y));
    int64_t determinant = fx_mul(a, a) + fx_mul(b, b);
    if (determinant <= 0) return 0;
    coefficients_q16[0] = (int32_t)fx_div(a, determinant);
    coefficients_q16[1] = (int32_t)fx_div(b, determinant);
    coefficients_q16[2] = (int32_t)fx_div(-(fx_mul(a, translate_x) + fx_mul(b, translate_y)), determinant);
    coefficients_q16[3] = (int32_t)fx_div(-b, determinant);
    coefficients_q16[4] = (int32_t)fx_div(a, determinant);
    coefficients_q16[5] = (int32_t)fx_div(fx_mul(b, translate_x) - fx_mul(a, translate_y), determinant);
    return 1;
}

static int decode_stride(uint32_t header, int32_t stride, int64_t scale_x, int64_t scale_y,
                         int32_t score_threshold, uint32_t *count, uint32_t *dropped) {
    tensor cls, obj, box, keypoints;
    if (!load_tensor(header, (uint32_t)OUTPUT_CLASS + (stride == 16 ? 1u : (stride == 32 ? 2u : 0u)), &cls) ||
        !load_tensor(header, (uint32_t)OUTPUT_OBJECT + (stride == 16 ? 1u : (stride == 32 ? 2u : 0u)), &obj) ||
        !load_tensor(header, (uint32_t)OUTPUT_BOX + (stride == 16 ? 1u : (stride == 32 ? 2u : 0u)), &box) ||
        !load_tensor(header, (uint32_t)OUTPUT_KEYPOINTS + (stride == 16 ? 1u : (stride == 32 ? 2u : 0u)), &keypoints))
        return 0;
    int32_t columns = 320 / stride, rows = 192 / stride;
    if (cls.channels != 1 || obj.channels != 1 || box.channels != 4 || keypoints.channels != 10 ||
        cls.height != rows || cls.width != columns || obj.height != rows || obj.width != columns ||
        box.height != rows || box.width != columns || keypoints.height != rows || keypoints.width != columns)
        return 0;
    for (int32_t row = 0; row < rows; ++row) {
        for (int32_t column = 0; column < columns; ++column) {
            int64_t class_value = dequantize(cell_byte(&cls, 0, row, column), &cls);
            int64_t object_value = dequantize(cell_byte(&obj, 0, row, column), &obj);
            /* Reject cells that cannot reach the threshold before paying for sigmoid. */
            int64_t class_probability = fx_sigmoid(class_value);
            int64_t object_probability = fx_sigmoid(object_value);
            if (class_probability > Q16_ONE) class_probability = Q16_ONE;
            if (object_probability > Q16_ONE) object_probability = Q16_ONE;
            if (class_probability <= 0 || object_probability <= 0) continue;
            int64_t score = fx_sqrt(fx_mul(class_probability, object_probability));
            if (score < score_threshold) continue;
            candidate value;
            value.score = (int32_t)score;
            int64_t center_x = ((int64_t)column << 16) + dequantize(cell_byte(&box, 0, row, column), &box);
            int64_t center_y = ((int64_t)row << 16) + dequantize(cell_byte(&box, 1, row, column), &box);
            int64_t width = fx_exp(dequantize(cell_byte(&box, 2, row, column), &box));
            int64_t height = fx_exp(dequantize(cell_byte(&box, 3, row, column), &box));
            center_x = fx_mul(center_x, stride << 16);
            center_y = fx_mul(center_y, stride << 16);
            width = fx_mul(width, stride << 16);
            height = fx_mul(height, stride << 16);
            value.x = to_q8(center_x - width / 2, scale_x);
            value.y = to_q8(center_y - height / 2, scale_y);
            value.width = to_q8(width, scale_x);
            value.height = to_q8(height, scale_y);
            for (int index = 0; index < 5; ++index) {
                int64_t point_x = ((int64_t)column << 16) + dequantize(cell_byte(&keypoints, index * 2, row, column), &keypoints);
                int64_t point_y = ((int64_t)row << 16) + dequantize(cell_byte(&keypoints, index * 2 + 1, row, column), &keypoints);
                value.keypoints[index * 2] = to_q8(fx_mul(point_x, stride << 16), scale_x);
                value.keypoints[index * 2 + 1] = to_q8(fx_mul(point_y, stride << 16), scale_y);
            }
            insert_candidate(&value, count, dropped);
        }
    }
    return 1;
}

int vision_yunet_decode(uint32_t model_base, uint32_t image_width, uint32_t image_height,
                        int32_t score_threshold, int32_t nms_threshold,
                        vision_yunet_face *faces, uint32_t capacity,
                        vision_yunet_stats *stats) {
    if (model_base & 31u) return -1;
    if (image_width == 0 || image_height == 0) return -2;
    if (faces == 0 || stats == 0 || capacity == 0) return -3;
    if (read32(model_base + H_MAGIC * 4u) != 0x4e4e4350u ||
        read32(model_base + H_VERSION * 4u) != 1u) return -1;
    uint32_t header = read32(model_base + H_OUTPUTS * 4u);
    uint32_t outputs = read32(model_base + H_OUTPUT_COUNT * 4u);
    if (header != model_base + 64u || outputs < OUTPUT_COUNT) return -1;

    int64_t scale_x, scale_y;
    letterbox_scale(image_width, image_height, &scale_x, &scale_y);
    if (scale_x <= 0 || scale_y <= 0) return -2;

    stats->candidates = 0;
    stats->dropped = 0;
    stats->kept = 0;
    const int32_t strides[3] = {8, 16, 32};
    for (int index = 0; index < 3; ++index)
        if (!decode_stride(header, strides[index], scale_x, scale_y, score_threshold,
                           &stats->candidates, &stats->dropped)) return -4;
    /* Greedy NMS with a stable descending sort, the way cv2.dnn.NMSBoxes runs. */
    uint32_t total = stats->candidates;
    uint32_t order[VISION_YUNET_MAX_CANDIDATES];
    for (uint32_t index = 0; index < total; ++index) order[index] = index;
    for (uint32_t index = 1; index < total; ++index) {
        uint32_t key = order[index];
        int32_t score = candidates[key].score;
        uint32_t position = index;
        while (position > 0 && candidates[order[position - 1]].score < score) {
            order[position] = order[position - 1];
            --position;
        }
        order[position] = key;
    }
    uint8_t suppressed[VISION_YUNET_MAX_CANDIDATES];
    for (uint32_t index = 0; index < total; ++index) suppressed[index] = 0;
    uint32_t written = 0;
    for (uint32_t index = 0; index < total && written < capacity; ++index) {
        uint32_t keep = order[index];
        if (suppressed[keep]) continue;
        const candidate *value = &candidates[keep];
        vision_yunet_face *face = &faces[written];
        face->x = value->x; face->y = value->y;
        face->width = value->width; face->height = value->height;
        for (int point = 0; point < 10; ++point) face->keypoints[point] = value->keypoints[point];
        face->score = value->score;
        if (!vision_yunet_align(face->keypoints, face->affine))
            for (int index2 = 0; index2 < 6; ++index2) face->affine[index2] = 0;
        written += 1;
        for (uint32_t other = index + 1; other < total; ++other) {
            uint32_t test = order[other];
            if (suppressed[test]) continue;
            if (intersection_over_union(value, &candidates[test]) > nms_threshold) suppressed[test] = 1;
        }
    }
    stats->kept = written;
    return (int)written;
}
