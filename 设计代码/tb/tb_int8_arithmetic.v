`timescale 1ns/1ps

module tb_int8_arithmetic;
    reg clk = 0, rst_n = 0, clear = 0, valid = 0, last = 0;
    always #5 clk = ~clk;
    reg [31:0] activations = 0;
    reg [23:0] weights = 0;
    reg [7:0] input_zero = 0;
    reg [1:0] read_channel = 0;
    wire [127:0] sums;
    wire done;
    int8_mac_array #(.PIXELS(4), .CHANNELS(3), .CHANNEL_BITS(2)) mac (
        .clk(clk), .rst_n(rst_n), .clear(clear), .valid(valid), .last(last),
        .activations(activations), .weights(weights), .activation_zero(input_zero),
        .read_channel(read_channel), .sums(sums), .done(done)
    );
    reg quant_valid = 0;
    reg [127:0] quant_sums = 0;
    reg signed [31:0] bias = 0, multiplier = 0;
    reg signed [31:0] multiplier_b = 0;
    reg add_mode = 0;
    reg [31:0] add_a = 0, add_b = 0;
    reg [7:0] add_zero_a = 0, add_zero_b = 0;
    reg [5:0] shift = 0;
    reg [7:0] output_zero = 0, clip_min = 0, clip_max = 255;
    wire result_valid;
    wire [31:0] result;
    int8_requantize #(.PIXELS(4)) quant (
        .clk(clk), .rst_n(rst_n), .valid(quant_valid), .sums(quant_sums),
        .bias(bias), .multiplier(multiplier), .shift(shift), .output_zero(output_zero),
        .add_mode(add_mode), .add_a(add_a), .add_b(add_b), .add_zero_a(add_zero_a),
        .add_zero_b(add_zero_b), .multiplier_b(multiplier_b),
        .clip_min(clip_min), .clip_max(clip_max), .result_valid(result_valid), .result(result)
    );
    integer expected [0:11];
    reg [31:0] quant_expected [0:511];
    integer pixel, channel, operation, step, a, w, received = 0, issued = 0;
    reg signed [63:0] numerator, rounded;
    reg [63:0] magnitude, quotient, remainder, denominator;
    function signed [63:0] round_even;
        input signed [63:0] value;
        input [5:0] bits;
        reg [63:0] absolute_value, whole, fraction, divisor;
        begin
            absolute_value = value < 0 ? -value : value;
            divisor = 64'd1 << bits;
            whole = absolute_value / divisor;
            fraction = absolute_value % divisor;
            if (bits != 0 && (fraction > divisor/2 || (fraction == divisor/2 && whole[0]))) whole = whole + 1;
            round_even = value < 0 ? -$signed(whole) : $signed(whole);
        end
    endfunction
    always @(negedge clk) if (rst_n && result_valid) begin
        if (received >= issued || result !== quant_expected[received])
            $fatal(1, "Requantization %0d mismatch %h/%h", received, result, quant_expected[received]);
        received = received + 1;
    end
    initial begin
        #12 rst_n = 1;
        for (operation = 0; operation < 10; operation = operation + 1) begin
            @(negedge clk); clear = 1;
            for (pixel = 0; pixel < 12; pixel = pixel + 1) expected[pixel] = 0;
            @(negedge clk); clear = 0; input_zero = $urandom;
            for (step = 0; step < 100; step = step + 1) begin
                valid = step % 7 != 1 || step == 99; last = step == 99;
                activations = $urandom; weights = $urandom;
                if (valid)
                    for (pixel = 0; pixel < 4; pixel = pixel + 1)
                        for (channel = 0; channel < 3; channel = channel + 1) begin
                            a = activations[pixel*8 +: 8]; w = $signed(weights[channel*8 +: 8]);
                            expected[channel*4+pixel] = expected[channel*4+pixel] + (a-input_zero)*w;
                        end
                @(negedge clk);
            end
            valid = 0; last = 0;
            wait(done); @(negedge clk);
            for (channel = 0; channel < 3; channel = channel + 1) begin
                read_channel = channel; #1;
                for (pixel = 0; pixel < 4; pixel = pixel + 1)
                    if ($signed(sums[pixel*32 +: 32]) !== expected[channel*4+pixel])
                        $fatal(1, "Signed MAC mismatch channel=%0d pixel=%0d", channel, pixel);
            end
        end
        for (operation = 0; operation < 400; operation = operation + 1) begin
            @(negedge clk);
            quant_valid = operation % 9 != 2;
            bias = $signed($urandom) % 1000000; multiplier = $urandom_range(1, 32'h7fffffff);
            add_mode = operation > 200 && operation % 2;
            multiplier_b = $urandom_range(1, 32'h3fffffff);
            add_a = $urandom; add_b = $urandom; add_zero_a = $urandom; add_zero_b = $urandom;
            shift = $urandom_range(0, 62); output_zero = $urandom;
            clip_min = $urandom_range(0, 30); clip_max = $urandom_range(180, 255);
            for (pixel = 0; pixel < 4; pixel = pixel + 1) begin
                a = $signed($urandom) % 10000000; quant_sums[pixel*32 +: 32] = a;
                numerator = (64'sd1*a + bias) * multiplier;
                if (add_mode) begin
                    a = add_a[pixel*8 +: 8]; w = add_b[pixel*8 +: 8];
                    numerator = (64'sd1*a-add_zero_a)*multiplier + (64'sd1*w-add_zero_b)*multiplier_b;
                end
                rounded = round_even(numerator, shift) + output_zero;
                if (rounded < $signed({1'b0, clip_min})) rounded = clip_min;
                if (rounded > $signed({1'b0, clip_max})) rounded = clip_max;
                quant_expected[issued][pixel*8 +: 8] = rounded[7:0];
            end
            if (quant_valid) issued = issued + 1;
        end
        @(negedge clk); quant_valid = 0;
        repeat (8) @(negedge clk);
        if (received != issued) $fatal(1, "Requantization dropped a vector");
        $display("PASS: signed U8/S8 outer-product MAC and %0d independently rounded pipelined requantization vectors", received);
        $finish;
    end
    initial begin #1000000; $fatal(1, "INT8 arithmetic timed out"); end
endmodule
