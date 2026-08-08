"""Stable incremental model for the desktop Games page."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    Property,
    Signal,
    Slot,
    Qt,
)


_PRESENTATION_FIELDS = (
    "id",
    "name",
    "launcher",
    "dataSource",
    "path",
    "installPath",
    "libraryPath",
    "libraryAvailable",
    "availabilityStatus",
    "filesystem",
    "steamBuildId",
    "effectiveArtworkUrl",
    "status",
    "lastTaskStatus",
    "compressionClassificationKey",
    "compressionAvailable",
    "physicalSize",
    "physicalSizeBytes",
    "savedSpace",
    "savedBytes",
    "compsizeUncompressedBytes",
    "currentCompressionSavingBytes",
    "compressionEffectPercent",
    "size",
    "sizeBytes",
    "logicalSizeGb",
    "savedSpaceGb",
    "sizeScanStatus",
    "sizeScanError",
    "updateInProgress",
    "isSteamTool",
)


def presentation_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return only values capable of changing a visible Games delegate."""

    return tuple(row.get(field) for field in _PRESENTATION_FIELDS)


class GamesListModel(QAbstractListModel):
    """Apply snapshots by stable game ID without resetting QML delegates."""

    ModelDataRole = int(Qt.ItemDataRole.UserRole) + 1
    GameIdRole = ModelDataRole + 1

    countChanged = Signal()
    mutation = Signal(str, str, int)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._all_order: list[str] = []
        self._all_rows: dict[str, dict[str, Any]] = {}
        self._visible_ids: list[str] = []
        self._query = ""
        self._launcher = ""
        self._filesystem = ""
        self._sort_mode = 0
        self._reset_count = 0

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 - Qt API
        return {
            self.ModelDataRole: QByteArray(b"modelData"),
            self.GameIdRole: QByteArray(b"gameId"),
        }

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._visible_ids)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._visible_ids):
            return None
        game_id = self._visible_ids[index.row()]
        if role in (self.ModelDataRole, int(Qt.ItemDataRole.DisplayRole)):
            return dict(self._all_rows[game_id])
        if role == self.GameIdRole:
            return game_id
        return None

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._visible_ids)

    @Property(int, constant=True)
    def modelResetCount(self) -> int:
        return self._reset_count

    @Slot(str, str, str, int)
    def setFilters(
        self,
        query: str,
        launcher: str,
        filesystem: str,
        sort_mode: int,
    ) -> None:
        normalized = (
            str(query).strip().casefold(),
            str(launcher).strip(),
            str(filesystem).strip(),
            max(0, min(3, int(sort_mode))),
        )
        current = (
            self._query,
            self._launcher,
            self._filesystem,
            self._sort_mode,
        )
        if normalized == current:
            return
        self._query, self._launcher, self._filesystem, self._sort_mode = normalized
        self._apply_visible(self._filtered_ids(), reason="filter")

    def apply_snapshot(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        reason: str,
    ) -> dict[str, int | bool]:
        next_order: list[str] = []
        next_rows: dict[str, dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            game_id = str(row.get("id") or "").strip()
            if not game_id or game_id in next_rows:
                continue
            next_order.append(game_id)
            next_rows[game_id] = row

        old_rows = self._all_rows
        old_signatures = {
            game_id: presentation_signature(row)
            for game_id, row in self._all_rows.items()
        }
        next_signatures = {
            game_id: presentation_signature(row)
            for game_id, row in next_rows.items()
        }
        presentation_unchanged = (
            self._all_order == next_order and old_signatures == next_signatures
        )
        if presentation_unchanged:
            self._all_order = next_order
            self._all_rows = next_rows
            return {
                "changed": False,
                "inserted": 0,
                "removed": 0,
                "updated": 0,
                "moved": 0,
                "resets": 0,
            }
        changed_ids = {
            game_id
            for game_id in set(old_signatures) & set(next_signatures)
            if old_signatures[game_id] != next_signatures[game_id]
        }
        self._all_order = next_order
        # Rows being removed must remain readable until endRemoveRows().
        # Retained rows already expose their new values when dataChanged fires,
        # and newly inserted rows are also available to the view immediately.
        self._all_rows = {**old_rows, **next_rows}
        target_ids = self._filtered_ids()
        mutation = self._apply_visible(
            target_ids,
            reason=reason,
            changed_ids=changed_ids,
        )
        self._all_rows = next_rows
        return mutation

    def _filtered_ids(self) -> list[str]:
        visible: list[str] = []
        for game_id in self._all_order:
            row = self._all_rows[game_id]
            name = str(row.get("name") or row.get("title") or "")
            path = str(row.get("path") or row.get("installPath") or "")
            if self._query and self._query not in name.casefold() and self._query not in path.casefold():
                continue
            if self._launcher and str(row.get("launcher") or "") != self._launcher:
                continue
            if self._filesystem and str(row.get("filesystem") or "") != self._filesystem:
                continue
            visible.append(game_id)

        if self._sort_mode == 1:
            visible.sort(
                key=lambda game_id: str(
                    self._all_rows[game_id].get("name") or ""
                ).casefold(),
                reverse=True,
            )
        elif self._sort_mode == 2:
            visible.sort(
                key=lambda game_id: float(
                    self._all_rows[game_id].get("sizeBytes") or 0
                ),
                reverse=True,
            )
        elif self._sort_mode == 3:
            visible.sort(
                key=lambda game_id: float(
                    self._all_rows[game_id].get("savedBytes") or 0
                ),
                reverse=True,
            )
        else:
            visible.sort(
                key=lambda game_id: str(
                    self._all_rows[game_id].get("name") or ""
                ).casefold()
            )
        return visible

    def _apply_visible(
        self,
        target_ids: list[str],
        *,
        reason: str,
        changed_ids: set[str] | None = None,
    ) -> dict[str, int | bool]:
        previous_count = len(self._visible_ids)
        inserted = removed = updated = moved = 0
        target_set = set(target_ids)

        for row in range(len(self._visible_ids) - 1, -1, -1):
            if self._visible_ids[row] in target_set:
                continue
            game_id = self._visible_ids[row]
            self.beginRemoveRows(QModelIndex(), row, row)
            self._visible_ids.pop(row)
            self.endRemoveRows()
            removed += 1
            self.mutation.emit("remove", game_id, row)

        for target_row, game_id in enumerate(target_ids):
            if target_row >= len(self._visible_ids):
                self.beginInsertRows(QModelIndex(), target_row, target_row)
                self._visible_ids.insert(target_row, game_id)
                self.endInsertRows()
                inserted += 1
                self.mutation.emit("insert", game_id, target_row)
                continue
            if self._visible_ids[target_row] != game_id:
                try:
                    source_row = self._visible_ids.index(game_id, target_row + 1)
                except ValueError:
                    self.beginInsertRows(QModelIndex(), target_row, target_row)
                    self._visible_ids.insert(target_row, game_id)
                    self.endInsertRows()
                    inserted += 1
                    self.mutation.emit("insert", game_id, target_row)
                else:
                    self.beginMoveRows(
                        QModelIndex(),
                        source_row,
                        source_row,
                        QModelIndex(),
                        target_row,
                    )
                    self._visible_ids.insert(
                        target_row, self._visible_ids.pop(source_row)
                    )
                    self.endMoveRows()
                    moved += 1
                    self.mutation.emit("move", game_id, target_row)

        for row, game_id in enumerate(self._visible_ids):
            if game_id not in (changed_ids or set()):
                continue
            model_index = self.index(row, 0)
            self.dataChanged.emit(
                model_index,
                model_index,
                [self.ModelDataRole, self.GameIdRole],
            )
            updated += 1

        if previous_count != len(self._visible_ids):
            self.countChanged.emit()
        changed = bool(inserted or removed or updated or moved)
        if changed:
            self.mutation.emit("commit", str(reason), len(self._visible_ids))
        return {
            "changed": changed,
            "inserted": inserted,
            "removed": removed,
            "updated": updated,
            "moved": moved,
            "resets": 0,
        }


__all__ = ["GamesListModel", "presentation_signature"]
