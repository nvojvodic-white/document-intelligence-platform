"""Shared core: hashing, extraction, chunking, embedding cache, repositories.

Imported by both entrypoints. The dependency direction is one-way and load
bearing: services/* may import core, core may never import services/*.

Everything here that touches tenant data takes user_id first.
"""
