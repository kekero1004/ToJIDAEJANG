# -*- coding: utf-8 -*-
"""
개발가능지 분석 모듈 ('입지분석 > 개발가능지 분석' 매뉴얼 이식)
- 표고분석: 기준 해발고도 + 개발가능 표고차 초과 셀 → 개발 곤란
- 경사분석: 급경사 기준(도) 이상 셀 → 개발 곤란
- 생태자연도/식생영급/국토환경성: 등급 수동 입력(전역) 기반 제외 판단
  (VWorld에서 환경 등급 레이어의 속성 조회가 불안정하여 수동 입력 폴백을
   기본 시나리오로 제공 - 분석 결과에 출처 표기)
- 법률 기준 초기값 자동 적용 + 사용자 수정 + [초기화] (동일)
- 결과: 등급별 셀 수/면적/비율 표 + 메모리 레이어 주제도

고도 출처: OpenTopoData srtm30m (참고용 정밀도, 일일 호출 한도 있음)
"""

import math

from qgis.PyQt.QtGui import QColor, QDoubleValidator
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QGridLayout, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QSpinBox, QComboBox, QApplication,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsPointXY, QgsCoordinateReferenceSystem,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsFillSymbol,
    Qgis, QgsMessageLog,
)
from PyQt5.QtCore import QVariant

from .legal_standards import DEV_FEASIBILITY_DEFAULTS

RESULT_LAYER_NAME = "개발가능지_분석"

GRADE_COLORS = {
    '개발가능': '#2ecc71',
    '표고초과': '#3498db',
    '급경사': '#e74c3c',
    '환경제외': '#9b59b6',
}


