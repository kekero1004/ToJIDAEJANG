# -*- coding: utf-8 -*-
"""
접도·맹지 분석 모듈 (NF-02 - 동종 road/accessibility 플러그인 벤치마킹 이식)
- 각 필지가 도로에 접하는지(맹지 여부)·접도길이·최단 도로거리를 산출.
  한국 건축법 제44조: 대지는 너비 2m 이상 도로에 2m 이상 접해야 건축 가능 →
  맹지(도로 미접)는 건축 불가로 토지가치에 결정적.
- 도로 레이어는 프로젝트 벡터 레이어(라인/폴리곤) 중 선택(VWorld 도로 WMS는
  래스터라 거리계산 불가 → 레이어 탭에서 SHP/도로망 로드 후 선택).
- 판정: 맹지(미접) / 준맹지(접하나 접도길이<기준) / 건축가능(접도길이>=기준).

주의: 도로 레이어 정확도(현황도로·지적도상 도로 포함 여부)에 따라 결과가 달라지는
      참고용 판정이다. 실제 건축 가능 여부는 관할 행정청·현황측량으로 확인해야 한다.
"""

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QAbstractItemView,
)
from qgis.PyQt.QtGui import QDoubleValidator
from qgis.core import (
    QgsProject, QgsGeometry, QgsFeature, QgsField,
    QgsVectorLayer, QgsWkbTypes, QgsFillSymbol, QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog,
)
from PyQt5.QtCore import QVariant

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu
from .cost_calculator import geojson_to_wkt
from .export_manager import ExportManager

RESULT_LAYER_NAME = "접도분석"

VERDICT_COLORS = {
    '건축가능': QColor(76, 175, 80),
    '준맹지': QColor(255, 152, 0),
    '맹지': QColor(231, 76, 60),
}

DEFAULT_TOLERANCE_M = 0.5   # 접도 판정 허용오차(m)
DEFAULT_MIN_FRONTAGE_M = 2.0  # 건축가능 최소 접도길이(m, 건축법 제44조)


class RoadAccessAnalyzer:
    """필지 접도·맹지 판정 클래스"""

    def __init__(self):
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def _road_geometry_5186(self, road_layer):
        """도로 레이어의 모든 피처를 EPSG:5186으로 모은 단일 지오메트리."""
        if road_layer is None:
            return None
        to5186 = QgsCoordinateTransform(
            road_layer.crs(), self.crs_5186, QgsProject.instance())
        geoms = []
        for feat in road_layer.getFeatures():
            g = feat.geometry()
            if g is None or g.isEmpty():
                continue
            g = QgsGeometry(g)
            try:
                g.transform(to5186)
            except Exception:
                continue
            geoms.append(g)
        if not geoms:
            return None
        combined = QgsGeometry.collectGeometry(geoms)
        if combined is None or combined.isEmpty():
            return None
        return combined

    @staticmethod
    def _boundary_lines(geom):
        """폴리곤 지오메트리의 모든 링(외곽+내부) → 멀티라인 지오메트리."""
        rings = []
        try:
            if geom.isMultipart():
                for poly in geom.asMultiPolygon():
                    rings.extend(poly)
            else:
                rings.extend(geom.asPolygon())
        except Exception:
            return None
        rings = [r for r in rings if r and len(r) >= 2]
        if not rings:
            return None
        return QgsGeometry.fromMultiPolylineXY(rings)

    def analyze(self, cadastral_items, road_layer,
                tolerance=DEFAULT_TOLERANCE_M,
                min_frontage=DEFAULT_MIN_FRONTAGE_M):
        """필지별 접도 판정.

        반환: [{'pnu','jibun','jimok','touches','frontage','distance',
                'verdict','geom_wkt'}]  (실패 시 빈 리스트)
        """
        road_geom = self._road_geometry_5186(road_layer)
        if road_geom is None:
            return []
        road_buffer = road_geom.buffer(max(tolerance, 0.01), 8)

        transform = QgsCoordinateTransform(
            self.crs_wgs84, self.crs_5186, QgsProject.instance())

        rows = []
        for item in (cadastral_items or []):
            props = item.get('properties', {})
            geom_data = item.get('geometry', {})
            if not geom_data:
                continue
            pnu = str(props.get('pnu', '') or '')
            jibun = str(props.get('jibun', '') or '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)

            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                continue
            geom_wgs84 = QgsGeometry.fromWkt(wkt)
            if geom_wgs84.isEmpty():
                continue
            geom = QgsGeometry(geom_wgs84)
            try:
                geom.transform(transform)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"RoadAccess transform error: {e}", "VWorld", Qgis.Warning)
                continue

            try:
                distance = geom.distance(road_geom)
            except Exception:
                distance = float('inf')

            frontage = 0.0
            boundary = self._boundary_lines(geom)
            if boundary is not None and not boundary.isEmpty():
                try:
                    inter = boundary.intersection(road_buffer)
                    if inter is not None and not inter.isEmpty():
                        frontage = inter.length()
                except Exception:
                    frontage = 0.0

            touches = distance <= tolerance
            if not touches:
                verdict = '맹지'
            elif frontage < min_frontage:
                verdict = '준맹지'
            else:
                verdict = '건축가능'

            rows.append({
                'pnu': pnu,
                'jibun': jibun,
                'jimok': jimok,
                'touches': touches,
                'frontage': frontage,
                'distance': distance,
                'verdict': verdict,
                'geom_wkt': geom_wgs84.asWkt(),
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
            QgsField("pnu", QVariant.String),
            QgsField("jibun", QVariant.String),
            QgsField("verdict", QVariant.String),
            QgsField("frontage", QVariant.Double),
            QgsField("road_dist", QVariant.Double),
        ])
        layer.updateFields()

        features = []
        for r in rows:
            geom = QgsGeometry.fromWkt(r['geom_wkt'])
            if geom.isEmpty():
                continue
            qf = QgsFeature(layer.fields())
            qf.setGeometry(geom)
            dist = r['distance'] if r['distance'] != float('inf') else -1.0
            qf.setAttributes(
                [r['pnu'], r['jibun'], r['verdict'], r['frontage'], dist])
            features.append(qf)
        provider.addFeatures(features)
        layer.updateExtents()

        categories = []
        for verdict, color in VERDICT_COLORS.items():
            fill = QColor(color)
            fill.setAlpha(140)
            symbol = QgsFillSymbol.createSimple({
                'color': fill.name(QColor.HexArgb),
                'outline_color': '#444444',
                'outline_width': '0.2',
            })
            categories.append(QgsRendererCategory(verdict, symbol, verdict))
        layer.setRenderer(QgsCategorizedSymbolRenderer("verdict", categories))
        QgsProject.instance().addMapLayer(layer)
        layer.triggerRepaint()
        return layer


