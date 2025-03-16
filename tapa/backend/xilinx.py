"""Xilinx backend of TAPA."""

__copyright__ = """
Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""

import argparse
import enum
import glob
import logging
import os
import shlex
import subprocess
import tarfile
import tempfile
import xml.sax.saxutils
import zipfile
from collections.abc import Iterable
from types import TracebackType
from typing import BinaryIO, NamedTuple, TextIO
from xml.etree import ElementTree as ET

_logger = logging.getLogger().getChild(__name__)


class Cat(enum.Enum):
    SCALAR = 0
    MMAP = 1
    ISTREAM = 2
    OSTREAM = 3


class Arg(NamedTuple):
    cat: Cat
    name: str  # name of the argument
    port: str  # name of the port to which the argument is connected to
    ctype: str
    width: int


class Vivado(subprocess.Popen):
    """Call vivado with the given Tcl commands and arguments.

    This is a subclass of subprocess.Popen. A temporary directory will be created
    and used as the working directory.

    Args:
      commands: A string of Tcl commands.
      args: Iterable of strings as arguments to the Tcl commands.
    """

    def __init__(self, commands: str, *args: Iterable[str]) -> None:
        self.cwd = tempfile.TemporaryDirectory(prefix="vivado-")
        with open(
            os.path.join(self.cwd.name, "commands.tcl"),
            mode="w+",
            encoding="locale",
        ) as tcl_file:
            tcl_file.write(commands)
        cmd_args = [
            "vivado",
            "-mode",
            "batch",
            "-source",
            tcl_file.name,
            "-nojournal",
            "-tclargs",
            *args,
        ]
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": os.environ
            | {
                "HOME": self.cwd.name,
            },
        }
        cmd_args = get_cmd_args(cmd_args, ["XILINX_VIVADO"], kwargs)
        super().__init__(cmd_args, cwd=self.cwd.name, **kwargs)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc_value, traceback)
        self.cwd.cleanup()


class VivadoHls(subprocess.Popen):
    """Call vivado_hls with the given Tcl commands.

    This is a subclass of subprocess.Popen. A temporary directory will be created
    and used as the working directory.

    Args:
      commands: A string of Tcl commands.
      hls: Either 'vivado_hls' or 'vitis_hls'.
    """

    def __init__(self, commands: str, hls: str = "vivado_hls", cwd: str = "") -> None:
        if cwd:
            self.cwd = cwd
        else:
            self.cwd = tempfile.TemporaryDirectory(prefix=f"{hls}-")
            cwd = self.cwd.name
        with open(
            os.path.join(cwd, "commands.tcl"), mode="w+", encoding="locale"
        ) as tcl_file:
            tcl_file.write(commands)
        cmd_args = [hls, "-f", tcl_file.name]
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": os.environ
            | {
                "HOME": cwd,
            },
        }
        if hls == "vitis_hls":
            cmd_args = get_cmd_args(cmd_args, ["XILINX_HLS", "XILINX_VITIS"], kwargs)
        elif hls == "vivado_hls":
            cmd_args = get_cmd_args(cmd_args, ["XILINX_VIVADO"], kwargs)
        super().__init__(cmd_args, cwd=cwd, **kwargs)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc_value, traceback)
        if isinstance(self.cwd, tempfile.TemporaryDirectory):
            self.cwd.cleanup()


PACKAGEXO_COMMANDS = r"""
set tmp_ip_dir "{tmpdir}/tmp_ip_dir"
set tmp_project "{tmpdir}/tmp_project"

create_project -force kernel_pack ${{tmp_project}}{part_num}
add_files {{
  {src_files}
}}
foreach tcl_file [glob -nocomplain {hdl_dir}/*.tcl {hdl_dir}/*/*.tcl] {{
  source ${{tcl_file}}
}}
set_property top {top_name} [current_fileset]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
ipx::package_project -root_dir ${{tmp_ip_dir}} -vendor tapa \
        -library xrtl -taxonomy /KernelIP -import_files -set_current false
