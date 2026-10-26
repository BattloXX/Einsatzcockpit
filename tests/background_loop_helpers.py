"""Helfer zur Prüfung der tatsächlichen Hintergrund-Loop-Registrierung."""

import asyncio


def registered_background_loop_names(monkeypatch) -> list[str]:
    import app.main as main

    names = []

    async def record_loop(loop):
        names.append(loop.__name__)

    async def collect():
        tasks = main._start_background_loops()
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    with monkeypatch.context() as patch:
        patch.setattr(main, "supervised_loop", record_loop)
        asyncio.run(collect())
    return names
