"""User-owned content, independent of the vendor's cached catalogs."""

from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy

from homeassistant.exceptions import HomeAssistantError

from .const import ANIMATIONS, EFFECT_SOLID
from .validation import validate_design, validate_pattern


def checked_content(kind: str, content: dict) -> dict:
    """Validate editable content before storing it or sending it to hardware."""
    value = deepcopy(content)
    try:
        if kind == "pattern":
            validate_pattern(value)
            if not value.get("colors"):
                raise ValueError("A pattern needs at least one color")
            animation = value.setdefault("animation", "motionless")
            if animation == EFFECT_SOLID:
                value["animation"] = "motionless"
            elif animation not in ANIMATIONS:
                raise ValueError("Unknown animation")
            for key in ("speed", "direction"):
                limit = 255 if key == "speed" else 1
                if key in value and (
                    type(value[key]) is not int or not 0 <= value[key] <= limit
                ):
                    raise ValueError(f"Invalid {key}")
        elif kind == "design":
            validate_design(value)
            if not value.get("zonePatterns") and not value.get("staticColors"):
                raise ValueError("A design needs zones or pixel colors")
            for zone in value.get("zonePatterns") or []:
                zone["pattern"] = checked_content("pattern", zone["pattern"])
        elif kind == "zone":
            start, end = value.get("start"), value.get("end")
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start <= end < 4096
            ):
                raise ValueError(
                    "Zone start/end must be inclusive pixel indices from 0 to 4095"
                )
            value = {"lights": [end - start + 1, start, end]}
        else:
            raise ValueError("Unknown content kind")
        if len(json.dumps(value).encode()) > 1024 * 1024:
            raise ValueError("Content is too large")
    except (ValueError, TypeError) as err:
        raise HomeAssistantError(str(err)) from err
    return value


