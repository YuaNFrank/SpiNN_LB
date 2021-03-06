# 
#   This is a basic implementation of a lattice Boltzmann method on SpiNNaker.
#   This project is currently being released under the GPL 3.0 license. Use it Free as in
#   Freedom. 
#  
#   The up-to-date information of SpiNNaker Project can be found here:
#   https://spinnakermanchester.github.io/

from collections import OrderedDict
from enum import Enum
import struct

from spinn_front_end_common.abstract_models import AbstractProvidesNKeysForPartition
from spinn_utilities.overrides import overrides
from pacman.executor.injection_decorator import inject_items
from pacman.model.graphs.machine import MachineVertex
from pacman.model.resources import ResourceContainer, VariableSDRAM
from pacman.utilities.utility_calls import is_single
from spinn_front_end_common.utilities.constants import (
    SYSTEM_BYTES_REQUIREMENT, BYTES_PER_WORD)
from spinn_front_end_common.utilities.exceptions import ConfigurationException
from spinn_front_end_common.utilities.helpful_functions import (
    locate_memory_region_for_placement)
from spinn_front_end_common.abstract_models.impl import (
    MachineDataSpecableVertex)
from spinn_front_end_common.interface.buffer_management.buffer_models import (
    AbstractReceiveBuffersToHost)
from spinn_front_end_common.interface.buffer_management import (
    recording_utilities)
from spinnaker_graph_front_end.utilities import SimulatorVertex
from spinnaker_graph_front_end.utilities.data_utils import (
    generate_system_data_region)

from data_specification.enums.data_type import DataType

import spinnaker_graph_front_end as front_end

from spinn_front_end_common.utilities import exceptions
import logging

logger = logging.getLogger(__name__)


