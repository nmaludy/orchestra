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
import logging
import re
import six

from stevedore import extension

from orchestra.expressions import utils
from orchestra.utils import plugin


LOG = logging.getLogger(__name__)

_EXP_EVALUATORS = None
_EXP_EVALUATOR_NAMESPACE = 'orchestra.expressions.evaluators'
_REGEX_VAR_EXTRACT = '\%s\.([a-zA-Z0-9_\-]*)\.?'


@six.add_metaclass(abc.ABCMeta)
class Evaluator(object):
    _type = 'unspecified'
    _delimiter = None

    @classmethod
    def get_type(cls):
        return cls._type

    @classmethod
    def strip_delimiter(cls, expr):
        return expr.strip(cls._delimiter).strip()

    @classmethod
    def has_expressions(cls, text):
        raise NotImplementedError()

    @classmethod
    @abc.abstractmethod
    def validate(cls, statement):
        raise NotImplementedError()

    @classmethod
    @abc.abstractmethod
    def evaluate(cls, text, data=None):
        raise NotImplementedError()

    @classmethod
    @abc.abstractmethod
    def extract_vars(cls, statement):
        raise NotImplementedError()


def get_evaluator(language):
    return plugin.get_module(_EXP_EVALUATOR_NAMESPACE, language)


def get_evaluators():
    global _EXP_EVALUATORS

    if _EXP_EVALUATORS is None:
        _EXP_EVALUATORS = {}

        mgr = extension.ExtensionManager(
            namespace=_EXP_EVALUATOR_NAMESPACE,
            invoke_on_load=False
        )

        for name in mgr.names():
            _EXP_EVALUATORS[name] = get_evaluator(name)

    return _EXP_EVALUATORS


def validate(statement):

    errors = []

    if isinstance(statement, dict):
        for k, v in six.iteritems(statement):
            errors.extend(validate(k)['errors'])
            errors.extend(validate(v)['errors'])

    elif isinstance(statement, list):
        for item in statement:
            errors.extend(validate(item)['errors'])

    elif isinstance(statement, six.string_types):
        evaluators = [
            evaluator for name, evaluator in six.iteritems(get_evaluators())
            if evaluator.has_expressions(statement)
        ]

        if len(evaluators) == 1:
            errors.extend(evaluators[0].validate(statement))
        elif len(evaluators) > 1:
            message = 'Expression with multiple types is not supported.'
            errors.append(utils.format_error(None, statement, message))

    return {'errors': errors}


def evaluate(statement, data=None):

    if isinstance(statement, dict):
        return {
            evaluate(k, data=data): evaluate(v, data=data)
            for k, v in six.iteritems(statement)
        }

    elif isinstance(statement, list):
        return [evaluate(item, data=data) for item in statement]

    elif isinstance(statement, six.string_types):
        for name, evaluator in six.iteritems(get_evaluators()):
            if evaluator.has_expressions(statement):
                return evaluator.evaluate(statement, data=data)

    return statement


def extract_vars(statement):

    variables = []

    if isinstance(statement, dict):
        for k, v in six.iteritems(statement):
            variables.extend(extract_vars(k))
            variables.extend(extract_vars(v))

    elif isinstance(statement, list):
        for item in statement:
            variables.extend(extract_vars(item))

    elif isinstance(statement, six.string_types):
        for name, evaluator in six.iteritems(get_evaluators()):
            regex_var_extract = _REGEX_VAR_EXTRACT % evaluator._var_symbol

            for var_ref in evaluator.extract_vars(statement):
                var = re.search(regex_var_extract, var_ref).group(1)
                variables.append((evaluator.get_type(), statement, var))

    return sorted(list(set(variables)), key=lambda var: var[2])
