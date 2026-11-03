`timescale 1ns/1ps

module tb_memory_line_backend #(
    parameter integer FAST_WRITE_RESPONSE = 0
);
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg req_valid = 0, req_write = 0, rsp_ready = 0;
    reg [31:0] req_addr = 0, req_wstrb = 0;
    reg [255:0] req_wdata = 0;
    wire req_ready, rsp_valid, rsp_error;
    wire [255:0] rsp_rdata;
    wire external_valid, external_write, external_rsp_ready;
    wire [31:0] external_addr, external_mask;
    wire [255:0] external_data;
    reg external_ready = 0, external_rsp_valid = 0, external_error = 0;
    reg [255:0] external_response = 0;
    memory_line_backend #(.ROM_WORDS(64), .RAM_WORDS(64),
        .FAST_WRITE_RESPONSE(FAST_WRITE_RESPONSE)) dut (
        .clk(clk), .rst_n(rst_n), .req_valid(req_valid), .req_ready(req_ready),
        .req_write(req_write), .req_addr(req_addr), .req_wdata(req_wdata), .req_wstrb(req_wstrb),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .external_req_valid(external_valid), .external_req_ready(external_ready),
        .external_req_write(external_write), .external_req_addr(external_addr),
        .external_req_wdata(external_data), .external_req_wstrb(external_mask),
        .external_rsp_valid(external_rsp_valid), .external_rsp_ready(external_rsp_ready),
        .external_rsp_rdata(external_response), .external_rsp_error(external_error)
    );
    integer latency, byte_number, operation, first;
    reg [255:0] reference_data [0:7];
    reg [255:0] data, held;
    reg [31:0] mask;
    task access;
        input [31:0] address;
        input write;
        input [255:0] value;
        input [31:0] strobes;
        input [255:0] expected;
        input error;
        begin
            @(negedge clk);
            req_valid = 1; req_addr = address; req_write = write;
            req_wdata = value; req_wstrb = strobes;
            do @(posedge clk); while (!req_ready);
            @(negedge clk); req_valid = 0;
            latency = 0;
            while (!rsp_valid && latency < 30) begin @(negedge clk); latency = latency + 1; end
            if (!rsp_valid || rsp_error !== error || (!error && !write && rsp_rdata !== expected))
                $fatal(1, "Backend response mismatch at %h", address);
            held = rsp_rdata;
            repeat (3) begin
                @(negedge clk);
                if (!rsp_valid || rsp_rdata !== held) $fatal(1, "Backend response backpressure failed");
            end
            rsp_ready = 1;
            @(negedge clk); rsp_ready = 0;
        end
    endtask
    task write_ready;
        input [255:0] value, expected;
        input [31:0] strobes;
        integer word_number;
        begin
            @(negedge clk);
            req_valid = 1; req_addr = 32'h10000000; req_write = 1;
            req_wdata = value; req_wstrb = strobes; rsp_ready = 1;
            do @(posedge clk); while (!req_ready);
            @(negedge clk); req_valid = 0;
            do @(posedge clk); while (!rsp_valid);
            if (rsp_error) $fatal(1, "Ready RAM store reported an error");
            #1;
            for (word_number = 0; word_number < 8; word_number = word_number + 1)
                if (dut.data_mem[word_number] !== expected[word_number*32 +: 32])
                    $fatal(1, "Store acknowledged before masked RAM commit, word=%0d", word_number);
            @(negedge clk); rsp_ready = 0;
        end
    endtask
    initial begin
        for (first = 0; first < 8; first = first + 1) reference_data[first] = 0;
        #12 rst_n = 1;
        access(0, 0, 0, 0, {8{32'h00000013}}, 0);
        access(0, 1, 0, 32'hffffffff, 0, 1);
        access(32'h10000004, 0, 0, 0, 0, 1);
        access(32'h10000100, 0, 0, 0, 0, 1);
        for (first = 0; first < 8; first = first + 1) begin
            data = 0; data[first*32 +: 32] = 32'habc01234 ^ first;
            mask = 32'hf << (first * 4);
            reference_data[0][first*32 +: 32] = data[first*32 +: 32];
            access(32'h10000000, 1, data, mask, 0, 0);
            if (latency != (FAST_WRITE_RESPONSE != 0 ? 0 : 1))
                $fatal(1, "Single-word write took %0d cycles", latency);
            access(32'h10000000, 0, 0, 0, reference_data[0], 0);
        end
        for (operation = 0; operation < 120; operation = operation + 1) begin
            first = $urandom_range(0, 7); mask = $urandom;
            for (byte_number = 0; byte_number < 32; byte_number = byte_number + 1) begin
                data[byte_number*8 +: 8] = $urandom;
                if (mask[byte_number]) reference_data[first][byte_number*8 +: 8] = data[byte_number*8 +: 8];
            end
            access(32'h10000000 + first * 32, 1, data, mask, 0, 0);
            access(32'h10000000 + first * 32, 0, 0, 0, reference_data[first], 0);
        end
        access(32'h10000000, 1, 0, 0, 0, 0);
        access(32'h10000000, 0, 0, 0, reference_data[0], 0);
        for (first = 0; first < 8; first = first + 1) begin
            data = 0; data[first*32 +: 32] = 32'hdead1234 ^ first;
            mask = 32'hf << (first * 4);
            reference_data[0][first*32 +: 32] = data[first*32 +: 32];
            write_ready(data, reference_data[0], mask);
        end
        access(32'h10000000, 0, 0, 0, reference_data[0], 0);
        fork
            access(32'h80000120, 1, {8{32'hc00ffee1}}, 32'h12345678, 0, 1);
            begin
                wait(external_valid);
                if (external_addr !== 32'h80000120 || !external_write ||
                    external_mask !== 32'h12345678 || external_data !== {8{32'hc00ffee1}})
                    $fatal(1, "External forwarding mismatch");
                repeat (4) @(negedge clk);
                external_ready = 1;
                @(negedge clk); external_ready = 0;
                repeat (5) @(negedge clk);
                external_error = 1; external_rsp_valid = 1;
                wait(external_rsp_ready);
                @(negedge clk); external_rsp_valid = 0;
            end
        join
        $display("PASS: synchronous line backend single-word latency, sparse byte masks, commit-before-ack, ROM protection, bounds and stalled external errors (fast_write=%0d)", FAST_WRITE_RESPONSE);
        $finish;
    end
    initial begin #1000000; $fatal(1, "Memory line backend timed out"); end
endmodule
