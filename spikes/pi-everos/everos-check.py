"""Disposable check of EverOS's on-disk project path partitioning."""
from tempfile import TemporaryDirectory
from everos.infra.persistence.memory_root import MemoryRoot

with TemporaryDirectory() as directory:
    root = MemoryRoot(directory)
    first = root.knowledge_dir('hmw', 'project-a')
    second = root.knowledge_dir('hmw', 'project-b')
    assert first != second
    print('EverOS knowledge directories differ for project-a and project-b')
