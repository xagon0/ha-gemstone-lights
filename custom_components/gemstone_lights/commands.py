"""Serialize complete read/modify/write operations per controller."""

from functools import wraps


def serialized(method):
    """Keep nested commands in one task inside the same device transaction."""
    @wraps(method)
    async def wrapped(self, device_id, *args, **kwargs):
        async with self._device_lock(device_id):
            return await method(self, device_id, *args, **kwargs)
    return wrapped
