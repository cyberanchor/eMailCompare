"""Shared setup for the tests: import paths and a temporary data folder. No test touches the network."""
import os
import shutil
import sys
import tempfile

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR = os.path.join(TOOLS, "collector")
for p in (TOOLS, COLLECTOR):
    if p not in sys.path:
        sys.path.insert(0, p)


class TempDir:
    """with TempDir() as d: ... — removed afterwards."""

    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="emc-test-")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def record(domain="example.org", order=1, **extra):
    rec = {"order": order, "domain": domain, "status": "pending"}
    rec.update(extra)
    return rec
