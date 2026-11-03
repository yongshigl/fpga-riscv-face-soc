`timescale 1ns/1ps

module tb_csr_write_intent;
    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;
    reg [31:0] instructions [0:1023];
    wire [31:0] instruction_address;
    wire memory_valid, memory_write, trap;
    wire [31:0] memory_address, memory_data;
    wire [3:0] memory_strobe;
    wire [63:0] cycles;
    integer index, traps = 0, older_stores = 0;

    function [31:0] swap_bytes;
        input [31:0] value;
        begin swap_bytes = {value[7:0], value[15:8], value[23:16], value[31:24]}; end
    endfunction

    rv32i_pipeline_core dut (
        .clk(clk), .rst_n(rst_n), .imem_addr(instruction_address),
        .imem_rdata(swap_bytes(instructions[instruction_address[11:2]])),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_valid(memory_valid), .dmem_we(memory_write),
        .dmem_addr(memory_address), .dmem_wdata(memory_data), .dmem_wstrb(memory_strobe),
        .dmem_rdata(swap_bytes(instructions[memory_address[11:2]])),
        .dmem_ready(1'b1), .dmem_error(1'b0),
        .irq_timer(1'b0), .irq_external(1'b0), .trap_taken(trap), .cycle_count(cycles)
    );

    always @(posedge clk) begin
        if (rst_n && memory_valid && memory_write) begin
            if (memory_strobe != 4'hf) $fatal(1, "Unexpected masked test store");
            if (memory_address == 32'h120)
                $fatal(1, "Younger store committed past a faulting CSR at cycle %0d", cycles);
            if (memory_address == 32'h124) begin
                if (memory_data != older_stores)
                    $fatal(1, "Older store duplicated/lost: value %0d expected %0d", memory_data, older_stores);
                older_stores = older_stores + 1;
            end
            if (memory_address == 32'h100) begin
                if (memory_data != 32'h600d || traps != 12 || older_stores != 12)
                    $fatal(1, "CSR program failed: result=%h traps=%0d older_stores=%0d PC=%h",
                           memory_data, traps, older_stores, instruction_address);
                $display("PASS: CSR register write intent with zero-valued non-x0, read-only aliases, immediate/x0 reads, writable zero writes and 12 precise illegal traps");
                $finish;
            end
        end
        #1;
        if (rst_n && trap) begin
            traps = traps + 1;
            if (dut.csr_mcause !== 32'd2 || older_stores != traps)
                $fatal(1, "Incorrect CSR trap priority/cause: cause=%h stores=%0d traps=%0d",
                       dut.csr_mcause, older_stores, traps);
        end
    end

    initial begin
        for (index = 0; index < 1024; index = index + 1) instructions[index] = 32'h13000000;
        $readmemh("build/csr_write_intent_test.hex", instructions);
        #12 rst_n = 1;
        #100000;
        $fatal(1, "CSR write-intent test timed out at PC %h", instruction_address);
    end
endmodule
