import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar entry point: an icon+count pill for Things 3. Left-click toggles the
// task popup, middle-click cycles the pill between Today / Done today /
// Inbox, right-click forces a cloud sync refresh. All Things state (tasks,
// fetching, the active view) lives in Panel.qml; this file only reads it
// back to decide what the pill shows.
BarWidget {
  id: root
  moduleName: "reysu.things"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function refresh() {
    if (panelLoader.item && panelLoader.item.refresh) panelLoader.item.refresh()
  }

  function cycleView() {
    if (panelLoader.item && panelLoader.item.cycleView) panelLoader.item.cycleView(1)
  }

  // ---- Shape contract for shell.summon/hide/toggle routing:
  //      Bar.findPanelWidget requires open/close/opened on this root.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function togglePanel() {
    if (panelLoader.item) panelLoader.item.toggle()
  }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  readonly property string pinnedTitle: panelLoader.item ? (panelLoader.item.pinnedTitle || "") : ""

  // Ellipsizes the pinned task's title to a fixed pixel budget for the bar.
  TextMetrics {
    id: pinMetrics
    font.family: button.fontFamily
    font.pixelSize: button.fontSize
    text: root.pinnedTitle
    elide: Text.ElideRight
    elideWidth: 170
  }

  // Count always shows — "\uf00c 0" reading as "nothing done yet today"
  // is information, not noise. A pinned "now" task rides along with its
  // own icon (horizontal bars only — no room in vertical mode).
  readonly property string displayLabel: {
    if (!panelLoader.item) return "\uf005"
    if (!root.vertical && root.pinnedTitle !== "")
      return "\uf04b " + pinMetrics.elidedText
    return panelLoader.item.viewIcon + " " + panelLoader.item.barCount
  }

  readonly property string tooltip: panelLoader.item ? panelLoader.item.countsTooltip : "Things"

  function injectPanelAndRefresh() {
    injectPanel()
    if (panelLoader.item && panelLoader.item.refresh) panelLoader.item.refresh()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanelAndRefresh)
    }
  }

  IpcHandler {
    target: "reysu.things"

    function refresh(): void { root.refresh() }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function cycle(): void { root.cycleView() }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.displayLabel
    tooltipText: root.tooltip
    horizontalMargin: 5
    verticalPadding: 8.75

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.cycleView()
      else if (b === Qt.RightButton) root.refresh()
      else root.togglePanel()
    }
  }
}
