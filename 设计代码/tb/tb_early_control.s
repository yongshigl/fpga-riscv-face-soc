`timescale 1ns/1ps

module tb_early_control;
    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;
    reg [31:0] program_words [0:4095];
    wire [31:0] address0, address1, result0, result1, pc0, pc1;
    wire trap0, trap1;
    wire [63:0] cycles0, cycles1, retired0, retired1;
    rv32i_pipeline_core #(.EARLY_BRANCH_CHECK(1)) optimized (
        .clk(clk), .rst_n(rst_n), .imem_addr(address0),
        .imem_rdata(program_words[address0[13:2]]), .imem_req_ready(1'b1),
        .imem_ready(1'b1), .imem_error(1'b0), .dmem_rdata(32'd0),
        .dmem_ready(1'b1), .dmem_error(1'b0), .irq_timer(1'b0), .irq_external(1'b0),
        .current_pc(pc0), .debug_x10(result0), .trap_taken(trap0),
        .cycle_count(cycles0), .instret_count(retired0)
    );
    rv32i_pipeline_core #(.EARLY_BRANCH_CHECK(0)) reference_core (
        .clk(clk), .rst_n(rst_n), .imem_addr(address1),
        .imem_rdata(program_words[address1[13:2]]), .imem_req_ready(1'b1),
        .imem_ready(1'b1), .imem_error(1'b0), .dmem_rdata(32'd0),
        .dmem_ready(1'b1), .dmem_error(1'b0), .irq_timer(1'b0), .irq_external(1'b0),
        .current_pc(pc1), .debug_x10(result1), .trap_taken(trap1),
        .cycle_count(cycles1), .instret_count(retired1)
    );
    integer cursor = 0, index, immediate, target, expected = 0, expected_traps = 0;
    integer count = 0, traps = 0;
    reg [31:0] seed = 32'h261002, operand, upper;
    task constant_x6;
        input [31:0] value;
        begin
            upper = value + 32'h800;
            program_words[cursor] = {upper[31:12], 5'd6, 7'b0110111};
            program_words[cursor+1] = {value[11:0], 5'd6, 3'b000, 5'd6, 7'b0010011};
            cursor = cursor + 2;
        end
    endtask
    initial begin
        for (index = 0; index < 4096; index = index + 1) program_words[index] = 32'h00000013;
        program_words[0] = {20'h3, 5'd20, 7'b0110111};
        program_words[1] = {12'h305, 5'd20, 3'b001, 5'd0, 7'b1110011};
        cursor = 2;
        for (index = 0; index < 40; index = index + 1) begin
            seed = seed * 32'd1664525 + 32'd1013904223;
            immediate = $signed({{20{seed[11]}}, seed[11:0]});
            target = cursor*4 + (index % 3 == 0 ? 12 : 16);
            if (index % 13 == 0) begin target = target + 2; expected_traps = expected_traps + 1; end
            operand = target - immediate + (index % 2);
            constant_x6(operand);
            program_words[cursor] = {immediate[11:0], 5'd6, 3'b000, 5'd5, 7'b1100111};
            program_words[cursor+1] = 32'h00250513;
            program_words[cursor+2] = 32'h00150513;
            cursor = cursor + 3;
            expected = expected + (index % 3 == 0 || index % 13 == 0 ? 3 : 1);
        end
        program_words[cursor] = 32'h12300593;
        program_words[cursor+1] = 32'h0000006f;
        program_words[3072] = {12'h341, 5'd0, 3'b010, 5'd20, 7'b1110011};
        program_words[3073] = 32'h004a0a13;
        program_words[3074] = {12'h341, 5'd20, 3'b001, 5'd0, 7'b1110011};
        program_words[3075] = 32'h30200073;
        #22 rst_n = 1;
    end
    always @(posedge clk) begin
        #1;
        if (rst_n) begin
            count = count + 1;
            if ({pc0, result0, trap0, cycles0, retired0} !== {pc1, result1, trap1, cycles1, retired1})
                $fatal(1, "Early prediction changed architectural execution at cycle %0d", count);
            if (optimized.idex_valid && optimized.ex_mispredict !== reference_core.ex_mispredict)
                $fatal(1, "Early JALR prediction differs at PC %h", optimized.idex_pc);
            if (trap0) traps = traps + 1;
            if (optimized.regs_valid[11] && optimized.regs[11] == 32'h123) begin
                if (result0 != expected || traps != expected_traps)
                    $fatal(1, "JALR results %0d/%0d traps %0d/%0d", result0, expected, traps, expected_traps);
                $display("PASS: early control cycle equivalence, 40 signed/odd/wrapping JALRs, predicted fallthroughs and %0d precise alignment traps", traps);
                $finish;
            end
            if (count > 10000) $fatal(1, "Early control comparison timed out");
        end
    end
endmodule
