from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .advanced_testing import (
    ADVANCED_TEST_MODEL,
    AdvancedTestInputs,
    DeadtimePoint,
    AdvancedCalculationResult,
    calculate_advanced_test,
    cc_per_min_from_lb_per_hour,
    default_deadtime_curve,
    lb_per_hour_from_cc_per_min,
)
from .state import AppController, AppState


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller
        self._syncing_injector_size = False
        self._updating_deadtime_table = False
        self._latest_advanced_result: AdvancedCalculationResult | None = None
        self._selected_port: str | None = None
        self._port_actions: dict[str, QAction] = {}
        self._poll_interval_actions: dict[int, QAction] = {}
        self.setWindowTitle("Injector Tester")
        self.resize(1240, 820)
        self._build_menu_bar()

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(self._build_error_banner())
        layout.addWidget(self._build_top_panels())
        layout.addWidget(self._build_action_panel())
        layout.addWidget(self._build_status_and_log_splitter(), stretch=1)

        controller.state_changed.connect(self.render)
        self._refresh_ports()
        self.render(controller.state)
        self._refresh_advanced_calculation()

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        communications_menu = menu_bar.addMenu("Communications")
        self.port_menu = communications_menu.addMenu("Port")
        self.port_action_group = QActionGroup(self)
        self.port_action_group.setExclusive(True)

        self.refresh_ports_action = QAction("Refresh Ports", self)
        self.refresh_ports_action.triggered.connect(self._refresh_ports)

        communications_menu.addSeparator()
        self.read_status_action = QAction("Read Status", self)
        self.read_status_action.triggered.connect(self._controller.refresh_status)
        communications_menu.addAction(self.read_status_action)
        communications_menu.addSeparator()

        self.connect_action = QAction("Connect", self)
        self.connect_action.triggered.connect(self._connect_port)
        communications_menu.addAction(self.connect_action)

        self.disconnect_action = QAction("Disconnect", self)
        self.disconnect_action.triggered.connect(self._controller.disconnect_port)
        communications_menu.addAction(self.disconnect_action)

        status_menu = menu_bar.addMenu("Status")
        self.auto_poll_action = QAction("Auto-poll", self)
        self.auto_poll_action.setCheckable(True)
        self.auto_poll_action.setChecked(True)
        self.auto_poll_action.toggled.connect(self._sync_auto_poll_enabled)
        status_menu.addAction(self.auto_poll_action)

        self.poll_interval_menu = status_menu.addMenu("Poll Interval")
        self.poll_interval_group = QActionGroup(self)
        self.poll_interval_group.setExclusive(True)
        for label, interval_ms in (
            ("0.25 s", 250),
            ("0.5 s", 500),
            ("1.0 s", 1000),
            ("2.0 s", 2000),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(interval_ms)
            action.triggered.connect(self._sync_auto_poll_interval)
            self.poll_interval_group.addAction(action)
            self.poll_interval_menu.addAction(action)
            self._poll_interval_actions[interval_ms] = action
        if 1000 in self._poll_interval_actions:
            self._poll_interval_actions[1000].setChecked(True)

        help_menu = menu_bar.addMenu("Help")
        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(self.about_action)

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
        layout.addWidget(self._build_testing_panel(), stretch=3)
        layout.addWidget(self._build_channel_selection_panel(), stretch=2)
        return container

    def _build_testing_panel(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_basic_testing_tab(), "Basic Testing")
        tabs.addTab(self._build_advanced_testing_tab(), "Advanced Testing")
        return tabs

    def _build_basic_testing_tab(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)

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

        self.pulses_spin = QSpinBox()
        self.pulses_spin.setRange(1, 2_000_000_000)
        self.pulses_spin.setValue(100)

        layout.addRow("Model", self.model_combo)
        layout.addRow("RPM", self.rpm_spin)
        layout.addRow("Duty %", self.duty_spin)
        layout.addRow("Test Mode", self.test_mode_combo)
        layout.addRow("Pulse Count", self.pulses_spin)
        return page

    def _build_advanced_testing_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        inputs_group = QGroupBox("Calculated Test Setup")
        inputs_layout = QFormLayout(inputs_group)

        self.advanced_battery_voltage_spin = QDoubleSpinBox()
        self.advanced_battery_voltage_spin.setRange(0.0, 32.0)
        self.advanced_battery_voltage_spin.setDecimals(2)
        self.advanced_battery_voltage_spin.setSingleStep(0.1)
        self.advanced_battery_voltage_spin.setValue(13.8)
        self.advanced_battery_voltage_spin.valueChanged.connect(self._refresh_advanced_calculation)

        self.advanced_fuel_amount_spin = QDoubleSpinBox()
        self.advanced_fuel_amount_spin.setRange(0.0, 500.0)
        self.advanced_fuel_amount_spin.setDecimals(3)
        self.advanced_fuel_amount_spin.setSingleStep(0.1)
        self.advanced_fuel_amount_spin.setValue(5.0)
        self.advanced_fuel_amount_spin.valueChanged.connect(self._refresh_advanced_calculation)

        self.advanced_injector_lb_hr_spin = QDoubleSpinBox()
        self.advanced_injector_lb_hr_spin.setRange(0.0, 1000.0)
        self.advanced_injector_lb_hr_spin.setDecimals(2)
        self.advanced_injector_lb_hr_spin.setSingleStep(0.5)
        self.advanced_injector_lb_hr_spin.setValue(32.0)
        self.advanced_injector_lb_hr_spin.valueChanged.connect(self._sync_injector_size_from_lb_hr)

        self.advanced_injector_cc_min_spin = QDoubleSpinBox()
        self.advanced_injector_cc_min_spin.setRange(0.0, 10000.0)
        self.advanced_injector_cc_min_spin.setDecimals(2)
        self.advanced_injector_cc_min_spin.setSingleStep(1.0)
        self.advanced_injector_cc_min_spin.setValue(
            cc_per_min_from_lb_per_hour(self.advanced_injector_lb_hr_spin.value())
        )
        self.advanced_injector_cc_min_spin.valueChanged.connect(self._sync_injector_size_from_cc_min)

        self.advanced_rpm_spin = QDoubleSpinBox()
        self.advanced_rpm_spin.setRange(1.0, 50000.0)
        self.advanced_rpm_spin.setDecimals(1)
        self.advanced_rpm_spin.setValue(1000.0)
        self.advanced_rpm_spin.valueChanged.connect(self._refresh_advanced_calculation)

        self.advanced_duration_spin = QDoubleSpinBox()
        self.advanced_duration_spin.setRange(0.1, 3600.0)
        self.advanced_duration_spin.setDecimals(1)
        self.advanced_duration_spin.setSingleStep(0.5)
        self.advanced_duration_spin.setValue(30.0)
        self.advanced_duration_spin.valueChanged.connect(self._refresh_advanced_calculation)

        inputs_layout.addRow("Battery Voltage", self.advanced_battery_voltage_spin)
        inputs_layout.addRow("Fuel Amount / Injector (mL)", self.advanced_fuel_amount_spin)
        inputs_layout.addRow("Injector Size (lb/hr)", self.advanced_injector_lb_hr_spin)
        inputs_layout.addRow("Injector Size (cc/min)", self.advanced_injector_cc_min_spin)
        inputs_layout.addRow("RPM", self.advanced_rpm_spin)
        inputs_layout.addRow("Test Duration (s)", self.advanced_duration_spin)
        layout.addWidget(inputs_group)

        curve_group = QGroupBox("Deadtime Curve")
        curve_layout = QVBoxLayout(curve_group)
        self.deadtime_curve_table = QTableWidget(0, 2)
        self.deadtime_curve_table.setHorizontalHeaderLabels(["Voltage", "Deadtime (ms)"])
        self.deadtime_curve_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.deadtime_curve_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.deadtime_curve_table.itemChanged.connect(self._on_deadtime_curve_changed)
        curve_layout.addWidget(self.deadtime_curve_table)

        curve_button_row = QHBoxLayout()
        self.deadtime_add_row_button = QPushButton("Add Row")
        self.deadtime_remove_row_button = QPushButton("Remove Selected Row")
        self.deadtime_add_row_button.clicked.connect(self._handle_add_deadtime_curve_row)
        self.deadtime_remove_row_button.clicked.connect(self._remove_selected_deadtime_curve_row)
        curve_button_row.addWidget(self.deadtime_add_row_button)
        curve_button_row.addWidget(self.deadtime_remove_row_button)
        curve_button_row.addStretch(1)
        curve_layout.addLayout(curve_button_row)
        layout.addWidget(curve_group)

        outputs_group = QGroupBox("Computed Outputs")
        outputs_layout = QFormLayout(outputs_group)
        self.advanced_model_value_label = QLabel("0 - 4-stroke")
        self.advanced_deadtime_value_label = QLabel("0.000 ms")
        self.advanced_pulse_count_value_label = QLabel("0")
        self.advanced_effective_open_time_value_label = QLabel("0.000 ms")
        self.advanced_commanded_pw_value_label = QLabel("0.000 ms")
        self.advanced_duty_cycle_value_label = QLabel("0.00 %")
        self.advanced_validation_label = QLabel()
        self.advanced_validation_label.setWordWrap(True)
        self.advanced_validation_label.setTextFormat(Qt.TextFormat.PlainText)
        self.advanced_validation_label.setStyleSheet(
            "background-color: #f8fafc; border: 1px solid #cbd5e1; padding: 8px;"
        )

        outputs_layout.addRow("Derived Model", self.advanced_model_value_label)
        outputs_layout.addRow("Interpolated Deadtime", self.advanced_deadtime_value_label)
        outputs_layout.addRow("Pulse Count", self.advanced_pulse_count_value_label)
        outputs_layout.addRow("Effective Open / Pulse", self.advanced_effective_open_time_value_label)
        outputs_layout.addRow("Commanded Pulse Width", self.advanced_commanded_pw_value_label)
        outputs_layout.addRow("Duty Cycle", self.advanced_duty_cycle_value_label)
        outputs_layout.addRow("Validation / Warnings", self.advanced_validation_label)
        layout.addWidget(outputs_group)

        self.apply_advanced_button = QPushButton("Apply Advanced Calculation")
        self.apply_advanced_button.clicked.connect(self._apply_advanced_calculation)
        layout.addWidget(self.apply_advanced_button)

        layout.addStretch(1)
        self._populate_default_deadtime_curve()
        return page

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
        layout.addWidget(self.run_selected_button, 1, 0, 1, 2)
        layout.addWidget(self.read_status_button, 1, 2)
        layout.addWidget(self.help_button, 1, 3)
        layout.addWidget(self.stop_all_button, 2, 0, 1, 4)

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
        ports = self._controller.list_ports()
        if self._selected_port not in ports:
            self._selected_port = ports[0] if ports else None

        self.port_menu.clear()
        self._port_actions.clear()
        for action in self.port_action_group.actions():
            self.port_action_group.removeAction(action)

        for port in ports:
            action = QAction(port, self)
            action.setCheckable(True)
            action.setChecked(port == self._selected_port)
            action.triggered.connect(lambda checked, port_name=port: self._select_port(port_name, checked))
            self.port_action_group.addAction(action)
            self.port_menu.addAction(action)
            self._port_actions[port] = action

        if not ports:
            no_ports_action = QAction("No ports found", self)
            no_ports_action.setEnabled(False)
            self.port_menu.addAction(no_ports_action)

        self.port_menu.addSeparator()
        self.port_menu.addAction(self.refresh_ports_action)
        self.connect_action.setEnabled((not self._controller.state.connected) and bool(self._selected_port))

    def _connect_port(self) -> None:
        if not self._selected_port:
            self._controller.report_validation_error(
                "Select a serial port from Communications > Port before connecting"
            )
            return
        self._controller.connect_port(self._selected_port)

    def _select_port(self, port: str, checked: bool) -> None:
        if not checked:
            return
        self._selected_port = port
        self.connect_action.setEnabled((not self._controller.state.connected) and bool(self._selected_port))

    def _sync_test_mode(self) -> None:
        self._controller.set_test_mode(str(self.test_mode_combo.currentData()))

    def _sync_auto_poll_enabled(self, checked: bool) -> None:
        self._controller.set_auto_poll_enabled(checked)

    def _sync_auto_poll_interval(self) -> None:
        action = self.sender()
        if not isinstance(action, QAction):
            return
        interval_ms = int(action.data())
        self._controller.set_auto_poll_interval_ms(interval_ms)

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About Injector Tester",
            "Injector Tester\n\n"
            "PySide6 desktop client for the injector test bench.\n"
            "Supports basic counted testing, advanced test setup, live status reads, and serial log inspection.",
        )

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

    def _populate_default_deadtime_curve(self) -> None:
        self._updating_deadtime_table = True
        self.deadtime_curve_table.setRowCount(0)
        for point in default_deadtime_curve():
            self._add_deadtime_curve_row(point.voltage, point.deadtime_ms)
        self._updating_deadtime_table = False

    def _add_deadtime_curve_row(
        self,
        voltage: float | None = None,
        deadtime_ms: float | None = None,
    ) -> None:
        row = self.deadtime_curve_table.rowCount()
        self.deadtime_curve_table.insertRow(row)
        self.deadtime_curve_table.setItem(
            row,
            0,
            QTableWidgetItem("" if voltage is None else f"{voltage:.2f}"),
        )
        self.deadtime_curve_table.setItem(
            row,
            1,
            QTableWidgetItem("" if deadtime_ms is None else f"{deadtime_ms:.3f}"),
        )
        if not self._updating_deadtime_table:
            self._refresh_advanced_calculation()

    def _handle_add_deadtime_curve_row(self, *_args: object) -> None:
        self._add_deadtime_curve_row()

    def _remove_selected_deadtime_curve_row(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self.deadtime_curve_table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not selected_rows and self.deadtime_curve_table.rowCount() > 0:
            selected_rows = [self.deadtime_curve_table.rowCount() - 1]
        for row in selected_rows:
            self.deadtime_curve_table.removeRow(row)
        self._refresh_advanced_calculation()

    def _on_deadtime_curve_changed(self, *_args: object) -> None:
        if self._updating_deadtime_table:
            return
        self._refresh_advanced_calculation()

    def _sync_injector_size_from_lb_hr(self, *_args: object) -> None:
        if self._syncing_injector_size:
            return
        # Guard against recursive updates while keeping both units editable.
        self._syncing_injector_size = True
        self.advanced_injector_cc_min_spin.setValue(
            cc_per_min_from_lb_per_hour(self.advanced_injector_lb_hr_spin.value())
        )
        self._syncing_injector_size = False
        self._refresh_advanced_calculation()

    def _sync_injector_size_from_cc_min(self, *_args: object) -> None:
        if self._syncing_injector_size:
            return
        self._syncing_injector_size = True
        self.advanced_injector_lb_hr_spin.setValue(
            lb_per_hour_from_cc_per_min(self.advanced_injector_cc_min_spin.value())
        )
        self._syncing_injector_size = False
        self._refresh_advanced_calculation()

    def _read_deadtime_curve(self) -> tuple[tuple[DeadtimePoint, ...], tuple[str, ...]]:
        curve: list[DeadtimePoint] = []
        errors: list[str] = []
        for row in range(self.deadtime_curve_table.rowCount()):
            voltage_item = self.deadtime_curve_table.item(row, 0)
            deadtime_item = self.deadtime_curve_table.item(row, 1)
            voltage_text = "" if voltage_item is None else voltage_item.text().strip()
            deadtime_text = "" if deadtime_item is None else deadtime_item.text().strip()
            if not voltage_text and not deadtime_text:
                continue
            if not voltage_text or not deadtime_text:
                errors.append(f"Deadtime curve row {row + 1} requires both voltage and deadtime.")
                continue
            try:
                voltage = float(voltage_text)
                deadtime_ms = float(deadtime_text)
            except ValueError:
                errors.append(f"Deadtime curve row {row + 1} must contain numeric values.")
                continue
            curve.append(DeadtimePoint(voltage=voltage, deadtime_ms=deadtime_ms))
        return tuple(curve), tuple(errors)

    def _refresh_advanced_calculation(self, *_args: object) -> None:
        curve, table_errors = self._read_deadtime_curve()
        result = calculate_advanced_test(
            AdvancedTestInputs(
                battery_voltage=self.advanced_battery_voltage_spin.value(),
                desired_fuel_ml=self.advanced_fuel_amount_spin.value(),
                injector_size_cc_per_min=self.advanced_injector_cc_min_spin.value(),
                rpm=self.advanced_rpm_spin.value(),
                duration_seconds=self.advanced_duration_spin.value(),
                deadtime_curve=curve,
            )
        )

        if table_errors:
            result = AdvancedCalculationResult(
                model=result.model,
                input_voltage=result.input_voltage,
                applied_voltage=result.applied_voltage,
                voltage_was_clamped=result.voltage_was_clamped,
                raw_pulse_count=result.raw_pulse_count,
                pulse_count=result.pulse_count,
                cycle_time_ms=result.cycle_time_ms,
                interpolated_deadtime_ms=result.interpolated_deadtime_ms,
                effective_open_time_ms=result.effective_open_time_ms,
                commanded_pulse_width_ms=result.commanded_pulse_width_ms,
                duty_cycle_percent=result.duty_cycle_percent,
                warnings=result.warnings,
                errors=(*table_errors, *result.errors),
            )

        self._latest_advanced_result = result
        self.advanced_model_value_label.setText("0 - 4-stroke")
        self.advanced_deadtime_value_label.setText(
            f"{result.interpolated_deadtime_ms:.3f} ms @ {result.applied_voltage:.2f} V"
        )
        self.advanced_pulse_count_value_label.setText(
            f"{result.pulse_count} ({result.raw_pulse_count:.3f} exact)"
        )
        self.advanced_effective_open_time_value_label.setText(
            f"{result.effective_open_time_ms:.3f} ms"
        )
        self.advanced_commanded_pw_value_label.setText(
            f"{result.commanded_pulse_width_ms:.3f} ms"
        )
        self.advanced_duty_cycle_value_label.setText(f"{result.duty_cycle_percent:.2f} %")
        self.apply_advanced_button.setEnabled(result.is_valid)
        self._render_advanced_messages(result)

    def _render_advanced_messages(self, result: AdvancedCalculationResult) -> None:
        messages: list[str] = []
        if result.errors:
            messages.extend(f"Block: {message}" for message in result.errors)
        if result.warnings:
            messages.extend(f"Warn: {message}" for message in result.warnings)
        if not messages:
            messages.append("Ready to apply. Advanced mode derives a 4-stroke test model.")
        self.advanced_validation_label.setText("\n".join(messages))
        if result.errors:
            self.advanced_validation_label.setStyleSheet(
                "background-color: #fff1f0; color: #9f1239; border: 2px solid #dc2626; padding: 8px;"
            )
            return
        if result.warnings:
            self.advanced_validation_label.setStyleSheet(
                "background-color: #fffbeb; color: #92400e; border: 2px solid #f59e0b; padding: 8px;"
            )
            return
        self.advanced_validation_label.setStyleSheet(
            "background-color: #ecfdf5; color: #166534; border: 2px solid #22c55e; padding: 8px;"
        )

    def _apply_advanced_calculation(self) -> None:
        self._refresh_advanced_calculation()
        result = self._latest_advanced_result
        if result is None or not result.is_valid:
            return

        # Advanced mode always targets the existing 4-stroke firmware model.
        model_index = self.model_combo.findData(ADVANCED_TEST_MODEL)
        if model_index >= 0:
            self.model_combo.setCurrentIndex(model_index)
        self.rpm_spin.setValue(self.advanced_rpm_spin.value())
        self.duty_spin.setValue(result.duty_cycle_percent)
        self.pulses_spin.setValue(result.pulse_count)

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
        self.statusBar().showMessage(state.status_message)
        if state.connection_port:
            self._selected_port = state.connection_port
        for port, action in self._port_actions.items():
            action.blockSignals(True)
            action.setChecked(port == self._selected_port)
            action.blockSignals(False)
        self.connect_action.setEnabled((not state.connected) and bool(self._selected_port))
        self.disconnect_action.setEnabled(state.connected)
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

        interval_action = self._poll_interval_actions.get(state.auto_poll_interval_ms)
        if interval_action is not None and not interval_action.isChecked():
            interval_action.blockSignals(True)
            interval_action.setChecked(True)
            interval_action.blockSignals(False)

        if self.auto_poll_action.isChecked() != state.auto_poll_enabled:
            self.auto_poll_action.blockSignals(True)
            self.auto_poll_action.setChecked(state.auto_poll_enabled)
            self.auto_poll_action.blockSignals(False)

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
