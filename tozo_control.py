#!/usr/bin/env python3
"""TOZO Control - GTK4 app for Battery, Game Mode, and EQ."""

import asyncio
import json
import os
import threading
import traceback
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

import subprocess
from bleak import BleakClient, BleakScanner

# GATT UUIDs (TOZO protocol)
WRITE_UUIDS = ["b611", "faa1"]
NOTIFY_UUIDS = ["b612", "faa2"]

EQ_BANDS = ["31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]
EQ_MAX = 50

CONFIG_DIR = os.path.expanduser("~/.config/tozo-control")
PRESETS_FILE = os.path.join(CONFIG_DIR, "presets.json")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")

BUILTIN_PRESETS = {
    "Bass Boost": [50, 45, 38, 25, 25, 25, 25, 25, 25, 25],
    "Treble":     [25, 25, 25, 25, 25, 30, 35, 40, 45, 50],
    "V-Shape":    [45, 40, 30, 20, 18, 18, 20, 32, 42, 48],
    "Vocal":      [20, 22, 30, 40, 45, 45, 38, 28, 22, 20],
}


def build_get(param_id):
    return bytes([0x00, param_id, 0x00, 0x00])


def build_set(param_id, payload):
    cmd = bytearray([0x10, param_id, len(payload)])
    cmd += bytearray(payload)
    cmd.append(sum(payload) & 0xFF)
    return bytes(cmd)


def log(msg):
    with open("/tmp/tozo_control.log", "a") as f:
        f.write(msg + "\n")
    print(msg)


def load_user_presets():
    if os.path.exists(PRESETS_FILE):
        try:
            with open(PRESETS_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_user_presets(presets):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PRESETS_FILE, "w") as f:
        json.dump(presets, f, indent=2)


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_settings(settings):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


class BLEWorker:
    def __init__(self, app):
        self.app = app
        self.client = None
        self.loop = None
        self.thread = None
        self.connected = False
        self._notifs = []
        self._write_uuid = None
        self._notify_uuid = None

    def _on_notify(self, sender, data):
        self._notifs.append(bytes(data))

    def start(self):
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_coro(self, coro):
        if self.loop and self.loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            def _on_done(f):
                try:
                    f.result()
                except Exception as e:
                    log(f"BLE error: {type(e).__name__}: {e}")
                    log(traceback.format_exc())
                    GLib.idle_add(self.app.set_status, f"Error: {e}")
            fut.add_done_callback(_on_done)

    async def scan_devices(self):
        """Scan for BLE devices and return list."""
        GLib.idle_add(self.app.set_status, "Scanning...")
        log("Scanning BLE devices...")
        devices = {}

        def cb(device, adv):
            name = device.name or adv.local_name
            if name and device.address not in devices:
                devices[device.address] = {
                    "address": device.address,
                    "name": name,
                    "rssi": adv.rssi if hasattr(adv, "rssi") else None,
                    "device": device,
                }
                GLib.idle_add(self.app.add_scan_result, devices[device.address])

        scanner = BleakScanner(detection_callback=cb)
        await scanner.start()
        await asyncio.sleep(8)
        await scanner.stop()

        GLib.idle_add(self.app.set_status, f"Found {len(devices)} devices")
        GLib.idle_add(self.app.on_scan_done)
        log(f"Scan done: {len(devices)} devices")

    def _find_char(self, client, uuid_patterns, prop_filter):
        for service in client.services:
            for char in service.characteristics:
                uuid_lower = char.uuid.lower()
                for pat in uuid_patterns:
                    if pat in uuid_lower and prop_filter in str(char.properties):
                        return char.uuid
        return None

    async def _scan_for_ble_device(self, name_hint):
        """Scan BLE to find a device by name (for paired Classic BT devices)."""
        log(f"Scanning BLE for device named '{name_hint}'...")
        GLib.idle_add(self.app.set_status, f"Scanning BLE for {name_hint}...")
        found = None

        def cb(device, adv):
            nonlocal found
            dev_name = device.name or (adv.local_name if hasattr(adv, "local_name") else "")
            if dev_name and found is None:
                if name_hint.lower() in dev_name.lower() or dev_name.lower() in name_hint.lower():
                    found = device
                    log(f"Found BLE device: {device.address} ({dev_name})")

        scanner = BleakScanner(detection_callback=cb)
        await scanner.start()
        for i in range(40):  # 20 seconds
            if found:
                break
            await asyncio.sleep(0.5)
            if i % 10 == 9:
                GLib.idle_add(self.app.set_status, f"Scanning... ({(i+1)//2}s)")
        await scanner.stop()
        return found

    async def connect_device(self, device_info):
        GLib.idle_add(self.app.set_status, "Connecting...")
        log(f"Connecting to {device_info['name']} ({device_info['address']})...")
        try:
            device = device_info.get("device")  # BLEDevice from scan, or None
            needs_ble_scan = device is None

            for attempt in range(3):
                if attempt > 0:
                    log(f"Retry {attempt+1}/3...")
                    GLib.idle_add(self.app.set_status, f"Retrying ({attempt+1}/3)...")
                    await asyncio.sleep(1)

                # Scan BLE if we don't have a BLEDevice yet
                if needs_ble_scan:
                    device = await self._scan_for_ble_device(device_info["name"])
                    if device:
                        log(f"Using BLE address {device.address}")

                if not device:
                    log(f"BLE device not found in scan (attempt {attempt+1})")
                    continue

                try:
                    self.client = BleakClient(device, timeout=12)
                    await self.client.connect()
                    if self.client.is_connected:
                        break
                except Exception as e:
                    log(f"Attempt {attempt+1} failed: {e}")
                    self.client = None

                # Fallback: direct with random address type
                try:
                    addr = device.address if device else device_info["address"]
                    self.client = BleakClient(addr, timeout=10, address_type="random")
                    await self.client.connect()
                    if self.client.is_connected:
                        break
                except Exception as e:
                    log(f"Direct fallback failed: {e}")
                    self.client = None

            if not self.client or not self.client.is_connected:
                GLib.idle_add(self.app.set_status, "Connection failed. Try again.")
                GLib.idle_add(self.app.on_connect_failed)
                return

            # Find write and notify characteristics
            self._write_uuid = self._find_char(self.client, WRITE_UUIDS, "write")
            self._notify_uuid = self._find_char(self.client, NOTIFY_UUIDS, "notify")

            if not self._write_uuid:
                log("No write characteristic found - not a compatible device")
                GLib.idle_add(self.app.set_status, "No TOZO protocol found on this device")
                await self.client.disconnect()
                GLib.idle_add(self.app.on_connect_failed)
                return

            if self._notify_uuid:
                await self.client.start_notify(self._notify_uuid, self._on_notify)
                log(f"Subscribed to {self._notify_uuid}")

            self.connected = True
            log(f"Connected! write={self._write_uuid} notify={self._notify_uuid}")

            # Save last device
            settings = load_settings()
            settings["last_device"] = {
                "address": device_info["address"],
                "name": device_info["name"],
            }
            save_settings(settings)

            GLib.idle_add(
                self.app.set_status,
                f"Connected to {device_info['name']}",
            )
            GLib.idle_add(self.app.on_connected)

        except Exception as e:
            log(f"Connect error: {type(e).__name__}: {e}")
            log(traceback.format_exc())
            GLib.idle_add(self.app.set_status, f"Error: {e}")
            GLib.idle_add(self.app.on_connect_failed)

    async def disconnect(self):
        try:
            if self.client and self.client.is_connected:
                if self._notify_uuid:
                    try:
                        await self.client.stop_notify(self._notify_uuid)
                    except:
                        pass
                await self.client.disconnect()
        except Exception as e:
            log(f"Disconnect error: {e}")
        self.connected = False
        self.client = None
        GLib.idle_add(self.app.set_status, "Disconnected")
        GLib.idle_add(self.app.on_disconnected)

    async def _send_get(self, param_id):
        if not self.client or not self.client.is_connected or not self._write_uuid:
            return None
        self._notifs.clear()
        await self.client.write_gatt_char(self._write_uuid, build_get(param_id), response=False)
        await asyncio.sleep(0.8)
        return self._notifs[-1] if self._notifs else None

    async def _send_set(self, param_id, payload):
        if not self.client or not self.client.is_connected or not self._write_uuid:
            return None
        self._notifs.clear()
        await self.client.write_gatt_char(
            self._write_uuid, build_set(param_id, payload), response=False
        )
        await asyncio.sleep(0.8)
        return self._notifs[-1] if self._notifs else None

    async def read_all(self):
        log("Reading all...")
        resp = await self._send_get(0x02)
        if resp and len(resp) >= 5:
            GLib.idle_add(self.app.update_battery, resp[3], resp[4])
            log(f"Battery: L={resp[3]}% R={resp[4]}%")

        resp = await self._send_get(0x06)
        if resp and len(resp) >= 4:
            GLib.idle_add(self.app.update_game_mode, resp[3] == 1)
            log(f"Game Mode: {'ON' if resp[3] else 'OFF'}")

        resp = await self._send_get(0x0B)
        if resp and len(resp) >= 23:
            gains = list(resp[3:13])
            qs = list(resp[13:23])
            GLib.idle_add(self.app.update_eq, gains, qs)
            log(f"EQ gains: {gains}")

        GLib.idle_add(self.app.set_status, "Connected")

    async def set_game_mode(self, on):
        log(f"Setting game mode {'ON' if on else 'OFF'}")
        await self._send_set(0x06, [0x01 if on else 0x00])
        resp = await self._send_get(0x06)
        if resp and len(resp) >= 4:
            GLib.idle_add(self.app.update_game_mode, resp[3] == 1)

    async def set_eq(self, gains, qs):
        log(f"Setting EQ: {gains}")
        await self._send_set(0x0B, gains + qs)
        resp = await self._send_get(0x0B)
        if resp and len(resp) >= 23:
            GLib.idle_add(self.app.update_eq, list(resp[3:13]), list(resp[13:23]))

    async def reset_eq(self):
        log("Resetting EQ")
        await self._send_set(0x0C, [0x00])
        await asyncio.sleep(0.5)
        resp = await self._send_get(0x0B)
        if resp and len(resp) >= 23:
            GLib.idle_add(self.app.update_eq, list(resp[3:13]), list(resp[13:23]))


class TozoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.tozo.control")
        self.ble = BLEWorker(self)
        self.eq_sliders = []
        self.eq_labels = []
        self.current_qs = [5, 6, 7, 8, 10, 12, 14, 16, 18, 20]
        self._game_mode_updating = False
        self._scan_results = {}
        self.user_presets = load_user_presets()

    def do_activate(self):
        self.win = Adw.ApplicationWindow(application=self, title="TOZO Control")
        self.win.set_default_size(540, 640)

        # Main navigation stack
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        # --- Page 1: Device picker ---
        self._build_device_page()

        # --- Page 2: Controls ---
        self._build_controls_page()

        self.win.set_content(self.stack)

        css = Gtk.CssProvider()
        css.load_from_string("""
            .caption { font-size: 11px; }
            .device-row { padding: 8px; }
            .scan-btn { margin: 12px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.ble.start()
        self.win.present()

    def _get_paired_devices(self):
        """Get paired Bluetooth devices from bluetoothctl."""
        devices = []
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices", "Paired"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                # Format: "Device XX:XX:XX:XX:XX:XX Name"
                parts = line.strip().split(" ", 2)
                if len(parts) >= 3 and parts[0] == "Device":
                    devices.append({"address": parts[1], "name": parts[2]})
        except Exception as e:
            log(f"Failed to get paired devices: {e}")
        return devices

    # ---- Device picker page ----

    def _build_device_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar(title_widget=Gtk.Label(label="TOZO Control"))
        page.append(header)

        self.status_label = Gtk.Label(label="Scan for nearby devices")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_margin_top(4)
        self.status_label.set_margin_bottom(4)
        page.append(self.status_label)

        # Scan button
        self.scan_btn = Gtk.Button(label="Scan for Devices")
        self.scan_btn.add_css_class("suggested-action")
        self.scan_btn.add_css_class("scan-btn")
        self.scan_btn.set_margin_start(16)
        self.scan_btn.set_margin_end(16)
        self.scan_btn.set_margin_top(8)
        self.scan_btn.connect("clicked", self.on_scan_clicked)
        page.append(self.scan_btn)

        # Last connected device
        settings = load_settings()
        last = settings.get("last_device")
        if last:
            last_group = Adw.PreferencesGroup(title="Last Connected")
            row = Adw.ActionRow(title=last["name"], subtitle=last["address"])
            connect_btn = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
            connect_btn.add_css_class("suggested-action")
            connect_btn.connect("clicked", self.on_last_device_clicked, last)
            row.add_suffix(connect_btn)
            last_group.add(row)
            last_group.set_margin_start(16)
            last_group.set_margin_end(16)
            last_group.set_margin_top(8)
            page.append(last_group)

        # Paired devices
        paired = self._get_paired_devices()
        if paired:
            paired_group = Adw.PreferencesGroup(title="Paired Devices")
            paired_group.set_margin_start(16)
            paired_group.set_margin_end(16)
            paired_group.set_margin_top(8)
            for info in paired:
                row = Adw.ActionRow(title=info["name"], subtitle=info["address"])
                btn = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
                btn.add_css_class("suggested-action")
                btn.connect("clicked", self.on_device_connect_clicked, info)
                row.add_suffix(btn)
                paired_group.add(row)
            page.append(paired_group)

        # Device list
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.device_list_group = Adw.PreferencesGroup(title="BLE Scan Results")
        self.device_list_group.set_margin_start(16)
        self.device_list_group.set_margin_end(16)
        self.device_list_group.set_margin_top(8)
        self.device_list_group.set_margin_bottom(16)

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.append(self.device_list_group)
        scroll.set_child(list_box)
        page.append(scroll)

        self.stack.add_named(page, "devices")

    def _build_controls_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        self.ctrl_title = Gtk.Label(label="TOZO Control")
        header.set_title_widget(self.ctrl_title)

        back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        back_btn.connect("clicked", self.on_back_clicked)
        header.pack_start(back_btn)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_btn.set_tooltip_text("Refresh")
        self.refresh_btn.connect("clicked", self.on_refresh)
        header.pack_end(self.refresh_btn)
        page.append(header)

        self.ctrl_status = Gtk.Label(label="")
        self.ctrl_status.add_css_class("dim-label")
        self.ctrl_status.set_margin_top(4)
        self.ctrl_status.set_margin_bottom(4)
        page.append(self.ctrl_status)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(8)
        content.set_margin_bottom(16)

        # Battery
        batt_group = Adw.PreferencesGroup(title="Battery")
        self.batt_left_row = Adw.ActionRow(title="Left", subtitle="--")
        self.batt_right_row = Adw.ActionRow(title="Right", subtitle="--")

        self.batt_left_bar = Gtk.LevelBar()
        self.batt_left_bar.set_min_value(0)
        self.batt_left_bar.set_max_value(100)
        self.batt_left_bar.set_valign(Gtk.Align.CENTER)
        self.batt_left_bar.set_size_request(120, -1)
        self.batt_left_row.add_suffix(self.batt_left_bar)

        self.batt_right_bar = Gtk.LevelBar()
        self.batt_right_bar.set_min_value(0)
        self.batt_right_bar.set_max_value(100)
        self.batt_right_bar.set_valign(Gtk.Align.CENTER)
        self.batt_right_bar.set_size_request(120, -1)
        self.batt_right_row.add_suffix(self.batt_right_bar)

        batt_group.add(self.batt_left_row)
        batt_group.add(self.batt_right_row)
        content.append(batt_group)

        # Game Mode
        game_group = Adw.PreferencesGroup(title="Game Mode")
        self.game_row = Adw.ActionRow(
            title="Low Latency", subtitle="Reduce audio delay for gaming"
        )
        self.game_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.game_switch.connect("state-set", self.on_game_mode_toggled)
        self.game_row.add_suffix(self.game_switch)
        self.game_row.set_activatable_widget(self.game_switch)
        game_group.add(self.game_row)
        content.append(game_group)

        # EQ
        eq_group = Adw.PreferencesGroup(title="Equalizer (10-Band)")
        eq_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        eq_box.set_margin_top(8)
        eq_box.set_margin_bottom(8)

        for i, band in enumerate(EQ_BANDS):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            db_label = Gtk.Label(label="0.0")
            db_label.add_css_class("caption")
            db_label.set_width_chars(4)
            self.eq_labels.append(db_label)
            col.append(db_label)

            adj = Gtk.Adjustment(value=EQ_MAX, lower=0, upper=EQ_MAX, step_increment=1, page_increment=5)
            slider = Gtk.Scale(
                orientation=Gtk.Orientation.VERTICAL, adjustment=adj,
                inverted=True, vexpand=True,
            )
            slider.set_draw_value(False)
            slider.set_size_request(-1, 180)
            slider.connect("value-changed", self.on_eq_slider_changed, i)
            self.eq_sliders.append(slider)
            col.append(slider)

            freq_label = Gtk.Label(label=band)
            freq_label.add_css_class("caption")
            col.append(freq_label)

            eq_box.append(col)

        eq_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        eq_wrapper.append(eq_box)

        eq_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, homogeneous=True)
        eq_btn_box.set_margin_top(8)

        self.eq_flat_btn = Gtk.Button(label="Flat")
        self.eq_flat_btn.connect("clicked", self.on_eq_flat)

        self.eq_reset_btn = Gtk.Button(label="Reset")
        self.eq_reset_btn.add_css_class("destructive-action")
        self.eq_reset_btn.connect("clicked", self.on_eq_reset)

        self.eq_apply_btn = Gtk.Button(label="Apply")
        self.eq_apply_btn.add_css_class("suggested-action")
        self.eq_apply_btn.connect("clicked", self.on_eq_apply)

        eq_btn_box.append(self.eq_flat_btn)
        eq_btn_box.append(self.eq_reset_btn)
        eq_btn_box.append(self.eq_apply_btn)
        eq_wrapper.append(eq_btn_box)
        eq_group.add(eq_wrapper)
        content.append(eq_group)

        # Presets (built-in + user)
        self.preset_group = Adw.PreferencesGroup(title="EQ Presets")
        self.preset_flow = None
        self._rebuild_presets()
        content.append(self.preset_group)

        # Save preset
        save_group = Adw.PreferencesGroup()
        save_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_box.set_margin_top(4)
        self.preset_name_entry = Gtk.Entry(placeholder_text="Preset name...")
        self.preset_name_entry.set_hexpand(True)
        save_box.append(self.preset_name_entry)

        save_btn = Gtk.Button(label="Save")
        save_btn.connect("clicked", self.on_save_preset)
        save_box.append(save_btn)
        save_group.add(save_box)
        content.append(save_group)

        scroll.set_child(content)
        page.append(scroll)
        self.stack.add_named(page, "controls")

    def _rebuild_presets(self):
        if self.preset_flow is not None:
            self.preset_group.remove(self.preset_flow)

        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            max_children_per_line=4,
            row_spacing=4,
            column_spacing=4,
        )
        flow.set_margin_top(4)
        flow.set_margin_bottom(4)

        # Built-in
        for name, values in BUILTIN_PRESETS.items():
            btn = Gtk.Button(label=name)
            btn.connect("clicked", self.on_preset_clicked, values)
            flow.append(btn)

        # User presets
        for name, data in self.user_presets.items():
            box = Gtk.Box(spacing=4)
            btn = Gtk.Button(label=name, hexpand=True)
            btn.connect("clicked", self.on_preset_clicked, data["gains"])
            box.append(btn)

            del_btn = Gtk.Button(icon_name="edit-delete-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_tooltip_text(f"Delete '{name}'")
            del_btn.connect("clicked", self.on_delete_preset, name)
            box.append(del_btn)

            flow.append(box)

        self.preset_flow = flow
        self.preset_group.add(flow)

    # ---- Status ----

    def set_status(self, text):
        self.status_label.set_label(str(text))
        if hasattr(self, "ctrl_status"):
            self.ctrl_status.set_label(str(text))

    # ---- Device picker callbacks ----

    def on_scan_clicked(self, btn):
        self.scan_btn.set_sensitive(False)
        self.scan_btn.set_label("Scanning...")
        self._scan_results.clear()
        # Recreate the device list group
        parent = self.device_list_group.get_parent()
        if parent:
            parent.remove(self.device_list_group)
        self.device_list_group = Adw.PreferencesGroup(title="Nearby Devices")
        self.device_list_group.set_margin_start(16)
        self.device_list_group.set_margin_end(16)
        self.device_list_group.set_margin_top(8)
        self.device_list_group.set_margin_bottom(16)
        if parent:
            parent.append(self.device_list_group)
        self.ble.run_coro(self.ble.scan_devices())

    def add_scan_result(self, info):
        addr = info["address"]
        if addr in self._scan_results:
            return
        self._scan_results[addr] = info

        rssi = info.get("rssi")
        rssi_str = f"  ({rssi} dBm)" if rssi else ""
        row = Adw.ActionRow(title=info["name"], subtitle=f"{addr}{rssi_str}")

        btn = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
        btn.connect("clicked", self.on_device_connect_clicked, info)
        row.add_suffix(btn)
        self.device_list_group.add(row)

    def on_scan_done(self):
        self.scan_btn.set_sensitive(True)
        self.scan_btn.set_label("Scan for Devices")

    def on_last_device_clicked(self, btn, info):
        btn.set_sensitive(False)
        self.set_status(f"Connecting to {info['name']}...")
        self.ble.run_coro(self.ble.connect_device(info))

    def on_device_connect_clicked(self, btn, info):
        btn.set_sensitive(False)
        self.set_status(f"Connecting to {info['name']}...")
        self.ble.run_coro(self.ble.connect_device(info))

    # ---- Connection state ----

    def on_connected(self):
        self.ctrl_title.set_label("TOZO Control")
        self.stack.set_visible_child_name("controls")
        self.ble.run_coro(self.ble.read_all())

    def on_connect_failed(self):
        self.scan_btn.set_sensitive(True)
        self.scan_btn.set_label("Scan for Devices")

    def on_disconnected(self):
        self.stack.set_visible_child_name("devices")
        self.batt_left_row.set_subtitle("--")
        self.batt_right_row.set_subtitle("--")
        self.batt_left_bar.set_value(0)
        self.batt_right_bar.set_value(0)

    def on_back_clicked(self, btn):
        if self.ble.connected:
            self.ble.run_coro(self.ble.disconnect())
        else:
            self.stack.set_visible_child_name("devices")

    def on_refresh(self, btn):
        if self.ble.connected:
            self.ble.run_coro(self.ble.read_all())

    # ---- Data updates ----

    def update_battery(self, left, right):
        self.batt_left_row.set_subtitle(f"{left}%")
        self.batt_right_row.set_subtitle(f"{right}%")
        self.batt_left_bar.set_value(left)
        self.batt_right_bar.set_value(right)

    def update_game_mode(self, on):
        self._game_mode_updating = True
        self.game_switch.set_active(on)
        self._game_mode_updating = False

    def update_eq(self, gains, qs):
        self.current_qs = qs
        for i, g in enumerate(gains):
            self.eq_sliders[i].set_value(g)
            db = (g - EQ_MAX) / 10.0
            self.eq_labels[i].set_label(f"{db:+.1f}")

    # ---- Control callbacks ----

    def on_game_mode_toggled(self, switch, state):
        if self._game_mode_updating:
            return False
        if self.ble.connected:
            self.ble.run_coro(self.ble.set_game_mode(state))
        return False

    def on_eq_slider_changed(self, slider, idx):
        val = int(slider.get_value())
        db = (val - EQ_MAX) / 10.0
        self.eq_labels[idx].set_label(f"{db:+.1f}")

    def on_eq_apply(self, btn):
        if not self.ble.connected:
            return
        gains = [int(s.get_value()) for s in self.eq_sliders]
        self.set_status("Applying EQ...")
        self.ble.run_coro(self.ble.set_eq(gains, self.current_qs))

    def on_eq_reset(self, btn):
        if not self.ble.connected:
            return
        self.set_status("Resetting EQ...")
        self.ble.run_coro(self.ble.reset_eq())

    def on_eq_flat(self, btn):
        for s in self.eq_sliders:
            s.set_value(EQ_MAX)

    def on_preset_clicked(self, btn, values):
        for i, v in enumerate(values):
            self.eq_sliders[i].set_value(v)

    def on_save_preset(self, btn):
        name = self.preset_name_entry.get_text().strip()
        if not name:
            self.set_status("Enter a preset name")
            return
        gains = [int(s.get_value()) for s in self.eq_sliders]
        self.user_presets[name] = {"gains": gains, "qs": list(self.current_qs)}
        save_user_presets(self.user_presets)
        self._rebuild_presets()
        self.preset_name_entry.set_text("")
        self.set_status(f"Saved preset '{name}'")

    def on_delete_preset(self, btn, name):
        if name in self.user_presets:
            del self.user_presets[name]
            save_user_presets(self.user_presets)
            self._rebuild_presets()
            self.set_status(f"Deleted preset '{name}'")


if __name__ == "__main__":
    open("/tmp/tozo_control.log", "w").close()
    app = TozoApp()
    app.run()
