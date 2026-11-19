"""Namespace for new, isolated production components.

This package is deliberately separate from the protected production code in
``core/`` and ``scripts/``. Nothing in ``src/`` is called by an existing
workflow; adoption is opt-in and requires an explicit owner decision.
"""
