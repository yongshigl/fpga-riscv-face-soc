#include "Vvision_soc.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef VISION_SIM_CPU_HZ
#define VISION_SIM_CPU_HZ 81000000
#endif
#ifndef VISION_SIM_AI_HZ
#define VISION_SIM_AI_HZ 54000000
#endif
#ifndef VISION_SIM_MEMORY_HZ
#define VISION_SIM_MEMORY_HZ 125000000
#endif
#ifndef VISION_SIM_CAMERA_BUILD_ID
#define VISION_SIM_CAMERA_BUILD_ID 0x26100106
#endif

using Bytes = std::vector<unsigned char>;

static uint32_t load32(const Bytes& data, unsigned offset) {
    if (offset + 4 > data.size()) throw std::runtime_error("Short packet");
    return uint32_t(data[offset]) | uint32_t(data[offset+1]) << 8 |
           uint32_t(data[offset+2]) << 16 | uint32_t(data[offset+3]) << 24;
}
static void store32(Bytes& data, uint32_t value) {
    for (unsigned i=0; i<4; ++i) data.push_back((value >> (i*8)) & 255);
}
static uint32_t crc32(const Bytes& data) {
    uint32_t crc = 0xffffffffU;
    for (auto value : data) {
        crc ^= value;
        for (unsigned bit=0; bit<8; ++bit) crc = (crc >> 1) ^ (0xedb88320U & (0U-(crc & 1U)));
    }
    return ~crc;
}
static Bytes file_bytes(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw std::runtime_error("Cannot open "+path);
    return Bytes(std::istreambuf_iterator<char>(stream), {});
}
static void require(bool result, const std::string& message) {
    if (!result) throw std::runtime_error(message);
}

struct Simulation {
    Vvision_soc dut;
    Bytes memory = Bytes(64*1024*1024, 0xa7);
    Bytes received;
    uint64_t time = 0, cpu_cycles = 0, reads = 0, writes = 0;
    /* Runaway guard. Tests that need a long stretch of plain execution (CoreMark
       printing its banner over the wire) raise it and put it back. */
    uint64_t cycle_cap = 100000000;
    std::mt19937 random{261002};
    bool pending = false, response = false, write = false;
    uint32_t address = 0, mask = 0, delay = 0;
    std::array<uint32_t, 8> write_data{};
    int uart_count = 0, uart_bit = -1;
    unsigned uart_value = 0;
    bool camera_pending = false;
    uint32_t camera_reply = 0;
    std::array<uint32_t, 64> camera_registers{0, 6, VISION_SIM_CAMERA_BUILD_ID, 0,
        0x06564030, 0x00000202, 0x80016800, 0x004407f4,
        0x06564030, 0x00000202, 0x80016800, 0x004407f4,
        (360U << 16) | 640U, 8, 921600, 27000000};
    unsigned camera_capture_delay = 0, camera_captures = 0, camera_affine_delay = 0;
    static constexpr unsigned clocks_per_bit = VISION_SIM_CPU_HZ/921600;
    static constexpr uint64_t cpu_half_ps = (500000000000ULL + VISION_SIM_CPU_HZ/2)/VISION_SIM_CPU_HZ;
    static constexpr uint64_t ai_half_ps = (500000000000ULL + VISION_SIM_AI_HZ/2)/VISION_SIM_AI_HZ;
    static constexpr uint64_t memory_half_ps = (500000000000ULL + VISION_SIM_MEMORY_HZ/2)/VISION_SIM_MEMORY_HZ;
    std::ofstream cpu_trace;
    std::string trace_path;
    uint32_t writeback_pc = 0, trace_pc = 0;
    bool trace_started = false;

    Simulation() {
        if (const char *path = std::getenv("FPGA_CPU_TRACE")) {
            trace_path = path;
            cpu_trace.open(trace_path);
            const char *pc = std::getenv("FPGA_CPU_TRACE_PC");
            trace_pc = pc ? std::strtoul(pc, nullptr, 0) : 0;
        }
    }

