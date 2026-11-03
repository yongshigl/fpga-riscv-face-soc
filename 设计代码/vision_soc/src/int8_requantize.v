`timescale 1ns/1ps

// Positive Q31-style multiplier, arithmetic shift, ties-to-even rounding and
// U8 saturation. Residual addition shares the five-stage arithmetic pipeline.
module int8_requantize #(
    parameter integer PIXELS = 32
) (
    input wire clk, rst_n, valid,
    input wire [PIXELS*32-1:0] sums,
    input wire signed [31:0] bias, multiplier,
    input wire add_mode,
    input wire [PIXELS*8-1:0] add_a, add_b,
    input wire [7:0] add_zero_a, add_zero_b,
    input wire signed [31:0] multiplier_b,
    input wire [5:0] shift,
    input wire [7:0] output_zero, clip_min, clip_max,
    output reg result_valid,
    output wire [PIXELS*8-1:0] result
);
    reg [3:0] valid_pipe;
    reg signed [31:0] coefficient, coefficient_b;
    reg [5:0] shift0, shift1, shift2;
    reg [7:0] zero0, zero1, zero2, zero3, min0, min1, min2, min3, max0, max1, max2, max3;
    genvar pixel;
    generate for (pixel = 0; pixel < PIXELS; pixel = pixel + 1) begin : quant_lane
        reg signed [31:0] biased;
        reg signed [8:0] second_centered;
        reg signed [63:0] product_a;
        reg signed [40:0] product_b;
        reg signed [63:0] product;
        reg signed [8:0] floor_low;
        reg round_up, large_positive, large_negative;
        reg [7:0] value;
        wire signed [8:0] centered_a = $signed({1'b0, add_a[pixel*8 +: 8]}) - $signed({1'b0, add_zero_a});
        wire signed [8:0] centered_b = $signed({1'b0, add_b[pixel*8 +: 8]}) - $signed({1'b0, add_zero_b});
        wire signed [63:0] floor_value = product >>> shift2;
        wire [63:0] discarded = product & ((64'd1 << shift2) - 1'b1);
        wire [63:0] half = 64'd1 << (shift2 - 1'b1);
        wire increment = shift2 != 0 && (discarded > half || (discarded == half && floor_value[0]));
        wire signed [10:0] quantized = $signed(floor_low) + $signed({1'b0, zero3}) +
                                      $signed({1'b0, round_up});
        assign result[pixel*8 +: 8] = value;
        always @(posedge clk) begin
            if (valid) begin
                biased <= add_mode ? {{23{centered_a[8]}}, centered_a} : $signed(sums[pixel*32 +: 32]) + bias;
                second_centered <= add_mode ? centered_b : 9'sd0;
            end
            if (valid_pipe[0]) begin product_a <= biased * coefficient; product_b <= second_centered * coefficient_b; end
            if (valid_pipe[1]) product <= product_a + product_b;
            if (valid_pipe[2]) begin
                floor_low <= floor_value[8:0]; round_up <= increment;
                large_positive <= !floor_value[63] && |floor_value[62:8];
                large_negative <= floor_value[63] && !(&floor_value[62:8]);
            end
            if (valid_pipe[3]) begin
                if (large_positive) value <= max3;
                else if (large_negative) value <= min3;
                else if (quantized > $signed({1'b0, max3})) value <= max3;
                else if (quantized < $signed({1'b0, min3})) value <= min3;
                else value <= quantized[7:0];
            end
        end
    end endgenerate
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin valid_pipe <= 0; result_valid <= 0; end
        else begin valid_pipe <= {valid_pipe[2:0], valid}; result_valid <= valid_pipe[3]; end
    end
    always @(posedge clk) begin
        if (valid) begin
            coefficient <= multiplier; coefficient_b <= multiplier_b; shift0 <= shift;
            zero0 <= output_zero; min0 <= clip_min; max0 <= clip_max;
        end
        if (valid_pipe[0]) begin shift1 <= shift0; zero1 <= zero0; min1 <= min0; max1 <= max0; end
        if (valid_pipe[1]) begin zero2 <= zero1; min2 <= min1; max2 <= max1; end
        if (valid_pipe[1]) shift2 <= shift1;
        if (valid_pipe[2]) begin zero3 <= zero2; min3 <= min2; max3 <= max2; end
    end
endmodule
