from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .state import AppController, AppState


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller
        self.setWindowTitle("Injector Tester")
        self.resize(1240, 820)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(self._build_safety_banner())
        layout.addWidget(self._build_connection_bar())
        layout.addWidget(self._build_error_banner())
        layout.addWidget(self._build_top_panels())
        layout.addWidget(self._build_action_panel())
        layout.addWidget(self._build_status_and_log_splitter(), stretch=1)

        controller.state_changed.connect(self.render)
        self._refresh_ports()
        self.render(controller.state)

    def _build_safety_banner(self) -> QGroupBox:
        group = QGroupBox("Injector Driver Safety")
        layout = QVBoxLayout(group)
        self.safety_warning_label = QLabel()
        self.safety_warning_label.setWordWrap(True)
        self.safety_warning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.safety_warning_label.setStyleSheet("color: #7a1f00; font-weight: 600;")
        layout.addWidget(self.safety_warning_label)
        return group

    def _build_connection_bar(self) -> QGroupBox:
        group = QGroupBox("Connection")
        layout = QHBoxLayout(group)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QComboBox.NoInsert)
        self.port_combo.setMinimumContentsLength(22)

        self.refresh_ports_button = QPushButton("Refresh Ports")
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.status_label = QLabel("Disconnected")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)

        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        self.connect_button.clicked.connect(self._connect_port)
        self.disconnect_button.clicked.connect(self._controller.disconnect_port)

        layout.addWidget(QLabel("Port"))
        layout.addWidget(self.port_combo, stretch=2)
        layout.addWidget(self.refresh_ports_button)
        layout.addWidget(self.connect_button)
        layout.addWidget(self.disconnect_button)
        layout.addWidget(self.status_label, stretch=3)
        return group

    def _build_error_banner(self) -> QGroupBox:
        group = QGroupBox("Errors")
        layout = QVBoxLayout(group)
        self.error_label = QLabel("No current errors.")
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setStyleSheet(
            "background-color: #fff1f0; color: #9f1239; border: 2px solid #dc2626; "
            "padding: 10px; font-weight: 700;"
        )
        layout.addWidget(self.error_label)
        return group

    def _build_top_panels(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_run_config_panel(), stretch=3)
        layout.addWidget(self._build_channel_selection_panel(), stretch=2)
        return container

    def _build_run_config_panel(self) -> QGroupBox:
        group = QGroupBox("Run Configuration")
        layout = QFormLayout(group)

        self.model_combo = QComboBox()
        self.model_combo.addItem("0 - 4-stroke", 0)
        self.model_combo.addItem("1 - 1 event/rev", 1)

        self.rpm_spin = QDoubleSpinBox()
        self.rpm_spin.setRange(1.0, 50000.0)
        self.rpm_spin.setDecimals(1)
        self.rpm_spin.setValue(1000.0)

        self.duty_spin = QDoubleSpinBox()
        self.duty_spin.setRange(0.1, 99.9)
        self.duty_spin.setDecimals(1)
        self.duty_spin.setValue(25.0)

        self.test_mode_combo = QComboBox()
        self.test_mode_combo.addItem("All", "all")
        self.test_mode_combo.addItem("Sequential", "sequential")
        self.test_mode_combo.setCurrentIndex(1)
        self.test_mode_combo.currentIndexChanged.connect(self._sync_test_mode)

        self.auto_poll_checkbox = QCheckBox("Auto-poll STATUS after test start")
        self.auto_poll_checkbox.setChecked(True)
        self.auto_poll_checkbox.stateChanged.connect(self._sync_auto_poll_enabled)

        self.auto_poll_interval_combo = QComboBox()
        self.auto_poll_interval_combo.addItem("0.25 s", 250)
        self.auto_poll_interval_combo.addItem("0.5 s", 500)
        self.auto_poll_interval_combo.addItem("1.0 s", 1000)
        self.auto_poll_interval_combo.addItem("2.0 s", 2000)
        self.auto_poll_interval_combo.setCurrentIndex(2)
        self.auto_poll_interval_combo.currentIndexChanged.connect(self._sync_auto_poll_interval)

        self.pulses_spin = QSpinBox()
        self.pulses_spin.setRange(1, 2_000_000_000)
        self.pulses_spin.setValue(100)

        layout.addRow("Model", self.model_combo)
        layout.addRow("RPM", self.rpm_spin)
        layout.addRow("Duty %", self.duty_spin)
        layout.addRow("Test Mode", self.test_mode_combo)
        layout.addRow("Pulse Count", self.pulses_spin)
        return group

    def _build_channel_selection_panel(self) -> QGroupBox:
        group = QGroupBox("Channel Selection")
        layout = QVBoxLayout(group)
        self.channel_checks: list[QCheckBox] = []

        for channel in range(1, 5):
            checkbox = QCheckBox(f"CH{channel}")
            checkbox.setChecked(channel == 1)
            checkbox.stateChanged.connect(self._sync_selected_channels)
            self.channel_checks.append(checkbox)
            layout.addWidget(checkbox)

        layout.addStretch(1)
        return group

    def _build_action_panel(self) -> QGroupBox:
        group = QGroupBox("Actions")
        layout = QGridLayout(group)
        self.selected_action_mode_label = QLabel()
        self.selected_action_mode_label.setWordWrap(True)
        self.selected_action_mode_label.setTextFormat(Qt.TextFormat.PlainText)

        self.run_selected_button = QPushButton("Run Selected")
        self.stop_all_button = QPushButton("Stop All")
        self.read_status_button = QPushButton("Read Status")
        self.help_button = QPushButton("Help")
        self.poll_interval_label = QLabel("Poll Interval")
        self.stop_all_button.setStyleSheet(
            "background-color: #b91c1c; color: white; font-weight: 700; min-height: 44px;"
        )
        self.run_selected_button.setToolTip(
            "Apply the current model, RPM, duty, and pulse count to the checked channels, then run them using the selected test mode."
        )
        self.stop_all_button.setToolTip(
            "Emergency stop for all four channels regardless of the checkbox selection."
        )
        self.read_status_button.setToolTip(
            "Query the firmware and refresh the status table with the current live state."
        )
        self.help_button.setToolTip(
            "Request the firmware HELP text and protocol summary."
        )

        self.run_selected_button.clicked.connect(self._run_selected)
        self.stop_all_button.clicked.connect(self._controller.stop_all)
        self.read_status_button.clicked.connect(self._controller.refresh_status)
        self.help_button.clicked.connect(self._controller.request_help)

        layout.addWidget(self.selected_action_mode_label, 0, 0, 1, 4)
        layout.addWidget(self.auto_poll_checkbox, 1, 0, 1, 2)
        layout.addWidget(self.poll_interval_label, 1, 2)
        layout.addWidget(self.auto_poll_interval_combo, 1, 3)
        layout.addWidget(self.run_selected_button, 2, 0, 1, 2)
        layout.addWidget(self.read_status_button, 2, 2)
        layout.addWidget(self.help_button, 2, 3)
        layout.addWidget(self.stop_all_button, 3, 0, 1, 4)

        return group

    def _build_status_and_log_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_status_table_group())
        splitter.addWidget(self._build_log_group())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _build_status_table_group(self) -> QGroupBox:
        group = QGroupBox("Channel Status")
        layout = QVBoxLayout(group)

        self.status_summary_label = QLabel("MODEL 0 | TICK_US 20 | ACTIVE_MASK 0x0 | STATE_MASK 0x0")
        self.status_summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.progress_label = QLabel("Idle")
        self.progress_label.setTextFormat(Qt.TextFormat.PlainText)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.status_table = QTableWidget(4, 10)
        self.status_table.setHorizontalHeaderLabels(
            [
                "Channel",
                "Enabled",
                "State",
                "Mode",
                "RPM",
                "Duty %",
                "On Ticks",
                "Off Ticks",
                "Pulses Left",
                "Stop After Low",
            ]
        )
        self.status_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.status_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        layout.addWidget(self.status_summary_label)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_table)
        return group

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("Raw Serial Log")
        layout = QVBoxLayout(group)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        return group

    def _refresh_ports(self) -> None:
        current_text = self.port_combo.currentText().strip()
        ports = self._controller.list_ports()

        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current_text:
            if current_text not in ports:
                self.port_combo.addItem(current_text)
            self.port_combo.setCurrentText(current_text)
        elif ports:
            self.port_combo.setCurrentIndex(0)
        else:
            self.port_combo.setEditText("/dev/ttyACM0")
        self.port_combo.blockSignals(False)

    def _connect_port(self) -> None:
        self._controller.connect_port(self.port_combo.currentText().strip())

    def _sync_test_mode(self) -> None:
        self._controller.set_test_mode(str(self.test_mode_combo.currentData()))

    def _sync_auto_poll_enabled(self) -> None:
        self._controller.set_auto_poll_enabled(self.auto_poll_checkbox.isChecked())

    def _sync_auto_poll_interval(self) -> None:
        interval_ms = int(self.auto_poll_interval_combo.currentData())
        self._controller.set_auto_poll_interval_ms(interval_ms)

    def _validate_run_config(self) -> bool:
        if not self.rpm_spin.text().strip():
            self._controller.report_validation_error("RPM is required before sending commands")
            return False
        if not self.duty_spin.text().strip():
            self._controller.report_validation_error("Duty is required before sending commands")
            return False
        return True

    def _run_selected(self) -> None:
        if not self._validate_run_config():
            return
        if not self.pulses_spin.text().strip():
            self._controller.report_validation_error("Pulse count is required before sending commands")
            return
        self._controller.run_selected_test(
            int(self.model_combo.currentData()),
            self.rpm_spin.value(),
            self.duty_spin.value(),
            self.pulses_spin.value(),
        )

    def _sync_selected_channels(self) -> None:
        channels = [index + 1 for index, checkbox in enumerate(self.channel_checks) if checkbox.isChecked()]
        self._controller.set_selected_channels(channels)

    def _set_table_item(self, row: int, column: int, value: str) -> None:
        item = self.status_table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.status_table.setItem(row, column, item)
        item.setText(value)

    def render(self, state: AppState) -> None:
        self.safety_warning_label.setText(state.safety_warning)
        self.status_label.setText(state.status_message)
        self.disconnect_button.setEnabled(state.connected)
        self.connect_button.setEnabled(not state.connected)
        self.selected_action_mode_label.setText(state.selected_action_mode_label)
        if state.has_error:
            self.error_label.setText(state.last_error_message)
            self.error_label.show()
        else:
            self.error_label.setText("No current errors.")
            self.error_label.hide()
        self.status_summary_label.setText(
            "MODEL "
            f"{state.firmware_status.model} | "
            f"TICK_US {state.firmware_status.tick_us} | "
            f"ACTIVE_MASK 0x{state.firmware_status.active_mask:X} | "
            f"STATE_MASK 0x{state.firmware_status.state_mask:X}"
        )

        model_index = self.model_combo.findData(state.pulse_model)
        if model_index >= 0 and self.model_combo.currentIndex() != model_index:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(model_index)
            self.model_combo.blockSignals(False)

        interval_index = self.auto_poll_interval_combo.findData(state.auto_poll_interval_ms)
        if interval_index >= 0 and self.auto_poll_interval_combo.currentIndex() != interval_index:
            self.auto_poll_interval_combo.blockSignals(True)
            self.auto_poll_interval_combo.setCurrentIndex(interval_index)
            self.auto_poll_interval_combo.blockSignals(False)

        if self.auto_poll_checkbox.isChecked() != state.auto_poll_enabled:
            self.auto_poll_checkbox.blockSignals(True)
            self.auto_poll_checkbox.setChecked(state.auto_poll_enabled)
            self.auto_poll_checkbox.blockSignals(False)

        test_mode_index = self.test_mode_combo.findData(state.test_mode)
        if test_mode_index >= 0 and self.test_mode_combo.currentIndex() != test_mode_index:
            self.test_mode_combo.blockSignals(True)
            self.test_mode_combo.setCurrentIndex(test_mode_index)
            self.test_mode_combo.blockSignals(False)

        self.progress_label.setText(state.test_progress.label)
        self.progress_bar.setRange(state.test_progress.minimum, state.test_progress.maximum)
        if state.test_progress.maximum > 0:
            self.progress_bar.setValue(state.test_progress.value)
        else:
            self.progress_bar.setValue(0)

        any_selected = bool(state.selected_mask)
        self.run_selected_button.setEnabled(any_selected)

        for index, channel in enumerate(state.channels):
            selected = bool(state.selected_mask & (1 << index))
            if self.channel_checks[index].isChecked() != selected:
                self.channel_checks[index].blockSignals(True)
                self.channel_checks[index].setChecked(selected)
                self.channel_checks[index].blockSignals(False)

            self._set_table_item(index, 0, f"CH{channel.channel}")
            self._set_table_item(index, 1, "Yes" if channel.enabled else "No")
            self._set_table_item(index, 2, "ON" if channel.state else "OFF")
            self._set_table_item(index, 3, channel.mode)
            self._set_table_item(index, 4, f"{channel.rpm:.1f}")
            self._set_table_item(index, 5, f"{channel.duty:.1f}")
            self._set_table_item(index, 6, str(channel.on_ticks))
            self._set_table_item(index, 7, str(channel.off_ticks))
            self._set_table_item(index, 8, str(channel.pulses_left))
            self._set_table_item(index, 9, "Yes" if channel.stop_after_low else "No")

        self.log_text.setPlainText("\n".join(state.log_lines))
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
