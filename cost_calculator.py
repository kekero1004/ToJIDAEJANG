# -*- coding: utf-8 -*-
"""
기반비용 산출 모듈 (PSS 인허가 사전진단 '토지 시뮬레이션 > 기반비용 산출' 참조)
- 농지보전부담금 (농지법 시행령)
- 대체산림자원조성비 (산지관리법)
- 개발부담금 (개발이익 환수에 관한 법률) 간이 추정

주의: 모든 산출액은 공개 산식 기반의 '참고용 추정치'이며, 실제 부과액은
      관할 행정청의 고시 단가/감면/가산 기준에 따라 달라질 수 있다.

QGIS 3.36.2 / PyQGIS 호환. 외부 API 호출 없이 이미 수집된 연속지적도
(지목·개별공시지가·지오메트리)와 선택 폴리곤만으로 계산한다.
"""

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox
)
from qgis.PyQt.QtGui import QDoubleValidator
from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog
)

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu


# 농지로 분류되는 지목 (농지법 제2조)
FARMLAND_JIMOK = {'전', '답', '과수원', '목장용지'}
# 산지로 분류되는 지목 (산지관리법)
FOREST_JIMOK = {'임야'}

# 대체산림자원조성비 단위면적당 금액 (원/m2) - 산림청 고시 기준 (연도별 변동, 기본값 예시)
FOREST_UNIT_RATES = {
    '준보전산지': 7260,
    '보전산지': 9430,
    '산지전용·일시사용제한지역': 14520,
}


def geojson_to_wkt(geom_data):
    """GeoJSON 지오메트리를 WKT로 변환 (dashboard_widget과 동일 규약)"""
    try:
        geom_type = geom_data.get('type', '')
        coordinates = geom_data.get('coordinates', [])

        if geom_type == 'Polygon':
            rings = []
            for ring in coordinates:
                points = ', '.join([f"{c[0]} {c[1]}" for c in ring])
                rings.append(f"({points})")
            return f"POLYGON({', '.join(rings)})"
        elif geom_type == 'MultiPolygon':
            polygons = []
            for polygon in coordinates:
                rings = []
                for ring in polygon:
                    points = ', '.join([f"{c[0]} {c[1]}" for c in ring])
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON({', '.join(polygons)})"
        return ""
    except Exception:
        return ""


