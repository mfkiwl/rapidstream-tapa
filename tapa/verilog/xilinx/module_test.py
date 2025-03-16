"""Unit tests for tapa.verilog.xilinx.module."""

__copyright__ = """
Copyright (c) 2025 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tapa.util import Options
from tapa.verilog.xilinx import ast_types
from tapa.verilog.xilinx.module import Module

_TESTDATA_PATH = (Path(__file__).parent / "testdata").resolve()


@pytest.fixture(params=["pyslang", "pyverilog"])
def options(request: pytest.FixtureRequest) -> Iterator[None]:
    enable_pyslang_saved = Options.enable_pyslang
    Options.enable_pyslang = request.param == "pyslang"
    yield
    Options.enable_pyslang = enable_pyslang_saved


@pytest.mark.usefixtures("options")
def test_invalid_module() -> None:
    with pytest.raises(ValueError, match="`files` and `name` cannot both be empty"):
        Module()


@pytest.mark.usefixtures("options")
def test_empty_module() -> None:
    """An empty module can be constructed from a name.

    This is used to create placeholders before Verilog is parsed, and to create
    skeleton FSM modules.
    """
    module = Module(name="foo")

    assert module.name == "foo"
    assert not module.ports


@pytest.mark.usefixtures("options")
def test_lower_level_task_module() -> None:
    module = Module(
        files=[str(_TESTDATA_PATH / "LowerLevelTask.v")],
        is_trimming_enabled=True,
    )

    assert module.name == "LowerLevelTask"

    assert list(module.ports) == [
        "ap_clk",
        "ap_rst_n",
        "ap_start",
        "ap_done",
        "ap_idle",
        "ap_ready",
        "istream_s_dout",
        "istream_s_empty_n",
        "istream_s_read",
        "istream_peek_dout",
        "istream_peek_empty_n",
        "istream_peek_read",
        "ostreams_s_din",
        "ostreams_s_full_n",
        "ostreams_s_write",
        "ostreams_peek",
        "scalar",
    ]
    assert list(module.signals) == [
        "ap_done",
        "ap_idle",
        "ap_ready",
    ]

    assert module.get_port_of("istream", "_dout").name == "istream_s_dout"
    assert module.get_port_of("ostreams[0]", "_din").name == "ostreams_s_din"

    for port_got, port_name_wanted in zip(
        module.generate_istream_ports("istream", "arg"),
        ("istream_s_dout", "istream_peek_dout"),
    ):
        assert port_got.portname == port_name_wanted

    for port_got, port_name_wanted in zip(
        module.generate_ostream_ports("ostreams[0]", "arg"),
        ("ostreams_s_din",),
    ):
        assert port_got.portname == port_name_wanted

    port = module.get_port_of("istream_peek", "_dout")
    assert port.name == "istream_peek_dout"
    module.del_port(port.name)
    with pytest.raises(
        ValueError,
        match=r"module LowerLevelTask does not have port istream_peek._dout",
    ):
        module.get_port_of("istream_peek", "_dout")

    assert module.find_port(prefix="istream", suffix="_dout") == "istream_s_dout"


@pytest.mark.usefixtures("options")
def test_upper_level_task_module() -> None:
    module = Module(
        files=[str(_TESTDATA_PATH / "UpperLevelTask.v")],
        is_trimming_enabled=False,
    )

    assert module.name == "UpperLevelTask"
    assert module.params != {}

    module.cleanup()

    assert module.params == {}


@pytest.mark.usefixtures("options")
def test_add_ports_succeeds() -> None:
    module = Module(name="foo")
    assert module.ports == {}

    module.add_ports([ast_types.Input("bar")])
    ports = module.ports
    assert list(ports) == ["bar"]
    assert ports["bar"].name == "bar"
    assert ports["bar"].width is None
    assert ports["bar"].direction == "input"

    module.add_ports([ast_types.Input("baz"), ast_types.Output("qux")])
    ports = module.ports
    assert list(ports) == ["bar", "baz", "qux"]
    assert ports["baz"].name == "baz"
    assert ports["baz"].width is None
    assert ports["baz"].direction == "input"
    assert ports["qux"].name == "qux"
    assert ports["qux"].width is None
    assert ports["qux"].direction == "output"


@pytest.mark.usefixtures("options")
def test_del_port_succeeds() -> None:
    module = Module(name="foo")
    module.add_ports([ast_types.Input("bar")])

    module.del_port("bar")

    assert module.ports == {}


@pytest.mark.usefixtures("options")
def test_del_nonexistent_port_fails() -> None:
    module = Module(name="foo")
    module.add_ports([ast_types.Input("bar")])

    with pytest.raises(ValueError, match="no port baz found in module foo"):
        module.del_port("baz")


@pytest.mark.usefixtures("options")
def test_add_m_axi() -> None:
    module = Module(name="foo")

    assert module.name == "foo"
    assert module.ports == {}

    module.add_m_axi(name="bar", data_width=32)

    for port in module.ports:
        assert port.startswith("m_axi_bar_")
    assert set(module.ports).issuperset(
        {
            "m_axi_bar_ARADDR",
            "m_axi_bar_ARREADY",
            "m_axi_bar_ARVALID",
            "m_axi_bar_AWADDR",
            "m_axi_bar_AWREADY",
            "m_axi_bar_AWVALID",
            "m_axi_bar_RDATA",
            "m_axi_bar_RREADY",
            "m_axi_bar_RVALID",
            "m_axi_bar_WDATA",
            "m_axi_bar_WREADY",
            "m_axi_bar_WVALID",
            "m_axi_bar_BRESP",
            "m_axi_bar_BREADY",
            "m_axi_bar_BVALID",
        }
    )
