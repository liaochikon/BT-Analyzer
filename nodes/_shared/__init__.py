"""Shared helpers/mixins for node implementations.

This package is intentionally NOT a node: it has no ``node.py``, so ``scan_and_register_nodes``
(which only imports ``nodes/<dir>/node.py``) skips it. Node modules import from here directly,
e.g. ``from nodes._shared.level_meter import LevelHistoryMixin``.

These are type-specific helpers that used to live in ``core.base_node`` but only apply to a
subset of nodes; keeping them out of the base class keeps every node from inheriting machinery
it never uses.
"""
