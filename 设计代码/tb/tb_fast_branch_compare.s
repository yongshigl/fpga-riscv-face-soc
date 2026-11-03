`timescale 1ns/1ps

module tb_fast_branch_compare;
    reg clk = 0;
    always #5 clk = !clk;
    reg [31:0] operand_a, operand_b, instruction;
    reg [31:0] boundaries [0:15];
    reg expected;
    integer sample, first, second, funct3, checks = 0;
    rv32i_pipeline_core #(.FAST_TRAP_PATH(1)) optimized (
        .clk(clk), .rst_n(1'b0), .imem_rdata(32'h00000013),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_rdata(32'd0), .dmem_ready(1'b1), .dmem_error(1'b0),
        .irq_timer(1'b0), .irq_external(1'b0)
    );
    rv32i_pipeline_core #(.FAST_TRAP_PATH(0)) reference_core (
        .clk(clk), .rst_n(1'b0), .imem_rdata(32'h00000013),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_rdata(32'd0), .dmem_ready(1'b1), .dmem_error(1'b0),
        .irq_timer(1'b0), .irq_external(1'b0)
    );
    task check_pair;
        begin
            for (funct3 = 0; funct3 < 8; funct3 = funct3 + 1) begin
                instruction = 32'h00000063 | (funct3 << 12);
                case (funct3)
                    0: expected = operand_a == operand_b;
                    1: expected = operand_a != operand_b;
                    4: expected = $signed(operand_a) < $signed(operand_b);
                    5: expected = $signed(operand_a) >= $signed(operand_b);
                    6: expected = operand_a < operand_b;
                    7: expected = operand_a >= operand_b;
                    default: expected = 0;
                endcase
                #1;
                if (optimized.ex_branch_taken !== expected || reference_core.ex_branch_taken !== expected)
                    $fatal(1, "Branch comparison mismatch: a=%h b=%h funct3=%0d actual=%b/%b expected=%b",
                        operand_a, operand_b, funct3, optimized.ex_branch_taken,
                        reference_core.ex_branch_taken, expected);
                if ({optimized.ex_exception, optimized.ex_legal, optimized.ex_redirect} !==
                    {reference_core.ex_exception, reference_core.ex_legal, reference_core.ex_redirect})
                    $fatal(1, "Branch control differs at a=%h b=%h funct3=%0d", operand_a, operand_b, funct3);
                checks = checks + 1;
            end
        end
    endtask
    initial begin
        boundaries[0] = 32'h00000000; boundaries[1] = 32'h00000001;
        boundaries[2] = 32'hffffffff; boundaries[3] = 32'hfffffffe;
        boundaries[4] = 32'h80000000; boundaries[5] = 32'h7fffffff;
        boundaries[6] = 32'h000000ff; boundaries[7] = 32'h00000100;
        boundaries[8] = 32'h0000ffff; boundaries[9] = 32'h00010000;
        boundaries[10] = 32'h00ffffff; boundaries[11] = 32'h01000000;
        boundaries[12] = 32'hff000000; boundaries[13] = 32'hffff0000;
        boundaries[14] = 32'h80808080; boundaries[15] = 32'h7f7f7f7f;
        #2;
        force optimized.idex_valid = 1'b1;
        force reference_core.idex_valid = 1'b1;
        force optimized.idex_instr = instruction;
        force reference_core.idex_instr = instruction;
        force optimized.control_rs1_value = operand_a;
        force reference_core.control_rs1_value = operand_a;
        force optimized.control_rs2_value = operand_b;
        force reference_core.control_rs2_value = operand_b;
        for (first = 0; first < 16; first = first + 1)
            for (second = 0; second < 16; second = second + 1) begin
                operand_a = boundaries[first]; operand_b = boundaries[second];
                check_pair;
            end
        for (sample = 0; sample < 256; sample = sample + 1) begin
            operand_a = $urandom; operand_b = $urandom;
            check_pair;
        end
        if (checks != 4096) $fatal(1, "Missing branch comparison coverage");
        $display("PASS: 4096 signed/unsigned/equality branch comparisons, byte boundaries, random operands and illegal funct3 controls");
        $finish;
    end
endmodule
