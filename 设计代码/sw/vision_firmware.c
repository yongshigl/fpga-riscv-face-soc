#include <stdint.h>
#include "miniz_tinfl.h"
#include "vision_yunet.h"

#ifndef VISION_BENCHMARK
#define VISION_BENCHMARK 0
#endif
#if VISION_BENCHMARK
#include "vision_bench.h"
#endif

#ifndef VISION_FAST_CRC32
#define VISION_FAST_CRC32 0
#endif
#if VISION_FAST_CRC32
#include "vision_crc32.h"
#endif

#ifndef VISION_RECIPROCAL_NORMALIZATION
#define VISION_RECIPROCAL_NORMALIZATION 0
#endif
#if VISION_RECIPROCAL_NORMALIZATION
#include "vision_normalize.h"
#endif

#ifndef CPU_CLOCK_HZ
#define CPU_CLOCK_HZ 81000000u
#endif
#ifndef AI_CLOCK_HZ
#define AI_CLOCK_HZ 54000000u
#endif
#ifndef CAMERA_ENABLE
#define CAMERA_ENABLE 0
#endif
#ifndef CAMERA_TENSOR
#define CAMERA_TENSOR 0
#endif
#ifndef DELTA_WRITE_ENABLE
#define DELTA_WRITE_ENABLE 0
#endif
#ifndef VISION_FACE_DECODE
#define VISION_FACE_DECODE (CAMERA_TENSOR ? 1 : 0)
#endif
#if VISION_BENCHMARK && VISION_FACE_DECODE
#define FIRMWARE_VERSION 0x26102A01u
#elif VISION_BENCHMARK
#define FIRMWARE_VERSION 0x26102A02u
#elif VISION_FACE_DECODE
#define FIRMWARE_VERSION 0x26102901u
#elif VISION_FAST_CRC32 && VISION_RECIPROCAL_NORMALIZATION
#define FIRMWARE_VERSION 0x26102503u
#elif VISION_FAST_CRC32
#define FIRMWARE_VERSION 0x26102504u
#elif VISION_RECIPROCAL_NORMALIZATION
#define FIRMWARE_VERSION 0x26102502u
#elif CAMERA_TENSOR
#define FIRMWARE_VERSION 0x26102401u
#elif DELTA_WRITE_ENABLE
#define FIRMWARE_VERSION 0x26101401u
#else
#define FIRMWARE_VERSION 0x26101301u
#endif

#define DEVICE ((volatile uint32_t *)0x20000000u)
#define ENGINE ((volatile uint32_t *)0x21000000u)
#define CAMERA ((volatile uint32_t *)0x21008000u)
#define REQUEST_MAGIC 0x31524750u
#define RESPONSE_MAGIC 0x31534750u
#define PAYLOAD_MAX 2048u
#define OUTPUT_MAX 4096u
#define GALLERY_SIZE 16u
#define DIMENSION 128u

enum { PING = 1, WRITE = 2, READ = 3, RUN = 4, CONTROL = 5,
       ENROLL = 6, MATCH = 7, CLEAR_GALLERY = 8, COUNTERS = 9, THRESHOLD = 10,
       CAMERA_PREVIEW = 11, COMPRESSED_WRITE = 12, CAPABILITIES = 13,
       PACKED_READ = 14, DELTA_COMPRESSED_WRITE = 15, CAMERA_DETECT = 16,
       CAMERA_TENSOR_STATUS = 17, CAMERA_ALIGN = 18, CAMERA_CAPTURE = 19,
       CAMERA_FRAME_READ = 20, CAMERA_FACES = 21, BENCHMARK = 22 };
enum { OK = 0, BAD_CRC = 1, BAD_COMMAND = 2, BAD_RANGE = 3,
       BAD_MODEL = 4, ENGINE_ERROR = 5, JOB_TIMEOUT = 6,
       BAD_FEATURE = 7, CPU_TRAP = 8, BAD_COMPRESSION = 9 };

