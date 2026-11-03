`timescale 1ns/1ps

module tb_ras;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] instructions [0:1023];
    reg [31:0] memory [0:255];
    wire [31:0] imem_addr, dmem_addr, dmem_wdata, debug_x10;
    wire [3:0] dmem_wstrb;
    wire dmem_valid, dmem_we;
    wire [31:0] imem_rdata = {instructions[imem_addr[11:2]][7:0],
        instructions[imem_addr[11:2]][15:8], instructions[imem_addr[11:2]][23:16],
        instructions[imem_addr[11:2]][31:24]};
    wire [31:0] dmem_rdata = memory[dmem_addr[9:2]];

    rv32i_pipeline_core #(.RAS_BITS(3), .CONTROL_LOAD_GUARD(1)) dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(), .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_wdata(dmem_wdata), .dmem_wstrb(dmem_wstrb), .dmem_rdata(dmem_rdata),
        .irq_timer(1'b0), .irq_external(1'b0), .current_pc(), .debug_x10(debug_x10),
        .trap_taken(), .cycle_count(), .instret_count()
    );
    always #5 clk = ~clk;
    integer i, byte_index;
    always @(posedge clk) if (rst_n && dmem_valid && dmem_we) begin
        for (byte_index = 0; byte_index < 4; byte_index = byte_index + 1)
            if (dmem_wstrb[byte_index])
                memory[dmem_addr[9:2]][byte_index*8 +: 8] <= dmem_wdata[byte_index*8 +: 8];
    end

    reg [31:0] reference_stack [0:7];
    integer count = 0, pointer = 0;
    integer overflows = 0, underflows = 0, exchanges = 0, same_links = 0, traps = 0;
    reg push, pop, jump, link_rd, link_rs1;
    reg [31:0] instruction;
    always @(posedge clk) if (rst_n) begin
        instruction = dut.idex_instr;
        jump = instruction[6:0] == 7'h6f || instruction[6:0] == 7'h67;
        link_rd = instruction[11:7] == 1 || instruction[11:7] == 5;
        link_rs1 = instruction[19:15] == 1 || instruction[19:15] == 5;
        push = jump && link_rd;
        pop = instruction[6:0] == 7'h67 && link_rs1 &&
              (!link_rd || instruction[11:7] != instruction[19:15]);
        if (dut.take_trap || (dut.idex_valid && instruction == 32'h30200073)) begin
            count = 0;
            pointer = 0;
            if (dut.take_trap) traps = traps + 1;
        end else if (dut.idex_valid && jump && !dut.ex_exception) begin
            if (pop && count == 0) underflows = underflows + 1;
            if (push && pop) exchanges = exchanges + 1;
            if (instruction[6:0] == 7'h67 && push && link_rs1 && !pop)
                same_links = same_links + 1;
            if (push && pop && count > 0) begin
                reference_stack[(pointer + 7) % 8] = dut.idex_pc + 4;
            end else if (push) begin
                reference_stack[pointer] = dut.idex_pc + 4;
                pointer = (pointer + 1) % 8;
                if (count < 8) count = count + 1;
                else overflows = overflows + 1;
            end else if (pop && count > 0) begin
                pointer = (pointer + 7) % 8;
                count = count - 1;
            end
        end
        #1;
        if (dut.ras_count !== count || dut.ras_pointer !== pointer)
            $fatal(1, "RAS state mismatch: count=%0d/%0d pointer=%0d/%0d",
                dut.ras_count, count, dut.ras_pointer, pointer);
        for (i = 0; i < count; i = i + 1)
            if (dut.ras_data[(pointer + 7 - i) % 8] !== reference_stack[(pointer + 7 - i) % 8])
                $fatal(1, "RAS return address mismatch");
    end

    initial begin
        for (i = 0; i < 1024; i = i + 1) instructions[i] = 32'h13000000;
        for (i = 0; i < 256; i = i + 1) memory[i] = 0;
        $readmemh("build/ras_test.hex", instructions);
        #12 rst_n = 1;
        repeat (2000) begin
            @(negedge clk);
            if (debug_x10 == 32'hbad) $fatal(1, "RAS architectural result failed");
            if (debug_x10 == 32'h600d) begin
                if (overflows < 5 || underflows < 5 || exchanges < 2 || same_links < 1 || traps != 1)
                    $fatal(1, "Incomplete RAS coverage: overflow=%0d underflow=%0d exchanges=%0d same=%0d traps=%0d",
                        overflows, underflows, exchanges, same_links, traps);
                $display("PASS: RAS recursion overflow=%0d underflow=%0d coroutine=%0d same-link=%0d wrong-path and trap",
                    overflows, underflows, exchanges, same_links);
                $finish;
            end
        end
        $fatal(1, "RAS test timed out");
    end
endmodule
