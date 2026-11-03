`timescale 1ns/1ps

// Outer product: PIXELS U8 activations share CHANNELS signed weights each cycle.
// Accumulators are cleared per tile; arithmetic registers have no reset so the
// FPGA mapper can use the multiplier's internal pipeline registers.
module int8_mac_array #(
    parameter integer PIXELS = 32,
    parameter integer CHANNELS = 16,
    parameter integer CHANNEL_BITS = CHANNELS < 2 ? 1 : $clog2(CHANNELS)
) (
    input wire clk, rst_n, clear, valid, last,
    input wire [PIXELS*8-1:0] activations,
    input wire [CHANNELS*8-1:0] weights,
    input wire [7:0] activation_zero,
    input wire [CHANNEL_BITS-1:0] read_channel,
    output wire [PIXELS*32-1:0] sums,
    output reg done
);
    reg product_valid, product_last;
    reg signed [31:0] accumulator [0:CHANNELS*PIXELS-1];
    genvar pixel, channel;
    generate for (pixel = 0; pixel < PIXELS; pixel = pixel + 1) begin : pixel_lane
        wire signed [8:0] centered = $signed({1'b0, activations[pixel*8 +: 8]}) -
                                     $signed({1'b0, activation_zero});
        assign sums[pixel*32 +: 32] = accumulator[read_channel*PIXELS + pixel];
        for (channel = 0; channel < CHANNELS; channel = channel + 1) begin : channel_lane
            wire signed [7:0] coefficient = weights[channel*8 +: 8];
            reg signed [16:0] product;
            always @(posedge clk) begin
                if (valid) product <= centered * coefficient;
                if (clear) accumulator[channel*PIXELS+pixel] <= 0;
                else if (product_valid)
                    accumulator[channel*PIXELS+pixel] <= accumulator[channel*PIXELS+pixel] +
                                                        {{15{product[16]}}, product};
            end
        end
    end endgenerate
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin product_valid <= 0; product_last <= 0; done <= 0; end
        else if (clear) begin product_valid <= 0; product_last <= 0; done <= 0; end
        else begin
            product_valid <= valid; product_last <= last; done <= product_valid && product_last;
        end
    end
endmodule
