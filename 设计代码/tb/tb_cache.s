`timescale 1ns/1ps

module tb_cache;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg [31:0] fetch_addr = 32'd0;
    reg fetch_valid = 1'b0;
    reg [31:0] data_addr = 32'h10000000;
    reg data_valid = 1'b0;
    reg data_write = 1'b0;
    reg [31:0] store_data = 32'd0;
    reg [3:0] store_strobe = 4'd0;
    integer misses_i = 0;
    integer hits_i = 0;
    integer misses_d = 0;
    integer hits_d = 0;

    rv32i_pipeline_soc #(
        .IMEM_WORDS(256), .DMEM_WORDS(256),
        .IMEM_INIT_FILE("build/pipeline_soc_test.hex"),
        .CACHE_ENABLE(1), .CLOCK_HZ(400), .UART_BAUD(100)
    ) dut (
        .clk(clk), .rst_n(rst_n), .external_irq(1'b0),
        .gpio_out(), .uart_tx_pin(), .uart_debug_data(),
        .uart_debug_strobe(), .current_pc(), .trap_taken(),
        .cycle_count(), .instret_count()
    );

    always #5 clk = ~clk;

    task check_fetch;
        input [31:0] address;
        input [31:0] expected;
        input expected_hit;
        begin
            @(negedge clk);
            fetch_addr = address;
            fetch_valid = 1'b1;
            #1;
            if (dut.imem_rdata !== expected || dut.ic_hit !== expected_hit)
                $fatal(1, "I-cache addr=%h data=%h hit=%b expected=%h/%b",
                       address, dut.imem_rdata, dut.ic_hit, expected, expected_hit);
            if (expected_hit) hits_i = hits_i + 1;
            else misses_i = misses_i + 1;
            @(posedge clk);
            #1;
        end
    endtask

    task check_data;
        input [31:0] address;
        input [31:0] expected;
        input expected_hit;
        begin
            @(negedge clk);
            data_addr = address;
            data_valid = 1'b1;
            data_write = 1'b0;
            #1;
            if (dut.dmem_rdata !== expected || dut.dc_hit !== expected_hit)
                $fatal(1, "D-cache addr=%h data=%h hit=%b expected=%h/%b",
                       address, dut.dmem_rdata, dut.dc_hit, expected, expected_hit);
            if (expected_hit) hits_d = hits_d + 1;
            else misses_d = misses_d + 1;
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        force dut.imem_valid = fetch_valid;
        force dut.imem_addr = fetch_addr;
        force dut.dmem_valid = data_valid;
        force dut.dmem_we = data_write;
        force dut.dmem_addr = data_addr;
        force dut.dmem_wdata = store_data;
        force dut.dmem_wstrb = store_strobe;

        #1;
        dut.instruction_mem[0] = 32'h11111111;
        dut.instruction_mem[32] = 32'h22222222;
        dut.instruction_mem[64] = 32'h33333333;
        dut.data_mem[0] = 32'h11223344;
        dut.data_mem[32] = 32'h55667788;
        dut.data_mem[64] = 32'h99aabbcc;
        #11 rst_n = 1'b1;

        check_fetch(32'h00000000, 32'h11111111, 1'b0);
        check_fetch(32'h00000080, 32'h22222222, 1'b0);
        check_fetch(32'h00000000, 32'h11111111, 1'b1);
        check_fetch(32'h00000100, 32'h33333333, 1'b0);
        check_fetch(32'h00000000, 32'h11111111, 1'b1);
        check_fetch(32'h00000080, 32'h22222222, 1'b0);

        check_data(32'h10000000, 32'h11223344, 1'b0);
        check_data(32'h10000080, 32'h55667788, 1'b0);
        check_data(32'h10000000, 32'h11223344, 1'b1);
        @(negedge clk);
        data_addr = 32'h10000000;
        data_write = 1'b1;
        store_data = 32'h0000aa00;
        store_strobe = 4'b0010;
        @(posedge clk);
        #1;
        data_write = 1'b0;
        check_data(32'h10000000, 32'h1122aa44, 1'b1);
        if (dut.data_mem[0] !== 32'h1122aa44)
            $fatal(1, "write-through backing RAM mismatch");
        check_data(32'h10000100, 32'h99aabbcc, 1'b0);
        check_data(32'h10000000, 32'h1122aa44, 1'b1);
        check_data(32'h10000080, 32'h55667788, 1'b0);

        check_data(32'h20000000, 32'd0, 1'b0);
        @(negedge clk);
        data_addr = 32'h20000000;
        data_write = 1'b1;
        store_data = 32'h00001234;
        store_strobe = 4'b1111;
        @(posedge clk);
        #1;
        data_write = 1'b0;
        if (dut.gpio_out !== 32'h00001234)
            $fatal(1, "MMIO bypass write failed");

        $display("PASS: two-way I/D cache I=%0d hits/%0d misses D=%0d hits/%0d misses",
                 hits_i, misses_i, hits_d, misses_d);
        $finish;
    end
endmodule