class FeasibilityAnalyzer:
    """개발가능지 분석 클래스 - 구조화 격자 셀별 판정"""

    def __init__(self, terrain_analyzer):
        self.terrain_analyzer = terrain_analyzer
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def analyze(self, geometry_wgs84, criteria, sample_count=100):
        """격자 셀별 개발가능성 판정.

        criteria: {'base_elevation': float|None, 'dev_elevation_limit': float,
                   'steep_slope_deg': float, 'env_excluded': bool}
        반환: {'cells': [{'ring': [(x,y)...], 'grade', 'elev', 'slope'}],
               'area_by_grade': {grade: m2}, 'count_by_grade': {grade: n},
               'cell_area': m2, 'criteria_used': dict} 또는 None
        """
        if geometry_wgs84 is None or geometry_wgs84.isEmpty():
            return None
        try:
            bbox = geometry_wgs84.boundingBox()
            center = bbox.center()
            grid_size = max(int(math.sqrt(max(4, min(sample_count, 200)))), 2)
            x_step = (bbox.xMaximum() - bbox.xMinimum()) / grid_size
            y_step = (bbox.yMaximum() - bbox.yMinimum()) / grid_size

            lat_rad = math.radians(center.y())
            dx_m = x_step * 111000 * math.cos(lat_rad)
            dy_m = y_step * 111000
            cell_area = abs(dx_m * dy_m)
            if dx_m == 0 or dy_m == 0:
                return None

            # 격자 노드 고도 조회 (terrain_analyzer 배치 재사용)
            nodes = []
            for i in range(grid_size + 1):
                for j in range(grid_size + 1):
                    x = bbox.xMinimum() + i * x_step
                    y = bbox.yMinimum() + j * y_step
                    nodes.append((i, j, x, y))
            locations = [f"{y},{x}" for (_, _, x, y) in nodes]
            elevations = self.terrain_analyzer._get_elevations_batch(locations)
            if not elevations or len(elevations) != len(nodes):
                return None
            elev_grid = {(i, j): e for (i, j, _, _), e
                         in zip(nodes, elevations)}

            base_elev = criteria.get('base_elevation')
            if base_elev is None:
                base_elev = min(elevations)
            elev_limit = float(criteria.get(
                'dev_elevation_limit',
                DEV_FEASIBILITY_DEFAULTS['dev_elevation_limit']))
            steep = float(criteria.get(
                'steep_slope_deg',
                DEV_FEASIBILITY_DEFAULTS['steep_slope_deg']))
            env_excluded = bool(criteria.get('env_excluded', False))

            cells = []
            area_by_grade = {g: 0.0 for g in GRADE_COLORS}
            count_by_grade = {g: 0 for g in GRADE_COLORS}

            for i in range(grid_size):
                for j in range(grid_size):
                    corners = [(i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)]
                    if any(c not in elev_grid for c in corners):
                        continue
                    cx = bbox.xMinimum() + (i + 0.5) * x_step
                    cy = bbox.yMinimum() + (j + 0.5) * y_step
                    if not geometry_wgs84.contains(
                            QgsGeometry.fromPointXY(QgsPointXY(cx, cy))):
                        continue

                    z00 = elev_grid[(i, j)]
                    z10 = elev_grid[(i + 1, j)]
                    z01 = elev_grid[(i, j + 1)]
                    z11 = elev_grid[(i + 1, j + 1)]
                    cell_elev = (z00 + z10 + z01 + z11) / 4.0
                    dz_dx = ((z10 + z11) - (z00 + z01)) / (2 * dx_m)
                    dz_dy = ((z01 + z11) - (z00 + z10)) / (2 * dy_m)
                    slope_deg = math.degrees(
                        math.atan(math.sqrt(dz_dx ** 2 + dz_dy ** 2)))

                    if env_excluded:
                        grade = '환경제외'
                    elif cell_elev > base_elev + elev_limit:
                        grade = '표고초과'
                    elif slope_deg >= steep:
                        grade = '급경사'
                    else:
                        grade = '개발가능'

                    x0 = bbox.xMinimum() + i * x_step
                    y0 = bbox.yMinimum() + j * y_step
                    ring = [
                        (x0, y0), (x0 + x_step, y0),
                        (x0 + x_step, y0 + y_step), (x0, y0 + y_step),
                        (x0, y0),
                    ]
                    cells.append({
                        'ring': ring,
                        'grade': grade,
                        'elev': cell_elev,
                        'slope': slope_deg,
                    })
                    area_by_grade[grade] += cell_area
                    count_by_grade[grade] += 1

            if not cells:
                return None
            return {
                'cells': cells,
                'area_by_grade': area_by_grade,
                'count_by_grade': count_by_grade,
                'cell_area': cell_area,
                'criteria_used': {
                    'base_elevation': base_elev,
                    'dev_elevation_limit': elev_limit,
                    'steep_slope_deg': steep,
                    'env_excluded': env_excluded,
                },
            }
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Feasibility analysis error: {e}", "VWorld", Qgis.Warning)
            return None

    def create_result_layer(self, result):
        """분석 결과 → 메모리 레이어 (등급별 색상 주제도)"""
        old = None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == RESULT_LAYER_NAME:
                old = layer.id()
        if old:
            QgsProject.instance().removeMapLayer(old)

        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326", RESULT_LAYER_NAME, "memory")
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField("grade", QVariant.String),
            QgsField("elev_m", QVariant.Double),
            QgsField("slope_deg", QVariant.Double),
        ])
        layer.updateFields()

        features = []
        for cell in result['cells']:
            qf = QgsFeature(layer.fields())
            qf.setGeometry(QgsGeometry.fromPolygonXY(
                [[QgsPointXY(x, y) for x, y in cell['ring']]]))
            qf.setAttributes(
                [cell['grade'], cell['elev'], cell['slope']])
            features.append(qf)
        provider.addFeatures(features)
        layer.updateExtents()

        categories = []
        for grade, color in GRADE_COLORS.items():
            fill = QColor(color)
            fill.setAlpha(120)
            symbol = QgsFillSymbol.createSimple({
                'color': fill.name(QColor.HexArgb),
                'outline_color': '#666666',
                'outline_width': '0.1',
            })
            categories.append(QgsRendererCategory(grade, symbol, grade))
        layer.setRenderer(QgsCategorizedSymbolRenderer("grade", categories))
        QgsProject.instance().addMapLayer(layer)
        layer.triggerRepaint()
        return layer


