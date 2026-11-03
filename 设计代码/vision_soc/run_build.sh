#!/bin/bash
# Full flow for the split-camera candidate: synthesize, place, route, bitstream.
# cwd must be this project directory (the tcl uses relative src/ paths).
set -e
cd "$(dirname "$0")"
# 按本机安装设置 PDS 路径与许可证，例如：
#   export PDS_SHELL=/opt/pango/PDS_2022.2-SP6.4/bin/pds_shell
#   export PANGO_LICENSE_FILE=/opt/pango/license/license.lic
exec "${PDS_SHELL:-pds_shell}" -file build_vision.tcl