class LatticeBasicCell(
    SimulatorVertex, MachineDataSpecableVertex,
    AbstractReceiveBuffersToHost,
    AbstractProvidesNKeysForPartition
):
    """ A Boltzmann Lattice which represents a cell within the 2d fabric
    """
    
    PARTITION_ID = "STATE"

    TRANSMISSION_DATA_SIZE = 2 * BYTES_PER_WORD  # has key and key
    STATE_DATA_SIZE = BYTES_PER_WORD  # 1 or 2 based off dead or alive
    # alive states, dead states
    NEIGHBOUR_INITIAL_STATES_SIZE = 2 * BYTES_PER_WORD
    RECORDING_ELEMENT_SIZE = STATE_DATA_SIZE  # A recording of the volocity

    POSITION_DATA_SIZE = 2 * BYTES_PER_WORD # x position and y position

    NEIGHBOUR_KEYS_SIZE = 9 * BYTES_PER_WORD  # for 8 directions and a mask

    VELOCITY_SIZE = 2 * BYTES_PER_WORD  # u_x and u_y

    VERTEX_INDEX_SIZE = 2 * BYTES_PER_WORD # the index of the lattice in the network

    # The order of which directions are writeen to sdram
    ORDER_OF_DIRECTIONS = ["N", "W", "S", "E", "NW", "SW", "SE", "NE"]
    # Regions for populations
    DATA_REGIONS = Enum(
        value="DATA_REGIONS",
        names=[('SYSTEM', 0),
               ('TRANSMISSIONS', 1),
               ('POSITION', 2),
               ('NEIGHBOUR_KEYS', 3),
               ('VELOCITY', 4),
               ('VERTEX_INDEX', 5),
               ('RESULTS', 6)])

    def __init__(self, label, x_position, y_position, u_x, u_y):
        super(LatticeBasicCell, self).__init__(label, "lattice_cell.aplx")
        AbstractProvidesNKeysForPartition.__init__(self)
        # app specific data items
        self._x_position = x_position
        self._y_position = y_position
        self.u_x = u_x
        self.u_y = u_y
        
        self._loccation_vertices = OrderedDict()
        for direction in self.ORDER_OF_DIRECTIONS:
            self._loccation_vertices[direction] = None

    def set_direction_vertex(self, direction, vertex):
        """
        Add a vertex to the corresponding direction
        """
        self._loccation_vertices[direction] = vertex

    def _write_key_data(self, spec, routing_info, graph):
        """
        Write the keys of its 8 neighbours
        """
        spec.switch_write_focus(region=self.DATA_REGIONS.NEIGHBOUR_KEYS.value)
        incoming_edges = graph.get_edges_ending_at_vertex_with_partition_name(self, self.PARTITION_ID)

        # verify the number of edges
        if len(incoming_edges) != 8:
            print(incoming_edges)
            raise exceptions.ConfigurationException("Should only have 8 edges")

        # get incoming edges
        for direction in self._loccation_vertices:
            key = routing_info.get_routing_info_from_pre_vertex(
                self._loccation_vertices[direction],
                self.PARTITION_ID).keys_and_masks[0].key
            if key is not None:
                spec.write_value(data=key)
            else:
                logger.warning("This lattice miss a edge from direction {}".format(direction))
                spec.write_value(data_type=DataType.INT32, data=-1)

        mask = routing_info.get_routing_info_from_pre_vertex(self._loccation_vertices["S"],
                                                             self.PARTITION_ID).keys_and_masks[0].mask
        spec.write_value(data=mask, data_type=DataType.UINT32)

    @inject_items({"data_n_time_steps": "DataNTimeSteps"})
    @overrides(
        MachineDataSpecableVertex.generate_machine_data_specification,
        additional_arguments={"data_n_time_steps"})
    def generate_machine_data_specification(
            self, spec, placement, machine_graph, routing_info, iptags,
            reverse_iptags, machine_time_step, time_scale_factor,
            data_n_time_steps):
        # Generate the system data region for simulation .c requirements
        generate_system_data_region(spec, self.DATA_REGIONS.SYSTEM.value,
                                    self, machine_time_step, time_scale_factor)

        # reserve memory regions for every data region
        spec.reserve_memory_region(
            region=self.DATA_REGIONS.TRANSMISSIONS.value,
            size=self.TRANSMISSION_DATA_SIZE, label="inputs")

        spec.reserve_memory_region(
            region=self.DATA_REGIONS.POSITION.value,
            size=self.POSITION_DATA_SIZE, label="position"
        )

        spec.reserve_memory_region(
            region=self.DATA_REGIONS.NEIGHBOUR_KEYS.value,
            size=self.NEIGHBOUR_KEYS_SIZE
        )

        spec.reserve_memory_region(
            region=self.DATA_REGIONS.VELOCITY.value,
            size=self.VELOCITY_SIZE
        )

        spec.reserve_memory_region(
            region=self.DATA_REGIONS.VERTEX_INDEX.value,
            size=self.VERTEX_INDEX_SIZE
        )

        spec.reserve_memory_region(
            region=self.DATA_REGIONS.RESULTS.value,
            size=recording_utilities.get_recording_header_size(1))

        # get recorded buffered regions sorted
        spec.switch_write_focus(self.DATA_REGIONS.RESULTS.value)
        spec.write_array(recording_utilities.get_recording_header_array(
            [self.RECORDING_ELEMENT_SIZE * data_n_time_steps]))

        # check got right number of keys and edges going into me
        partitions = \
            machine_graph.get_outgoing_edge_partitions_starting_at_vertex(self)
        if not is_single(partitions):
            raise ConfigurationException(
                "Can only handle one type of partition.")

        # check for duplicates
        edges = list(machine_graph.get_edges_ending_at_vertex(self))
        if len(edges) != 8:
            raise ConfigurationException(
                "I've not got the right number of connections. I have {} "
                "instead of 8".format(
                    len(machine_graph.get_edges_ending_at_vertex(self))))

        for edge in edges:
            if edge.pre_vertex == self:
                raise ConfigurationException(
                    "I'm connected to myself, this is deemed an error"
                    " please fix.")

        # write key needed to transmit with
        key = routing_info.get_first_key_from_pre_vertex(
            self, self.PARTITION_ID)

        spec.switch_write_focus(
            region=self.DATA_REGIONS.TRANSMISSIONS.value)
        spec.write_value(0 if key is None else 1)
        spec.write_value(0 if key is None else key)

        # write POSITION data
        spec.switch_write_focus(
            region=self.DATA_REGIONS.POSITION.value
        )
        spec.write_value(int(self._x_position))
        spec.write_value(int(self._y_position))

        #write VERTEX_INDEX data. Mainly for add a random delay
        spec.switch_write_focus(region=self.DATA_REGIONS.VERTEX_INDEX.value)
        spec.write_value(machine_graph.vertices.index(self))
        self.offset = generate_offset(placement.p)
        spec.write_value(self.offset)

        # write the neighbour keys and masks
        self._write_key_data(spec, routing_info, machine_graph)

        #write velocity data in two dimension, x and y
        spec.switch_write_focus(region=self.DATA_REGIONS.VELOCITY.value)
        spec.write_value(self.u_x, data_type=DataType.FLOAT_32)
        spec.write_value(self.u_y, data_type=DataType.FLOAT_32)

        # End-of-Spec:
        spec.end_specification()

    def get_data(self, buffer_manager, placement):
        # for buffering output info is taken form the buffer manager
        # get raw data, convert to list of booleans
        raw_data, data_missing = buffer_manager.get_data_by_placement(
            placement, 0)

        # do check for missing data
        if data_missing:
            print("missing_data from ({}, {}, {}); ".format(
                placement.x, placement.y, placement.p))

        # return the data, converted to list of booleans
        return [
            element
            for element in struct.unpack(
                "<{}f".format(len(raw_data) // BYTES_PER_WORD), raw_data)]

    @property
    @overrides(MachineVertex.resources_required)
    def resources_required(self):
        """
        Reserve resources for data regions
        """
        fixed_sdram = (SYSTEM_BYTES_REQUIREMENT + self.TRANSMISSION_DATA_SIZE +
                       self.POSITION_DATA_SIZE +
                       self.NEIGHBOUR_KEYS_SIZE +
                       self.VELOCITY_SIZE +
                       self.VERTEX_INDEX_SIZE +
                       recording_utilities.get_recording_header_size(1) +
                       recording_utilities.get_recording_data_constant_size(1))
        per_timestep_sdram = self.RECORDING_ELEMENT_SIZE
        return ResourceContainer(
            sdram=VariableSDRAM(fixed_sdram, per_timestep_sdram))

    @property
    def x_position(self):
        return self._x_position

    @overrides(AbstractProvidesNKeysForPartition.get_n_keys_for_partition)
    def get_n_keys_for_partition(self, partition, graph_mapper):
        """
        Ask for 8 keys for every lattice for the transmission of 8 fi
        """
        return 8  # for its 8 neighbours to send

    @property
    def y_position(self):
        return self._y_position

    def __repr__(self):
        return self.label

    @overrides(AbstractReceiveBuffersToHost.get_recorded_region_ids)
    def get_recorded_region_ids(self):
        return [0]

    @overrides(AbstractReceiveBuffersToHost.get_recording_region_base_address)
    def get_recording_region_base_address(self, txrx, placement):
        return locate_memory_region_for_placement(
            placement, self.DATA_REGIONS.RESULTS.value, txrx)
