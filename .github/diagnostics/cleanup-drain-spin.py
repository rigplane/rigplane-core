import asyncio
import runpy
import sys

authority = runpy.run_path("tests/test_managed_tx_authority.py")["authority"]


async def main():
    managed, _, _, _, fence, _ = authority()
    await managed._stop_scheduler(managed._scheduler_task)
    managed._pending_abort_cleanup.append(fence.force_off())
    managed._start_abort_cleanup()
    (cleanup,) = managed._abort_cleanup

    async def drain():
        assert cleanup.done() and cleanup in managed._abort_cleanup
        print("CHECKED cleanup_done=True cleanup_owned=True discard_pending=True", flush=True)
        await managed._finish_abort_cleanup()
        assert not managed._abort_cleanup
        print("DRAINED", flush=True)

    await asyncio.create_task(drain())
    await managed.close()


print(sys.version, flush=True)
asyncio.run(main())
