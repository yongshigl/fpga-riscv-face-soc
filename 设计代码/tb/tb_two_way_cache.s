`timescale 1ns/1ps

module tb_two_way_cache #(
    parameter integer FAST_RESPONSE = 0,
    parameter integer EARLY_RESTART = 0,
    parameter integer INGRESS_WRITE_REQUEST = 0,
    parameter integer FAST_WRITE_RESPONSE = 0,
    parameter integer REGISTER_TAG_LOOKUP = 0,
    parameter integer STATISTICS_ENABLE = 1
);
    reg clk = 0, rst_n = 0, invalidate = 0;
    reg req_valid = 0, req_write = 0, req_cacheable = 1, rsp_ready = 0;
    reg [31:0] req_addr = 0, req_wdata = 0;
    reg [3:0] req_wstrb = 0;
    wire req_ready, rsp_valid, rsp_error;
    wire [31:0] rsp_rdata, hits, misses;
    wire mem_req_valid, mem_req_write, mem_rsp_ready;
    wire [31:0] mem_req_addr, mem_req_wstrb;
    wire [255:0] mem_req_wdata;
    reg mem_req_ready = 0, mem_rsp_valid = 0, mem_rsp_error = 0;
    reg [255:0] mem_rsp_rdata = 0;
    two_way_cache #(.SET_BITS(2), .FAST_RESPONSE(FAST_RESPONSE),
        .EARLY_RESTART(EARLY_RESTART), .INGRESS_WRITE_REQUEST(INGRESS_WRITE_REQUEST),
        .FAST_WRITE_RESPONSE(FAST_WRITE_RESPONSE),
        .REGISTER_TAG_LOOKUP(REGISTER_TAG_LOOKUP), .STATISTICS_ENABLE(STATISTICS_ENABLE)) dut (
        .clk(clk), .rst_n(rst_n), .invalidate(invalidate),
        .req_valid(req_valid), .req_ready(req_ready), .req_addr(req_addr),
        .req_write(req_write), .req_wdata(req_wdata), .req_wstrb(req_wstrb), .req_cacheable(req_cacheable),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .mem_req_valid(mem_req_valid), .mem_req_ready(mem_req_ready), .mem_req_addr(mem_req_addr),
        .mem_req_write(mem_req_write), .mem_req_wdata(mem_req_wdata), .mem_req_wstrb(mem_req_wstrb),
        .mem_rsp_valid(mem_rsp_valid), .mem_rsp_ready(mem_rsp_ready),
        .mem_rsp_rdata(mem_rsp_rdata), .mem_rsp_error(mem_rsp_error), .hits(hits), .misses(misses)
    );
    always #5 clk = ~clk;
    reg [31:0] memory [0:1023];
    reg [31:0] reference [0:1023];
    reg pending = 0, pending_write, pending_error, next_error = 0;
    reg [31:0] pending_addr, pending_strobe;
    reg [255:0] pending_data;
    integer delay_cycles, transactions = 0, ingress_transactions = 0, i, b;
    integer lookup_store_acks = 0, lookup_store_faults = 0;
    reg held_request = 0;
    reg [320:0] held_packet;
    always @(posedge clk) begin
        if (rst_n && dut.lookup_store_response) begin
            lookup_store_acks <= lookup_store_acks + 1;
            if (mem_rsp_error) lookup_store_faults <= lookup_store_faults + 1;
        end
        if (rst_n && STATISTICS_ENABLE == 0 && {hits, misses} !== 64'd0)
            $fatal(1, "Disabled auxiliary cache counters did not remain zero");
        if (rst_n && held_request && (!mem_req_valid ||
            {mem_req_write, mem_req_addr, mem_req_wdata, mem_req_wstrb} !== held_packet))
            $fatal(1, "Backing request changed under backpressure");
        held_request <= rst_n && mem_req_valid && !mem_req_ready;
        held_packet <= {mem_req_write, mem_req_addr, mem_req_wdata, mem_req_wstrb};
        mem_req_ready <= rst_n && !pending && !mem_rsp_valid && ($urandom_range(0, 3) != 0);
        if (rst_n && mem_req_valid && mem_req_ready) begin
            if (dut.ingress_store_request) ingress_transactions <= ingress_transactions + 1;
            if (mem_req_addr[4:0] != 0) $fatal(1, "Unaligned backing line address");
            if (pending || mem_rsp_valid) $fatal(1, "Multiple outstanding cache transactions");
            pending <= 1;
            pending_addr <= mem_req_addr;
            pending_data <= mem_req_wdata;
            pending_strobe <= mem_req_wstrb;
            pending_write <= mem_req_write;
            pending_error <= next_error;
            delay_cycles <= $urandom_range(1, 17);
            transactions <= transactions + 1;
            mem_req_ready <= 0;
            // Some writes complete during LOOKUP, including injected errors.
            if (FAST_WRITE_RESPONSE != 0 && dut.ingress_store_request &&
                (next_error || ingress_transactions % 3 == 0)) begin
                pending <= 0;
                mem_rsp_valid <= 1;
                mem_rsp_error <= next_error;
                mem_rsp_rdata <= 0;
                for (b = 0; b < 32; b = b + 1)
                    if (!next_error && mem_req_wstrb[b])
                        memory[(mem_req_addr >> 2) + b/4][(b%4)*8 +: 8] <= mem_req_wdata[b*8 +: 8];
            end
        end
        if (pending) begin
            if (delay_cycles != 0) delay_cycles <= delay_cycles - 1;
            else begin
                pending <= 0;
                mem_rsp_valid <= 1;
                mem_rsp_error <= pending_error;
                for (b = 0; b < 32; b = b + 1) begin
                    mem_rsp_rdata[b*8 +: 8] <= memory[(pending_addr >> 2) + b/4][(b%4)*8 +: 8];
                    if (pending_write && !pending_error && pending_strobe[b])
                        memory[(pending_addr >> 2) + b/4][(b%4)*8 +: 8] <= pending_data[b*8 +: 8];
                end
            end
        end
        if (mem_rsp_valid && mem_rsp_ready) mem_rsp_valid <= 0;
    end

    task access;
        input [31:0] address;
        input write;
        input [31:0] value;
        input [3:0] mask;
        input cached;
        input error;
        reg [31:0] expected, held_data;
        reg held_error;
        integer cycles, byte_number;
        begin
            @(negedge clk);
            req_addr = address;
            req_write = write;
            req_wdata = value;
            req_wstrb = mask;
            req_cacheable = cached;
            next_error = error;
            expected = write ? 0 : reference[address >> 2];
            req_valid = 1;
            do @(posedge clk); while (!req_ready);
            @(negedge clk);
            req_valid = 0;
            cycles = 0;
            while (!rsp_valid && cycles < 100) begin
                @(negedge clk);
                cycles = cycles + 1;
            end
            if (!rsp_valid) $fatal(1, "Cache request timed out");
            if (rsp_error !== error || (!error && rsp_rdata !== expected))
                $fatal(1, "Cache response address=%h data=%h/%h error=%b/%b",
                    address, rsp_rdata, expected, rsp_error, error);
            held_data = rsp_rdata;
            held_error = rsp_error;
            repeat (3) begin
                @(negedge clk);
                if (!rsp_valid || rsp_rdata !== held_data || rsp_error !== held_error)
                    $fatal(1, "Cache response changed under backpressure");
            end
            if (write && !error)
                for (byte_number = 0; byte_number < 4; byte_number = byte_number + 1)
                    if (mask[byte_number])
                        reference[address >> 2][byte_number*8 +: 8] = value[byte_number*8 +: 8];
            rsp_ready = 1;
            @(negedge clk);
            rsp_ready = 0;
            next_error = 0;
        end
    endtask

    task flush;
        begin
            @(negedge clk); invalidate = 1;
            @(negedge clk); invalidate = 0;
        end
    endtask

    task hit_burst;
        integer sent, received, elapsed, entry;
        reg [31:0] burst_addresses [0:15];
        begin
            for (entry = 0; entry < 16; entry = entry + 1) begin
                burst_addresses[entry] = ((entry % 8) / 2) * 32 + (entry % 2) * 128 + (entry / 8) * 4;
                if (entry < 8) access(burst_addresses[entry], 0, 0, 0, 1, 0);
            end
            wait(req_ready);
            @(negedge clk);
            req_addr = burst_addresses[0]; req_write = 0; req_cacheable = 1;
            req_valid = 1; rsp_ready = 1;
            sent = 0; received = 0; elapsed = 0;
            while (received < 16 && elapsed < 22) begin
                @(posedge clk);
                elapsed = elapsed + 1;
                if (rsp_valid) begin
                    if (rsp_error || rsp_rdata !== reference[burst_addresses[received] >> 2])
                        $fatal(1, "Continuous cache hit data/order failed at word %0d", received);
                    received = received + 1;
                end
                if (req_valid && req_ready) sent = sent + 1;
                @(negedge clk);
                if (sent < 16) req_addr = burst_addresses[sent];
                req_valid = sent < 16;
            end
            req_valid = 0; rsp_ready = 0;
            if (received != 16 || elapsed != 17)
                $fatal(1, "Warm hits did not sustain one response per cycle: %0d in %0d cycles", received, elapsed);
        end
    endtask

    task invalidate_lookup;
        integer elapsed;
        begin
            access(0, 0, 0, 0, 1, 0);
            wait(req_ready);
            @(negedge clk);
            req_addr = 0; req_write = 0; req_cacheable = 1; req_valid = 1;
            do @(posedge clk); while (!req_ready);
            @(negedge clk);
            req_valid = 0;
            invalidate = 1;
            memory[0] = memory[0] ^ 32'hf05ac381;
            reference[0] = memory[0];
            #1;
            if (rsp_valid) $fatal(1, "Invalidation exposed the accepted stale lookup");
            @(negedge clk);
            invalidate = 0;
            elapsed = 0;
            while (!rsp_valid && elapsed < 100) begin
                @(negedge clk);
                elapsed = elapsed + 1;
            end
            if (!rsp_valid || rsp_error || rsp_rdata !== reference[0])
                $fatal(1, "Invalidated lookup did not return fresh backing data");
            rsp_ready = 1;
            @(negedge clk);
            rsp_ready = 0;
            wait(req_ready);
        end
    endtask

    integer before_transactions, operation;
    reg [31:0] random_addr, random_data;
    reg [3:0] random_mask;
    reg random_write, random_cached, random_error;
    initial begin
        for (i = 0; i < 1024; i = i + 1) begin
            memory[i] = (32'h7317a511 * (i + 1)) ^ 32'h9dbb29ff;
            reference[i] = memory[i];
        end
        #12 rst_n = 1;
        access(0, 0, 0, 0, 1, 0);
        access(128, 0, 0, 0, 1, 0);
        access(0, 0, 0, 0, 1, 0);
        access(256, 0, 0, 0, 1, 0);
        access(0, 0, 0, 0, 1, 0);
        access(128, 0, 0, 0, 1, 0);
        if (transactions != 4 || (STATISTICS_ENABLE != 0 && (hits != 2 || misses != 4)))
            $fatal(1, "Two-way LRU sequence failed");
        access(0, 1, 32'h1287aaff, 4'b0101, 1, 0);
        before_transactions = transactions;
        access(0, 0, 0, 0, 1, 0);
        if (transactions != before_transactions) $fatal(1, "Masked store hit was not retained");
        access(0, 1, 32'hdeadbeef, 4'hf, 1, 1);
        access(0, 0, 0, 0, 1, 0);
        if (transactions != before_transactions + 2) $fatal(1, "Failed store left a valid cached line");

        access(128, 0, 0, 0, 1, 0);
        access(2048, 0, 0, 0, 0, 1);
        before_transactions = transactions;
        access(128, 0, 0, 0, 1, 0);
        if (transactions != before_transactions)
            $fatal(1, "Backing error recovery did not preserve the unrelated cached line");

        before_transactions = transactions;
        access(1024, 1, 32'h6c3b5610, 4'hf, 1, 0);
        access(1024, 0, 0, 0, 1, 0);
        if (transactions != before_transactions + 2) $fatal(1, "Store miss unexpectedly allocated");
        before_transactions = transactions;
        access(2048, 0, 0, 0, 0, 0);
        access(2048, 0, 0, 0, 0, 0);
        if (transactions != before_transactions + 2) $fatal(1, "Uncached request hit the cache");

        flush();
        fork
            access(384, 0, 0, 0, 1, 0);
            begin
                wait(mem_req_valid && !mem_req_write);
                flush();
            end
        join
        before_transactions = transactions;
        access(384, 0, 0, 0, 1, 0);
        if (transactions != before_transactions + 1) $fatal(1, "Invalidated fill revalidated stale data");

        if (EARLY_RESTART != 0) begin
            flush();
            access(2880, 0, 0, 0, 1, 0);
            if (dut.state != dut.FILL) $fatal(1, "Early restart did not return before line fill completion");
            flush();
            wait(req_ready);
            before_transactions = transactions;
            access(2880, 0, 0, 0, 1, 0);
            if (transactions != before_transactions + 1)
                $fatal(1, "Invalidation after early response revalidated the filling line");
        end

        if (FAST_WRITE_RESPONSE != 0 && INGRESS_WRITE_REQUEST != 0) begin
            wait(req_ready && !pending && !mem_rsp_valid);
            force mem_req_ready = 1'b1;
            access(0, 1, 32'hbad12345, 4'hf, 1, 1);
            release mem_req_ready;
            access(0, 0, 0, 0, 1, 0);
        end
        for (operation = 0; operation < 300; operation = operation + 1) begin
            random_addr = $urandom_range(0, 1023) * 4;
            random_data = $urandom;
            random_mask = $urandom_range(1, 15);
            random_write = $urandom_range(0, 3) == 0;
            random_cached = $urandom_range(0, 4) != 0;
            random_error = $urandom_range(0, 24) == 0;
            if (random_error || operation % 23 == 0) flush();
            access(random_addr, random_write, random_data, random_mask, random_cached, random_error);
        end
        for (i = 0; i < 1024; i = i + 1)
            if (memory[i] !== reference[i]) $fatal(1, "Backing memory mismatch at word %0d", i);
        invalidate_lookup();
        hit_burst();
        if ((INGRESS_WRITE_REQUEST != 0) != (ingress_transactions != 0))
            $fatal(1, "Ingress write coverage did not match the enabled policy");
        if (FAST_WRITE_RESPONSE != 0 && INGRESS_WRITE_REQUEST != 0 &&
            (lookup_store_acks == 0 || lookup_store_faults == 0))
            $fatal(1, "Missing LOOKUP write/error response coverage");
        $display("PASS: two-way cache LRU, masked stores, no allocation, bypass, precise errors, error-to-hit recovery, lookup/fill invalidation, 300 stalled transactions, stable requests and 16 cross-set/way hits in 17 cycles (fast=%0d early=%0d ingress=%0d tags=%0d stats=%0d accepted=%0d fast_write=%0d lookup_acks=%0d lookup_errors=%0d)", FAST_RESPONSE, EARLY_RESTART, INGRESS_WRITE_REQUEST, REGISTER_TAG_LOOKUP, STATISTICS_ENABLE, ingress_transactions, FAST_WRITE_RESPONSE, lookup_store_acks, lookup_store_faults);
        $finish;
    end
    initial begin
        #1000000;
        $fatal(1, "Cache test timed out");
    end
endmodule
