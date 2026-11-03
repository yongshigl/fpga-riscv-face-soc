/* Host harness for sw/vision_yunet.c: maps a DDR image into memory and runs the
 * same decoder the RISC-V firmware runs, so the two can be compared. */
#include <stdio.h>
#include <stdlib.h>

#include "vision_yunet.h"

const uint8_t *vision_yunet_heap;
uint32_t vision_yunet_heap_base;

int main(int argc, char **argv) {
    if (argc != 9) {
        fprintf(stderr, "usage: %s heap.bin base width height score_q16 nms_q16 heap_bytes out.json\n", argv[0]);
        return 2;
    }
    uint32_t base = (uint32_t)strtoul(argv[2], NULL, 0);
    uint32_t width = (uint32_t)strtoul(argv[3], NULL, 0);
    uint32_t height = (uint32_t)strtoul(argv[4], NULL, 0);
    int32_t score = (int32_t)strtol(argv[5], NULL, 0);
    int32_t nms = (int32_t)strtol(argv[6], NULL, 0);
    size_t bytes = (size_t)strtoull(argv[7], NULL, 0);
    uint8_t *heap = calloc(1, bytes);
    if (!heap) { fprintf(stderr, "out of memory\n"); return 1; }
    FILE *input = fopen(argv[1], "rb");
    if (!input) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    if (fread(heap, 1, bytes, input) != bytes) { fprintf(stderr, "short heap\n"); return 1; }
    fclose(input);

    vision_yunet_heap = heap;
    vision_yunet_heap_base = base;
    vision_yunet_face faces[VISION_YUNET_MAX_FACES];
    vision_yunet_stats stats;
    int count = vision_yunet_decode(base, width, height, score, nms, faces,
                                    VISION_YUNET_MAX_FACES, &stats);
    if (count < 0) { fprintf(stderr, "decode failed %d\n", count); return 1; }

    FILE *output = fopen(argv[8], "w");
    if (!output) { fprintf(stderr, "cannot write %s\n", argv[8]); return 1; }
    fprintf(output, "{\"count\": %d, \"candidates\": %u, \"dropped\": %u, \"kept\": %u, \"faces\": [",
            count, stats.candidates, stats.dropped, stats.kept);
    for (int index = 0; index < count; ++index) {
        const vision_yunet_face *face = &faces[index];
        fprintf(output, "%s{\"x\": %d, \"y\": %d, \"w\": %d, \"h\": %d, \"score\": %d, \"keypoints\": [",
                index ? ", " : "", face->x, face->y, face->width, face->height, face->score);
        for (int point = 0; point < 10; ++point)
            fprintf(output, "%s%d", point ? ", " : "", face->keypoints[point]);
        fprintf(output, "], \"affine\": [");
        for (int coefficient = 0; coefficient < 6; ++coefficient)
            fprintf(output, "%s%d", coefficient ? ", " : "", face->affine[coefficient]);
        fprintf(output, "]}");
    }
    fprintf(output, "]}\n");
    fclose(output);
    free(heap);
    return 0;
}
