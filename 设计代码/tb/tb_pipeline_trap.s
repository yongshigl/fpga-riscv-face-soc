`timescale 1ns/1ps

module tb_pipeline_trap;
    reg clk = 1'b0;
    reg rst_n = 1'b0;
    reg irq_timer = 1'b0;
    wire [31:0] imem_addr;
    wire [31:0] imem_rdata;
    wire dmem_valid;
    wire dmem_we;
    wire [31:0] dmem_addr;
    wire [31:0] dmem_wdata;
    wire [3:0] dmem_wstrb;
    wire [31:0] current_pc;
    wire trap_taken;
    wire [63:0] cycle_count;
    wire [63:0] instret_count;
    reg [31:0] instruction_mem [0:1023];
    reg [31:0] data_mem [0:255];
    integer index;
    integer trap_count = 0;

    function [31:0] swap_bytes;
        input [31:0] value;
        begin
            swap_bytes = {value[7:0], value[15:8], value[23:16], value[31:24]};
        end
    endfunction

    assign imem_rdata = swap_bytes(instruction_mem[imem_addr[11:2]]);

    rv32i_pipeline_core dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(), .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(data_mem[dmem_addr[9:2]]),
        .irq_timer(irq_timer), .irq_external(1'b0), .current_pc(current_pc), .debug_x10(),
        .trap_taken(trap_taken), .cycle_count(cycle_count), .instret_count(instret_count)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (trap_taken)
            trap_count <= trap_count + 1;
        if (cycle_count == 64'd45)
            irq_timer <= 1'b1;
        if (trap_count >= 2)
            irq_timer <= 1'b0;
        if (dmem_valid && dmem_we && dmem_addr[31:10] == 0) begin
            if (dmem_wstrb[0]) data_mem[dmem_addr[9:2]][7:0] <= dmem_wdata[7:0];
            if (dmem_wstrb[1]) data_mem[dmem_addr[9:2]][15:8] <= dmem_wdata[15:8];
            if (dmem_wstrb[2]) data_mem[dmem_addr[9:2]][23:16] <= dmem_wdata[23:16];
            if (dmem_wstrb[3]) data_mem[dmem_addr[9:2]][31:24] <= dmem_wdata[31:24];
        end
        if (dmem_valid && dmem_we && dmem_addr == 32'h00000100) begin
            if (dmem_wdata !== 32'h0000600d || trap_count < 2) begin
                $display("FAIL: trap test result=%h traps=%0d pc=%h", dmem_wdata, trap_count, current_pc);
                $fatal(1);
            end
            $display("PASS: exception/interrupt result=%h traps=%0d cycles=%0d instret=%0d", dmem_wdata, trap_count, cycle_count, instret_count);
            $finish;
        end
    end

    initial begin
        $readmemh("build/trap_test.hex", instruction_mem);
        for (index = 0; index < 256; index = index + 1)
            data_mem[index] = 32'd0;
        $dumpfile("build/pipeline_trap.vcd");
        $dumpvars(0, tb_pipeline_trap);
        #12 rst_n = 1'b1;
        #30000;
        $display("FAIL: trap test timeout pc=%h traps=%0d", current_pc, trap_count);
        $fatal(1);
    end
endmodule