    void step() {
        time += std::min({cpu_half_ps-time%cpu_half_ps, ai_half_ps-time%ai_half_ps,
                         memory_half_ps-time%memory_half_ps});
        const bool cpu = time%(2*cpu_half_ps) < cpu_half_ps;
        const bool ai = time%(2*ai_half_ps) < ai_half_ps;
        const bool mem = time%(2*memory_half_ps) < memory_half_ps;
        const bool memory_edge = mem && !dut.memory_clk;
        const bool cpu_edge = cpu && !dut.cpu_clk;
        const bool retire = cpu_edge && dut.vision_soc__DOT__cpu__DOT__core__DOT__memwb_valid;
        const uint32_t retiring_pc = writeback_pc;
        const uint32_t next_writeback_pc = dut.vision_soc__DOT__cpu__DOT__core__DOT__exmem_pc;
        bool accept = false, consume = false;
        bool camera_accept = false, camera_consume = false;
        unsigned camera_index = 0;
        if (cpu_edge) {
            if (camera_capture_delay && --camera_capture_delay == 0) {
                ++camera_captures;
                ++camera_registers[16];
                ++camera_registers[34];
                camera_registers[35] = ((camera_captures - 1) & 1U) << 2;
                camera_registers[37] = (camera_captures & 1U) << 1;
                const auto base = (camera_captures & 1U) ? 0x3000000U : 0x3400000U;
                for (unsigned offset = 0; offset < 1280U*720U*2U; ++offset)
                    memory[base+offset] = (offset*7U + offset/2560U*13U + camera_captures*31U) & 255U;
            }
            if (camera_affine_delay && --camera_affine_delay == 0) {
                camera_registers[33] = (camera_registers[36] & 1U) ? 0U : 2U;
                ++camera_registers[36];
            }
            dut.camera_req_ready = !camera_pending;
            dut.camera_rsp_valid = camera_pending;
            dut.camera_rsp_rdata = camera_reply;
            dut.camera_rsp_error = 0;
            dut.eval();
            camera_accept = dut.camera_req_valid && dut.camera_req_ready;
            camera_consume = dut.camera_rsp_valid && dut.camera_rsp_ready;
            if (camera_accept) {
                require((dut.camera_req_addr & 0xffffff03U) == 0x21008000U, "Invalid camera peripheral address");
                camera_index = (dut.camera_req_addr >> 2) & 63;
                if (dut.camera_req_write) {
                    require(dut.camera_req_wstrb == 15, "Partial camera peripheral write");
                    if (camera_index == 0) {
                        require(dut.camera_req_wdata <= 1, "Invalid preview control");
                        camera_registers[0] = dut.camera_req_wdata;
                        camera_registers[1] = 6 | dut.camera_req_wdata;
                    } else if (camera_index == 35) {
                        require(dut.camera_req_wdata <= 3, "Invalid frame lock control");
                        camera_registers[35] = (camera_registers[35] & 4U) | dut.camera_req_wdata;
                    } else if (camera_index == 37) {
                        require(dut.camera_req_wdata == 1 && !camera_capture_delay, "Overlapping camera capture");
                        camera_registers[37] |= 1U;
                        camera_capture_delay = 1000;
                    } else if (camera_index == 33) {
                        require(dut.camera_req_wdata == 1 && !camera_affine_delay, "Overlapping camera alignment");
                        camera_registers[33] |= 1U;
                        camera_affine_delay = 1000;
                    } else if (camera_index >= 26 && camera_index <= 32) {
                        camera_registers[camera_index] = dut.camera_req_wdata;
                    } else throw std::runtime_error("Invalid camera peripheral write");
                }
                camera_reply = camera_registers[camera_index];
            }
        }
        if (memory_edge) {
            dut.mem_req_ready = !pending && !response && random()%4 != 0;
            if (pending && delay == 0) {
                for (unsigned i=0; i<32; ++i)
                    if (write && (mask & (1U << i))) memory[address+i] = (write_data[i/4] >> ((i%4)*8)) & 255;
                for (unsigned i=0; i<8; ++i)
                    dut.mem_rsp_rdata[i] = uint32_t(memory[address+i*4]) | uint32_t(memory[address+i*4+1]) << 8 |
                        uint32_t(memory[address+i*4+2]) << 16 | uint32_t(memory[address+i*4+3]) << 24;
                pending = false; response = true;
            } else if (pending) --delay;
            dut.mem_rsp_valid = response;
            dut.eval();
            accept = dut.mem_req_valid && dut.mem_req_ready;
            consume = dut.mem_rsp_valid && dut.mem_rsp_ready;
            if (accept) {
                require(!pending && !response, "Multiple outstanding external requests");
                require(dut.mem_req_addr >= 0x80000000U &&
                        uint64_t(dut.mem_req_addr)-0x80000000U+32 <= memory.size() &&
                        !(dut.mem_req_addr & 31U), "External transfer address is invalid");
                address = dut.mem_req_addr-0x80000000U; mask = dut.mem_req_wstrb; write = dut.mem_req_write;
                for (unsigned i=0; i<8; ++i) write_data[i] = dut.mem_req_wdata[i];
                if (write) ++writes; else ++reads;
            }
        }
        dut.cpu_clk = cpu; dut.ai_clk = ai; dut.memory_clk = mem;
        dut.eval();
        if (cpu_edge) {
            writeback_pc = next_writeback_pc;
            if (retire && cpu_trace.is_open() && (trace_started || retiring_pc == trace_pc)) {
                if (!trace_started) {
                    std::ofstream ram(trace_path+".ram", std::ios::binary);
                    ram.write(reinterpret_cast<const char *>(dut.vision_soc__DOT__cpu__DOT__memory__DOT__data_mem), 32768);
                    trace_started = true;
                }
                cpu_trace << std::hex << retiring_pc;
                for (unsigned reg=0; reg<32; ++reg)
                    cpu_trace << " " << (reg && (dut.vision_soc__DOT__cpu__DOT__core__DOT__regs_valid & (1U << reg)) ?
                        dut.vision_soc__DOT__cpu__DOT__core__DOT__regs[reg] : 0);
                cpu_trace << "\n";
            }
        }
        if (accept) { pending = true; delay = 1+random()%12; }
        if (consume) response = false;
        if (camera_accept) camera_pending = true;
        if (camera_consume) camera_pending = false;
        if (cpu_edge) {
            ++cpu_cycles;
            if (dut.trap_taken) throw std::runtime_error("Unexpected CPU trap at PC="+std::to_string(dut.current_pc));
            if (uart_bit < 0) {
                if (!dut.uart_tx) { uart_bit = 0; uart_count = clocks_per_bit+clocks_per_bit/2; uart_value = 0; }
            } else if (--uart_count == 0) {
                if (uart_bit < 8) {
                    uart_value |= unsigned(dut.uart_tx) << uart_bit;
                    ++uart_bit; uart_count = clocks_per_bit;
                } else {
                    require(dut.uart_tx, "UART response has an invalid stop bit");
                    received.push_back(uart_value); uart_bit = -1;
                }
            }
            // The delta suite adds six full UART round trips. Keep the existing
            // per-request deadline while allowing their cumulative wire time.
            if (cpu_cycles > cycle_cap) throw std::runtime_error("Vision SoC simulation timed out at PC="+std::to_string(dut.current_pc));
        }
    }

