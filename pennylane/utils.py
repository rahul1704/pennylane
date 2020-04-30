# Copyright 2018-2020 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
This module contains utilities and auxiliary functions which are shared
across the PennyLane submodules.
"""
# pylint: disable=protected-access
from collections.abc import Iterable
from collections import OrderedDict
import copy
import numbers
import functools
import inspect
import itertools
import operator

import numpy as np

import pennylane as qml
from pennylane.variable import Variable


def decompose_hamiltonian(H):
    """Decomposes a Hermitian matrix into a linear combination of Pauli operators.
    **Example:**

    We can use this function to compute the Pauli operator decomposition of an arbitrary Hermitian
    matrix:

    >>> A = np.array([[-2, -2+1j, -2, -2], [-2-1j,  0,  0, -1], [-2,  0, -2, -1], [-2, -1, -1,  0]])
    >>>  coeffs, obs_list = decompose_hamiltonian(A)
    >>> coeffs
    [-1.0, -1.5, -0.5, -1.0, -1.5, -1.0, -0.5, 1.0, -0.5, -0.5]

    We can use the output coefficients and tensor Pauli terms to construct a :class:`~.Hamiltonian`:
    >>> H = qml.Hamiltonian(coeffs, obs_list)
    >>> print(H)
    (-1.0) [I0 I1]
    + (-1.5) [X1]
    + (-0.5) [Y1]
    + (-1.0) [Z1]
    + (-1.5) [X0]
    + (-1.0) [X0 X1]
    + (-0.5) [X0 Z1]
    + (1.0) [Y0 Y1]
    + (-0.5) [Z0 X1]
    + (-0.5) [Z0 Y1]

    This Hamiltonian can then be used in defining VQE problems using :class:`~VQECost`.

        Args:
            H (array[complex]): an Hermitian matrix of dimension :math:`2^N\times 2^N`

        Returns:
            tuple[list[float], list[~.Observable]]: Returns a list of tensor products of PennyLane Pauli observables, as
            well as the corresponding coefficients for each tensor product.
        """
    N = int(np.log2(len(H)))
    if len(H) - 2 ** N != 0:
        raise ValueError("Hamiltonian should be in the form (n^2 x n^2), for any n>=1")
        
    paulis = [qml.Identity, qml.PauliX, qml.PauliY, qml.PauliZ]
    obs = []
    coeffs = []
    
    for term in itertools.product(paulis, repeat=N):
        matrices = [i._matrix() for i in term]
        coeff = np.trace(functools.reduce(np.kron, matrices) @ H) / (2 ** N)
        coeff = np.real_if_close(coeff).item()
        
        if not np.allclose(coeff, 0):
            coeffs.append(coeff)
            
            if not all(t is qml.Identity for t in term):
                obs.append(
                    functools.reduce(
                        operator.matmul, [t(i) for i, t in enumerate(term) if t is not qml.Identity]
                    )
                )
            else:
                obs.append(functools.reduce(operator.matmul, [t(i) for i, t in enumerate(term)]))
                
    return coeffs, obs


def _flatten(x):
    """Iterate recursively through an arbitrarily nested structure in depth-first order.

    See also :func:`_unflatten`.

    Args:
        x (array, Iterable, Any): each element of an array or an Iterable may itself be any of these types

    Yields:
        Any: elements of x in depth-first order
    """
    if isinstance(x, np.ndarray):
        yield from _flatten(x.flat)  # should we allow object arrays? or just "yield from x.flat"?
    elif isinstance(x, Iterable) and not isinstance(x, (str, bytes)):
        for item in x:
            yield from _flatten(item)
    else:
        yield x


def _unflatten(flat, model):
    """Restores an arbitrary nested structure to a flattened iterable.

    See also :func:`_flatten`.

    Args:
        flat (array): 1D array of items
        model (array, Iterable, Number): model nested structure

    Raises:
        TypeError: if ``model`` contains an object of unsupported type

    Returns:
        Union[array, list, Any], array: first elements of flat arranged into the nested
        structure of model, unused elements of flat
    """
    if isinstance(model, (numbers.Number, Variable, str)):
        return flat[0], flat[1:]

    if isinstance(model, np.ndarray):
        idx = model.size
        res = np.array(flat)[:idx].reshape(model.shape)
        return res, flat[idx:]

    if isinstance(model, Iterable):
        res = []
        for x in model:
            val, flat = _unflatten(flat, x)
            res.append(val)
        return res, flat

    raise TypeError("Unsupported type in the model: {}".format(type(model)))


def unflatten(flat, model):
    """Wrapper for :func:`_unflatten`.

    Args:
        flat (array): 1D array of items
        model (array, Iterable, Number): model nested structure

    Raises:
        ValueError: if ``flat`` has more elements than ``model``
    """
    # pylint:disable=len-as-condition
    res, tail = _unflatten(np.asarray(flat), model)
    if len(tail) != 0:
        raise ValueError("Flattened iterable has more elements than the model.")
    return res


def _inv_dict(d):
    """Reverse a dictionary mapping.

    Returns multimap where the keys are the former values,
    and values are sets of the former keys.

    Args:
        d (dict[a->b]): mapping to reverse

    Returns:
        dict[b->set[a]]: reversed mapping
    """
    ret = {}
    for k, v in d.items():
        ret.setdefault(v, set()).add(k)
    return ret


def _get_default_args(func):
    """Get the default arguments of a function.

    Args:
        func (callable): a function

    Returns:
        dict[str, tuple]: mapping from argument name to (positional idx, default value)
    """
    signature = inspect.signature(func)
    return {
        k: (idx, v.default)
        for idx, (k, v) in enumerate(signature.parameters.items())
        if v.default is not inspect.Parameter.empty
    }


def expand(U, wires, num_wires):
    r"""Expand a multi-qubit operator into a full system operator.

    Args:
        U (array): :math:`2^n \times 2^n` matrix where n = len(wires).
        wires (Sequence[int]): Target subsystems (order matters! the
            left-most Hilbert space is at index 0).

    Raises:
        ValueError: if wrong wires of the system were targeted or
            the size of the unitary is incorrect

    Returns:
        array: :math:`2^N\times 2^N` matrix. The full system operator.
    """
    if num_wires == 1:
        # total number of wires is 1, simply return the matrix
        return U

    N = num_wires
    wires = np.asarray(wires)

    if np.any(wires < 0) or np.any(wires >= N) or len(set(wires)) != len(wires):
        raise ValueError("Invalid target subsystems provided in 'wires' argument.")

    if U.shape != (2 ** len(wires), 2 ** len(wires)):
        raise ValueError("Matrix parameter must be of size (2**len(wires), 2**len(wires))")

    # generate N qubit basis states via the cartesian product
    tuples = np.array(list(itertools.product([0, 1], repeat=N)))

    # wires not acted on by the operator
    inactive_wires = list(set(range(N)) - set(wires))

    # expand U to act on the entire system
    U = np.kron(U, np.identity(2 ** len(inactive_wires)))

    # move active wires to beginning of the list of wires
    rearranged_wires = np.array(list(wires) + inactive_wires)

    # convert to computational basis
    # i.e., converting the list of basis state bit strings into
    # a list of decimal numbers that correspond to the computational
    # basis state. For example, [0, 1, 0, 1, 1] = 2^3+2^1+2^0 = 11.
    perm = np.ravel_multi_index(tuples[:, rearranged_wires].T, [2] * N)

    # permute U to take into account rearranged wires
    return U[:, perm][perm]


@functools.lru_cache()
def pauli_eigs(n):
    r"""Eigenvalues for :math:`A^{\otimes n}`, where :math:`A` is
    Pauli operator, or shares its eigenvalues.

    As an example if n==2, then the eigenvalues of a tensor product consisting
    of two matrices sharing the eigenvalues with Pauli matrices is returned.

    Args:
        n (int): the number of qubits the matrix acts on
    Returns:
        list: the eigenvalues of the specified observable
    """
    if n == 1:
        return np.array([1, -1])
    return np.concatenate([pauli_eigs(n - 1), -pauli_eigs(n - 1)])


class OperationRecorder(qml.QueuingContext):
    """A template and quantum function inspector,
    allowing easy introspection of operators that have been
    applied without requiring a QNode.

    **Example**:

    The OperationRecorder is a context manager. Executing templates
    or quantum functions stores resulting applied operators in the
    recorder, which can then be printed.

    >>> weights = qml.init.strong_ent_layers_normal(n_layers=1, n_wires=2)
    >>>
    >>> with qml.utils.OperationRecorder() as rec:
    >>>    qml.templates.layers.StronglyEntanglingLayers(*weights, wires=[0, 1])
    >>>
    >>> print(rec)
    Operations
    ==========
    Rot(-0.10832656163640327, 0.14429091013664083, -0.010835826725765343, wires=[0])
    Rot(-0.11254523669444501, 0.0947222564914006, -0.09139600968423377, wires=[1])
    CNOT(wires=[0, 1])
    CNOT(wires=[1, 0])

    Alternatively, the :attr:`~.OperationRecorder.queue` attribute can be used
    to directly accessed the applied :class:`~.Operation` and :class:`~.Observable`
    objects.

    Attributes:
        queue (List[~.Operators]): list of operators applied within
            the OperatorRecorder context, includes operations and observables
        operations (List[~.Operations]): list of operations applied within
            the OperatorRecorder context
        observables (List[~.Observables]): list of observables applied within
            the OperatorRecorder context
    """

    def __init__(self):
        self.queue = []
        self.operations = None
        self.observables = None

    def _append_operator(self, operator):
        self.queue.append(operator)

    def _remove_operator(self, operator):
        self.queue.remove(operator)

    def __exit__(self, exception_type, exception_value, traceback):
        super().__exit__(exception_type, exception_value, traceback)

        # Remove duplicates that might have arisen from measurements
        self.queue = list(OrderedDict.fromkeys(self.queue))
        self.operations = list(
            filter(
                lambda op: not (
                    isinstance(op, qml.operation.Observable) and not op.return_type is None
                ),
                self.queue,
            )
        )
        self.observables = list(
            filter(
                lambda op: isinstance(op, qml.operation.Observable) and not op.return_type is None,
                self.queue,
            )
        )

    def __str__(self):
        output = ""
        output += "Operations\n"
        output += "==========\n"
        for op in self.operations:
            output += repr(op) + "\n"

        output += "\n"
        output += "Observables\n"
        output += "==========\n"
        for op in self.observables:
            output += repr(op) + "\n"

        return output


def inv(operation_list):
    """Invert a list of operations or a :doc:`template </introduction/templates>`.

    If the inversion happens inside a QNode, the operations are removed and requeued
    in the reversed order for proper inversion.

    **Example:**

    The following example illuminates the inversion of a template:

    .. code-block:: python3

        @qml.template
        def ansatz(weights, wires):
            for idx, wire in enumerate(wires):
                qml.RX(weights[idx], wires=[wire])

            for idx in range(len(wires) - 1):
                qml.CNOT(wires=[wires[idx], wires[idx + 1]])

        dev = qml.device('default.qubit', wires=2)

        @qml.qnode(dev)
        def circuit(weights):
            qml.inv(ansatz(weights, wires=[0, 1]))
            return qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))

    We may also invert an operation sequence:

    .. code-block:: python3

        dev = qml.device('default.qubit', wires=2)

        @qml.qnode(dev)
        def circuit1():
            qml.T(wires=[0]).inv()
            qml.Hadamard(wires=[0]).inv()
            qml.S(wires=[0]).inv()
            return qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))

        @qml.qnode(dev)
        def circuit2():
            qml.inv([qml.S(wires=[0]), qml.Hadamard(wires=[0]), qml.T(wires=[0])])
            return qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))

    Double checking that both circuits produce the same output:

    >>> ZZ1 = circuit1()
    >>> ZZ2 = circuit2()
    >>> assert ZZ1 == ZZ2
    True

    Args:
        operation_list (Iterable[~.Operation]): An iterable of operations

    Returns:
        List[~.Operation]: The inverted list of operations
    """
    if isinstance(operation_list, qml.operation.Operation):
        operation_list = [operation_list]
    elif operation_list is None:
        raise ValueError(
            "None was passed as an argument to inv. "
            "This could happen if inversion of a template without the template decorator is attempted."
        )
    elif callable(operation_list):
        raise ValueError(
            "A function was passed as an argument to inv. "
            "This could happen if inversion of a template function is attempted. "
            "Please use inv on the function including its arguments, as in inv(template(args))."
        )
    elif not isinstance(operation_list, Iterable):
        raise ValueError("The provided operation_list is not iterable.")

    non_ops = [
        (idx, op)
        for idx, op in enumerate(operation_list)
        if not isinstance(op, qml.operation.Operation)
    ]

    if non_ops:
        string_reps = [" operation_list[{}] = {}".format(idx, op) for idx, op in non_ops]
        raise ValueError(
            "The given operation_list does not only contain Operations."
            + "The following elements of the iterable were not Operations:"
            + ",".join(string_reps)
        )

    inv_ops = [op.inv() for op in reversed(copy.deepcopy(operation_list))]

    for op in operation_list:
        qml.QueuingContext.remove_operator(op)

    for inv_op in inv_ops:
        qml.QueuingContext.append_operator(inv_op)

    return inv_ops
