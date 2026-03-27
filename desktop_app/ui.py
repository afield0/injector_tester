from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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
    QStackedWidget,
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
from .injector_profile import InjectorProfile, dump_injector_profile, load_injector_profile
from .state import AppController, AppState


class DeadtimeGraphWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._curve: tuple[DeadtimePoint, ...] = ()
        self._marker_point: DeadtimePoint | None = None
        self.setMinimumHeight(220)

    def set_curve(self, curve: tuple[DeadtimePoint, ...]) -> None:
        self._curve = tuple(sorted(curve, key=lambda point: point.voltage))
        self.update()

    def set_marker_point(self, point: DeadtimePoint | None) -> None:
        self._marker_point = point
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f8fafc"))

        plot_rect = self.rect().adjusted(52, 16, -20, -34)
        if plot_rect.width() <= 0 or plot_rect.height() <= 0:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawRect(plot_rect)

        if len(self._curve) < 1:
            painter.setPen(QColor("#64748b"))
            painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "Add deadtime points to display the curve")
            return

        min_voltage = min(point.voltage for point in self._curve)
        max_voltage = max(point.voltage for point in self._curve)
        min_deadtime = min(point.deadtime_ms for point in self._curve)
        max_deadtime = max(point.deadtime_ms for point in self._curve)

        if min_voltage == max_voltage:
            min_voltage -= 1.0
            max_voltage += 1.0
        if min_deadtime == max_deadtime:
            min_deadtime -= 0.1
            max_deadtime += 0.1

        def map_point(point: DeadtimePoint) -> QPointF:
            x_ratio = (point.voltage - min_voltage) / (max_voltage - min_voltage)
            y_ratio = (point.deadtime_ms - min_deadtime) / (max_deadtime - min_deadtime)
            return QPointF(
                plot_rect.left() + (x_ratio * plot_rect.width()),
                plot_rect.bottom() - (y_ratio * plot_rect.height()),
            )

        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        for step in range(1, 4):
            x = plot_rect.left() + int((step / 4) * plot_rect.width())
            y = plot_rect.top() + int((step / 4) * plot_rect.height())
            painter.drawLine(x, plot_rect.top(), x, plot_rect.bottom())
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)

        painter.setPen(QColor("#475569"))
        painter.drawText(QRectF(0, plot_rect.bottom() - 10, plot_rect.left() - 8, 20), Qt.AlignmentFlag.AlignRight, f"{min_deadtime:.2f} ms")
        painter.drawText(QRectF(0, plot_rect.top() - 10, plot_rect.left() - 8, 20), Qt.AlignmentFlag.AlignRight, f"{max_deadtime:.2f} ms")
        painter.drawText(QRectF(plot_rect.left() - 20, plot_rect.bottom() + 8, 60, 20), Qt.AlignmentFlag.AlignLeft, f"{min_voltage:.1f} V")
        painter.drawText(QRectF(plot_rect.right() - 40, plot_rect.bottom() + 8, 60, 20), Qt.AlignmentFlag.AlignRight, f"{max_voltage:.1f} V")

        points = [map_point(point) for point in self._curve]
        painter.setPen(QPen(QColor("#0f766e"), 2))
        for start, end in zip(points, points[1:]):
            painter.drawLine(start, end)

        painter.setBrush(QColor("#0f766e"))
        for point in points:
            painter.drawEllipse(point, 4, 4)

        if self._marker_point is not None:
            marker = map_point(self._marker_point)
            painter.setPen(QPen(QColor("#b91c1c"), 2))
            painter.drawLine(marker.x() - 6, marker.y() - 6, marker.x() + 6, marker.y() + 6)
            painter.drawLine(marker.x() - 6, marker.y() + 6, marker.x() + 6, marker.y() - 6)


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller
        self._syncing_injector_size = False
        self._updating_deadtime_table = False
        self._latest_advanced_result: AdvancedCalculationResult | None = None
        self._selected_port: str | None = None
        self._last_displayed_error: str = ""
        self.setWindowTitle("Injector Tester")
        self.resize(1240, 820)
        self._build_menu_bar()
        self._build_serial_log_window()
        self._build_error_log_window()
        self._build_deadtime_window()

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.addWidget(self._build_wizard_flow(), stretch=1)

        controller.state_changed.connect(self.render)
        self._refresh_ports()
        self.render(controller.state)
        self._refresh_advanced_calculation()

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        self.load_injector_data_action = QAction("Load Injector Data...", self)
        self.load_injector_data_action.triggered.connect(self._load_injector_data)
        file_menu.addAction(self.load_injector_data_action)
        self.save_injector_data_action = QAction("Save Injector Data...", self)
        self.save_injector_data_action.triggered.connect(self._save_injector_data)
        file_menu.addAction(self.save_injector_data_action)
        file_menu.addSeparator()
        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        view_menu = menu_bar.addMenu("View")
        self.show_serial_log_action = QAction("Serial Log", self)
        self.show_serial_log_action.triggered.connect(self._show_serial_log_window)
        view_menu.addAction(self.show_serial_log_action)
        self.show_error_log_action = QAction("Error Log", self)
        self.show_error_log_action.triggered.connect(self._show_error_log_window)
        view_menu.addAction(self.show_error_log_action)

        help_menu = menu_bar.addMenu("Help")
        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(self.about_action)

    def _build_serial_log_window(self) -> None:
        self.serial_log_window = QWidget(self, Qt.WindowType.Window)
        self.serial_log_window.setWindowTitle("Serial Log")
        self.serial_log_window.resize(900, 360)
        layout = QVBoxLayout(self.serial_log_window)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def _build_deadtime_window(self) -> None:
        self.deadtime_window = QWidget(self, Qt.WindowType.Window)
        self.deadtime_window.setWindowTitle("Deadtime Configuration")
        self.deadtime_window.resize(860, 620)
        layout = QVBoxLayout(self.deadtime_window)

        self.deadtime_graph = DeadtimeGraphWidget(self.deadtime_window)
        layout.addWidget(self.deadtime_graph)

        self.deadtime_curve_table = QTableWidget(0, 2)
        self.deadtime_curve_table.setHorizontalHeaderLabels(["Voltage", "Deadtime (ms)"])
        self.deadtime_curve_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.deadtime_curve_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.deadtime_curve_table.itemChanged.connect(self._on_deadtime_curve_changed)
        layout.addWidget(self.deadtime_curve_table)

        curve_button_row = QHBoxLayout()
        self.deadtime_add_row_button = QPushButton("Add Row")
        self.deadtime_remove_row_button = QPushButton("Remove Selected Row")
        self.deadtime_move_up_button = QPushButton("Move Up")
        self.deadtime_move_down_button = QPushButton("Move Down")
        self.deadtime_add_row_button.clicked.connect(self._handle_add_deadtime_curve_row)
        self.deadtime_remove_row_button.clicked.connect(self._remove_selected_deadtime_curve_row)
        self.deadtime_move_up_button.clicked.connect(self._move_selected_deadtime_curve_row_up)
        self.deadtime_move_down_button.clicked.connect(self._move_selected_deadtime_curve_row_down)
        curve_button_row.addWidget(self.deadtime_add_row_button)
        curve_button_row.addWidget(self.deadtime_remove_row_button)
        curve_button_row.addWidget(self.deadtime_move_up_button)
        curve_button_row.addWidget(self.deadtime_move_down_button)
        curve_button_row.addStretch(1)
        layout.addLayout(curve_button_row)

    def _build_error_log_window(self) -> None:
        self.error_log_window = QWidget(self, Qt.WindowType.Window)
        self.error_log_window.setWindowTitle("Error Log")
        self.error_log_window.resize(760, 320)
        layout = QVBoxLayout(self.error_log_window)
        self.error_log_text = QPlainTextEdit()
        self.error_log_text.setReadOnly(True)
        layout.addWidget(self.error_log_text)

    def _build_wizard_flow(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.wizard_stack = QStackedWidget()
        self.wizard_stack.addWidget(self._build_connection_page())
        self.wizard_stack.addWidget(self._build_test_type_page())
        self.wizard_stack.addWidget(self._build_execute_page())
        self.wizard_stack.currentChanged.connect(lambda *_args: self._update_wizard_navigation())
        layout.addWidget(self.wizard_stack, stretch=1)

        nav_row = QHBoxLayout()
        self.wizard_page_label = QLabel()
        self.wizard_page_label.setTextFormat(Qt.TextFormat.PlainText)
        self.wizard_back_button = QPushButton("Back")
        self.wizard_next_button = QPushButton("Next")
        self.wizard_back_button.clicked.connect(self._controller.go_previous_step)
        self.wizard_next_button.clicked.connect(self._controller.go_next_step)
        nav_row.addWidget(self.wizard_page_label)
        nav_row.addStretch(1)
        nav_row.addWidget(self.wizard_back_button)
        nav_row.addWidget(self.wizard_next_button)
        layout.addLayout(nav_row)
        self._update_wizard_navigation()
        return container

    def _build_connection_page(self) -> QWidget:
        page = QWidget()
        layout = QFormLayout(page)

        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.currentIndexChanged.connect(self._on_selected_port_changed)
        self.refresh_ports_button = QPushButton("Refresh")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        port_row.addWidget(self.port_combo, stretch=1)
        port_row.addWidget(self.refresh_ports_button)

        connect_row = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect_port)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self._controller.disconnect_port)
        self.verify_status_button = QPushButton("Verify Connection")
        self.verify_status_button.clicked.connect(self._controller.verify_connection)
        connect_row.addWidget(self.connect_button)
        connect_row.addWidget(self.disconnect_button)
        connect_row.addWidget(self.verify_status_button)

        self.verification_status_label = QLabel("Connection not verified")
        self.verification_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.firmware_version_label = QLabel("Unknown")
        self.firmware_version_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addRow("Port", port_row)
        layout.addRow("Connection", connect_row)
        layout.addRow("Verification", self.verification_status_label)
        layout.addRow("Firmware", self.firmware_version_label)
        layout.addRow(QLabel("Use Next only after connecting and verifying communication."))
        return page

    def _build_test_type_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Mode"))
        self.test_type_combo = QComboBox()
        self.test_type_combo.addItem("Select a mode...", None)
        self.test_type_combo.addItem("Simple", "simple")
        self.test_type_combo.addItem("Advanced", "advanced")
        self.test_type_combo.currentIndexChanged.connect(self._sync_test_type_page)
        selector_row.addWidget(self.test_type_combo, stretch=1)
        layout.addLayout(selector_row)

        self.test_type_stack = QStackedWidget()
        self.test_type_prompt = QLabel("Choose Simple or Advanced to configure the test.")
        self.test_type_prompt.setWordWrap(True)
        self.test_type_prompt.setTextFormat(Qt.TextFormat.PlainText)
        self.test_type_stack.addWidget(self.test_type_prompt)
        self.test_type_stack.addWidget(self._build_basic_testing_tab())
        self.test_type_stack.addWidget(self._build_advanced_testing_tab())
        layout.addWidget(self.test_type_stack, stretch=2)
        layout.addWidget(self._build_channel_selection_panel(), stretch=1)
        self._update_test_type_page_ui(None)
        return page

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

        deadtime_group = QGroupBox("Deadtime Configuration")
        deadtime_layout = QVBoxLayout(deadtime_group)
        injector_data_row = QHBoxLayout()
        self.load_injector_data_button = QPushButton("Load Injector Data")
        self.save_injector_data_button = QPushButton("Save Injector Data")
        self.load_injector_data_button.clicked.connect(self._load_injector_data)
        self.save_injector_data_button.clicked.connect(self._save_injector_data)
        injector_data_row.addWidget(self.load_injector_data_button)
        injector_data_row.addWidget(self.save_injector_data_button)
        self.deadtime_window_button = QPushButton("Open Deadtime Configuration")
        self.deadtime_window_button.clicked.connect(self._show_deadtime_window)
        self.deadtime_curve_summary_label = QLabel("Interpolated Deadtime: 0.000 ms")
        self.deadtime_curve_summary_label.setWordWrap(True)
        self.deadtime_curve_summary_label.setTextFormat(Qt.TextFormat.PlainText)
        deadtime_layout.addLayout(injector_data_row)
        deadtime_layout.addWidget(self.deadtime_window_button)
        deadtime_layout.addWidget(self.deadtime_curve_summary_label)
        layout.addWidget(deadtime_group)

        outputs_group = QGroupBox("Computed Outputs")
        outputs_layout = QFormLayout(outputs_group)
        self.advanced_model_value_label = QLabel("0 - 4-stroke")
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

        test_mode_row = QHBoxLayout()
        test_mode_row.addWidget(QLabel("Test Mode"))
        test_mode_row.addWidget(self.test_mode_combo, stretch=1)
        layout.addLayout(test_mode_row)

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

        self.start_test_button = QPushButton("Start Test")
        self.cancel_test_button = QPushButton("Cancel")
        self.cancel_test_button.setStyleSheet(
            "background-color: #b91c1c; color: white; font-weight: 700; min-height: 44px;"
        )
        self.start_test_button.setToolTip(
            "Apply the current mode-specific configuration to the checked channels, then start the selected test."
        )
        self.cancel_test_button.setToolTip(
            "Stop all channels immediately."
        )

        self.start_test_button.clicked.connect(self._run_selected)
        self.cancel_test_button.clicked.connect(self._controller.stop_all)

        self.auto_poll_checkbox = QCheckBox("Auto-poll status")
        self.auto_poll_checkbox.setChecked(True)
        self.auto_poll_checkbox.toggled.connect(self._sync_auto_poll_enabled)
        self.poll_interval_combo = QComboBox()
        for label, interval_ms in (
            ("0.25 s", 250),
            ("0.5 s", 500),
            ("1.0 s", 1000),
            ("2.0 s", 2000),
        ):
            self.poll_interval_combo.addItem(label, interval_ms)
        default_interval_index = self.poll_interval_combo.findData(1000)
        if default_interval_index >= 0:
            self.poll_interval_combo.setCurrentIndex(default_interval_index)
        self.poll_interval_combo.currentIndexChanged.connect(self._sync_auto_poll_interval)
        poll_row = QHBoxLayout()
        poll_row.addWidget(QLabel("Poll Interval"))
        poll_row.addWidget(self.poll_interval_combo, stretch=1)

        layout.addWidget(self.selected_action_mode_label, 0, 0, 1, 2)
        layout.addWidget(self.start_test_button, 1, 0)
        layout.addWidget(self.cancel_test_button, 1, 1)
        layout.addWidget(self.auto_poll_checkbox, 2, 0, 1, 2)
        layout.addLayout(poll_row, 3, 0, 1, 2)

        return group

    def _build_execute_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_action_panel())
        layout.addWidget(self._build_status_table_group(), stretch=1)
        return page

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

    def _refresh_ports(self) -> None:
        ports = self._controller.list_ports()
        if self._selected_port not in ports:
            self._selected_port = ports[0] if ports else None

        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        if ports:
            for port in ports:
                self.port_combo.addItem(port, port)
            selected_index = self.port_combo.findData(self._selected_port)
            if selected_index >= 0:
                self.port_combo.setCurrentIndex(selected_index)
        else:
            self.port_combo.addItem("No ports found", None)
            self.port_combo.setCurrentIndex(0)
        self.port_combo.blockSignals(False)
        self._update_connection_controls(self._controller.state.connected)

    def _connect_port(self) -> None:
        if not self._selected_port:
            self._controller.report_validation_error(
                "Select a serial port from the Port picker before connecting."
            )
            return
        self._controller.connect_port(self._selected_port)

    def _on_selected_port_changed(self, *_args: object) -> None:
        self._selected_port = self.port_combo.currentData()
        self._update_connection_controls(self._controller.state.connected)

    def _update_connection_controls(self, connected: bool) -> None:
        has_port = bool(self._selected_port)
        self.connect_button.setEnabled((not connected) and has_port)
        self.disconnect_button.setEnabled(connected)
        self.verify_status_button.setEnabled(connected)

    def _sync_test_mode(self) -> None:
        self._controller.set_test_mode(str(self.test_mode_combo.currentData()))

    def _sync_test_type_page(self) -> None:
        mode = self.test_type_combo.currentData()
        self._update_test_type_page_ui(mode)
        self._controller.set_wizard_test_kind(mode)

    def _update_test_type_page_ui(self, mode: str | None) -> None:
        if mode == "simple":
            self.test_type_stack.setCurrentIndex(1)
            if hasattr(self, "start_test_button"):
                self.start_test_button.setText("Start Test")
                self.start_test_button.setToolTip(
                    "Apply the simple test model, RPM, duty, and pulse count to the checked channels, then run them using the selected test mode."
                )
            return
        if mode == "advanced":
            self.test_type_stack.setCurrentIndex(2)
            if hasattr(self, "start_test_button"):
                self.start_test_button.setText("Start Test")
                self.start_test_button.setToolTip(
                    "Apply the advanced calculated RPM, duty, and pulse count to the checked channels, then run them using the selected test mode."
                )
            return
        self.test_type_stack.setCurrentIndex(0)
        if hasattr(self, "start_test_button"):
            self.start_test_button.setText("Start Test")
            self.start_test_button.setToolTip(
                "Choose Simple or Advanced on step 2 before starting the selected channels."
            )

    def _show_previous_wizard_page(self) -> None:
        self._controller.go_previous_step()

    def _show_next_wizard_page(self) -> None:
        self._controller.go_next_step()

    def _update_wizard_navigation(self) -> None:
        state = self._controller.state
        index = state.wizard_step
        total = self.wizard_stack.count()
        self.wizard_page_label.setText(
            f"Step {index + 1} of {total}: "
            + (
                "Connection"
                if index == 0
                else "Test Type"
                if index == 1
                else "Execute"
            )
        )
        test_active = state.test_progress.active
        self.wizard_back_button.setEnabled((index > 0) and state.can_navigate_back)
        can_advance = index < total - 1
        if index == 0:
            can_advance = (
                can_advance
                and state.connected
                and state.connection_verified
            )
        elif index == 1:
            can_advance = can_advance and (state.selected_test_kind is not None)
        self.wizard_next_button.setEnabled(can_advance and (not test_active))

    def _sync_auto_poll_enabled(self, checked: bool) -> None:
        self._controller.set_auto_poll_enabled(checked)

    def _sync_auto_poll_interval(self, *_args: object) -> None:
        interval_ms = self.poll_interval_combo.currentData()
        if interval_ms is None:
            return
        self._controller.set_auto_poll_interval_ms(int(interval_ms))

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About Injector Tester",
            "Injector Tester\n\n"
            "PySide6 desktop client for the injector test bench.\n"
            "Supports basic counted testing, advanced test setup, live status reads, and serial log inspection.",
        )

    def _show_serial_log_window(self) -> None:
        self.serial_log_window.show()
        self.serial_log_window.raise_()
        self.serial_log_window.activateWindow()

    def _show_error_log_window(self) -> None:
        self.error_log_window.show()
        self.error_log_window.raise_()
        self.error_log_window.activateWindow()

    def _show_deadtime_window(self) -> None:
        self.deadtime_window.show()
        self.deadtime_window.raise_()
        self.deadtime_window.activateWindow()

    def _load_injector_data(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Injector Data",
            "",
            "Injector Data (*.inj *.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as handle:
                profile = load_injector_profile(handle.read())
        except OSError as exc:
            self._controller.report_validation_error(f"Failed to read injector data: {exc}")
            return
        except ValueError as exc:
            self._controller.report_validation_error(f"Invalid injector data: {exc}")
            return

        self._apply_injector_profile(profile)
        self.statusBar().showMessage(f"Loaded injector data from {path}")

    def _save_injector_data(self) -> None:
        curve, errors = self._read_deadtime_curve()
        if errors:
            self._controller.report_validation_error(
                "Cannot save injector data until deadtime curve errors are resolved."
            )
            return
        if not curve:
            self._controller.report_validation_error(
                "Cannot save injector data without at least one deadtime curve row."
            )
            return

        profile = InjectorProfile(
            injector_lb_per_hour=self.advanced_injector_lb_hr_spin.value(),
            injector_cc_per_min=self.advanced_injector_cc_min_spin.value(),
            deadtime_curve=curve,
        )

        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Injector Data",
            "injector_data.inj",
            "Injector Data (*.inj *.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(dump_injector_profile(profile))
        except OSError as exc:
            self._controller.report_validation_error(f"Failed to save injector data: {exc}")
            return

        self.statusBar().showMessage(f"Saved injector data to {path}")

    def _record_error(self, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing_text = self.error_log_text.toPlainText()
        entry = f"[{timestamp}] {message}"
        self.error_log_text.setPlainText(f"{existing_text}\n{entry}".strip())
        self.error_log_text.verticalScrollBar().setValue(self.error_log_text.verticalScrollBar().maximum())

    def _validate_run_config(self) -> bool:
        wizard_test_kind = self._controller.state.wizard_test_kind
        if wizard_test_kind is None:
            self._controller.report_validation_error(
                "Choose Simple or Advanced on step 2 before starting a test."
            )
            return False
        if wizard_test_kind == "advanced":
            self._refresh_advanced_calculation()
            result = self._latest_advanced_result
            if result is None or not result.is_valid:
                self._controller.report_validation_error(
                    "Advanced mode configuration is incomplete. Resolve highlighted issues before running."
                )
                return False
            return True

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
        wizard_test_kind = self._controller.state.wizard_test_kind
        if wizard_test_kind == "advanced":
            result = self._latest_advanced_result
            if result is None:
                self._controller.report_validation_error("Advanced mode did not produce a calculation result.")
                return
            self._controller.run_selected_test(
                ADVANCED_TEST_MODEL,
                self.advanced_rpm_spin.value(),
                result.duty_cycle_percent,
                result.pulse_count,
            )
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
        self._update_deadtime_curve_views()

    def _apply_injector_profile(self, profile: InjectorProfile) -> None:
        self._syncing_injector_size = True
        self.advanced_injector_lb_hr_spin.setValue(profile.injector_lb_per_hour)
        self.advanced_injector_cc_min_spin.setValue(profile.injector_cc_per_min)
        self._syncing_injector_size = False

        self._updating_deadtime_table = True
        self.deadtime_curve_table.setRowCount(0)
        for point in profile.deadtime_curve:
            self._add_deadtime_curve_row(point.voltage, point.deadtime_ms)
        self._updating_deadtime_table = False
        self._update_deadtime_curve_views()
        self._refresh_advanced_calculation()

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
            self._update_deadtime_curve_views()
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
        self._update_deadtime_curve_views()
        self._refresh_advanced_calculation()

    def _move_selected_deadtime_curve_row_up(self) -> None:
        self._move_selected_deadtime_curve_row(-1)

    def _move_selected_deadtime_curve_row_down(self) -> None:
        self._move_selected_deadtime_curve_row(1)

    def _move_selected_deadtime_curve_row(self, direction: int) -> None:
        selected_rows = sorted(
            {index.row() for index in self.deadtime_curve_table.selectionModel().selectedRows()}
        )
        if len(selected_rows) != 1:
            return

        source_row = selected_rows[0]
        target_row = source_row + direction
        if target_row < 0 or target_row >= self.deadtime_curve_table.rowCount():
            return

        row_values = []
        for row in (source_row, target_row):
            values: list[str] = []
            for column in range(self.deadtime_curve_table.columnCount()):
                item = self.deadtime_curve_table.item(row, column)
                values.append("" if item is None else item.text())
            row_values.append(values)

        self._updating_deadtime_table = True
        for column, value in enumerate(row_values[1]):
            self.deadtime_curve_table.setItem(source_row, column, QTableWidgetItem(value))
        for column, value in enumerate(row_values[0]):
            self.deadtime_curve_table.setItem(target_row, column, QTableWidgetItem(value))
        self._updating_deadtime_table = False

        self.deadtime_curve_table.clearSelection()
        self.deadtime_curve_table.selectRow(target_row)
        self._update_deadtime_curve_views()
        self._refresh_advanced_calculation()

    def _on_deadtime_curve_changed(self, *_args: object) -> None:
        if self._updating_deadtime_table:
            return
        self._update_deadtime_curve_views()
        self._refresh_advanced_calculation()

    def _update_deadtime_curve_views(self) -> None:
        curve, errors = self._read_deadtime_curve()
        self.deadtime_graph.set_curve(curve)
        self.deadtime_graph.set_marker_point(None)
        if errors:
            self.deadtime_curve_summary_label.setText("\n".join(errors))
            return
        if not curve:
            self.deadtime_curve_summary_label.setText("Interpolated Deadtime: 0.000 ms")

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
        self.deadtime_curve_summary_label.setText(
            f"Interpolated Deadtime: {result.interpolated_deadtime_ms:.3f} ms @ {result.applied_voltage:.2f} V"
        )
        if not table_errors and curve:
            self.deadtime_graph.set_marker_point(
                DeadtimePoint(
                    voltage=result.applied_voltage,
                    deadtime_ms=result.interpolated_deadtime_ms,
                )
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
        if self.wizard_stack.currentIndex() != state.wizard_step:
            self.wizard_stack.blockSignals(True)
            self.wizard_stack.setCurrentIndex(state.wizard_step)
            self.wizard_stack.blockSignals(False)
        if state.connection_port:
            self._selected_port = state.connection_port
        selected_port_index = self.port_combo.findData(self._selected_port)
        if selected_port_index >= 0 and self.port_combo.currentIndex() != selected_port_index:
            self.port_combo.blockSignals(True)
            self.port_combo.setCurrentIndex(selected_port_index)
            self.port_combo.blockSignals(False)
        self._update_connection_controls(state.connected)
        self.verification_status_label.setText(state.verification_message)
        self.firmware_version_label.setText(
            f"{state.firmware_version} (expected {state.expected_firmware_version})"
        )
        self._update_wizard_navigation()
        self.selected_action_mode_label.setText(state.selected_action_mode_label)
        if state.has_error:
            if state.last_error_message != self._last_displayed_error:
                self._record_error(state.last_error_message)
                QMessageBox.critical(self, "Injector Tester Error", state.last_error_message)
                self._last_displayed_error = state.last_error_message
        else:
            self._last_displayed_error = ""
        self.status_summary_label.setText(
            "FW "
            f"{state.firmware_version} | "
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

        interval_index = self.poll_interval_combo.findData(state.auto_poll_interval_ms)
        if interval_index >= 0 and self.poll_interval_combo.currentIndex() != interval_index:
            self.poll_interval_combo.blockSignals(True)
            self.poll_interval_combo.setCurrentIndex(interval_index)
            self.poll_interval_combo.blockSignals(False)

        if self.auto_poll_checkbox.isChecked() != state.auto_poll_enabled:
            self.auto_poll_checkbox.blockSignals(True)
            self.auto_poll_checkbox.setChecked(state.auto_poll_enabled)
            self.auto_poll_checkbox.blockSignals(False)

        test_mode_index = self.test_mode_combo.findData(state.test_mode)
        if test_mode_index >= 0 and self.test_mode_combo.currentIndex() != test_mode_index:
            self.test_mode_combo.blockSignals(True)
            self.test_mode_combo.setCurrentIndex(test_mode_index)
            self.test_mode_combo.blockSignals(False)

        test_kind_index = self.test_type_combo.findData(state.selected_test_kind)
        if test_kind_index >= 0 and self.test_type_combo.currentIndex() != test_kind_index:
            self.test_type_combo.blockSignals(True)
            self.test_type_combo.setCurrentIndex(test_kind_index)
            self.test_type_combo.blockSignals(False)
        elif test_kind_index < 0 and self.test_type_combo.currentIndex() != 0:
            self.test_type_combo.blockSignals(True)
            self.test_type_combo.setCurrentIndex(0)
            self.test_type_combo.blockSignals(False)
        self._update_test_type_page_ui(state.selected_test_kind)

        self.progress_label.setText(state.test_progress.label)
        self.progress_bar.setRange(state.test_progress.minimum, state.test_progress.maximum)
        if state.test_progress.maximum > 0:
            self.progress_bar.setValue(state.test_progress.value)
        else:
            self.progress_bar.setValue(0)

        any_selected = bool(state.selected_mask)
        test_active = state.test_progress.active
        self.start_test_button.setEnabled(
            any_selected and (state.selected_test_kind is not None) and (not test_active)
        )
        self.cancel_test_button.setEnabled(test_active)
        self.wizard_back_button.setEnabled((state.wizard_step > 0) and state.can_navigate_back)
        self.wizard_next_button.setEnabled(self.wizard_next_button.isEnabled() and (not test_active))
        self.test_type_combo.setEnabled(not test_active)

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
