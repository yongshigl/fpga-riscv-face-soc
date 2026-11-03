# 设计代码说明

本目录是两份位流（只含处理器的评测位流、运行识别应用的交付位流）对应的完整源码快照，按“位流 ↔ 源码”一一对应组织。目录同时保留了构建脚本、固件、测试平台和必要的工程配置，便于复现设计。
`cpu_soc/` 与 `vision_soc/` 各带一份 `SHA256SUMS`，根目录的 `SHA256SUMS_ALL` 覆盖全部文件，可以逐字节核对源码与位流是否为同一份。

## 目录

| 目录 | 内容 | 对应位流 |
|---|---|---|
| `cpu_soc/` | 只含处理器的评测 SoC：RTL、约束、PDS 流程脚本、ROM 镜像、布线报告（`.prr` / `.rtr`）、位流 | 评测位流，162.000 MHz |
| `vision_soc/` | 完整视觉 SoC：处理器、INT8 加速器、硬件仿射、双摄采集、DDR3，以及布线报告与位流 | 交付位流，CPU 135 MHz / 加速器 72 MHz |
| `sw/` | 固件：视觉固件 `vision_firmware.c`（22 条串口命令，含 21 号检测解码与 22 号 CoreMark）、YuNet 输出解码 `vision_yunet.c`、启动代码与链接脚本 | — |
| `coremark_port/` | CoreMark 移植层（`ee_printf`、计时、迭代次数旋钮），不改动官方源码 | — |
| `third_party/` | CoreMark 官方源码、miniz，均保留原许可证 | — |
| `host/` | 主机侧：串口协议栈、网页控制台 `demo_server.py`、模型量化导出、LFW 评测、上板验证脚本、主机侧测试 | — |
| `tools/` | 固件构建 `prepare_vision.py`、SoC 回归 `verify_vision_soc.py`、片内逻辑分析仪波形解析 `analyze_ila_wdf.py` | — |
| `tb/` | 47 个模块级测试平台：指令、冒险、分支预测、异常与 CSR、缓存、跨时钟域、加速器算术等 | — |
| `pds/` | PDS 流程脚本与 JTAG 易失配置脚本 `program_frozen_volatile.ps1` | — |

## 两份位流的关系

两份位流中的 `rv32i_pipeline_core.v` 出自同一份源码：`vision_soc/src/` 中是较早的版本，`cpu_soc/src/` 中的版本在此基础上加入了深度为 2 的取指队列和带异或的分支目标缓冲索引，`fence.i` 的触发条件也略有不同（`diff` 两个文件可以看到全部差别）。其余差异是编译期参数（设计报告 2.2.5.8 节）：

| 参数 | 评测位流 | 交付位流 |
|---|---|---|
| 取指缓冲 | 深度 2 的取指队列 | 1 级取指寄存器 |
| 分支目标缓冲 / 返回地址栈 | 64 项（PC[7:2] 与 PC[9:8] 异或索引）/ 8 项 | 32 项（PC[6:2] 索引）/ 8 项 |
| 提前发起载入 | 开 | 关 |
| 指令缓存 / 数据缓存 | 4 KB / 1 KB，两路组相联 | 4 KB / 2 KB，两路组相联 |
| 片上 ROM / RAM | 16 KB / 5 KB | 32 KB / 32 KB |
| 固件编译 | `-O3` 等 24 项选项 | `-Os` |

两份位流的 CoreMark 分别报告，不互相换算：评测位流 2.770148 CoreMark/MHz（162 MHz），交付位流 1.491932 CoreMark/MHz（135 MHz，与识别应用为同一个 `.sbit`）。

仓库中另有一个 `fpga-riscv/rtl/` 目录，那是早期的固定参数版本，不是这两份位流的源码；本目录以位流快照为准。

## 重建

```bash
# 1. 视觉固件（与上板 ROM 镜像逐字节一致；--benchmark 把 CoreMark 编进同一镜像）
python3 tools/prepare_vision.py --output build/firmware \
    --cpu-hz 135000000 --ai-hz 72000000 --optimization=-Os \
    --cameras --camera-tensor --face-decode --delta-compressed-write \
    --reciprocal-normalization --fast-crc32 --benchmark \
    --rom-bytes 32768 --linker-script sw/vision_bench_32k.ld --coremark-optimization=-Os

# 2. 交付位流：综合、映射、布局布线、时序、位流
cd vision_soc && bash run_build.sh
sha256sum pg2l200h_mini_vision_top.sbit
#   A2AA956C09C46F767258D50057D5E6E9CECE62F142350C2F3C12A9AF2D2407A8

# 3. 评测位流（布局种子 447、布线种子 3）
cd cpu_soc && pds_shell -f build_cpu.tcl
#    其中的 CoreMark 固件由 tools/prepare_coremark.py 构建，额外的 15 个编译选项记录在
#    cpu_soc/firmware.json 的 compiler_flags 中；按该数组重建，text/data 与 mem/ 逐字节一致

# 4. SoC 周期精确回归（指定快照中的 src；缓存几何用脚本默认值，即上板的 4 KB / 2 KB）
#    第 1 步的输出是 build/firmware，脚本的默认值与之对应
python3 tools/verify_vision_soc.py --rtl-directory vision_soc/src \
    --cpu-core rv32i_pipeline_core.v --cpu-soc rv32i_cached_soc.v
#    加 --case <可执行文件> 才对 INT8 通路做仿真；该测试程序由 tb/sim_int8.cpp 构建，
#    没有时脚本只编译 SoC 并核对固件与源码哈希
```

## 上板运行

1. 用 `pds/program_frozen_volatile.ps1` 以 JTAG 易失方式配置位流（不写 Flash，掉电恢复）；
2. 评测位流：串口 115200，上电自动运行 7,500 轮 CoreMark，约 17 s；
3. 交付位流：启动网页控制台，浏览器打开 `http://localhost:8086`；
   ```bash
   python3 host/demo_server.py --port COM7 --mode vision --http-port 8086
   ```
4. 在同一次配置下跑 CoreMark（22 号命令），脚本会校验四项 CRC 并在结束后重新 ping 板子：
   ```bash
   python3 host/verify_benchmark_board.py --port COM7
   ```

人脸库保存在板内 RAM，断电或复位后清空，入库与识别需在同一次上电内完成。

## 未随附的内容

LFW 数据集与 YuNet / MobileFaceNet 的 INT8 量化模型体积较大，不在本目录中；`host/fetch_lfw.py` 与 `host/fetch_assets.py` 可重新下载与生成。

PDS 的器件原语（`GTP_GPLL`、`GTP_CLKBUF*`、`ips2l_ddrphy_*` 等）随 PDS 安装提供，不在本目录；PDS 综合不受影响，用 Verilator 单独 lint 时需要把它们作为黑盒跳过（`verify_vision_soc.py` 已处理）。两个顶层文件没有 `` `timescale ``，与子模块混用会触发 TIMESCALEMOD 提示，`verify_vision_soc.py` 以 `-Wno-TIMESCALEMOD` 屏蔽。
