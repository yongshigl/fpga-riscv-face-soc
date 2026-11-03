#include <cstdint>
#include <iostream>
#ifdef COREMARK_PROFILE
#include <algorithm>
#include <unordered_map>
#include <vector>
#endif

#include "Vcoremark_soc_top.h"
#include "verilated.h"

int main(int argc, char **argv)
{
    Verilated::commandArgs(argc, argv);
    Vcoremark_soc_top top;

    top.clk = 0;
    top.rst_n = 0;
    for (int reset_cycle = 0; reset_cycle < 4; ++reset_cycle) {
        top.clk = 0;
        top.eval();
        top.clk = 1;
        top.eval();
    }
    top.rst_n = 1;

    constexpr std::uint64_t max_cycles = 50'000'000;
    bool left_reset_vector = false;
#if defined(COREMARK_RAM_BYTES) || defined(COREMARK_COMPACT_8K) || defined(COREMARK_COMPACT_4K) || defined(COREMARK_COMPACT_3K)
#ifdef COREMARK_RAM_BYTES
    constexpr std::uint32_t ram_top = 0x10000000u + COREMARK_RAM_BYTES;
#elif defined(COREMARK_COMPACT_3K)
    constexpr std::uint32_t ram_top = 0x10000c00u;
#elif defined(COREMARK_COMPACT_4K)
    constexpr std::uint32_t ram_top = 0x10001000u;
#else
    constexpr std::uint32_t ram_top = 0x10002000u;
#endif
    std::uint32_t min_sp = ram_top;
    bool stack_initialized = false;
#endif
#ifdef COREMARK_PROFILE
    struct PcStats { std::uint64_t total = 0; std::uint64_t misses = 0; };
    std::unordered_map<std::uint32_t, PcStats> pc_stats;
    std::uint64_t branch_count = 0, jal_count = 0, jalr_count = 0;
    std::uint64_t branch_misses = 0, jal_misses = 0, jalr_misses = 0;
    std::uint64_t load_stalls = 0;
    std::uint64_t memory_stalls = 0, store_waits = 0, load_waits = 0;
    std::uint64_t fetch_waits = 0, frontend_bubbles = 0;
#endif
    for (std::uint64_t cycle = 0; cycle < max_cycles; ++cycle) {
        top.clk = 0;
        top.eval();
#if defined(COREMARK_RAM_BYTES) || defined(COREMARK_COMPACT_8K) || defined(COREMARK_COMPACT_4K) || defined(COREMARK_COMPACT_3K)
        if (top.debug_sp == ram_top) stack_initialized = true;
        if (stack_initialized && top.debug_sp < min_sp) min_sp = top.debug_sp;
        if (stack_initialized &&
            (top.debug_sp < 0x10000800u || top.debug_sp > ram_top)) {
            std::cerr << "\nFAIL: compact CoreMark stack out of range sp=0x"
                      << std::hex << top.debug_sp << std::dec << '\n';
            return 1;
        }
        if (top.debug_dmem_valid && top.debug_dmem_addr >= 0x10000000u &&
            top.debug_dmem_addr < 0x20000000u &&
            top.debug_dmem_addr >= ram_top) {
            std::cerr << "\nFAIL: compact CoreMark RAM access out of range addr=0x"
                      << std::hex << top.debug_dmem_addr << std::dec << '\n';
            return 1;
        }
#endif
#ifdef COREMARK_PROFILE
        if (top.profile_load_stall) ++load_stalls;
        if (top.profile_memory_stall) {
            ++memory_stalls;
            if (top.profile_store) ++store_waits;
            if (top.profile_load) ++load_waits;
        }
        if (top.profile_fetch_wait) ++fetch_waits;
        if (top.profile_frontend_bubble) ++frontend_bubbles;
        if (top.profile_idex_valid &&
            (top.profile_branch || top.profile_jal || top.profile_jalr)) {
            auto &stats = pc_stats[top.profile_ex_pc];
            ++stats.total;
            if (top.profile_mispredict) ++stats.misses;
            if (top.profile_branch) {
                ++branch_count;
                if (top.profile_mispredict) ++branch_misses;
            } else if (top.profile_jal) {
                ++jal_count;
                if (top.profile_mispredict) ++jal_misses;
            } else {
                ++jalr_count;
                if (top.profile_mispredict) ++jalr_misses;
            }
        }
#endif
        top.clk = 1;
        top.eval();

        if (top.uart_debug_strobe)
            std::cout << static_cast<char>(top.uart_debug_data) << std::flush;

        if (top.current_pc > 0x100u)
            left_reset_vector = true;
        if (left_reset_vector && top.current_pc == 0u) {
            std::cerr << "\nDEBUG: unexpected reset-vector fetch cycle=" << cycle
                      << " ra=0x" << std::hex << top.debug_ra
                      << " sp=0x" << top.debug_sp
                      << " stack_ra=0x" << top.debug_stack_ra << std::dec << '\n';
            return 2;
        }

        if (top.trap_taken) {
            std::cerr << "\nFAIL: CoreMark trapped at pc=0x" << std::hex
                      << top.current_pc << std::dec << '\n';
            return 1;
        }

        if (top.gpio_out == 0x0000c0deu) {
#ifdef COREMARK_CACHE
            std::cerr << "CACHE I hits=" << top.icache_hits << " misses=" << top.icache_misses
                      << " D hits=" << top.dcache_hits << " misses=" << top.dcache_misses << '\n';
#endif
#ifdef COREMARK_PROFILE
            std::vector<std::pair<std::uint32_t, PcStats>> hotspots(pc_stats.begin(), pc_stats.end());
            std::sort(hotspots.begin(), hotspots.end(), [](const auto &a, const auto &b) {
                return a.second.misses > b.second.misses;
            });
            std::cerr << "PROFILE branches=" << branch_count << " misses=" << branch_misses
                      << " jal=" << jal_count << " misses=" << jal_misses
                      << " jalr=" << jalr_count << " misses=" << jalr_misses
                      << " load_stalls=" << load_stalls << '\n';
            std::cerr << "PROFILE memory_stalls=" << memory_stalls
                      << " store_waits=" << store_waits << " load_waits=" << load_waits
                      << " fetch_waits=" << fetch_waits
                      << " frontend_bubbles=" << frontend_bubbles << '\n';
            for (std::size_t i = 0; i < std::min<std::size_t>(hotspots.size(), 20); ++i)
                std::cerr << "PROFILE pc=0x" << std::hex << hotspots[i].first << std::dec
                          << " total=" << hotspots[i].second.total
                          << " misses=" << hotspots[i].second.misses << '\n';
#endif
            const std::uint64_t cycles = top.cycle_count;
            const std::uint64_t instructions = top.instret_count;
            const std::uint64_t cpi_milli = instructions == 0 ? 0 :
                (cycles * 1000u) / instructions;
            std::cout << "\nPASS: CoreMark CRC validated cycles=" << cycles
                      << " instret=" << instructions
                      << " CPI=" << (cpi_milli / 1000u) << '.'
                      << (cpi_milli / 100u) % 10u
                      << (cpi_milli / 10u) % 10u
                      << cpi_milli % 10u << '\n';
#if defined(COREMARK_RAM_BYTES) || defined(COREMARK_COMPACT_8K) || defined(COREMARK_COMPACT_4K) || defined(COREMARK_COMPACT_3K)
            std::cout << "PASS: compact RAM stack minimum=0x" << std::hex
                      << min_sp << std::dec << '\n';
#endif
            return 0;
        }

        if (top.gpio_out == 0x0000deadu) {
            std::cerr << "\nFAIL: CoreMark reported validation errors\n";
            return 1;
        }

        if (cycle != 0 && cycle % 1'000'000u == 0)
            std::cerr << "CoreMark progress: " << cycle << " cycles, pc=0x"
                      << std::hex << top.current_pc << std::dec << '\n';
    }

    std::cerr << "\nFAIL: CoreMark timeout at pc=0x" << std::hex
              << top.current_pc << std::dec << '\n';
    return 1;
}
