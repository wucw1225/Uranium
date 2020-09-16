# Copyright (c) 2019 Ultimaker B.V.
# Uranium is released under the terms of the LGPLv3 or higher.

import imp
import json
import os
import shutil  # For deleting plugin directories;
import stat  # For setting file permissions correctly;
import time
import types
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtCore import QObject, pyqtSlot, QUrl, pyqtProperty, pyqtSignal

from UM.Logger import Logger
from UM.Message import Message
from UM.Platform import Platform
from UM.PluginError import PluginNotFoundError, InvalidMetaDataError
from UM.PluginObject import PluginObject  # For type hinting
from UM.Resources import Resources
from UM.Trust import Trust, TrustException, TrustBasics
from UM.Version import Version
from UM.i18n import i18nCatalog

i18n_catalog = i18nCatalog("uranium")

if TYPE_CHECKING:
    from UM.Application import Application


plugin_path_ignore_list = ["__pycache__", "tests", ".git"]


class PluginRegistry(QObject):
    """动态加载模块作为插件的中心对象。

    PluginRegistry类可以动态加载模块并将其用作插件。 每个插件模块应该是一个目录，
    其中带有__init__`文件，该文件定义了一个getMetaData和一个register函数。

    有关更多详细信息，请参见[plugins]文件。

    [plugins]: docs/plugins.md
    """

    def __init__(self, application: "Application", parent: QObject = None) -> None:
        if PluginRegistry.__instance is not None:
            raise RuntimeError("Try to create singleton '%s' more than once" % self.__class__.__name__)
        PluginRegistry.__instance = self

        super().__init__(parent)
        self._application = application  # type: Application
        self._api_version = application.getAPIVersion()  # type: Version

        self._all_plugins = []        # type: List[str]
        self._metadata = {}           # type: Dict[str, Dict[str, Any]]

        self._plugins_installed = []  # type: List[str]

        # 注：disabled_plugins和plugins_to_remove明确设置为“无”。
        # 实际加载首选项时，它会设置为一个列表。 这样，我们可以看到无列表和空列表之间的区别。
        self._disabled_plugins = []  # type: List[str]
        self._outdated_plugins = []  # type: List[str]
        self._plugins_to_install = dict()  # type: Dict[str, Dict[str, str]]
        self._plugins_to_remove = []  # type: List[str]

        self._plugins = {}            # type: Dict[str, types.ModuleType]
        self._found_plugins = {}      # type: Dict[str, types.ModuleType]  # 缓存以加快_findPlugin
        self._plugin_objects = {}     # type: Dict[str, PluginObject]

        self._plugin_locations = []  # type: List[str]
        self._plugin_folder_cache = {}  # type: Dict[str, List[Tuple[str, str]]]  # 缓存以加快_locatePlugin

        self._bundled_plugin_cache = {}  # type: Dict[str, bool]

        self._supported_file_types = {"umplugin": "Uranium Plugin"} # type: Dict[str, str]

        self._check_if_trusted = False  # type: bool
        self._checked_plugin_ids = []     # type: List[str]
        self._distrusted_plugin_ids = []  # type: List[str]
        self._trust_checker = None  # type: Optional[Trust]

    def setCheckIfTrusted(self, check_if_trusted: bool) -> None:
        self._check_if_trusted = check_if_trusted
        if self._check_if_trusted:
            self._trust_checker = Trust.getInstance()
            # 如果发生任何错误（例如：“无法读取公共密钥”），“ Trust.getInstance（）”将引发异常。
            # 任何此类异常都在此处 explicitly _not_ caught，因为应用程序应退出并崩溃。

    def getCheckIfTrusted(self) -> bool:
        return self._check_if_trusted

    def initializeBeforePluginsAreLoaded(self) -> None:
        config_path = Resources.getConfigStoragePath()

        # 用于存储插件信息的文件，例如要安装/删除的插件信息以及已禁用的插件信息。
        # 此时，我们可以在此处加载它，因为我们已经知道了实际的应用程序名称，目录名称也是
        self._plugin_config_filename = os.path.join(os.path.abspath(config_path), "plugins.json") # type: str

        from UM.Settings.ContainerRegistry import ContainerRegistry
        container_registry = ContainerRegistry.getInstance()

        try:
            with container_registry.lockFile():
                # 加载插件信息（如果存在）
                if os.path.exists(self._plugin_config_filename):
                    Logger.log("i", "Loading plugin configuration file '%s'", self._plugin_config_filename)
                    with open(self._plugin_config_filename, "r", encoding = "utf-8") as f:
                        data = json.load(f)
                        self._disabled_plugins = data["disabled"]
                        self._plugins_to_install = data["to_install"]
                        self._plugins_to_remove = data["to_remove"]
        except:
            Logger.logException("e", "Failed to load plugin configuration file '%s'", self._plugin_config_filename)

        # 还可以从偏好设置中加载数据，这些信息曾经保存在其中
        preferences = self._application.getPreferences()
        disabled_plugins = preferences.getValue("general/disabled_plugins")
        disabled_plugins = disabled_plugins.split(",") if disabled_plugins else []
        disabled_plugins = [plugin for plugin in disabled_plugins if len(plugin.strip()) > 0]
        for plugin_id in disabled_plugins:
            if plugin_id not in self._disabled_plugins:
                self._disabled_plugins.append(plugin_id)

        plugins_to_remove = preferences.getValue("general/plugins_to_remove")
        plugins_to_remove = plugins_to_remove.split(",") if plugins_to_remove else []
        for plugin_id in plugins_to_remove:
            if plugin_id not in self._plugins_to_remove:
                self._plugins_to_remove.append(plugin_id)

        # 删除需要删除的插件
        for plugin_id in self._plugins_to_remove:
            self._removePlugin(plugin_id)
        self._plugins_to_remove = []
        if plugins_to_remove is not None:
            preferences.setValue("general/plugins_to_remove", "")
        self._savePluginData()

        # 安装需要安装的插件（覆盖现有）
        for plugin_id, plugin_info in self._plugins_to_install.items():
            self._installPlugin(plugin_id, plugin_info["filename"])
        self._plugins_to_install = {}
        self._savePluginData()

    def initializeAfterPluginsAreLoaded(self) -> None:
        preferences = self._application.getPreferences()

        # 从首选项中删除旧的首选项设置
        preferences.resetPreference("general/disabled_plugins")
        preferences.resetPreference("general/plugins_to_remove")

    def _savePluginData(self) -> None:
        from UM.Settings.ContainerRegistry import ContainerRegistry
        container_registry = ContainerRegistry.getInstance()
        try:
            with container_registry.lockFile():
                with open(self._plugin_config_filename, "w", encoding = "utf-8") as f:
                    data = json.dumps({"disabled": self._disabled_plugins,
                                       "to_install": self._plugins_to_install,
                                       "to_remove": self._plugins_to_remove,
                                       })
                    f.write(data)
        except:
            # 由于我们正在写文件（并等待锁定），因此有些事情可能会出错。
            # 无需为此而使应用程序崩溃，但这是我们要记录的失败。
            Logger.logException("e", "Unable to save the plugin data.")

    # TODO:
    # - [ ] Improve how metadata is stored. It should not be in the 'plugin' prop
    #       of the dictionary item.
    # - [ ] Remove usage of "active" in favor of "enabled".
    # - [ ] Switch self._disabled_plugins to self._plugins_disabled
    # - [ ] External plugins only appear in installed after restart
    #
    # NOMENCLATURE:
    # Enabled (active):  A plugin which is installed and currently enabled.
    # Disabled: A plugin which is installed but not currently enabled.
    # Available: A plugin which is not installed but could be.
    # Installed: A plugin which is installed locally in Cura.

    #===============================================================================
    # PUBLIC METHODS
    #===============================================================================

    #   将插件位置添加到要搜索的位置列表中：
    def addPluginLocation(self, location: str) -> None:
        #TODO: Add error checking!
        self._plugin_locations.append(location)

    #   检查是否已加载所有必需的插件：
    def checkRequiredPlugins(self, required_plugins: List[str]) -> bool:
        plugins = self._findInstalledPlugins()
        for plugin_id in required_plugins:
            if plugin_id not in plugins:
                Logger.log("e", "Plugin %s is required, but not added or loaded", plugin_id)
                return False
        return True

    #   从启用的插件列表中删除插件并保存到首选项：
    def disablePlugin(self, plugin_id: str) -> None:
        if plugin_id not in self._disabled_plugins:
            self._disabled_plugins.append(plugin_id)
        self._savePluginData()

    #   将插件添加到已启用插件的列表中并保存到首选项：
    def enablePlugin(self, plugin_id: str) -> None:
        if plugin_id in self._disabled_plugins:
            self._disabled_plugins.remove(plugin_id)
        self._savePluginData()

    #   获取已启用插件的列表：
    def getActivePlugins(self) -> List[str]:
        plugin_list = []
        for plugin_id in self._all_plugins:
            if self.isActivePlugin(plugin_id):
                plugin_list.append(plugin_id)
        return plugin_list

    #   获取与元数据的某个子集匹配的所有元数据的列表：
    #   \param kwargs 关键字参数.
    #       可能的关键字:
    #       - filter: \type{dict} 应该匹配的元数据的子集。
    #       - active_only: 布尔值，当仅返回活动插件元数据时为True.
    def getAllMetaData(self, **kwargs: Any):
        data_filter = kwargs.get("filter", {})
        active_only = kwargs.get("active_only", False)
        metadata_list = []
        for plugin_id in self._all_plugins:
            if active_only and (plugin_id in self._disabled_plugins or plugin_id in self._outdated_plugins):
                continue
            plugin_metadata = self.getMetaData(plugin_id)
            if self._subsetInDict(plugin_metadata, data_filter):
                metadata_list.append(plugin_metadata)
        return metadata_list

    #   获取禁用的插件列表：
    def getDisabledPlugins(self) -> List[str]:
        return self._disabled_plugins

    #   获取已安装插件的列表：
    #   NOTE: 这些是已经注册的插件。 该列表实际上由私有_findInstalledPlugins（）方法填充。
    def getInstalledPlugins(self) -> List[str]:
        plugins = self._plugins_installed.copy()
        for plugin_id in self._plugins_to_remove:
            if plugin_id in plugins:
                plugins.remove(plugin_id)
        for plugin_id in self._plugins_to_install:
            if plugin_id not in plugins:
                plugins.append(plugin_id)
        return sorted(plugins)

    #   获取某个插件的元数据：
    #   NOTE: 当找不到元数据或元数据缺少正确的键时，引发InvalidMetaDataError。
    def getMetaData(self, plugin_id: str) -> Dict[str, Any]:
        if plugin_id not in self._metadata:
            try:
                if not self._populateMetaData(plugin_id):
                    return {}
            except InvalidMetaDataError:
                return {}

        return self._metadata[plugin_id]

    @pyqtSlot(str, result = "QVariantMap")
    def installPlugin(self, plugin_path: str) -> Optional[Dict[str, str]]:
        plugin_path = QUrl(plugin_path).toLocalFile()

        plugin_id = self._getPluginIdFromFile(plugin_path)
        if plugin_id is None: #Failed to load.
            return None

        # Remove it from the to-be-removed list if it's there
        if plugin_id in self._plugins_to_remove:
            self._plugins_to_remove.remove(plugin_id)
            self._savePluginData()

        # Copy the plugin file to the cache directory so it can later be used for installation
        cache_dir = os.path.join(Resources.getCacheStoragePath(), "plugins")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok = True)
        cache_plugin_filename = os.path.join(cache_dir, plugin_id + ".plugin")
        if os.path.exists(cache_plugin_filename):
            os.remove(cache_plugin_filename)
        shutil.copy2(plugin_path, cache_plugin_filename)

        # Add new install data
        install_info = {"plugin_id": plugin_id,
                        "filename": cache_plugin_filename}
        self._plugins_to_install[plugin_id] = install_info
        self._savePluginData()
        Logger.log("i", "Plugin '%s' has been scheduled for installation.", plugin_id)

        result = {"status": "ok",
                  "id": "",
                  "message": i18n_catalog.i18nc("@info:status", "The plugin has been installed.\nPlease re-start the application to activate the plugin."),
                  }
        return result

    #   Check by ID if a plugin is active (enabled):
    def isActivePlugin(self, plugin_id: str) -> bool:
        if plugin_id not in self._disabled_plugins and plugin_id not in self._outdated_plugins and plugin_id in self._all_plugins:
            return True

        return False

    def isBundledPlugin(self, plugin_id: str) -> bool:
        if plugin_id in self._bundled_plugin_cache:
            return self._bundled_plugin_cache[plugin_id]
        install_prefix = os.path.abspath(self._application.getInstallPrefix())

        # Go through all plugin locations and check if the given plugin is located in the installation path.
        is_bundled = False
        for plugin_dir in self._plugin_locations:
            try:
                is_in_installation_path = os.path.commonpath([install_prefix, plugin_dir]).startswith(install_prefix)
            except ValueError:
                is_in_installation_path = False
            if not is_in_installation_path:
                # To prevent the situation in a 'trusted' env. that the user-folder has a supposedly 'bundled' plugin:
                if self._check_if_trusted:
                    result = self._locatePlugin(plugin_id, plugin_dir)
                    if result:
                        is_bundled = False
                        break
                else:
                    continue

            result = self._locatePlugin(plugin_id, plugin_dir)
            if result:
                is_bundled = True
                break
        self._bundled_plugin_cache[plugin_id] = is_bundled
        return is_bundled
    def loadPlugins(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Load all plugins matching a certain set of metadata

        :param metadata: The meta data that needs to be matched.
        NOTE: This is the method which kicks everything off at app launch.
        """

        start_time = time.time()
        # Get a list of all installed plugins:
        plugin_ids = self._findInstalledPlugins()
        for plugin_id in plugin_ids:
            # Get the plugin metadata:
            try:
                plugin_metadata = self.getMetaData(plugin_id)
            except TrustException:
                Logger.error("Plugin {} was not loaded because it could not be verified.", plugin_id)
                message_text = i18n_catalog.i18nc("@error:untrusted",
                                                  "Plugin {} was not loaded because it could not be verified.", plugin_id)
                Message(text = message_text).show()
                continue

            # Save all metadata to the metadata dictionary:
            self._metadata[plugin_id] = plugin_metadata
            if metadata is None or self._subsetInDict(self._metadata[plugin_id], metadata):
                #
                try:
                    self.loadPlugin(plugin_id)
                    QCoreApplication.processEvents()  # Ensure that the GUI does not freeze.
                    # Add the plugin to the list after actually load the plugin:
                    self._all_plugins.append(plugin_id)
                    self._plugins_installed.append(plugin_id)
                except PluginNotFoundError:
                    pass
        Logger.log("d", "Loading all plugins took %s seconds", time.time() - start_time)

    # Checks if the given plugin API version is compatible with the current version.
    def isPluginApiVersionCompatible(self, plugin_api_version: "Version") -> bool:
        return plugin_api_version.getMajor() == self._api_version.getMajor() \
               and plugin_api_version.getMinor() <= self._api_version.getMinor()

    #   Load a single plugin by ID:
    def loadPlugin(self, plugin_id: str) -> None:
        # If plugin has already been loaded, do not load it again:
        if plugin_id in self._plugins:
            Logger.log("w", "Plugin %s was already loaded", plugin_id)
            return

        # Find the actual plugin on drive, do security checks if necessary:
        plugin = self._findPlugin(plugin_id)

        # If not found, raise error:
        if not plugin:
            raise PluginNotFoundError(plugin_id)

        # If found, but isn't in the metadata dictionary, add it:
        if plugin_id not in self._metadata:
            try:
                self._populateMetaData(plugin_id)
            except InvalidMetaDataError:
                return

        # Do not load plugin that has been disabled
        if plugin_id in self._disabled_plugins:
            Logger.log("i", "Plugin [%s] has been disabled. Skip loading it.", plugin_id)
            return

        # If API version is incompatible, don't load it.
        supported_sdk_versions = self._metadata[plugin_id].get("plugin", {}).get("supported_sdk_versions", [Version("0")])
        is_plugin_supported = False
        for supported_sdk_version in supported_sdk_versions:
            is_plugin_supported |= self.isPluginApiVersionCompatible(supported_sdk_version)
            if is_plugin_supported:
                break

        if not is_plugin_supported:
            Logger.log("w", "Plugin [%s] with supported sdk versions [%s] is incompatible with the current sdk version [%s].",
                       plugin_id, [str(version) for version in supported_sdk_versions], self._api_version)
            self._outdated_plugins.append(plugin_id)
            return

        try:
            to_register = plugin.register(self._application)  # type: ignore  # We catch AttributeError on this in case register() doesn't exist.
            if not to_register:
                Logger.log("w", "Plugin %s did not return any objects to register", plugin_id)
                return
            for plugin_type, plugin_object in to_register.items():
                if type(plugin_object) == list:
                    for metadata_index, nested_plugin_object in enumerate(plugin_object):
                        nested_plugin_object.setVersion(self._metadata[plugin_id].get("plugin", {}).get("version"))
                        all_metadata = self._metadata[plugin_id].get(plugin_type, [])
                        try:
                            nested_plugin_object.setMetaData(all_metadata[metadata_index])
                        except IndexError:
                            nested_plugin_object.setMetaData({})
                        self._addPluginObject(nested_plugin_object, plugin_id, plugin_type)
                else:
                    plugin_object.setVersion(self._metadata[plugin_id].get("plugin", {}).get("version"))
                    metadata = self._metadata[plugin_id].get(plugin_type, {})
                    if type(metadata) == list:
                        try:
                            metadata = metadata[0]
                        except IndexError:
                            metadata = {}
                    plugin_object.setMetaData(metadata)
                    self._addPluginObject(plugin_object, plugin_id, plugin_type)

            self._plugins[plugin_id] = plugin
            self.enablePlugin(plugin_id)
            Logger.info("Loaded plugin %s", plugin_id)

        except Exception as ex:
            Logger.logException("e", "Error loading plugin %s:", plugin_id)

    #   Uninstall a plugin with a given ID:
    @pyqtSlot(str, result = "QVariantMap")
    def uninstallPlugin(self, plugin_id: str) -> Dict[str, str]:
        result = {"status": "error", "message": "", "id": plugin_id}
        success_message = i18n_catalog.i18nc("@info:status", "The plugin has been removed.\nPlease restart {0} to finish uninstall.", self._application.getApplicationName())

        if plugin_id not in self._plugins_installed:
            return result

        in_to_install = plugin_id in self._plugins_to_install
        if in_to_install:
            del self._plugins_to_install[plugin_id]
            self._savePluginData()
            Logger.log("i", "Plugin '%s' removed from to-be-installed list.", plugin_id)
        else:
            if plugin_id not in self._plugins_to_remove:
                self._plugins_to_remove.append(plugin_id)
            self._savePluginData()
            Logger.log("i", "Plugin '%s' has been scheduled for later removal.", plugin_id)

            # Remove the plugin object from the Plugin Registry:
            self._plugins.pop(plugin_id, None)
            self._plugins_installed.remove(plugin_id)

        result["status"] = "ok"
        result["message"] = success_message
        return result

    # Installs the given plugin file. It will overwrite the existing plugin if present.
    def _installPlugin(self, plugin_id: str, plugin_path: str) -> None:
        Logger.log("i", "Attempting to install a new plugin %s from file '%s'", plugin_id, plugin_path)

        local_plugin_path = os.path.join(Resources.getStoragePath(Resources.Resources), "plugins")

        if plugin_id in self._bundled_plugin_cache:
            del self._bundled_plugin_cache[plugin_id]

        try:
            with zipfile.ZipFile(plugin_path, "r") as zip_ref:
                plugin_folder = os.path.join(local_plugin_path, plugin_id)

                # Overwrite the existing plugin if already installed
                if os.path.isdir(plugin_folder):
                    shutil.rmtree(plugin_folder, ignore_errors = True)
                os.makedirs(plugin_folder, exist_ok = True)

                # Extract all files
                for info in zip_ref.infolist():
                    extracted_path = zip_ref.extract(info.filename, path = plugin_folder)
                    permissions = os.stat(extracted_path).st_mode
                    os.chmod(extracted_path, permissions | stat.S_IEXEC) # Make these files executable.
        except: # Installing a new plugin should never crash the application.
            Logger.logException("e", "An exception occurred while installing plugin {path}".format(path = plugin_path))

        if plugin_id in self._disabled_plugins:
            self._disabled_plugins.remove(plugin_id)

    # Removes the given plugin.
    def _removePlugin(self, plugin_id: str) -> None:
        plugin_folder = os.path.join(Resources.getStoragePath(Resources.Resources), "plugins")
        plugin_path = os.path.join(plugin_folder, plugin_id)

        if plugin_id in self._bundled_plugin_cache:
            del self.bundled_plugin_cache[plugin_id]

        Logger.log("i", "Attempting to remove plugin '%s' from directory '%s'", plugin_id, plugin_path)
        shutil.rmtree(plugin_path)

#===============================================================================
# PRIVATE METHODS
#===============================================================================

    def _getPluginIdFromFile(self, filename: str) -> Optional[str]:
        plugin_id = None
        try:
            with zipfile.ZipFile(filename, "r") as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename.endswith("/"):
                        plugin_id = file_info.filename.strip("/")
                        break
        except zipfile.BadZipFile:
            Logger.logException("e", "Failed to load plug-in file. The zip archive seems to be corrupt.")
            return None  # Signals that loading this failed.
        except FileNotFoundError:
            Logger.logException("e", "Failed to load plug-in file as we were unable to find it.")
            return None  # Signals that loading this failed.
        return plugin_id

    #   Returns a list of all possible plugin ids in the plugin locations:
    def _findInstalledPlugins(self, paths = None) -> List[str]:
        plugin_ids = []

        if not paths:
            paths = self._plugin_locations

        for folder in paths:
            try:
                if not os.path.isdir(folder):
                    continue

                for file in os.listdir(folder):
                    filepath = os.path.join(folder, file)
                    if os.path.isdir(filepath):
                        if os.path.isfile(os.path.join(filepath, "__init__.py")):
                            plugin_ids.append(file)
                        else:
                            plugin_ids += self._findInstalledPlugins([filepath])
            except EnvironmentError as err:
                Logger.warning("Unable to read folder {folder}: {err}".format(folder = folder, err = err))
                continue

        return plugin_ids

    def _findPlugin(self, plugin_id: str) -> Optional[types.ModuleType]:
        """Try to find a module implementing a plugin

        :param plugin_id: The name of the plugin to find
        :returns: module if it was found (and, if 'self._check_if_trusted' is set, also secure), None otherwise
        """

        if plugin_id in self._found_plugins:
            return self._found_plugins[plugin_id]
        location = None
        for folder in self._plugin_locations:
            location = self._locatePlugin(plugin_id, folder)
            if location:
                break

        if not location:
            return None

        try:
            file, path, desc = imp.find_module(plugin_id, [location])
        except Exception:
            Logger.logException("e", "Import error when importing %s", plugin_id)
            return None

        # Define a trusted plugin as either: already checked, correctly signed, or bundled with the application.
        if self._check_if_trusted and plugin_id not in self._checked_plugin_ids and not self.isBundledPlugin(plugin_id):

            # NOTE: '__pychache__'s (+ subfolders) are deleted on startup _before_ load module:
            if not TrustBasics.removeCached(path):
                self._distrusted_plugin_ids.append(plugin_id)
                return None

            # Do the actual check:
            if self._trust_checker is not None and self._trust_checker.signedFolderCheck(path):
                self._checked_plugin_ids.append(plugin_id)
            else:
                self._distrusted_plugin_ids.append(plugin_id)
                return None

        try:
            module = imp.load_module(plugin_id, file, path, desc) #type: ignore #MyPy gets the wrong output type from imp.find_module for some reason.
        except Exception:
            Logger.logException("e", "Import error loading module %s", plugin_id)
            return None
        finally:
            if file:
                os.close(file) #type: ignore #MyPy gets the wrong output type from imp.find_module for some reason.
        self._found_plugins[plugin_id] = module
        return module

    def _locatePlugin(self, plugin_id: str, folder: str) -> Optional[str]:
        if not os.path.isdir(folder):
            return None

        # self._plugin_folder_cache is a per-plugin-location list of all subfolders that contain a __init__.py file
        if folder not in self._plugin_folder_cache:
            plugin_folders = []
            for root, dirs, files in os.walk(folder, topdown = True, followlinks = True):
                # modify dirs in place to ignore .git, pycache and test folders completely
                dirs[:] = [d for d in dirs if d not in plugin_path_ignore_list]

                if "plugin.json" in files:
                    plugin_folders.append((root, os.path.basename(root)))

            self._plugin_folder_cache[folder] = plugin_folders

        for folder_path, folder_name in self._plugin_folder_cache[folder]:
            if folder_name == plugin_id:
                return os.path.abspath(os.path.join(folder_path, ".."))

        return None

    #   Load the plugin data from the stream and in-place update the metadata.
    def _parsePluginInfo(self, plugin_id, file_data, meta_data):
        try:
            meta_data["plugin"] = json.loads(file_data)
        except json.decoder.JSONDecodeError:
            Logger.logException("e", "Failed to parse plugin.json for plugin %s", plugin_id)
            raise InvalidMetaDataError(plugin_id)

        # Check if metadata is valid;
        if "version" not in meta_data["plugin"]:
            Logger.log("e", "Version must be set!")
            raise InvalidMetaDataError(plugin_id)

        # Check if the plugin states what API version it needs.
        if "api" not in meta_data["plugin"] and "supported_sdk_versions" not in meta_data["plugin"]:
            Logger.log("e", "The API or the supported_sdk_versions must be set!")
            raise InvalidMetaDataError(plugin_id)
        else:
            # Store the api_version as a Version object.
            all_supported_sdk_versions = []  # type: List[Version]
            if "supported_sdk_versions" in meta_data["plugin"]:
                all_supported_sdk_versions += [Version(supported_version) for supported_version in
                                               meta_data["plugin"]["supported_sdk_versions"]]
            if "api" in meta_data["plugin"]:
                all_supported_sdk_versions += [Version(meta_data["plugin"]["api"])]
            meta_data["plugin"]["supported_sdk_versions"] = all_supported_sdk_versions

        if "i18n-catalog" in meta_data["plugin"]:
            # A catalog was set, try to translate a few strings
            i18n_catalog = i18nCatalog(meta_data["plugin"]["i18n-catalog"])
            if "name" in meta_data["plugin"]:
                meta_data["plugin"]["name"] = i18n_catalog.i18n(meta_data["plugin"]["name"])
            if "description" in meta_data["plugin"]:
                meta_data["plugin"]["description"] = i18n_catalog.i18n(meta_data["plugin"]["description"])

    def _populateMetaData(self, plugin_id: str) -> bool:
        """Populate the list of metadata"""

        plugin = self._findPlugin(plugin_id)
        if not plugin:
            Logger.log("w", "Could not find plugin %s", plugin_id)
            return False

        location = None
        for folder in self._plugin_locations:
            location = self._locatePlugin(plugin_id, folder)
            if location:
                break

        if not location:
            Logger.log("w", "Could not find plugin %s", plugin_id)
            return False
        location = os.path.join(location, plugin_id)

        try:
            meta_data = plugin.getMetaData() #type: ignore #We catch the AttributeError that this would raise if the module has no getMetaData function.
            metadata_file = os.path.join(location, "plugin.json")
            try:
                with open(metadata_file, "r", encoding = "utf-8") as file_stream:
                    self._parsePluginInfo(plugin_id, file_stream.read(), meta_data)
            except FileNotFoundError:
                Logger.logException("e", "Unable to find the required plugin.json file for plugin %s", plugin_id)
                raise InvalidMetaDataError(plugin_id)
            except UnicodeDecodeError:
                Logger.logException("e", "The plug-in metadata file for plug-in {plugin_id} is corrupt.".format(plugin_id = plugin_id))
                raise InvalidMetaDataError(plugin_id)
            except EnvironmentError as e:
                Logger.logException("e", "Can't open the metadata file for plug-in {plugin_id}: {err}".format(plugin_id = plugin_id, err = str(e)))
                raise InvalidMetaDataError(plugin_id)

        except AttributeError as e:
            Logger.log("e", "Plug-in {plugin_id} has no getMetaData function to get metadata of the plug-in: {err}".format(plugin_id = plugin_id, err = str(e)))
            raise InvalidMetaDataError(plugin_id)
        except TypeError as e:
            Logger.log("e", "Plug-in {plugin_id} has a getMetaData function with the wrong signature: {err}".format(plugin_id = plugin_id, err = str(e)))
            raise InvalidMetaDataError(plugin_id)

        if not meta_data:
            raise InvalidMetaDataError(plugin_id)

        meta_data["id"] = plugin_id
        meta_data["location"] = location

        # Application-specific overrides
        appname = self._application.getApplicationName()
        if appname in meta_data:
            meta_data.update(meta_data[appname])
            del meta_data[appname]

        self._metadata[plugin_id] = meta_data
        return True

    #   Check if a certain dictionary contains a certain subset of key/value pairs
    #   \param dictionary \type{dict} The dictionary to search
    #   \param subset \type{dict} The subset to search for
    def _subsetInDict(self, dictionary: Dict[Any, Any], subset: Dict[Any, Any]) -> bool:
        for key in subset:
            if key not in dictionary:
                return False
            if subset[key] != {} and dictionary[key] != subset[key]:
                return False
        return True

    def getPluginObject(self, plugin_id: str) -> PluginObject:
        """Get a specific plugin object given an ID. If not loaded, load it.

        :param plugin_id: The ID of the plugin object to get.
        """

        if plugin_id not in self._plugins:
            self.loadPlugin(plugin_id)
        if plugin_id not in self._plugin_objects:
            raise PluginNotFoundError(plugin_id)
        return self._plugin_objects[plugin_id]

    def _addPluginObject(self, plugin_object: PluginObject, plugin_id: str, plugin_type: str) -> None:
        plugin_object.setPluginId(plugin_id)
        self._plugin_objects[plugin_id] = plugin_object
        try:
            self._type_register_map[plugin_type](plugin_object)
        except Exception as e:
            Logger.logException("e", "Unable to add plugin %s", plugin_id)

    def addSupportedPluginExtension(self, extension: str, description: str) -> None:
        if extension not in self._supported_file_types:
            self._supported_file_types[extension] = description
            self.supportedPluginExtensionsChanged.emit()

    supportedPluginExtensionsChanged = pyqtSignal()

    @pyqtProperty("QStringList", notify=supportedPluginExtensionsChanged)
    def supportedPluginExtensions(self) -> List[str]:
        file_types = []
        all_types = []

        if Platform.isLinux():
            for ext, desc in self._supported_file_types.items():
                file_types.append("{0} (*.{1} *.{2})".format(desc, ext.lower(), ext.upper()))
                all_types.append("*.{0} *.{1}".format(ext.lower(), ext.upper()))
        else:
            for ext, desc in self._supported_file_types.items():
                file_types.append("{0} (*.{1})".format(desc, ext))
                all_types.append("*.{0}".format(ext))

        file_types.sort()
        file_types.insert(0, i18n_catalog.i18nc("@item:inlistbox", "All Supported Types ({0})", " ".join(all_types)))
        file_types.append(i18n_catalog.i18nc("@item:inlistbox", "All Files (*)"))
        return file_types

    def getPluginPath(self, plugin_id: str) -> Optional[str]:
        """Get the path to a plugin.

        :param plugin_id: The PluginObject.getPluginId() of the plugin.
        :return: The absolute path to the plugin or an empty string if the plugin could not be found.
        """

        if plugin_id in self._plugins:
            plugin = self._plugins.get(plugin_id)
        else:
            plugin = self._findPlugin(plugin_id)

        if not plugin:
            return None

        path = os.path.dirname(self._plugins[plugin_id].__file__)
        if os.path.isdir(path):
            return path

        return None

    @classmethod
    def addType(cls, plugin_type: str, register_function: Callable[[Any], None]) -> None:
        """Add a new plugin type.

        This function is used to add new plugin types. Plugin types are simple
        string identifiers that match a certain plugin to a registration function.

        The callable `register_function` is responsible for handling the object.
        Usually it will add the object to a list of objects in the relevant class.
        For example, the plugin type 'tool' has Controller::addTool as register
        function.

        `register_function` will be called every time a plugin of `type` is loaded.

        :param plugin_type: The name of the plugin type to add.
        :param register_function: A callable that takes an object as parameter.
        """

        cls._type_register_map[plugin_type] = register_function

    @classmethod
    def removeType(cls, plugin_type: str) -> None:
        """Remove a plugin type.

        :param plugin_type: The plugin type to remove.
        """

        if plugin_type in cls._type_register_map:
            del cls._type_register_map[plugin_type]

    _type_register_map = {}  # type: Dict[str, Callable[[Any], None]]
    __instance = None    # type: PluginRegistry

    @classmethod
    def getInstance(cls, *args, **kwargs) -> "PluginRegistry":
        return cls.__instance
