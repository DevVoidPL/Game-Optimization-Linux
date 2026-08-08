pragma Singleton

import QtQuick

QtObject {
    property var activePopup: null
    readonly property bool popupOpen: activePopup !== null
                                      && activePopup.visible === true

    function opening(popup) {
        if (activePopup && activePopup !== popup)
            activePopup.close()
        activePopup = popup
    }

    function closed(popup) {
        if (activePopup === popup)
            activePopup = null
    }

    function closeActive() {
        if (activePopup)
            activePopup.close()
        activePopup = null
    }
}