class FeasibilityTab(QWidget):
    """개발가능지 분석 탭 (입지분석 서브탭)"""

    def __init__(self, terrain_analyzer, parent=None):
        super().__init__(parent)
        self.analyzer = FeasibilityAnalyzer(terrain_analyzer)
        self.geometry_wgs84 = None
        self.terrain_result = None
        self.result = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        criteria_group = QGroupBox(
            "분석 기준 (법률 기준 초기값 자동 적용 - 수정 가능)")
        grid = QGridLayout()

        grid.addWidget(QLabel("기준 해발고도(m):"), 0, 0)
        self.base_elev_edit = QLineEdit("")
        self.base_elev_edit.setPlaceholderText("비우면 대상지 최저표고")
        self.base_elev_edit.setValidator(QDoubleValidator(-500, 3000, 1))
        grid.addWidget(self.base_elev_edit, 0, 1)

        grid.addWidget(QLabel("개발가능 표고차(m):"), 0, 2)
        self.elev_limit_edit = QLineEdit(
            str(DEV_FEASIBILITY_DEFAULTS['dev_elevation_limit']))
        self.elev_limit_edit.setValidator(QDoubleValidator(0, 1000, 1))
        grid.addWidget(self.elev_limit_edit, 0, 3)

        grid.addWidget(QLabel("급경사 기준(도):"), 1, 0)
        self.steep_edit = QLineEdit(
            str(DEV_FEASIBILITY_DEFAULTS['steep_slope_deg']))
        self.steep_edit.setValidator(QDoubleValidator(0, 90, 1))
        grid.addWidget(self.steep_edit, 1, 1)

        grid.addWidget(QLabel("격자 표본 수:"), 1, 2)
        self.sample_spin = QSpinBox()
        self.sample_spin.setRange(25, 200)
        self.sample_spin.setValue(100)
        grid.addWidget(self.sample_spin, 1, 3)

        # 환경 등급 (수동 입력 - 전역)
        grid.addWidget(QLabel("생태자연도 등급:"), 2, 0)
        self.eco_combo = QComboBox()
        self.eco_combo.addItems(["미입력", "1등급", "2등급", "3등급", "별도관리"])
        grid.addWidget(self.eco_combo, 2, 1)

        grid.addWidget(QLabel("식생 영급:"), 2, 2)
        self.veg_combo = QComboBox()
        self.veg_combo.addItems(
            ["미입력", "1영급", "2영급", "3영급", "4영급", "5영급 이상"])
        grid.addWidget(self.veg_combo, 2, 3)

        grid.addWidget(QLabel("국토환경성 등급:"), 3, 0)
        self.env_combo = QComboBox()
        self.env_combo.addItems(["미입력", "1등급", "2등급", "3등급", "4등급", "5등급"])
        grid.addWidget(self.env_combo, 3, 1)

        reset_btn = QPushButton("초기화 (법률 기준값)")
        reset_btn.clicked.connect(self.reset_criteria)
        grid.addWidget(reset_btn, 4, 0, 1, 2)
        self.analyze_btn = QPushButton("분석")
        self.analyze_btn.setStyleSheet("font-weight: bold;")
        self.analyze_btn.clicked.connect(self.run_analysis)
        grid.addWidget(self.analyze_btn, 4, 2)
        self.layer_btn = QPushButton("지도 표시 (주제도 레이어)")
        self.layer_btn.clicked.connect(self.show_layer)
        grid.addWidget(self.layer_btn, 4, 3)

        criteria_group.setLayout(grid)
        layout.addWidget(criteria_group)

        env_note = QLabel(
            "※ 생태자연도 1등급 / 식생 5영급 이상 / 국토환경성 1등급 입력 시 "
            "전체 '환경제외'로 판정합니다 (환경 등급은 환경공간정보서비스 "
            "egis.me.go.kr 등에서 확인 후 수동 입력).")
        env_note.setWordWrap(True)
        env_note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(env_note)

        # 결과 표
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["판정 등급", "셀 수", "면적(m2)", "비율(%)"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.setMaximumHeight(170)
        layout.addWidget(self.table)

        self.summary_label = QLabel("분석 전 - 토지정보 조회 후 [분석]을 실행하세요.")
        self.summary_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        note = QLabel(
            "※ OpenTopoData(SRTM 30m) 기반 참고용 추정 - 정밀 분석은 "
            "수치표고모델(DEM) 자료 사용 권장. 법적 효력 없음.")
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    # ------------------------------------------------------------------
    def set_context(self, geometry_wgs84, terrain_result=None):
        self.geometry_wgs84 = geometry_wgs84
        self.terrain_result = terrain_result
        if terrain_result and terrain_result.get('min_elevation'):
            self.base_elev_edit.setPlaceholderText(
                f"비우면 최저표고 {terrain_result['min_elevation']:.0f}m")

    def reset_criteria(self):
        self.base_elev_edit.setText("")
        self.elev_limit_edit.setText(
            str(DEV_FEASIBILITY_DEFAULTS['dev_elevation_limit']))
        self.steep_edit.setText(
            str(DEV_FEASIBILITY_DEFAULTS['steep_slope_deg']))
        self.eco_combo.setCurrentIndex(0)
        self.veg_combo.setCurrentIndex(0)
        self.env_combo.setCurrentIndex(0)

    def _env_excluded(self):
        """환경 등급 입력값으로 전역 제외 여부 판단"""
        eco = self.eco_combo.currentIndex()       # 1=1등급
        veg = self.veg_combo.currentIndex()       # 5=5영급 이상
        env = self.env_combo.currentIndex()       # 1=1등급
        reasons = []
        if eco == 1 or eco == 4:  # 1등급 또는 별도관리
            reasons.append(f"생태자연도 {self.eco_combo.currentText()}")
        if veg >= 5:
            reasons.append(f"식생 {self.veg_combo.currentText()}")
        if env == 1:
            reasons.append(f"국토환경성 {self.env_combo.currentText()}")
        return (len(reasons) > 0, reasons)

    def run_analysis(self):
        if self.geometry_wgs84 is None or self.geometry_wgs84.isEmpty():
            QMessageBox.warning(
                self, "대상지 없음",
                "먼저 토지정보를 조회하세요 (선택 폴리곤 또는 구역계).")
            return
        env_excluded, env_reasons = self._env_excluded()

        base_text = self.base_elev_edit.text().strip()
        criteria = {
            'base_elevation': float(base_text) if base_text else None,
            'dev_elevation_limit': float(
                self.elev_limit_edit.text() or
                DEV_FEASIBILITY_DEFAULTS['dev_elevation_limit']),
            'steep_slope_deg': float(
                self.steep_edit.text() or
                DEV_FEASIBILITY_DEFAULTS['steep_slope_deg']),
            'env_excluded': env_excluded,
        }
        self.analyze_btn.setEnabled(False)
        self.summary_label.setText("고도 조회 및 분석 중... (잠시 기다려 주세요)")
        QApplication.processEvents()
        try:
            self.result = self.analyzer.analyze(
                self.geometry_wgs84, criteria, self.sample_spin.value())
        finally:
            self.analyze_btn.setEnabled(True)

        if self.result is None:
            self.summary_label.setText(
                "분석 실패 - 고도 API 응답 없음 (일일 한도 초과 또는 네트워크 "
                "오류). 잠시 후 다시 시도하세요.")
            return

        total_area = sum(self.result['area_by_grade'].values())
        total_count = sum(self.result['count_by_grade'].values())
        self.table.setRowCount(len(GRADE_COLORS))
        for i, grade in enumerate(GRADE_COLORS):
            count = self.result['count_by_grade'].get(grade, 0)
            area = self.result['area_by_grade'].get(grade, 0.0)
            ratio = (area / total_area * 100.0) if total_area > 0 else 0.0
            self.table.setItem(i, 0, QTableWidgetItem(grade))
            self.table.setItem(i, 1, QTableWidgetItem(f"{count:,}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{area:,.1f}"))
            self.table.setItem(i, 3, QTableWidgetItem(f"{ratio:.1f}%"))
            color = QColor(GRADE_COLORS[grade])
            color.setAlpha(70)
            for c in range(4):
                self.table.item(i, c).setBackground(color)

        used = self.result['criteria_used']
        dev_area = self.result['area_by_grade'].get('개발가능', 0.0)
        dev_ratio = (dev_area / total_area * 100.0) if total_area > 0 else 0.0
        summary = (
            f"개발가능지: {dev_area:,.1f} m2 ({dev_ratio:.1f}%) / "
            f"분석 셀 {total_count}개\n"
            f"적용 기준: 기준표고 {used['base_elevation']:.1f}m + "
            f"표고차 {used['dev_elevation_limit']:.0f}m, "
            f"급경사 {used['steep_slope_deg']:.0f}도 이상 제외")
        if env_reasons:
            summary += f"\n환경 제외 사유(수동 입력): {', '.join(env_reasons)}"
        self.summary_label.setText(summary)

    def show_layer(self):
        if self.result is None:
            QMessageBox.information(self, "안내", "먼저 [분석]을 실행하세요.")
            return
        self.analyzer.create_result_layer(self.result)
        QMessageBox.information(
            self, "주제도 생성",
            f"'{RESULT_LAYER_NAME}' 레이어가 지도에 추가되었습니다.\n"
            "(녹색=개발가능, 파랑=표고초과, 빨강=급경사, 보라=환경제외)")

    def reset(self):
        self.geometry_wgs84 = None
        self.terrain_result = None
        self.result = None
        self.table.setRowCount(0)
        self.reset_criteria()
        self.summary_label.setText(
            "분석 전 - 토지정보 조회 후 [분석]을 실행하세요.")

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if self.result is None:
            return None
        used = self.result['criteria_used']
        total_area = sum(self.result['area_by_grade'].values())
        rows = []
        for grade in GRADE_COLORS:
            count = self.result['count_by_grade'].get(grade, 0)
            area = self.result['area_by_grade'].get(grade, 0.0)
            ratio = (area / total_area * 100.0) if total_area > 0 else 0.0
            rows.append([grade, f"{count:,}", f"{area:,.1f}",
                         f"{ratio:.1f}%"])
        return {
            'title': '입지분석 - 개발가능지 분석 (참고용 추정)',
            'kv': [
                ('기준 해발고도', f"{used['base_elevation']:,.1f} m"),
                ('개발가능 표고차', f"{used['dev_elevation_limit']:,.0f} m"),
                ('급경사 기준', f"{used['steep_slope_deg']:,.0f} 도 이상 제외"),
                ('환경 제외 적용', '예' if used['env_excluded'] else '아니오'),
            ],
            'tables': [{
                'title': '판정 등급별 면적',
                'headers': ['판정 등급', '셀 수', '면적(m2)', '비율(%)'],
                'rows': rows,
            }],
        }