class RoadAccessTab(QWidget):
    """접도·맹지 분석 탭 (개발성 분석 서브탭)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyzer = RoadAccessAnalyzer()
        self.cadastral_items = []
        self.rows = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        cfg_group = QGroupBox("도로 레이어 및 기준")
        grid = QGridLayout()
        grid.addWidget(QLabel("도로 레이어:"), 0, 0)
        self.road_combo = QComboBox()
        self.road_combo.setMinimumWidth(220)
        grid.addWidget(self.road_combo, 0, 1)
        refresh_btn = QPushButton("레이어 새로고침")
        refresh_btn.clicked.connect(self.refresh_layers)
        grid.addWidget(refresh_btn, 0, 2)
        grid.addWidget(QLabel("접도 허용오차(m):"), 1, 0)
        self.tol_edit = QLineEdit(str(DEFAULT_TOLERANCE_M))
        self.tol_edit.setValidator(QDoubleValidator(0, 100, 2))
        self.tol_edit.setMaximumWidth(80)
        grid.addWidget(self.tol_edit, 1, 1)
        grid.addWidget(QLabel("건축가능 최소 접도길이(m):"), 2, 0)
        self.frontage_edit = QLineEdit(str(DEFAULT_MIN_FRONTAGE_M))
        self.frontage_edit.setValidator(QDoubleValidator(0, 1000, 2))
        self.frontage_edit.setMaximumWidth(80)
        grid.addWidget(self.frontage_edit, 2, 1)
        self.analyze_btn = QPushButton("접도·맹지 분석")
        self.analyze_btn.setStyleSheet("font-weight: bold;")
        self.analyze_btn.clicked.connect(self.run_analysis)
        grid.addWidget(self.analyze_btn, 1, 2)
        self.map_btn = QPushButton("지도 표시")
        self.map_btn.clicked.connect(self.show_on_map)
        grid.addWidget(self.map_btn, 2, 2)
        cfg_group.setLayout(grid)
        layout.addWidget(cfg_group)

        hint = QLabel(
            "※ 도로 레이어가 없으면 [레이어] 탭에서 도로망 SHP를 불러온 뒤 "
            "[레이어 새로고침]을 누르세요. 건축법 제44조 기준 기본 2m 접도.")
        hint.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        top = QHBoxLayout()
        export_btn = QPushButton("엑셀 내보내기")
        export_btn.clicked.connect(self.export_xlsx)
        top.addWidget(export_btn)
        top.addStretch()
        layout.addLayout(top)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["PNU", "지번", "지목", "접도길이(m)", "최단도로거리(m)", "판정"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        self.summary_label = QLabel("분석 전")
        self.summary_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.refresh_layers()

    # ------------------------------------------------------------------
    def set_land_info(self, cadastral_items):
        self.cadastral_items = cadastral_items or []

    def refresh_layers(self):
        current = self.road_combo.currentData()
        self.road_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if (isinstance(layer, QgsVectorLayer)
                    and layer.geometryType() in (
                        QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry)):
                self.road_combo.addItem(layer.name(), layer.id())
        if current is not None:
            idx = self.road_combo.findData(current)
            if idx >= 0:
                self.road_combo.setCurrentIndex(idx)

    def _selected_road_layer(self):
        lid = self.road_combo.currentData()
        if not lid:
            return None
        return QgsProject.instance().mapLayer(lid)

    def _read_float(self, edit, default):
        try:
            return float(edit.text().replace(',', '').strip())
        except (ValueError, AttributeError):
            return default

    def run_analysis(self):
        if not self.cadastral_items:
            QMessageBox.warning(self, "데이터 없음", "먼저 토지정보를 조회하세요.")
            return
        road_layer = self._selected_road_layer()
        if road_layer is None:
            QMessageBox.warning(
                self, "도로 레이어 필요",
                "도로 레이어를 선택하세요.\n도로망 SHP를 [레이어] 탭에서 "
                "불러온 뒤 [레이어 새로고침]을 누르면 목록에 나타납니다.")
            return
        tol = self._read_float(self.tol_edit, DEFAULT_TOLERANCE_M)
        min_frontage = self._read_float(
            self.frontage_edit, DEFAULT_MIN_FRONTAGE_M)
        self.rows = self.analyzer.analyze(
            self.cadastral_items, road_layer, tol, min_frontage)
        if not self.rows:
            QMessageBox.information(
                self, "결과 없음",
                "분석 결과가 없습니다. 도로 레이어에 유효한 도형이 있는지 "
                "확인하세요.")
        self.populate_table()
        self.update_summary()

    def populate_table(self):
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            dist = ('-' if r['distance'] == float('inf')
                    else f"{r['distance']:,.1f}")
            vals = [r['pnu'], r['jibun'], r['jimok'],
                    f"{r['frontage']:,.1f}", dist, r['verdict']]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 5:
                    item.setBackground(
                        VERDICT_COLORS.get(r['verdict'], QColor(255, 255, 255)))
                self.table.setItem(i, c, item)

    def update_summary(self):
        total = len(self.rows)
        if total == 0:
            self.summary_label.setText("분석 결과 없음")
            return
        landlocked = sum(1 for r in self.rows if r['verdict'] == '맹지')
        semi = sum(1 for r in self.rows if r['verdict'] == '준맹지')
        ok = sum(1 for r in self.rows if r['verdict'] == '건축가능')
        self.summary_label.setText(
            f"총 {total}필지 | 맹지 {landlocked}필지 "
            f"({landlocked / total * 100:.1f}%) | 준맹지 {semi}필지 | "
            f"건축가능 {ok}필지")

    def show_on_map(self):
        if not self.rows:
            QMessageBox.information(self, "안내", "먼저 [접도·맹지 분석]을 실행하세요.")
            return
        try:
            self.analyzer.create_result_layer(self.rows)
            QMessageBox.information(
                self, "지도 표시",
                f"'{RESULT_LAYER_NAME}' 레이어를 추가했습니다 "
                "(맹지=빨강/준맹지=주황/건축가능=초록).")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"지도 표시 실패: {e}")

    def export_xlsx(self):
        if not self.rows:
            QMessageBox.information(self, "안내", "먼저 [접도·맹지 분석]을 실행하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "접도·맹지 분석 저장", "접도맹지분석.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        headers = ["PNU", "지번", "지목", "접도길이(m)",
                   "최단도로거리(m)", "판정"]
        data_rows = [
            [r['pnu'], r['jibun'], r['jimok'], f"{r['frontage']:,.1f}",
             ('-' if r['distance'] == float('inf') else f"{r['distance']:,.1f}"),
             r['verdict']] for r in self.rows]
        saved = ExportManager.export_table_xlsx(headers, data_rows, path)
        QMessageBox.information(self, "저장 완료", f"저장됨: {saved}")

    def reset(self):
        self.cadastral_items = []
        self.rows = []
        self.table.setRowCount(0)
        self.summary_label.setText("분석 전")
        self.refresh_layers()

    def get_report_data(self):
        if not self.rows:
            return None
        total = len(self.rows)
        landlocked = sum(1 for r in self.rows if r['verdict'] == '맹지')
        semi = sum(1 for r in self.rows if r['verdict'] == '준맹지')
        ok = sum(1 for r in self.rows if r['verdict'] == '건축가능')
        return {
            'title': '개발성 분석 - 접도·맹지 (참고용 판정)',
            'kv': [
                ('총 필지 수', f"{total}"),
                ('맹지', f"{landlocked}필지 ({landlocked / total * 100:.1f}%)"),
                ('준맹지', f"{semi}필지"),
                ('건축가능', f"{ok}필지"),
            ],
            'tables': [{
                'title': '필지별 접도 판정',
                'headers': ['PNU', '지번', '접도길이(m)',
                            '최단도로거리(m)', '판정'],
                'rows': [[r['pnu'], r['jibun'], f"{r['frontage']:,.1f}",
                          ('-' if r['distance'] == float('inf')
                           else f"{r['distance']:,.1f}"), r['verdict']]
                         for r in self.rows[:200]],
            }],
        }
