# -*- coding: utf-8 -*-
"""
정비사업/소규모정비사업 요건검토 모듈
('법률분석 > 도시개발분석 > 정비사업/소규모정비사업' 매뉴얼 이식)
- 정비사업: 방식별(서울 4유형/지방 3유형) 구역지정 요건(필수/선택) vs
  대상지 현황 자동비교 → 충족 여부 판정
- 현황 수치 직접 수정 → 즉시 재판정 (다양한 시나리오 검토 - 동일)
- 소규모정비사업: 빈집 및 소규모주택 정비 특례법 기준 유형별 요건 분석
- 현황 자동산출: 구역면적(EPSG:5186), 필지 수, 과소필지 비율(토지형상 탭 연계),
  노후도(건축물대장 활성 시) - 그 외 항목은 수동 입력

주의: 요건 기준값은 법령·조례 참고 간이값으로 법적 효력이 없다.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)
from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
)

from .legal_standards import (
    REDEV_REQUIREMENTS, REDEV_SCHEMES_BY_REGION, SMALL_REDEV_REQUIREMENTS,
)

MET_COLOR = QColor(200, 230, 201)      # 충족 초록
UNMET_COLOR = QColor(255, 205, 210)    # 미충족 빨강
MANUAL_COLOR = QColor(255, 249, 196)   # 수동입력 필요 노랑


class RedevelopmentAnalyzer:
    """정비사업 요건 산정/판정 클래스"""

    def __init__(self):
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def compute_status(self, district_geom_wgs84, cadastral_items,
                       building_items=None, shape_summary=None):
        """대상지 현황 자동산출.

        반환: {'district_area': m2|None, 'parcel_count': int,
               'small_parcel_ratio': %|None, 'deterioration_ratio': %|None}
        """
        status = {
            'district_area': None,
            'parcel_count': len(cadastral_items or []),
            'small_parcel_ratio': None,
            'deterioration_ratio': None,
        }
        if district_geom_wgs84 is not None and \
                not district_geom_wgs84.isEmpty():
            try:
                transform = QgsCoordinateTransform(
                    self.crs_wgs84, self.crs_5186, QgsProject.instance())
                geom = QgsGeometry(district_geom_wgs84)
                geom.transform(transform)
                status['district_area'] = geom.area()
            except Exception:
                pass

        if shape_summary and shape_summary.get('total'):
            status['small_parcel_ratio'] = shape_summary.get('small_ratio')

        # 노후도: 건축물대장(use_apr_day) 기반 - 30년 이상 비율 (간이)
        buildings = building_items or []
        ages = []
        from datetime import datetime
        current_year = datetime.now().year
        for item in buildings:
            props = item.get('properties', {})
            day = str(props.get('useAprDay', props.get('use_apr_day', ''))
                      or '')
            if len(day) >= 4:
                try:
                    ages.append(current_year - int(day[:4]))
                except ValueError:
                    continue
        if ages:
            old = sum(1 for a in ages if a >= 30)
            status['deterioration_ratio'] = old / len(ages) * 100.0
        return status

    @staticmethod
    def evaluate(requirements, status):
        """요건 목록 vs 현황 비교.

        반환: [{'name','unit','threshold','op','mandatory',
                'current': float|None, 'met': True|False|None}]
        met=None 은 현황 미입력 (판정 불가).
        """
        results = []
        for req in requirements:
            current = None
            if req.get('auto_key'):
                current = status.get(req['auto_key'])
            met = None
            if current is not None:
                if req['op'] == '>=':
                    met = float(current) >= float(req['threshold'])
                else:
                    met = float(current) <= float(req['threshold'])
            results.append({
                'name': req['name'],
                'unit': req['unit'],
                'threshold': req['threshold'],
                'op': req['op'],
                'mandatory': req['mandatory'],
                'current': current,
                'met': met,
            })
        return results

    @staticmethod
    def overall_judgement(rows):
        """종합판정: 필수요건 전부 충족 + 선택요건(있으면) 1개 이상 충족"""
        mandatory = [r for r in rows if r['mandatory']]
        optional = [r for r in rows if not r['mandatory']]
        if any(r['met'] is None for r in mandatory):
            return ('판정불가', '필수요건 현황 미입력')
        if not all(r['met'] for r in mandatory):
            return ('불가', '필수요건 미충족')
        if optional:
            known = [r for r in optional if r['met'] is not None]
            if known and any(r['met'] for r in known):
                return ('가능', '필수요건 충족 + 선택요건 충족')
            if not known:
                return ('조건부 가능', '필수요건 충족 (선택요건 미입력)')
            return ('불가', '선택요건 모두 미충족')
        return ('가능', '필수요건 모두 충족')


class RedevelopmentTab(QWidget):
    """정비사업/소규모정비 요건검토 탭 (법률분석 서브탭)"""

    def __init__(self, district_manager=None, parent=None):
        super().__init__(parent)
        self.analyzer = RedevelopmentAnalyzer()
        self.district_manager = district_manager
        self.district_geom = None
        self.cadastral_items = []
        self.building_items = []
        self.shape_summary = {}
        self.status = {}
        self._updating = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 정비사업 그룹
        redev_group = QGroupBox(
            "정비사업 구역지정 요건검토 (도시 및 주거환경정비법 - 간이 기준)")
        redev_layout = QVBoxLayout()
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("지역:"))
        self.region_combo = QComboBox()
        self.region_combo.addItems(list(REDEV_SCHEMES_BY_REGION.keys()))
        self.region_combo.currentTextChanged.connect(self.update_scheme_combo)
        sel_row.addWidget(self.region_combo)
        sel_row.addWidget(QLabel("사업 방식:"))
        self.scheme_combo = QComboBox()
        sel_row.addWidget(self.scheme_combo, 2)
        self.redev_btn = QPushButton("요건 분석")
        self.redev_btn.setStyleSheet("font-weight: bold;")
        self.redev_btn.clicked.connect(self.run_redev)
        sel_row.addWidget(self.redev_btn)
        self.shape_btn = QPushButton("형상 (구역 지도표시)")
        self.shape_btn.clicked.connect(self.zoom_district)
        sel_row.addWidget(self.shape_btn)
        redev_layout.addLayout(sel_row)

        self.redev_table = QTableWidget()
        self.redev_table.setColumnCount(6)
        self.redev_table.setHorizontalHeaderLabels(
            ["구분", "요건", "기준값", "현황 (더블클릭 수정)", "단위", "충족"])
        self.redev_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.redev_table.horizontalHeader().setStretchLastSection(True)
        self.redev_table.itemChanged.connect(self.on_redev_item_changed)
        self.redev_table.setMinimumHeight(160)
        redev_layout.addWidget(self.redev_table)
        self.redev_result_label = QLabel("종합판정: -")
        self.redev_result_label.setStyleSheet(
            "font-weight: bold; font-size: 13px;")
        redev_layout.addWidget(self.redev_result_label)
        redev_group.setLayout(redev_layout)
        layout.addWidget(redev_group)

        # 소규모정비 그룹
        small_group = QGroupBox(
            "소규모정비사업 요건검토 (빈집 및 소규모주택 정비 특례법 - 간이 기준)")
        small_layout = QVBoxLayout()
        small_row = QHBoxLayout()
        small_row.addWidget(QLabel("사업 유형:"))
        self.small_combo = QComboBox()
        self.small_combo.addItems(list(SMALL_REDEV_REQUIREMENTS.keys()))
        small_row.addWidget(self.small_combo, 2)
        self.small_btn = QPushButton("요건 분석")
        self.small_btn.setStyleSheet("font-weight: bold;")
        self.small_btn.clicked.connect(self.run_small)
        small_row.addWidget(self.small_btn)
        small_layout.addLayout(small_row)

        self.small_table = QTableWidget()
        self.small_table.setColumnCount(6)
        self.small_table.setHorizontalHeaderLabels(
            ["구분", "요건", "기준값", "현황 (더블클릭 수정)", "단위", "충족"])
        self.small_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.small_table.horizontalHeader().setStretchLastSection(True)
        self.small_table.itemChanged.connect(self.on_small_item_changed)
        self.small_table.setMinimumHeight(150)
        small_layout.addWidget(self.small_table)
        self.small_result_label = QLabel("종합판정: -")
        self.small_result_label.setStyleSheet(
            "font-weight: bold; font-size: 13px;")
        small_layout.addWidget(self.small_result_label)
        small_group.setLayout(small_layout)
        layout.addWidget(small_group)

        note = QLabel(
            "※ 기준값은 도시정비법 시행령·특례법·서울시 조례 참고 간이값입니다. "
            "노후도·호수밀도 등 미산출 항목(노란색)은 현황을 직접 입력하세요. "
            "수정 즉시 재판정됩니다. 법적 효력 없음 - 지자체 조례 확인 필수.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(note)

        self.update_scheme_combo(self.region_combo.currentText())

    # ------------------------------------------------------------------
    def update_scheme_combo(self, region):
        self.scheme_combo.clear()
        self.scheme_combo.addItems(REDEV_SCHEMES_BY_REGION.get(region, []))

    def set_land_info(self, district_geom_wgs84, cadastral_items,
                      building_items=None):
        self.district_geom = district_geom_wgs84
        self.cadastral_items = cadastral_items or []
        self.building_items = building_items or []
        self.refresh_status()

    def set_shape_summary(self, summary):
        """토지형상 탭 연계 - 과소필지 비율 자동반영"""
        self.shape_summary = summary or {}
        self.refresh_status()

    def refresh_status(self):
        self.status = self.analyzer.compute_status(
            self.district_geom, self.cadastral_items,
            self.building_items, self.shape_summary)

    def zoom_district(self):
        """'형상' 버튼 - 구역을 지도에 표시 (요건 부합 구역 표시 대응)"""
        if self.district_manager is not None and \
                self.district_manager.zoom_to_district():
            return
        QMessageBox.information(
            self, "안내",
            "확정된 구역계가 없습니다. 구역계 탭에서 구역을 확정하거나 "
            "레이어에서 폴리곤을 선택해 조회하세요.")

    # ------------------------------------------------------------------
    def _requirements_for_scheme(self):
        scheme = self.scheme_combo.currentText()
        region = self.region_combo.currentText()
        scheme_data = REDEV_REQUIREMENTS.get(scheme, {})
        reqs = scheme_data.get(region)
        if reqs is None and scheme_data:
            # 지방에 없는 유형(도시정비형 등)은 서울 기준 참조
            reqs = next(iter(scheme_data.values()))
        return reqs or []

    def run_redev(self):
        self._populate(self.redev_table, self._requirements_for_scheme(),
                       self.redev_result_label)

    def run_small(self):
        reqs = SMALL_REDEV_REQUIREMENTS.get(
            self.small_combo.currentText(), [])
        self._populate(self.small_table, reqs, self.small_result_label)

    def _populate(self, table, requirements, result_label):
        if not requirements:
            QMessageBox.warning(self, "요건 없음", "선택한 유형의 요건이 없습니다.")
            return
        if not self.cadastral_items and self.district_geom is None:
            QMessageBox.warning(
                self, "데이터 없음",
                "먼저 토지정보를 조회하세요 (현황 자동산출용).")
        self.refresh_status()
        rows = self.analyzer.evaluate(requirements, self.status)

        self._updating = True
        try:
            table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                kind_item = QTableWidgetItem(
                    '필수' if row['mandatory'] else '선택')
                kind_item.setFlags(Qt.ItemIsEnabled)
                table.setItem(i, 0, kind_item)

                name_item = QTableWidgetItem(row['name'])
                name_item.setFlags(Qt.ItemIsEnabled)
                table.setItem(i, 1, name_item)

                op_label = '이상' if row['op'] == '>=' else '이하'
                th_item = QTableWidgetItem(
                    f"{row['threshold']:,.1f} {op_label}")
                th_item.setFlags(Qt.ItemIsEnabled)
                table.setItem(i, 2, th_item)

                current = row['current']
                cur_item = QTableWidgetItem(
                    '' if current is None else f"{current:,.1f}")
                cur_item.setData(Qt.UserRole, i)
                if current is None:
                    cur_item.setBackground(MANUAL_COLOR)
                table.setItem(i, 3, cur_item)

                unit_item = QTableWidgetItem(row['unit'])
                unit_item.setFlags(Qt.ItemIsEnabled)
                table.setItem(i, 4, unit_item)

                met_item = QTableWidgetItem(self._met_label(row['met']))
                met_item.setFlags(Qt.ItemIsEnabled)
                self._color_met(met_item, row['met'])
                table.setItem(i, 5, met_item)
            table._rows_cache = rows  # 재판정용
        finally:
            self._updating = False
        self._update_result(table, result_label)

    @staticmethod
    def _met_label(met):
        if met is None:
            return '입력필요'
        return 'O 충족' if met else 'X 미충족'

    @staticmethod
    def _color_met(item, met):
        if met is None:
            item.setBackground(MANUAL_COLOR)
        elif met:
            item.setBackground(MET_COLOR)
        else:
            item.setBackground(UNMET_COLOR)

    def on_redev_item_changed(self, item):
        self._handle_edit(self.redev_table, item, self.redev_result_label)

    def on_small_item_changed(self, item):
        self._handle_edit(self.small_table, item, self.small_result_label)

    def _handle_edit(self, table, item, result_label):
        """현황 수치 수정 → 즉시 재판정 (동작)"""
        if self._updating or item.column() != 3:
            return
        rows = getattr(table, '_rows_cache', None)
        if not rows:
            return
        idx = item.row()
        if idx >= len(rows):
            return
        text = item.text().replace(',', '').strip()
        row = rows[idx]
        try:
            current = float(text) if text else None
        except ValueError:
            current = None
        row['current'] = current
        if current is None:
            row['met'] = None
        elif row['op'] == '>=':
            row['met'] = current >= float(row['threshold'])
        else:
            row['met'] = current <= float(row['threshold'])

        self._updating = True
        try:
            met_item = table.item(idx, 5)
            met_item.setText(self._met_label(row['met']))
            self._color_met(met_item, row['met'])
            if current is None:
                item.setBackground(MANUAL_COLOR)
            else:
                item.setBackground(QColor(255, 255, 255))
        finally:
            self._updating = False
        self._update_result(table, result_label)

    def _update_result(self, table, result_label):
        rows = getattr(table, '_rows_cache', [])
        verdict, reason = self.analyzer.overall_judgement(rows)
        color = {'가능': '#27ae60', '조건부 가능': '#f39c12',
                 '불가': '#c0392b', '판정불가': '#7f8c8d'}.get(verdict, '#000')
        result_label.setText(f"종합판정: {verdict} ({reason})")
        result_label.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {color};")

    def reset(self):
        self.district_geom = None
        self.cadastral_items = []
        self.building_items = []
        self.shape_summary = {}
        self.status = {}
        self.redev_table.setRowCount(0)
        self.small_table.setRowCount(0)
        self.redev_result_label.setText("종합판정: -")
        self.redev_result_label.setStyleSheet(
            "font-weight: bold; font-size: 13px;")
        self.small_result_label.setText("종합판정: -")
        self.small_result_label.setStyleSheet(
            "font-weight: bold; font-size: 13px;")

    @staticmethod
    def _table_to_rows(table):
        rows = []
        for i in range(table.rowCount()):
            row = []
            for c in range(table.columnCount()):
                item = table.item(i, c)
                row.append(item.text() if item else '')
            rows.append(row)
        return rows

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        has_redev = self.redev_table.rowCount() > 0
        has_small = self.small_table.rowCount() > 0
        if not has_redev and not has_small:
            return None
        headers = ['구분', '요건', '기준값', '현황', '단위', '충족']
        section = {
            'title': '법률분석 - 정비사업 요건검토 (간이 기준, 법적 효력 없음)',
            'kv': [],
            'tables': [],
        }
        if has_redev:
            section['kv'].extend([
                ('정비사업 방식',
                 f"{self.region_combo.currentText()} / "
                 f"{self.scheme_combo.currentText()}"),
                ('정비사업 종합판정',
                 self.redev_result_label.text().replace('종합판정: ', '')),
            ])
            section['tables'].append({
                'title': '정비사업 구역지정 요건',
                'headers': headers,
                'rows': self._table_to_rows(self.redev_table),
            })
        if has_small:
            section['kv'].extend([
                ('소규모정비 유형', self.small_combo.currentText()),
                ('소규모정비 종합판정',
                 self.small_result_label.text().replace('종합판정: ', '')),
            ])
            section['tables'].append({
                'title': '소규모정비사업 요건',
                'headers': headers,
                'rows': self._table_to_rows(self.small_table),
            })
        return section
