import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Services.Pipewire
import qs.Ui as Ui
import qs.Commons

Ui.Panel {
  id: root
  moduleName: "patrickfanella.ht3"
  manageIpc: false
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  property var headphones: []
  property string address: ""
  property var state: ({ connected: false })
  property bool busy: false
  property bool ready: false
  property string message: "Open to connect headphone controls"
  property var draft: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  property bool dirty: false
  readonly property var frequencies: ["20", "50", "100", "200", "400", "800", "1.6k", "3.2k", "6.4k", "12.8k"]
  readonly property var sinks: {
    var result = []
    var values = Pipewire.nodes ? Pipewire.nodes.values : []
    for (var i = 0; i < values.length; i++)
      if (values[i].isSink && !values[i].isStream) result.push(values[i])
    return result
  }
  readonly property var sink: {
    if (!address) return null
    var token = address.replace(/:/g, "_").toLowerCase()
    for (var i = 0; i < sinks.length; i++)
      if (String(sinks[i].name).toLowerCase().indexOf(token) >= 0) return sinks[i]
    return null
  }
  readonly property bool hasVolume: !!(sink && sink.audio)
  readonly property bool hasEq: state.connected === true && Array.isArray(state.eq) && state.spatial === false
  readonly property bool hasMode: state.connected === true && typeof state.mode === "string"
  PwObjectTracker { objects: root.sinks }

  function request(action, args) {
    if (busy || !ready) return
    busy = true
    message = action === "connect" ? "Connecting to HT3…" : "Reading headphones…"
    var data = args || {}
    data.action = action
    helper.write(JSON.stringify(data) + "\n")
    watchdog.restart()
  }

  function changeVolume(value) {
    if (hasVolume) sink.audio.volume = Math.max(0, Math.min(1, value))
  }

  function editBand(index, value) {
    var next = draft.slice()
    next[index] = Math.round(value)
    draft = next
    dirty = true
  }

  function receive(line) {
    try {
      var response = JSON.parse(line)
      watchdog.stop()
      busy = false
      state = response.state || { connected: false }
      if (response.devices) {
        headphones = response.devices
        if (!headphones.some(function(d) { return d.address === root.address }))
          address = headphones.length ? headphones[0].address : ""
      }
      if (response.ok && response.action === "eq") dirty = false
      if (!dirty && Array.isArray(state.eq)) draft = state.eq.slice()
      message = response.ok
        ? (state.connected ? "Connected · changes confirmed by headphones" : (address ? "Ready to connect controls" : "Pair your TOZO HT3 in Bluetooth settings"))
        : response.error
      if (response.ok && response.action === "devices" && headphones.length === 1)
        request("connect", { address: address })
    } catch (error) {
      busy = false
      state = { connected: false }
      message = "Could not read headphone controls. Close and reopen to retry."
    }
  }

  onOpenedChanged: {
    if (opened) {
      dirty = false
      message = "Starting headphone controls…"
      helper.running = true
    } else {
      helper.running = false
      watchdog.stop()
      busy = false
      state = { connected: false }
    }
  }

  Process {
    id: helper
    command: ["/usr/bin/python3", decodeURIComponent(Qt.resolvedUrl("bridge.py").toString().replace(/^file:\/\//, ""))]
    stdinEnabled: true
    onStarted: {
      root.ready = true
      root.busy = false
      root.request("devices")
    }
    stdout: SplitParser { onRead: function(line) { root.receive(line) } }
    stderr: SplitParser { onRead: function(line) { console.warn("HT3 helper: " + line) } }
    onExited: function(code) {
      root.ready = false
      root.busy = false
      root.state = { connected: false }
      watchdog.stop()
      if (root.opened) root.message = "Headphone helper stopped. Close and reopen to retry."
    }
  }

  Timer {
    id: watchdog
    interval: 16000
    onTriggered: {
      helper.running = false
      root.busy = false
      root.state = { connected: false }
      root.message = "Headphone request timed out. Close and reopen to retry."
    }
  }

  Timer {
    interval: 30000
    running: root.opened && root.state.connected === true
    repeat: true
    onTriggered: if (!root.busy) root.request("refresh")
  }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰋋"
    tooltipText: "TOZO HT3 · Volume, EQ & noise control"
    onPressed: root.toggle()
    onWheelMoved: function(delta) {
      if (delta !== 0 && root.hasVolume) root.changeVolume(root.sink.audio.volume + (delta > 0 ? 0.02 : -0.02))
    }
  }

  Ui.KeyboardPanel {
    id: popup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: content
    contentWidth: popup.fittedContentWidth(Style.space(440))
    contentHeight: popup.fittedContentHeight(column.implicitHeight, Style.space(700))

    FocusScope {
      id: content
      anchors.fill: parent
      Keys.onEscapePressed: root.close()

      ScrollView {
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
          id: column
          width: content.width
          spacing: Style.space(12)

          Label {
            text: "TOZO HT3"
            color: Color.foreground
            font.pixelSize: Style.space(24)
            font.bold: true
          }
          Label {
            Layout.fillWidth: true
            text: root.message
            color: Color.foreground
            opacity: 0.75
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
          }
          RowLayout {
            Layout.fillWidth: true
            ComboBox {
              Layout.fillWidth: true
              model: root.headphones
              textRole: "name"
              enabled: !root.busy && !root.state.connected
              onActivated: function(index) {
                root.address = root.headphones[index].address
                root.dirty = false
              }
            }
            Button {
              text: root.state.connected ? "Refresh" : "Connect"
              enabled: root.ready && !root.busy && root.address !== ""
              onClicked: root.request(root.state.connected ? "refresh" : "connect", { address: root.address })
            }
          }
          Label {
            text: typeof root.state.battery === "number" ? "Battery  " + root.state.battery + "%" : "Battery  —"
            color: Color.foreground
          }
          Rectangle { Layout.fillWidth: true; height: 1; color: Color.foreground; opacity: 0.15 }
          RowLayout {
            Layout.fillWidth: true
            Label { text: "HEADPHONE VOLUME"; color: Color.foreground; font.bold: true; Layout.fillWidth: true }
            Label { text: root.hasVolume ? Math.round(root.sink.audio.volume * 100) + "%" : "Offline"; color: Color.foreground }
          }
          RowLayout {
            Layout.fillWidth: true
            Slider {
              Layout.fillWidth: true
              from: 0; to: 1; stepSize: 0.01
              enabled: root.hasVolume
              value: root.hasVolume ? root.sink.audio.volume : 0
              onMoved: root.changeVolume(value)
              Accessible.name: "HT3 playback volume"
            }
            Button {
              text: root.hasVolume && root.sink.audio.muted ? "Unmute" : "Mute"
              enabled: root.hasVolume
              onClicked: root.sink.audio.muted = !root.sink.audio.muted
            }
          }
          Label {
            visible: !root.hasVolume
            text: "Connect HT3 audio in Bluetooth settings to adjust its volume."
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Color.foreground
            opacity: 0.65
          }
          Rectangle { Layout.fillWidth: true; height: 1; color: Color.foreground; opacity: 0.15 }
          Label { text: "NOISE CONTROL"; color: Color.foreground; font.bold: true }
          RowLayout {
            Layout.fillWidth: true
            Repeater {
              model: [{ name: "Normal", mode: "normal" }, { name: "ANC", mode: "anc" }, { name: "Transparency", mode: "transparency" }]
              Button {
                required property var modelData
                Layout.fillWidth: true
                text: modelData.name
                highlighted: root.state.mode === modelData.mode
                enabled: root.hasMode && !root.busy
                onClicked: root.request("mode", { mode: modelData.mode })
                Accessible.name: "Noise control: " + modelData.name
              }
            }
          }
          Label {
            text: root.hasMode ? "Current mode: " + root.state.mode : "Connect controls to read the current mode."
            color: Color.foreground
            opacity: 0.65
          }
          Rectangle { Layout.fillWidth: true; height: 1; color: Color.foreground; opacity: 0.15 }
          RowLayout {
            Layout.fillWidth: true
            Label { text: "HARDWARE EQ"; color: Color.foreground; font.bold: true; Layout.fillWidth: true }
            Button {
              text: "Flat"
              enabled: root.hasEq && !root.busy
              onClicked: { root.draft = [0,0,0,0,0,0,0,0,0,0]; root.dirty = true }
            }
          }
          Label {
            text: root.state.spatial === true ? "Turn off spatial audio in the TOZO app to use hardware EQ." : "−5 to +5 dB · edits apply together"
            color: Color.foreground
            opacity: 0.65
            Layout.fillWidth: true
            wrapMode: Text.Wrap
          }
          RowLayout {
            Layout.fillWidth: true
            spacing: 0
            Repeater {
              model: 10
              ColumnLayout {
                required property int index
                Layout.minimumWidth: column.width / 10
                Layout.preferredWidth: column.width / 10
                Layout.maximumWidth: column.width / 10
                spacing: 4
                Label {
                  Layout.alignment: Qt.AlignHCenter
                  text: (root.draft[parent.index] / 10).toFixed(1)
                  color: Color.foreground
                  font.pixelSize: Style.space(11)
                }
                Slider {
                  Layout.alignment: Qt.AlignHCenter
                  Layout.preferredHeight: Style.space(125)
                  orientation: Qt.Vertical
                  from: -50; to: 50; stepSize: 1
                  enabled: root.hasEq && !root.busy
                  value: root.draft[parent.index]
                  onMoved: root.editBand(parent.index, value)
                  Accessible.name: root.frequencies[parent.index] + " Hz EQ gain"
                }
                Label {
                  Layout.alignment: Qt.AlignHCenter
                  text: root.frequencies[parent.index]
                  color: Color.foreground
                  font.pixelSize: Style.space(10)
                }
              }
            }
          }
          RowLayout {
            Layout.fillWidth: true
            Button {
              Layout.fillWidth: true
              text: root.dirty ? "Apply EQ" : "EQ synchronized"
              enabled: root.hasEq && root.dirty && !root.busy
              onClicked: root.request("eq", { gains: root.draft })
            }
            Button {
              text: "Revert edits"
              enabled: root.dirty && Array.isArray(root.state.eq) && !root.busy
              onClicked: { root.draft = root.state.eq.slice(); root.dirty = false }
            }
          }
        }
      }
    }
  }
}