class CostCalculator:
    """기반비용(부담금) 산출 클래스"""

    def __init__(self):
        # 산출 파라미터 (UI에서 변경 가능)
        self.farmland_rate = 0.30           # 농지보전부담금 부과율 (공시지가의 30%)
        self.farmland_cap_per_m2 = 50000    # 농지보전부담금 m2당 상한액 (원)
        self.forest_unit_rate = FOREST_UNIT_RATES['준보전산지']  # 대체산림 단위금액(원/m2)
        self.forest_jiga_add_rate = 0.01    # 대체산림 공시지가 가산율 (1%)
        self.dev_charge_rate = 0.25         # 개발부담금 부과율 (25%)

    def _inclusion_area_5186(self, geom_data, selected_geom_5186, transform_to_5186):
        """필지 GeoJSON 지오메트리의 (전체면적, 편입면적) m2 산출 - EPSG:5186 기준"""
        wkt = geojson_to_wkt(geom_data)
        if not wkt:
            return (0.0, 0.0)
        parcel_geom = QgsGeometry.fromWkt(wkt)
        if parcel_geom.isEmpty():
            return (0.0, 0.0)
        parcel_5186 = QgsGeometry(parcel_geom)
        parcel_5186.transform(transform_to_5186)
        total_area = parcel_5186.area()

        if selected_geom_5186 is not None and not selected_geom_5186.isEmpty():
            inter = parcel_5186.intersection(selected_geom_5186)
            incl_area = inter.area() if not inter.isEmpty() else 0.0
        else:
            incl_area = total_area
        return (total_area, incl_area)

    def calculate(self, cadastral_items, selected_geometry_wgs84):
        """
        연속지적도 데이터로 부담금 산출.
        반환: {'parcels': [...], 'summary': {...}}
        편입면적(선택 폴리곤과 교차한 면적) 기준으로 계산한다.
        """
        result = {
            'parcels': [],
            'summary': {
                'farmland_area': 0.0, 'farmland_charge': 0.0,
                'forest_area': 0.0, 'forest_charge': 0.0,
                'other_area': 0.0,
                'total_area': 0.0, 'total_charge': 0.0,
            }
        }

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")
        transform_to_5186 = QgsCoordinateTransform(crs_wgs84, crs_5186, QgsProject.instance())

        selected_geom_5186 = None
        if selected_geometry_wgs84 is not None and not selected_geometry_wgs84.isEmpty():
            selected_geom_5186 = QgsGeometry(selected_geometry_wgs84)
            selected_geom_5186.transform(transform_to_5186)

        s = result['summary']

        for item in cadastral_items:
            props = item.get('properties', {})
            geom_data = item.get('geometry', {})
            if not geom_data:
                continue

            jibun = props.get('jibun', '')
            pnu = props.get('pnu', '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)

            try:
                jiga = float(props.get('jiga', 0) or 0)
            except (ValueError, TypeError):
                jiga = 0.0

            try:
                _, incl_area = self._inclusion_area_5186(
                    geom_data, selected_geom_5186, transform_to_5186)
            except Exception as e:
                QgsMessageLog.logMessage(f"Cost area error: {e}", "VWorld", Qgis.Warning)
                continue

            if incl_area <= 0:
                continue

            category = '기타'
            charge = 0.0
            charge_type = ''

            if jimok in FARMLAND_JIMOK:
                category = '농지'
                # 농지보전부담금 = min(공시지가 × 부과율, m2당 상한) × 편입면적
                per_m2 = min(jiga * self.farmland_rate, self.farmland_cap_per_m2)
                charge = per_m2 * incl_area
                charge_type = '농지보전부담금'
                s['farmland_area'] += incl_area
                s['farmland_charge'] += charge
            elif jimok in FOREST_JIMOK:
                category = '산지'
                # 대체산림자원조성비 = (단위면적금액 + 공시지가 × 가산율) × 편입면적
                per_m2 = self.forest_unit_rate + jiga * self.forest_jiga_add_rate
                charge = per_m2 * incl_area
                charge_type = '대체산림자원조성비'
                s['forest_area'] += incl_area
                s['forest_charge'] += charge
            else:
                s['other_area'] += incl_area

            s['total_area'] += incl_area
            s['total_charge'] += charge

            result['parcels'].append({
                'pnu': pnu,
                'jibun': jibun,
                'jimok': jimok,
                'category': category,
                'incl_area': incl_area,
                'jiga': jiga,
                'charge_type': charge_type,
                'charge': charge,
            })

        return result


class CostAnalysisTab(QWidget):
    """기반비용 산출 탭 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calculator = CostCalculator()
        self.cadastral_items = []
        self.selected_geometry = None
        self.last_result = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 산출 파라미터 설정
        param_group = QGroupBox("산출 기준 (참고용 추정 - 필요시 단가 조정)")
        param_layout = QGridLayout()

        param_layout.addWidget(QLabel("농지보전부담금 부과율(%):"), 0, 0)
        self.farmland_rate_edit = QLineEdit("30")
        self.farmland_rate_edit.setValidator(QDoubleValidator(0, 100, 2))
        param_layout.addWidget(self.farmland_rate_edit, 0, 1)

        param_layout.addWidget(QLabel("농지 m2당 상한(원):"), 0, 2)
        self.farmland_cap_edit = QLineEdit("50000")
        self.farmland_cap_edit.setValidator(QDoubleValidator(0, 1e9, 0))
        param_layout.addWidget(self.farmland_cap_edit, 0, 3)

        param_layout.addWidget(QLabel("산지 구분:"), 1, 0)
        self.forest_type_combo = QComboBox()
        self.forest_type_combo.addItems(list(FOREST_UNIT_RATES.keys()))
        self.forest_type_combo.currentTextChanged.connect(self._on_forest_type_changed)
        param_layout.addWidget(self.forest_type_combo, 1, 1)

        param_layout.addWidget(QLabel("대체산림 단위금액(원/m2):"), 1, 2)
        self.forest_unit_edit = QLineEdit(str(FOREST_UNIT_RATES['준보전산지']))
        self.forest_unit_edit.setValidator(QDoubleValidator(0, 1e9, 0))
        param_layout.addWidget(self.forest_unit_edit, 1, 3)

        self.calc_btn = QPushButton("기반비용 계산")
        self.calc_btn.clicked.connect(self.run_calculation)
        param_layout.addWidget(self.calc_btn, 2, 0, 1, 4)

        param_group.setLayout(param_layout)
        layout.addWidget(param_group)

        # 요약
        summary_group = QGroupBox("부담금 산출 요약 (편입면적 기준)")
        summary_layout = QGridLayout()
        self.farmland_label = QLabel("농지보전부담금: - 원 (면적 - m2)")
        self.forest_label = QLabel("대체산림자원조성비: - 원 (면적 - m2)")
        self.other_label = QLabel("기타 지목 면적: - m2")
        self.total_label = QLabel("기반비용 합계: - 원")
        self.total_label.setStyleSheet("font-weight: bold; color: #c0392b; font-size: 13px;")
        summary_layout.addWidget(self.farmland_label, 0, 0)
        summary_layout.addWidget(self.forest_label, 0, 1)
        summary_layout.addWidget(self.other_label, 1, 0)
        summary_layout.addWidget(self.total_label, 1, 1)
        summary_group.setLayout(summary_layout)
        layout.addWidget(summary_group)

        # 개발부담금 간이 추정
        dev_group = QGroupBox("개발부담금 간이 추정 (개발이익 환수법)")
        dev_layout = QHBoxLayout()
        dev_layout.addWidget(QLabel("추정 개발이익(원):"))
        self.dev_profit_edit = QLineEdit("0")
        self.dev_profit_edit.setValidator(QDoubleValidator(0, 1e15, 0))
        dev_layout.addWidget(self.dev_profit_edit)
        dev_layout.addWidget(QLabel("부과율(%):"))
        self.dev_rate_edit = QLineEdit("25")
        self.dev_rate_edit.setValidator(QDoubleValidator(0, 100, 2))
        dev_layout.addWidget(self.dev_rate_edit)
        self.dev_calc_btn = QPushButton("개발부담금 계산")
        self.dev_calc_btn.clicked.connect(self.calc_dev_charge)
        dev_layout.addWidget(self.dev_calc_btn)
        self.dev_result_label = QLabel("개발부담금: - 원")
        self.dev_result_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        dev_layout.addWidget(self.dev_result_label)
        dev_group.setLayout(dev_layout)
        layout.addWidget(dev_group)

        # 필지별 상세 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["PNU", "지번", "지목", "구분", "편입면적(m2)", "공시지가(원/m2)", "부담금(원)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

    def _on_forest_type_changed(self, text):
        rate = FOREST_UNIT_RATES.get(text)
        if rate is not None:
            self.forest_unit_edit.setText(str(rate))

    def set_land_info(self, cadastral_items, selected_geometry_wgs84):
        """조회 완료 후 토지 정보 설정"""
        self.cadastral_items = cadastral_items or []
        self.selected_geometry = selected_geometry_wgs84

    def _read_float(self, edit, default):
        try:
            return float(edit.text().replace(',', '').strip())
        except (ValueError, AttributeError):
            return default

    def run_calculation(self):
        if not self.cadastral_items:
            QMessageBox.warning(self, "경고", "먼저 토지 정보를 조회하세요.")
            return

        # 파라미터 반영
        self.calculator.farmland_rate = self._read_float(self.farmland_rate_edit, 30) / 100.0
        self.calculator.farmland_cap_per_m2 = self._read_float(self.farmland_cap_edit, 50000)
        self.calculator.forest_unit_rate = self._read_float(
            self.forest_unit_edit, FOREST_UNIT_RATES['준보전산지'])

        result = self.calculator.calculate(self.cadastral_items, self.selected_geometry)
        self.last_result = result
        s = result['summary']

        self.farmland_label.setText(
            f"농지보전부담금: {s['farmland_charge']:,.0f} 원 (면적 {s['farmland_area']:,.2f} m2)")
        self.forest_label.setText(
            f"대체산림자원조성비: {s['forest_charge']:,.0f} 원 (면적 {s['forest_area']:,.2f} m2)")
        self.other_label.setText(f"기타 지목 면적: {s['other_area']:,.2f} m2")
        self.total_label.setText(f"기반비용 합계: {s['total_charge']:,.0f} 원")

        parcels = result['parcels']
        self.table.setRowCount(len(parcels))
        for i, p in enumerate(parcels):
            self.table.setItem(i, 0, QTableWidgetItem(str(p['pnu'])))
            self.table.setItem(i, 1, QTableWidgetItem(str(p['jibun'])))
            self.table.setItem(i, 2, QTableWidgetItem(str(p['jimok'])))
            self.table.setItem(i, 3, QTableWidgetItem(str(p['category'])))
            self.table.setItem(i, 4, QTableWidgetItem(f"{p['incl_area']:,.2f}"))
            self.table.setItem(i, 5, QTableWidgetItem(f"{p['jiga']:,.0f}"))
            self.table.setItem(i, 6, QTableWidgetItem(f"{p['charge']:,.0f}"))

    def calc_dev_charge(self):
        profit = self._read_float(self.dev_profit_edit, 0)
        rate = self._read_float(self.dev_rate_edit, 25) / 100.0
        charge = max(0.0, profit) * rate
        self.dev_result_label.setText(f"개발부담금: {charge:,.0f} 원")

    def reset(self):
        self.cadastral_items = []
        self.selected_geometry = None
        self.last_result = None
        self.table.setRowCount(0)
        self.farmland_label.setText("농지보전부담금: - 원 (면적 - m2)")
        self.forest_label.setText("대체산림자원조성비: - 원 (면적 - m2)")
        self.other_label.setText("기타 지목 면적: - m2")
        self.total_label.setText("기반비용 합계: - 원")
        self.dev_result_label.setText("개발부담금: - 원")

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if not self.last_result:
            return None
        s = self.last_result['summary']
        section = {
            'title': '기반비용 산출 (참고용 추정)',
            'kv': [
                ('농지보전부담금',
                 f"{s['farmland_charge']:,.0f} 원 (면적 {s['farmland_area']:,.2f} m2)"),
                ('대체산림자원조성비',
                 f"{s['forest_charge']:,.0f} 원 (면적 {s['forest_area']:,.2f} m2)"),
                ('기타 지목 면적', f"{s['other_area']:,.2f} m2"),
                ('기반비용 합계', f"{s['total_charge']:,.0f} 원"),
                ('개발부담금(간이)', self.dev_result_label.text().replace('개발부담금: ', '')),
            ],
            'tables': [{
                'title': '필지별 부담금 상세',
                'headers': ['PNU', '지번', '지목', '구분', '편입면적(m2)',
                            '공시지가(원/m2)', '부담금(원)'],
                'rows': [[p['pnu'], p['jibun'], p['jimok'], p['category'],
                          f"{p['incl_area']:,.2f}", f"{p['jiga']:,.0f}",
                          f"{p['charge']:,.0f}"]
                         for p in self.last_result['parcels'][:200]],
            }],
        }
        return section
