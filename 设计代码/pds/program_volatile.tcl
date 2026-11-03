if {![info exists ::env(FPGA_BITSTREAM)] || ![file isfile $::env(FPGA_BITSTREAM)]} {
    puts "BITSTREAM_NOT_FOUND"
    exit 1
}
if {[catch {
    cfg_connect -ip 127.0.0.1 -port 65450
    cfg_scan_chain
    cfg_assign_file -file $::env(FPGA_BITSTREAM) -device_index 0
    cfg_program -device_index 0
} error]} {
    puts "VOLATILE_PROGRAM_FAILED: $error"
    catch {cfg_disconnect}
    exit 2
}
puts "FPGA_VOLATILE_PROGRAM_COMPLETE"
cfg_disconnect
