pragma Singleton

import QtQuick
import QtQuick.Controls

QtObject {
    id: theme

    property SystemPalette systemPalette: SystemPalette { }

    // The controller stores one of: system, dark, light.
    property string mode: "system"

    readonly property color systemWindowColor: systemPalette.window
    readonly property bool systemIsDark: {
        var c = systemWindowColor
        return (c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722) < 0.5
    }
    readonly property bool dark: mode === "dark" || (mode === "system" && systemIsDark)

    readonly property color background: dark ? "#0B1018" : "#F3F6FA"
    readonly property color backgroundElevated: dark ? "#101722" : "#E9EEF5"
    readonly property color sidebar: dark ? "#0E141E" : "#FFFFFF"
    readonly property color surface: dark ? "#151D29" : "#FFFFFF"
    readonly property color surfaceRaised: dark ? "#1B2533" : "#F8FAFC"
    readonly property color surfaceHover: dark ? "#222E3E" : "#EEF3F8"
    readonly property color surfacePressed: dark ? "#29374A" : "#E2E9F1"
    readonly property color surfaceSelected: dark ? "#152F38" : "#E4F8F2"
    readonly property color input: dark ? "#101722" : "#F7F9FC"

    readonly property color text: dark ? "#F2F6FA" : "#17202B"
    readonly property color textSecondary: dark ? "#AAB6C5" : "#5A6878"
    readonly property color textMuted: dark ? "#748196" : "#8793A2"
    readonly property color textOnAccent: "#07130F"
    readonly property color textOnDanger: "#FFFFFF"
    readonly property color modalScrim: "#99060A10"
    readonly property color border: dark ? "#2A3545" : "#DCE3EB"
    readonly property color borderStrong: dark ? "#3A485B" : "#C6D0DC"

    readonly property color accent: dark ? "#61E6B6" : "#168C68"
    readonly property color accentHover: dark ? "#82EDC8" : "#117858"
    readonly property color accentSoft: dark ? "#163A34" : "#DDF5ED"
    readonly property color accentGlow: dark ? "#4461E6B6" : "#33168C68"
    readonly property color secondary: dark ? "#8AA8FF" : "#4D66C8"
    readonly property color success: dark ? "#62D993" : "#168954"
    readonly property color successSoft: dark ? "#17372A" : "#DDF5E8"
    readonly property color warning: dark ? "#F2C260" : "#A86808"
    readonly property color warningSoft: dark ? "#3B301B" : "#FFF0D4"
    readonly property color danger: dark ? "#FF7A86" : "#C43D4D"
    readonly property color dangerSoft: dark ? "#3B2028" : "#FCE6E9"
    readonly property color info: dark ? "#74B8FF" : "#277DC1"
    readonly property color infoSoft: dark ? "#183149" : "#E1F0FC"

    readonly property int radiusSmall: 8
    readonly property int radiusMedium: 12
    readonly property int radiusLarge: 18
    readonly property int radiusXLarge: 24
    readonly property int controlHeight: 42
    readonly property int sidebarExpanded: 244
    readonly property int sidebarCollapsed: 78
    readonly property int contentPadding: 28
    readonly property int spacingSmall: 8
    readonly property int spacingMedium: 14
    readonly property int spacingLarge: 22

    readonly property int fontCaption: 11
    readonly property int fontBody: 13
    readonly property int fontBodyLarge: 15
    readonly property int fontTitle: 22
    readonly property int fontDisplay: 30
    readonly property int animationFast: 120
    readonly property int animationNormal: 220
    readonly property int animationSlow: 360

    // Shared Game Optimization Classic television metrics.  Couch Mode scales these
    // from a 1920 px reference width instead of inheriting desktop DPI sizes.
    readonly property int couchPageMargin: 64
    readonly property int couchTopMargin: 104
    readonly property int couchBottomMargin: 90
    readonly property int couchSectionSpacing: 18
    readonly property int couchCardRadius: 18
    readonly property int couchPanelRadius: 24
    readonly property int couchDialogWidth: 880
    readonly property int couchDialogMargin: 36
    readonly property int couchButtonHeight: 64
    readonly property int couchFocusWidth: 4
    readonly property int couchTitleSize: 42
    readonly property int couchHeroTitleSize: 46
    readonly property int couchBodySize: 18
    readonly property int couchHelperSize: 16
    readonly property int couchAnimation: 150

    function statusColor(status) {
        var value = String(status || "").toLowerCase()
        if (["completed", "available", "ready", "supported", "fully supported", "active", "healthy"].indexOf(value) >= 0)
            return success
        if (["failed", "error", "unsupported", "cancelled", "missing", "missing files", "drive disconnected", "library unavailable"].indexOf(value) >= 0)
            return danger
        if (["paused", "partial support", "warning"].indexOf(value) >= 0)
            return warning
        if (["running", "analyzing", "queued", "checking"].indexOf(value) >= 0)
            return info
        return textMuted
    }

    function statusSurface(status) {
        var value = String(status || "").toLowerCase()
        if (["completed", "available", "ready", "supported", "fully supported", "active", "healthy"].indexOf(value) >= 0)
            return successSoft
        if (["failed", "error", "unsupported", "cancelled", "missing", "missing files", "drive disconnected", "library unavailable"].indexOf(value) >= 0)
            return dangerSoft
        if (["paused", "partial support", "warning"].indexOf(value) >= 0)
            return warningSoft
        if (["running", "analyzing", "queued", "checking"].indexOf(value) >= 0)
            return infoSoft
        return surfaceRaised
    }

    function display(value, fallback) {
        if (value === undefined || value === null || value === "")
            return fallback === undefined ? "-" : fallback
        return String(value)
    }
}
