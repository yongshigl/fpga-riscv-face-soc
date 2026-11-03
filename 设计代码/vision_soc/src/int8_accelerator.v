`timescale 1ns/1ps

// NCHW U8 tensors have 32-byte-aligned rows. Weights are [OC/16][K][16]
// signed bytes. Per-channel records are bias, multiplier, shift/min/max and
// reserved bytes (16 bytes total). All DDR transfers use the line port.
module int8_accelerator #(
    parameter integer MAX_K = 576,
    parameter integer WEIGHT_WORDS = 4096,
    parameter integer TIMEOUT_BITS = 30,
    parameter integer MAC_PIXELS = 16,
    // Power-of-two line counts. Defaults preserve the validated 2-line engine.
    parameter integer READ_CACHE_LINES = 2,
    parameter integer DEPTHWISE_CHANNEL_OUTER = 0,
    parameter integer READ_CACHE_FULL_LRU = 0
) (
    input wire clk, rst_n,
    input wire control_req_valid,
    output wire control_req_ready,
    input wire control_req_write,
    input wire [31:0] control_req_addr, control_req_wdata,
    input wire [3:0] control_req_wstrb,
    output reg control_rsp_valid,
    input wire control_rsp_ready,
    output reg [31:0] control_rsp_rdata,
    output reg control_rsp_error,
    output wire irq,
    output wire mem_req_valid,
    input wire mem_req_ready,
    output wire mem_req_write,
    output wire [31:0] mem_req_addr,
    output wire [255:0] mem_req_wdata,
    output wire [31:0] mem_req_wstrb,
    input wire mem_rsp_valid,
    output wire mem_rsp_ready,
    input wire [255:0] mem_rsp_rdata,
    input wire mem_rsp_error,
    output wire [7:0] debug_state
);
    localparam IDLE = 0, WEIGHT_REQUEST = 1, WEIGHT_FIRST = 2, WEIGHT_SECOND = 3,
               PARAM_REQUEST = 4, PARAM_FIRST = 5, PARAM_SECOND = 6,
               GATHER_INIT = 7, GATHER_ROW = 8, GATHER_READ = 9, GATHER_APPLY = 10,
               GATHER_DONE = 11, MAC_START = 12, MAC_RUN = 13, MAC_WAIT = 14,
               QUANT_PARAM = 15, QUANT_SEND = 16, QUANT_WAIT = 17,
               OUTPUT_BEGIN = 18, OUTPUT_DONE = 19,
               DMA_REQUEST = 20, DMA_WAIT = 21, OUTPUT_READ = 22, OUTPUT_LATCH = 23;
    reg [4:0] state, dma_return;
    reg [31:0] cfg [0:17];
    reg start_requested, clear_requested, busy, done, error;
    reg [31:0] error_code, read_transactions, write_transactions, compute_cycles;
    reg [63:0] elapsed_cycles;
    reg [TIMEOUT_BITS-1:0] timeout_count;
    wire [5:0] control_index = control_req_addr[7:2];
    assign control_req_ready = !control_rsp_valid;
    assign irq = done;
    assign debug_state = {3'd0, state};
    wire [2:0] op = cfg[1][2:0];
    wire [9:0] iw = cfg[7][9:0], ih = cfg[7][25:16];
    wire [9:0] cin = cfg[8][9:0], cout = cfg[8][25:16];
    wire [9:0] ow = cfg[9][9:0], oh = cfg[9][25:16];
    wire [9:0] input_row = cfg[10][9:0], output_row = cfg[10][25:16];
    wire [3:0] kw = cfg[13][3:0], kh = cfg[13][7:4];
    wire [3:0] sx = cfg[13][11:8], sy = cfg[13][15:12];
    wire [3:0] px = cfg[13][19:16], py = cfg[13][23:20];
    wire [7:0] input_zero = cfg[14][7:0], output_zero = cfg[14][15:8];
    wire [7:0] second_zero = cfg[14][23:16];
    wire [15:0] reduction_depth = op == 2 ? 16'd1 : {6'd0, cin};
    // Supported kernels are square. Constant shift/add products avoid cascaded APMs in validation.
    wire [15:0] requested_k = kw == 1 ? reduction_depth :
        kw == 3 ? (reduction_depth << 3) + reduction_depth :
        kw == 7 ? (reduction_depth << 5) + (reduction_depth << 4) + reduction_depth : 16'd0;
    wire [5:0] output_blocks = (cout + 15) >> 4;
    wire [31:0] requested_weight_words = requested_k * output_blocks;
    wire [31:0] input_size = cin * cfg[11], output_size = cout * cfg[12];
    wire [31:0] input_end = cfg[2] + input_size, output_end = cfg[4] + output_size;
    wire [31:0] second_end = cfg[3] + input_size;
    wire shapes_valid = cfg[7][15:0] != 0 && cfg[7][15:0] <= 640 &&
        cfg[7][31:16] != 0 && cfg[7][31:16] <= 640 &&
        cfg[8][15:0] != 0 && cfg[8][15:0] <= 512 &&
        cfg[8][31:16] != 0 && cfg[8][31:16] <= 512 &&
        cfg[9][15:0] != 0 && cfg[9][15:0] <= 640 &&
        cfg[9][31:16] != 0 && cfg[9][31:16] <= 640 &&
        cfg[10][15:0] >= iw && cfg[10][15:0] <= 640 &&
        cfg[10][31:16] >= ow && cfg[10][31:16] <= 640 &&
        cfg[10][4:0] == 0 && cfg[10][20:16] == 0 &&
        cfg[11] == input_row * ih && cfg[12] == output_row * oh;
    wire buffers_valid = cfg[2][31:30] == 2'b10 && cfg[4][31:30] == 2'b10 &&
        cfg[2][4:0] == 0 && cfg[4][4:0] == 0 &&
        input_end > cfg[2] && input_end <= 32'hc0000000 &&
        output_end > cfg[4] && output_end <= 32'hc0000000 &&
        (output_end <= cfg[2] || input_end <= cfg[4]);
    wire [15:0] conv_width = sx == 2 ? ((iw + 2*px - kw) >> 1) + 1 : iw + 2*px - kw + 1;
    wire [15:0] conv_height = sy == 2 ? ((ih + 2*py - kh) >> 1) + 1 : ih + 2*py - kh + 1;
    wire [31:0] weights_end = cfg[5] + requested_weight_words*16;
    wire [31:0] parameters_end = cfg[6] + cout*16;
    wire convolution_valid = (kw == 1 || kw == 3 || kw == 7) && kh == kw &&
        (sx == 1 || sx == 2) && sy == sx && px < kw && py < kh &&
        iw + 2*px >= kw && ih + 2*py >= kh && ow == conv_width && oh == conv_height &&
        requested_k != 0 && requested_k <= MAX_K && requested_weight_words <= WEIGHT_WORDS &&
        cfg[5][31:30] == 2'b10 && cfg[6][31:30] == 2'b10 &&
        cfg[5][4:0] == 0 && cfg[6][4:0] == 0 && (op != 2 || cin == cout) &&
        weights_end > cfg[5] && weights_end <= 32'hc0000000 &&
        parameters_end > cfg[6] && parameters_end <= 32'hc0000000;
    wire operation_valid = (op == 1 || op == 2) ? convolution_valid :
        op == 3 ? (cin == cout && ow == iw && oh == ih && cfg[3][31:30] == 2'b10 &&
                    cfg[3][4:0] == 0 && second_end > cfg[3] && second_end <= 32'hc0000000 &&
                    (output_end <= cfg[3] || second_end <= cfg[4]) &&
                    cfg[15][31] == 0 && cfg[16][31] == 0 && cfg[17] <= 63) :
        op == 4 ? (cin == cout && ow == (iw >> 1) && oh == (ih >> 1) &&
                   kw == 2 && kh == 2 && sx == 2 && sy == 2 && px == 0 && py == 0 && input_zero == output_zero) :
        op == 5 ? (cin == cout && ow == iw*2 && oh == ih*2 && kw == 1 && kh == 1 &&
                   sx == 2 && sy == 2 && px == 0 && py == 0 && input_zero == output_zero) : 1'b0;

    integer register_number, control_byte;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            start_requested <= 0; clear_requested <= 0;
            control_rsp_valid <= 0; control_rsp_rdata <= 0; control_rsp_error <= 0;
            for (register_number = 0; register_number < 18; register_number = register_number + 1) cfg[register_number] <= 0;
        end else begin
            start_requested <= 0; clear_requested <= 0;
            if (control_rsp_valid && control_rsp_ready) control_rsp_valid <= 0;
            if (control_req_valid && control_req_ready) begin
                control_rsp_valid <= 1; control_rsp_error <= 0; control_rsp_rdata <= 0;
                if (control_req_addr[1:0] != 0) control_rsp_error <= 1;
                else if (control_req_write) begin
                    if (busy) control_rsp_error <= 1;
                    else if (control_index == 0 && control_req_wstrb[0]) begin
                        start_requested <= control_req_wdata[0]; clear_requested <= control_req_wdata[1];
                    end else if (control_index >= 1 && control_index <= 17) begin
                        for (control_byte = 0; control_byte < 4; control_byte = control_byte + 1)
                            if (control_req_wstrb[control_byte])
                                cfg[control_index][control_byte*8 +: 8] <= control_req_wdata[control_byte*8 +: 8];
                    end
                    else control_rsp_error <= 1;
                end else case (control_index)
                    0: control_rsp_rdata <= {29'd0, error, done, busy};
                    20: control_rsp_rdata <= elapsed_cycles[31:0];
                    21: control_rsp_rdata <= elapsed_cycles[63:32];
                    22: control_rsp_rdata <= read_transactions;
                    23: control_rsp_rdata <= write_transactions;
                    24: control_rsp_rdata <= compute_cycles;
                    25: control_rsp_rdata <= error_code;
                    26: control_rsp_rdata <= 32'h26100123;
                    default: if (control_index >= 1 && control_index <= 17) control_rsp_rdata <= cfg[control_index];
                             else control_rsp_error <= 1;
                endcase
            end
        end
    end

    reg [9:0] k_count, fill_k, reduction_channel, compute_index;
    reg [12:0] weight_count, weight_index, weight_base;
    reg [9:0] parameter_index;
    reg [9:0] tile_x, tile_y;
    reg [8:0] output_channel;
    reg [3:0] kernel_x, kernel_y, quant_channel;
    reg quant_half, mac_half, gather_second;
    wire [5:0] valid_pixels = ow-tile_x >= 32 ? 6'd32 : ow-tile_x;
    wire [4:0] valid_channels = op == 2 ? 5'd1 : cout-output_channel >= 16 ? 5'd16 : cout-output_channel;
    reg signed [12:0] gather_x, gather_y;
    wire signed [12:0] gather_last_x = gather_x + (op == 5 ? ((valid_pixels-1) >> 1) :
                                                 sx == 2 && op != 3 ? ((valid_pixels-1) << 1) : valid_pixels-1);
    reg [31:0] gather_row_address;
    reg [4:0] gather_line, gather_last_line;
    reg [255:0] gathered, vector_a, vector_b, pool_values, output_data;
    reg [31:0] dma_address;
    localparam CACHE_INDEX_BITS = READ_CACHE_LINES < 2 ? 1 : $clog2(READ_CACHE_LINES);
    reg dma_write, dma_cache_fill;
    reg [CACHE_INDEX_BITS-1:0] dma_cache_way;
    reg [255:0] dma_data;
    reg [READ_CACHE_LINES-1:0] read_cache_valid;
    reg [31:0] read_cache_tag [0:READ_CACHE_LINES-1];
    reg [255:0] read_cache_data [0:READ_CACHE_LINES-1];
    // A tree bit names the less recently used child. With two lines this is
    // exactly the original one-bit LRU policy; larger caches use tree PLRU.
    reg [READ_CACHE_LINES-2:0] read_cache_plru;
    wire [CACHE_INDEX_BITS-1:0] cache_age [0:READ_CACHE_LINES-1];
    wire [31:0] gather_address = gather_row_address + {22'd0, gather_line, 5'd0};
    reg cached;
    reg [CACHE_INDEX_BITS-1:0] cache_hit_way, cache_victim;
    integer cache_lookup, cache_level, cache_node;
    always @* begin
        cached = 0;
        cache_hit_way = 0;
        for (cache_lookup = READ_CACHE_LINES-1; cache_lookup >= 0; cache_lookup = cache_lookup-1)
            if (read_cache_valid[cache_lookup] && read_cache_tag[cache_lookup] == gather_address) begin
                cached = 1;
                cache_hit_way = cache_lookup;
            end
        cache_victim = 0;
        cache_node = 0;
        for (cache_level = 0; cache_level < CACHE_INDEX_BITS; cache_level = cache_level+1) begin
            cache_victim[CACHE_INDEX_BITS-1-cache_level] = read_cache_plru[cache_node];
            cache_node = cache_node*2+1+read_cache_plru[cache_node];
        end
        if (READ_CACHE_FULL_LRU != 0) begin
            cache_victim = 0;
            for (cache_lookup = 1; cache_lookup < READ_CACHE_LINES; cache_lookup = cache_lookup+1)
                if (cache_age[cache_lookup] > cache_age[cache_victim]) cache_victim = cache_lookup;
        end
        // Prefer empty lines, choosing the same first invalid way as before.
        for (cache_lookup = READ_CACHE_LINES-1; cache_lookup >= 0; cache_lookup = cache_lookup-1)
            if (!read_cache_valid[cache_lookup]) cache_victim = cache_lookup;
    end
    function [READ_CACHE_LINES-2:0] touch_cache;
        input [READ_CACHE_LINES-2:0] tree;
        input [CACHE_INDEX_BITS-1:0] way;
        integer level, node;
        reg direction;
        begin
            touch_cache = tree;
            node = 0;
            for (level = 0; level < CACHE_INDEX_BITS; level = level+1) begin
                direction = way[CACHE_INDEX_BITS-1-level];
                touch_cache[node] = !direction;
                node = node*2+1+direction;
            end
        end
    endfunction
    generate if (READ_CACHE_FULL_LRU != 0) begin : ranked_cache_lru
        // Small stencil working sets benefit from exact LRU. This optional
        // branch has only LINES*log2(LINES) rank bits; default PLRU is retained.
        reg [CACHE_INDEX_BITS-1:0] age [0:READ_CACHE_LINES-1];
        wire touch_hit = state == GATHER_READ && cached;
        wire touch_fill = state == DMA_WAIT && mem_rsp_valid && !mem_rsp_error && dma_cache_fill;
        wire [CACHE_INDEX_BITS-1:0] touch_way = touch_hit ? cache_hit_way : dma_cache_way;
        genvar age_way;
        for (age_way = 0; age_way < READ_CACHE_LINES; age_way = age_way+1) begin : age_lane
            assign cache_age[age_way] = age[age_way];
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) age[age_way] <= 0;
                else if (start_requested && !busy) age[age_way] <= 0;
                else if (touch_hit || touch_fill) begin
                    if (touch_way == age_way) age[age_way] <= 0;
                    else if (read_cache_valid[age_way] &&
                             (!read_cache_valid[touch_way] || age[age_way] < age[touch_way]))
                        age[age_way] <= age[age_way]+1'b1;
                end
            end
        end
    end else begin : unused_ranked_cache
        genvar age_way;
        for (age_way = 0; age_way < READ_CACHE_LINES; age_way = age_way+1) begin : age_lane
            assign cache_age[age_way] = 0;
        end
    end endgenerate
    assign mem_req_valid = state == DMA_REQUEST;
    assign mem_req_write = dma_write;
    assign mem_req_addr = dma_address;
    assign mem_req_wdata = output_data;
    assign mem_req_wstrb = dma_write ? 32'hffffffff : 32'd0;
    assign mem_rsp_ready = state == DMA_WAIT;

    reg [127:0] weight_memory [0:WEIGHT_WORDS-1];
    reg [127:0] parameter_memory [0:511];
    reg [255:0] activation_memory [0:MAX_K-1];
    reg [127:0] weight_read, parameter_read;
    reg [255:0] activation_read;
    wire weight_write = state == WEIGHT_FIRST || state == WEIGHT_SECOND;
    wire parameter_write = state == PARAM_FIRST || state == PARAM_SECOND;
    wire [127:0] preload_data = (state == WEIGHT_SECOND || state == PARAM_SECOND) ? dma_data[255:128] : dma_data[127:0];
    wire [11:0] weight_read_address = weight_base + compute_index;
    wire [8:0] parameter_read_address = output_channel + quant_channel;
    always @(posedge clk) begin
        if (weight_write) weight_memory[weight_index[11:0]] <= preload_data;
        if (parameter_write) parameter_memory[parameter_index[8:0]] <= preload_data;
        if (state == GATHER_DONE && (op == 1 || op == 2)) activation_memory[fill_k] <= gathered;
        weight_read <= weight_memory[weight_read_address];
        parameter_read <= parameter_memory[parameter_read_address];
        activation_read <= activation_memory[compute_index];
    end
    reg compute_read_valid, compute_read_last;
    wire [127:0] mac_weights = op == 2 ? {120'd0, weight_read[output_channel[3:0]*8 +: 8]} : weight_read;
    wire [MAC_PIXELS*32-1:0] mac_sums;
    wire mac_done;
    wire [MAC_PIXELS*8-1:0] mac_activations;
    wire [511:0] requant_sums;
    generate if (MAC_PIXELS == 16) begin : reused_pixels
        assign mac_activations = activation_read[mac_half*128 +: 128];
        assign requant_sums = mac_sums;
    end else begin : full_pixels
        assign mac_activations = activation_read;
        assign requant_sums = mac_sums[quant_half*512 +: 512];
    end endgenerate
    int8_mac_array #(.PIXELS(MAC_PIXELS)) mac (
        .clk(clk), .rst_n(rst_n), .clear(state == MAC_START),
        .valid(compute_read_valid), .last(compute_read_last), .activations(mac_activations),
        .weights(mac_weights), .activation_zero(input_zero), .read_channel(quant_channel),
        .sums(mac_sums), .done(mac_done)
    );
    wire [127:0] quantized;
    wire quantized_valid;
    int8_requantize #(.PIXELS(16)) requantizer (
        .clk(clk), .rst_n(rst_n), .valid(state == QUANT_SEND),
        .sums(requant_sums), .bias(op == 3 ? 32'd0 : parameter_read[31:0]),
        .multiplier(op == 3 ? cfg[15] : parameter_read[63:32]),
        .shift(op == 3 ? cfg[17][5:0] : parameter_read[69:64]),
        .output_zero(output_zero), .clip_min(op == 3 ? 8'd0 : parameter_read[79:72]),
        .clip_max(op == 3 ? 8'd255 : parameter_read[87:80]),
        .add_mode(op == 3), .add_a(vector_a[quant_half*128 +: 128]),
        .add_b(vector_b[quant_half*128 +: 128]), .add_zero_a(input_zero),
        .add_zero_b(second_zero), .multiplier_b(op == 3 ? cfg[16] : 32'd0),
        .result_valid(quantized_valid), .result(quantized)
    );

    wire quant_group = MAC_PIXELS == 16 && (op == 1 || op == 2) ? mac_half : quant_half;
    wire [127:0] quantized_padded;
    genvar quant_lane;
    generate for (quant_lane = 0; quant_lane < 16; quant_lane = quant_lane + 1) begin : pad_quantized
        assign quantized_padded[quant_lane*8 +: 8] = quant_group*16+quant_lane < valid_pixels ?
            quantized[quant_lane*8 +: 8] : output_zero;
    end endgenerate
    reg [127:0] output_lower [0:15], output_upper [0:15];
    reg [255:0] output_buffer_read;
    generate if (MAC_PIXELS == 16) begin : compact_output
        always @(posedge clk) begin
            if (state == QUANT_WAIT && quantized_valid && (op == 1 || op == 2)) begin
                if (mac_half) output_upper[quant_channel] <= quantized_padded;
                else begin
                    output_lower[quant_channel] <= quantized_padded;
                    if (valid_pixels <= 16) output_upper[quant_channel] <= {16{output_zero}};
                end
            end
            output_buffer_read <= {output_upper[quant_channel], output_lower[quant_channel]};
        end
    end else begin : unused_compact_output
        always @(posedge clk) output_buffer_read <= 0;
    end endgenerate

    task fail_job;
        input [31:0] code;
        begin state <= IDLE; busy <= 0; done <= 1; error <= 1; error_code <= code; end
    endtask
    task next_tile;
        begin
            output_channel <= 0; weight_base <= 0; quant_channel <= 0;
            fill_k <= 0; reduction_channel <= 0; kernel_x <= 0; kernel_y <= 0; gather_second <= 0;
            pool_values <= 0;
            mac_half <= 0;
            if (tile_x + 32 < ow) begin tile_x <= tile_x + 32; state <= GATHER_INIT; end
            else if (tile_y + 1 < oh) begin tile_x <= 0; tile_y <= tile_y + 1'b1; state <= GATHER_INIT; end
            else begin state <= IDLE; busy <= 0; done <= 1; end
        end
    endtask
    task next_depthwise_tile;
        begin
            // Keep one channel active across adjacent x/y tiles so its stencil
            // lines remain reusable. All output addresses still use NCHW.
            quant_channel <= 0; quant_half <= 0; mac_half <= 0;
            fill_k <= 0; reduction_channel <= 0; kernel_x <= 0; kernel_y <= 0;
            gather_second <= 0; pool_values <= 0;
            if (tile_x + 32 < ow) begin tile_x <= tile_x + 32; state <= GATHER_INIT; end
            else if (tile_y + 1 < oh) begin tile_x <= 0; tile_y <= tile_y + 1'b1; state <= GATHER_INIT; end
            else if (output_channel + 1 < cout) begin
                tile_x <= 0; tile_y <= 0;
                if (output_channel[3:0] == 15) weight_base <= weight_base + k_count;
                output_channel <= output_channel + 1'b1;
                state <= GATHER_INIT;
            end else begin state <= IDLE; busy <= 0; done <= 1; end
        end
    endtask
    genvar pixel;
    wire [415:0] gather_coordinates;
    wire [31:0] gather_enabled;
    wire [255:0] gather_bytes;
    generate for (pixel = 0; pixel < 32; pixel = pixel + 1) begin : gather_lane
        assign gather_coordinates[pixel*13 +: 13] = gather_x +
            (op == 5 ? pixel/2 : sx == 2 && op != 3 ? pixel*2 : pixel);
        assign gather_enabled[pixel] = pixel < valid_pixels &&
            $signed(gather_coordinates[pixel*13 +: 13]) >= 0 &&
            $signed(gather_coordinates[pixel*13 +: 13]) < $signed({3'd0, iw}) &&
            gather_coordinates[pixel*13+5 +: 5] == gather_line;
        assign gather_bytes[pixel*8 +: 8] = dma_data[gather_coordinates[pixel*13 +: 5]*8 +: 8];
    end endgenerate
    integer lane;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE; dma_return <= IDLE; busy <= 0; done <= 0; error <= 0; error_code <= 0;
            elapsed_cycles <= 0; timeout_count <= 0; read_transactions <= 0; write_transactions <= 0; compute_cycles <= 0;
            k_count <= 0; fill_k <= 0; reduction_channel <= 0; compute_index <= 0;
            weight_count <= 0; weight_index <= 0; weight_base <= 0; parameter_index <= 0;
            tile_x <= 0; tile_y <= 0; output_channel <= 0; kernel_x <= 0; kernel_y <= 0;
            quant_channel <= 0; quant_half <= 0; mac_half <= 0; gather_second <= 0; gather_x <= 0; gather_y <= 0;
            gather_row_address <= 0; gather_line <= 0; gather_last_line <= 0;
            gathered <= 0; vector_a <= 0; vector_b <= 0; pool_values <= 0; output_data <= 0;
            dma_address <= 0; dma_write <= 0; dma_cache_fill <= 0; dma_cache_way <= 0; dma_data <= 0;
            read_cache_valid <= 0; read_cache_plru <= 0;
            compute_read_valid <= 0; compute_read_last <= 0;
        end else begin
            compute_read_valid <= state == MAC_RUN && compute_index < k_count;
            compute_read_last <= state == MAC_RUN && compute_index == k_count-1;
            if (busy) begin elapsed_cycles <= elapsed_cycles + 1'b1; timeout_count <= timeout_count + 1'b1; end
            if (clear_requested && !busy) begin done <= 0; error <= 0; error_code <= 0; end
            if (start_requested && !busy) begin
                elapsed_cycles <= 0; timeout_count <= 0; read_transactions <= 0; write_transactions <= 0; compute_cycles <= 0;
                done <= 0; error <= 0; error_code <= 0; busy <= 1;
                tile_x <= 0; tile_y <= 0; output_channel <= 0; quant_channel <= 0; quant_half <= 0;
                fill_k <= 0; reduction_channel <= 0; kernel_x <= 0; kernel_y <= 0; gather_second <= 0;
                weight_base <= 0; weight_index <= 0; parameter_index <= 0;
                k_count <= requested_k[9:0]; weight_count <= requested_weight_words[12:0];
                read_cache_valid <= 0; read_cache_plru <= 0; pool_values <= 0;
                if (!shapes_valid || !buffers_valid || !operation_valid || cfg[1] == 0 || cfg[1] > 5) fail_job(1);
                else state <= op == 1 || op == 2 ? WEIGHT_REQUEST : GATHER_INIT;
            end else case (state)
                IDLE: state <= IDLE;
                WEIGHT_REQUEST: begin
                    dma_address <= cfg[5] + weight_index*16; dma_write <= 0;
                    dma_cache_fill <= 0; dma_return <= WEIGHT_FIRST; state <= DMA_REQUEST;
                end
                WEIGHT_FIRST: if (weight_index + 1 == weight_count) begin parameter_index <= 0; state <= PARAM_REQUEST; end
                              else begin weight_index <= weight_index + 1'b1; state <= WEIGHT_SECOND; end
                WEIGHT_SECOND: if (weight_index + 1 == weight_count) begin parameter_index <= 0; state <= PARAM_REQUEST; end
                               else begin weight_index <= weight_index + 1'b1; state <= WEIGHT_REQUEST; end
                PARAM_REQUEST: begin
                    dma_address <= cfg[6] + parameter_index*16; dma_write <= 0;
                    dma_cache_fill <= 0; dma_return <= PARAM_FIRST; state <= DMA_REQUEST;
                end
                PARAM_FIRST: if (parameter_index + 1 == cout) state <= GATHER_INIT;
                             else begin parameter_index <= parameter_index + 1'b1; state <= PARAM_SECOND; end
                PARAM_SECOND: if (parameter_index + 1 == cout) state <= GATHER_INIT;
                              else begin parameter_index <= parameter_index + 1'b1; state <= PARAM_REQUEST; end
                GATHER_INIT: begin
                    gathered <= {32{gather_second ? second_zero : input_zero}};
                    if (op == 5) begin gather_x <= $signed({3'd0, tile_x >> 1}); gather_y <= $signed({3'd0, tile_y >> 1}); end
                    else begin
                        gather_x <= $signed({3'd0, tile_x}) * (op == 3 ? 1 : sx) + kernel_x - px;
                        gather_y <= $signed({3'd0, tile_y}) * (op == 3 ? 1 : sy) + kernel_y - py;
                    end
                    state <= GATHER_ROW;
                end
                GATHER_ROW: if (gather_y < 0 || gather_y >= $signed({3'd0, ih}) ||
                               gather_x >= $signed({3'd0, iw}) || gather_last_x < 0) state <= GATHER_DONE;
                            else begin
                                gather_row_address <= (gather_second ? cfg[3] : cfg[2]) +
                                    (op == 1 ? reduction_channel : {1'b0, output_channel}) * cfg[11] + gather_y * input_row;
                                gather_line <= gather_x < 0 ? 0 : gather_x[9:5];
                                gather_last_line <= gather_last_x >= $signed({3'd0, iw}) ? (iw-1) >> 5 : gather_last_x[9:5];
                                state <= GATHER_READ;
                            end
                GATHER_READ: if (cached) begin
                    dma_data <= read_cache_data[cache_hit_way];
                    read_cache_plru <= touch_cache(read_cache_plru, cache_hit_way); state <= GATHER_APPLY;
                end else begin
                    dma_address <= gather_address; dma_write <= 0; dma_return <= GATHER_APPLY;
                    dma_cache_fill <= 1; dma_cache_way <= cache_victim;
                    state <= DMA_REQUEST;
                end
                GATHER_APPLY: begin
                    for (lane = 0; lane < 32; lane = lane + 1) begin
                        if (gather_enabled[lane]) gathered[lane*8 +: 8] <= gather_bytes[lane*8 +: 8];
                    end
                    if (gather_line == gather_last_line) state <= GATHER_DONE;
                    else begin gather_line <= gather_line + 1'b1; state <= GATHER_READ; end
                end
                GATHER_DONE: if (op == 1 || op == 2) begin
                    if (fill_k + 1 == k_count) begin
                        compute_index <= 0; quant_channel <= 0; mac_half <= 0; state <= MAC_START;
                    end
                    else begin
                        fill_k <= fill_k + 1'b1;
                        if (kernel_x + 1 < kw) kernel_x <= kernel_x + 1'b1;
                        else begin
                            kernel_x <= 0;
                            if (kernel_y + 1 < kh) kernel_y <= kernel_y + 1'b1;
                            else begin kernel_y <= 0; reduction_channel <= reduction_channel + 1'b1; end
                        end
                        state <= GATHER_INIT;
                    end
                end else if (op == 3) begin
                    if (!gather_second) begin vector_a <= gathered; gather_second <= 1; state <= GATHER_INIT; end
                    else begin vector_b <= gathered; quant_channel <= 0; quant_half <= 0; state <= QUANT_PARAM; end
                end else if (op == 4) begin
                    for (lane = 0; lane < 32; lane = lane + 1)
                        if ((kernel_x == 0 && kernel_y == 0) || gathered[lane*8 +: 8] > pool_values[lane*8 +: 8])
                            pool_values[lane*8 +: 8] <= gathered[lane*8 +: 8];
                    if (kernel_x == 1 && kernel_y == 1) begin
                        for (lane = 0; lane < 32; lane = lane + 1)
                            output_data[lane*8 +: 8] <= lane >= valid_pixels ? output_zero :
                                gathered[lane*8 +: 8] > pool_values[lane*8 +: 8] ? gathered[lane*8 +: 8] : pool_values[lane*8 +: 8];
                        state <= OUTPUT_BEGIN;
                    end else begin
                        kernel_x <= kernel_x == 0 ? 1 : 0;
                        if (kernel_x == 1) kernel_y <= 1;
                        state <= GATHER_INIT;
                    end
                end else begin output_data <= gathered; state <= OUTPUT_BEGIN; end
                MAC_START: begin compute_index <= 0; state <= MAC_RUN; end
                MAC_RUN: if (compute_index == k_count) state <= MAC_WAIT;
                         else begin compute_index <= compute_index + 1'b1; compute_cycles <= compute_cycles + 1'b1; end
                MAC_WAIT: if (mac_done) begin quant_channel <= 0; quant_half <= 0; state <= QUANT_PARAM; end
                QUANT_PARAM: state <= QUANT_SEND;
                QUANT_SEND: state <= QUANT_WAIT;
                QUANT_WAIT: if (quantized_valid) begin
                    if (MAC_PIXELS == 16 && (op == 1 || op == 2)) begin
                        if (op == 1 && quant_channel + 1 < valid_channels) begin
                            quant_channel <= quant_channel + 1'b1; state <= QUANT_PARAM;
                        end else if (!mac_half && valid_pixels > 16) begin
                            mac_half <= 1; quant_channel <= 0; compute_index <= 0; state <= MAC_START;
                        end else begin quant_channel <= 0; state <= OUTPUT_READ; end
                    end else begin
                        output_data[quant_half*128 +: 128] <= quantized_padded;
                        if (!quant_half) begin quant_half <= 1; state <= QUANT_SEND; end
                        else state <= OUTPUT_BEGIN;
                    end
                end
                OUTPUT_READ: state <= OUTPUT_LATCH;
                OUTPUT_LATCH: begin output_data <= output_buffer_read; state <= OUTPUT_BEGIN; end
                OUTPUT_BEGIN: begin
                    dma_address <= cfg[4] + (output_channel + quant_channel) * cfg[12] + tile_y*output_row + tile_x;
                    dma_write <= 1; dma_cache_fill <= 0; dma_return <= OUTPUT_DONE; state <= DMA_REQUEST;
                end
                OUTPUT_DONE: if (op == 1 && quant_channel + 1 < valid_channels) begin
                    quant_channel <= quant_channel + 1'b1; quant_half <= 0;
                    state <= MAC_PIXELS == 16 ? OUTPUT_READ : QUANT_PARAM;
                end else if (op == 1 && output_channel + 16 < cout) begin
                    output_channel <= output_channel + 16; weight_base <= weight_base + k_count;
                    quant_channel <= 0; quant_half <= 0; mac_half <= 0; compute_index <= 0; state <= MAC_START;
                end else if (op == 2 && DEPTHWISE_CHANNEL_OUTER != 0) next_depthwise_tile();
                else if (op != 1 && output_channel + 1 < cout) begin
                    if (output_channel[3:0] == 15) weight_base <= weight_base + k_count;
                    output_channel <= output_channel + 1'b1; quant_channel <= 0; quant_half <= 0;
                    fill_k <= 0; reduction_channel <= 0; kernel_x <= 0; kernel_y <= 0; gather_second <= 0;
                    pool_values <= 0; state <= GATHER_INIT;
                end else next_tile();
                DMA_REQUEST: if (mem_req_ready) begin
                    if (dma_write) write_transactions <= write_transactions + 1'b1;
                    else read_transactions <= read_transactions + 1'b1;
                    state <= DMA_WAIT;
                end
                DMA_WAIT: if (mem_rsp_valid) begin
                    if (mem_rsp_error) fail_job(2);
                    else begin
                        dma_data <= mem_rsp_rdata; state <= dma_return;
                        if (dma_cache_fill) begin
                            read_cache_valid[dma_cache_way] <= 1;
                            read_cache_tag[dma_cache_way] <= dma_address;
                            read_cache_data[dma_cache_way] <= mem_rsp_rdata;
                            read_cache_plru <= touch_cache(read_cache_plru, dma_cache_way);
                        end
                    end
                end
                default: fail_job(4);
            endcase
            if (busy && &timeout_count) fail_job(3);
            if (parameter_write && (preload_data[63] || preload_data[63:32] == 0 ||
                preload_data[71:64] > 63 || preload_data[79:72] > preload_data[87:80])) fail_job(5);
        end
    end
endmodule
