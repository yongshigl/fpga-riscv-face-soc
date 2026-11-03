`timescale 1ns/1ps

module tb_early_load_decode;
`ifdef CPU_EARLY_LOAD_PORTS
    reg clk = 0, rst_n = 0;
    reg [31:0] base, instruction;
    reg access_error = 0, older_request = 0, interrupt_pending = 0;
    wire early_valid;
    wire [31:0] early_addr;
    integer checks = 0, early_checks = 0, mmio_checks = 0;
    integer i, j, width;
    reg [31:0] bases [0:22];
    integer offsets [0:8];

    rv32i_pipeline_core #(.WAIT_MEMORY(1), .EARLY_LOAD_REQUEST(1)) dut (
        .clk(clk), .rst_n(rst_n), .imem_valid(), .imem_addr(),
        .imem_rdata(32'h00000013), .imem_req_ready(1'b0), .imem_ready(1'b0),
        .imem_error(1'b0), .imem_consume(), .fetch_flush(), .fence_i(),
        .dmem_valid(), .dmem_we(), .dmem_addr(), .dmem_wdata(), .dmem_wstrb(),
        .dmem_rdata(32'd0), .dmem_ready(1'b0), .dmem_error(1'b0),
        .dmem_early_valid(early_valid), .dmem_early_addr(early_addr),
        .irq_timer(1'b0), .irq_external(interrupt_pending),
        .current_pc(), .debug_x10(), .trap_taken(), .cycle_count(), .instret_count()
    );

    task check_load;
        input [31:0] address_base;
        input integer offset;
        input [2:0] funct3;
        input [6:0] opcode;
        reg [31:0] effective;
        reg legal, aligned, safe_base, expected;
        begin
            base = address_base;
            instruction = {offset[11:0], 5'd1, funct3, 5'd2, opcode};
            effective = address_base + offset;
            legal = funct3 == 0 || funct3 == 1 || funct3 == 2 || funct3 == 4 || funct3 == 5;
            aligned = (funct3 == 0 || funct3 == 4) ||
                      ((funct3 == 1 || funct3 == 5) && !effective[0]) ||
                      (funct3 == 2 && effective[1:0] == 0);
            safe_base = address_base[31:24] == 8'h00 || address_base[31:24] == 8'h10 ||
                        address_base[31:24] == 8'h40;
            expected = opcode == 7'b0000011 && legal && aligned && safe_base &&
                       !access_error && !older_request && !interrupt_pending;
            #1;
            if (early_valid !== expected)
                $fatal(1, "Early eligibility base=%h offset=%0d funct3=%0d opcode=%h valid=%b expected=%b",
                    address_base, offset, funct3, opcode, early_valid, expected);
            if (opcode == 7'b0000011 && early_addr !== effective)
                $fatal(1, "Load address mismatch base=%h offset=%0d actual=%h expected=%h",
                    address_base, offset, early_addr, effective);
            if (opcode == 7'b0000011 && !access_error &&
                dut.ex_load_address !== dut.ex_alu_sum[31:0])
                $fatal(1, "Dedicated load address differs from canonical ALU");
            if (effective[31:16] == 16'h2000 || effective[31:16] == 16'h2100) begin
                mmio_checks = mmio_checks + 1;
                if (early_valid) $fatal(1, "MMIO load started in EX at %h", effective);
            end
            if (early_valid) early_checks = early_checks + 1;
            checks = checks + 1;
        end
    endtask

    initial begin
        force dut.idex_valid = 1'b1;
        force dut.idex_instr = instruction;
        force dut.idex_access_error = access_error;
        force dut.ex_rs1_value = base;
        force dut.ex_rs2_value = 32'd0;
        force dut.exmem_valid = older_request;
        force dut.exmem_mem_read = 1'b1;
        force dut.exmem_mem_write = 1'b0;
        force dut.csr_mstatus = 32'h00000008;
        force dut.csr_mie = 32'h00000800;
        bases[0] = 32'h00000000; bases[1] = 32'h00000001;
        bases[2] = 32'h000007ff; bases[3] = 32'h00000800;
        bases[4] = 32'h00fff800; bases[5] = 32'h00ffffff;
        bases[6] = 32'h0fffffff; bases[7] = 32'h10000000;
        bases[8] = 32'h1000fffc; bases[9] = 32'h10ffffff;
        bases[10] = 32'h1ffff800; bases[11] = 32'h1fffffff;
        bases[12] = 32'h20000000; bases[13] = 32'h2000ffff;
        bases[14] = 32'h20fff800; bases[15] = 32'h20ffffff;
        bases[16] = 32'h21000000; bases[17] = 32'h2100ffff;
        bases[18] = 32'h40000000; bases[19] = 32'h40ffffff;
        bases[20] = 32'hffffffff; bases[21] = 32'h10001000;
        bases[22] = 32'h40000800;
        offsets[0] = 0; offsets[1] = 1; offsets[2] = -1;
        offsets[3] = 2047; offsets[4] = -2048; offsets[5] = 4;
        offsets[6] = -4; offsets[7] = -2047; offsets[8] = 2046;
        #1 rst_n = 1;
        for (i = 0; i < 23; i = i + 1)
            for (j = 0; j < 9; j = j + 1)
                for (width = 0; width < 8; width = width + 1)
                    check_load(bases[i], offsets[j], width[2:0], 7'b0000011);
        for (i = 0; i < 2048; i = i + 1)
            check_load($urandom, $urandom_range(0, 4095) - 2048,
                       $urandom_range(0, 7), 7'b0000011);
        access_error = 1;
        check_load(32'h10001000, -4, 3'b010, 7'b0000011);
        access_error = 0;
        older_request = 1;
        check_load(32'h10001000, -4, 3'b010, 7'b0000011);
        older_request = 0;
        interrupt_pending = 1;
        check_load(32'h10001000, -4, 3'b010, 7'b0000011);
        interrupt_pending = 0;
        check_load(32'h10001000, -4, 3'b010, 7'b0100011);
        if (early_checks < 100 || mmio_checks < 50)
            $fatal(1, "Insufficient early/MMIO coverage %0d/%0d", early_checks, mmio_checks);
        $display("PASS: early load address/alignment, signed offsets, wraparound, MMIO boundaries and fault/IRQ/older-request guards (%0d cases, %0d early, %0d MMIO)",
            checks, early_checks, mmio_checks);
        $finish;
    end
`else
    initial begin
        $display("PASS: early load decode not applicable to this historical core");
        $finish;
    end
`endif
endmodule
