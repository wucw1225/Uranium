# Copyright (c) 2018 Ultimaker B.V.
# Uranium is released under the terms of the LGPLv3 or higher.

from PyQt5.QtCore import QAbstractListModel, QVariant, QModelIndex, pyqtSlot, pyqtProperty, pyqtSignal
from typing import Any, Callable, Dict, List


class ListModel(QAbstractListModel):
    """models of a list of items的便捷基类.

    此类表示可以暴露给QML的字典对象的列表。 它主要用作只读便利类，但支持删除元素，因此也可以用于有限的编写。
    """

    def __init__(self, parent = None) -> None:
        super().__init__(parent)
        self._items = []  # type: List[Dict[str, Any]]
        self._role_names = {}  # type: Dict[int, bytes]

    itemsChanged = pyqtSignal()

    @pyqtProperty(int, notify = itemsChanged)
    def count(self) -> int:
        return len(self._items) #返回元素个数

    @pyqtSlot(result = int)
    def rowCount(self, parent = None) -> int:
        """该函数是必需的，因为它在QAbstractListModel中是抽象的。

        在后台，Qt将在需要知道模型中有多少项时调用此函数。
        该pyqtSlot不会链接到itemsChanged信号，因此请改用常规count（）函数。
        """

        return self.count

    def addRoleName(self, role: int, name: str):
        # _role_names需要一个QByteArray。 PyQt 5不会将str隐式转换为字节数组，因此请手动强制转换。
        self._role_names[role] = name.encode("utf-8")

    def roleNames(self):
        return self._role_names

    def data(self, index, role):
        """从QAbstractListModel重新实现"""

        if not index.isValid():
            return QVariant()
        return self._items[index.row()][self._role_names[role].decode("utf-8")]

    @pyqtSlot(int, result="QVariantMap")
    def getItem(self, index: int) -> Dict[str, Any]:
        """Get an item from the list"""

        try:
            return self._items[index]
        except:
            return {}

    @pyqtProperty("QVariantList", notify = itemsChanged)
    def items(self) -> List[Dict[str, Any]]:
        """The list of items in this model."""

        return self._items

    def setItems(self, items: List[Dict[str, Any]]) -> None:
        """一次更换所有 items
        :param items: 新的items列表.
        """

        # 由于以下原因，我们不使用模型重置：
        #   - 它十分慢
        #   - 调用endResetModel（）时，由于某种原因，它可能会导致Mac OS X崩溃（CURA-6015）
        # 因此，在这种情况下，我们使用insertRows（），removeRows（）和dataChanged信号进行更智能的模型更新。

        old_row_count = len(self._items)
        new_row_count = len(items)
        changed_row_count = min(old_row_count, new_row_count)

        need_to_add = old_row_count < new_row_count
        need_to_remove = old_row_count > new_row_count

        # 在插入和删除的情况下，在修改items之前，我们需要调用beginInsertRows（）/ beginRemoveRows（）和endInsertRows（）/ endRemoveRows（）。
        # 在对现有items进行修改的情况下，我们只需要修改items，然后发出dataChanged（）。
        #
        # 在这里，简化了替换完整items列表的过程，而不是一一添加/删除/修改它们，并且需要确保在items替换之前和之后发出必要的信号（插入/删除/修改）。

        if need_to_add:
            self.beginInsertRows(QModelIndex(), old_row_count, new_row_count - 1)
        elif need_to_remove:
            self.beginRemoveRows(QModelIndex(), new_row_count, old_row_count - 1)

        self._items = items

        if need_to_add:
            self.endInsertRows()
        elif need_to_remove:
            self.endRemoveRows()

        # 通知现有items已更改。
        if changed_row_count >= 0:
            self.dataChanged.emit(self.index(0, 0), self.index(changed_row_count - 1, 0))

        # 使用自定义信号itemsChanged进行通知，以使其向后兼容，以防某些情况下依赖它。
        self.itemsChanged.emit()

    @pyqtSlot(dict)
    def appendItem(self, item: Dict[str, Any]):
        """Add an item to the list.

        :param item: The item to add.
        """

        self.insertItem(len(self._items), item)

    @pyqtSlot(int, dict)
    def insertItem(self, index: int, item: Dict[str, Any]) -> None:
        """Insert an item into the list at an index.

        :param index: The index where to insert.
        :param item: The item to add.
        """

        self.beginInsertRows(QModelIndex(), index, index)
        self._items.insert(index, item)
        self.endInsertRows()
        self.itemsChanged.emit()

    @pyqtSlot(int)
    def removeItem(self, index: int) -> None:
        """Remove an item from the list.

        :param index: The index of the item to remove.
        """

        self.beginRemoveRows(QModelIndex(), index, index)
        del self._items[index]
        self.endRemoveRows()
        self.itemsChanged.emit()

    @pyqtSlot()
    def clear(self) -> None:
        """Clear the list."""

        self.beginResetModel()
        self._items.clear()
        self.endResetModel()
        self.itemsChanged.emit()

    @pyqtSlot(int, str, QVariant)
    def setProperty(self, index: int, property: str, value: Any) -> None:
        self._items[index][property] = value
        self.dataChanged.emit(self.index(index, 0), self.index(index, 0))

    def sort(self, fun: Callable[[Any], float]) -> None:
        """Sort the list.

        :param fun: 用于确定排序键的可调用对象。
        """

        self.beginResetModel()
        self._items.sort(key = fun)
        self.endResetModel()

    @pyqtSlot(str, QVariant, result = int)
    def find(self, key: str, value: Any) -> int:
        """Find a entry by key value pair

        :param key:
        :param value:
        :return: index of setting if found, None otherwise
        """

        for i in range(len(self._items)):
            if key in self._items[i]:
                if self._items[i][key] == value:
                    return i
        return -1
