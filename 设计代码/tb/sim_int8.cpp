#include "Vint8_accelerator.h"
#include "verilated.h"
#include <algorithm>
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
    uint64_t cycles = 0;
    bool pending = false, response = false, fault_used = false;
    bool pending_write = false;
    uint32_t address = 0, mask = 0, fault_mode = 0, delay = 0;
    std::array<uint32_t, 8> data{};

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
            for (unsigned i=0; i<32; ++i) {
                if (pending_write && !fault && (mask & (1U << i))) memory[address+i] = (data[i/4] >> ((i%4)*8)) & 255;
            }
            for (unsigned i=0; i<8; ++i) {
                dut.mem_rsp_rdata[i] = uint32_t(memory[address+i*4]) | uint32_t(memory[address+i*4+1]) << 8 |
                    uint32_t(memory[address+i*4+2]) << 16 | uint32_t(memory[address+i*4+3]) << 24;
            }
            response = true; pending = false; dut.mem_rsp_error = fault;
        } else if (pending) --delay;
        dut.mem_rsp_valid = response;
        dut.eval();
        const bool accept = dut.mem_req_valid && dut.mem_req_ready;
        const bool consume = dut.mem_rsp_valid && dut.mem_rsp_ready;
        if (accept) {
            if (pending || response) throw std::runtime_error("More than one pending memory transaction");
            address = offset(dut.mem_req_addr); pending_write = dut.mem_req_write; mask = dut.mem_req_wstrb;
            for (unsigned i=0; i<8; ++i) data[i] = dut.mem_req_wdata[i];
        }
        dut.clk = 1; dut.eval(); ++cycles;
        if (accept) { pending = true; delay = 1+random()%12; }
        if (consume) response = false;
        if (cycles > 4000000) throw std::runtime_error("Accelerator test timed out, state="+std::to_string(dut.debug_state));
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

    void initialize(const std::string& path) {
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
        dut.rst_n = 0; dut.control_req_valid = 0; dut.control_rsp_ready = 1;
        for (unsigned i=0; i<5; ++i) tick();
        dut.rst_n = 1;
        for (unsigned i=0; i<3; ++i) tick();
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    try {
        if (argc != 2) throw std::runtime_error("Expected a test-case directory");
        const std::string directory = argv[1];
        std::ifstream config_file(directory+"/config.bin", std::ios::binary);
        std::array<uint32_t, 24> config{};
        for (auto& value : config) value = word(config_file);
        Simulation sim; sim.initialize(directory+"/segments.bin"); sim.fault_mode = config[18];
        for (unsigned i=0; i<17; ++i) sim.mmio(i+1, config[i], true);
        if (sim.mmio(26) != 0x26100123) throw std::runtime_error("Unexpected accelerator version");
        sim.mmio(0, 1, true);
        uint32_t status = sim.mmio(0);
        if (status & 1) sim.mmio(1, 0xffffffffU, true, true);
        while ((status & 2) == 0) { for (unsigned i=0; i<20; ++i) sim.tick(); status = sim.mmio(0); }
        if (bool(status & 4) != bool(config[17]) || sim.mmio(25) != config[17])
            throw std::runtime_error("Wrong job fault status, code="+std::to_string(sim.mmio(25)));
        if (!config[17]) {
            std::ifstream expected_file(directory+"/expected.bin", std::ios::binary);
            std::vector<unsigned char> expected(config[20]);
            if (!expected_file.read(reinterpret_cast<char*>(expected.data()), expected.size())) throw std::runtime_error("Truncated expected output");
            const uint32_t base = config[19]-0x80000000U;
            unsigned errors = 0;
            for (unsigned i=0; i<expected.size(); ++i) if (expected[i] != sim.memory[base+i]) {
                if (errors < 12) std::cerr << "mismatch byte=" << i << " got=" << unsigned(sim.memory[base+i]) << " expected=" << unsigned(expected[i]) << "\n";
                ++errors;
            }
            if (errors) throw std::runtime_error("INT8 output mismatch: "+std::to_string(errors)+" bytes");
        }
        if (config[18] && !sim.fault_used) throw std::runtime_error("Memory fault was not exercised");
        const auto low = sim.mmio(20), high = sim.mmio(21), reads = sim.mmio(22), writes = sim.mmio(23), compute = sim.mmio(24);
        sim.mmio(0, 2, true);
        if (sim.mmio(0) != 0) throw std::runtime_error("Job completion did not clear");
        std::cout << "PASS: INT8 engine cycles=" << ((uint64_t(high)<<32)|low) << " reads=" << reads
                  << " writes=" << writes << " compute=" << compute << " error=" << config[17] << "\n";
        return 0;
    } catch (const std::exception& error) { std::cerr << "FAIL: " << error.what() << "\n"; return 1; }
}
