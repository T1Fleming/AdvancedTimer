"""Tiny async pub/sub used to fan timer-state snapshots out to SSE clients."""
import asyncio


class StateBroadcaster:
    def __init__(self):
        self._queues = set()

    def subscribe(self):
        queue = asyncio.Queue()
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue):
        self._queues.discard(queue)

    def publish(self, snapshot):
        for queue in self._queues:
            queue.put_nowait(snapshot)
