`timescale 1ns/1ps

module tb_btb_hash_alias;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] test_pc = 0, execute_pc = 0;
    reg [31:0] seen;
    reg [31:0] target;
    integer tag_number, word_number, other_word, slot, alias_count;
    integer roundtrips = 0, aliases = 0;
    always #5 clk = !clk;
    rv32i_pipeline_core #(.BTB_INDEX_BITS(5), .RAS_BITS(0)) dut (
        .clk(clk), .rst_n(rst_n), .imem_rdata(32'h00000013),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_rdata(32'd0), .dmem_ready(1'b1), .dmem_error(1'b0),
        .irq_timer(1'b0), .irq_external(1'b0)
    );
    initial begin
        #12 rst_n = 1;
        force dut.fetch_pc = test_pc;
        force dut.idex_pc = execute_pc;
        force dut.idex_valid = 1'b1;
        force dut.idex_instr = 32'h00000063;
`ifdef CPU_PRECOMPUTE_CONTROL_TARGET
        force dut.idex_branch_target = execute_pc;
        force dut.idex_jal_target = execute_pc;
        force dut.idex_sequential_pc = execute_pc + 32'd4;
`endif
        force dut.execute_advance = 1'b1;
        force dut.take_trap = 1'b0;
        force dut.ex_branch_taken = 1'b1;
        force dut.ex_legal = 1'b1;
        for (tag_number = 0; tag_number < 128; tag_number = tag_number + 1) begin
            seen = 0;
            for (word_number = 0; word_number < 32; word_number = word_number + 1) begin
                execute_pc = (tag_number << 7) | (word_number << 2);
                test_pc = execute_pc;
                target = execute_pc;
                @(negedge clk);
                @(posedge clk);
                #1;
                slot = dut.ex_btb_index;
                if (dut.fetch_btb_index !== slot[4:0] || seen[slot])
                    $fatal(1, "Fetch/update indices disagree or PCs with one tag alias");
                seen[slot] = 1;
                #1;
                if (!dut.fetch_btb_hit || dut.fetch_next_pc !== target || dut.btb_target[slot] !== target)
                    $fatal(1, "BTB update cannot be retrieved with its original PC");
                roundtrips = roundtrips + 1;
                alias_count = 0;
                for (other_word = 0; other_word < 32; other_word = other_word + 1) begin
                    test_pc = ((tag_number + 1024) << 7) | (other_word << 2);
                    #1;
                    if (dut.fetch_btb_index == slot[4:0]) begin
                        alias_count = alias_count + 1;
                        if (dut.fetch_btb_hit || dut.fetch_next_pc !== test_pc + 32'd4)
                            $fatal(1, "A different tag consumed a colliding branch target");
                        aliases = aliases + 1;
                    end
                end
                if (alias_count != 1)
                    $fatal(1, "The index is not a permutation within a tag");
            end
            if (seen != 32'hffffffff) $fatal(1, "Some BTB indices are unreachable");
        end
        if (roundtrips != 4096 || aliases != 4096) $fatal(1, "Missing BTB address coverage");
        $display("PASS: 4096 BTB update/fetch roundtrips and 4096 conflicting-tag fallthroughs; every tag uses all 32 slots");
        $finish;
    end
endmodule
