`timescale 1ns/1ps

module tb_machine_csr;
    reg clk = 0, rst_n = 0, irq_timer = 0, irq_external = 0;
    always #5 clk = !clk;
    reg [31:0] instructions [0:1023];
    wire [31:0] instruction_address;
    wire memory_valid, memory_write, trap;
    wire [31:0] memory_address, memory_data;
    wire [3:0] memory_strobe;
    integer index, illegal_traps = 0, ecall_traps = 0, irq_markers = 0;

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
        .irq_timer(irq_timer), .irq_external(irq_external), .trap_taken(trap)
    );

    always @(posedge clk) begin
        if (rst_n && memory_valid && memory_write) begin
            if (memory_strobe != 4'hf) $fatal(1, "Unexpected masked CSR test store");
            if (memory_address == 32'h180) begin
                irq_timer <= memory_data[0];
                irq_external <= memory_data[0];
                irq_markers = irq_markers + 1;
            end
            if (memory_address == 32'h100) begin
                if (memory_data != 32'h600d || illegal_traps != 8 || ecall_traps != 1 || irq_markers != 2)
                    $fatal(1, "Machine CSR program failed: result=%h illegal=%0d ECALL=%0d markers=%0d PC=%h",
                           memory_data, illegal_traps, ecall_traps, irq_markers, instruction_address);
                $display("PASS: machine-only mstatus/MRET, mie/mip masks, mtvec WARL, aligned mepc, fixed RV32I misa and read-only machine IDs");
                $finish;
            end
        end
        #1;
        if (rst_n && trap) begin
            case (dut.csr_mcause)
                32'd2: illegal_traps = illegal_traps + 1;
                32'd11: ecall_traps = ecall_traps + 1;
                default: $fatal(1, "Unexpected machine CSR trap cause %h", dut.csr_mcause);
            endcase
        end
    end

    initial begin
        for (index = 0; index < 1024; index = index + 1) instructions[index] = 32'h13000000;
        $readmemh("build/machine_csr_test.hex", instructions);
        #12 rst_n = 1;
        #100000;
        $fatal(1, "Machine CSR test timed out at PC %h", instruction_address);
    end
endmodule