class LocalCatalog:
    """Persist local edits separately so cloud refreshes cannot overwrite them."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.data = {
            "patterns": [],
            "designs": {},
            "zones": {},
            "library": {},
            "library_folders": {},
        }
        self.lock = asyncio.Lock()

    def restore(self, data):
        """Restore our own previously validated storage."""
        if isinstance(data, dict):
            for key, default in self.data.items():
                if isinstance(data.get(key), type(default)):
                    self.data[key] = deepcopy(data[key])

    def entries(self, kind, device_id):
        """Get entries in the appropriate account or controller scope."""
        if kind == "pattern":
            return self.data["patterns"]
        return self.data[f"{kind}s"].get(device_id, [])

    def merge(self, kind, device_id, vendor):
        """Local names override imported names while retaining untouched vendor entries."""
        local = self.entries(kind, device_id)
        names = {item["name"].casefold() for item in local}
        return [
            *local,
            *(
                item
                for item in vendor
                if (item.get("name") or "").casefold() not in names
            ),
        ]

    def _save(self, device_id, kind, name, content, folder=""):
        if not isinstance(name, str):
            raise HomeAssistantError("Content name must be text")
        name = name.strip()
        if not name or name == "None" or len(name) > 128:
            raise HomeAssistantError(
                "Choose a content name from 1 to 128 characters other than None"
            )
        value = checked_content(kind, content)
        entries = self.entries(kind, device_id)
        previous = next(
            (item for item in entries if item["name"].casefold() == name.casefold()),
            None,
        )
        previous_id = (previous.get("data", previous) if previous else {}).get("id")
        if kind == "zone":
            start, end = value["lights"][-2:]
            for zone in self.coordinator.zones(device_id):
                if zone["name"].casefold() == name.casefold():
                    previous_id = zone["id"]
                    continue
                lights = zone.get("lights", [])
                if len(lights) >= 3 and max(start, lights[-2]) <= min(end, lights[-1]):
                    raise HomeAssistantError(f"Zone overlaps {zone['name']}")
        value.update(
            id=previous_id
            or (f"ha:{uuid.uuid4()}" if kind == "zone" else str(uuid.uuid4())),
            name=name,
        )
        item = (
            {"name": name, "folder": folder, "data": value}
            if kind == "pattern"
            else value
        )
        updated = [
            entry for entry in entries if entry["name"].casefold() != name.casefold()
        ]
        updated.append(item)
        if kind == "pattern":
            self.data["patterns"] = updated
        else:
            self.data[f"{kind}s"][device_id] = updated

    async def save(self, device_id, kind, name, content, folder=""):
        """Create or replace a named local item, then publish the catalog change."""
        async with self.lock:
            self._save(device_id, kind, name, content, folder)
            await self.coordinator._async_save_cache()
            self.coordinator.async_update_listeners()

    async def delete(self, device_id, kind, name):
        """Delete only a user-owned item, leaving vendor content untouched."""
        async with self.lock:
            entries = self.entries(kind, device_id)
            updated = [
                item for item in entries if item["name"].casefold() != name.casefold()
            ]
            if len(updated) == len(entries):
                raise HomeAssistantError("No local item with that name exists")
            if kind == "pattern":
                self.data["patterns"] = updated
            else:
                self.data[f"{kind}s"][device_id] = updated
            await self.coordinator._async_save_cache()
            self.coordinator.async_update_listeners()

    def export(self, device_id):
        """Export usable content without device addresses or account credentials."""
        return deepcopy(
            {
                "version": 1,
                "patterns": self.coordinator.patterns(),
                "designs": self.coordinator.designs(device_id),
                "zones": self.coordinator.zones(device_id),
                "library": self.coordinator._library,
                "library_folders": self.coordinator._library_folders,
            }
        )

    async def import_data(self, device_id, catalog):
        """Validate an entire portable catalog before committing any of it."""
        if not isinstance(catalog, dict) or catalog.get("version") != 1:
            raise HomeAssistantError("Expected an exported version 1 catalog")
        async with self.lock:
            previous = deepcopy(self.data)
            library = deepcopy(catalog.get("library", {}))
            folders = deepcopy(catalog.get("library_folders", {}))
            try:
                for key in ("zones", "patterns", "designs"):
                    items = catalog.get(key, [])
                    if not isinstance(items, list) or any(
                        not isinstance(item, dict) for item in items
                    ):
                        raise ValueError(f"Invalid {key}")
                for kind in ("zone", "pattern", "design"):
                    for item in catalog.get(f"{kind}s", []):
                        content = item["data"] if kind == "pattern" else item
                        if kind == "zone":
                            content = {
                                "start": item["lights"][-2],
                                "end": item["lights"][-1],
                            }
                        self._save(
                            device_id,
                            kind,
                            item["name"],
                            content,
                            item.get("folder", ""),
                        )
                        # Keep zone references and content IDs portable within the exported design.
                        saved = self.entries(kind, device_id)[-1]
                        if kind == "pattern":
                            saved = saved["data"]
                        if isinstance(item.get("id"), str):
                            saved["id"] = item["id"]
                if not isinstance(library, dict) or not isinstance(folders, dict):
                    raise ValueError("Invalid library")
                for folder_id, items in library.items():
                    if not isinstance(items, list) or any(
                        not isinstance(item, dict) for item in items
                    ):
                        raise ValueError("Invalid library patterns")
                    if not isinstance(folders.get(folder_id), dict):
                        raise ValueError("Missing library folder")
                    for item in items:
                        if not isinstance(item.get("name"), str):
                            raise ValueError("Missing library pattern name")
                        checked_content("pattern", item["data"])
            except (
                KeyError,
                IndexError,
                TypeError,
                ValueError,
                HomeAssistantError,
            ) as err:
                self.data = previous
                raise HomeAssistantError(f"Invalid catalog: {err}") from err
            self.data["library"].update(library)
            self.data["library_folders"].update(folders)
            self.coordinator._library.update(library)
            self.coordinator._library_folders.update(folders)
            await self.coordinator._async_save_cache()
            self.coordinator.async_update_listeners()
