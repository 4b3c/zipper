"""A test run never reaches live data.

On the box every shell carries the live environment -- `ZIPPER_VAULT=/zipper/vault`,
the host socket, the `.env` path -- so a test module that forgets to point those
somewhere temporary writes into the operator's real vault. Worse, modules are imported
once per process: under `unittest discover` the first module to import `core` or
`settings` fixes their paths for every module after it, so one unsandboxed file
(one that imports `bot`, say) aims the whole run at the live vault. That deleted
the vault, its `.git` and its `Inbox/` state.

So the paths themselves refuse: while a test runner is the main program, a vault,
settings file or env file outside the temp directory raises instead of resolving.
A loud failure points at the test to fix; a quiet redirect would hide it.
"""
import os, sys, tempfile


def under_test():
    """True when the process is a test runner (`python -m unittest`, pytest)."""
    if 'pytest' in sys.modules:
        return True
    spec = getattr(sys.modules.get('__main__'), '__spec__', None)
    return (getattr(spec, 'name', '') or '').startswith('unittest')


def _inside_tmp(path):
    tmp = os.path.realpath(tempfile.gettempdir())
    p = os.path.realpath(path)
    return p == tmp or p.startswith(tmp + os.sep)


def check(path, what):
    """`path`, unless this is a test run and `path` is not under the temp directory."""
    if path and under_test() and not _inside_tmp(path):
        raise RuntimeError(
            'refusing to use %s %s during a test run: tests must point ZIPPER_VAULT '
            '(and ZIPPER_SETTINGS / ZIPPER_ENV_FILE) at a temp directory before '
            'importing zipper. See zipper/testguard.py.' % (what, path))
    return path
