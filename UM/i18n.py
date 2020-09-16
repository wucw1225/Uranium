# Copyright (c) 2020 Ultimaker B.V.
# Uranium is released under the terms of the LGPLv3 or higher.

import gettext
from typing import Any, Dict, Optional, cast, TYPE_CHECKING

from UM.Logger import Logger
from UM.Resources import Resources

if TYPE_CHECKING:
    from UM.Application import Application


class i18nCatalog: # [CodeStyle：Ultimaker代码样式要求类以大写字母开头。 但是按照惯例，i18n是小写字母。] pylint: disable=invalid-name
    """包装gettext转换目录以简化使用。

    此类包装gettext转换目录以简化其使用。 它将从Resource/i18n加载翻译目录，并允许指定要加载的语言。

    要使用此类，请使用要加载的目录名称创建其实例。 然后在实例上调用`i18n`或`i18nc`在目录中执行查找。

    标准上下文和翻译标签
    --------------------------------------

    翻译系统依赖于一组标准上下文和类似HTML的翻译标签。 有关详细信息，请参见[翻译指南]（docs / translations.md）。

    """

    def __init__(self, name: str = None, language: str = "default") -> None: #pylint: disable=bad-whitespace
        """创建一个新目录。

        :param name: 要加载的目录的名称。
        :param language: 要加载的语言。 有效值为语言代码或“默认”。 当指定“默认”时，将根据系统的语言设置确定要加载的语言。

        :注意当“语言”为“默认”时，可以使用“ LANGUAGE”环境变量覆盖要加载的语言。
        """

        self.__name = name
        self.__language = language
        self.__translation = None   # type: Optional[gettext.NullTranslations]
        self.__require_update = True
        self._update() #现在已经设置了语言，请加载实际的翻译文档。

    def hasTranslationLoaded(self) -> bool:
        """是否将翻译的文本加载到此目录中。

        如果有翻译后的文本，可以安全地使用“ gettext”方法等请求文本。

        :return: ``True`` 如果文本已加载到此目录中, or ``False``如果不是。
        """

        return self.__translation is not None

    def i18n(self, text: str, *args: Any) -> str:
        """将字符串标记为可翻译。

        :param text: 标记为可翻译的字符串
        :param args: 格式化参数。 这些将替换转换后的字符串中的格式设置元素。 参见python str.format（）。
        :return: 翻译的文本或未翻译的文本（如果未找到翻译）。
        """


        if self.__require_update:
            self._update()

        translated = text  # 如果未加载翻译目录，则默认为硬编码文本。
        if self.hasTranslationLoaded():
            translated = cast(gettext.NullTranslations, self.__translation).gettext(text)

        if args:
            translated = translated.format(*args)  # 位置参数将在（翻译的）文本中替换。
        return self._replaceTags(translated)  # 同时替换 global keys.

    def i18nc(self, context: str, text: str, *args: Any) -> str:
        """将字符串标记为可翻译，并为翻译人员提供上下文。

        :param context: 字符串的上下文，即解释文本用法的内容。
        :param text: 标记为可翻译的文本。
        :param args: 格式化参数。 这些将替换转换后的字符串中的格式设置元素。 参见python的str.format（）。
        :return: 翻译的文本或未翻译的文本（如果在此目录中找不到）。
        """

        if self.__require_update:
            self._update()

        translated = text  # 如果未加载翻译目录，则默认为硬编码文本。
        if self.hasTranslationLoaded():
            message_with_context = "{0}\x04{1}".format(context, text)  # \x04 是“传输结束”字节，指示gettext它们是两个不同的文本。
            message = cast(gettext.NullTranslations, self.__translation).gettext(message_with_context)
            if message != message_with_context:
                translated = message

        if args:
            translated = translated.format(*args)  # 位置参数将在（翻译的）文本中替换。
        return self._replaceTags(translated)  # 同时替换 global keys.

    def i18np(self, single: str, multiple: str, counter: int, *args: Any) -> str:
        """将字符串标记为可复数形式的翻译。

        :param single: 字符串的单数形式。
        :param multiple: 字符串的复数形式。
        :param counter: 该值确定应使用单数形式还是复数形式。
        :param args: 格式化参数。 这些将替换转换后的字符串中的格式设置元素。 参见python的str.format（）。
        :return: 翻译的字符串，如果找不到翻译，则为未翻译的文本。 请注意，后备仅检查counter是否大于一，如果大于，则返回复数形式。

        :注意对于英语以外的其他语言，可能存在一种以上的复数形式。
        counter始终用于确定使用哪种格式，语言文件指定可用的复数形式。此外，将计数器作为第一个参数传递以格式化字符串。
        """

        if self.__require_update:
            self._update()

        translated = multiple if counter != 1 else single  # 如果未加载翻译目录，则默认为硬编码文本。
        if self.hasTranslationLoaded():
            translated = cast(gettext.NullTranslations, self.__translation).ngettext(single, multiple, counter)

        translated = translated.format(counter, args)  # 在（翻译的）文本中替换了位置参数，但是这次将counter视为第一个参数。
        return self._replaceTags(translated)  # 同时替换 global keys.

    def i18ncp(self, context: str, single: str, multiple: str, counter: int, *args: Any) -> str:
        """将字符串标记为可翻译的形式，包括复数形式和翻译上下文。

        :param context: 此字符串的上下文。
        :param single: 字符串的单数形式。
        :param multiple: 字符串的复数形式
        :param counter: 该值确定应使用单数形式还是复数形式。
        :param args: 格式化参数。 这些将替换转换后的字符串中的格式设置元素。 参见python的str.format（）。
        :return: 已翻译的字符串，如果没有则为未翻译的文本。请注意，后备仅检查counter是否大于一，如果大于，则返回复数形式。

        :注意对于英语以外的其他语言，可能存在一种以上的复数形式。
        counter始终用于确定使用哪种格式，语言文件指定可用的复数形式。此外，将计数器作为第一个参数传递以格式化字符串。
        """

        if self.__require_update:
            self._update()

        translated = multiple if counter != 1 else single  # 如果未加载翻译目录，则默认为硬编码文本。
        if self.hasTranslationLoaded():
            message_with_context = "{0}\x04{1}".format(context, single)  # \x04 是“传输结束”字节，指示gettext它们是两个不同的文本。
            message = cast(gettext.NullTranslations, self.__translation).ngettext(message_with_context, multiple, counter)

            if message != message_with_context:
                translated = message

        translated = translated.format(counter, args)
        return self._replaceTags(translated)

    def _replaceTags(self, string: str) -> str:
        """用全局定义的替换值替换字符串中的格式标记。

        可以使用setTagReplacements方法定义替换哪些标签。

        :param string: 替换标签的文本。
        :return: 已替换标签的文本
        """

        output = string
        for key, value in self.__tag_replacements.items():
            source_open = "<{0}>".format(key)
            source_close = "</{0}>".format(key)

            if value:
                dest_open = "<{0}>".format(value)
                dest_close = "</{0}>".format(value)
            else:
                dest_open = ""
                dest_close = ""

            output = output.replace(source_open, dest_open).replace(source_close, dest_close)

        return output

    def _update(self) -> None:
        """通过(再次)从文件中加载翻译后的文本来填充目录。"""

        if not self.__application:
            self.__require_update = True
            return

        if not self.__name:
            self.__name = self.__application.getApplicationName()
        if self.__language == "default":
            self.__language = self.__application.getApplicationLanguage()

        # 询问gettext以获取.mo文件中的所有翻译。
        for path in Resources.getAllPathsForType(Resources.i18n):
            if gettext.find(cast(str, self.__name), path, languages = [self.__language]):
                try:
                    self.__translation = gettext.translation(cast(str, self.__name), path, languages = [self.__language])
                except OSError:
                    Logger.warning("Corrupt or inaccessible translation file: {fname}".format(fname = self.__name))

        self.__require_update = False

    @classmethod
    def setTagReplacements(cls, replacements: Dict[str, Optional[str]]) -> None:
        """更改在每个国际化字符串中替换的全局标签。

        如果文本包含某种形式的<key>或</key>，则“ key”一词将替换为该词典中指定键的内容。

        :param replacements: 字符串到字符串的字典，指示应替换标签之间的哪些单词。
        """

        cls.__tag_replacements = replacements

    @classmethod
    def setApplication(cls, application: "Application") -> None:
        """设置``Application``实例以从中请求语言和应用程序名称。

        :param application: 正在运行的应用程序的``应用程序''实例。
        """

        cls.__application = application

    @classmethod
    def setApplicationName(cls, applicationName: str) -> None:
        cls.__name = applicationName
        cls.__require_update = True

    @classmethod
    def setLanguage(cls, language: str) -> None:
        cls.__language = language
        cls.__require_update = True

    # 默认 replacements 将丢弃所有标签
    __tag_replacements = {
        "filename": None,
        "message": None
    }   # type: Dict[str, Optional[str]]
    __application = None  # type: Optional[Application]
