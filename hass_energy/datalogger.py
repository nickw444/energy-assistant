from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .ha_client import HomeAssistantWebSocketClient

StatusCallback = Callable[[str], None]
SnapshotCallback = Callable[[Path], None]


@dataclass
class DataLogger:
    client: HomeAssistantWebSocketClient
    entities: list[str]
    triggers: list[str]
    output_dir: Path
    debounce_seconds: float = 2.0
    on_snapshot: SnapshotCallback | None = None
    on_error: StatusCallback | None = None
    _queue: asyncio.Queue[dict[str, Any] | None] = field(
        init=False, default_factory=asyncio.Queue
    )
    _pending_triggers: list[dict[str, Any]] = field(init=False, default_factory=list)
    _debounce_task: asyncio.Task[None] | None = field(init=False, default=None)
    _running: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.entities = list(dict.fromkeys(self.entities))
        self.triggers = list(dict.fromkeys(self.triggers))
        self.output_dir = self.output_dir.expanduser()

    async def run(self) -> bool:
        """Run the datalogger until stopped or cancelled. Returns True if started."""
        if self._running:
            return True
        self._running = True

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            self._notify_error(f"Failed to prepare output directory: {err}")
            self._running = False
            return False

        try:
            await self.client.connect()
            await self.client.subscribe_states(self.triggers, self._handle_change)
            await self._write_snapshot(trigger_changes=None)
        except (
            ValueError,
            PermissionError,
            FileNotFoundError,
            TimeoutError,
            OSError,
        ) as err:
            self._notify_error(f"Failed to start datalogger: {err}")
            await self.client.disconnect()
            self._running = False
            return False

        try:
            while self._running:
                change = await self._queue.get()
                if change is None:
                    break

                self._pending_triggers.append(change)
                await self._restart_debounce()
        except asyncio.CancelledError:
            raise
        finally:
            await self.stop()
        return True

    async def stop(self) -> None:
        """Stop the logger and clean up resources."""
        if not self._running:
            return
        self._running = False
        await self._cancel_debounce()
        await self.client.disconnect()
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def _handle_change(self, change: dict[str, Any]) -> None:
        await self._queue.put(change)

    async def _restart_debounce(self) -> None:
        await self._cancel_debounce()
        self._debounce_task = asyncio.create_task(self._schedule_snapshot())

    async def _cancel_debounce(self) -> None:
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
        self._debounce_task = None

    async def _schedule_snapshot(self) -> None:
        try:
            if self.debounce_seconds:
                await asyncio.sleep(self.debounce_seconds)
            await self._write_snapshot(trigger_changes=self._pending_triggers.copy())
            self._pending_triggers.clear()
        except asyncio.CancelledError:
            raise

    async def _write_snapshot(self, trigger_changes: list[dict[str, Any]] | None) -> None:
        targets = list(dict.fromkeys([*self.entities, *self.triggers]))

        try:
            states = await self.client.get_states(targets)
        except (
            ValueError,
            PermissionError,
            FileNotFoundError,
            TimeoutError,
            OSError,
            RuntimeError,
        ) as err:
            self._notify_error(f"Failed to fetch states for logging: {err}")
            return

        now = datetime.now().astimezone()
        timestamp = now.isoformat(timespec="milliseconds")
        filename_timestamp = timestamp.replace(":", "-")
        filename = f"datalog-{filename_timestamp}.json"
        output_path = self.output_dir / filename

        trigger_details: dict[str, Any] | None = None
        if trigger_changes:
            trigger_details = {
                "raw_events": trigger_changes,
            }

        snapshot = {
            "captured_at": timestamp,
            "entities": self.entities,
            "triggers": self.triggers,
            "trigger": trigger_details,
            "states": states,
        }

        try:
            output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        except OSError as err:
            self._notify_error(f"Failed to write snapshot {output_path}: {err}")
            return

        if self.on_snapshot:
            self.on_snapshot(output_path)

    def _notify_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)
