import asyncio

from adapter_jibot import Adapter
from tests.test_adapter_jibot_v3_order import FakeVehicle


def make_adapter():
    adapter = Adapter()
    adapter.set_vehicle(FakeVehicle())
    return adapter


async def start_state(adapter):
    """Spin the publish loop until adapter.state exists; return the task to cancel."""
    adapter._loop = asyncio.get_running_loop()
    task = asyncio.create_task(adapter.publish_state("state", interval_sec=0.02))
    for _ in range(200):
        if adapter.state is not None:
            break
        await asyncio.sleep(0.01)
    assert adapter.state is not None
    return task
