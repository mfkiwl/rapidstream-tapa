"""Generate rapidstream configuration files."""

__copyright__ = """
Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""

import os
from pathlib import Path

import click

from rapidstream import get_u55c_vitis_3x3_device_factory
from rapidstream.assets.floorplan.floorplan_config import FloorplanConfig
from rapidstream.assets.utilities.impl import ImplConfig
from rapidstream.assets.utilities.pipeline_config import PipelineConfig

VITIS_PLATFORM = "xilinx_u55c_gen3x16_xdma_3_202210_1"
TEMP_DIR = Path(".")
DEVICE_CONFIG = TEMP_DIR / "device_config.json"
FLOORPLAN_CONFIG = TEMP_DIR / "floorplan_config.json"
PIPELINE_CONFIG = TEMP_DIR / "pipeline_config.json"
IMPL_CONFIG = TEMP_DIR / "impl_config.json"


def gen_device_config() -> None:
    """Generate device configuration."""
    factory = get_u55c_vitis_3x3_device_factory(VITIS_PLATFORM)

    # reduce some budget due to HBM
    factory.reduce_slot_area(2, 0, lut=55000, ff=70000)
    factory.reduce_slot_area(1, 0, lut=30000, ff=40000)
    factory.reduce_slot_area(0, 0, lut=30000, ff=40000)

    # reduce the right-most column of DSP from 3X3Slot(2,1)
    factory.reduce_slot_area(2, 1, dsp=100, lut=5000, ff=10000)

    # reduce the wire number between (1,1)<->(2,1), (1,0)<->(2,0)
    factory.set_slot_wire_capacity_in_pair(1, 1, 2, 1, 10000)
    factory.set_slot_wire_capacity_in_pair(1, 0, 2, 0, 10000)

    factory.generate_virtual_device(DEVICE_CONFIG)

    # reduce (2,0) LUT from 50,000 to 55,000. FF from 60,000 to 70,000
    # reduce (1,0) LUT from 25,000 to 30,000. FF from 30,000 to 40,000
    # reduce (0,0) LUT from 25,000 to 30,000. FF from 30,000 to 40,000
    # reduce (2,1) LUT from 0 to 5,000, FF from 0 to 10,000

    # reduce wire number from 12,000 to 10,000 between (1,1)<->(2,1), (1,0)<->(2,0)


def gen_floorplan_config() -> None:
    """Generate floorplan configuration."""
    floorplan_config = FloorplanConfig(
        dse_range_max=0.8,
        dse_range_min=0.6,
        partition_strategy="flat",
        port_pre_assignments={
            "ap_clk": "SLOT_X2Y0:SLOT_X2Y0",
            "ap_rst_n": "SLOT_X2Y0:SLOT_X2Y0",
            "interrupt": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_edge_list_ch_0_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_1_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_2_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_3_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_4_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_5_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_6_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_edge_list_ch_7_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_edge_list_ptr_.*": "SLOT_X0Y0:SLOT_X0Y0",
            "m_axi_mat_B_ch_0_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_B_ch_1_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_B_ch_2_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_B_ch_3_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_C_ch_0_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_C_ch_1_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_C_ch_2_.*": "SLOT_X1Y0:SLOT_X1Y0",
            "m_axi_mat_C_ch_3_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_4_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_5_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_6_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_7_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_0_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_1_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_2_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_3_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_4_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_5_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_6_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "m_axi_mat_C_ch_in_7_.*": "SLOT_X2Y0:SLOT_X2Y0",
            "s_axi_control_.*": "SLOT_X2Y1:SLOT_X2Y1",
        },
        slot_to_rtype_to_max_limit={
            "SLOT_X0Y0:SLOT_X0Y0": {"LUT": 0.75},
            "SLOT_X1Y0:SLOT_X1Y0": {"LUT": 0.75},
            "SLOT_X2Y0:SLOT_X2Y0": {"LUT": 0.75},
            "SLOT_X2Y1:SLOT_X2Y1": {"LUT": 0.75},
            "SLOT_X1Y1:SLOT_X1Y1": {"LUT": 0.77},
        },
    )
    floorplan_config.save_to_file(FLOORPLAN_CONFIG)


def gen_pipeline_config() -> None:
    """Generate pipeline configuration."""
    pipeline_config = PipelineConfig(
        pp_scheme="single_h_double_v",
    )
    pipeline_config.save_to_file(PIPELINE_CONFIG)


def gen_impl_config(max_workers: int, max_synth_jobs: int) -> None:
    """Generate implementation configuration."""
    impl_config = ImplConfig(
        max_workers=max_workers,  # to avoid too many parallel active jobs during tests
        placement_strategy="EarlyBlockPlacement",
        port_to_clock_period={"ap_clk": 2.9},
        vitis_platform="xilinx_u55c_gen3x16_xdma_3_202210_1",
        max_synth_jobs=max_synth_jobs,
    )
    impl_config.save_to_file(IMPL_CONFIG)


@click.command
@click.option(
    "--max-workers",
    type=int,
    default=1,
    help="Number of parallel workers for Vivado implementation. Default is 1.",
    show_default=True,
)
@click.option(
    "--max-synth-jobs",
    type=int,
    default=1,
    help="Number of synthesis jobs for each Vivado implementation. Default is 1.",
    show_default=True,
)
def gen_config(max_workers: int, max_synth_jobs: int) -> None:
    """Generate configuration files."""
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)
    gen_device_config()
    gen_floorplan_config()
    gen_pipeline_config()
    gen_impl_config(max_workers, max_synth_jobs)


if __name__ == "__main__":
    gen_config()
