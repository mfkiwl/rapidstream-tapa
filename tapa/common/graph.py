"""Graph object class in TAPA."""

__copyright__ = """
Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
All rights reserved. The contributor(s) of this file has/have agreed to the
RapidStream Contributor License Agreement.
"""
import copy
from collections import defaultdict
from functools import lru_cache
from typing import TYPE_CHECKING

from tapa.common.base import Base
from tapa.common.task_definition import TaskDefinition
from tapa.common.task_instance import TaskInstance
from tapa.util import get_instance_name

if TYPE_CHECKING:
    from tapa.common.interconnect_instance import InterconnectInstance


class Graph(Base):
    """TAPA task graph."""

    @lru_cache(None)
    def get_task_def(self, name: str) -> TaskDefinition:
        """Returns the task definition which is named `name`."""
        assert isinstance(self.obj["tasks"], dict)
        return TaskDefinition(name, self.obj["tasks"][name], self)

    @lru_cache(None)
    def get_top_task_def(self) -> TaskDefinition:
        """Returns the top-level task instance."""
        return self.get_task_def(self.get_top_task_name())

    @lru_cache(None)
    def get_top_task_inst(self) -> TaskInstance:
        """Returns the top-level task instance."""
        name = self.get_top_task_name()
        return TaskInstance(0, name, None, self, self.get_top_task_def())

    @lru_cache(None)
    def get_top_task_name(self) -> str:
        """Returns the name of the top-level task."""
        assert isinstance(self.obj["top"], str)
        return self.obj["top"]

    def get_flatten_graph(self) -> "Graph":
        """Returns the flatten graph with all leaf tasks as the top's children."""
        # Construct obj of the new flatten graph
        new_obj = self.to_dict()

        # Find all leaf task instances
        leaves = self.get_top_task_inst().get_leaf_tasks_insts()

        # obj['tasks']:
        # Reconstruct the task definitions of the graph
        #   are (1) all relevant definitions of the leaf tasks
        defs = {leaf.definition for leaf in leaves}
        new_obj["tasks"] = {d.name: d.to_dict() for d in defs}
        #   and (2) also the top task
        top_name = self.obj["top"]
        assert isinstance(top_name, str)
        assert isinstance(new_obj["tasks"], dict)
        new_obj["tasks"][top_name] = self.get_top_task_def().to_dict()

        # obj['tasks'][top_task]['tasks']:
        # Reconstruct the subtasks of the top level task
        #   insts = {definition: [instance, ...], ...}
        insts = {i: [j for j in leaves if j.definition is i] for i in defs}
        #   {definition.name: [instance.to_dict(), ...], ...}
        new_subtask_instantiations = {
            definition.name: [
                inst.to_dict(interconnect_global_name=True)
                for inst in insts_of_definition
            ]
            for definition, insts_of_definition in insts.items()
        }
        new_obj["tasks"][top_name]["tasks"] = new_subtask_instantiations

        # obj['tasks'][top_task]['fifos']:
        # Reconstruct the local interconnects of the top level task
        #   -> [instance, ...]
        interconnect_insts = self.get_top_task_inst().recursive_get_interconnect_insts()
        #   -> {instance.gid: instance.definition.to_dict()}
        new_obj["tasks"][top_name]["fifos"] = {
            i.global_name: i.to_dict(
                insts_override=new_subtask_instantiations,
                interconnect_global_name=True,
            )
            for i in interconnect_insts
        }

        return Graph(self.name, new_obj)

    def get_floorplan_slot(
        self, slot_name: str, task_inst_in_slot: list[str], top: Base
    ) -> TaskDefinition:
        """Return the task grouping the tasks floorplanned in the given slot."""
        # Construct obj of the slot by modifying the top task
        new_obj = self.get_top_task_def().to_dict()

        new_obj["level"] = "upper"

        # Find all task instances
        insts = self.get_top_task_inst().get_subtasks_insts()
        assert all(inst in [inst.name for inst in insts] for inst in task_inst_in_slot)
        for inst in insts:
            # make sure top has been flattened
            assert isinstance(inst.definition, TaskDefinition)
            assert inst.definition.get_level() == TaskDefinition.Level.LEAF, (
                "Top task must be flattened for floorplanning"
            )

        # filter out tasks that are not in the slot
        new_insts = [inst for inst in insts if inst.name in task_inst_in_slot]

        assert new_insts, [inst.name for inst in insts]

        # obj['tasks']:
        # construct the task insts of the slot
        new_obj["tasks"] = defaultdict(list)
        for inst in new_insts:
            new_obj["tasks"][inst.definition.name].append(inst.to_dict())

        # obj['fifos']:
        # construct the fifos of the slot
        fifos = self.get_top_task_inst().get_interconnect_insts()
        new_fifos: list[InterconnectInstance] = []
        fifo_ports: list[str] = []
        for fifo in fifos:
            fifo_obj = fifo.to_dict()
            src = fifo_obj["consumed_by"]
            dst = fifo_obj["produced_by"]
            # For fifo connecting task insts inside the slot, keep it
            if (
                get_instance_name((src[0], src[1])) in task_inst_in_slot
                and get_instance_name((dst[0], dst[1])) in task_inst_in_slot
            ):
                new_fifos.append(fifo)
            # For fifo connecting a task inst inside and an inst outside the slot,
            # put it to ports later
            elif (
                get_instance_name((src[0], src[1])) in task_inst_in_slot
                or get_instance_name((dst[0], dst[1])) in task_inst_in_slot
            ):
                fifo_ports.append(fifo.name)
        new_obj["fifos"] = {fifo.name: fifo.to_dict() for fifo in new_fifos}

        # obj['ports']:
        # Reconstruct the ports of the slot
        # Remove all ports that are not used by the insts in the slot
        used_args: set[str] = set()
        for inst in new_insts:
            assert isinstance(inst.obj["args"], dict)
            args = inst.obj["args"]
            assert isinstance(args, dict)
            for arg in args.values():
                assert isinstance(arg, dict)
                assert isinstance(arg["arg"], str)
                used_args.add(arg["arg"])
        assert isinstance(new_obj["ports"], list)
        new_ports = [port for port in new_obj["ports"] if port["name"] in used_args]

        # Add fifos connecting the slot and the outside as ports
        # Find the port on inst that connects to the fifo and copy
        # the port to the slot
        new_ports += _get_used_ports(new_insts, fifo_ports)

        new_obj["ports"] = new_ports

        return TaskDefinition(slot_name, new_obj, top)

    def get_floorplan_top(self, slot_defs: dict[str, TaskDefinition]) -> TaskDefinition:
        """Return the new top level by grouping slot instances."""
        top_obj = self.get_top_task_def().to_dict()
        assert top_obj["level"] != "lower"
        new_top_obj = copy.deepcopy(top_obj)

        # obj['tasks']:
        # construct slot instances
        new_top_insts = defaultdict(list)
        for slot_name, slot_def in slot_defs.items():
            assert isinstance(slot_def.obj["ports"], list)
            ports: dict[str, dict] = {p["name"]: p for p in slot_def.obj["ports"]}
            args = {}
            for port_name, port_obj in ports.items():
                args[port_name] = {"arg": port_name, "cat": port_obj["cat"]}
            new_top_insts[slot_name].append({"args": args, "step": 0})
        new_top_obj["tasks"] = new_top_insts

        # obj['fifos']:
        # remove all fifos except the ones connecting the slots
        in_slot_fifos = []
        for slot_def in slot_defs.values():
            assert isinstance(slot_def.obj["fifos"], dict)
            in_slot_fifos += slot_def.obj["fifos"].keys()

        assert isinstance(top_obj["fifos"], dict)
        new_top_obj["fifos"] = {
            name: fifo
            for name, fifo in top_obj["fifos"].items()
            if name not in in_slot_fifos
        }

        return TaskDefinition(self.get_top_task_name(), new_top_obj, self)

    def get_floorplan_graph(self, slot_to_insts: dict[str, list[str]]) -> "Graph":
        """Generate floorplanned graph."""
        new_obj = self.to_dict()

        assert isinstance(new_obj["tasks"], dict)
        slot_defs = {}
        for slot_name, insts in slot_to_insts.items():
            slot_def = self.get_floorplan_slot(slot_name, insts, self)
            assert slot_name not in new_obj["tasks"]
            new_obj["tasks"][slot_name] = slot_def.to_dict()
            slot_defs[slot_name] = slot_def

        top_name = self.get_top_task_name()
        assert top_name in new_obj["tasks"]
        new_obj["tasks"][top_name] = self.get_floorplan_top(slot_defs).to_dict()

        return Graph(self.name, new_obj)


def _get_used_ports(new_insts: list[TaskInstance], fifo_ports: list[str]) -> list:
    """Find the port on inst that connects to the fifo."""
    new_ports = []
    for inst in new_insts:
        assert isinstance(inst.obj["args"], dict)
        args = inst.obj["args"]
        assert isinstance(args, dict)
        for port_name, arg in args.items():
            if arg["arg"] not in fifo_ports:
                continue
            ports = inst.definition.obj["ports"]
            assert isinstance(ports, list)
            for port in ports:
                if port["name"] != port_name:
                    continue
                new_port = port.copy()
                new_port["name"] = arg["arg"]
                new_ports.append(new_port)

    return new_ports
