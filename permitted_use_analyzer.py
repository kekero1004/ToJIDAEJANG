# -*- coding: utf-8 -*-
"""
허용용도 분석 모듈 ('법률분석 > 행위제한분석 > 허용용도 분석' 매뉴얼 이식)
- 건축물 용도 선택 (대분류 > 중·소분류, 건축법 시행령 별표1 기준)
- 대상지 용도지역(토지이용계획 조회 결과)별 건축 가능 여부 자동판정
  (가능 / 조례위임 / 불가능 - 국토계획법 시행령 별표 간이 매트릭스)
- 조건분석: 판정 결과를 수동 수정 (가능/보류/불가/조례위임/지자체협의)

주의: 대분류 단위 간이 판정으로 세부 호수·면적·층수 제한과 자치법규를
      반영하지 않는다. 실제 인허가 판단은 관할 지자체 확인 필요.
"""

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView,
)

from .legal_standards import (
    BUILDING_USES, BUILDING_USE_SUBTYPES,
    PERMITTED_USE_MATRIX, STATUS_LABELS, CONDITION_CHOICES,
    match_zone_key,
)

STATUS_COLORS = {
    '가능': QColor(200, 230, 201),       # 초록
    '조례위임': QColor(255, 249, 196),   # 노랑
    '불가능': QColor(255, 205, 210),     # 빨강
    '보류': QColor(225, 190, 231),       # 보라
    '불가': QColor(255, 205, 210),
    '지자체협의': QColor(187, 222, 251),  # 파랑
}


class PermittedUseAnalyzer:
    """허용용도 간이 판정 클래스"""

    @staticmethod
    def get_zones_from_landuse(land_use_items):
        """토지이용계획 결과 → 대상지 용도지역 목록.

        반환: [(zone_key|None, zone_name, parcel_count)] (필지 수 내림차순)
        """
        counts = {}
        for item in (land_use_items or []):
            props = item.get('properties', {})
            zone = str(props.get('prposAreaDstrcCodeNm', '') or '').strip()
            if not zone:
                continue
            # 용도지역만 집계 ('지역'으로 끝나는 항목 위주, 그 외는 참고)
            if '지역' not in zone:
                continue
            counts[zone] = counts.get(zone, 0) + 1
        result = []
        for zone_name, count in sorted(
                counts.items(), key=lambda kv: -kv[1]):
            result.append((match_zone_key(zone_name), zone_name, count))
        return result

    @staticmethod
    def judge(zone_key, use_no):
        """'O'/'C'/'X' 판정 (매트릭스 미기재 = 'X')"""
        if not zone_key:
            return None
        return PERMITTED_USE_MATRIX.get(zone_key, {}).get(use_no, 'X')


