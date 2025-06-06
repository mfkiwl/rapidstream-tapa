"""TAPA abstract base classes.

This module defines abstract base classes used internally by TAPA, which helps
distributing implementation details among multiple modules.
"""

__copyright__ = """
Copyright (c) 2025 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""

from abc import abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapa.task import Task


# TODO: add docstrings for each property/method.
class ProgramInterface:
    @property
    @abstractmethod
    def top_task(self) -> "Task":
        pass

    @abstractmethod
    def get_task(self, name: str) -> "Task":
        pass

    @abstractmethod
    def _get_part_num(self, name: str) -> str:
        pass
