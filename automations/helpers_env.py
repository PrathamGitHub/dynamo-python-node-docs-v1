"""Vendored-dependency bootstrap for the Dynamo CPython3 environment.
Call ensure_vendored_imports() ONCE before importing duckdb/pyarrow anywhere.
Idempotent; safe to call from every module that needs them."""
import os
import sys
import importlib


def _vendor_dir():
    # Site-packages sits at the SAME level as the automations package dir.
    pkg_dir = os.path.dirname(os.path.abspath(__file__))   # .../automations
    repo_dir = os.path.dirname(pkg_dir)                    # repo root
    return os.path.join(repo_dir, "Site-packages")


def _evict(*roots):
    """Drop already-imported modules (and submodules) so a fresh import resolves
    against the vendored copy instead of a cached stub."""
    for name in list(sys.modules):
        for root in roots:
            if name == root or name.startswith(f"{root}."):
                del sys.modules[name]
                break


def ensure_vendored_imports(verbose=False):
    """Make the vendored duckdb/pyarrow the ones that import. Idempotent."""
    vendor = _vendor_dir()
    if not os.path.isdir(vendor):
        raise ImportError(f"Vendored Site-packages not found at: {vendor}. "
                          f"It must sit next to the 'automations' dir.")
    # 1) vendored dir FIRST on the path
    if vendor in sys.path:
        sys.path.remove(vendor)
    sys.path.insert(0, vendor)
    # 2) evict the stale/fake duckdb (and pyarrow) so the cache can't shadow us
    _evict("duckdb", "pyarrow")
    # 3) refresh the import machinery after mutating sys.path
    importlib.invalidate_caches()
    # 4) import and VERIFY we got the real thing, not the fake stub
    try:
        import duckdb
        if not hasattr(duckdb, "connect"):
            raise ImportError(
                f"Imported duckdb has no .connect -- still the stub. "
                f"Resolved from: {getattr(duckdb, '__file__', '?')}. "
                f"Check the vendored copy is complete in {vendor}.")
    except ImportError:
        raise ImportError("Failed to import duckdb")
    try:
        import pyarrow as pa
        if not hasattr(pa, "Table"):
            raise ImportError(
                f"Imported pyarrow has no .Table -- still the stub. "
                f"Resolved from: {getattr(pa, '__file__', '?')}. "
                f"Check the vendored copy is complete in {vendor}.")
    except ImportError:
        raise ImportError("Failed to import pyarrow")
    if verbose:
        print(f"[helpers_env] duckdb {duckdb.__version__} from {duckdb.__file__}")
        print(f"[helpers_env] pyarrow {pa.__version__} from {pa.__file__}")