static uint8_t input[PAYLOAD_MAX] __attribute__((aligned(4)));
static uint8_t output[OUTPUT_MAX] __attribute__((aligned(4)));
static int16_t gallery[GALLERY_SIZE][DIMENSION];
static int16_t last_embedding[DIMENSION];
static uint32_t gallery_valid, embedding_valid;
static int32_t match_threshold = 18022;
static uint32_t request_op, request_sequence, output_bytes;
static uint32_t previous_crc, previous_sequence, previous_op, previous_valid;
#if CAMERA_ENABLE
static uint32_t preview_after_response;
#if VISION_BENCHMARK
/* CoreMark prints plain text, so it runs after the framed reply has been sent. */
static uint32_t benchmark_iterations;
#endif
#endif

static uint32_t cycle_low(void) {
    uint32_t value;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(value));
    return value;
}

static uint64_t cycle_count(void) {
    uint32_t first, low, second;
    do {
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(first));
        low = cycle_low();
        __asm__ volatile ("csrr %0, mcycleh" : "=r"(second));
    } while (first != second);
    return ((uint64_t)first << 32) | low;
}

static uint32_t crc_byte(uint32_t crc, uint8_t value) {
#if VISION_FAST_CRC32
    return vision_crc32_byte(crc, value);
#else
    crc ^= value;
    for (unsigned bit = 0; bit < 8; ++bit)
        crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    return crc;
#endif
}

static void put_byte(uint8_t value) {
    while (!(DEVICE[2] & 1u)) { }
    DEVICE[1] = value;
}

static int get_byte(uint8_t *value, int timed) {
    uint32_t start = cycle_low();
    while (!(DEVICE[2] & 2u))
        if (timed && (uint32_t)(cycle_low() - start) > CPU_CLOCK_HZ)
            return 0;
    *value = (uint8_t)DEVICE[3];
    return 1;
}

