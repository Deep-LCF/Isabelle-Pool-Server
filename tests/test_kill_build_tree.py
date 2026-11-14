"""kill_build_tree (build_verify, 2026-11-14): a timed-out `isabelle build` must not hang its handler.

Reproduces the wenda-Amd-Wrx80 incident in miniature: the build command is a shell wrapper
that spawns a child holding our stdout pipe (the JVM / Poly/ML role). Killing the wrapper
alone leaves the child alive and `proc.communicate()` waiting for EOF for ever; killing the
process group ends both and the reap returns at once.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

import pytest

from server.app.services.build_verify import kill_build_tree

# A wrapper (sh) that forks a long-lived child sharing stdout, then waits on it --
# exactly the shape of `isabelle` -> java -> poly as far as the pipes are concerned.
_WRAPPER = "sleep 600 & wait"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_group_kill_reaps_wrapper_and_child_quickly():
    async def run():
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", _WRAPPER,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        assert pgid == proc.pid
        await asyncio.sleep(0.3)                       # let the child start
        t0 = time.monotonic()
        await kill_build_tree(proc, reap_timeout=10.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"reap took {elapsed:.1f}s: the child kept the pipe open"
        assert proc.returncode is not None
        # nothing of the group survives
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
    asyncio.run(run())


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_wrapper_only_kill_would_hang_the_old_way():
    """The defect itself, kept as documentation: killing just the wrapper leaves the
    child holding the pipe, and communicate() does not return within the wait."""
    async def run():
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", _WRAPPER,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        pgid = os.getpgid(proc.pid)
        await asyncio.sleep(0.3)
        proc.kill()                                    # the old code
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(proc.communicate(), timeout=1.0)
        os.killpg(pgid, signal.SIGKILL)                # clean up the orphan
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
    asyncio.run(run())
