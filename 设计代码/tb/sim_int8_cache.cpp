#include "Vint8_accelerator.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

static uint32_t word(std::istream& stream) {
    std::array<unsigned char, 4> bytes{};
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), 4)) throw std::runtime_error("Truncated test input");
    return uint32_t(bytes[0]) | uint32_t(bytes[1]) << 8 | uint32_t(bytes[2]) << 16 | uint32_t(bytes[3]) << 24;
}

struct Simulation {
    Vint8_accelerator dut;
    std::vector<unsigned char> memory = std::vector<unsigned char>(64*1024*1024, 0xa7);
    std::mt19937 random{261001};
    uint64_t cycles = 0, deadline = 4000000;
    bool pending = false, response = false, fault_used = false;
    bool pending_write = false, stalled = false, held_write = false;
    uint32_t address = 0, mask = 0, fault_mode = 0, delay = 0;
    uint32_t held_address = 0, held_mask = 0, output_address = 0, output_bytes = 0;
    std::array<uint32_t, 8> data{}, held_data{};

    uint32_t offset(uint32_t value) {
        if (value < 0x80000000U || uint64_t(value)-0x80000000U+32 > memory.size() || (value & 31))
            throw std::runtime_error("Out-of-range or unaligned accelerator transfer");
        return value-0x80000000U;
    }

    void tick() {
        dut.clk = 0;
        dut.mem_req_ready = !pending && !response && random()%4 != 0;
        if (pending && delay == 0) {
            bool fault = !fault_used && ((fault_mode == 1 && !pending_write) || (fault_mode == 2 && pending_write));
            fault_used |= fault;
            for (unsigned i=0; i<32; ++i)
                if (pending_write && !fault && (mask & (1U << i))) memory[address+i] = (data[i/4] >> ((i%4)*8)) & 255;
            for (unsigned i=0; i<8; ++i)
                dut.mem_rsp_rdata[i] = uint32_t(memory[address+i*4]) | uint32_t(memory[address+i*4+1]) << 8 |
                    uint32_t(memory[address+i*4+2]) << 16 | uint32_t(memory[address+i*4+3]) << 24;
            response = true; pending = false; dut.mem_rsp_error = fault;
        } else if (pending) --delay;
        dut.mem_rsp_valid = response;
        dut.eval();
        if (stalled) {
            if (!dut.mem_req_valid || held_address != dut.mem_req_addr || held_write != bool(dut.mem_req_write) ||
                held_mask != dut.mem_req_wstrb) throw std::runtime_error("DMA request changed under backpressure");
            for (unsigned i=0; i<8; ++i)
                if (held_data[i] != dut.mem_req_wdata[i]) throw std::runtime_error("DMA data changed under backpressure");
        }
        const bool accept = dut.mem_req_valid && dut.mem_req_ready;
        const bool consume = dut.mem_rsp_valid && dut.mem_rsp_ready;
        stalled = dut.mem_req_valid && !dut.mem_req_ready;
        if (stalled) {
            held_address = dut.mem_req_addr; held_write = dut.mem_req_write; held_mask = dut.mem_req_wstrb;
            for (unsigned i=0; i<8; ++i) held_data[i] = dut.mem_req_wdata[i];
        }
        if (accept) {
            if (pending || response) throw std::runtime_error("More than one pending memory transaction");
            address = offset(dut.mem_req_addr); pending_write = dut.mem_req_write; mask = dut.mem_req_wstrb;
            if (pending_write && (dut.mem_req_addr < output_address ||
                uint64_t(dut.mem_req_addr)+32 > uint64_t(output_address)+output_bytes))
                throw std::runtime_error("Output write escaped the NCHW output tensor");
            for (unsigned i=0; i<8; ++i) data[i] = dut.mem_req_wdata[i];
        }
        dut.clk = 1; dut.eval(); ++cycles;
        if (accept) { pending = true; delay = 1+random()%12; }
        if (consume) response = false;
        if (cycles > deadline) throw std::runtime_error("Accelerator test timed out, state="+std::to_string(dut.debug_state));
    }

