from __future__ import annotations

import os
import sys
from typing import Any, Callable

try:
    from PySide6.QtCore import QObject, QThread, Signal, Slot
    from PySide6.QtWidgets import (
        QApplication, QDialog, QFormLayout, QInputDialog, QLabel, QLineEdit,
        QMainWindow, QMessageBox, QPushButton, QTabWidget, QTableWidget,
        QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
    )
except ImportError as exc:  # keep server-side imports and tests usable
    raise RuntimeError("Для запуска desk-client установите PySide6") from exc

from api_client import ApiClient, ApiError


class ApiWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class LoginDialog(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Service Desk — вход")
        form = QFormLayout(self)
        self.url = QLineEdit(os.getenv("SERVICE_DESK_API", "http://localhost:8000"))
        self.employee_id = QLineEdit(os.getenv("SERVICE_DESK_EMPLOYEE_ID", "1"))
        form.addRow("Адрес API:", self.url)
        form.addRow("ID сотрудника:", self.employee_id)
        button = QPushButton("Проверить и войти")
        button.clicked.connect(self.accept)
        form.addRow(button)


class MainWindow(QMainWindow):
    def __init__(self, client: ApiClient, employee_id: int) -> None:
        super().__init__()
        self.client = client
        self.employee_id = employee_id
        self._threads: list[QThread] = []
        self._workers: list[ApiWorker] = []
        self.setWindowTitle("Service Desk")
        self.resize(1100, 700)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_queue()
        self._build_ticket_card()
        self._build_sla()
        self._build_engineers()
        self._build_assets()
        self._build_admin()
        self._build_health()
        self.load_queue()

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSortingEnabled(True)
        return table

    def _run(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        thread = QThread(self)
        worker = ApiWorker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_success)
        worker.failed.connect(self.show_error)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)
        self._workers.append(worker)
        def _cleanup():
            if worker in self._workers:
                self._workers.remove(worker)
            if thread in self._threads:
                self._threads.remove(thread)
        thread.finished.connect(_cleanup)
        thread.start()

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Ошибка", message)

    def _build_queue(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page)
        self.queue = self._table(["ID", "Номер", "Статус", "Приоритет", "Тема", "Создана", "SLA"])
        refresh = QPushButton("Обновить очередь"); refresh.clicked.connect(self.load_queue)
        self.queue.cellClicked.connect(lambda row, _: self.select_ticket(row))
        layout.addWidget(refresh); layout.addWidget(self.queue)
        self.tabs.addTab(page, "Очередь заявок")

    def load_queue(self) -> None:
        self._run(self.client.tickets, self.fill_queue)

    def fill_queue(self, rows: list[dict[str, Any]]) -> None:
        self.queue.setRowCount(0)
        for row in rows:
            index = self.queue.rowCount(); self.queue.insertRow(index)
            values = [row.get("ticket_id"), row.get("ticket_number"), row.get("status"), row.get("priority"), row.get("subject"), row.get("created_at"), row.get("sla_deadline")]
            for col, value in enumerate(values): self.queue.setItem(index, col, QTableWidgetItem(str(value or "")))

    def select_ticket(self, row: int) -> None:
        item = self.queue.item(row, 0)
        if item: self._run(lambda: self.client.ticket(int(item.text())), self.fill_ticket)

    def _build_ticket_card(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page)
        self.ticket_text = QTextEdit(); self.ticket_text.setReadOnly(True)
        status = QPushButton("Изменить статус"); status.clicked.connect(self.change_status)
        layout.addWidget(self.ticket_text); layout.addWidget(status)
        self.tabs.addTab(page, "Карточка заявки")

    def fill_ticket(self, ticket: dict[str, Any]) -> None:
        self.current_ticket = ticket
        self.ticket_text.setPlainText("\n".join(f"{key}: {value}" for key, value in ticket.items()))
        deadline = ticket.get("sla_deadline") or "не рассчитан"
        self.sla_label.setText(f"SLA deadline: {deadline}\nЗатрачено рабочих минут: {ticket.get('sla_spent_min', 0)}")
        self.tabs.setCurrentIndex(1)

    def change_status(self) -> None:
        if not hasattr(self, "current_ticket"): return
        status, ok = QInputDialog.getText(self, "Статус", "Новый статус:")
        if ok and status:
            self._run(lambda: self.client.change_status(self.current_ticket["ticket_id"], status), lambda _: self.load_queue())

    def _build_sla(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.sla_label = QLabel("Выберите заявку для просмотра SLA")
        layout.addWidget(self.sla_label); self.tabs.addTab(page, "SLA")

    def _build_engineers(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.engineer_table = self._table(["ID", "Инженер", "Назначено", "Открыто", "Решено"])
        button = QPushButton("Обновить загрузку"); button.clicked.connect(lambda: self._run(self.client.engineer_load, self.fill_engineers))
        layout.addWidget(button); layout.addWidget(self.engineer_table); self.tabs.addTab(page, "Загрузка инженеров")

    def fill_engineers(self, rows: list[dict[str, Any]]) -> None:
        self.engineer_table.setRowCount(0)
        for row in rows:
            index = self.engineer_table.rowCount(); self.engineer_table.insertRow(index)
            for col, key in enumerate(("engineer_id", "engineer_name", "assigned", "closed", "avg_hours")):
                self.engineer_table.setItem(index, col, QTableWidgetItem(str(row.get(key, ""))))

    def _build_assets(self) -> None:
        page = QWidget(); layout = QFormLayout(page); self.asset_id = QLineEdit(); self.asset_result = QTextEdit(); self.asset_result.setReadOnly(True)
        button = QPushButton("Найти оборудование"); button.clicked.connect(self.load_asset)
        layout.addRow("ID оборудования:", self.asset_id); layout.addRow(button); layout.addRow(self.asset_result); self.tabs.addTab(page, "Оборудование")

    def load_asset(self) -> None:
        try: asset_id = int(self.asset_id.text())
        except ValueError: self.show_error("ID оборудования должен быть числом"); return
        self._run(lambda: self.client.asset(asset_id), lambda row: self.asset_result.setPlainText("\n".join(f"{k}: {v}" for k, v in row.items())))

    def _build_admin(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.users = self._table(["ID", "Табельный номер", "Фамилия", "Имя", "Email", "Активен"])
        button = QPushButton("Загрузить пользователей"); button.clicked.connect(lambda: self._run(self.client.users, self.fill_users))
        layout.addWidget(button); layout.addWidget(self.users); self.tabs.addTab(page, "Администрирование")

    def fill_users(self, rows: list[dict[str, Any]]) -> None:
        self.users.setRowCount(0)
        for row in rows:
            index = self.users.rowCount(); self.users.insertRow(index)
            values = [row.get("employee_id"), row.get("tab_number"), row.get("last_name"), row.get("first_name"), row.get("email"), row.get("is_active")]
            for col, value in enumerate(values): self.users.setItem(index, col, QTableWidgetItem(str(value or "")))

    def _build_health(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.health = QLabel("Проверка не выполнялась")
        button = QPushButton("Проверить API"); button.clicked.connect(lambda: self._run(self.client.health, lambda row: self.health.setText(f"API: {row.get('status', 'unknown')}")))
        layout.addWidget(button); layout.addWidget(self.health); self.tabs.addTab(page, "Состояние системы")


def main() -> int:
    app = QApplication(sys.argv)
    dialog = LoginDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted: return 0
    try:
        client = ApiClient(dialog.url.text())
        client.health()
        window = MainWindow(client, int(dialog.employee_id.text()))
    except (ApiError, ValueError) as exc:
        QMessageBox.critical(None, "Вход не выполнен", str(exc)); return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
