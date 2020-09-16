# Copyright (c) 2019 Ultimaker B.V.
# Uranium is released under the terms of the LGPLv3 or higher.

from UM.Qt.ListModel import ListModel

from PyQt5.QtCore import pyqtProperty, Qt, pyqtSignal

from UM.Settings.ContainerRegistry import ContainerRegistry
from UM.Settings.DefinitionContainer import DefinitionContainer
from typing import Dict


class DefinitionContainersModel(ListModel):
    """保存定义容器的模型。
    通过设置过滤器属性，可以更改此模型保留的定义。
    """

    NameRole = Qt.UserRole + 1          # Human readable name (string)
    IdRole = Qt.UserRole + 2            # Unique ID of Definition
    SectionRole = Qt.UserRole + 3       # Section of definition / machine. (string)

    def __init__(self, parent = None):
        super().__init__(parent)
        self.addRoleName(self.NameRole, "name")
        self.addRoleName(self.IdRole, "id")
        self.addRoleName(self.SectionRole, "section")

        # 监听changes
        ContainerRegistry.getInstance().containerAdded.connect(self._onContainerChanged)
        ContainerRegistry.getInstance().containerRemoved.connect(self._onContainerChanged)

        self._section_property = ""

        #首选项应显示在顶部。 每个部分的权重。最小值的部分显示在顶部。 不在此列表中的节的值为0。
        self._preferred_sections = {} #type: Dict[str, int]

        self._filter_dict = {}
        self._update()

    def _onContainerChanged(self, container):
        """注册表(registry)中container change事件的处理程序"""

        # 当更改的容器是DefinitionContainer时，我们仅需要更新。
        if isinstance(container, DefinitionContainer): # isinstance()函数判断一个对象是否是已知的类型
            self._update()

    def _update(self) -> None:
        """私人便利函数可重置 & 重新填充模型。"""

        items = []
        definition_containers = ContainerRegistry.getInstance().findDefinitionContainersMetadata(**self._filter_dict)
        definition_containers.sort(key = self._sortKey)

        for metadata in definition_containers:
            metadata = dict(metadata) # 对于完全加载的定义，元数据是一个OrderedDict，无法正确传递给QML

            items.append({
                "name": metadata["name"],
                "id": metadata["id"],
                "metadata": metadata,
                "section": metadata.get(self._section_property, ""),
            })
        self.setItems(items)
    # sectionProperty属性
    def setSectionProperty(self, property_name):
        if self._section_property != property_name:
            self._section_property = property_name
            self.sectionPropertyChanged.emit()
            self._update()

    sectionPropertyChanged = pyqtSignal()

    @pyqtProperty(str, fset = setSectionProperty, notify = sectionPropertyChanged)
    def sectionProperty(self):
        return self._section_property

    # preferredSections属性
    def setPreferredSections(self, weights: Dict[str, int]):
        if self._preferred_sections != weights:
            self._preferred_sections = weights
            self.preferredSectionsChanged.emit()
            self._update()

    preferredSectionsChanged = pyqtSignal()

    @pyqtProperty("QVariantMap", fset = setPreferredSections, notify = preferredSectionsChanged)
    def preferredSections(self):
        return self._preferred_sections

    # filter属性
    def setFilter(self, filter_dict):
        """根据字符串设置此模型的过滤器。
        :param filter_dict: 用字典做过滤依据。
        """

        self._filter_dict = filter_dict
        self._update()

    filterChanged = pyqtSignal()
    @pyqtProperty("QVariantMap", fset = setFilter, notify = filterChanged)
    def filter(self):
        return self._filter_dict

    def _sortKey(self, item):
        result = []

        if self._section_property:
            section_value = item.get(self._section_property, "")
            section_weight = self._preferred_sections.get(section_value, 0)
            result.append(section_weight)
            result.append(section_value.lower())

        result.append(int(item.get("weight", 0))) #Weight within a section.
        result.append(item["name"].lower())

        return result

    def _updateMetaData(self, container):
        index = self.find("id", container.id)

        if self._section_property:
            self.setProperty(index, "section", container.getMetaDataEntry(self._section_property, ""))

        self.setProperty(index, "metadata", container.getMetaData())