    uint32_t mmio(unsigned index, uint32_t value=0, bool write=false, bool expected_error=false) {
        dut.control_req_valid = 1; dut.control_req_addr = 0x21000000U+index*4;
        dut.control_req_write = write; dut.control_req_wdata = value; dut.control_req_wstrb = 15;
        dut.clk = 0; dut.eval();
        while (!dut.control_req_ready) tick();
        tick(); dut.control_req_valid = 0;
        while (!dut.control_rsp_valid) tick();
        const uint32_t result = dut.control_rsp_rdata;
        if (bool(dut.control_rsp_error) != expected_error) throw std::runtime_error("Unexpected MMIO response error");
        tick(); return result;
    }

    void reset() {
        dut.rst_n = 0; dut.control_req_valid = 0; dut.control_rsp_ready = 1;
        for (unsigned i=0; i<5; ++i) tick();
        dut.rst_n = 1;
        for (unsigned i=0; i<3; ++i) tick();
    }

    void load(const std::string& path) {
        std::ifstream stream(path, std::ios::binary);
        if (!stream) throw std::runtime_error("Cannot open test memory segments");
        const auto count = word(stream);
        for (unsigned segment=0; segment<count; ++segment) {
            const uint32_t base = word(stream), length = word(stream);
            if (base < 0x80000000U || uint64_t(base)-0x80000000U+length > memory.size())
                throw std::runtime_error("Test memory segment is out of range");
            if (!stream.read(reinterpret_cast<char*>(memory.data()+base-0x80000000U), length))
                throw std::runtime_error("Truncated memory segment");
        }
    }

    void job(const std::string& directory) {
        std::ifstream config_file(directory+"/config.bin", std::ios::binary);
        std::array<uint32_t, 24> config{};
        for (auto& value : config) value = word(config_file);
        if (pending || response || stalled) throw std::runtime_error("A previous job left an outstanding transaction");
        load(directory+"/segments.bin");
        random.seed(261001);
        fault_mode = config[18]; fault_used = false; deadline = cycles+4000000;
        output_address = config[19]; output_bytes = config[20];
        for (unsigned i=0; i<output_bytes; ++i) memory[output_address-0x80000000U+i] = 0xa7;
        for (unsigned i=0; i<17; ++i) mmio(i+1, config[i], true);
        if (mmio(26) != 0x26100123) throw std::runtime_error("Unexpected accelerator version");
        mmio(0, 1, true);
        uint32_t status = mmio(0);
        if (status & 1) mmio(1, 0xffffffffU, true, true);
        while ((status & 2) == 0) { for (unsigned i=0; i<20; ++i) tick(); status = mmio(0); }
        if (bool(status & 4) != bool(config[17]) || mmio(25) != config[17])
            throw std::runtime_error("Wrong job fault status, code="+std::to_string(mmio(25)));
        if (!config[17]) {
            std::ifstream expected_file(directory+"/expected.bin", std::ios::binary);
            std::vector<unsigned char> expected(config[20]);
            if (!expected_file.read(reinterpret_cast<char*>(expected.data()), expected.size())) throw std::runtime_error("Truncated expected output");
            const uint32_t base = config[19]-0x80000000U;
            unsigned errors = 0;
            for (unsigned i=0; i<expected.size(); ++i) if (expected[i] != memory[base+i]) {
                if (errors < 12) std::cerr << "mismatch byte=" << i << " got=" << unsigned(memory[base+i]) << " expected=" << unsigned(expected[i]) << "\n";
                ++errors;
            }
            if (errors) throw std::runtime_error("INT8 output mismatch: "+std::to_string(errors)+" bytes");
        }
        if (config[18] && !fault_used) throw std::runtime_error("Memory fault was not exercised");
        const auto low = mmio(20), high = mmio(21), reads = mmio(22), writes = mmio(23), compute = mmio(24);
        mmio(0, 2, true);
        if (mmio(0) != 0) throw std::runtime_error("Job completion did not clear");
        std::cout << "PASS case=" << directory.substr(directory.find_last_of('/')+1)
                  << " cycles=" << ((uint64_t(high)<<32)|low) << " reads=" << reads
                  << " writes=" << writes << " compute=" << compute << " error=" << config[17] << "\n";
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    try {
        if (argc < 2) throw std::runtime_error("Expected test-case directories");
        Simulation sim; sim.reset();
        // One reset for the whole suite deliberately tests per-job cache
        // invalidation when the next case rewrites the same DDR addresses.
        for (int i=1; i<argc; ++i) sim.job(argv[i]);
        return 0;
    } catch (const std::exception& error) { std::cerr << "FAIL: " << error.what() << "\n"; return 1; }
}