    void ticks(unsigned count) {
        const auto target = cpu_cycles+count;
        while (cpu_cycles < target) step();
    }

    void reset() {
        dut.cpu_rst_n = 0; dut.ai_rst_n = 0; dut.memory_rst_n = 0; dut.uart_rx = 1;
        dut.cpu_clk = 0; dut.ai_clk = 0; dut.memory_clk = 0; dut.mem_rsp_valid = 0; dut.mem_rsp_error = 0;
        dut.camera_req_ready = 0; dut.camera_rsp_valid = 0; dut.camera_rsp_rdata = 0; dut.camera_rsp_error = 0;
        dut.eval(); ticks(8);
        dut.cpu_rst_n = 1; dut.ai_rst_n = 1; dut.memory_rst_n = 1;
        ticks(200000);
    }

    void send(const Bytes& data) {
        for (auto value : data) {
            dut.uart_rx = 0; ticks(clocks_per_bit);
            for (unsigned bit=0; bit<8; ++bit) { dut.uart_rx = (value >> bit) & 1; ticks(clocks_per_bit); }
            dut.uart_rx = 1; ticks(clocks_per_bit);
        }
    }

    Bytes request(unsigned op, unsigned sequence, const Bytes& payload, unsigned status=0, bool corrupt=false) {
        Bytes frame;
        store32(frame, 0x31524750); store32(frame, op); store32(frame, sequence); store32(frame, payload.size());
        frame.insert(frame.end(), payload.begin(), payload.end()); store32(frame, crc32(frame));
        if (corrupt) frame.back() ^= 0x40;
        require(received.empty(), "Previous response was not consumed");
        send(frame);
        const auto deadline = cpu_cycles+5000000;
        while (received.size() < 16) { require(cpu_cycles < deadline, "No response header"); ticks(10); }
        const unsigned total = load32(received, 12)+20;
        const auto payload_deadline = cpu_cycles + uint64_t(total)*clocks_per_bit*10 + 5000000;
        while (received.size() < total) { require(cpu_cycles < payload_deadline, "No response payload"); ticks(10); }
        Bytes reply(received.begin(), received.begin()+total);
        received.erase(received.begin(), received.begin()+total);
        require(load32(reply, 0) == 0x31534750 && load32(reply, 4) == op && load32(reply, 8) == sequence,
                "Response header is incorrect");
        std::string detail;
        for (unsigned offset=20; offset+4 <= total-4; offset+=4)
            detail += " "+std::to_string(load32(reply, offset));
        require(load32(reply, 16) == status, "Response op="+std::to_string(op)+" sequence="+std::to_string(sequence)+
                " status="+std::to_string(load32(reply, 16))+" expected="+std::to_string(status)+" details="+detail);
        require(load32(reply, total-4) == crc32(Bytes(reply.begin(), reply.end()-4)), "Response CRC mismatch");
        return Bytes(reply.begin()+20, reply.end()-4);
    }

