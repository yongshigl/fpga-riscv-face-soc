`timescale 1ns/1ps

module tb_fast_trap_path;
    reg clk = 0, rst_n = 0, irq_timer = 0, irq_external = 0;
    always #5 clk = !clk;
    reg [31:0] program_words [0:1023];
    wire [31:0] address0, address1, result0, result1, pc0, pc1;
    wire trap0, trap1;
    wire [63:0] cycles0, cycles1, retired0, retired1;
    rv32i_pipeline_core #(.FAST_TRAP_PATH(1)
`ifdef FAST_REDIRECT_SELECT_COMPARE
        , .FAST_REDIRECT_SELECT(1)
`endif
`ifdef COUNTER_CSR_PREDECODE_COMPARE
        , .COUNTER_CSR_PREDECODE(1)
`endif
`ifdef CSR_ADDRESS_PREDECODE_COMPARE
        , .CSR_ADDRESS_PREDECODE(1)
`endif
    ) optimized (
        .clk(clk), .rst_n(rst_n), .imem_addr(address0),
        .imem_rdata(program_words[address0[11:2]]), .imem_req_ready(1'b1),
        .imem_ready(1'b1), .imem_error(1'b0), .dmem_rdata(32'd0),
        .dmem_ready(1'b1), .dmem_error(1'b0), .irq_timer(irq_timer), .irq_external(irq_external),
        .current_pc(pc0), .debug_x10(result0), .trap_taken(trap0),
        .cycle_count(cycles0), .instret_count(retired0)
    );
    rv32i_pipeline_core #(
`ifdef FAST_REDIRECT_SELECT_COMPARE
        .FAST_TRAP_PATH(1), .FAST_REDIRECT_SELECT(0)
`elsif COUNTER_CSR_PREDECODE_COMPARE
        .FAST_TRAP_PATH(1), .COUNTER_CSR_PREDECODE(0)
`elsif CSR_ADDRESS_PREDECODE_COMPARE
        .FAST_TRAP_PATH(1), .CSR_ADDRESS_PREDECODE(0)