ipx::unload_core ${{tmp_ip_dir}}/component.xml
ipx::edit_ip_in_project -upgrade true -name tmp_edit_project \
        -directory ${{tmp_ip_dir}} ${{tmp_ip_dir}}/component.xml
set_property core_revision 2 [ipx::current_core]
foreach up [ipx::get_user_parameters] {{
  ipx::remove_user_parameter [get_property NAME ${{up}}] [ipx::current_core]
}}
set_property sdx_kernel true [ipx::current_core]
set_property sdx_kernel_type rtl [ipx::current_core]
ipx::create_xgui_files [ipx::current_core]
{bus_ifaces}
set_property xpm_libraries {{XPM_CDC XPM_MEMORY XPM_FIFO}} [ipx::current_core]
set_property supported_families {{ }} [ipx::current_core]
set_property auto_family_support_level level_2 [ipx::current_core]
ipx::update_checksums [ipx::current_core]
ipx::save_core [ipx::current_core]
close_project -delete

package_xo -force -xo_path "{xo_file}" -kernel_name {top_name} \
        -ip_directory ${{tmp_ip_dir}} -kernel_xml {kernel_xml}{cpp_kernels}
"""

BUS_IFACE = r"""
ipx::associate_bus_interfaces -busif {} -clock ap_clk [ipx::current_core]
"""

BUS_PARAM = """\
set_property value {2} [ipx::add_bus_parameter {1} [ipx::get_bus_interfaces {0}]]
"""

S_AXI_NAME = "s_axi_control"
M_AXI_PREFIX = "m_axi_"


class PackageXo(Vivado):
    """Packages the given files into a Xilinx hardware object.

    This is a subclass of subprocess.Popen. A temporary directory will be created
    and used as the working directory.

    Args:
      xo_file: Name of the generated xo file.
      top_name: Top-level module name.
      kernel_xml: Name of a xml file containing description of the kernel.
      hdl_dir: Directory name containing all HDL files.
      m_axi_names: Variable names connected to the m_axi bus, optionally with
          values being key-value pairs of additional bus parameters.
      iface_names: Other interface names, default to (S_AXI_NAME,).
      cpp_kernels: File names of C++ kernels.
      part_num: Part number of the target device.
    """

    def __init__(  # noqa: PLR0913,PLR0917
        self,
        xo_file: str,
        top_name: str,
        kernel_xml: str,
        hdl_dir: str,
        m_axi_names: Iterable[str] | dict[str, dict[str, str]] = (),
        iface_names: Iterable[str] = (S_AXI_NAME,),
        cpp_kernels: Iterable[str] = (),
        part_num: str = "",
    ) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="package-xo-")
        if _logger.isEnabledFor(logging.DEBUG):
            for _, _, files in os.walk(hdl_dir):
                for filename in files:
                    _logger.debug("packing: %s", filename)

        bus_ifaces: list[str] = list(map(BUS_IFACE.format, iface_names))
        for m_axi_name in m_axi_names:
            m_axi_iface_name = M_AXI_PREFIX + m_axi_name
            bus_ifaces.append(BUS_IFACE.format(m_axi_iface_name))
            if not isinstance(m_axi_names, dict):
                continue
            for key, value in m_axi_names.get(m_axi_name, {}).items():
                bus_ifaces.append(BUS_PARAM.format(m_axi_iface_name, key, value))

        # get all files under hdl_dir that does not ends with .tcl
        all_files = glob.glob(f"{hdl_dir}/**", recursive=True)
        src_files = [f for f in all_files if not f.endswith(".tcl")]

        kwargs = {
            "top_name": top_name,
            "kernel_xml": kernel_xml,
            "src_files": " ".join(src_files),
            "hdl_dir": hdl_dir,
            "xo_file": xo_file,
            "bus_ifaces": "".join(bus_ifaces),
            "tmpdir": self.tmpdir.name,
            "cpp_kernels": "".join(map(" -kernel_files {}".format, cpp_kernels)),
            "part_num": f" -part {part_num}" if part_num else "",
        }
        super().__init__(PACKAGEXO_COMMANDS.format(**kwargs))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc_value, traceback)
        self.tmpdir.cleanup()


HLS_COMMANDS = r"""
cd "{project_dir}"
open_project "{project_name}"
set_top {top_name}
{add_kernels}
open_solution "{solution_name}"
set_part {{{part_num}}}
create_clock -period {clock_period} -name default
config_compile -name_max_length 253
config_interface -m_axi_addr64
{config}
{other_configs}
set_param hls.enable_hidden_option_error false
config_rtl -enableFreeRunPipeline=false
config_rtl -disableAutoFreeRunPipeline=true
csynth_design
exit
"""


class RunHls(VivadoHls):
    """Runs Vivado HLS for the given kernels and generate HDL files

    This is a subclass of subprocess.Popen. A temporary directory will be created
    and used as the working directory.

    Args:
      tarfileobj: File object that will contain the reports and HDL files.
      kernel_files: File names or tuple of file names and cflags of the kernels.
      work_dir: The working directory for the HLS run or None for a temporary
          directory.
      top_name: Top-level module name.
      clock_period: Target clock period.
      part_num: Target part number.
      reset_low: In `config_rtl`, `-reset_level low` or `-reset_level high`.
      auto_prefix: In `config_rtl`, add `-auto_prefix` or not. Note that Vitis HLS
          2020.2 enables this option regardless of the option here.
      hls: Either 'vivado_hls' or 'vitis_hls'.
    """

    def __init__(  # noqa: PLR0913,PLR0917
        self,
        tarfileobj: BinaryIO,
        kernel_files: Iterable[str | tuple[str, str]],
        work_dir: str | None,
        top_name: str,
        clock_period: str,
        part_num: str,
        reset_low: bool = True,
        auto_prefix: bool = False,
        hls: str = "vivado_hls",
        std: str = "c++11",
        other_configs: str = "",
    ) -> None:
        if work_dir is None:
            self.tempdir = tempfile.TemporaryDirectory(prefix=f"run-hls-{top_name}-")
            self.project_path = self.tempdir.name
        else:
            self.tempdir = None
            self.project_path = f"{work_dir}/{top_name}"
            os.makedirs(self.project_path, exist_ok=True)
        self.project_name = "project"
        self.solution_name = top_name
        self.tarfileobj = tarfileobj
        self.hls = hls
        kernels = []
        for kernel_file in kernel_files:
            if isinstance(kernel_file, str):
                kernels.append(
                    f'add_files "{{}}" -cflags "-std={std}"'.format(kernel_file)
                )
            else:
                kernels.append(
                    f'add_files "{{}}" -cflags "-std={std} {{}}"'.format(*kernel_file)
                )
        rtl_config = "config_rtl -reset_level " + ("low" if reset_low else "high")
        if auto_prefix:
            if hls == "vivado_hls":
                rtl_config += " -auto_prefix"
            elif hls == "vitis_hls":
                rtl_config += " -module_auto_prefix"
        kwargs = {
            "project_dir": self.project_path,
            "project_name": self.project_name,
            "solution_name": self.solution_name,
            "top_name": top_name,
            "add_kernels": "\n".join(kernels),
            "part_num": part_num,
            "clock_period": clock_period,
            "config": rtl_config,
            "other_configs": other_configs,
        }
        super().__init__(HLS_COMMANDS.format(**kwargs), hls, self.project_path)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # wait for process termination and keep the log
        subprocess.Popen.__exit__(self, exc_type, exc_value, traceback)
        if self.returncode == 0:
            with tarfile.open(mode="w", fileobj=self.tarfileobj) as tar:
                solution_dir = os.path.join(
                    self.project_path, self.project_name, self.solution_name
                )
                try:
                    tar.add(os.path.join(solution_dir, "syn/report"), arcname="report")
                    tar.add(os.path.join(solution_dir, "syn/verilog"), arcname="hdl")
                    tar.add(
                        os.path.join(
                            solution_dir, self.project_path, f"{self.hls}.log"
                        ),
                        arcname="log/" + self.solution_name + ".log",
                    )
                    for pattern in (
                        "*.sched.adb.xml",
                        "*.verbose.sched.rpt",
                        "*.verbose.sched.rpt.xml",
                    ):
                        for f in glob.glob(
                            os.path.join(solution_dir, ".autopilot", "db", pattern)
                        ):
                            tar.add(f, arcname="report/" + os.path.basename(f))
                except FileNotFoundError as e:
                    self.returncode = 1
                    _logger.error("%s", e)
        super().__exit__(exc_type, exc_value, traceback)
        if self.tempdir is not None:
            self.tempdir.cleanup()


class RunAie(subprocess.Popen):
    """Runs Vitis AIE for the given kernels and generate aie.a files

    This is a subclass of subprocess.Popen. A temporary directory will be created
    and used as the working directory.
    """

    def __init__(  # noqa: PLR0913,PLR0917
        self,
        tarfileobj: BinaryIO,
        kernel_files: Iterable[str],
        work_dir: str | None,
        top_name: str,
        clock_period: str,  # noqa: ARG002
        xpfm: str | None,
    ) -> None:
        if work_dir is None:
            self.tempdir = tempfile.TemporaryDirectory(prefix=f"run-aie-{top_name}-")
            self.project_path = self.tempdir.name
        else:
            self.tempdir = None
            self.project_path = f"{work_dir}/{top_name}"
            os.makedirs(self.project_path, exist_ok=True)
        self.project_name = "project"
        self.solution_name = top_name
        self.tarfileobj = tarfileobj
        self.aiecompiler = "aiecompiler"
        include_path_str = [f"--include={os.path.dirname(f)}" for f in kernel_files]
        cmd_args = [
            self.aiecompiler,
            "--target=hw",
            f"--platform={xpfm}",
            *include_path_str,
            f"--workdir={self.project_path}",
            *kernel_files,
        ]
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "env": os.environ
            | {
                "HOME": self.project_path,
            },
        }

        cmd_args = get_cmd_args(cmd_args, ["XILINX_VITIS"], kwargs)
        super().__init__(cmd_args, cwd=self.project_path, **kwargs)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # wait for process termination and keep the log
        subprocess.Popen.__exit__(self, exc_type, exc_value, traceback)
        if self.returncode == 0:
            with tarfile.open(mode="w", fileobj=self.tarfileobj) as tar:
                solution_dir = os.path.join(
                    self.project_path, self.project_name, self.solution_name
                )
                try:
                    tar.add(os.path.join(solution_dir, "syn/report"), arcname="report")
                    tar.add(os.path.join(solution_dir, "syn/verilog"), arcname="hdl")
                    tar.add(
                        os.path.join(
                            solution_dir, self.project_path, f"{self.hls}.log"
                        ),
                        arcname="log/" + self.solution_name + ".log",
                    )
                    for pattern in (
                        "*.sched.adb.xml",
                        "*.verbose.sched.rpt",
                        "*.verbose.sched.rpt.xml",
                    ):
                        for f in glob.glob(
                            os.path.join(solution_dir, ".autopilot", "db", pattern)
                        ):
                            tar.add(f, arcname="report/" + os.path.basename(f))
                except FileNotFoundError as e:
                    self.returncode = 1
                    _logger.error("%s", e)
        super().__exit__(exc_type, exc_value, traceback)
        if self.tempdir is not None:
            self.tempdir.cleanup()


XILINX_XML_NS = {"xd": "http://www.xilinx.com/xd"}


def get_device_info(platform_path: str) -> dict[str, str]:
    """Extract device part number and target frequency from SDAccel platform.

    Currently only support 5.x platforms.

    Args:
      platform_path: Path to the platform directory, e.g.,
          '/opt/xilinx/platforms/xilinx_u200_qdma_201830_2'.

    Raises:
      ValueError: If cannot parse the platform properly.
    """
    device_name = os.path.basename(platform_path)
    try:
        platform_file = next(
            glob.iglob(os.path.join(glob.escape(platform_path), "hw", "*.[xd]sa"))
        )
    except StopIteration as e:
        msg = f"cannot find platform file for {device_name}"
        raise ValueError(msg) from e
    with (
        zipfile.ZipFile(platform_file) as platform,
        platform.open(os.path.basename(platform_file)[:-4] + ".hpfm") as metadata,
    ):
        platform_info = ET.parse(metadata).find(
            "./xd:component/xd:platformInfo", XILINX_XML_NS
        )
        if platform_info is None:
            msg = "cannot parse platform"
            raise ValueError(msg)
        clock_period = platform_info.find(
            "./xd:systemClocks/xd:clock/[@xd:id='0']", XILINX_XML_NS
        )
        if clock_period is None:
            msg = "cannot find clock period in platform"
            raise ValueError(msg)
        part_num = platform_info.find("xd:deviceInfo", XILINX_XML_NS)
        if part_num is None:
            msg = "cannot find part number in platform"
            raise ValueError(msg)
        return {
            "clock_period": clock_period.attrib[
                "{{{xd}}}period".format(**XILINX_XML_NS)
            ],
            "part_num": part_num.attrib["{{{xd}}}name".format(**XILINX_XML_NS)],
        }


def parse_device_info(  # noqa: C901
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    platform_name: str,
    part_num_name: str,
    clock_period_name: str,
) -> dict[str, str]:
    platform = getattr(args, platform_name)
    part_num = getattr(args, part_num_name)
    clock_period = getattr(args, clock_period_name)
    option_string_table = {
        x.dest: x.option_strings[0]
        for x in getattr(parser, "_actions")
        if x.dest in {platform_name, part_num_name, clock_period_name}
    }
    raw_platform_input = platform

    if platform is not None:
        platform = os.path.join(
            os.path.dirname(platform),
            os.path.basename(platform).replace(":", "_").replace(".", "_"),
        )
    if platform is not None:
        for platform_dir in (
            os.path.join("/", "opt", "xilinx"),
            os.environ.get("XILINX_VITIS"),
            os.environ.get("XILINX_SDX"),
        ):
            if not os.path.isdir(platform) and platform_dir is not None:
                platform = os.path.join(platform_dir, "platforms", platform)
        if not os.path.isdir(platform):
            parser.error(
                f"cannot find the specified platform '{raw_platform_input}'; "
                "are you sure it has been installed, "
                "e.g., in '/opt/xilinx/platforms'?"
            )
    if platform is None or not os.path.isdir(platform):
        if clock_period is None:
            parser.error(
                "cannot determine the target clock period; "
                f"please either specify '{option_string_table[platform_name]}' "
                "so the target clock period can be extracted from it, or "
                f"specify '{option_string_table[clock_period_name]}' directly"
            )
        if part_num is None:
            parser.error(
                "cannot determine the target part number; "
                f"please either specify '{option_string_table[platform_name]}' "
                "so the target part number can be extracted from it, or "
                f"specify '{option_string_table[part_num_name]}' directly"
            )
        device_info = {
            "clock_period": clock_period,
            "part_num": part_num,
        }
    else:
        device_info = get_device_info(platform)
        if clock_period is not None:
            device_info["clock_period"] = clock_period
        if part_num is not None:
            device_info["part_num"] = part_num
    return device_info


KERNEL_XML_TEMPLATE = """
<?xml version="1.0" encoding="UTF-8"?>
<root versionMajor="1" versionMinor="6">
  <kernel name="{name}" \
          language="ip_c" \
          vlnv="tapa:xrtl:{name}:1.0" \
          attributes="" \
          preferredWorkGroupSizeMultiple="0" \
          workGroupSize="1" \
          interrupt="true" \
          hwControlProtocol="{hw_ctrl_protocol}">
    <ports>{ports}
    </ports>
    <args>{args}
    </args>
  </kernel>
