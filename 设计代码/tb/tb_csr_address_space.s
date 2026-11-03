`timescale 1ns/1ps
module tb_csr_address_space;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg [31:0] program_words [0:16383];
    wire [31:0] addr0, addr1, pc0, pc1;
    wire [63:0] cycles0, cycles1, retired0, retired1;
    wire trap0, trap1;
    integer i, count = 0, valid_reads = 0, illegal_reads = 0;
    integer last_csr_pc = -1;
    reg expected_supported;
    rv32i_pipeline_core #(.FAST_TRAP_PATH(1), .CSR_ADDRESS_PREDECODE(1)) optimized (
        .clk(clk), .rst_n(rst_n), .imem_addr(addr0), .imem_rdata(program_words[addr0[15:2]]),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_rdata(32'd0), .dmem_ready(1'b1), .dmem_error(1'b0),
        .irq_timer(1'b0), .irq_external(1'b0), .current_pc(pc0), .trap_taken(trap0),
        .cycle_count(cycles0), .instret_count(retired0));
    rv32i_pipeline_core #(.FAST_TRAP_PATH(1), .CSR_ADDRESS_PREDECODE(0)) original (
        .clk(clk), .rst_n(rst_n), .imem_addr(addr1), .imem_rdata(program_words[addr1[15:2]]),
        .imem_req_ready(1'b1), .imem_ready(1'b1), .imem_error(1'b0),
        .dmem_rdata(32'd0), .dmem_ready(1'b1), .dmem_error(1'b0),
        .irq_timer(1'b0), .irq_external(1'b0), .current_pc(pc1), .trap_taken(trap1),
        .cycle_count(cycles1), .instret_count(retired1));
    function csr_implemented;
        input [11:0] address;
        begin
            // Independent architectural address list, including RO aliases.
            csr_implemented = address == 12'h300 || address == 12'h301 ||
                address == 12'h304 || address == 12'h305 ||
                (address >= 12'h340 && address <= 12'h344) ||
                (address >= 12'hf11 && address <= 12'hf14) ||
                address == 12'hb00 || address == 12'hb02 ||
                address == 12'hb80 || address == 12'hb82 ||
                address == 12'hc00 || address == 12'hc02 ||
                address == 12'hc80 || address == 12'hc82;
        end
    endfunction
    always @(negedge clk) if (rst_n) begin
        count = count + 1;
        if ({pc0, cycles0, retired0, trap0, optimized.fetch_flush, optimized.idex_valid,
             optimized.exmem_valid, optimized.csr_mepc, optimized.csr_mcause, optimized.csr_mtval} !==
            {pc1, cycles1, retired1, trap1, original.fetch_flush, original.idex_valid,
             original.exmem_valid, original.csr_mepc, original.csr_mcause, original.csr_mtval})
            $fatal(1, "CSR address-space enabled/disabled state divergence at cycle %0d",count);
        if (optimized.idex_valid && optimized.execute_advance &&
            optimized.idex_pc >= 16 && optimized.idex_pc < 12+4096*8 && !optimized.idex_pc[2]) begin
            if (last_csr_pc == optimized.idex_pc) $fatal(1,"CSR executed twice");
            last_csr_pc = optimized.idex_pc;
            expected_supported = csr_implemented(optimized.idex_instr[31:20]);
            if (optimized.csr_address_valid !== expected_supported ||
                optimized.ex_exception !== !expected_supported)
                $fatal(1,"Incorrect CSR legality address=%h",optimized.idex_instr[31:20]);
            if (expected_supported) valid_reads = valid_reads + 1;
            else begin
                illegal_reads = illegal_reads + 1;
                if (optimized.ex_exception_cause != 2 || optimized.ex_exception_tval !== optimized.idex_instr)
                    $fatal(1,"Incorrect precise CSR illegal trap");
            end
        end
        if (optimized.regs_valid[10] && optimized.regs[10] == 32'h600d && last_csr_pc == 32776) begin
            if (valid_reads != 21 || illegal_reads != 4075)
                $fatal(1,"Missing complete CSR address space valid=%0d illegal=%0d",valid_reads,illegal_reads);
            $display("PASS: all4096 CSR addresses executed by actual fetch/ID/EX, 21 implemented reads,4075 precise illegal traps and enabled/disabled cycle-equivalent state (%0d cycles)",count);
            $finish;
        end
        if (count > 100000) $fatal(1,"CSR address-space timeout valid=%0d illegal=%0d pc=%h",valid_reads,illegal_reads,pc0);
    end
    initial begin
        for (i=0;i<16384;i=i+1) program_words[i]=32'h00000013;
        // mtvec=0xc000, then all twelve-bit CSR addresses as legal read encodings.
        program_words[0]=32'h0000c2b7;
        program_words[1]={12'h305,5'd5,3'b001,5'd0,7'b1110011};
        program_words[2]=32'h00000013;
        for (i=0;i<4096;i=i+1) begin
            program_words[3+2*i]=32'h07b00513;
            program_words[4+2*i]={i[11:0],5'd0,3'b010,5'd10,7'b1110011};
        end
        program_words[8195]=32'h00006537;
        program_words[8196]=32'h00d50513;
        program_words[8197]=32'h0000006f;
        // Preserve the CSR read destination and skip one faulting instruction.
        program_words[12288]={12'h341,5'd0,3'b010,5'd5,7'b1110011};
        program_words[12289]=32'h00428293;
        program_words[12290]={12'h341,5'd5,3'b001,5'd0,7'b1110011};
        program_words[12291]=32'h30200073;
        repeat(5) @(negedge clk);rst_n=1;
    end
endmodule