static uint32_t little32(const uint8_t *bytes) {
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
           (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

static void append32(uint32_t value) {
    for (unsigned byte = 0; byte < 4; ++byte)
        output[output_bytes++] = (uint8_t)(value >> (byte * 8));
}

static void append64(uint64_t value) {
    append32((uint32_t)value);
    append32((uint32_t)(value >> 32));
}

static void result_status(uint32_t status) {
    output_bytes = 0;
    append32(status);
}

#if CAMERA_ENABLE
static void pause_preview(void) {
    CAMERA[0] = 0;
    while (CAMERA[1] & 1u) { }
}
#endif

#if CAMERA_TENSOR
static uint32_t tensor_sequence(void) {
    uint32_t value = CAMERA[16];
    for (unsigned attempt = 0; attempt < 8u; ++attempt) {
        uint32_t next = CAMERA[16];
        if (next == value) return value;
        value = next;
    }
    return value;
}
#endif

static void respond(void) {
#if CAMERA_ENABLE
    pause_preview();
#endif
    uint32_t header[4] = { RESPONSE_MAGIC, request_op, request_sequence, output_bytes };
    uint32_t crc = 0xffffffffu;
    for (unsigned word = 0; word < 4; ++word)
        for (unsigned byte = 0; byte < 4; ++byte) {
            uint8_t value = (uint8_t)(header[word] >> (byte * 8));
            crc = crc_byte(crc, value);
            put_byte(value);
        }
    for (uint32_t byte = 0; byte < output_bytes; ++byte) {
        crc = crc_byte(crc, output[byte]);
        put_byte(output[byte]);
    }
    crc = ~crc;
    for (unsigned byte = 0; byte < 4; ++byte)
        put_byte((uint8_t)(crc >> (byte * 8)));
#if CAMERA_ENABLE
    if (preview_after_response) {
        while (!(DEVICE[2] & 1u)) { }
        CAMERA[0] = 1;
    }
#endif
}

static int ddr_range(uint32_t address, uint32_t bytes) {
    return address >= 0x80000000u && bytes != 0 &&
           bytes <= 0x40000000u && address < 0xc0000000u &&
           bytes <= 0xc0000000u - address;
}

static void invalidate_data(void);

/* Return logical NCHW bytes without transmitting the DMA row/plane padding. */
static void packed_read(uint32_t bytes) {
    if (bytes != 32u) { result_status(BAD_RANGE); return; }
    uint32_t address = little32(input), width = little32(input + 4);
    uint32_t height = little32(input + 8), channels = little32(input + 12);
    uint32_t row_stride = little32(input + 16), plane_stride = little32(input + 20);
    uint32_t offset = little32(input + 24), length = little32(input + 28);
    if (!width || !height || !channels || width > row_stride ||
        !length || length > OUTPUT_MAX - 4u) {
        result_status(BAD_RANGE); return;
    }
    uint64_t plane_bytes = (uint64_t)height * row_stride;
    uint64_t logical_plane = (uint64_t)height * width;
    /* Bound products before multiplying by channels to prevent uint64 overflow. */
    if (plane_bytes > plane_stride || logical_plane > 0xffffffffu ||
        channels > 0xffffffffu / (uint32_t)logical_plane) {
        result_status(BAD_RANGE); return;
    }
    uint32_t logical_bytes = (uint32_t)logical_plane * channels;
    uint64_t physical_bytes = (uint64_t)(channels - 1u) * plane_stride +
                              (uint64_t)(height - 1u) * row_stride + width;
    if (offset >= logical_bytes || length > logical_bytes - offset ||
        physical_bytes > 0xffffffffu || !ddr_range(address, (uint32_t)physical_bytes)) {
        result_status(BAD_RANGE); return;
    }
    invalidate_data();
    while (length) {
        uint32_t channel = offset / (uint32_t)logical_plane;
        uint32_t within = offset % (uint32_t)logical_plane;
        uint32_t row = within / width, column = within % width;
        uint32_t count = width - column;
        if (count > length) count = length;
        volatile uint8_t *source = (volatile uint8_t *)(address + channel * plane_stride +
                                                       row * row_stride + column);
        for (uint32_t byte = 0; byte < count; ++byte) output[output_bytes++] = source[byte];
        offset += count;
        length -= count;
    }
}

static void write_ddr(uint32_t address, const uint8_t *source, uint32_t bytes) {
    uint32_t offset = 0;
    while ((address & 3u) && offset < bytes) *(volatile uint8_t *)address++ = source[offset++];
    while (offset + 4u <= bytes) {
        *(volatile uint32_t *)address = little32(source + offset); address += 4u; offset += 4u;
    }
    while (offset < bytes) *(volatile uint8_t *)address++ = source[offset++];
}

static void compressed_write(uint32_t bytes, uint32_t decode_delta) {
    uint32_t address = bytes > 8u ? little32(input) : 0;
    uint32_t length = bytes > 8u ? little32(input + 4) : 0;
    if (length > OUTPUT_MAX || !ddr_range(address, length)) {
        result_status(BAD_RANGE); return;
    }
    tinfl_decompressor decoder;
    tinfl_init(&decoder);
    size_t input_bytes = bytes - 8u, decoded_bytes = length;
    tinfl_status status = tinfl_decompress(&decoder, input + 8, &input_bytes,
        output, output, &decoded_bytes,
        TINFL_FLAG_PARSE_ZLIB_HEADER | TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
    // Reject malformed streams before any shared-memory side effect.
    if (status != TINFL_STATUS_DONE || input_bytes != bytes - 8u || decoded_bytes != length) {
        result_status(BAD_COMPRESSION);
        append32((uint32_t)status); append32(input_bytes); append32(decoded_bytes);
        return;
    }
#if DELTA_WRITE_ENABLE
    if (decode_delta) {
        uint32_t previous = 0;
        for (uint32_t offset = 0; offset < length; ++offset) {
            previous = (previous + output[offset]) & 255u;
            output[offset] = (uint8_t)previous;
        }
    }
#else
    (void)decode_delta;
#endif
    write_ddr(address, output, length);
    result_status(OK);
    append32(length);
}

static void invalidate_data(void) {
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    DEVICE[12] = 1;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
}

static uint64_t integer_sqrt(uint64_t value) {
    uint64_t root = 0, bit = (uint64_t)1 << 62;
    while (bit > value) bit >>= 2;
    while (bit) {
        if (value >= root + bit) {
            value -= root + bit;
            root = (root >> 1) + bit;
        } else root >>= 1;
        bit >>= 2;
    }
    return root;
}

static int model_header(uint32_t base, volatile uint32_t **header) {
    if ((base & 31u) || !ddr_range(base, 0x400000u)) return 0;
    volatile uint32_t *value = (volatile uint32_t *)base;
    if (value[0] != 0x4e4e4350u || value[1] != 1u ||
        value[2] == 0 || value[2] > 128u || (value[3] & 31u) ||
        value[3] < base || value[3] - base > 0x400000u - value[2] * 96u ||
        value[11] == 0 || value[11] > 16u || value[10] != base + 64u)
        return 0;
    *header = value;
    return 1;
}

/* Runs one accelerator layer; returns 0 on timeout, 1 with the final status. */
static int run_layer(volatile uint32_t *header, uint32_t layer, uint32_t timeout, uint32_t *status) {
    volatile uint32_t *descriptor = (volatile uint32_t *)(header[3] + layer * 96u);
    ENGINE[0] = 2;
    for (unsigned reg = 1; reg <= 17; ++reg) ENGINE[reg] = descriptor[reg - 1];
    ENGINE[0] = 1;
    uint32_t begin = cycle_low();
    do {
        *status = ENGINE[0];
        if ((uint32_t)(cycle_low() - begin) > timeout) return 0;
    } while (!(*status & 2u));
    return 1;
}

static void run_model(uint32_t bytes) {
    if (bytes != 12u) { result_status(BAD_RANGE); return; }
    volatile uint32_t *header;
    uint32_t base = little32(input), first = little32(input + 4), count = little32(input + 8);
    if (!model_header(base, &header)) { result_status(BAD_MODEL); return; }
    if (first >= header[2] || count == 0 || count > header[2] - first) {
        result_status(BAD_RANGE); return;
    }
    uint64_t start = cycle_count();
    append32(count);
    append64(0);
    uint32_t timeout = CPU_CLOCK_HZ * 15u;
    for (uint32_t layer = first; layer < first + count; ++layer) {
        uint32_t status;
        if (!run_layer(header, layer, timeout, &status)) {
            result_status(JOB_TIMEOUT); append32(layer); return;
        }
        if (status & 4u) {
            result_status(ENGINE_ERROR); append32(layer); append32(ENGINE[25]); return;
        }
        append32(layer);
        append32(ENGINE[20]); append32(ENGINE[21]);
        append32(ENGINE[22]); append32(ENGINE[23]); append32(ENGINE[24]);
        append32(ENGINE[25]);
    }
    uint64_t elapsed = cycle_count() - start;
    for (unsigned byte = 0; byte < 8; ++byte) output[8 + byte] = (uint8_t)(elapsed >> (byte * 8));
    invalidate_data();
}

static void match_model(uint32_t bytes) {
    if (bytes != 4u) { result_status(BAD_RANGE); return; }
    volatile uint32_t *header;
    if (!model_header(little32(input), &header) || header[11] != 1u) {
        result_status(BAD_MODEL); return;
    }
    volatile uint32_t *tensor = (volatile uint32_t *)header[10];
    if (tensor[1] != DIMENSION || tensor[2] != 1u || tensor[3] != 1u ||
        tensor[4] != 32u || tensor[5] != 32u || tensor[7] > 255u || tensor[8] != 0 ||
        !ddr_range(tensor[0], DIMENSION * 32u)) {
        result_status(BAD_FEATURE); return;
    }
    invalidate_data();
    uint64_t start = cycle_count();
    uint32_t sum = 0;
    for (unsigned index = 0; index < DIMENSION; ++index) {
        int value = *(volatile uint8_t *)(tensor[0] + index * 32u) - (int)tensor[7];
        last_embedding[index] = (int16_t)value;
        sum += (uint32_t)(value * value);
    }
    if (sum == 0) { embedding_valid = 0; result_status(BAD_FEATURE); return; }
    uint64_t denominator = integer_sqrt((uint64_t)sum << 30);
#if VISION_RECIPROCAL_NORMALIZATION
    uint32_t reciprocal = vision_normalize_reciprocal((uint32_t)denominator);
#endif
    for (unsigned index = 0; index < DIMENSION; ++index) {
        int value = last_embedding[index];
        uint32_t magnitude = (uint32_t)(value < 0 ? -value : value);
#if VISION_RECIPROCAL_NORMALIZATION
        uint32_t normalized = vision_normalize_magnitude(magnitude, (uint32_t)denominator, reciprocal);
#else
        uint32_t normalized = (uint32_t)((((uint64_t)magnitude << 30) + denominator / 2u) / denominator);
#endif
        if (normalized > 32767u) normalized = 32767u;
        last_embedding[index] = (int16_t)(value < 0 ? -(int)normalized : (int)normalized);
    }
    embedding_valid = 1;
    int32_t best_score = -32768, scores[GALLERY_SIZE];
    uint32_t best = 0xffffffffu;
    for (unsigned identity = 0; identity < GALLERY_SIZE; ++identity) {
        scores[identity] = -32768;
        if (!(gallery_valid & (1u << identity))) continue;
        int64_t dot = 0;
        for (unsigned index = 0; index < DIMENSION; ++index)
            dot += (int32_t)last_embedding[index] * (int32_t)gallery[identity][index];
        scores[identity] = (int32_t)(dot / 32768);
        if (scores[identity] > best_score) { best_score = scores[identity]; best = identity; }
    }
    append32(best_score >= match_threshold ? best : 0xffffffffu);
    append32((uint32_t)best_score); append32(gallery_valid);
    append64(cycle_count() - start);
    for (unsigned index = 0; index < DIMENSION; ++index) {
        output[output_bytes++] = (uint8_t)last_embedding[index];
        output[output_bytes++] = (uint8_t)((uint16_t)last_embedding[index] >> 8);
    }
    for (unsigned identity = 0; identity < GALLERY_SIZE; ++identity) append32((uint32_t)scores[identity]);
}

#if CAMERA_TENSOR
static int capture_camera(void) {
    CAMERA[35] = 0;
    uint32_t before_tensor = tensor_sequence();
    uint32_t before_frame = CAMERA[34];
    uint64_t begin = cycle_count();
    CAMERA[37] = 1;
    while (CAMERA[37] & 1u) {
        if ((uint32_t)(cycle_count() - begin) > CPU_CLOCK_HZ * 2u) return JOB_TIMEOUT;
    }
    if (tensor_sequence() != before_tensor + 1u || CAMERA[34] != before_frame + 1u ||
        (CAMERA[17] & 2u)) return ENGINE_ERROR;
    uint32_t bank = (CAMERA[35] >> 2) & 1u;
    CAMERA[35] = 1u | (bank << 1);
    return OK;
}

static void camera_status(void) {
    append32(tensor_sequence());
    append32(CAMERA[17]);
    append32(CAMERA[16]);
    append32(CAMERA[20]);
    append32(CAMERA[21]);
    append32(CAMERA[22]);
    append32(CAMERA[34]);
    append32(CAMERA[35]);
}

#if VISION_FACE_DECODE
/* The camera frame is reported in a virtual 1280x720 space, which is what the
 * host decode used and what the alignment coefficients expect. */
#define FACE_FRAME_WIDTH 1280u
#define FACE_FRAME_HEIGHT 720u

/* Captures a frame, runs the detector and returns finished faces: box, five
 * landmarks, score and the destination->source alignment coefficients. */
static void detect_faces(uint32_t bytes) {
    if (bytes != 20u) { result_status(BAD_RANGE); return; }
    volatile uint32_t *header;
    uint32_t base = little32(input), first = little32(input + 4), count = little32(input + 8);
    int32_t score = (int32_t)little32(input + 12), nms = (int32_t)little32(input + 16);
    if (!model_header(base, &header)) { result_status(BAD_MODEL); return; }
    if (first >= header[2] || count == 0 || count > header[2] - first ||
        score <= 0 || score > 65536 || nms < 0 || nms > 65536) {
        result_status(BAD_RANGE); return;
    }
    append32(count);
    int status = capture_camera();
    if (status != OK) { result_status((uint32_t)status); return; }
    uint64_t start = cycle_count();
    uint32_t timeout = CPU_CLOCK_HZ * 15u;
    for (uint32_t layer = first; layer < first + count; ++layer) {
        uint32_t engine_status;
        if (!run_layer(header, layer, timeout, &engine_status)) {
            uint64_t elapsed = cycle_count() - start;
            result_status(JOB_TIMEOUT); append32(layer); append64(elapsed); return;
        }
        if (engine_status & 4u) {
            uint64_t elapsed = cycle_count() - start;
            result_status(ENGINE_ERROR); append32(layer); append32(ENGINE[25]); append64(elapsed); return;
        }
    }
    uint64_t detect_elapsed = cycle_count() - start;
    invalidate_data();
    static vision_yunet_face faces[VISION_YUNET_MAX_FACES];
    vision_yunet_stats stats;
    uint64_t decode_start = cycle_count();
    int found = vision_yunet_decode(base, FACE_FRAME_WIDTH, FACE_FRAME_HEIGHT, score, nms,
                                    faces, VISION_YUNET_MAX_FACES, &stats);
    uint64_t decode_elapsed = cycle_count() - decode_start;
    if (found < 0) { result_status(BAD_MODEL); append32((uint32_t)(-found)); append64(decode_elapsed); return; }
    append32((uint32_t)found);
    append32(stats.candidates);
    append32(stats.dropped);
    append64(detect_elapsed);
    append64(decode_elapsed);
    for (int index = 0; index < found; ++index) {
        const vision_yunet_face *face = &faces[index];
        append32((uint32_t)face->x); append32((uint32_t)face->y);
        append32((uint32_t)face->width); append32((uint32_t)face->height);
        for (unsigned point = 0; point < 10u; ++point) append32((uint32_t)face->keypoints[point]);
        append32((uint32_t)face->score);
        for (unsigned coefficient = 0; coefficient < 6u; ++coefficient)
            append32((uint32_t)face->affine[coefficient]);
    }
}
#endif
#endif

static void dispatch(uint32_t bytes) {
    result_status(OK);
    switch (request_op) {
    case PING:
        if (bytes) { result_status(BAD_RANGE); break; }
        append32(FIRMWARE_VERSION); append32(ENGINE[26]);
        append32(CPU_CLOCK_HZ); append32(AI_CLOCK_HZ); append32(921600u);
        append32(DEVICE[13]); append32(PAYLOAD_MAX); append32(GALLERY_SIZE);
        break;
    case WRITE: {
        uint32_t address = bytes >= 4u ? little32(input) : 0;
        if (bytes <= 4u || !ddr_range(address, bytes - 4u)) { result_status(BAD_RANGE); break; }
        write_ddr(address, input + 4, bytes - 4u);
        append32(bytes - 4u);
        break;
    }
    case READ: {
        uint32_t address = bytes == 8u ? little32(input) : 0;
        uint32_t length = bytes == 8u ? little32(input + 4) : 0;
        if (length > PAYLOAD_MAX || !ddr_range(address, length)) { result_status(BAD_RANGE); break; }
        invalidate_data();
        for (uint32_t byte = 0; byte < length; ++byte) output[output_bytes++] = *(volatile uint8_t *)(address + byte);
        break;
    }
    case RUN: run_model(bytes); break;
    case CAMERA_DETECT:
#if CAMERA_TENSOR
        if (bytes != 12u) { result_status(BAD_RANGE); break; }
        {
            int status = capture_camera();
            if (status != OK) { result_status((uint32_t)status); break; }
            run_model(bytes);
        }
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CAMERA_CAPTURE:
#if CAMERA_TENSOR
        if (bytes) { result_status(BAD_RANGE); break; }
        {
            int status = capture_camera();
            if (status != OK) { result_status((uint32_t)status); break; }
            camera_status();
        }
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CAMERA_FACES:
#if CAMERA_TENSOR && VISION_FACE_DECODE
        detect_faces(bytes);
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CAMERA_TENSOR_STATUS:
#if CAMERA_TENSOR
        if (bytes) { result_status(BAD_RANGE); break; }
        camera_status();
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CAMERA_FRAME_READ:
#if CAMERA_TENSOR
        if (bytes != 8u) { result_status(BAD_RANGE); break; }
        {
            uint32_t offset = little32(input), count = little32(input + 4);
            uint32_t frame_status = CAMERA[35];
            if (!(frame_status & 1u) || (offset & 1u) || (count & 1u) || count > OUTPUT_MAX - 4u ||
                offset > 115200u || count > 115200u - offset) { result_status(BAD_RANGE); break; }
            uint32_t source_pixel = offset >> 1;
            uint32_t row = source_pixel / 320u, column = source_pixel % 320u;
            uint32_t base = (frame_status & 2u) ? 0x83400000u : 0x83000000u;
            invalidate_data();
            for (uint32_t byte = 0; byte < count; byte += 2u) {
                volatile uint8_t *address = (volatile uint8_t *)(base + row * 10240u + column * 8u);
                output[output_bytes++] = address[0];
                output[output_bytes++] = address[1];
                if (++column == 320u) { column = 0; ++row; }
            }
        }
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CAMERA_ALIGN:
#if CAMERA_TENSOR
        if (bytes != 28u) { result_status(BAD_RANGE); break; }
        for (unsigned index = 0; index < 7u; ++index)
            CAMERA[26 + index] = little32(input + index * 4u);
        CAMERA[33] = 1u;
        {
            uint64_t begin = cycle_count();
            int timed_out = 0;
            while (CAMERA[33] & 1u) {
                if ((uint32_t)(cycle_count() - begin) > CPU_CLOCK_HZ * 2u) {
                    timed_out = 1;
                    break;
                }
            }
            if (timed_out) { result_status(JOB_TIMEOUT); break; }
            append32(CAMERA[36]);
            append32((uint32_t)(cycle_count() - begin));
        }
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CONTROL: {
        if (bytes != 12u) { result_status(BAD_RANGE); break; }
        uint32_t write = little32(input), index = little32(input + 4), value = little32(input + 8);
        if (index > 26u || (index > 17u && index < 20u) || (write && index > 17u) || write > 1u) {
            result_status(BAD_RANGE); break;
        }
        if (write && (ENGINE[0] & 1u)) { result_status(ENGINE_ERROR); break; }
        if (write) ENGINE[index] = value;
        else append32(ENGINE[index]);
        break;
    }
    case ENROLL: {
        uint32_t identity = bytes >= 4u ? little32(input) : GALLERY_SIZE;
        if (identity >= GALLERY_SIZE || (bytes != 4u && bytes != 4u + DIMENSION * 2u) ||
            (bytes == 4u && !embedding_valid)) { result_status(BAD_FEATURE); break; }
        for (unsigned index = 0; index < DIMENSION; ++index)
            gallery[identity][index] = bytes == 4u ? last_embedding[index] :
                (int16_t)((uint16_t)input[4 + index * 2] | (uint16_t)input[5 + index * 2] << 8);
        gallery_valid |= 1u << identity;
        append32(gallery_valid);
        break;
    }
    case MATCH: match_model(bytes); break;
    case CLEAR_GALLERY:
        if (bytes) { result_status(BAD_RANGE); break; }
        gallery_valid = 0; embedding_valid = 0;
        break;
    case COUNTERS:
        if (bytes) { result_status(BAD_RANGE); break; }
        append64(cycle_count());
        for (unsigned index = 8; index < 12; ++index) append32(DEVICE[index]);
        append32(DEVICE[13]);
        break;
    case THRESHOLD:
        if (bytes != 0u && bytes != 4u) { result_status(BAD_RANGE); break; }
        if (bytes) {
            uint32_t value = little32(input);
            if (value > 32767u) { result_status(BAD_RANGE); break; }
            match_threshold = (int32_t)value;
        }
        append32((uint32_t)match_threshold);
        break;
    case COMPRESSED_WRITE: compressed_write(bytes, 0); break;
#if DELTA_WRITE_ENABLE
    case DELTA_COMPRESSED_WRITE: compressed_write(bytes, 1); break;
#endif
    case PACKED_READ: packed_read(bytes); break;
    case CAPABILITIES:
        if (bytes) { result_status(BAD_RANGE); break; }
        append32(2u | 4u | (CAMERA_ENABLE ? 1u : 0u) | (DELTA_WRITE_ENABLE ? 8u : 0u) |
                 (CAMERA_TENSOR ? 48u : 0u) | (VISION_FACE_DECODE ? 64u : 0u) |
                 (VISION_BENCHMARK ? 128u : 0u));
        append32(OUTPUT_MAX);
        break;
    case BENCHMARK:
#if VISION_BENCHMARK
        if (bytes != 4u) { result_status(BAD_RANGE); break; }
        {
            uint32_t iterations = little32(input);
            if (iterations == 0u || iterations > 1000000u) { result_status(BAD_RANGE); break; }
            benchmark_iterations = iterations;
            append32(iterations);
        }
#else
        result_status(BAD_FEATURE);
#endif
        break;
    case CAMERA_PREVIEW:
#if CAMERA_ENABLE
        if (bytes != 4u || little32(input) > 2u) { result_status(BAD_RANGE); break; }
        pause_preview();
        preview_after_response = little32(input) == 1u;
        for (unsigned index = 0; index < 16; ++index) append32(CAMERA[index]);
#else
        result_status(BAD_COMMAND);
#endif
        break;
    default: result_status(BAD_COMMAND); break;
    }
}

void firmware_trap(uint32_t cause, uint32_t pc, uint32_t value) {
#if CAMERA_ENABLE
    preview_after_response = 0;
#endif
    DEVICE[0] = 0xe0u;
    result_status(CPU_TRAP); append32(cause); append32(pc); append32(value);
    respond();
    for (;;) { }
}

int main(void) {
    DEVICE[0] = 0x20u;
    DEVICE[13] = 1;
    for (;;) {
        uint8_t header[16], byte;
        uint32_t magic = 0;
        do { get_byte(&byte, 0); magic = (magic >> 8) | (uint32_t)byte << 24; } while (magic != REQUEST_MAGIC);
        header[0] = 'P'; header[1] = 'G'; header[2] = 'R'; header[3] = '1';
        unsigned index;
        for (index = 4; index < 16; ++index) if (!get_byte(header + index, 1)) break;
        if (index != 16) continue;
        request_op = little32(header + 4); request_sequence = little32(header + 8);
#if CAMERA_ENABLE
        preview_after_response = 0;
#endif
        uint32_t bytes = little32(header + 12);
        if (bytes > PAYLOAD_MAX) continue;
        uint32_t crc = 0xffffffffu;
        for (index = 0; index < 16; ++index) crc = crc_byte(crc, header[index]);
        for (index = 0; index < bytes; ++index) {
            if (!get_byte(input + index, 1)) break;
            crc = crc_byte(crc, input[index]);
        }
        if (index != bytes) continue;
        uint8_t trailer[4];
        for (index = 0; index < 4; ++index) if (!get_byte(trailer + index, 1)) break;
        if (index != 4) continue;
        if (~crc != little32(trailer)) {
            previous_valid = 0; result_status(BAD_CRC); respond(); continue;
        }
        if (previous_valid && previous_sequence == request_sequence && previous_op == request_op && previous_crc == crc) {
#if CAMERA_ENABLE
            preview_after_response = request_op == CAMERA_PREVIEW && bytes == 4u && little32(input) == 1u &&
                                     little32(output) == OK;
#endif
            respond(); continue;
        }
        DEVICE[0] = 0x40u | request_op;
        dispatch(bytes);
        previous_sequence = request_sequence; previous_op = request_op; previous_crc = crc; previous_valid = 1;
        respond();
        DEVICE[0] = 0x20u;
#if VISION_BENCHMARK
        if (benchmark_iterations) {
            uint32_t iterations = benchmark_iterations;
            const char end_marker[] = "\r\n#BENCH-END\r\n";
            benchmark_iterations = 0;
            vision_bench_run(iterations);
            uint64_t cycles = cycle_count();
            put_byte('\r'); put_byte('\n');
            for (int shift = 60; shift >= 0; shift -= 4) {
                uint32_t digit = (uint32_t)((cycles >> shift) & 15u);
                put_byte((uint8_t)(digit < 10u ? '0' + digit : 'a' + digit - 10u));
            }
            for (unsigned index = 0; end_marker[index] != '\0'; ++index)
                put_byte((uint8_t)end_marker[index]);
            DEVICE[0] = 0x20u;
        }
#endif
    }
}
