# -*- coding: utf-8 -*-
"""
토지정보 탭 위젯 모듈
"""

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit
)

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu


class LandInfoTab(QWidget):
    """토지정보 탭 위젯"""

    def __init__(self, tab_type, parent=None):
        super().__init__(parent)
        self.tab_type = tab_type
        self.data = []
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel("검색:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("PNU 또는 주소로 검색...")
        self.search_edit.textChanged.connect(self.filter_data)
        filter_layout.addWidget(self.search_edit)

        layout.addLayout(filter_layout)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        self.setup_columns()
        layout.addWidget(self.table)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(150)
        layout.addWidget(self.detail_text)

        self.table.itemSelectionChanged.connect(self.show_detail)

    def setup_columns(self):
        columns = {
            'cadastral': ["PNU", "지번", "주소", "지목", "토지/임야", "공시지가", "고시년월"],
            'land_forest': ["PNU", "주소", "토지구분코드", "지번"],
            'land_character': ["PNU", "지번", "주소", "지목", "구분"],
            'land_price': ["PNU", "공시지가", "고시년도", "고시월"],
            'land_use_plan': ["PNU", "용도지역", "도시계획"],
            'land_owner': ["PNU", "주소", "지번", "소유구분", "공시지가"],
            'land_move_history': ["PNU", "지번", "주소"],
            'building_register': ["PNU", "건물명", "동명", "주용도", "연면적", "건축면적",
                                  "건폐율", "용적률", "사용승인일", "지상층수", "지하층수"],
        }

        cols = columns.get(self.tab_type, ["항목", "값"])
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)

    def update_data(self, data):
        self.data = data
        self.refresh_table()

    def refresh_table(self):
        self.table.setRowCount(0)

        for item in self.data:
            props = item.get('properties', {})
            row = self.table.rowCount()
            self.table.insertRow(row)

            jibun = props.get('jibun', '')
            pnu = props.get('pnu', '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)

            if self.tab_type == 'cadastral':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('jibun', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('addr', ''))))
                self.table.setItem(row, 3, QTableWidgetItem(jimok))
                bchk = props.get('bchk', '')
                bchk_name = '토지' if bchk == '1' else ('임야' if bchk == '2' else bchk)
                self.table.setItem(row, 4, QTableWidgetItem(bchk_name))
                jiga = props.get('jiga', '')
                if jiga:
                    try:
                        jiga = f"{int(float(jiga)):,} 원/m2"
                    except:
                        pass
                self.table.setItem(row, 5, QTableWidgetItem(str(jiga)))
                gosi = f"{props.get('gosi_year', '')}-{props.get('gosi_month', '')}"
                self.table.setItem(row, 6, QTableWidgetItem(gosi))
            elif self.tab_type == 'land_forest':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('addr', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('bchk', ''))))
                self.table.setItem(row, 3, QTableWidgetItem(str(props.get('jibun', ''))))
            elif self.tab_type == 'land_character':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('jibun', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('addr', ''))))
                self.table.setItem(row, 3, QTableWidgetItem(jimok))
                self.table.setItem(row, 4, QTableWidgetItem(str(props.get('bchk', ''))))
            elif self.tab_type == 'land_price':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                jiga = props.get('jiga', '')
                if jiga:
                    try:
                        jiga = f"{int(float(jiga)):,} 원/m2"
                    except:
                        pass
                self.table.setItem(row, 1, QTableWidgetItem(str(jiga)))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('gosi_year', ''))))
                self.table.setItem(row, 3, QTableWidgetItem(str(props.get('gosi_month', ''))))
            elif self.tab_type == 'land_use_plan':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('prpos_area_dstrc_nm', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('cty_plan_spfc_nm', ''))))
            elif self.tab_type == 'land_owner':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('addr', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('jibun', ''))))
                bchk = props.get('bchk', '')
                owner_type = '토지' if bchk == '1' else ('임야' if bchk == '2' else '기타')
                self.table.setItem(row, 3, QTableWidgetItem(owner_type))
                jiga = props.get('jiga', '')
                if jiga:
                    try:
                        jiga = f"{int(float(jiga)):,} 원/m2"
                    except:
                        pass
                self.table.setItem(row, 4, QTableWidgetItem(str(jiga)))
            elif self.tab_type == 'land_move_history':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('jibun', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('addr', ''))))
            elif self.tab_type == 'building_register':
                self.table.setItem(row, 0, QTableWidgetItem(str(props.get('pnu', ''))))
                self.table.setItem(row, 1, QTableWidgetItem(str(props.get('bld_nm', ''))))
                self.table.setItem(row, 2, QTableWidgetItem(str(props.get('dong_nm', ''))))
                self.table.setItem(row, 3, QTableWidgetItem(str(props.get('main_purps_cd_nm', ''))))
                self.table.setItem(row, 4, QTableWidgetItem(str(props.get('tot_area', ''))))
                self.table.setItem(row, 5, QTableWidgetItem(str(props.get('arch_area', ''))))
                self.table.setItem(row, 6, QTableWidgetItem(str(props.get('bc_rat', ''))))
                self.table.setItem(row, 7, QTableWidgetItem(str(props.get('vl_rat', ''))))
                self.table.setItem(row, 8, QTableWidgetItem(str(props.get('use_apr_day', ''))))
                self.table.setItem(row, 9, QTableWidgetItem(str(props.get('grnd_flr_cnt', ''))))
                self.table.setItem(row, 10, QTableWidgetItem(str(props.get('ugrnd_flr_cnt', ''))))

    def filter_data(self, text):
        for row in range(self.table.rowCount()):
            match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and text.lower() in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(row, not match)

    def show_detail(self):
        selected = self.table.selectedItems()
        if not selected:
            return

        row = selected[0].row()
        if row < len(self.data):
            item = self.data[row]
            props = item.get('properties', {})

            detail = "=== 상세 정보 ===\n"
            for key, value in props.items():
                detail += f"{key}: {value}\n"

            self.detail_text.setText(detail)
