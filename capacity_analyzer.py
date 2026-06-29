# -*- coding: utf-8 -*-
"""
개발용량 분석 모듈 (NF-01 - 동종 플러그인 MORPHINT/PlanX 벤치마킹 이식)
- 용도지역별 법정 건폐율(BCR)/용적률(FAR) 상한 → 필지·구역 개발용량 산출
  · 건축면적 상한 = 대지면적 × 건폐율/100
  · 허용 연면적   = 대지면적 × 용적률/100
  · 추정 층수     = 용적률 / 건폐율 (지상층, 건폐율 만재 가정)
  · 공지율 OSR    = (1 - 건폐율/100) × 100
- 용도지역별 기준값은 표에서 직접 수정(조례 강화 반영) + [기준 초기화]
- 구역 집계(면적가중 평균 건폐/용적률, 총 허용연면적) + 엑셀 + 보고서 + 주제도

주의: 법정 상한 기반 참고용 추정이다. 실제는 지구단위계획·도시계획조례·고도지구·
      일조 사선제한 등으로 더 제한될 수 있으므로 인허가 판단은 관할 행정청 확인 필요.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFileDialog, QAbstractItemView,
)
from qgis.core import (
    QgsProject, QgsGeometry, QgsPointXY, QgsFeature, QgsField,
    QgsVectorLayer, QgsFillSymbol, QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog,
)
from PyQt5.QtCore import QVariant

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu
from .cost_calculator import geojson_to_wkt
from .export_manager import ExportManager
from .legal_standards import (
    BCR_FAR_BY_ZONE, CAPACITY_DEFAULTS, match_capacity_zone,
)

RESULT_LAYER_NAME = "개발용량_분석"


class CapacityAnalyzer:
    """필지별 개발용량(건폐율·용적률 기반) 산출 클래스"""

    def __init__(self):
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def analyze(self, cadastral_items, land_use_items, overrides=None):
        """필지별 개발용량 산출.

        overrides: {zone_key: {'bcr': %, 'far': %}} (사용자 수정 기준)
        반환: [{'pnu','jibun','jimok','zone','zone_key','area','bcr','far',
                'build_area','gfa','floors','osr','geom_wkt'}]
        """
        overrides = overrides or {}

        zone_by_pnu = {}
        for item in (land_use_items or []):
            props = item.get('properties', {})
            pnu = str(props.get('pnu', '') or '')
            zone = str(props.get('prposAreaDstrcCodeNm', '') or '')
            if pnu and zone and '지역' in zone and pnu not in zone_by_pnu:
                zone_by_pnu[pnu] = zone

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
                    f"Capacity transform error: {e}", "VWorld", Qgis.Warning)
                continue
            area = geom.area()
            if area <= 0:
                continue

            zone = zone_by_pnu.get(pnu, '')
            zone_key = match_capacity_zone(zone)
            if zone_key and zone_key in overrides:
                bcr = float(overrides[zone_key].get('bcr', 0) or 0)
                far = float(overrides[zone_key].get('far', 0) or 0)
            elif zone_key:
                std = BCR_FAR_BY_ZONE[zone_key]
                bcr = float(std['bcr'])
                far = float(std['far'])
            else:
                bcr = 0.0
                far = 0.0

            build_area = area * bcr / 100.0
            gfa = area * far / 100.0
            floors = (far / bcr) if bcr > 0 else 0.0
            osr = (1.0 - bcr / 100.0) * 100.0 if bcr > 0 else 0.0

            rows.append({
                'pnu': pnu,
                'jibun': jibun,
                'jimok': jimok,
                'zone': zone or '(용도지역 미확인)',
                'zone_key': zone_key or '',
                'area': area,
                'bcr': bcr,
                'far': far,
                'build_area': build_area,
                'gfa': gfa,
                'floors': floors,
                'osr': osr,
                'geom_wkt': geom_wgs84.asWkt(),
            })
        return rows

    @staticmethod
    def aggregate(rows):
        """구역 집계: 면적가중 평균 건폐/용적률, 총 대지/연면적."""
        valid = [r for r in rows if r['far'] > 0]
        total_area = sum(r['area'] for r in rows)
        cap_area = sum(r['area'] for r in valid)
        total_gfa = sum(r['gfa'] for r in valid)
        total_build = sum(r['build_area'] for r in valid)
        if cap_area > 0:
            avg_bcr = sum(r['area'] * r['bcr'] for r in valid) / cap_area
            avg_far = sum(r['area'] * r['far'] for r in valid) / cap_area
        else:
            avg_bcr = avg_far = 0.0
        return {
            'count': len(rows),
            'count_valid': len(valid),
            'total_area': total_area,
            'total_build_area': total_build,
            'total_gfa': total_gfa,
            'avg_bcr': avg_bcr,
            'avg_far': avg_far,
        }

    def create_result_layer(self, rows):
        """용적률 한도 구간별 색상 주제도 (메모리 레이어)"""
        old = [layer.id() for layer in QgsProject.instance().mapLayers().values()
               if layer.name() == RESULT_LAYER_NAME]
        for lid in old:
            QgsProject.instance().removeMapLayer(lid)

        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326", RESULT_LAYER_NAME, "memory")
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField("pnu", QVariant.String),
            QgsField("zone", QVariant.String),
            QgsField("far", QVariant.Double),
            QgsField("gfa", QVariant.Double),
            QgsField("band", QVariant.String),
        ])
        layer.updateFields()

        step = float(CAPACITY_DEFAULTS.get('far_band_step', 100.0))
        features = []
        bands = set()
        for r in rows:
            geom = QgsGeometry.fromWkt(r['geom_wkt'])
            if geom.isEmpty():
                continue
            band = self._band_label(r['far'], step)
            bands.add(band)
            qf = QgsFeature(layer.fields())
            qf.setGeometry(geom)
            qf.setAttributes(
                [r['pnu'], r['zone'], r['far'], r['gfa'], band])
            features.append(qf)
        provider.addFeatures(features)
        layer.updateExtents()

        ordered = sorted(bands, key=self._band_sort_key)
        n = max(len(ordered), 1)
        categories = []
        for i, band in enumerate(ordered):
            # 초록(저밀)→빨강(고밀) 그라데이션
            ratio = i / (n - 1) if n > 1 else 0.0
            color = QColor(int(80 + 175 * ratio), int(180 - 140 * ratio), 60)
            color.setAlpha(130)
            symbol = QgsFillSymbol.createSimple({
                'color': color.name(QColor.HexArgb),
                'outline_color': '#555555',
                'outline_width': '0.1',
            })
            categories.append(QgsRendererCategory(band, symbol, band))
        layer.setRenderer(QgsCategorizedSymbolRenderer("band", categories))
        QgsProject.instance().addMapLayer(layer)
        layer.triggerRepaint()
        return layer

    @staticmethod
    def _band_label(far, step):
        if far <= 0:
            return "미확인"
        lo = int(far // step) * int(step)
        return f"{lo}~{lo + int(step)}%"

    @staticmethod
    def _band_sort_key(band):
        if band == "미확인":
            return -1
        try:
            return int(band.split('~')[0])
        except (ValueError, IndexError):
            return 0


class CapacityTab(QWidget):
    """개발용량 분석 탭 (개발성 분석 서브탭)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyzer = CapacityAnalyzer()
        self.cadastral_items = []
        self.land_use_items = []
        self.rows = []
        self.overrides = {}   # zone_key -> {'bcr','far'}
        self.agg = None
        self._updating = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        ctrl = QHBoxLayout()
        self.analyze_btn = QPushButton("개발용량 분석")
        self.analyze_btn.setStyleSheet("font-weight: bold;")
        self.analyze_btn.clicked.connect(self.run_analysis)
        ctrl.addWidget(self.analyze_btn)
        reset_btn = QPushButton("기준 초기화")
        reset_btn.clicked.connect(self.reset_criteria)
        ctrl.addWidget(reset_btn)
        self.map_btn = QPushButton("지도 표시")
        self.map_btn.clicked.connect(self.show_on_map)
        ctrl.addWidget(self.map_btn)
        export_btn = QPushButton("엑셀 내보내기")
        export_btn.clicked.connect(self.export_xlsx)
        ctrl.addWidget(export_btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        hint = QLabel(
            "※ 용도지역별 건폐율·용적률은 법정 상한값입니다. 조례 강화분은 "
            "아래 기준표에서 직접 수정 후 [개발용량 분석]을 다시 실행하세요.")
        hint.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 기준(수정) 표 — 데이터에 존재하는 용도지역만
        crit_group = QGroupBox("용도지역별 기준 (건폐율/용적률 % - 더블클릭 수정)")
        crit_layout = QVBoxLayout()
        self.crit_table = QTableWidget()
        self.crit_table.setColumnCount(3)
        self.crit_table.setHorizontalHeaderLabels(
            ["용도지역", "건폐율(%)", "용적률(%)"])
        self.crit_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.crit_table.setMaximumHeight(150)
        self.crit_table.itemChanged.connect(self.on_criteria_changed)
        crit_layout.addWidget(self.crit_table)
        crit_group.setLayout(crit_layout)
        layout.addWidget(crit_group)

        # 결과 표
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["PNU", "지번", "용도지역", "대지면적(m2)", "건폐율(%)",
             "용적률(%)", "건축면적(m2)", "허용연면적(m2)", "추정층수"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        self.summary_label = QLabel("분석 전")
        self.summary_label.setStyleSheet(
            "font-weight: bold; color: #2c3e50;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        note = QLabel(
            "※ 법정 상한 기반 참고용 추정치입니다. 지구단위계획·고도지구·일조 "
            "사선제한 등으로 실제 가용 용량은 더 작을 수 있습니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(note)

    # ------------------------------------------------------------------
    def set_land_info(self, cadastral_items, land_use_items):
        self.cadastral_items = cadastral_items or []
        self.land_use_items = land_use_items or []

    def reset_criteria(self):
        self.overrides = {}
        if self.rows:
            self.run_analysis()
        else:
            self.populate_criteria([])

    def _zone_keys_in_data(self):
        keys = []
        seen = set()
        for r in self.rows:
            key = r['zone_key']
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    def populate_criteria(self, zone_keys):
        self._updating = True
        try:
            self.crit_table.setRowCount(len(zone_keys))
            for i, key in enumerate(zone_keys):
                std = BCR_FAR_BY_ZONE.get(key, {'bcr': 0, 'far': 0})
                ov = self.overrides.get(key, {})
                bcr = ov.get('bcr', std['bcr'])
                far = ov.get('far', std['far'])
                name_item = QTableWidgetItem(key)
                name_item.setFlags(Qt.ItemIsEnabled)
                self.crit_table.setItem(i, 0, name_item)
                self.crit_table.setItem(
                    i, 1, QTableWidgetItem(f"{float(bcr):.0f}"))
                self.crit_table.setItem(
                    i, 2, QTableWidgetItem(f"{float(far):.0f}"))
        finally:
            self._updating = False

    def on_criteria_changed(self, item):
        if self._updating or item.column() not in (1, 2):
            return
        key_item = self.crit_table.item(item.row(), 0)
        if key_item is None:
            return
        key = key_item.text()
        try:
            value = float(item.text().replace(',', '').strip())
        except ValueError:
            return
        ov = self.overrides.setdefault(key, {
            'bcr': BCR_FAR_BY_ZONE.get(key, {}).get('bcr', 0),
            'far': BCR_FAR_BY_ZONE.get(key, {}).get('far', 0),
        })
        ov['bcr' if item.column() == 1 else 'far'] = value

    def run_analysis(self):
        if not self.cadastral_items:
            QMessageBox.warning(self, "데이터 없음", "먼저 토지정보를 조회하세요.")
            return
        self.rows = self.analyzer.analyze(
            self.cadastral_items, self.land_use_items, self.overrides)
        self.agg = self.analyzer.aggregate(self.rows)
        self.populate_criteria(self._zone_keys_in_data())
        self.populate_table()
        self.update_summary()

    def populate_table(self):
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            vals = [
                r['pnu'], r['jibun'], r['zone'],
                f"{r['area']:,.1f}", f"{r['bcr']:.0f}", f"{r['far']:.0f}",
                f"{r['build_area']:,.1f}", f"{r['gfa']:,.1f}",
                f"{r['floors']:.1f}" if r['floors'] > 0 else '-',
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if r['far'] <= 0:
                    item.setBackground(QColor(255, 235, 200))
                self.table.setItem(i, c, item)

    def update_summary(self):
        if not self.agg or self.agg['count'] == 0:
            self.summary_label.setText("분석 결과 없음")
            return
        a = self.agg
        unknown = a['count'] - a['count_valid']
        msg = (
            f"총 {a['count']}필지 | 대지면적 합 {a['total_area']:,.0f}m2 "
            f"({a['total_area'] / 10000.0:,.2f}ha) | "
            f"허용연면적 합 {a['total_gfa']:,.0f}m2 | "
            f"면적가중 평균 건폐율 {a['avg_bcr']:.1f}% / 용적률 {a['avg_far']:.1f}%")
        if unknown:
            msg += f" | 용도지역 미확인 {unknown}필지(수동 보정 필요)"
        self.summary_label.setText(msg)

    def show_on_map(self):
        if not self.rows:
            QMessageBox.information(self, "안내", "먼저 [개발용량 분석]을 실행하세요.")
            return
        try:
            self.analyzer.create_result_layer(self.rows)
            QMessageBox.information(
                self, "지도 표시",
                f"'{RESULT_LAYER_NAME}' 레이어를 지도에 추가했습니다 "
                "(용적률 구간별 색상).")
        except Exception as e:
            QMessageBox.warning(self, "오류", f"지도 표시 실패: {e}")

    def export_xlsx(self):
        if not self.rows:
            QMessageBox.information(self, "안내", "먼저 [개발용량 분석]을 실행하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "개발용량 분석 저장", "개발용량분석.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        headers = ["PNU", "지번", "용도지역", "대지면적(m2)", "건폐율(%)",
                   "용적률(%)", "건축면적(m2)", "허용연면적(m2)", "추정층수"]
        data_rows = [
            [r['pnu'], r['jibun'], r['zone'], f"{r['area']:,.1f}",
             f"{r['bcr']:.0f}", f"{r['far']:.0f}", f"{r['build_area']:,.1f}",
             f"{r['gfa']:,.1f}", f"{r['floors']:.1f}" if r['floors'] > 0 else '-']
            for r in self.rows]
        if self.agg:
            data_rows.append([])
            data_rows.append([
                '합계/평균', '', '', f"{self.agg['total_area']:,.1f}",
                f"{self.agg['avg_bcr']:.1f}", f"{self.agg['avg_far']:.1f}",
                f"{self.agg['total_build_area']:,.1f}",
                f"{self.agg['total_gfa']:,.1f}", ''])
        saved = ExportManager.export_table_xlsx(headers, data_rows, path)
        QMessageBox.information(self, "저장 완료", f"저장됨: {saved}")

    def reset(self):
        self.cadastral_items = []
        self.land_use_items = []
        self.rows = []
        self.overrides = {}
        self.agg = None
        self.crit_table.setRowCount(0)
        self.table.setRowCount(0)
        self.summary_label.setText("분석 전")

    def get_report_data(self):
        if not self.rows or not self.agg:
            return None
        a = self.agg
        return {
            'title': '개발성 분석 - 개발용량 (건폐율·용적률, 참고용 추정)',
            'kv': [
                ('총 필지 수', f"{a['count']}"),
                ('대지면적 합',
                 f"{a['total_area']:,.0f} m2 ({a['total_area'] / 10000.0:,.2f} ha)"),
                ('허용 연면적 합', f"{a['total_gfa']:,.0f} m2"),
                ('면적가중 평균 건폐율', f"{a['avg_bcr']:.1f} %"),
                ('면적가중 평균 용적률', f"{a['avg_far']:.1f} %"),
            ],
            'tables': [{
                'title': '필지별 개발용량',
                'headers': ['PNU', '지번', '용도지역', '대지면적(m2)',
                            '건폐율(%)', '용적률(%)', '허용연면적(m2)', '추정층수'],
                'rows': [[r['pnu'], r['jibun'], r['zone'], f"{r['area']:,.1f}",
                          f"{r['bcr']:.0f}", f"{r['far']:.0f}",
                          f"{r['gfa']:,.1f}",
                          f"{r['floors']:.1f}" if r['floors'] > 0 else '-']
                         for r in self.rows[:200]],
            }],
        }
