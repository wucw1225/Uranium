// Copyright (c) 2018 Ultimaker B.V.
// Uranium is released under the terms of the LGPLv3 or higher.

import QtQuick 2.1
import QtQuick.Controls 1.1

/**
 * 这是QtQuick控件菜单栏中缺少API的一种解决方法。
 * 它复制了QtQuick Controls的ApplicationWindow类中包含的某些功能，以使菜单栏真正起作用。
 */
Rectangle
{
    id: menuBackground;

    property QtObject window;
    Binding
    {
        target: menu.__contentItem
        property: "width"
        value: window.width
        when: !menu.__isNative
    }

    default property alias menus: menu.menus

    width: menu.__isNative ? 0 : menu.__contentItem.width
    height: menu.__isNative ? 0 : menu.__contentItem.height

    color: palette.window;

    Keys.forwardTo: menu.__contentItem;

    MenuBar
    {
        id: menu

        Component.onCompleted:
        {
            __contentItem.parent = menuBackground;
        }
    }

    SystemPalette
    {
        id: palette
        colorGroup: SystemPalette.Active
    }
}
