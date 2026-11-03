set bitstream [file join [file dirname [file normalize [info script]]] generate_bitstream pg2l200h_mini_coremark_top.sbit]

if {![file isfile $bitstream]} {
    puts "BITSTREAM_NOT_FOUND: $bitstream"
    exit 1
}
if {[catch {cfg_connect -ip 127.0.0.1 -port 65450} error]} {
    puts "JTAG_CONNECT_FAILED: $error"
    exit 2
}
if {[catch {cfg_scan_chain} error]} {
    puts "JTAG_SCAN_FAILED: $error"
    exit 3
}
if {[catch {cfg_assign_file -file $bitstream -device_index 0} error]} {
    puts "BITSTREAM_ASSIGN_FAILED: $error"
    catch {cfg_disconnect}
    exit 4
}
if {[catch {cfg_program -device_index 0} error]} {
    puts "FPGA_PROGRAM_FAILED: $error"
    catch {cfg_disconnect}
    exit 5
}
puts "FPGA_VOLATILE_PROGRAM_COMPLETE"
cfg_disconnect
