# -*- coding: utf-8 -*-
"""
필지 분할 시뮬레이션 모듈 (NF-03 - Cadastral Divisions/Split Polygon 벤치마킹 이식)
- 대상(구역계/조회영역 또는 개별 필지)을 다음 방식으로 분할:
  1) N등분(등면적): 장축 방향 누적면적 i/N 위치를 이분탐색으로 찾아 밴드(geom∩사각형)
     생성 → splitGeometry 의존 없이 안정적 등면적 분할
  2) 목표면적별: 목표면적 스트라이프를 순차 절단, 잔여는 마지막 조각
  3) 분할선 그리기: LineDrawTool로 선 1개 → QgsGeometry.splitGeometry 절단
- 결과를 메모리 레이어 '분할시뮬레이션'으로 표시 + 조각 면적표 + 과소필지 경고

주의: 기하학적 등면적 분할로 실제 분할측량·도로계획·획지계획과 다른 참고용 시뮬레이션.
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor, QDoubleValidator
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QFileDialog,
    QAbstractItemView,
)
from qgis.core import (
    QgsProject, QgsGeometry, QgsPointXY, QgsRectangle, QgsFeature, QgsField,
    QgsVectorLayer, QgsFillSymbol, QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog,
)
from PyQt5.QtCore import QVariant

from .constants import extract_jimok_from_jibun
from .cost_calculator import geojson_to_wkt
from .export_manager import ExportManager
from .legal_standards import MIN_PARCEL_AREA_BY_ZONE

RESULT_LAYER_NAME = "분할시뮬레이션"
DEFAULT_MIN_AREA = min(MIN_PARCEL_AREA_BY_ZONE.values())  # 과소 경고 기본값


class ParcelSplitAnalyzer:
    """등면적/목표면적/분할선 기반 폴리곤 분할 클래스 (계산은 EPSG:5186)"""

    def __init__(self):
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def _to_5186(self, geom_wgs84):
        transform = QgsCoordinateTransform(
            self.crs_wgs84, self.crs_5186, QgsProject.instance())
        geom = QgsGeometry(geom_wgs84)
        geom.transform(transform)
        return geom

    def _to_wgs84(self, geom_5186):
        transform = QgsCoordinateTransform(
            self.crs_5186, self.crs_wgs84, QgsProject.instance())
        geom = QgsGeometry(geom_5186)
        geom.transform(transform)
        return geom

    # --- 등면적 밴드 분할 ------------------------------------------------
    def _cumulative(self, geom, axis, base_min, val, omin, omax):
        if axis == 'x':
            rect = QgsRectangle(base_min, omin, val, omax)
        else:
            rect = QgsRectangle(omin, base_min, omax, val)
        inter = geom.intersection(QgsGeometry.fromRect(rect))
        if inter is None or inter.isEmpty():
            return 0.0
        return inter.area()

    def _band(self, geom, axis, c0, c1, omin, omax):
        if axis == 'x':
            rect = QgsRectangle(c0, omin, c1, omax)
        else:
            rect = QgsRectangle(omin, c0, omax, c1)
        return geom.intersection(QgsGeometry.fromRect(rect))

    def _find_pos(self, geom, axis, base_min, lo, hi, omin, omax, target):
        a, b = lo, hi
        for _ in range(45):
            mid = (a + b) / 2.0
            area = self._cumulative(geom, axis, base_min, mid, omin, omax)
            if area < target:
                a = mid
            else:
                b = mid
        return (a + b) / 2.0

    def _axis_bounds(self, geom):
        bb = geom.boundingBox()
        if bb.width() >= bb.height():
            return ('x', bb.xMinimum(), bb.xMaximum(),
                    bb.yMinimum(), bb.yMaximum())
        return ('y', bb.yMinimum(), bb.yMaximum(),
                bb.xMinimum(), bb.xMaximum())

    def split_equal(self, geom_5186, n):
        if n < 2:
            return [QgsGeometry(geom_5186)]
        axis, lo, hi, omin, omax = self._axis_bounds(geom_5186)
        total = geom_5186.area()
        bounds = [lo]
        for i in range(1, n):
            bounds.append(self._find_pos(
                geom_5186, axis, lo, lo, hi, omin, omax, total * i / n))
        bounds.append(hi)
        return self._bands_from_bounds(geom_5186, axis, bounds, omin, omax)

    def split_by_area(self, geom_5186, target_area):
        total = geom_5186.area()
        if target_area <= 0 or target_area >= total:
            return [QgsGeometry(geom_5186)]
        axis, lo, hi, omin, omax = self._axis_bounds(geom_5186)
        bounds = [lo]
        pos = lo
        guard = 0
        while guard < 1000:
            guard += 1
            base = self._cumulative(geom_5186, axis, lo, pos, omin, omax)
            if total - base <= target_area * 1.0001:
                break
            x = self._find_pos(geom_5186, axis, lo, pos, hi, omin, omax,
                               base + target_area)
            if x <= pos + (hi - lo) * 1e-7:
                break
            bounds.append(x)
            pos = x
        bounds.append(hi)
        return self._bands_from_bounds(geom_5186, axis, bounds, omin, omax)

    def _bands_from_bounds(self, geom, axis, bounds, omin, omax):
        parts = []
        for j in range(len(bounds) - 1):
            part = self._band(geom, axis, bounds[j], bounds[j + 1], omin, omax)
            if part is not None and not part.isEmpty() and part.area() > 1e-6:
                parts.append(part)
        return parts

    # --- 분할선 절단 ----------------------------------------------------
    def split_by_line(self, geom_5186, line_wgs84):
        """그린 단일 선으로 폴리곤 절단. 실패 시 None."""
        line = self._to_5186(line_wgs84)
        try:
            pts = line.asPolyline()
        except Exception:
            pts = []
        if len(pts) < 2:
            return None
        # 양 끝을 폴리곤 밖으로 충분히 연장해 완전 횡단 보장
        p0, p1 = pts[0], pts[-1]
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            return None
        bb = geom_5186.boundingBox()
        ext = (bb.width() + bb.height()) or 1000.0
        ux, uy = dx / length, dy / length
        a = QgsPointXY(p0.x() - ux * ext, p0.y() - uy * ext)
        b = QgsPointXY(p1.x() + ux * ext, p1.y() + uy * ext)

        g = QgsGeometry(geom_5186)
        try:
            res = g.splitGeometry([a, b], False)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"split_by_line error: {e}", "VWorld", Qgis.Warning)
            return None
        new_geoms = []
        if isinstance(res, tuple):
            if len(res) > 1 and res[1]:
                new_geoms = list(res[1])
        parts = [g] + new_geoms
        parts = [p for p in parts if p is not None and not p.isEmpty()
                 and p.area() > 1e-6]
        if len(parts) < 2:
            return None
        return parts

    # --- 공통 ----------------------------------------------------------
    def to_rows(self, parts_5186):
        """분할 조각 → [{'idx','area','area_pyeong','geom_wkt'(4326)}]"""
        rows = []
        for i, part in enumerate(parts_5186, start=1):
            wgs = self._to_wgs84(part)
            rows.append({
                'idx': i,
                'area': part.area(),
                'area_pyeong': part.area() / 3.305785,
                'geom_wkt': wgs.asWkt(),
            })
        return rows

    def create_result_layer(self, rows):
        old = [layer.id() for layer in QgsProject.instance().mapLayers().values()
               if layer.name() == RESULT_LAYER_NAME]
        for lid in old:
            QgsProject.instance().removeMapLayer(lid)

        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326", RESULT_LAYER_NAME, "memory")
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField("idx", QVariant.Int),
            QgsField("area_m2", QVariant.Double),
        ])
        layer.updateFields()

        features = []
        for r in rows:
            geom = QgsGeometry.fromWkt(r['geom_wkt'])
            if geom.isEmpty():
                continue
            qf = QgsFeature(layer.fields())
            qf.setGeometry(geom)
            qf.setAttributes([r['idx'], r['area']])
            features.append(qf)
        provider.addFeatures(features)
        layer.updateExtents()

        categories = []
        for r in rows:
            color = QColor.fromHsv((r['idx'] * 53) % 360, 170, 230)
            color.setAlpha(130)
            symbol = QgsFillSymbol.createSimple({
                'color': color.name(QColor.HexArgb),
                'outline_color': '#333333',
                'outline_width': '0.3',
            })
            categories.append(
                QgsRendererCategory(r['idx'], symbol, f"{r['idx']}번"))
        layer.setRenderer(QgsCategorizedSymbolRenderer("idx", categories))
        QgsProject.instance().addMapLayer(layer)
        layer.triggerRepaint()
        return layer


class ParcelSplitTab(QWidget):
    """필지 분할 시뮬레이션 탭 (개발성 분석 서브탭)"""

    requestDrawLine = pyqtSignal()  # 분할선 그리기 도구 요청 - main이 활성화

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyzer = ParcelSplitAnalyzer()
        self.targets = []   # [(label, geom_wgs84)]
        self.rows = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        cfg = QGroupBox("분할 대상 및 방식")
        grid = QGridLayout()
        grid.addWidget(QLabel("분할 대상:"), 0, 0)
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(240)
        grid.addWidget(self.target_combo, 0, 1, 1, 2)

        grid.addWidget(QLabel("분할 방식:"), 1, 0)
        self.method_combo = QComboBox()
        self.method_combo.addItems(
            ["N등분 (등면적)", "목표면적별 (m2)", "분할선 그리기"])
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        grid.addWidget(self.method_combo, 1, 1)

        self.n_spin = QSpinBox()
        self.n_spin.setRange(2, 50)
        self.n_spin.setValue(2)
        self.n_spin.setPrefix("N=")
        grid.addWidget(self.n_spin, 1, 2)

        self.area_edit = QLineEdit("330")
        self.area_edit.setValidator(QDoubleValidator(1, 1e9, 1))
        self.area_edit.setPlaceholderText("목표면적(m2)")
        self.area_edit.setEnabled(False)
        grid.addWidget(self.area_edit, 1, 3)

        grid.addWidget(QLabel("과소 경고기준(m2):"), 2, 0)
        self.min_area_edit = QLineEdit(str(DEFAULT_MIN_AREA))
        self.min_area_edit.setValidator(QDoubleValidator(0, 1e6, 1))
        self.min_area_edit.setMaximumWidth(90)
        grid.addWidget(self.min_area_edit, 2, 1)

        self.run_btn = QPushButton("분할 실행")
        self.run_btn.setStyleSheet("font-weight: bold;")
        self.run_btn.clicked.connect(self.run_split)
        grid.addWidget(self.run_btn, 2, 2)
        export_btn = QPushButton("엑셀 내보내기")
        export_btn.clicked.connect(self.export_xlsx)
        grid.addWidget(export_btn, 2, 3)
        cfg.setLayout(grid)
        layout.addWidget(cfg)

        hint = QLabel(
            "※ '분할선 그리기' 선택 후 [분할 실행]을 누르면 지도에서 선을 그립니다 "
            "(시작·끝점 클릭, 더블클릭/Enter 완료). 등면적 분할은 장축 방향 "
            "스트라이프로 계산됩니다.")
        hint.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(
            ["조각 번호", "면적(m2)", "면적(평)"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        self.summary_label = QLabel("분할 전")
        self.summary_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

    # ------------------------------------------------------------------
    def _on_method_changed(self, idx):
        self.n_spin.setEnabled(idx == 0)
        self.area_edit.setEnabled(idx == 1)

    def set_land_info(self, cadastral_items, district_geom_wgs84):
        self.targets = []
        if district_geom_wgs84 is not None and \
                not district_geom_wgs84.isEmpty():
            self.targets.append(
                ("구역/조회영역 전체", QgsGeometry(district_geom_wgs84)))
        for item in (cadastral_items or []):
            props = item.get('properties', {})
            geom_data = item.get('geometry', {})
            if not geom_data:
                continue
            pnu = str(props.get('pnu', '') or '')
            jibun = str(props.get('jibun', '') or '')
            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                continue
            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue
            label = f"{jibun or '필지'} ({pnu})"
            self.targets.append((label, geom))
        self.target_combo.clear()
        for label, _ in self.targets:
            self.target_combo.addItem(label)

    def _current_target(self):
        idx = self.target_combo.currentIndex()
        if idx < 0 or idx >= len(self.targets):
            return None
        return self.targets[idx][1]

    def _read_float(self, edit, default):
        try:
            return float(edit.text().replace(',', '').strip())
        except (ValueError, AttributeError):
            return default

    def run_split(self):
        target = self._current_target()
        if target is None or target.isEmpty():
            QMessageBox.warning(
                self, "대상 없음",
                "분할 대상이 없습니다. 먼저 토지정보를 조회하거나 구역계를 "
                "확정하세요.")
            return
        method = self.method_combo.currentIndex()
        if method == 2:
            # 분할선 그리기 → main이 LineDrawTool 활성화 후 set_drawn_line 호출
            self.requestDrawLine.emit()
            return
        geom_5186 = self.analyzer._to_5186(target)
        if method == 0:
            parts = self.analyzer.split_equal(geom_5186, self.n_spin.value())
        else:
            parts = self.analyzer.split_by_area(
                geom_5186, self._read_float(self.area_edit, 330.0))
        self._apply_parts(parts)

    def set_drawn_line(self, line_wgs84):
        """main이 분할선 완료 시 호출"""
        target = self._current_target()
        if target is None or target.isEmpty():
            return
        geom_5186 = self.analyzer._to_5186(target)
        parts = self.analyzer.split_by_line(geom_5186, line_wgs84)
        if not parts:
            QMessageBox.warning(
                self, "분할 실패",
                "그린 선으로 대상을 분할하지 못했습니다. 선이 대상 폴리곤을 "
                "완전히 가로지르도록 다시 그려보세요.")
            return
        self._apply_parts(parts)

    def _apply_parts(self, parts):
        if not parts:
            QMessageBox.information(self, "결과 없음", "분할 결과가 없습니다.")
            return
        self.rows = self.analyzer.to_rows(parts)
        self.populate_table()
        self.update_summary()
        try:
            self.analyzer.create_result_layer(self.rows)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"split layer error: {e}", "VWorld", Qgis.Warning)

    def populate_table(self):
        min_area = self._read_float(self.min_area_edit, DEFAULT_MIN_AREA)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            vals = [f"{r['idx']}", f"{r['area']:,.1f}",
                    f"{r['area_pyeong']:,.1f}"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if r['area'] < min_area:
                    item.setBackground(QColor(255, 205, 210))
                self.table.setItem(i, c, item)

    def update_summary(self):
        if not self.rows:
            self.summary_label.setText("분할 결과 없음")
            return
        areas = [r['area'] for r in self.rows]
        min_area = self._read_float(self.min_area_edit, DEFAULT_MIN_AREA)
        under = sum(1 for a in areas if a < min_area)
        msg = (f"조각 {len(areas)}개 | 최소 {min(areas):,.1f} / "
               f"최대 {max(areas):,.1f} / 평균 {sum(areas) / len(areas):,.1f} m2")
        if under:
            msg += (f" | ⚠ 과소(<{min_area:,.0f}m2) {under}조각 - "
                    "분필 시 건축 제한 검토 필요")
        self.summary_label.setText(msg)

    def export_xlsx(self):
        if not self.rows:
            QMessageBox.information(self, "안내", "먼저 [분할 실행]을 수행하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "분할 결과 저장", "필지분할시뮬레이션.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        headers = ["조각 번호", "면적(m2)", "면적(평)"]
        data_rows = [[f"{r['idx']}", f"{r['area']:,.1f}",
                      f"{r['area_pyeong']:,.1f}"] for r in self.rows]
        saved = ExportManager.export_table_xlsx(headers, data_rows, path)
        QMessageBox.information(self, "저장 완료", f"저장됨: {saved}")

    def reset(self):
        self.targets = []
        self.rows = []
        self.target_combo.clear()
        self.table.setRowCount(0)
        self.summary_label.setText("분할 전")

    def get_report_data(self):
        if not self.rows:
            return None
        areas = [r['area'] for r in self.rows]
        min_area = self._read_float(self.min_area_edit, DEFAULT_MIN_AREA)
        under = sum(1 for a in areas if a < min_area)
        return {
            'title': '개발성 분석 - 필지 분할 시뮬레이션 (참고용)',
            'kv': [
                ('대상', self.target_combo.currentText()),
                ('분할 방식', self.method_combo.currentText()),
                ('조각 수', f"{len(areas)}"),
                ('면적 최소/평균/최대',
                 f"{min(areas):,.1f} / {sum(areas) / len(areas):,.1f} / "
                 f"{max(areas):,.1f} m2"),
                ('과소 조각', f"{under}개 (<{min_area:,.0f}m2)"),
            ],
            'tables': [{
                'title': '분할 조각 면적',
                'headers': ['조각 번호', '면적(m2)', '면적(평)'],
                'rows': [[f"{r['idx']}", f"{r['area']:,.1f}",
                          f"{r['area_pyeong']:,.1f}"] for r in self.rows],
            }],
        }
