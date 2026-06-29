# -*- coding: utf-8 -*-
"""
사업비분석 모듈 ('사업비분석 > 토지이용계획/추정사업비' 매뉴얼 이식)
- 토지이용계획: 유사 사업장 평균 용지비율 프리셋 → 용지별 면적표,
  비율 직접 편집 + [적용](합계 100% 검증) + [초기화] (동일)
- 추정사업비: 기준년도 선택 시 건설공사비지수로 물가변동 자동 반영,
  보상비(편입면적×공시지가×배수) + 조성비 + 기반시설비 + 부대비 + 예비비
- 엑셀 내보내기 (항목별 세부내역)

주의: 모든 산출액은 평균 단가 기반 '참고용 추정치'다. 기반비용(부담금) 탭과
      합산해 총사업비를 가늠하는 용도로만 사용한다.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QDoubleValidator
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QAbstractItemView,
)
from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
)

from .cost_calculator import CostCalculator
from .export_manager import ExportManager
from .legal_standards import (
    CONSTRUCTION_COST_INDEX, COST_INDEX_BASE_YEAR,
    LANDUSE_RATIO_PRESETS, PROJECT_UNIT_COSTS,
)


class ProjectCostAnalyzer:
    """추정사업비 산출 클래스"""

    def __init__(self):
        self.cost_calculator = CostCalculator()
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    @staticmethod
    def build_landuse_plan(total_area, preset_name):
        """용지구성 프리셋 → [{'use','ratio','area'}]"""
        preset = LANDUSE_RATIO_PRESETS.get(preset_name, [])
        return [{'use': use, 'ratio': ratio,
                 'area': total_area * ratio / 100.0}
                for use, ratio in preset]

    def district_area_5186(self, district_geom_wgs84):
        if district_geom_wgs84 is None or district_geom_wgs84.isEmpty():
            return 0.0
        try:
            transform = QgsCoordinateTransform(
                self.crs_wgs84, self.crs_5186, QgsProject.instance())
            geom = QgsGeometry(district_geom_wgs84)
            geom.transform(transform)
            return geom.area()
        except Exception:
            return 0.0

    def compensation_base(self, cadastral_items, district_geom_wgs84):
        """보상비 기초: Σ(편입면적 × 공시지가) (원)"""
        total = 0.0
        transform = QgsCoordinateTransform(
            self.crs_wgs84, self.crs_5186, QgsProject.instance())
        selected_5186 = None
        if district_geom_wgs84 is not None and \
                not district_geom_wgs84.isEmpty():
            selected_5186 = QgsGeometry(district_geom_wgs84)
            try:
                selected_5186.transform(transform)
            except Exception:
                selected_5186 = None
        for item in (cadastral_items or []):
            props = item.get('properties', {})
            geom_data = item.get('geometry', {})
            if not geom_data:
                continue
            try:
                jiga = float(props.get('jiga', 0) or 0)
            except (ValueError, TypeError):
                jiga = 0.0
            try:
                _, incl = self.cost_calculator._inclusion_area_5186(
                    geom_data, selected_5186, transform)
            except Exception:
                continue
            total += incl * jiga
        return total

    def estimate(self, total_area, landuse_rows, cadastral_items,
                 district_geom_wgs84, base_year, params):
        """추정사업비 산출.

        반환: {'rows': [(항목, 산정기준, 금액원)], 'total': 원,
               'index_factor': 물가지수 배율, 'base_year': 기준년도}
        """
        index_base = CONSTRUCTION_COST_INDEX.get(COST_INDEX_BASE_YEAR, 100.0)
        index_now = CONSTRUCTION_COST_INDEX.get(base_year, index_base)
        factor = index_now / index_base if index_base else 1.0

        comp_mult = params.get(
            'compensation_multiplier',
            PROJECT_UNIT_COSTS['compensation_multiplier'])
        site_unit = params.get(
            'site_work_per_m2', PROJECT_UNIT_COSTS['site_work_per_m2'])
        infra_unit = params.get(
            'infra_per_m2', PROJECT_UNIT_COSTS['infra_per_m2'])
        incidental_rate = params.get(
            'incidental_rate', PROJECT_UNIT_COSTS['incidental_rate'])
        contingency_rate = params.get(
            'contingency_rate', PROJECT_UNIT_COSTS['contingency_rate'])

        # 보상비 (공시지가 기반 - 물가지수 미적용, 현재 공시지가가 이미 시점가)
        comp_base = self.compensation_base(
            cadastral_items, district_geom_wgs84)
        compensation = comp_base * comp_mult

        # 기반시설 용지 면적 (도로/공원/기타 기반시설 용지 합)
        infra_area = sum(
            r['area'] for r in landuse_rows
            if any(k in r['use'] for k in ('도로', '공원', '기반', '공공')))

        site_work = total_area * site_unit * factor
        infra_cost = infra_area * infra_unit * factor
        direct = site_work + infra_cost
        incidental = direct * incidental_rate
        contingency = direct * contingency_rate
        total = compensation + direct + incidental + contingency

        rows = [
            ('용지보상비',
             f"공시지가합 {comp_base:,.0f}원 × 배수 {comp_mult:.2f}",
             compensation),
            ('부지조성비',
             f"{total_area:,.0f}m2 × {site_unit:,.0f}원 × 지수 {factor:.3f}",
             site_work),
            ('기반시설설치비',
             f"기반용지 {infra_area:,.0f}m2 × {infra_unit:,.0f}원 × "
             f"지수 {factor:.3f}",
             infra_cost),
            ('부대비 (조사·설계·감리)',
             f"직접비 × {incidental_rate * 100:.0f}%", incidental),
            ('예비비',
             f"직접비 × {contingency_rate * 100:.0f}%", contingency),
        ]
        return {'rows': rows, 'total': total,
                'index_factor': factor, 'base_year': base_year}


class ProjectCostTab(QWidget):
    """사업비분석 탭 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyzer = ProjectCostAnalyzer()
        self.cadastral_items = []
        self.district_geom = None
        self.total_area = 0.0
        self.landuse_rows = []
        self.result = None
        self._updating = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 토지이용계획 그룹
        landuse_group = QGroupBox(
            "토지이용계획 (유사 사업장 평균 용지비율 - 비율 직접 수정 가능)")
        lu_layout = QVBoxLayout()
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("사업 유형:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(LANDUSE_RATIO_PRESETS.keys()))
        self.preset_combo.currentTextChanged.connect(self.load_preset)
        preset_row.addWidget(self.preset_combo, 2)
        self.area_label = QLabel("구역면적: - m2")
        preset_row.addWidget(self.area_label)
        apply_btn = QPushButton("적용 (비율 검증)")
        apply_btn.clicked.connect(self.apply_ratios)
        preset_row.addWidget(apply_btn)
        reset_btn = QPushButton("초기화")
        reset_btn.clicked.connect(
            lambda: self.load_preset(self.preset_combo.currentText()))
        preset_row.addWidget(reset_btn)
        lu_layout.addLayout(preset_row)

        self.landuse_table = QTableWidget()
        self.landuse_table.setColumnCount(3)
        self.landuse_table.setHorizontalHeaderLabels(
            ["용지 구분", "비율(%) - 더블클릭 수정", "면적(m2)"])
        self.landuse_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.landuse_table.setMinimumHeight(170)
        self.landuse_table.itemChanged.connect(self.on_ratio_changed)
        lu_layout.addWidget(self.landuse_table)
        landuse_group.setLayout(lu_layout)
        layout.addWidget(landuse_group)

        # 추정사업비 그룹
        cost_group = QGroupBox("추정사업비 (기준년도 물가변동 자동 반영)")
        cost_layout = QVBoxLayout()
        param_grid = QGridLayout()
        param_grid.addWidget(QLabel("기준년도:"), 0, 0)
        self.year_combo = QComboBox()
        for year in sorted(CONSTRUCTION_COST_INDEX.keys(), reverse=True):
            self.year_combo.addItem(str(year), year)
        param_grid.addWidget(self.year_combo, 0, 1)
        param_grid.addWidget(QLabel("보상 배수:"), 0, 2)
        self.comp_mult_edit = QLineEdit(
            str(PROJECT_UNIT_COSTS['compensation_multiplier']))
        self.comp_mult_edit.setValidator(QDoubleValidator(0, 10, 2))
        param_grid.addWidget(self.comp_mult_edit, 0, 3)
        param_grid.addWidget(QLabel("조성단가(원/m2):"), 1, 0)
        self.site_unit_edit = QLineEdit(
            str(int(PROJECT_UNIT_COSTS['site_work_per_m2'])))
        self.site_unit_edit.setValidator(QDoubleValidator(0, 1e8, 0))
        param_grid.addWidget(self.site_unit_edit, 1, 1)
        param_grid.addWidget(QLabel("기반시설단가(원/m2):"), 1, 2)
        self.infra_unit_edit = QLineEdit(
            str(int(PROJECT_UNIT_COSTS['infra_per_m2'])))
        self.infra_unit_edit.setValidator(QDoubleValidator(0, 1e8, 0))
        param_grid.addWidget(self.infra_unit_edit, 1, 3)
        self.calc_btn = QPushButton("추정사업비 산출")
        self.calc_btn.setStyleSheet("font-weight: bold;")
        self.calc_btn.clicked.connect(self.run_estimate)
        param_grid.addWidget(self.calc_btn, 2, 0, 1, 2)
        export_btn = QPushButton("엑셀 내보내기")
        export_btn.clicked.connect(self.export_xlsx)
        param_grid.addWidget(export_btn, 2, 2, 1, 2)
        cost_layout.addLayout(param_grid)

        self.cost_table = QTableWidget()
        self.cost_table.setColumnCount(3)
        self.cost_table.setHorizontalHeaderLabels(
            ["항목", "산정 기준", "금액(원)"])
        self.cost_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.cost_table.horizontalHeader().setStretchLastSection(True)
        self.cost_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cost_table.setMinimumHeight(170)
        cost_layout.addWidget(self.cost_table)

        self.total_label = QLabel("총 추정사업비: - 원")
        self.total_label.setStyleSheet(
            "font-weight: bold; color: #c0392b; font-size: 14px;")
        cost_layout.addWidget(self.total_label)
        note = QLabel(
            "※ 평균 단가 기반 참고용 추정치 (감정평가·실시설계 금액과 다름). "
            "농지보전부담금 등 부담금은 '기반비용' 탭에서 별도 산출해 합산하세요.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        cost_layout.addWidget(note)
        cost_group.setLayout(cost_layout)
        layout.addWidget(cost_group)

    # ------------------------------------------------------------------
    def set_land_info(self, cadastral_items, district_geom_wgs84):
        """조회 완료 후 데이터 주입 (main 연계)"""
        self.cadastral_items = cadastral_items or []
        self.district_geom = district_geom_wgs84
        self.total_area = self.analyzer.district_area_5186(district_geom_wgs84)
        self.area_label.setText(
            f"구역면적: {self.total_area:,.1f} m2 "
            f"({self.total_area / 10000.0:,.2f} ha)")
        self.load_preset(self.preset_combo.currentText())

    def load_preset(self, preset_name):
        self.landuse_rows = self.analyzer.build_landuse_plan(
            self.total_area, preset_name)
        self.populate_landuse_table()

    def populate_landuse_table(self):
        self._updating = True
        try:
            self.landuse_table.setRowCount(len(self.landuse_rows) + 1)
            total_ratio = 0.0
            for i, row in enumerate(self.landuse_rows):
                use_item = QTableWidgetItem(row['use'])
                use_item.setFlags(Qt.ItemIsEnabled)
                self.landuse_table.setItem(i, 0, use_item)
                self.landuse_table.setItem(
                    i, 1, QTableWidgetItem(f"{row['ratio']:.1f}"))
                area_item = QTableWidgetItem(f"{row['area']:,.1f}")
                area_item.setFlags(Qt.ItemIsEnabled)
                self.landuse_table.setItem(i, 2, area_item)
                total_ratio += row['ratio']
            # 합계 행
            n = len(self.landuse_rows)
            sum_item = QTableWidgetItem("합계")
            sum_item.setFlags(Qt.ItemIsEnabled)
            self.landuse_table.setItem(n, 0, sum_item)
            ratio_sum_item = QTableWidgetItem(f"{total_ratio:.1f}")
            ratio_sum_item.setFlags(Qt.ItemIsEnabled)
            self.landuse_table.setItem(n, 1, ratio_sum_item)
            area_sum_item = QTableWidgetItem(
                f"{sum(r['area'] for r in self.landuse_rows):,.1f}")
            area_sum_item.setFlags(Qt.ItemIsEnabled)
            self.landuse_table.setItem(n, 2, area_sum_item)
        finally:
            self._updating = False

    def on_ratio_changed(self, item):
        """비율 수정 → 면적 즉시 재계산 (적용 전 미리보기)"""
        if self._updating or item.column() != 1:
            return
        idx = item.row()
        if idx >= len(self.landuse_rows):
            return
        try:
            ratio = float(item.text().replace(',', '').strip())
        except ValueError:
            self._updating = True
            item.setText(f"{self.landuse_rows[idx]['ratio']:.1f}")
            self._updating = False
            return
        self.landuse_rows[idx]['ratio'] = ratio
        self.landuse_rows[idx]['area'] = self.total_area * ratio / 100.0
        self.populate_landuse_table()

    def apply_ratios(self):
        """[적용] - 비율 합계 100% 검증 (동작)"""
        total_ratio = sum(r['ratio'] for r in self.landuse_rows)
        if abs(total_ratio - 100.0) > 0.5:
            QMessageBox.warning(
                self, "비율 오류",
                f"용지 비율 합계가 {total_ratio:.1f}%입니다.\n"
                "합계가 100%가 되도록 수정 후 적용하세요.")
            return
        QMessageBox.information(
            self, "적용 완료",
            "토지이용계획이 적용되었습니다.\n"
            "수정 내용은 추정사업비 산출에 반영됩니다.")

    def _read_float(self, edit, default):
        try:
            return float(edit.text().replace(',', '').strip())
        except (ValueError, AttributeError):
            return default

    def run_estimate(self):
        if self.total_area <= 0 and not self.cadastral_items:
            QMessageBox.warning(
                self, "데이터 없음", "먼저 토지정보를 조회하세요.")
            return
        params = {
            'compensation_multiplier': self._read_float(
                self.comp_mult_edit,
                PROJECT_UNIT_COSTS['compensation_multiplier']),
            'site_work_per_m2': self._read_float(
                self.site_unit_edit, PROJECT_UNIT_COSTS['site_work_per_m2']),
            'infra_per_m2': self._read_float(
                self.infra_unit_edit, PROJECT_UNIT_COSTS['infra_per_m2']),
            'incidental_rate': PROJECT_UNIT_COSTS['incidental_rate'],
            'contingency_rate': PROJECT_UNIT_COSTS['contingency_rate'],
        }
        self.result = self.analyzer.estimate(
            self.total_area, self.landuse_rows, self.cadastral_items,
            self.district_geom, self.year_combo.currentData(), params)

        rows = self.result['rows']
        self.cost_table.setRowCount(len(rows))
        for i, (name, basis, amount) in enumerate(rows):
            self.cost_table.setItem(i, 0, QTableWidgetItem(name))
            self.cost_table.setItem(i, 1, QTableWidgetItem(basis))
            self.cost_table.setItem(
                i, 2, QTableWidgetItem(f"{amount:,.0f}"))
        self.total_label.setText(
            f"총 추정사업비: {self.result['total']:,.0f} 원 "
            f"(기준년도 {self.result['base_year']}, "
            f"공사비지수 배율 {self.result['index_factor']:.3f})")

    def export_xlsx(self):
        if self.result is None:
            QMessageBox.information(self, "안내", "먼저 [추정사업비 산출]을 실행하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "추정사업비 저장", "추정사업비.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        rows = [[name, basis, f"{amount:,.0f}"]
                for name, basis, amount in self.result['rows']]
        rows.append(['총계', f"기준년도 {self.result['base_year']}",
                     f"{self.result['total']:,.0f}"])
        rows.append([])
        rows.append(['토지이용계획', '비율(%)', '면적(m2)'])
        for r in self.landuse_rows:
            rows.append([r['use'], f"{r['ratio']:.1f}", f"{r['area']:,.1f}"])
        saved = ExportManager.export_table_xlsx(
            ['항목', '산정 기준', '금액(원)'], rows, path)
        QMessageBox.information(self, "저장 완료", f"저장됨: {saved}")

    def get_summary(self):
        """보고서 연계용 요약"""
        if self.result is None:
            return None
        return {
            'total': self.result['total'],
            'base_year': self.result['base_year'],
            'rows': self.result['rows'],
            'landuse': self.landuse_rows,
        }

    def reset(self):
        self.cadastral_items = []
        self.district_geom = None
        self.total_area = 0.0
        self.landuse_rows = []
        self.result = None
        self.landuse_table.setRowCount(0)
        self.cost_table.setRowCount(0)
        self.area_label.setText("구역면적: - m2")
        self.total_label.setText("총 추정사업비: - 원")

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if self.result is None and not self.landuse_rows:
            return None
        section = {'title': '사업비분석 (참고용 추정)', 'kv': [], 'tables': []}
        if self.total_area > 0:
            section['kv'].append(
                ('구역면적',
                 f"{self.total_area:,.1f} m2 "
                 f"({self.total_area / 10000.0:,.2f} ha)"))
        section['kv'].append(
            ('사업 유형', self.preset_combo.currentText()))
        if self.landuse_rows:
            section['tables'].append({
                'title': '토지이용계획',
                'headers': ['용지 구분', '비율(%)', '면적(m2)'],
                'rows': [[r['use'], f"{r['ratio']:.1f}",
                          f"{r['area']:,.1f}"] for r in self.landuse_rows],
            })
        if self.result is not None:
            section['kv'].extend([
                ('기준년도',
                 f"{self.result['base_year']} "
                 f"(공사비지수 배율 {self.result['index_factor']:.3f})"),
                ('총 추정사업비', f"{self.result['total']:,.0f} 원"),
            ])
            section['tables'].append({
                'title': '추정사업비 내역',
                'headers': ['항목', '산정 기준', '금액(원)'],
                'rows': [[name, basis, f"{amount:,.0f}"]
                         for name, basis, amount in self.result['rows']],
            })
        return section