`else
        .FAST_TRAP_PATH(0)
`endif
    ) reference_core (
        .clk(clk), .rst_n(rst_n), .imem_addr(address1),
        .imem_rdata(program_words[address1[11:2]]), .imem_req_ready(1'b1),
        .imem_ready(1'b1), .imem_error(1'b0), .dmem_rdata(32'd0),
        .dmem_ready(1'b1), .dmem_error(1'b0), .irq_timer(irq_timer), .irq_external(irq_external),
        .current_pc(pc1), .debug_x10(result1), .trap_taken(trap1),
        .cycle_count(cycles1), .instret_count(retired1)
    );
    integer cursor = 0, phase, index, count = 0;
    integer timer_traps = 0, external_traps = 0, exceptions = 0;
    integer alignment_traps = 0, illegal_traps = 0;
    reg timer_sent = 0, external_sent = 0;
    reg [31:0] upper;
    reg [63:0] pattern;
    reg [31:0] vector_test_base, vector_expected;
    reg vector_test_irq;
    integer vector_case, vector_mode, vector_kind, vector_checks = 0;
    function [31:0] csr_write;
        input [11:0] address;
        input [4:0] source;
        begin csr_write = {address, source, 3'b001, 5'd0, 7'b1110011}; end
    endfunction
    function [31:0] jump;
        input [20:0] offset;
        begin jump = {offset[20], offset[10:1], offset[11], offset[19:12], 5'd0, 7'b1101111}; end
    endfunction
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
        for (index = 0; index < 1024; index = index + 1) program_words[index] = 32'h00000013;
        constant_x6(32'hc01);
        program_words[cursor] = csr_write(12'h305, 5'd6); cursor = cursor + 1;
        for (phase = 0; phase < 8; phase = phase + 1) begin
            constant_x6(32'h880);
            program_words[cursor] = csr_write(12'h304, 5'd6); cursor = cursor + 1;
            constant_x6(32'h8);
            program_words[cursor] = csr_write(12'h300, 5'd6); cursor = cursor + 1;
            case (phase)
                0: pattern = 64'h000000000000ffe0;
                1: pattern = 64'h00000000ffffffe0;
                2: pattern = 64'h0000ffffffffffe0;
                3: pattern = 64'hffffffffffffffe0;
                4: pattern = 64'h123456789abcffe0;
                5: pattern = 64'h76543210ffffffe0;
                6: pattern = 64'hffffffff0000ffe0;
                default: pattern = 64'hffffffffffffffe0;
            endcase
            constant_x6(pattern[63:32]);
            program_words[cursor] = csr_write(12'hb80, 5'd6);
            program_words[cursor+1] = csr_write(12'hb82, 5'd6); cursor = cursor + 2;
            constant_x6(pattern[31:0]);
            program_words[cursor] = csr_write(12'hb00, 5'd6);
            program_words[cursor+1] = csr_write(12'hb02, 5'd6); cursor = cursor + 2;
            program_words[cursor] = {phase[11:0], 5'd0, 3'b000, 5'd19, 7'b0010011}; cursor = cursor + 1;
            cursor = cursor + 48;
            program_words[cursor] = 32'h00000073; cursor = cursor + 1;
        end
        // These faulting controls can request recovery; the precise trap wins.
        program_words[cursor] = jump(21'd2);
        program_words[cursor+1] = 32'h00031067;
        program_words[cursor+2] = 32'h00200067;
        program_words[cursor+3] = 32'h00000163;
        cursor = cursor + 4;
        program_words[cursor] = 32'h12300513;
        program_words[cursor+1] = 32'h0000006f;
        // Synchronous exceptions use the base; IRQ vectors lead to the same handler.
        program_words[768] = jump(21'h100);
        program_words[775] = jump(21'he4);
        program_words[779] = jump(21'hd4);
        program_words[832] = {12'h342, 5'd0, 3'b010, 5'd5, 7'b1110011};
        program_words[833] = 32'h0002c863;
        program_words[834] = {12'h341, 5'd0, 3'b010, 5'd6, 7'b1110011};
        program_words[835] = 32'h00430313;
        program_words[836] = csr_write(12'h341, 5'd6);
        program_words[837] = csr_write(12'h304, 5'd0);
        program_words[838] = 32'h30200073;
        force optimized.csr_mtvec = vector_test_base;
        force reference_core.csr_mtvec = vector_test_base;
        force optimized.csr_mie = 32'h880;
        force reference_core.csr_mie = 32'h880;
        force optimized.take_interrupt = vector_test_irq;
        force reference_core.take_interrupt = vector_test_irq;
        for (vector_case = 0; vector_case < 64; vector_case = vector_case + 1) begin
            case (vector_case)
                0: upper = 32'hfffffffc;
                1: upper = 32'hffffffe0;
                2: upper = 32'hfffffc00;
                3: upper = 32'h000003e0;
                4: upper = 32'h000003d4;
                5: upper = 32'h000003dc;
                default: upper = $urandom & 32'hfffffffc;
            endcase
            for (vector_mode = 0; vector_mode < 4; vector_mode = vector_mode + 1) begin
                vector_test_base = upper | vector_mode;
                for (vector_kind = 0; vector_kind < 4; vector_kind = vector_kind + 1) begin
                    vector_test_irq = vector_kind[0];
                    irq_external = vector_kind[1];
                    vector_expected = upper;
                    if (vector_test_irq && vector_mode == 1)
                        vector_expected = upper + (irq_external ? 32'd44 : 32'd28);
                    #1;
                    if (optimized.trap_vector !== vector_expected || reference_core.trap_vector !== vector_expected)
                        $fatal(1, "Vector mismatch: mtvec=%h IRQ=%b external=%b actual=%h/%h expected=%h",
                            vector_test_base, vector_test_irq, irq_external,
                            optimized.trap_vector, reference_core.trap_vector, vector_expected);
                    vector_checks = vector_checks + 1;
                end
            end
        end
        release optimized.csr_mtvec;
        release reference_core.csr_mtvec;
        release optimized.csr_mie;
        release reference_core.csr_mie;
        release optimized.take_interrupt;
        release reference_core.take_interrupt;
        irq_external = 0;
        #22 rst_n = 1;
    end
    always @(posedge clk) begin
        #1;
        if (rst_n) begin
            count = count + 1;
            if ({pc0, result0, trap0, cycles0, retired0} !== {pc1, result1, trap1, cycles1, retired1})
                $fatal(1, "Trap/counter optimization differs at cycle %0d: PC %h/%h cycle %h/%h retired %h/%h",
                       count, pc0, pc1, cycles0, cycles1, retired0, retired1);
            if ({optimized.fetch_flush, optimized.imem_valid, optimized.fence_i,
                 optimized.dmem_valid, optimized.dmem_we, optimized.ifid_valid,
                 optimized.idex_valid, optimized.exmem_valid} !==
                {reference_core.fetch_flush, reference_core.imem_valid, reference_core.fence_i,
                 reference_core.dmem_valid, reference_core.dmem_we, reference_core.ifid_valid,
                 reference_core.idex_valid, reference_core.exmem_valid})
                $fatal(1, "Control recovery/flush differs at cycle %0d", count);
            if (optimized.regs_valid[19] && optimized.regs[19] == 1 && !timer_sent) begin
                irq_timer = 1; timer_sent = 1;
            end
            if (optimized.regs_valid[19] && optimized.regs[19] == 3 && !external_sent) begin
                irq_external = 1; external_sent = 1;
            end
            if (trap0) begin
                case (optimized.csr_mcause)
                    32'h80000007: begin timer_traps = timer_traps + 1; irq_timer = 0; end
                    32'h8000000b: begin external_traps = external_traps + 1; irq_external = 0; end
                    32'd11: exceptions = exceptions + 1;
                    32'd0: alignment_traps = alignment_traps + 1;
                    32'd2: illegal_traps = illegal_traps + 1;
                    default: $fatal(1, "Unexpected trap %h", optimized.csr_mcause);
                endcase
            end
            if (result0 == 32'h123) begin
                if (timer_traps != 1 || external_traps != 1 || exceptions != 8 ||
                    alignment_traps != 3 || illegal_traps != 1 || vector_checks != 1024)
                    $fatal(1, "Missing trap cases: timer %0d external %0d ECALL %0d alignment %0d illegal %0d",
                           timer_traps, external_traps, exceptions, alignment_traps, illegal_traps);
                $display("PASS: cycle-equivalent counters, CSR half writes, 1024 IRQ vector boundaries, control faults and trap-priority recovery/flush");
`ifdef COUNTER_CSR_PREDECODE_COMPARE
                $display("PASS: counter CSR ID-predecode enabled/disabled remain cycle-equivalent");
`endif
`ifdef CSR_ADDRESS_PREDECODE_COMPARE
                $display("PASS: CSR address ID-predecode enabled/disabled remain cycle-equivalent");
`endif
`ifdef FAST_REDIRECT_SELECT_COMPARE
                $display("PASS: fast redirect selection enabled/disabled remain cycle-equivalent");
`endif
                $finish;
            end
            if (count > 10000) $fatal(1, "Trap/counter comparison timed out");
        end
    end
endmodule
