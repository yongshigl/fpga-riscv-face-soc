`timescale 1ns/1ps

module tb_transaction_cdc;
    reg source_clk = 0, target_clk = 0, source_rst_n = 0, target_rst_n = 0;
    always #5 source_clk = ~source_clk;
    always #7 target_clk = ~target_clk;
    reg req_valid = 0, req_write = 0, rsp_ready = 0;
    reg [31:0] req_addr = 0;
    reg [255:0] req_data = 0;
    reg [31:0] req_mask = 0;
    wire req_ready, rsp_valid, rsp_error;
    wire [255:0] rsp_data;
    wire target_valid, target_write, target_response_ready;
    wire [31:0] target_addr, target_mask;
    wire [255:0] target_data;
    reg target_ready = 0, target_response_valid = 0, target_error = 0;
    reg [255:0] target_response_data = 0;
    transaction_cdc dut (
        .source_clk(source_clk), .source_rst_n(source_rst_n),
        .source_req_valid(req_valid), .source_req_ready(req_ready), .source_req_write(req_write),
        .source_req_addr(req_addr), .source_req_wdata(req_data), .source_req_wstrb(req_mask),
        .source_rsp_valid(rsp_valid), .source_rsp_ready(rsp_ready), .source_rsp_rdata(rsp_data), .source_rsp_error(rsp_error),
        .target_clk(target_clk), .target_rst_n(target_rst_n),
        .target_req_valid(target_valid), .target_req_ready(target_ready), .target_req_write(target_write),
        .target_req_addr(target_addr), .target_req_wdata(target_data), .target_req_wstrb(target_mask),
        .target_rsp_valid(target_response_valid), .target_rsp_ready(target_response_ready),
        .target_rsp_rdata(target_response_data), .target_rsp_error(target_error)
    );
    integer requests = 0, responses = 0, operation, delay_cycles;
    reg pending = 0;
    reg [31:0] expected_addr, expected_mask;
    reg [255:0] expected_data;
    reg expected_write, expected_error;
    always @(posedge target_clk) begin
        if (!source_rst_n || !target_rst_n) begin
            pending <= 0; target_ready <= 0; target_response_valid <= 0;
        end else begin
            target_ready <= !pending && !target_response_valid && $urandom_range(0, 2) != 0;
            if (target_valid && target_ready) begin
                if (pending || target_response_valid || target_addr !== expected_addr ||
                    target_mask !== expected_mask || target_data !== expected_data || target_write !== expected_write)
                    $fatal(1, "CDC changed or duplicated request payload");
                pending <= 1; delay_cycles <= $urandom_range(1, 20); requests <= requests + 1;
                target_ready <= 0;
            end
            if (pending) begin
                if (delay_cycles > 0) delay_cycles <= delay_cycles - 1;
                else begin
                    pending <= 0; target_response_valid <= 1;
                    target_response_data <= expected_data ^ {8{expected_addr}};
                    target_error <= expected_error;
                end
            end
            if (target_response_valid && target_response_ready) begin
                target_response_valid <= 0; responses <= responses + 1;
            end
        end
    end
    task access;
        input integer number;
        reg [255:0] held;
        begin
            @(negedge source_clk);
            expected_addr = $urandom; expected_mask = $urandom;
            expected_data = {$urandom, $urandom, $urandom, $urandom, $urandom, $urandom, $urandom, $urandom};
            expected_write = number % 3 == 0; expected_error = number % 11 == 0;
            req_addr = expected_addr; req_mask = expected_mask; req_data = expected_data;
            req_write = expected_write; req_valid = 1;
            do @(posedge source_clk); while (!req_ready);
            @(negedge source_clk); req_valid = 0;
            req_addr = $urandom; req_data = ~expected_data; req_mask = ~expected_mask;
            wait(rsp_valid); @(negedge source_clk);
            if (rsp_data !== (expected_data ^ {8{expected_addr}}) || rsp_error !== expected_error)
                $fatal(1, "CDC changed response payload");
            held = rsp_data;
            repeat (5) begin
                @(negedge source_clk);
                if (!rsp_valid || rsp_data !== held || req_ready) $fatal(1, "CDC response backpressure failed");
            end
            rsp_ready = 1;
            @(negedge source_clk); rsp_ready = 0;
        end
    endtask
    initial begin
        #13 source_rst_n = 1; #16 target_rst_n = 1;
        for (operation = 0; operation < 150; operation = operation + 1) access(operation);
        if (requests != 150 || responses != 150) $fatal(1, "CDC request count mismatch");
        @(negedge source_clk);
        expected_addr = 123; expected_data = 456; expected_mask = 789; expected_write = 0;
        req_addr = expected_addr; req_data = expected_data; req_mask = expected_mask; req_write = 0; req_valid = 1;
        do @(posedge source_clk); while (!req_ready);
        @(negedge source_clk); req_valid = 0;
        wait(target_valid);
        #2 target_rst_n = 0;
        #31 target_rst_n = 1;
        repeat (12) @(negedge source_clk);
        if (rsp_valid || !req_ready) $fatal(1, "One-sided reset did not cancel the old transaction");
        access(151);
        #2 source_rst_n = 0; #19 source_rst_n = 1;
        access(152);
        $display("PASS: bundled-data CDC with unequal clocks, 150 delayed requests, response backpressure and either reset");
        $finish;
    end
    initial begin #1000000; $fatal(1, "CDC test timed out"); end
endmodule
