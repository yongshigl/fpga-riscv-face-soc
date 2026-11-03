`timescale 1ns/1ps

module tb_exception_matrix;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    wire [31:0] imem_addr;
    wire [31:0] dmem_addr;
    wire [31:0] dmem_wdata;
    wire [3:0] dmem_wstrb;
    wire dmem_valid;
    wire dmem_we;
    wire trap_taken;
    wire [63:0] cycle_count;
    reg [31:0] instruction_mem [0:1023];
    reg [31:0] data_mem [0:255];
    integer trap_count = 0;
    integer index;

    function [31:0] swap_bytes;
        input [31:0] value;
        begin
            swap_bytes = {value[7:0], value[15:8], value[23:16], value[31:24]};
        end
    endfunction

    rv32i_pipeline_core dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(), .imem_addr(imem_addr),
        .imem_rdata(swap_bytes(instruction_mem[imem_addr[11:2]])),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(data_mem[dmem_addr[9:2]]),
        .irq_timer(1'b0), .irq_external(1'b0), .current_pc(), .debug_x10(),
        .trap_taken(trap_taken), .cycle_count(cycle_count), .instret_count()
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (trap_taken)
            trap_count <= trap_count + 1;
        if (dmem_valid && dmem_we && dmem_addr == 32'h00000100) begin
            if (dmem_wdata !== 32'h00005afe || trap_count != 6) begin
                $display("FAIL: exception matrix result=%h traps=%0d", dmem_wdata, trap_count);
                $fatal(1);
            end
            $display("PASS: exception matrix traps=%0d cycles=%0d", trap_count, cycle_count);
            $finish;
        end
    end

    initial begin
        $readmemh("build/exception_matrix.hex", instruction_mem);
        for (index = 0; index < 256; index = index + 1)
            data_mem[index] = 32'd0;
        $dumpfile("build/exception_matrix.vcd");
        $dumpvars(0, tb_exception_matrix);
        #12 rst_n = 1'b1;
        #30000;
        $display("FAIL: exception matrix timeout traps=%0d", trap_count);
        $fatal(1);
    end
endmodule
