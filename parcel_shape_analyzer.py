# -*- coding: utf-8 -*-
"""
토지형상 분석 모듈 ('입지분석 > 토지형상 결과수정' 매뉴얼 이식)
- 과소필지 자동판정: 용도지역별 기준면적(사용자 수정 가능) 미만
- 부정형·세장형 자동판정: 최소외접직사각형(OMBB) 기반
  세장형 = 장변/단변 비 초과, 부정형 = 면적/OMBB면적 비 미만
- 결과수정: 판정 셀 클릭 토글 (1클릭 과소 → 2클릭 부정형·세장형 → 3클릭 일반)
- 요약 통계를 정비사업 요건검토 탭으로 전달 (과소필지 비율 자동반영)

주의: 판정 기준은 참고용이며 정비사업 등 법적 판단은 지자체 조례 기준 확인 필요.
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor, QDoubleValidator
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QGridLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QAbstractItemView,
)
from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog,
)

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu
from .cost_calculator import geojson_to_wkt
from .legal_standards import (
    MIN_PARCEL_AREA_BY_ZONE, SHAPE_CRITERIA, SHAPE_STATES,
    match_zone_category,
)

STATE_COLORS = {
    '일반': QColor(255, 255, 255),
    '과소': QColor(255, 224, 178),          # 주황
    '부정형·세장형': QColor(255, 205, 210),  # 빨강
}


class ParcelShapeAnalyzer:
    """필지 형상 자동판정 클래스"""

    def __init__(self):
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def analyze(self, cadastral_items, land_use_items,
                min_area_by_zone=None, criteria=None):
        """필지별 형상 판정.

        반환: [{'pnu','jibun','jimok','zone','zone_cat','area',
                'slender','rect','auto_state','reason'}]
        """
        min_area_by_zone = min_area_by_zone or dict(MIN_PARCEL_AREA_BY_ZONE)
        criteria = criteria or dict(SHAPE_CRITERIA)
        slender_th = float(criteria.get('slender_ratio', 4.0))
        rect_th = float(criteria.get('irregular_rectangularity', 0.5))

        # PNU → 용도지역명 (토지이용계획 prposAreaDstrcCodeNm)
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
            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue
            try:
                geom.transform(transform)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"Shape transform error: {e}", "VWorld", Qgis.Warning)
                continue

            area = geom.area()
            slender = 0.0
            rect = 1.0
            try:
                ombb = geom.orientedMinimumBoundingBox()
                # (geometry, area, angle, width, height)
                ombb_area = ombb[1]
                width = ombb[3]
                height = ombb[4]
                if min(width, height) > 0:
                    slender = max(width, height) / min(width, height)
                if ombb_area > 0:
                    rect = area / ombb_area
            except Exception:
                pass

            zone = zone_by_pnu.get(pnu, '')
            zone_cat = match_zone_category(zone)
            min_area = float(min_area_by_zone.get(
                zone_cat, min_area_by_zone.get('기타', 60.0)))

            if area < min_area:
                auto_state = '과소'
                reason = f"면적 {area:,.1f} < 기준 {min_area:,.0f}m2"
            elif slender > slender_th:
                auto_state = '부정형·세장형'
                reason = f"세장비 {slender:.1f} > 기준 {slender_th:.1f}"
            elif rect < rect_th:
                auto_state = '부정형·세장형'
                reason = f"형상비 {rect:.2f} < 기준 {rect_th:.2f}"
            else:
                auto_state = '일반'
                reason = '-'

            rows.append({
                'pnu': pnu,
                'jibun': jibun,
                'jimok': jimok,
                'zone': zone or '(용도지역 미확인)',
                'zone_cat': zone_cat,
                'area': area,
                'slender': slender,
                'rect': rect,
                'auto_state': auto_state,
                'reason': reason,
            })
        return rows


class ParcelShapeTab(QWidget):
    """토지형상 분석 탭 (입지분석 서브탭)"""

    # 정비사업 탭으로 요약 전달: {'total','small_count','small_ratio',
    #                            'irregular_count','irregular_ratio'}
    shapeResultChanged = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyzer = ParcelShapeAnalyzer()
        self.cadastral_items = []
        self.land_use_items = []
        self.rows = []
        self.overrides = {}  # pnu -> 수동 상태
        self._updating = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 기준 편집
        criteria_group = QGroupBox(
            "판정 기준 (용도지역별 과소필지 기준면적 / 형상 기준 - 수정 가능)")
        grid = QGridLayout()
        self.zone_edits = {}
        for col, (zone, value) in enumerate(MIN_PARCEL_AREA_BY_ZONE.items()):
            grid.addWidget(QLabel(f"{zone}(m2):"), 0, col * 2)
            edit = QLineEdit(str(value))
            edit.setValidator(QDoubleValidator(0, 100000, 1))
            edit.setMaximumWidth(70)
            grid.addWidget(edit, 0, col * 2 + 1)
            self.zone_edits[zone] = edit
        grid.addWidget(QLabel("세장형 기준(장단비):"), 1, 0)
        self.slender_edit = QLineEdit(str(SHAPE_CRITERIA['slender_ratio']))
        self.slender_edit.setValidator(QDoubleValidator(1, 100, 1))
        self.slender_edit.setMaximumWidth(70)
        grid.addWidget(self.slender_edit, 1, 1)
        grid.addWidget(QLabel("부정형 기준(형상비 미만):"), 1, 2)
        self.rect_edit = QLineEdit(
            str(SHAPE_CRITERIA['irregular_rectangularity']))
        self.rect_edit.setValidator(QDoubleValidator(0, 1, 2))
        self.rect_edit.setMaximumWidth(70)
        grid.addWidget(self.rect_edit, 1, 3)
        reset_btn = QPushButton("기준 초기화")
        reset_btn.clicked.connect(self.reset_criteria)
        grid.addWidget(reset_btn, 1, 6)
        self.analyze_btn = QPushButton("분석")
        self.analyze_btn.setStyleSheet("font-weight: bold;")
        self.analyze_btn.clicked.connect(self.run_analysis)
        grid.addWidget(self.analyze_btn, 1, 7)
        criteria_group.setLayout(grid)
        layout.addWidget(criteria_group)

        hint = QLabel(
            "※ 결과수정: '판정' 셀 클릭 토글 - 1클릭 과소 → 2클릭 부정형·세장형 "
            "→ 3클릭 일반 (동일)")
        hint.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(hint)

        # 결과 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["PNU", "지번", "지목", "용도지역", "면적(m2)",
             "세장비", "형상비", "판정 (클릭 토글)"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellClicked.connect(self.on_cell_clicked)
        layout.addWidget(self.table)

        # 요약
        self.summary_label = QLabel("분석 전")
        self.summary_label.setStyleSheet(
            "font-weight: bold; color: #2c3e50;")
        layout.addWidget(self.summary_label)

    # ------------------------------------------------------------------
    def set_land_info(self, cadastral_items, land_use_items):
        self.cadastral_items = cadastral_items or []
        self.land_use_items = land_use_items or []

    def reset_criteria(self):
        for zone, value in MIN_PARCEL_AREA_BY_ZONE.items():
            if zone in self.zone_edits:
                self.zone_edits[zone].setText(str(value))
        self.slender_edit.setText(str(SHAPE_CRITERIA['slender_ratio']))
        self.rect_edit.setText(
            str(SHAPE_CRITERIA['irregular_rectangularity']))

    def _read_float(self, edit, default):
        try:
            return float(edit.text().replace(',', '').strip())
        except (ValueError, AttributeError):
            return default

    def run_analysis(self):
        if not self.cadastral_items:
            QMessageBox.warning(self, "데이터 없음", "먼저 토지정보를 조회하세요.")
            return
        if self.overrides:
            reply = QMessageBox.question(
                self, "수동 수정 보존",
                f"수동으로 수정한 판정 {len(self.overrides)}건이 있습니다.\n"
                "재분석 후에도 유지할까요? (아니오=초기화)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.No:
                self.overrides = {}

        min_area_by_zone = {
            zone: self._read_float(edit, MIN_PARCEL_AREA_BY_ZONE[zone])
            for zone, edit in self.zone_edits.items()}
        criteria = {
            'slender_ratio': self._read_float(
                self.slender_edit, SHAPE_CRITERIA['slender_ratio']),
            'irregular_rectangularity': self._read_float(
                self.rect_edit, SHAPE_CRITERIA['irregular_rectangularity']),
        }
        self.rows = self.analyzer.analyze(
            self.cadastral_items, self.land_use_items,
            min_area_by_zone, criteria)
        self.populate_table()
        self.update_summary()

    def current_state(self, row):
        return self.overrides.get(row['pnu'], row['auto_state'])

    def populate_table(self):
        self._updating = True
        try:
            self.table.setRowCount(len(self.rows))
            for i, row in enumerate(self.rows):
                state = self.current_state(row)
                self.table.setItem(i, 0, QTableWidgetItem(row['pnu']))
                self.table.setItem(i, 1, QTableWidgetItem(row['jibun']))
                self.table.setItem(i, 2, QTableWidgetItem(row['jimok']))
                self.table.setItem(i, 3, QTableWidgetItem(row['zone']))
                self.table.setItem(
                    i, 4, QTableWidgetItem(f"{row['area']:,.1f}"))
                self.table.setItem(
                    i, 5, QTableWidgetItem(f"{row['slender']:.2f}"))
                self.table.setItem(
                    i, 6, QTableWidgetItem(f"{row['rect']:.2f}"))
                state_item = QTableWidgetItem(self._state_label(row, state))
                state_item.setBackground(
                    STATE_COLORS.get(state, QColor(255, 255, 255)))
                self.table.setItem(i, 7, state_item)
        finally:
            self._updating = False

    def _state_label(self, row, state):
        label = state
        if row['pnu'] in self.overrides:
            label += " (수정)"
        elif state != '일반':
            label += f" [{row['reason']}]"
        return label

    def on_cell_clicked(self, row_idx, col):
        """판정 셀 클릭 토글: 일반→과소→부정형·세장형→일반"""
        if col != 7 or self._updating or row_idx >= len(self.rows):
            return
        row = self.rows[row_idx]
        state = self.current_state(row)
        next_state = SHAPE_STATES[
            (SHAPE_STATES.index(state) + 1) % len(SHAPE_STATES)]
        if next_state == row['auto_state']:
            self.overrides.pop(row['pnu'], None)
        else:
            self.overrides[row['pnu']] = next_state
        item = self.table.item(row_idx, 7)
        item.setText(self._state_label(row, next_state))
        item.setBackground(
            STATE_COLORS.get(next_state, QColor(255, 255, 255)))
        self.update_summary()

    def update_summary(self):
        total = len(self.rows)
        if total == 0:
            self.summary_label.setText("분석 결과 없음")
            self.shapeResultChanged.emit({})
            return
        small = sum(1 for r in self.rows if self.current_state(r) == '과소')
        irregular = sum(
            1 for r in self.rows
            if self.current_state(r) == '부정형·세장형')
        small_ratio = small / total * 100.0
        irregular_ratio = irregular / total * 100.0
        self.summary_label.setText(
            f"총 {total}필지 | 과소필지 {small}필지 ({small_ratio:.1f}%) | "
            f"부정형·세장형 {irregular}필지 ({irregular_ratio:.1f}%) | "
            f"수동수정 {len(self.overrides)}건")
        self.shapeResultChanged.emit({
            'total': total,
            'small_count': small,
            'small_ratio': small_ratio,
            'irregular_count': irregular,
            'irregular_ratio': irregular_ratio,
        })

    def reset(self):
        self.cadastral_items = []
        self.land_use_items = []
        self.rows = []
        self.overrides = {}
        self.table.setRowCount(0)
        self.summary_label.setText("분석 전")

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if not self.rows:
            return None
        total = len(self.rows)
        small = sum(1 for r in self.rows if self.current_state(r) == '과소')
        irregular = sum(1 for r in self.rows
                        if self.current_state(r) == '부정형·세장형')
        return {
            'title': '입지분석 - 토지형상 (참고용 추정)',
            'kv': [
                ('총 필지 수', f"{total}"),
                ('과소필지', f"{small}필지 ({small / total * 100:.1f}%)"),
                ('부정형·세장형',
                 f"{irregular}필지 ({irregular / total * 100:.1f}%)"),
                ('수동 수정', f"{len(self.overrides)}건"),
            ],
            'tables': [{
                'title': '필지별 형상 판정',
                'headers': ['PNU', '지번', '용도지역', '면적(m2)',
                            '세장비', '형상비', '판정'],
                'rows': [[r['pnu'], r['jibun'], r['zone'],
                          f"{r['area']:,.1f}", f"{r['slender']:.2f}",
                          f"{r['rect']:.2f}",
                          self.current_state(r)
                          + (' (수정)' if r['pnu'] in self.overrides else '')]
                         for r in self.rows[:200]],
            }],
        }
