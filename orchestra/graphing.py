# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import copy
import logging

import networkx as nx
from networkx.readwrite import json_graph
import six

from orchestra import exceptions as exc
from orchestra.expressions import base as expressions
from orchestra.utils import dictionary as dict_utils
from orchestra import states


LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class WorkflowGraph(object):

    def __init__(self, graph=None):
        self._graph = graph if graph else nx.MultiDiGraph()

    def serialize(self):
        return json_graph.adjacency_data(self._graph)

    @classmethod
    def deserialize(cls, data):
        g = json_graph.adjacency_graph(data, directed=True, multigraph=True)

        return cls(graph=g)

    @property
    def state(self):
        return self._graph.graph.get('state', None)

    @state.setter
    def state(self, value):
        if value not in states.ALL_STATES:
            raise ValueError('State "%s" is not valid.', value)

        if not states.is_transition_valid(self.state, value):
            raise exc.InvalidStateTransition(self.state, value)

        self._graph.graph['state'] = value

    def has_task(self, task_id):
        return self._graph.has_node(task_id)

    def get_task(self, task_id):
        if not self.has_task(task_id):
            raise Exception('Task "%s" does not exist.', task_id)

        task = {'id': task_id}
        task.update(copy.deepcopy(self._graph.node[task_id]))

        return task

    def get_task_attributes(self, attribute):
        return dict_utils.merge_dicts(
            {n: None for n in self._graph.nodes()},
            nx.get_node_attributes(self._graph, attribute),
            overwrite=True
        )

    def add_task(self, task_id, **kwargs):
        if not self.has_task(task_id):
            self._graph.add_node(task_id, **kwargs)
        else:
            self.update_task(task_id, **kwargs)

    def update_task(self, task_id, **kwargs):
        if not self.has_task(task_id):
            raise Exception('Task "%s" does not exist.', task_id)

        # Check if change in task state is valid.
        old_state = self._graph.node[task_id].get('state', None)
        new_state = kwargs.get('state', None)

        if not states.is_transition_valid(old_state, new_state):
            raise exc.InvalidStateTransition(old_state, new_state)

        # Update the task attributes.
        for key, value in six.iteritems(kwargs):
            self._graph.node[task_id][key] = value

    def reset_task(self, task_id):
        if not self.has_task(task_id):
            raise Exception('Task "%s" does not exist.', task_id)

        task_attrs = {}

        if 'name' in self._graph.node[task_id]:
            task_attrs['name'] = self._graph.node[task_id]['name']

        if 'barrier' in self._graph.node[task_id]:
            task_attrs['barrier'] = self._graph.node[task_id]['barrier']

        self._graph.node[task_id] = task_attrs

    def get_start_tasks(self):
        tasks = [
            {'id': n, 'name': self._graph.node[n].get('name', n)}
            for n, d in self._graph.in_degree().items() if d == 0
        ]

        return sorted(tasks, key=lambda x: x['name'])

    def get_next_tasks(self, task_id, context=None):
        task = self.get_task(task_id)

        if task['state'] not in states.COMPLETED_STATES:
            return []

        context = dict_utils.merge_dicts(
            context or {},
            {'__task_states': self.get_task_attributes('state')},
            overwrite=True
        )

        tasks = []
        outbounds = []

        for seq in self.get_next_transitions(task_id):
            evaluated_criteria = [
                expressions.evaluate(criterion, context)
                for criterion in seq[3]['criteria']
            ]

            if all(evaluated_criteria):
                outbounds.append(seq)

        for seq in outbounds:
            next_task_id, seq_key, attrs = seq[1], seq[2], seq[3]

            if not attrs.get('satisfied', False):
                self.update_transition(task_id, next_task_id, key=seq_key, satisfied=True)

            if self.has_barrier(next_task_id):
                barrier = self.get_barrier(next_task_id)
                inbounds = self.get_prev_transitions(next_task_id)
                satisfied = [s for s in inbounds if s[3].get('satisfied')]
                barrier = len(inbounds) if barrier == '*' else barrier

                if len(satisfied) < barrier:
                    continue

            next_task = self.get_task(next_task_id)
            tasks.append({'id': next_task_id, 'name': next_task['name']})

        return sorted(tasks, key=lambda x: x['name'])

    def has_transition(self, source, destination, criteria=None):
        return [
            edge for edge in self._graph.edges(data=True, keys=True)
            if (edge[0] == source and edge[1] == destination and
                edge[3].get('criteria', None) == criteria)
        ]

    def get_transition(self, source, destination, key=None, criteria=None):
        seqs = [
            edge for edge in self._graph.edges(data=True, keys=True)
            if (edge[0] == source and edge[1] == destination and (
                edge[3].get('criteria', None) == criteria or
                edge[2] == key))
        ]

        if not seqs:
            raise Exception('Task transition does not exist.')

        if len(seqs) > 1:
            raise Exception('More than one task transitions found.')

        return seqs[0]

    def get_transition_attributes(self, attribute):
        return nx.get_edge_attributes(self._graph, attribute)

    def add_transition(self, source, destination, criteria=None):
        if not self.has_task(source):
            self.add_task(source)

        if not self.has_task(destination):
            self.add_task(destination)

        seqs = self.has_transition(source, destination, criteria)

        if len(seqs) > 1:
            raise Exception('More than one task transitions found.')

        if not seqs:
            self._graph.add_edge(source, destination, criteria=criteria)
        else:
            self.update_transition(source, destination, key=seqs[0][2], criteria=criteria)

    def update_transition(self, source, destination, key, **kwargs):
        seq = self.get_transition(source, destination, key=key)

        for attr, value in six.iteritems(kwargs):
            self._graph[source][destination][seq[2]][attr] = value

    def get_next_transitions(self, task_id):
        return sorted(
            [e for e in self._graph.out_edges([task_id], data=True, keys=True)],
            key=lambda x: x[1]
        )

    def get_prev_transitions(self, task_id):
        return sorted(
            [e for e in self._graph.in_edges([task_id], data=True, keys=True)],
            key=lambda x: x[1]
        )

    def set_barrier(self, task_id, value='*'):
        self.update_task(task_id, barrier=value)

    def get_barrier(self, task_id):
        return self.get_task(task_id).get('barrier')

    def has_barrier(self, task_id):
        b = self.get_barrier(task_id)

        return (b is not None and b != '')

    def in_cycle(self, task_id):
        return [c for c in nx.simple_cycles(self._graph) if task_id in c]
