"""A union type to represent any type of a module definition."""

__copyright__ = """
Copyright (c) 2025 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""

from typing import Annotated

from pydantic import Field

from tapa.graphir.types.modules.definitions.aux import (
    AuxModuleDefinition,
    AuxSplitModuleDefinition,
)
from tapa.graphir.types.modules.definitions.grouped import GroupedModuleDefinition
from tapa.graphir.types.modules.definitions.internal import (
    InternalGroupedModuleDefinition,
    InternalVerilogModuleDefinition,
)
from tapa.graphir.types.modules.definitions.pass_through import (
    PassThroughModuleDefinition,
)
from tapa.graphir.types.modules.definitions.stub import StubModuleDefinition
from tapa.graphir.types.modules.definitions.verilog import VerilogModuleDefinition

AnyModuleDefinition = Annotated[
    AuxModuleDefinition
    | AuxSplitModuleDefinition
    | GroupedModuleDefinition
    | InternalGroupedModuleDefinition
    | InternalVerilogModuleDefinition
    | StubModuleDefinition
    | PassThroughModuleDefinition
    | VerilogModuleDefinition,
    Field(discriminator="module_type"),
]