class PermittedUseTab(QWidget):
    """허용용도 분석 탭 (법률분석 서브탭)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analyzer = PermittedUseAnalyzer()
        self.land_use_items = []
        self.zones = []        # [(zone_key, zone_name, count)]
        self._updating = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        select_group = QGroupBox("건축물 용도 선택 (건축법 시행령 별표1)")
        row = QHBoxLayout()
        row.addWidget(QLabel("대분류:"))
        self.use_combo = QComboBox()
        for no, name in BUILDING_USES:
            self.use_combo.addItem(f"{no}. {name}", no)
        self.use_combo.currentIndexChanged.connect(self.update_subtypes)
        row.addWidget(self.use_combo, 2)
        row.addWidget(QLabel("중·소분류(참고):"))
        self.subtype_combo = QComboBox()
        row.addWidget(self.subtype_combo, 2)
        self.analyze_btn = QPushButton("분석")
        self.analyze_btn.setStyleSheet("font-weight: bold;")
        self.analyze_btn.clicked.connect(self.run_analysis)
        row.addWidget(self.analyze_btn)
        select_group.setLayout(row)
        layout.addWidget(select_group)

        hint = QLabel(
            "※ 판정은 대분류 단위 간이 기준입니다. '조건분석' 열에서 법령·조례 "
            "확인 결과를 직접 수정할 수 있습니다 (수정 시 최종판정에 즉시 반영).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["용도지역", "필지 수", "자동판정", "조건분석 (수동 수정)", "최종판정"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table)

        self.summary_label = QLabel("토지정보 조회 후 용도를 선택하고 [분석]을 실행하세요.")
        self.summary_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        disclaimer = QLabel(
            "※ 본 결과는 국토계획법 시행령 별표 기준 간이 판정으로 법적 효력이 "
            "없습니다. 세부 호수·층수·면적 제한 및 자치법규(조례)는 토지이음 "
            "(www.eum.go.kr)과 관할 지자체에서 확인하세요.")
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet("color: #c0392b; font-size: 11px;")
        layout.addWidget(disclaimer)

        self.update_subtypes()

    # ------------------------------------------------------------------
    def update_subtypes(self):
        self.subtype_combo.clear()
        use_no = self.use_combo.currentData()
        for name in BUILDING_USE_SUBTYPES.get(use_no, []):
            self.subtype_combo.addItem(name)

    def set_land_info(self, land_use_items):
        self.land_use_items = land_use_items or []
        self.zones = self.analyzer.get_zones_from_landuse(self.land_use_items)
        if self.zones:
            zone_text = ", ".join(
                f"{name}({count}필지)" for _, name, count in self.zones[:5])
            self.summary_label.setText(
                f"대상지 용도지역: {zone_text}\n용도 선택 후 [분석]을 실행하세요.")

    def run_analysis(self):
        if not self.zones:
            if not self.land_use_items:
                QMessageBox.warning(
                    self, "데이터 없음",
                    "먼저 토지정보를 조회하세요 (토지이용계획 필요).")
                return
            self.zones = self.analyzer.get_zones_from_landuse(
                self.land_use_items)
            if not self.zones:
                QMessageBox.warning(
                    self, "용도지역 없음",
                    "토지이용계획에서 용도지역을 찾지 못했습니다.")
                return

        use_no = self.use_combo.currentData()
        self._updating = True
        try:
            self.table.setRowCount(len(self.zones))
            for i, (zone_key, zone_name, count) in enumerate(self.zones):
                self.table.setItem(i, 0, QTableWidgetItem(zone_name))
                self.table.setItem(i, 1, QTableWidgetItem(str(count)))

                code = self.analyzer.judge(zone_key, use_no)
                if code is None:
                    auto_label = '기타 (수동확인)'
                else:
                    auto_label = STATUS_LABELS[code]
                auto_item = QTableWidgetItem(auto_label)
                auto_item.setBackground(
                    STATUS_COLORS.get(auto_label, QColor(238, 238, 238)))
                self.table.setItem(i, 2, auto_item)

                combo = QComboBox()
                combo.addItems(CONDITION_CHOICES)
                combo.currentIndexChanged.connect(
                    lambda _, r=i: self.update_final(r))
                self.table.setCellWidget(i, 3, combo)

                final_item = QTableWidgetItem(auto_label)
                final_item.setBackground(
                    STATUS_COLORS.get(auto_label, QColor(238, 238, 238)))
                self.table.setItem(i, 4, final_item)
        finally:
            self._updating = False

        use_name = self.use_combo.currentText()
        subtype = self.subtype_combo.currentText()
        self.summary_label.setText(
            f"선택 용도: {use_name}"
            + (f" ({subtype})" if subtype else "")
            + f" - 용도지역 {len(self.zones)}종 판정 완료")

    def update_final(self, row_idx):
        """조건분석 콤보 변경 → 최종판정 즉시 반영 ([적용] 동작)"""
        if self._updating:
            return
        combo = self.table.cellWidget(row_idx, 3)
        auto_item = self.table.item(row_idx, 2)
        final_item = self.table.item(row_idx, 4)
        if combo is None or final_item is None:
            return
        choice = combo.currentText()
        if choice == '자동판정':
            label = auto_item.text() if auto_item else ''
        else:
            label = choice
        final_item.setText(label)
        final_item.setBackground(
            STATUS_COLORS.get(label, QColor(238, 238, 238)))

    def reset(self):
        self.land_use_items = []
        self.zones = []
        self.table.setRowCount(0)
        self.summary_label.setText(
            "토지정보 조회 후 용도를 선택하고 [분석]을 실행하세요.")

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if self.table.rowCount() == 0:
            return None
        rows = []
        for i in range(self.table.rowCount()):
            zone = self.table.item(i, 0)
            count = self.table.item(i, 1)
            auto = self.table.item(i, 2)
            combo = self.table.cellWidget(i, 3)
            final = self.table.item(i, 4)
            rows.append([
                zone.text() if zone else '',
                count.text() if count else '',
                auto.text() if auto else '',
                combo.currentText() if combo else '',
                final.text() if final else '',
            ])
        subtype = self.subtype_combo.currentText()
        use_label = self.use_combo.currentText() + \
            (f" ({subtype})" if subtype else "")
        return {
            'title': '법률분석 - 허용용도분석 (간이 판정, 법적 효력 없음)',
            'kv': [('선택 건축물 용도', use_label)],
            'tables': [{
                'title': '용도지역별 건축 가능 여부',
                'headers': ['용도지역', '필지 수', '자동판정',
                            '조건분석(수동)', '최종판정'],
                'rows': rows,
            }],
        }