    /* CoreMark prints plain text, so consume raw UART bytes until the firmware's
       end marker arrives. */
    std::string raw_until(const std::string& marker, uint64_t budget) {
        std::string text;
        const auto deadline = cpu_cycles + budget;
        while (!(text.size() >= marker.size() &&
                 text.compare(text.size()-marker.size(), marker.size(), marker) == 0)) {
            if (!(cpu_cycles < deadline)) {
                /* Keep the tail so a stall on the board-firmware side is visible
                   instead of a bare timeout. */
                const std::string tail = text.size() > 240 ? text.substr(text.size()-240) : text;
                throw std::runtime_error("Benchmark output did not terminate after " +
                    std::to_string(budget) + " cycles, " + std::to_string(text.size()) +
                    " bytes seen, tail: " + tail);
            }
            if (received.empty()) ticks(10);
            else { text.push_back(char(received.front())); received.erase(received.begin()); }
        }
        return text;
    }

    void put(uint32_t addr, uint32_t value) {
        for (unsigned i=0; i<4; ++i) memory[addr-0x80000000U+i] = (value >> (i*8)) & 255;
    }

    Bytes download(uint32_t base, uint32_t count, unsigned& sequence) {
        Bytes result;
        for (unsigned offset=0; offset<count; offset+=2048) {
            Bytes payload; store32(payload, base+offset); store32(payload, std::min(2048U, count-offset));
            const auto reply = request(3, ++sequence, payload);
            result.insert(result.end(), reply.begin(), reply.end());
        }
        return result;
    }
};

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    try {
        require(argc == 3 || argc == 4, "Expected an INT8 directory, zlib cases and optional delta cases");
        const std::string directory = argv[1];
        Simulation sim;
        const auto segments = file_bytes(directory+"/segments.bin");
        unsigned position = 4;
        for (unsigned segment=0; segment<load32(segments, 0); ++segment) {
            const uint32_t address = load32(segments, position), length = load32(segments, position+4);
            position += 8;
            require(address >= 0x80000000U && uint64_t(address)-0x80000000U+length <= sim.memory.size(), "Invalid test segment");
            require(position+length <= segments.size(), "Truncated test segments");
            std::copy_n(segments.begin()+position, length, sim.memory.begin()+address-0x80000000U);
            position += length;
        }
        const auto config = file_bytes(directory+"/config.bin");
        const uint32_t model = 0x80080000, descriptor = model+128;
        sim.put(model, 0x4e4e4350); sim.put(model+4, 1); sim.put(model+8, 1);
        sim.put(model+12, descriptor); sim.put(model+40, model+64); sim.put(model+44, 1);
        for (unsigned reg=0; reg<17; ++reg) sim.put(descriptor+reg*4, load32(config, reg*4));
        sim.reset();
        unsigned sequence = 10;
        const auto ping = sim.request(1, ++sequence, {});
        require(ping.size() == 32 && load32(ping, 0) >= 0x26100131 && load32(ping, 4) == 0x26100123,
                "Firmware version mismatch");
        require(load32(ping, 8) == VISION_SIM_CPU_HZ && load32(ping, 12) == VISION_SIM_AI_HZ &&
                load32(ping, 16) == 921600, "Firmware clock or UART configuration mismatch");
        if (load32(ping, 0) >= 0x26100133) {
            const auto capabilities = sim.request(13, ++sequence, {});
            require(capabilities.size() == 8 && (load32(capabilities, 0) & 2) && load32(capabilities, 4) == 4096,
                    "Compressed-write capabilities are incorrect");
            const auto cases = file_bytes(argv[2]);
            unsigned cursor = 4;
            Bytes last_payload;
            for (unsigned test=0; test<load32(cases, 0); ++test) {
                const auto count = load32(cases, cursor), size = load32(cases, cursor+4);
                cursor += 8;
                require(cursor+count+size <= cases.size(), "Truncated compressed test cases");
                Bytes expected(cases.begin()+cursor, cases.begin()+cursor+count);
                cursor += count;
                Bytes encoded(cases.begin()+cursor, cases.begin()+cursor+size);
                cursor += size;
                Bytes payload; store32(payload, 0x83100001); store32(payload, count);
                payload.insert(payload.end(), encoded.begin(), encoded.end());
                require(payload.size() <= 2048, "Compressed test exceeds the UART request limit");
                require(load32(sim.request(12, ++sequence, payload), 0) == count, "Decompression length mismatch");
                require(sim.download(0x83100001, count, sequence) == expected, "DEFLATE DDR byte mismatch");
                last_payload = payload;
            }
            require(cursor == cases.size(), "Trailing compressed test-case bytes");
            const auto before_invalid = sim.writes;
            auto damaged = last_payload; damaged.back() ^= 1;
            sim.request(12, ++sequence, damaged, 9);
            damaged = last_payload; damaged.pop_back();
            sim.request(12, ++sequence, damaged, 9);
            damaged = last_payload; damaged.push_back(0);
            sim.request(12, ++sequence, damaged, 9);
            damaged = last_payload; damaged[4] = 1; damaged[5] = 0;
            sim.request(12, ++sequence, damaged, 9);
            damaged = last_payload; damaged[4] = 1; damaged[5] = 16;
            sim.request(12, ++sequence, damaged, 3);
            require(sim.writes == before_invalid, "Invalid compressed input changed shared DDR");
            std::cout << "PASS: standard zlib stored/fixed/dynamic blocks, exact unaligned DDR bytes, checksums and bounded decoding\n";
            if (load32(capabilities, 0) & 8) {
                require(argc == 4, "Delta upload requires independent test vectors");
                const auto delta_cases = file_bytes(argv[3]);
                unsigned delta_cursor = 4;
                Bytes delta_last;
                for (unsigned test=0; test<load32(delta_cases, 0); ++test) {
                    const auto count = load32(delta_cases, delta_cursor), size = load32(delta_cases, delta_cursor+4);
                    delta_cursor += 8;
                    require(delta_cursor+count+size <= delta_cases.size(), "Truncated delta vectors");
                    Bytes expected(delta_cases.begin()+delta_cursor, delta_cases.begin()+delta_cursor+count);
                    delta_cursor += count;
                    Bytes payload; store32(payload, 0x83100001); store32(payload, count);
                    payload.insert(payload.end(), delta_cases.begin()+delta_cursor, delta_cases.begin()+delta_cursor+size);
                    delta_cursor += size;
                    require(payload.size() <= 2048, "Delta test exceeds the request limit");
                    require(load32(sim.request(15, ++sequence, payload), 0) == count, "Delta length mismatch");
                    const auto writes_before_duplicate = sim.writes;
                    require(load32(sim.request(15, sequence, payload), 0) == count, "Delta duplicate reply differs");
                    require(sim.writes == writes_before_duplicate, "Delta duplicate repeated DDR writes");
                    require(sim.download(0x83100001, count, sequence) == expected, "Delta DDR bytes differ");
                    std::cout << "PASS: delta case=" << test << " bytes=" << count
                              << " cycles=" << sim.cpu_cycles << "\n";
                    delta_last = payload;
                }
                require(delta_cursor == delta_cases.size(), "Trailing delta vectors");
                const auto writes_before_invalid = sim.writes;
                auto damaged_delta = delta_last; damaged_delta.back() ^= 1;
                sim.request(15, ++sequence, damaged_delta, 9);
                damaged_delta = delta_last; damaged_delta.pop_back();
                sim.request(15, ++sequence, damaged_delta, 9);
                damaged_delta = delta_last; damaged_delta.push_back(0);
                sim.request(15, ++sequence, damaged_delta, 9);
                damaged_delta = delta_last; damaged_delta[4] = 1; damaged_delta[5] = 0;
                sim.request(15, ++sequence, damaged_delta, 9);
                damaged_delta = delta_last; damaged_delta[4] = 1; damaged_delta[5] = 16;
                sim.request(15, ++sequence, damaged_delta, 3);
                sim.request(15, ++sequence, {}, 3);
                require(sim.writes == writes_before_invalid, "Invalid delta input changed DDR");
                std::cout << "PASS: delta uploads, modulo256 wraps, block reset, duplicate suppression, exact bytes and invalid-stream side-effect guards\n";
            }
            Bytes mode; store32(mode, 1);
            if (load32(capabilities, 0) & 1) {
                const auto preview = sim.request(11, ++sequence, mode);
                require(preview.size() == 64 && load32(preview, 8) == VISION_SIM_CAMERA_BUILD_ID,
                        "Camera command returned accelerator registers");
                sim.ticks(5000);
                require(sim.camera_registers[0] == 1, "Preview did not start after the CPU response");
                mode.clear(); store32(mode, 2);
                sim.request(11, ++sequence, mode);
                require(sim.camera_registers[0] == 0, "Preview did not stop before the CPU response");
                mode.clear(); store32(mode, 3);
                sim.request(11, ++sequence, mode, 3);
                std::cout << "PASS: camera MMIO route, preview start/stop and command validation\n";
            } else sim.request(11, ++sequence, mode, 2);
            if (load32(capabilities, 0) & 32U) {
                Bytes slice; store32(slice, 0); store32(slice, 64);
                sim.request(20, ++sequence, slice, 3);
                sim.request(19, ++sequence, Bytes{0}, 3);
                for (unsigned capture = 1; capture <= 2; ++capture) {
                    const auto status = sim.request(19, ++sequence, {});
                    require(status.size() == 32 && load32(status, 0) == capture && load32(status, 24) == capture,
                            "Camera capture counters differ");
                    const auto flags = load32(status, 28);
                    require((flags & 1U) && ((flags >> 1) & 1U) == (capture-1)%2,
                            "Camera capture did not lock the completed bank");
                    slice.clear(); store32(slice, 620); store32(slice, 4092);
                    const auto image = sim.request(20, ++sequence, slice);
                    require(image.size() == 4092, "Camera frame slice length differs");
                    for (unsigned byte = 0; byte < image.size(); ++byte) {
                        const unsigned pixel = (620U+byte)/2U;
                        const unsigned source = (pixel/320U)*10240U + (pixel%320U)*8U + byte%2U;
                        const unsigned expected = (source*7U + source/2560U*13U + capture*31U) & 255U;
                        require(image[byte] == expected, "Camera frame read sampled the wrong pixel or bank");
                    }
                    Bytes affine;
                    for (const auto value : {65536U, 0U, 0U, 0U, 65536U, 0U,
                                            (capture & 1U) ? 0x83000000U : 0x83400000U}) store32(affine, value);
                    for (unsigned face = 0; face < 2; ++face) {
                        const auto alignment = sim.request(18, ++sequence, affine);
                        require(alignment.size() == 8 && load32(alignment, 0) == (capture-1)*2+face+1,
                                "Affine completion did not advance");
                        require(sim.camera_registers[35] == flags, "Alignment released the source frame lock");
                    }
                    sim.ticks(5000);
                    require(sim.request(17, ++sequence, {}) == status, "Camera status changed without a new capture");
                }
                for (const auto values : {std::array<uint32_t, 2>{1, 2}, {0, 3}, {0, 4094},
                                          {115200, 2}, {0xfffffff0U, 64}}) {
                    Bytes invalid; store32(invalid, values[0]); store32(invalid, values[1]);
                    sim.request(20, ++sequence, invalid, 3);
                }
                require(sim.camera_captures == 2, "Readback or alignment unexpectedly recaptured the camera");
                std::cout << "PASS: real firmware coherent capture, sampled frame readback, alternating banks, retained multi-face lock and invalid ranges\n";
            }
        }
        Bytes pattern(2044);
        for (unsigned i=0; i<pattern.size(); ++i) pattern[i] = (i*173+19) & 255;
        Bytes payload; store32(payload, 0x83000001);
        payload.insert(payload.end(), pattern.begin(), pattern.end());
        require(load32(sim.request(2, ++sequence, payload), 0) == pattern.size(), "Upload length mismatch");
        require(sim.download(0x83000001, pattern.size(), sequence) == pattern, "CPU DDR upload/readback mismatch");
        const auto old_writes = sim.writes;
        payload.back() ^= 1;
        sim.request(2, ++sequence, payload, 1, true);
        require(sim.writes == old_writes, "Bad CRC modified DDR");
        Bytes invalid; store32(invalid, 0xbfffffff); store32(invalid, 2);
        sim.request(3, ++sequence, invalid, 3);
        std::cout << "PASS: UART continuous upload, CRC, unaligned masked DDR writes and invalid ranges\n";
        if (load32(ping, 0) >= 0x26101301) {
            const uint32_t base = 0x83200001, width = 37, height = 3, channels = 4;
            const uint32_t row = 64, plane = 224;
            Bytes logical;
            for (unsigned c=0; c<channels; ++c) for (unsigned y=0; y<height; ++y)
                for (unsigned x=0; x<width; ++x) {
                    const unsigned char value = (c*67+y*43+x*13)&255;
                    sim.memory[base-0x80000000U+c*plane+y*row+x] = value;
                    logical.push_back(value);
                }
            const auto packed_request = [&](const std::array<uint32_t, 8>& fields, unsigned status=0) {
                Bytes data;
                for (auto field:fields) store32(data, field);
                return sim.request(14, ++sequence, data, status);
            };
            auto fields = std::array<uint32_t, 8>{base,width,height,channels,row,plane,0,uint32_t(logical.size())};
            require(packed_request(fields) == logical, "Packed NCHW bytes differ at row/plane boundaries");
            fields[6] = 7; fields[7] = 243;
            require(packed_request(fields) == Bytes(logical.begin()+7, logical.begin()+250),
                    "Packed tensor offset/count differs across rows and channels");
            fields[6] = logical.size()-1; fields[7] = 1;
            require(packed_request(fields) == Bytes{logical.back()}, "Packed tensor final byte differs");
            const auto before_invalid_packed = sim.writes;
            const auto valid_fields = fields;
            for (unsigned index: {1U,2U,3U,7U}) {
                fields = valid_fields; fields[index] = 0; packed_request(fields, 3);
            }
            fields = valid_fields; fields[4] = width-1; packed_request(fields, 3);
            fields = valid_fields; fields[5] = height*row-1; packed_request(fields, 3);
            fields = valid_fields; fields[6] = logical.size(); packed_request(fields, 3);
            fields = valid_fields; fields[7] = 2; packed_request(fields, 3);
            fields = valid_fields; fields[0] = 0xbfffffff; packed_request(fields, 3);
            fields = valid_fields; fields[7] = 4093; packed_request(fields, 3);
            fields = valid_fields; fields[1] = fields[2] = fields[4] = fields[5] = 0xffffffff;
            packed_request(fields, 3);
            fields = valid_fields; fields[3] = fields[5] = 0xffffffff; packed_request(fields, 3);
            sim.request(14, ++sequence, {}, 3);
            require(sim.writes == before_invalid_packed, "Invalid packed reads changed DDR");
            const uint32_t vector_base = 0x83201001;
            Bytes compact_vector;
            for (unsigned c=0; c<128; ++c) {
                sim.memory[vector_base-0x80000000U+c*32] = (c*19)&255;
                compact_vector.push_back((c*19)&255);
            }
            fields = {vector_base,1,1,128,32,32,0,128};
            require(packed_request(fields) == compact_vector, "Packed one-pixel feature planes differ");
            std::cout << "PASS: padding-free NCHW UART readback, unaligned rows/planes/tails, overflow and invalid-range guards\n";
        }
        payload.clear(); store32(payload, model); store32(payload, 0); store32(payload, 1);
        const auto run = sim.request(4, ++sequence, payload);
        require(run.size() == 40 && load32(run, 0) == 1 && load32(run, 12) == 0, "Layer profile mismatch");
        const auto after_run_writes = sim.writes;
        require(sim.request(4, sequence, payload) == run && sim.writes == after_run_writes,
                "Duplicate request reran a completed job");
        const auto expected = file_bytes(directory+"/expected.bin");
        require(sim.download(load32(config, 19*4), expected.size(), sequence) == expected,
                "CPU-scheduled accelerator output mismatch");
        std::cout << "PASS: RISC-V descriptor schedule, three clocks, delayed DDR, cache invalidation and exact layer bytes\n";
        sim.put(descriptor+6*4, 0);
        sim.request(4, ++sequence, payload, 5);
        std::cout << "PASS: Invalid model geometry returned an engine error without a CPU trap\n";

        const uint32_t feature_model = 0x80090000, tensor = 0x80300000;
        sim.put(feature_model, 0x4e4e4350); sim.put(feature_model+4, 1); sim.put(feature_model+8, 1);
        sim.put(feature_model+12, feature_model+128); sim.put(feature_model+40, feature_model+64); sim.put(feature_model+44, 1);
        std::array<uint32_t, 9> metadata{tensor, 128, 1, 1, 32, 32, 0x3c000000, 127, 0};
        for (unsigned i=0; i<metadata.size(); ++i) sim.put(feature_model+64+i*4, metadata[i]);
        for (unsigned i=0; i<128; ++i) sim.memory[tensor-0x80000000+i*32] = (i*3)%251;
        payload.clear(); store32(payload, feature_model);
        const auto unknown = sim.request(7, ++sequence, payload);
        require(unknown.size() == 340 && load32(unknown, 0) == 0xffffffff && load32(unknown, 8) == 0,
                "Empty gallery matching is incorrect");
        Bytes identity; store32(identity, 0);
        require(load32(sim.request(6, ++sequence, identity), 0) == 1, "Enrollment failed");
        const auto match = sim.request(7, ++sequence, payload);
        require(load32(match, 0) == 0 && int32_t(load32(match, 4)) > 32760,
                "CPU normalization or gallery cosine is incorrect");
        require(std::equal(unknown.begin()+20, unknown.begin()+276, match.begin()+20), "Feature normalization is unstable");
        std::cout << "PASS: RISC-V software division, feature normalization, enrollment and Q15 gallery matching\n";
        require(load32(sim.request(10, ++sequence, {}), 0) == 18022, "Default threshold changed");
        Bytes threshold; store32(threshold, 32768);
        sim.request(10, ++sequence, threshold, 3);
        sim.request(10, ++sequence, Bytes{0}, 3);
        sim.request(8, ++sequence, {});
        identity.clear(); store32(identity, 0);
        for (unsigned index=0; index<128; ++index) {
            const auto value = int16_t(uint16_t(unknown[20+2*index]) | uint16_t(unknown[21+2*index]) << 8);
            const auto scaled = int16_t(value*2/5);
            identity.push_back(uint16_t(scaled) & 255);
            identity.push_back(uint16_t(scaled) >> 8);
        }
        sim.request(6, ++sequence, identity);
        require(load32(sim.request(7, ++sequence, payload), 0) == 0xffffffff, "Conservative threshold did not reject");
        threshold.clear(); store32(threshold, 9612);
        require(load32(sim.request(10, ++sequence, threshold), 0) == 9612, "Threshold update failed");
        require(load32(sim.request(7, ++sequence, payload), 0) == 0, "Training threshold did not accept");
        require(load32(sim.request(10, ++sequence, {}), 0) == 9612, "Threshold readback failed");
        std::cout << "PASS: runtime Q15 threshold, invalid settings and changed CPU matching decisions\n";
        if (load32(sim.request(13, ++sequence, {}), 0) & 128u) {
            Bytes iterations; store32(iterations, 1u);
            require(load32(sim.request(22, ++sequence, iterations), 0) == 1u,
                    "Benchmark rejected the iteration count");
            /* CoreMark prints its whole banner through the 921600-baud UART, which
               costs ~1,460 CPU ticks per byte, so this one test needs a larger
               runaway allowance than the rest of the suite. */
            const auto bench_start = sim.cpu_cycles;
            const auto saved_cap = sim.cycle_cap;
            sim.cycle_cap = bench_start + 80000000u;
            const std::string text = sim.raw_until("#BENCH-END\r\n", 80000000u);
            sim.cycle_cap = saved_cap;
            require(text.find("CoreMark") != std::string::npos && text.find("seedcrc") != std::string::npos,
                    "CoreMark printed no result banner");
            /* One iteration cannot reach CoreMark's 10 s minimum, so upstream prints
               "ERROR! Must execute for at least 10 secs" and withholds the
               "Correct operation validated" line. The CRCs alone decide whether the
               port computes correctly; the 10 s rule is asserted on the board run. */
            /* The four CRCs are iteration independent and no other line prints these
               values, so matching them avoids depending on the banner's alignment. */
            for (const auto& crc : {"0xe9f5", "0xe714", "0x1fd7", "0x8e3a"})
                require(text.find(crc) != std::string::npos,
                        std::string("CoreMark CRC is wrong or missing: ")+crc);
            require(load32(sim.request(1, ++sequence, {}), 0) >= 0x26102A00u,
                    "Firmware did not answer after the benchmark");
            std::cout << "PASS: stock CoreMark runs inside the application image and the protocol survives it"
                      << " bench_cycles=" << sim.cpu_cycles-bench_start << " bytes=" << text.size() << "\n";
        }
        const auto counters = sim.request(9, ++sequence, {});
        require(counters.size() == 28 && load32(counters, 24) == 0, "UART receive errors occurred");
        std::cout << "PASS: complete vision SoC cycles=" << sim.cpu_cycles << " reads=" << sim.reads << " writes=" << sim.writes << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << "\n";
        return 1;
    }
}