</root>
"""

S_AXI_PORT = f"""
      <port name="{S_AXI_NAME}" \
            mode="slave" \
            range="0x1000" \
            dataWidth="32" \
            portType="addressable" \
            base="0x0"/>
"""

M_AXI_PORT_TEMPLATE = f"""
      <port name="{M_AXI_PREFIX}{{name}}" \
            mode="master" \
            range="0xFFFFFFFFFFFFFFFF" \
            dataWidth="{{width}}" \
            portType="addressable" \
            base="0x0"/>
"""

AXIS_PORT_TEMPLATE = """
      <port name="{name}" \
            mode="{mode}" \
            dataWidth="{width}" \
            portType="stream"/>
"""

ARG_TEMPLATE = """
      <arg name="{name}" \
           addressQualifier="{addr_qualifier}" \
           id="{arg_id}" \
           port="{port_name}" \
           size="{size:#x}" \
           offset="{offset:#x}" \
           hostOffset="0x0" \
           hostSize="{host_size:#x}" \
           type="{c_type}"/>
"""


def print_kernel_xml(name: str, args: Iterable[Arg], kernel_xml: TextIO) -> None:
    """Generate kernel.xml file.

    Args:
      top_name: Name of the kernel.
      args: Iterable of Arg. The `port` field should not include any prefix and
          could be an empty string to connect the argument to a default port.
      kernel_xml: File object to write to.
    """
    kernel_ports = ""
    kernel_args = ""
    offset = 0x10
    has_s_axi_control = False
    for arg_id, arg in enumerate(args):
        is_stream = False
        if arg.cat == Cat.SCALAR:
            has_s_axi_control = True
            addr_qualifier = 0  # scalar
            host_size = arg.width // 8
            size = max(4, host_size)
            port_name = arg.port or S_AXI_NAME
        elif arg.cat == Cat.MMAP:
            has_s_axi_control = True
            addr_qualifier = 1  # mmap
            size = host_size = 8  # 64-bit
            port_name = M_AXI_PREFIX + (arg.port or arg.name)
            kernel_ports += M_AXI_PORT_TEMPLATE.format(
                name=arg.port or arg.name, width=arg.width
            ).rstrip("\n")
        elif arg.cat in {Cat.ISTREAM, Cat.OSTREAM}:
            is_stream = True
            addr_qualifier = 4  # stream
            size = host_size = 8  # 64-bit
            port_name = arg.port or arg.name
            mode = "read_only" if arg.cat == Cat.ISTREAM else "write_only"
            kernel_ports += AXIS_PORT_TEMPLATE.format(
                name=arg.name, mode=mode, width=arg.width
            ).rstrip("\n")
        else:
            msg = f"unknown arg category: {arg.cat}"
            raise NotImplementedError(msg)
        kernel_args += ARG_TEMPLATE.format(
            name=arg.name,
            addr_qualifier=addr_qualifier,
            arg_id=arg_id,
            port_name=port_name,
            c_type=xml.sax.saxutils.escape(arg.ctype),
            size=size,
            offset=0 if is_stream else offset,
            host_size=host_size,
        ).rstrip("\n")
        if not is_stream:
            offset += size + 4
    hw_ctrl_protocol = "ap_ctrl_none"
    if has_s_axi_control:
        hw_ctrl_protocol = "ap_ctrl_hs"
        kernel_ports += S_AXI_PORT.rstrip("\n")
    kernel_xml.write(
        KERNEL_XML_TEMPLATE.format(
            name=name,
            ports=kernel_ports,
            args=kernel_args,
            hw_ctrl_protocol=hw_ctrl_protocol,
        )
    )


def get_cmd_args(
    cmd_args: list[str],
    env_names: Iterable[str],
    kwargs: dict[str, str | int | bool],
) -> list[str] | str:
    """Get command arguments for subprocess.Popen with specified environment.

    Args:
      cmd_args: The original command arguments.
      env_names: Environment variable names to try.
      kwargs: Keyword arguments to subprocess.Popen. This will be updated if
          necessary.

    Returns:
      Command arguments for subprocess.Popen, with env_name/settings64.sh sourced
      if it exists.
    """
    for env_name in env_names:
        env_value = os.environ.get(env_name)
        if env_value is not None:
            settings = f"{env_value}/settings64.sh"
            if os.path.isfile(settings):
                kwargs["shell"] = True
                kwargs["executable"] = "bash"
                return " ".join(
                    [
                        "source",
                        shlex.quote(settings),
                        ";",
                        "exec",
                        *map(shlex.quote, cmd_args),
                    ]
                )
    return cmd_args
