"""Shared core: hashing, extraction, chunking, embedding cache, repositories.

Imported by both entrypoints - services/api (request path) and
services/worker (sync path). The dependency direction is one-way and load
bearing: services/* may import core, core may never import from services/*.
That is what lets a long sync run in its own process without the request path
having to know the worker exists.

Everything here that touches tenant data takes user_id as its first argument.
There are no unscoped queries; see core/repositories.py.
"""
