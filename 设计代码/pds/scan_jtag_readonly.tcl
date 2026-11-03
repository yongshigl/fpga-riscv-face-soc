if {[catch {cfg_connect -ip 127.0.0.1 -port 65450} error]} {
    puts "JTAG_CONNECT_FAILED: $error"
    exit 2
}
if {[catch {cfg_scan_chain} error]} {
    puts "JTAG_SCAN_FAILED: $error"
    catch {cfg_disconnect}
    exit 3
}
puts "JTAG_SCAN_OK"
cfg_disconnect
