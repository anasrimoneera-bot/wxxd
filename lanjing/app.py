"""蓝鲸科技 · 图形界面（PySide6）。

数据来源：微信小店联盟官方接口 / 样例模式。
不含任何进程注入、内存读取或系统代理修改。
"""
from __future__ import annotations
import sys
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QCheckBox, QPlainTextEdit, QFrame,
)

from . import APP_NAME, __version__
from .models import product_from_api, Product
from .exporter import export_excel

NAVY = "#17275c"
ORANGE = "#ff7a18"


class FetchWorker(QThread):
    got = Signal(object)          # Product
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, client, max_items: int, min_commission: float):
        super().__init__()
        self.client, self.max_items, self.min_commission = client, max_items, min_commission

    def run(self):
        try:
            n = 0
            for raw in self.client.iter_products(max_items=self.max_items):
                p = product_from_api(raw)
                if p.commission_rate < self.min_commission:
                    continue
                self.got.emit(p)
                n += 1
            self.done.emit(n)
        except Exception as e:  # noqa
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    COLS = ["商品ID", "标题", "店铺", "价格(元)", "佣金%", "销量", "SKU", "视频"]

    def __init__(self):
        super().__init__()
        self.products: list[Product] = []
        self.client = None
        self.worker: Optional[FetchWorker] = None
        self.setWindowTitle(f"{APP_NAME} · 微信小店联盟选品工作台")
        self.resize(1100, 720)
        self._build()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶栏
        bar = QFrame()
        bar.setStyleSheet(f"background:{NAVY};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 14, 20, 14)
        brand = QLabel(f"🐋 {APP_NAME}")
        brand.setStyleSheet("color:white;font-size:20px;font-weight:800;")
        sub = QLabel("WECHAT SHOP · 联盟选品（官方接口 · 零注入）")
        sub.setStyleSheet("color:#9fb0d6;font-size:12px;")
        bl.addWidget(brand)
        bl.addSpacing(12)
        bl.addWidget(sub)
        bl.addStretch()
        lay.addWidget(bar)

        # 控制区
        ctrl = QWidget()
        cl = QHBoxLayout(ctrl)
        cl.setContentsMargins(20, 16, 20, 8)
        self.demo_cb = QCheckBox("样例数据模式")
        self.demo_cb.setChecked(True)
        self.demo_cb.stateChanged.connect(self._toggle_demo)
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("联盟 access_token（真实模式填写）")
        self.token_edit.setEnabled(False)
        self.token_edit.setMinimumWidth(320)
        self.commission = QDoubleSpinBox()
        self.commission.setRange(0, 100)
        self.commission.setValue(0)
        self.commission.setPrefix("最低佣金 ")
        self.commission.setSuffix(" %")
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setRange(0, 100000)
        self.max_spin.setDecimals(0)
        self.max_spin.setValue(0)
        self.max_spin.setPrefix("最多 ")
        self.max_spin.setSuffix(" 件(0=全部)")
        self.fetch_btn = QPushButton("拉取商品")
        self.fetch_btn.clicked.connect(self.start_fetch)
        self.fetch_btn.setStyleSheet(
            f"background:{ORANGE};color:white;font-weight:700;padding:6px 18px;border:none;border-radius:6px;")
        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.clicked.connect(self.do_export)
        for w in (self.demo_cb, self.token_edit, self.commission, self.max_spin):
            cl.addWidget(w)
        cl.addStretch()
        cl.addWidget(self.fetch_btn)
        cl.addWidget(self.export_btn)
        lay.addWidget(ctrl)

        # 搜索
        srow = QWidget()
        sl = QHBoxLayout(srow)
        sl.setContentsMargins(20, 0, 20, 8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("在已拉取结果中搜索标题 / 商品ID")
        self.search.textChanged.connect(self._filter)
        sl.addWidget(self.search)
        lay.addWidget(srow)

        # 表格
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setStyleSheet("QTableWidget{border:none;} QHeaderView::section{background:#eef2fb;padding:6px;border:none;}")
        lay.addWidget(self.table, 1)

        # 日志
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setStyleSheet("background:#0b1220;color:#8fd0a0;font-family:Consolas,monospace;font-size:12px;")
        lay.addWidget(self.log)

        self.statusBar().showMessage(f"{APP_NAME} v{__version__} · 就绪 · 官方接口模式，不注入微信进程")

    # --- 行为 ---
    def _toggle_demo(self):
        self.token_edit.setEnabled(not self.demo_cb.isChecked())

    def _append(self, msg: str):
        self.log.appendPlainText(msg)

    def _make_client(self):
        if self.demo_cb.isChecked():
            from .sample_data import SampleClient
            return SampleClient()
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, APP_NAME, "真实模式需要填写联盟 access_token")
            return None
        from .api_client import LeagueClient
        return LeagueClient(access_token=token)

    def start_fetch(self):
        if self.worker and self.worker.isRunning():
            return
        client = self._make_client()
        if client is None:
            return
        self.products.clear()
        self.table.setRowCount(0)
        self.fetch_btn.setEnabled(False)
        self._append("开始拉取联盟商品…（官方接口）")
        self.worker = FetchWorker(client, int(self.max_spin.value()), self.commission.value())
        self.worker.got.connect(self._add_product)
        self.worker.done.connect(self._fetch_done)
        self.worker.failed.connect(self._fetch_failed)
        self.worker.start()

    def _add_product(self, p: Product):
        self.products.append(p)
        self._insert_row(p)

    def _insert_row(self, p: Product):
        r = self.table.rowCount()
        self.table.insertRow(r)
        vals = [p.product_id, p.title, p.shop_name, p.price_text,
                f"{p.commission_rate:.1f}", str(p.sold_count), str(len(p.skus)),
                "有" if p.video_url else ""]
        for c, v in enumerate(vals):
            self.table.setItem(r, c, QTableWidgetItem(v))

    def _fetch_done(self, n: int):
        self.fetch_btn.setEnabled(True)
        self._append(f"完成，共 {n} 件。")
        self.statusBar().showMessage(f"已拉取 {n} 件商品")

    def _fetch_failed(self, msg: str):
        self.fetch_btn.setEnabled(True)
        self._append("失败：" + msg)
        QMessageBox.critical(self, APP_NAME, "拉取失败：\n" + msg)

    def _filter(self, text: str):
        text = text.strip().lower()
        for r in range(self.table.rowCount()):
            pid = self.table.item(r, 0).text().lower()
            title = self.table.item(r, 1).text().lower()
            self.table.setRowHidden(r, bool(text) and text not in pid and text not in title)

    def do_export(self):
        if not self.products:
            QMessageBox.information(self, APP_NAME, "请先拉取商品")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "蓝鲸选品.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            n = export_excel(self.products, path)
            self._append(f"已导出 {n} 件到 {path}")
            QMessageBox.information(self, APP_NAME, f"已导出 {n} 件到：\n{path}")
        except Exception as e:  # noqa
            QMessageBox.critical(self, APP_NAME, "导出失败：\n" + str(e))


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
