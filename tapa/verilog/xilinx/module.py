__copyright__ = """
Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""

import functools
import itertools
import logging
import os.path
import re
import tempfile
from collections.abc import Callable, Generator, Iterable, Iterator

import pyslang
from pyverilog.ast_code_generator.codegen import ASTCodeGenerator
from pyverilog.vparser.ast import (
    Assign,
    Constant,
    Decl,
    Description,
    Identifier,
    Inout,
    Input,
    Instance,
    InstanceList,
    Ioport,
    ModuleDef,
    Node,
    Output,
    ParamArg,
    Parameter,
    Paramlist,
    Port,
    PortArg,
    Portlist,
    Pragma,
    Reg,
    Source,
    Unot,
    Width,
    Wire,
)
from pyverilog.vparser.parser import VerilogCodeParser

from tapa.backend.xilinx import M_AXI_PREFIX
from tapa.common.pyslang_rewriter import PyslangRewriter
from tapa.common.unique_attrs import UniqueAttrs
from tapa.util import Options
from tapa.verilog.ast_utils import make_port_arg
from tapa.verilog.util import (
    Pipeline,
    async_mmap_instance_name,
    match_array_name,
    sanitize_array_name,
    wire_name,
)
from tapa.verilog.xilinx import ioport
from tapa.verilog.xilinx.ast_types import Directive, IOPort, Logic, Signal
from tapa.verilog.xilinx.async_mmap import ASYNC_MMAP_SUFFIXES, async_mmap_arg_name
from tapa.verilog.xilinx.const import (
    CLK,
    FIFO_READ_PORTS,
    FIFO_WRITE_PORTS,
    HANDSHAKE_DONE,
    HANDSHAKE_IDLE,
    HANDSHAKE_READY,
    HANDSHAKE_RST,
    ISTREAM_SUFFIXES,
    OSTREAM_SUFFIXES,
    RST_N,
    STREAM_PORT_DIRECTION,
    TRUE,
)
from tapa.verilog.xilinx.m_axi import M_AXI_PORTS, M_AXI_SUFFIXES, get_m_axi_port_width

_logger = logging.getLogger().getChild(__name__)

__all__ = [
    "Module",
    "generate_m_axi_ports",
]

# vitis hls generated port infixes
FIFO_INFIXES = ("_V", "_r", "_s", "")

_CODEGEN = ASTCodeGenerator()
_SIGNAL_SYNTAX = pyslang.DataDeclarationSyntax | pyslang.NetDeclarationSyntax
_LOGIC_SYNTAX = pyslang.ContinuousAssignSyntax | pyslang.ProceduralBlockSyntax


class Module:  # noqa: PLR0904  # TODO: refactor this class
    """AST and helpers for a verilog module.

    `_next_*_idx` is the index to module_def.items where the next type of item
    should be inserted.

    Attributes
    ----------
      ast: The Source node.
      directives: Tuple of Directives.
      _next_io_port_idx: Next index of an IOPort in module_def.items.
      _next_signal_idx: Next index of Wire or Reg in module_def.items.
      _next_param_idx: Next index of Parameter in module_def.items.
      _next_instance_idx: Next index of InstanceList in module_def.items.
      _next_logic_idx: Next index of Assign or Always in module_def.items.

    """

    # module_def.items should contain the following attributes, in that order.
    _ATTRS = "param", "io_port", "signal", "logic", "instance"

    def __init__(
        self,
        files: Iterable[str] = (),
        is_trimming_enabled: bool = False,
        name: str = "",
    ) -> None:
        """Construct a Module from files."""
        if not files:
            if not name:
                msg = "`files` and `name` cannot both be empty"
                raise ValueError(msg)
            if Options.enable_pyslang:
                self._syntax_tree = pyslang.SyntaxTree.fromText(
                    f"module {name}(); endmodule",
                )
                self._rewriter = PyslangRewriter(self._syntax_tree)
                self._parse_syntax_tree()
                return
            self.ast = Source(
                name,
                Description([ModuleDef(name, Paramlist(()), Portlist(()), items=())]),
            )
            self.directives = ()
            self._calculate_indices()
            return
        with tempfile.TemporaryDirectory(prefix="pyverilog-") as output_dir:
            if is_trimming_enabled:
                # trim the body since we only need the interface information
                new_files = []

                def gen_trimmed_file(file: str, idx: int) -> str:
                    lines = []
                    with open(file, encoding="utf-8") as fp:
                        for line in fp:
                            items = line.strip().split()
                            if (
                                len(items) > 1
                                and items[0] in {"reg", "wire"}
                                and items[1].startswith("ap_rst")
                            ):
                                lines.append("endmodule")
                                break
                            lines.append(line)
                    new_file = os.path.join(output_dir, f"trimmed_{idx}.v")
                    with open(new_file, "w", encoding="utf-8") as fp:
                        fp.writelines(lines)
                    return new_file

                for idx, file in enumerate(files):
                    new_files.append(gen_trimmed_file(file, idx))
                files = new_files
            if Options.enable_pyslang:
                self._syntax_tree = pyslang.SyntaxTree.fromFiles(files)
                self._rewriter = PyslangRewriter(self._syntax_tree)
                self._parse_syntax_tree()
                return
            codeparser = VerilogCodeParser(
                files,
                preprocess_output=os.path.join(output_dir, "preprocess.output"),
                outputdir=output_dir,
                debug=False,
            )
            self.ast: Source = codeparser.parse()
            self.directives: tuple[Directive, ...] = codeparser.get_directives()
        self._calculate_indices()

    def _calculate_indices(self) -> None:
        for idx, item in enumerate(self._module_def.items):
            if isinstance(item, Decl):
                if any(isinstance(x, Input | Output) for x in item.list):
                    self._next_io_port_idx = idx + 1
                elif any(isinstance(x, Wire | Reg) for x in item.list):
                    self._next_signal_idx = idx + 1
                elif any(isinstance(x, Parameter) for x in item.list):
                    self._next_param_idx = idx + 1
            elif isinstance(item, Logic):
                self._next_logic_idx = idx + 1
            elif isinstance(item, InstanceList):
                self._next_instance_idx = idx + 1

        # if an attr type is not present, set its idx to the previous attr type
        last_idx = 0
        for attr in self._ATTRS:
            idx = getattr(self, f"_next_{attr}_idx", None)
            if idx is None:
                setattr(self, f"_next_{attr}_idx", last_idx)
            else:
                last_idx = idx

    def _parse_syntax_tree(self) -> None:
        """Parse syntax tree and memorize relevant nodes.

        The following attributes will be created/updated:

        _module_decl: ModuleDeclarationSyntax

        _params: dict[str, Parameter]
        _param_name_to_decl: dict[str, ParameterDeclarationSyntax]
        _param_source_range: SourceRange

        _ports: dict[str, IOPort]
        _port_name_to_decl: dict[str, PortDeclarationSyntax]
        _port_source_range: SourceRange

        _signals: dict[str, Signal]
        _signal_name_to_decl: dict[str, DataDeclarationSyntax | NetDeclarationSyntax]
        _signal_source_range: SourceRange

        _logics: list[ContinuousAssignSyntax | ProceduralBlockSyntax]
        _logic_source_range: SourceRange

        _instances: list[HierarchyInstantiationSyntax]
        _instance_source_range: SourceRange
        """

        class Attrs(UniqueAttrs):
            module_decl: pyslang.ModuleDeclarationSyntax

        attrs = Attrs()

        self._params: dict[str, Parameter] = {}
        self._param_name_to_decl: dict[str, pyslang.ParameterDeclarationStatementSyntax]
        self._param_name_to_decl = {}
        self._param_source_range: pyslang.SourceRange

        self._ports: dict[str, ioport.IOPort] = {}
        self._port_name_to_decl: dict[str, pyslang.PortDeclarationSyntax] = {}
        self._port_source_range: pyslang.SourceRange

        self._signals: dict[str, Signal] = {}
        self._signal_name_to_decl: dict[str, _SIGNAL_SYNTAX] = {}
        self._signal_source_range: pyslang.SourceRange

        self._logics: list[_LOGIC_SYNTAX] = []
        self._logic_source_range: pyslang.SourceRange

        self._instances: list[pyslang.HierarchyInstantiationSyntax] = []
        self._instance_source_range: pyslang.SourceRange

        @functools.singledispatch
        def visitor(_: object) -> pyslang.VisitAction:
            return pyslang.VisitAction.Advance

        @visitor.register
        def _(node: pyslang.ModuleDeclarationSyntax) -> pyslang.VisitAction:
            attrs.module_decl = node
            # Append after the header by default.
            self._update_source_range_for_param(node.header)
            return pyslang.VisitAction.Advance

        @visitor.register
        def _(node: pyslang.ParameterDeclarationStatementSyntax) -> pyslang.VisitAction:
            assert isinstance(node.parameter, pyslang.ParameterDeclarationSyntax)
            param = Parameter(
                _get_name(node.parameter),
                Constant(str(node.parameter.declarators[0].initializer.expr).strip()),
                _get_width(node.parameter.type),
            )
            self._params[param.name] = param
            self._param_name_to_decl[param.name] = node
            self._update_source_range_for_param(node)
            return pyslang.VisitAction.Skip

        @visitor.register
        def _(node: pyslang.PortDeclarationSyntax) -> pyslang.VisitAction:
            port = ioport.IOPort.create(node)
            self._ports[port.name] = port
            self._port_name_to_decl[port.name] = node
            self._update_source_range_for_port(node)
            return pyslang.VisitAction.Skip

        @visitor.register
        def _(node: _SIGNAL_SYNTAX) -> pyslang.VisitAction:
            signal = {
                pyslang.DataDeclarationSyntax: Reg,
                pyslang.NetDeclarationSyntax: Wire,
            }[type(node)](_get_name(node), _get_width(node.type))
            self._signals[signal.name] = signal
            self._signal_name_to_decl[signal.name] = node
            self._update_source_range_for_signal(node)
            return pyslang.VisitAction.Skip

        @visitor.register
        def _(node: _LOGIC_SYNTAX) -> pyslang.VisitAction:
            self._logics.append(node)
            self._update_source_range_for_logic(node)
            return pyslang.VisitAction.Skip

        @visitor.register
        def _(node: pyslang.HierarchyInstantiationSyntax) -> pyslang.VisitAction:
            self._instances.append(node)
            self._update_source_range_for_instance(node)
            return pyslang.VisitAction.Skip

        self._syntax_tree.root.visit(visitor)

        self._module_decl = attrs.module_decl

    def _update_source_range_for_param(self, node: pyslang.SyntaxNode) -> None:
        self._param_source_range = node.sourceRange
        self._update_source_range_for_port(node)

    def _update_source_range_for_port(self, node: pyslang.SyntaxNode) -> None:
        self._port_source_range = node.sourceRange
        self._update_source_range_for_signal(node)

    def _update_source_range_for_signal(self, node: pyslang.SyntaxNode) -> None:
        self._signal_source_range = node.sourceRange
        self._update_source_range_for_logic(node)

    def _update_source_range_for_logic(self, node: pyslang.SyntaxNode) -> None:
        self._logic_source_range = node.sourceRange
        self._update_source_range_for_instance(node)

    def _update_source_range_for_instance(self, node: pyslang.SyntaxNode) -> None:
        self._instance_source_range = node.sourceRange

    @property
    def _module_def(self) -> ModuleDef:
        module_defs = [
            x for x in self.ast.description.definitions if isinstance(x, ModuleDef)
        ]
        if len(module_defs) != 1:
            msg = "unexpected number of modules"
            raise ValueError(msg)
        return module_defs[0]

    @property
    def name(self) -> str:
        if Options.enable_pyslang:
            return self._module_decl.header.name.valueText
        return self._module_def.name

    @property
    def ports(self) -> dict[str, ioport.IOPort]:
        if Options.enable_pyslang:
            return self._ports
        port_lists = [
            # ANSI style: ports declared in header
            (x.first for x in self._module_def.portlist.ports if isinstance(x, Ioport)),
            # Non-ANSI style: ports declared in body
            *(x.list for x in self._module_def.items if isinstance(x, Decl)),
        ]
        return {
            x.name: ioport.IOPort.create(x)
            for x in itertools.chain.from_iterable(port_lists)
            if isinstance(x, Input | Output | Inout)
        }

    class NoMatchingPortError(ValueError):
        """No matching port being found exception."""

    def get_port_of(self, fifo: str, suffix: str) -> ioport.IOPort:
        """Return the IOPort of the given fifo with the given suffix.

        Args:
        ----
          fifo (str): Name of the fifo.
          suffix (str): One of the suffixes in ISTREAM_SUFFIXES or OSTREAM_SUFFIXES.

        Raises:
        ------
          ValueError: Module does not have the port.

        Returns:
        -------
          IOPort.

        """
        ports = self.ports
        sanitized_fifo = sanitize_array_name(fifo)
        for infix in FIFO_INFIXES:
            port = ports.get(f"{sanitized_fifo}{infix}{suffix}")
            if port is not None:
                return port
        # may be a singleton array without the numerical suffix...
        match = match_array_name(fifo)
        if match is not None and match[1] == 0:
            singleton_fifo = match[0]
            for infix in FIFO_INFIXES:
                port = ports.get(f"{singleton_fifo}{infix}{suffix}")
                if port is not None:
                    _logger.warning("assuming %s is a singleton array", singleton_fifo)
                    return port

        msg = f"module {self.name} does not have port {fifo}.{suffix}"
        raise Module.NoMatchingPortError(msg)

    def generate_istream_ports(
        self,
        port: str,
        arg: str,
        ignore_peek_fifos: Iterable[str] = (),
    ) -> Iterator[PortArg]:
        for suffix in ISTREAM_SUFFIXES:
            arg_name = None

            arg_name = wire_name(arg, suffix)

            # read port
            yield make_port_arg(
                port=self.get_port_of(port, suffix).name,
                arg=arg_name,
            )

            if STREAM_PORT_DIRECTION[suffix] == "input":
                # peek port
                if port in ignore_peek_fifos:
                    continue
                match = match_array_name(port)
                if match is None:
                    peek_port = f"{port}_peek"
                else:
                    peek_port = f"{match[0]}_peek[{match[1]}]"
                assert arg_name
                yield make_port_arg(
                    port=self.get_port_of(peek_port, suffix).name,
                    arg=arg_name,
                )

    def generate_ostream_ports(
        self,
        port: str,
        arg: str,
    ) -> Iterator[PortArg]:
        for suffix in OSTREAM_SUFFIXES:
            yield make_port_arg(
                port=self.get_port_of(port, suffix).name,
                arg=wire_name(arg, suffix),
            )

    @property
    def signals(self) -> dict[str, Wire | Reg]:
        if Options.enable_pyslang:
            return self._signals
        signal_lists = (x.list for x in self._module_def.items if isinstance(x, Decl))
        return {
            x.name: x
            for x in itertools.chain.from_iterable(signal_lists)
            if isinstance(x, Wire | Reg)
        }

    @property
    def params(self) -> dict[str, Parameter]:
        if Options.enable_pyslang:
            return self._params
        param_lists = (x.list for x in self._module_def.items if isinstance(x, Decl))
        return {
            x.name: x
            for x in itertools.chain.from_iterable(param_lists)
            if isinstance(x, Parameter)
        }

    @property
    def code(self) -> str:
        if Options.enable_pyslang:
            self._syntax_tree = self._rewriter.commit()
            self._parse_syntax_tree()
            return str(self._syntax_tree.root)
        return "\n".join(
            directive for _, directive in self.directives
        ) + _CODEGEN.visit(self.ast)

    def get_template_code(self) -> str:
        portlist = []
        items = []
        for port in self.ports.values():
            ast_port = port.ast_port
            portlist.append(
                Port(
                    name=ast_port.name,
                    width=None,
                    dimensions=None,
                    type=None,
                )
            )
            items.append(Decl((ast_port,)))

        # Create the module definition
        template_ast = Source(
            self.name,
            Description(
                [
                    ModuleDef(
                        name=self.name,
                        paramlist=Paramlist(()),
                        portlist=Portlist(tuple(portlist)),
                        items=tuple(items),
                    )
                ]
            ),
        )

        return _CODEGEN.visit(template_ast)

    def _increment_idx(self, length: int, target: str) -> None:
        attr_map = {attr: priority for priority, attr in enumerate(self._ATTRS)}
        target_priority = attr_map.get(target)
        if target_priority is None:
            msg = f"target must be one of {self._ATTRS}"
            raise ValueError(msg)

        # Get the index of the target once, since it could change in the loop.
        target_idx = getattr(self, f"_next_{target}_idx")

        # Increment `_next_*_idx` if it is after `_next_{target}_idx`. If
        # `_next_*_idx` == `_next_{target}_idx`, increment only if `priority` is
        # larger, i.e., `attr` should show up after `target` in `module_def.items`.
        for priority, attr in enumerate(self._ATTRS):
            attr_name = f"_next_{attr}_idx"
            idx = getattr(self, attr_name)
            if (idx, priority) >= (target_idx, target_priority):
                setattr(self, attr_name, idx + length)

    def _filter(self, func: Callable[[Node], bool], _target: str) -> None:
        self._module_def.items = tuple(filter(func, self._module_def.items))
        self._calculate_indices()

    def add_ports(self, ports: Iterable[IOPort | Decl]) -> "Module":
        """Add IO ports to this module.

        Each port could be an `IOPort`, or an `Decl` that has a single `IOPort`
        prefixed with 0 or more `Pragma`s.
        """
        if Options.enable_pyslang:
            return self._add_ports_pyslang(ports)
        decl_list = []
        port_list = []
        for port in ports:
            if isinstance(port, Decl):
                decl_list.append(port)
                port_list.extend(x for x in port.list if isinstance(x, IOPort))
            elif isinstance(port, IOPort):
                decl_list.append(Decl((port,)))
                port_list.append(port)
            else:
                msg = f"unexpected port `{port}`"
                raise ValueError(msg)
        self._module_def.portlist.ports += tuple(
            Port(name=port.name, width=None, dimensions=None, type=None)
            for port in port_list
        )
        self._module_def.items = (
            self._module_def.items[: self._next_io_port_idx]
            + tuple(decl_list)
            + self._module_def.items[self._next_io_port_idx :]
        )
        self._increment_idx(len(decl_list), "io_port")
        return self

    def _add_ports_pyslang(self, ports: Iterable[IOPort | Decl]) -> "Module":
        def flatten(ports: Iterable[IOPort | Decl]) -> Generator[ioport.IOPort]:
            for port in ports:
                if isinstance(port, Decl):
                    yield from flatten(x for x in port.list if isinstance(x, IOPort))
                elif isinstance(port, IOPort):
                    yield ioport.IOPort.create(port)
                else:
                    msg = f"unexpected port `{port}`"
                    raise ValueError(msg)

        header_pieces = []
        body_pieces = []
        is_ports_empty = len(self._ports) == 0
        for port in flatten(ports):
            self._ports[port.name] = port
            header_pieces.extend([",\n  ", port.name])
            body_pieces.extend(["\n  ", str(port)])
        if is_ports_empty and header_pieces:
            # Remove the first `,` if there is no preceding ports.
            header_pieces[0] = "  "

        self._rewriter.add_before(
            # Append new ports before token `)` of the port list in header.
            self._module_decl.header.ports.getLastToken().location,
            header_pieces,
        )
        self._rewriter.add_before(
            # If module has no existing port, append new ports after the header.
            self._port_source_range.end,
            body_pieces,
        )
        return self

    def del_port(self, port_name: str) -> None:
        """Delete IO port from this module.

        Args:
        ----
          port_name (str): Name of the IO port.

        Raises:
        ------
          ValueError: Module does not have the port.
        """
        if Options.enable_pyslang:
            self._del_port_pyslang(port_name)
            return

        removed_ports = []

        def func(item: Node) -> bool:
            if isinstance(item, Decl):
                for decl in item.list:
                    if isinstance(decl, IOPort) and decl.name == port_name:
                        removed_ports.append(decl.name)
                        return False
            return True

        self._filter(func, "port")

        if not removed_ports:
            msg = f"no port {port_name} found in module {self.name}"
            raise ValueError(msg)

        self._module_def.portlist.ports = tuple(
            x for x in self._module_def.portlist.ports if x.name != port_name
        )

    def _del_port_pyslang(self, port_name: str) -> None:
        if self._ports.pop(port_name, None) is None:
            msg = f"no port {port_name} found in module {self.name}"
            raise ValueError(msg)

        self._rewriter.remove(self._port_name_to_decl[port_name].sourceRange)

        non_ansi_port_list = self._module_decl.header.ports
        assert isinstance(non_ansi_port_list, pyslang.NonAnsiPortListSyntax)

        # `ports` is a list of alternating `SyntaxNode`s and `Token`s; find the
        # port in header that to delete, and the corresponding comma token.
        nodes = []
        tokens = []
        index_to_del = -1
        for i, node_or_token in enumerate(non_ansi_port_list.ports):
            if i % 2 == 0:
                assert isinstance(node_or_token, pyslang.ImplicitNonAnsiPortSyntax)
                assert isinstance(node_or_token.expr, pyslang.PortReferenceSyntax)
                nodes.append(node_or_token)
                if node_or_token.expr.name.valueText == port_name:
                    index_to_del = i // 2
            else:
                assert isinstance(node_or_token, pyslang.Token)
                assert node_or_token.valueText == ","
                tokens.append(node_or_token)
        assert len(nodes) == len(tokens) + 1

        if index_to_del == -1:
            msg = f"no port {port_name} found in module {self.name}"
            raise ValueError(msg)

        # Remove the `SyntaxNode` of port in header.
        self._rewriter.remove(nodes[index_to_del].sourceRange)

        # If the removed `SyntaxNode` is the last in the list, remove the last
        # comma which is right before the removed `SyntaxNode`. Otherwise,
        # remove the comma right after.
        if index_to_del == len(nodes) - 1:
            index_to_del = -1
        self._rewriter.remove(tokens[index_to_del].range)

    def add_comment_lines(self, lines: Iterable[str]) -> "Module":
        """Add comment lines after the module header.

        Each line must start with `// ` and must not contain the new line character.
        """
        pieces = ["\n"]
        for line in lines:
            if not line.startswith("// "):
                msg = f"line must start with `// `, got `{line}`"
                raise ValueError(msg)
            if "\n" in line:
                msg = f"line must not contain newlines`, got `{line}`"
                raise ValueError(msg)
            pieces.append(line)
            pieces.append("\n")

        if not Options.enable_pyslang:
            return self.add_ports([Decl([Identifier("".join(pieces))])])

        # Append the comment lines after the module header.
        self._rewriter.add_before(self._module_decl.header.sourceRange.end, pieces)
        return self

    def add_signals(self, signals: Iterable[Signal]) -> "Module":
        if Options.enable_pyslang:
            return self._add_signals_pyslang(signals)
        decl = Decl(list=tuple(signals))
        self._module_def.items = (
            *self._module_def.items[: self._next_signal_idx],
            decl,
            *self._module_def.items[self._next_signal_idx :],
        )
        self._increment_idx(len(decl.list), "signal")
        return self

    def _add_signals_pyslang(self, signals: Iterable[Signal]) -> "Module":
        for signal in signals:
            self._signals[signal.name] = signal
            self._rewriter.add_before(
                self._signal_source_range.end, ["\n  ", _CODEGEN.visit(signal)]
            )
        return self

    def add_pipeline(self, q: Pipeline, init: Node) -> None:
        """Add registered signals and logics for q initialized by init.

        Args:
        ----
            q (Pipeline): The pipelined variable.
            init (Node): Value used to drive the first stage of the pipeline.

        """
        self.add_signals(q.signals)
        self.add_logics([Assign(left=q[0], right=init)])

    def del_signals(self, prefix: str = "", suffix: str = "") -> None:
        if Options.enable_pyslang:
            self._del_signals_pyslang(prefix, suffix)
            return

        def func(item: Node) -> bool:
            if isinstance(item, Decl):
                item = item.list[0]
                if isinstance(item, Reg | Wire):
                    name: str = item.name
                    if name.startswith(prefix) and name.endswith(suffix):
                        return False
            return True

        self._filter(func, "signal")

    def _del_signals_pyslang(self, prefix: str, suffix: str) -> None:
        new_signals = {}
        for name, signal in self._signals.items():
            if name.startswith(prefix) and name.endswith(suffix):
                # TODO: support deleting added signals
                self._rewriter.remove(self._signal_name_to_decl[name].sourceRange)
            else:
                new_signals[name] = signal
        self._signals = new_signals

    def add_params(self, params: Iterable[Parameter]) -> "Module":
        if Options.enable_pyslang:
            return self._add_params_pyslang(params)
        decl = Decl(list=tuple(params))
        self._module_def.items = (
            *self._module_def.items[: self._next_param_idx],
            decl,
            *self._module_def.items[self._next_param_idx :],
        )
        self._increment_idx(len(decl.list), "param")
        return self

    def _add_params_pyslang(self, params: Iterable[Parameter]) -> "Module":
        for param in params:
            self._params[param.name] = param
            self._rewriter.add_before(
                self._param_source_range.end, ["\n  ", _CODEGEN.visit(param)]
            )
        return self

    def del_params(self, prefix: str = "", suffix: str = "") -> None:
        if Options.enable_pyslang:
            self._del_params_pyslang(prefix, suffix)
            return

        def func(item: Node) -> bool:
            if isinstance(item, Decl):
                item = item.list[0]
                if isinstance(item, Parameter):
                    name: str = item.name
                    if name.startswith(prefix) and name.endswith(suffix):
                        return False
            return True

        self._filter(func, "param")

    def _del_params_pyslang(self, prefix: str, suffix: str) -> None:
        new_params = {}
        for name, param in self._params.items():
            if name.startswith(prefix) and name.endswith(suffix):
                # TODO: support deleting added params
                self._rewriter.remove(self._param_name_to_decl[name].sourceRange)
            else:
                new_params[name] = param
        self._params = new_params

    def _add_instancelist(self, item: InstanceList) -> "Module":
        self._module_def.items = (
            *self._module_def.items[: self._next_instance_idx],
            item,
            *self._module_def.items[self._next_instance_idx :],
        )
        self._increment_idx(1, "instance")
        return self

    def add_instance(
        self,
        module_name: str,
        instance_name: str,
        ports: Iterable[PortArg],
        params: Iterable[ParamArg] = (),
    ) -> "Module":
        item = InstanceList(
            module=module_name,
            parameterlist=tuple(params),
            instances=(
                Instance(
                    module=None,
                    name=instance_name,
                    parameterlist=None,
                    portlist=tuple(ports),
                ),
            ),
        )
        if Options.enable_pyslang:
            return self._add_instancelist_pyslang(item)
        self._add_instancelist(item)
        return self

    def _add_instancelist_pyslang(self, item: InstanceList) -> "Module":
        self._rewriter.add_before(
            self._instance_source_range.end, ["\n  ", _CODEGEN.visit(item)]
        )
        return self

    def add_logics(self, logics: Iterable[Logic]) -> "Module":
        if Options.enable_pyslang:
            return self._add_logics_pyslang(logics)

        logic_tuple = tuple(logics)
        self._module_def.items = (
            self._module_def.items[: self._next_logic_idx]
            + logic_tuple
            + self._module_def.items[self._next_logic_idx :]
        )
        self._increment_idx(len(logic_tuple), "logic")
        return self

    def _add_logics_pyslang(self, logics: Iterable[Logic]) -> "Module":
        for logic in logics:
            self._rewriter.add_before(
                self._logic_source_range.end, ["\n  ", _CODEGEN.visit(logic)]
            )
        return self

    def del_logics(self) -> None:
        if Options.enable_pyslang:
            self._del_logics_pyslang()
            return

        def func(item: Node) -> bool:
            return not isinstance(item, Logic)

        self._filter(func, "param")

    def _del_logics_pyslang(self) -> None:
        for logic in self._logics:
            self._rewriter.remove(logic.sourceRange)

    def del_instances(self, prefix: str = "", suffix: str = "") -> None:
        """Deletes instances with a matching *module* name."""
        if Options.enable_pyslang:
            self._del_instances_pyslang(prefix, suffix)
            return

        def func(item: Node) -> bool:
            return not (
                isinstance(item, InstanceList)
                and item.module.startswith(prefix)
                and item.module.endswith(suffix)
            )

        self._filter(func, "instance")

    def _del_instances_pyslang(self, prefix: str, suffix: str) -> None:
        for instance in self._instances:
            module_name = instance.type.valueText
            if module_name.startswith(prefix) and module_name.endswith(suffix):
                self._rewriter.remove(instance.sourceRange)

    def add_rs_pragmas(self) -> "Module":
        """Add RapidStream pragmas for existing ports.

        Note, this is based on port name suffix matching and may not be perfect.

        Returns
        -------
            Module: self, for chaining.

        """
        if Options.enable_pyslang:
            return self._add_rs_pragmas_pyslang()
        items = []
        for item in self._module_def.items:
            if isinstance(item, Decl):
                items.append(with_rs_pragma(item))
            else:
                items.append(item)
        self._module_def.items = tuple(items)
        self._calculate_indices()
        return self

    def _add_rs_pragmas_pyslang(self) -> "Module":
        self._syntax_tree = self._rewriter.commit()
        self._parse_syntax_tree()
        for port in self._ports.values():
            if port.rs_pragma is not None:
                self._rewriter.add_before(
                    self._port_name_to_decl[port.name].sourceRange.start,
                    _CODEGEN.visit(port.rs_pragma),
                )
        return self

    def del_pragmas(self, pragma: Iterable[str]) -> None:
        def func(item: Node) -> bool:
            return not isinstance(item, Pragma) or (
                item.entry.name != pragma and item.entry.name not in pragma
            )

        self._filter(func, "signal")

    def add_fifo_instance(
        self,
        name: str,
        rst: Node,
        width: Constant,
        depth: int,
    ) -> "Module":
        name = sanitize_array_name(name)

        def ports() -> Iterator[PortArg]:
            yield make_port_arg(port="clk", arg=CLK)
            yield make_port_arg(port="reset", arg=rst)
            for port_name, arg_suffix in zip(FIFO_READ_PORTS, ISTREAM_SUFFIXES):
                yield make_port_arg(port=port_name, arg=wire_name(name, arg_suffix))

            yield make_port_arg(port=FIFO_READ_PORTS[-1], arg=TRUE)
            for port_name, arg_suffix in zip(FIFO_WRITE_PORTS, OSTREAM_SUFFIXES):
                yield make_port_arg(port=port_name, arg=wire_name(name, arg_suffix))
            yield make_port_arg(port=FIFO_WRITE_PORTS[-1], arg=TRUE)

        module_name = "fifo"
        return self.add_instance(
            module_name=module_name,
            instance_name=name,
            ports=ports(),
            params=(
                ParamArg(paramname="DATA_WIDTH", argname=width),
                ParamArg(
                    paramname="ADDR_WIDTH",
                    argname=Constant(max(1, (depth - 1).bit_length())),
                ),
                ParamArg(paramname="DEPTH", argname=Constant(depth)),
            ),
        )

    def add_async_mmap_instance(  # noqa: PLR0913,PLR0917
        self,
        name: str,
        tags: Iterable[str],
        rst: Node,
        data_width: int,
        addr_width: int = 64,
        buffer_size: int | None = None,
        max_wait_time: int = 3,
        max_burst_len: int | None = None,
    ) -> "Module":
        paramargs = [
            ParamArg(paramname="DataWidth", argname=Constant(data_width)),
            ParamArg(
                paramname="DataWidthBytesLog",
                argname=Constant((data_width // 8 - 1).bit_length()),
            ),
        ]
        portargs = [
            make_port_arg(port="clk", arg=CLK),
            make_port_arg(port="rst", arg=rst),
        ]
        paramargs.append(ParamArg(paramname="AddrWidth", argname=Constant(addr_width)))
        if buffer_size:
            paramargs.extend(
                (
                    ParamArg(paramname="BufferSize", argname=Constant(buffer_size)),
                    ParamArg(
                        paramname="BufferSizeLog",
                        argname=Constant((buffer_size - 1).bit_length()),
                    ),
                ),
            )

        max_wait_time = max(1, max_wait_time)
        paramargs.extend(
            (
                ParamArg(
                    paramname="WaitTimeWidth",
                    argname=Constant(max_wait_time.bit_length()),
                ),
                ParamArg(
                    paramname="MaxWaitTime",
                    argname=Constant(max(1, max_wait_time)),
                ),
            ),
        )

        if max_burst_len is None:
            # 1KB burst length
            max_burst_len = max(0, 8192 // data_width - 1)
        paramargs.extend(
            (
                ParamArg(paramname="BurstLenWidth", argname=Constant(9)),
                ParamArg(paramname="MaxBurstLen", argname=Constant(max_burst_len)),
            ),
        )

        for channel, ports in M_AXI_PORTS.items():
            for port, _direction in ports:
                portargs.append(
                    make_port_arg(
                        port=f"{M_AXI_PREFIX}{channel}{port}",
                        arg=f"{M_AXI_PREFIX}{name}_{channel}{port}",
                    ),
                )

        tags = set(tags)
        for tag in ASYNC_MMAP_SUFFIXES:
            for suffix in ASYNC_MMAP_SUFFIXES[tag]:
                if tag in tags:
                    arg = async_mmap_arg_name(arg=name, tag=tag, suffix=suffix)
                elif suffix.endswith(("_read", "_write")):
                    arg = "1'b0"
                elif suffix.endswith("_din"):
                    arg = "'d0"
                else:
                    arg = ""
                portargs.append(make_port_arg(port=tag + suffix, arg=arg))

        return self.add_instance(
            module_name="async_mmap",
            instance_name=async_mmap_instance_name(name),
            ports=portargs,
            params=paramargs,
        )

    def find_port(self, prefix: str, suffix: str) -> str | None:
        """Find an IO port with given prefix and suffix in this module."""
        for port_name in self.ports:
            if port_name.startswith(prefix) and port_name.endswith(suffix):
                return port_name
        return None

    def add_m_axi(
        self,
        name: str,
        data_width: int,
        addr_width: int = 64,
        id_width: int | None = None,
    ) -> "Module":
        io_ports = []
        for channel, ports in M_AXI_PORTS.items():
            for port, direction in ports:
                io_port = (Input if direction == "input" else Output)(
                    name=f"{M_AXI_PREFIX}{name}_{channel}{port}",
                    width=get_m_axi_port_width(port, data_width, addr_width, id_width),
                )
                io_ports.append(with_rs_pragma(io_port))
        return self.add_ports(io_ports)

    def cleanup(self) -> None:
        self.del_params(prefix="ap_ST_fsm_state")
        self.del_signals(prefix="ap_CS_fsm")
        self.del_signals(prefix="ap_NS_fsm")
        self.del_signals(suffix="_read")
        self.del_signals(suffix="_write")
        self.del_signals(suffix="_blk_n")
        self.del_signals(suffix="_regslice")
        self.del_signals(prefix="regslice_")
        self.del_signals(HANDSHAKE_RST)
        self.del_signals(HANDSHAKE_DONE)
        self.del_signals(HANDSHAKE_IDLE)
        self.del_signals(HANDSHAKE_READY)
        self.del_logics()
        self.del_instances(suffix="_regslice_both")
        if not Options.enable_pyslang:
            self.del_pragmas("fsm_encoding")
        self.add_signals(
            map(
                Wire,
                (
                    HANDSHAKE_RST,
                    HANDSHAKE_DONE,
                    HANDSHAKE_IDLE,
                    HANDSHAKE_READY,
                ),
            ),
        )
        self.add_logics(
            [
                # `s_axi_control` still uses `ap_rst_n_inv`.
                Assign(left=Identifier(HANDSHAKE_RST), right=Unot(RST_N)),
            ],
        )


def get_streams_fifos(module: Module, streams_name: str) -> list[str]:
    """Get all FIFOs that are related to a streams."""
    pattern = re.compile(rf"{streams_name}_(\d+)_")
    fifos = set()

    for s in module.ports:
        match = pattern.match(s)
        if match:
            number = match.group(1)
            fifos.add(f"{streams_name}_{number}")

    # singleton array without number
    # when a stream array has length 1,
    # the generated port name may not have the number
    # in it.
    # e.g., in tests/functional/singleton/vadd.cpp,
    # "tapa::istreams<float, M>& a" where M = 1 resulted in
    # stream data port a_s_dout rather than a_0_dout.
    if not fifos:
        for s in module.ports:
            for infix in FIFO_INFIXES:
                if s.startswith(f"{streams_name}{infix}"):
                    return [streams_name]

    if not fifos:
        msg = f"no fifo found for {streams_name}"
        raise ValueError(msg)
    return list(fifos)


def generate_m_axi_ports(
    module: Module,
    port: str,
    arg: str,
    arg_reg: str = "",
) -> Iterator[PortArg]:
    """Generate AXI mmap ports that instantiate given module.

    Args:
    ----
        module (Module): Module that needs to be instantiated.
        port (str): Port name in the instantiated module.
        arg (str): Argument name in the instantiating module.
        arg_reg (str, optional): Registered argument name. Defaults to ''.

    Raises:
    ------
        ValueError: If the offset port cannot be found in the instantiated module.

    Yields:
    ------
        Iterator[PortArg]: PortArgs.

    """
    for suffix in M_AXI_SUFFIXES:
        yield make_port_arg(
            port=M_AXI_PREFIX + port + suffix,
            arg=M_AXI_PREFIX + arg + suffix,
        )
    for suffix in "_offset", "_data_V", "_V", "":
        if (port_name := port + suffix) in module.ports:
            yield make_port_arg(port=port_name, arg=arg_reg or arg)
            break
    else:
        msg = f"cannot find offset port for {port}"
        raise ValueError(msg)


def get_rs_port(port: str) -> str:
    """Return the RapidStream port for the given m_axi `port`."""
    if port in {"READY", "VALID"}:
        return port.lower()
    return "data"


def with_rs_pragma(node: Input | Output | Decl) -> Decl:
    """Return an `Decl` with RapidStream pragma for the given `node`."""
    items = []
    if isinstance(node, Input | Output):
        items.extend([ioport.IOPort.create(node).rs_pragma, node])
    elif isinstance(node, Decl):
        for item in node.list:
            if isinstance(item, Input | Output):
                items.append(ioport.IOPort.create(item).rs_pragma)
            # Decl with other node types is OK.
            items.append(item)
    else:
        msg = f"unexpected node: {node}"
        raise ValueError(msg)

    return Decl(tuple(x for x in items if x is not None))


@functools.singledispatch
def _get_name(node: object) -> str:
    raise TypeError(type(node))


@_get_name.register
def _(node: pyslang.ParameterDeclarationStatementSyntax) -> str:
    return _get_name(node.parameter)


@_get_name.register
def _(
    node: (
        pyslang.DataDeclarationSyntax
        | pyslang.NetDeclarationSyntax
        | pyslang.ParameterDeclarationSyntax
    ),
) -> str:
    return node.declarators[0].name.valueText


def _get_width(node: pyslang.DataTypeSyntax) -> Width | None:
    assert isinstance(node, pyslang.IntegerTypeSyntax | pyslang.ImplicitTypeSyntax)
    if node.dimensions:
        range_select = node.dimensions[0][1][0]
        assert isinstance(range_select, pyslang.RangeSelectSyntax)
        return Width(
            msb=Constant(str(range_select.left)),
            lsb=Constant(str(range_select.right)),
        )
    return None
