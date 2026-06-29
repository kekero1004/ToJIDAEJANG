# -*- coding: utf-8 -*-
"""
/***************************************************************************
 VWorld Land Information Tool
                                 A QGIS plugin
 브이월드 API를 활용한 토지정보 조회 플러그인
 ***************************************************************************/
"""

import os
import json
from datetime import datetime

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QComboBox, QGroupBox, QMessageBox, QFileDialog, QSplitter,
    QHeaderView, QProgressBar, QTextEdit, QCheckBox,
    QApplication, QStyle
)
from qgis.core import (
    QgsProject, QgsMapLayer, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsWkbTypes, Qgis, QgsMessageLog,
    QgsVectorFileWriter, QgsFields, QgsField
)
from PyQt5.QtCore import QVariant

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu
from .api_manager import VWorldAPIManager
from .chart_widget import ChartWidget
from .dashboard_widget import DashboardWidget
from .land_info_tab import LandInfoTab
from .export_manager import ExportManager
from .terrain_analyzer import TerrainAnalyzer, TerrainAnalysisTab
from .streetview_manager import StreetViewManager, StreetViewTab
from .ai_analyzer import AIAnalyzer, AIAnalysisTab
from .cost_calculator import CostAnalysisTab
from .map_tools import ParcelPickTool, PolygonDrawTool, LineDrawTool
from .district_manager import DistrictManager, DistrictTab, MiniToolBar
from .layer_manager import LayerManager, LayerTab
from .section_analyzer import SectionAnalyzer, SectionAnalysisTab
from .feasibility_analyzer import FeasibilityTab
from .parcel_shape_analyzer import ParcelShapeTab
from .permitted_use_analyzer import PermittedUseTab
from .redevelopment_analyzer import RedevelopmentTab
from .project_cost_analyzer import ProjectCostTab
from .capacity_analyzer import CapacityTab
from .road_access_analyzer import RoadAccessTab
from .parcel_split_analyzer import ParcelSplitTab


class VWorldLandInfoDialog(QDialog):
    """메인 다이얼로그"""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.api_manager = VWorldAPIManager()
        self.terrain_analyzer = TerrainAnalyzer()
        self.streetview_manager = StreetViewManager()
        self.district_manager = DistrictManager(iface, self.api_manager)
        self.layer_manager = LayerManager(iface)
        self.all_data = {}
        self.last_query_geometry = None  # 마지막 조회 영역 (WGS84)

        # 맵툴 수명주기 상태
        self._active_map_tool = None
        self._prev_map_tool = None
        self._mini_bar = None
        self._finishing_tool = False

        self.setWindowTitle("브이월드 토지정보 조회")
        self.setMinimumSize(1200, 700)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(5)

        # API 키 설정 영역
        api_group = QGroupBox("API 설정")
        api_group.setMaximumHeight(80)
        api_layout = QVBoxLayout()
        api_layout.setContentsMargins(5, 2, 5, 2)
        api_layout.setSpacing(3)

        # 첫 번째 행: 브이월드 API 키
        api_row1 = QHBoxLayout()
        api_row1.addWidget(QLabel("브이월드 API 키:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setPlaceholderText("API 키를 입력하세요 (vworld.kr에서 발급)")
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        api_row1.addWidget(self.api_key_edit)

        self.show_key_btn = QPushButton("표시")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self.toggle_api_key_visibility)
        api_row1.addWidget(self.show_key_btn)

        # 구역계 필지선택/WMS 레이어 등 조회 전 기능에서도 키 사용 가능하도록 즉시 반영
        self.api_key_edit.textChanged.connect(
            lambda text: self.api_manager.set_api_key(text.strip()))


        api_layout.addLayout(api_row1)

        # 두 번째 행: 건축물대장 API 키
        # api_row2 = QHBoxLayout()
        # api_row2.addWidget(QLabel("건축물대장 API 키:"))
        # self.building_api_key_edit = QLineEdit()
        # self.building_api_key_edit.setPlaceholderText("건축물대장정보 API 키를 입력하세요 (data.go.kr)")
        # self.building_api_key_edit.setEchoMode(QLineEdit.Password)
        # api_row2.addWidget(self.building_api_key_edit)
        #
        # self.show_building_key_btn = QPushButton("표시")
        # self.show_building_key_btn.setCheckable(True)
        # self.show_building_key_btn.toggled.connect(self.toggle_building_api_key_visibility)
        # api_row2.addWidget(self.show_building_key_btn)
        #
        # api_layout.addLayout(api_row2)

        api_group.setLayout(api_layout)
        main_layout.addWidget(api_group)

        # 조회 조건 영역
        query_group = QGroupBox("조회 조건")
        query_group.setMaximumHeight(80)
        query_main_layout = QVBoxLayout()
        query_main_layout.setContentsMargins(5, 2, 5, 2)
        query_main_layout.setSpacing(3)

        # 첫 번째 행: 레이어 선택 및 조회 버튼
        query_row1 = QHBoxLayout()
        self.layer_combo = QComboBox()
        self.layer_combo.setMinimumWidth(200)
        self.refresh_layers()
        query_row1.addWidget(QLabel("레이어 선택:"))
        query_row1.addWidget(self.layer_combo)

        self.refresh_layers_btn = QPushButton("새로고침")
        self.refresh_layers_btn.clicked.connect(self.refresh_layers)
        query_row1.addWidget(self.refresh_layers_btn)

        query_row1.addStretch()

        self.use_selected_cb = QCheckBox("선택된 피처만 사용")
        self.use_selected_cb.setChecked(True)
        query_row1.addWidget(self.use_selected_cb)

        # 구역계 탭에서 확정한 구역으로 조회 (구역계 워크플로)
        self.use_district_cb = QCheckBox("구역계 사용")
        self.use_district_cb.setToolTip(
            "구역계 탭에서 확정한 구역계로 조회합니다 (레이어 선택 무시).")
        query_row1.addWidget(self.use_district_cb)

        self.query_btn = QPushButton("조회 시작")
        self.query_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.query_btn.clicked.connect(self.start_query)
        query_row1.addWidget(self.query_btn)

        self.reset_btn = QPushButton("초기화")
        self.reset_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        self.reset_btn.clicked.connect(self.reset_all)
        query_row1.addWidget(self.reset_btn)

        # 필지 주변정보 버퍼 (PSS 필지 주변정보 반경 분석 참조)
        query_row1.addWidget(QLabel("반경:"))
        self.buffer_radius_combo = QComboBox()
        self.buffer_radius_combo.addItems(["1km", "3km", "5km"])
        query_row1.addWidget(self.buffer_radius_combo)

        self.buffer_btn = QPushButton("주변정보 버퍼")
        self.buffer_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogHelpButton))
        self.buffer_btn.setToolTip("선택 영역 기준 반경 버퍼를 지도에 레이어로 생성합니다.")
        self.buffer_btn.clicked.connect(self.create_buffer_layer)
        query_row1.addWidget(self.buffer_btn)

        query_main_layout.addLayout(query_row1)

        # 두 번째 행: 진행률 및 상태 표시
        query_row2 = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(15)
        query_row2.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #0066cc; font-size: 11px;")
        query_row2.addWidget(self.status_label)
        query_row2.addStretch()

        query_main_layout.addLayout(query_row2)

        query_group.setLayout(query_main_layout)
        main_layout.addWidget(query_group)

        # 메인 스플리터
        splitter = QSplitter(Qt.Horizontal)

        # 왼쪽: 트리 구조
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("조회 결과 트리"))

        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["항목", "값"])
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setColumnCount(2)
        self.result_tree.header().setStretchLastSection(True)
        self.result_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        left_layout.addWidget(self.result_tree)

        splitter.addWidget(left_widget)

        # 오른쪽: 탭 위젯
        self.tab_widget = QTabWidget()

        # 대시보드 탭
        self.dashboard = DashboardWidget()
        self.tab_widget.addTab(self.dashboard, "대시보드")

        # 구역계 탭 (NEW v1.5.0 - 구역계 설정/편집)
        self.district_tab = DistrictTab(self.district_manager)
        self.district_tab.toolRequested.connect(self._on_district_tool)
        self.district_tab.districtConfirmed.connect(self._on_district_confirmed)
        self.tab_widget.addTab(self.district_tab, "구역계")

        # 연속지적도 탭
        self.cadastral_tab = LandInfoTab('cadastral')
        self.tab_widget.addTab(self.cadastral_tab, "연속지적도")

        # 토지임야정보 탭
        self.land_forest_tab = LandInfoTab('land_forest')
        self.tab_widget.addTab(self.land_forest_tab, "토지임야정보")

        # 토지특성정보 탭
        self.land_char_tab = LandInfoTab('land_character')
        self.tab_widget.addTab(self.land_char_tab, "토지특성정보")

        # 개별공시지가 탭
        self.land_price_tab = LandInfoTab('land_price')
        self.tab_widget.addTab(self.land_price_tab, "개별공시지가")

        # 토지이용계획 탭
        self.land_use_tab = LandInfoTab('land_use_plan')
        self.tab_widget.addTab(self.land_use_tab, "토지이용계획")

        # 토지소유자정보 탭
        self.land_owner_tab = LandInfoTab('land_owner')
        self.tab_widget.addTab(self.land_owner_tab, "토지소유자정보")

        # 건축물대장 탭 (NEW)
        # self.building_tab = LandInfoTab('building_register')
        # self.tab_widget.addTab(self.building_tab, "건축물대장")

        # 지형분석 탭 (NEW)
        self.terrain_tab = TerrainAnalysisTab()
        self.tab_widget.addTab(self.terrain_tab, "지형분석")

        # 가로경관 탭 (NEW)
        self.streetview_tab = StreetViewTab()
        self.tab_widget.addTab(self.streetview_tab, "가로경관")

        # 기반비용 산출 탭 (NEW - PSS 토지 시뮬레이션 참조)
        self.cost_tab = CostAnalysisTab()
        self.tab_widget.addTab(self.cost_tab, "기반비용")

        # 입지분석 탭 (NEW v1.5.0 - 입지분석: 단면/개발가능지/토지형상)
        self.location_tabs = QTabWidget()
        self.section_tab = SectionAnalysisTab(
            self.iface, SectionAnalyzer(self.terrain_analyzer))
        self.section_tab.requestDrawLine.connect(self._on_section_line_tool)
        self.location_tabs.addTab(self.section_tab, "단면분석")
        self.feasibility_tab = FeasibilityTab(self.terrain_analyzer)
        self.location_tabs.addTab(self.feasibility_tab, "개발가능지")
        self.shape_tab = ParcelShapeTab()
        self.location_tabs.addTab(self.shape_tab, "토지형상")
        self.tab_widget.addTab(self.location_tabs, "입지분석")

        # 법률분석 탭 (NEW v1.5.0 - 법률분석: 허용용도/정비사업)
        self.legal_tabs = QTabWidget()
        self.permitted_tab = PermittedUseTab()
        self.legal_tabs.addTab(self.permitted_tab, "허용용도분석")
        self.redev_tab = RedevelopmentTab(self.district_manager)
        self.legal_tabs.addTab(self.redev_tab, "정비사업 요건검토")
        self.tab_widget.addTab(self.legal_tabs, "법률분석")
        # 토지형상 요약 → 정비사업 과소필지율 자동반영
        self.shape_tab.shapeResultChanged.connect(
            self.redev_tab.set_shape_summary)

        # 사업비분석 탭 (NEW v1.5.0 - 사업비분석)
        self.project_cost_tab = ProjectCostTab()
        self.tab_widget.addTab(self.project_cost_tab, "사업비분석")

        # 개발성 분석 탭 (NEW v1.6.0 - 동종 QGIS 플러그인 벤치마킹: 개발용량/접도맹지/분할)
        self.development_tabs = QTabWidget()
        self.capacity_tab = CapacityTab()
        self.development_tabs.addTab(self.capacity_tab, "개발용량")
        self.road_access_tab = RoadAccessTab()
        self.development_tabs.addTab(self.road_access_tab, "접도·맹지")
        self.parcel_split_tab = ParcelSplitTab()
        self.parcel_split_tab.requestDrawLine.connect(self._on_split_line_tool)
        self.development_tabs.addTab(self.parcel_split_tab, "분할 시뮬레이션")
        self.tab_widget.addTab(self.development_tabs, "개발성 분석")

        # 레이어 탭 (NEW v1.5.0 - 레이어 활용법/공통기능)
        self.layer_tab = LayerTab(
            self.iface, self.layer_manager,
            get_api_key=lambda: self.api_key_edit.text(),
            district_manager=self.district_manager,
            get_all_data=lambda: self.all_data,
            get_district_geometry=self._current_region_geometry,
            api_manager=self.api_manager)
        self.tab_widget.addTab(self.layer_tab, "레이어")

        # AI 분석 탭 (NEW)
        self.ai_tab = AIAnalysisTab()
        self.tab_widget.addTab(self.ai_tab, "AI 분석")

        # 디버그 탭
        self.debug_tab = QWidget()
        debug_layout = QVBoxLayout(self.debug_tab)
        self.debug_text = QTextEdit()
        self.debug_text.setReadOnly(True)
        debug_layout.addWidget(self.debug_text)
        self.tab_widget.addTab(self.debug_tab, "디버그 로그")

        splitter.addWidget(self.tab_widget)

        splitter.setSizes([400, 800])
        main_layout.addWidget(splitter, 1)

        # 하단 버튼 영역
        button_layout = QHBoxLayout()

        self.export_excel_btn = QPushButton("엑셀 내보내기")
        self.export_excel_btn.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))
        self.export_excel_btn.clicked.connect(self.export_to_excel)
        button_layout.addWidget(self.export_excel_btn)

        self.export_word_btn = QPushButton("보고서 내보내기")
        self.export_word_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        self.export_word_btn.clicked.connect(self.export_to_word)
        button_layout.addWidget(self.export_word_btn)

        button_layout.addStretch()

        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.close)
        button_layout.addWidget(self.close_btn)

        main_layout.addLayout(button_layout)

    def toggle_api_key_visibility(self, checked):
        if checked:
            self.api_key_edit.setEchoMode(QLineEdit.Normal)
            self.show_key_btn.setText("숨김")
            self.api_key_edit.setEchoMode(QLineEdit.Password)
            self.show_key_btn.setText("표시")

    def toggle_building_api_key_visibility(self, checked):
        if checked:
            self.building_api_key_edit.setEchoMode(QLineEdit.Normal)
            self.show_building_key_btn.setText("숨김")
        else:
            self.building_api_key_edit.setEchoMode(QLineEdit.Password)
            self.show_building_key_btn.setText("표시")

    def refresh_layers(self):
        self.layer_combo.clear()

        layers = QgsProject.instance().mapLayers().values()
        for layer in layers:
            if layer.type() == QgsMapLayer.VectorLayer:
                if layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                    self.layer_combo.addItem(layer.name(), layer.id())

    def start_query(self):
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "경고", "API 키를 입력하세요.\n\n브이월드(vworld.kr)에서 무료로 발급받을 수 있습니다.")
            return

        self.api_manager.set_api_key(api_key)
        
        # building_api_key = self.building_api_key_edit.text().strip()
        # self.api_manager.set_building_api_key(building_api_key)
        
        self.api_manager.clear_debug_log()
        self.terrain_analyzer.set_api_key(api_key)
        self.streetview_manager.set_api_key(api_key)

        # 구역계 사용 분기 (구역계 탭에서 확정한 구역으로 조회 - v1.5.0)
        use_district = self.use_district_cb.isChecked()
        if use_district:
            if self.district_manager.confirmed_geometry is None or \
                    self.district_manager.confirmed_geometry.isEmpty():
                QMessageBox.warning(
                    self, "경고",
                    "확정된 구역계가 없습니다.\n\n'구역계' 탭에서 영역을 만들고 "
                    "[구역 확정]을 먼저 실행하세요.")
                return
            features = []
            crs = QgsCoordinateReferenceSystem("EPSG:4326")
        else:
            layer_id = self.layer_combo.currentData()
            if not layer_id:
                QMessageBox.warning(self, "경고", "폴리곤 레이어를 선택하세요.")
                return

            layer = QgsProject.instance().mapLayer(layer_id)
            if not layer:
                QMessageBox.warning(self, "경고", "레이어를 찾을 수 없습니다.")
                return

            if self.use_selected_cb.isChecked():
                features = layer.selectedFeatures()
                if not features:
                    QMessageBox.warning(self, "경고", "선택된 피처가 없습니다.\n\n맵에서 폴리곤을 선택하거나 '선택된 피처만 사용' 체크박스를 해제하세요.")
                    return
            else:
                features = list(layer.getFeatures())
                if not features:
                    QMessageBox.warning(self, "경고", "레이어에 피처가 없습니다.")
                    return
            crs = layer.crs()

        # 전체 단계 수 계산 (연속지적도 + 토지이용계획 + 건축물대장 + 지형분석 + 가로경관)
        total_steps = max(len(features), 1) + 4  # 피처별 연속지적도 + 추가 분석 4단계
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total_steps)
        self.progress_bar.setValue(0)
        self.status_label.setText("조회 중...")

        self.all_data = {
            'cadastral': [],
            'land_forest': [],
            'land_character': [],
            'land_price': [],
            'land_use_plan': [],
            'land_owner': [],
            'building_register': []
        }

        progress = 0

        self.result_tree.clear()

        total_features_found = 0

        # 선택된 폴리곤들을 합쳐서 저장 (편입면적 계산용)
        if use_district:
            combined_geometry_wgs84 = QgsGeometry(
                self.district_manager.confirmed_geometry)
            self.dashboard.set_selected_geometry(combined_geometry_wgs84)
        else:
            combined_geometry = QgsGeometry()
            for feature in features:
                if combined_geometry.isEmpty():
                    combined_geometry = QgsGeometry(feature.geometry())
                else:
                    combined_geometry = combined_geometry.combine(feature.geometry())

            # WGS84로 변환
            combined_geometry_wgs84 = QgsGeometry()
            if not combined_geometry.isEmpty():
                target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
                transform = QgsCoordinateTransform(crs, target_crs, QgsProject.instance())
                combined_geometry_wgs84 = QgsGeometry(combined_geometry)
                combined_geometry_wgs84.transform(transform)
                self.dashboard.set_selected_geometry(combined_geometry_wgs84)

        # 단계 1: 연속지적도 조회 (피처별 / 구역계는 폴리곤 정밀 질의)
        if use_district:
            self.api_manager.debug_log.append("\n=== Processing district geometry ===")
            cadastral_result = self.api_manager.get_cadastral_by_polygon(
                combined_geometry_wgs84)
            if cadastral_result:
                features_data = self.api_manager.parse_features(cadastral_result)
                if features_data:
                    self.all_data['cadastral'].extend(features_data)
                    total_features_found += len(features_data)
                    self.all_data['land_forest'].extend(features_data)
                    self.all_data['land_character'].extend(features_data)
                    self.all_data['land_price'].extend(features_data)
                    self.all_data['land_owner'].extend(features_data)
                    self.api_manager.debug_log.append(
                        f"Found {len(features_data)} parcels (district)")
            else:
                self.api_manager.debug_log.append(
                    f"API call failed: {self.api_manager.last_error}")
            progress += 1
            self.progress_bar.setValue(progress)
            self.status_label.setText("연속지적도 조회 중... (구역계)")
            QApplication.processEvents()

        for feature in features:
            geometry = feature.geometry()

            self.api_manager.debug_log.append(f"\n=== Processing feature {progress + 1} ===")
            self.api_manager.debug_log.append(f"Geometry type: {geometry.type()}")
            self.api_manager.debug_log.append(f"Source CRS: {crs.authid()}")

            cadastral_result = self.api_manager.get_cadastral_by_geometry(geometry, crs)

            if cadastral_result:
                features_data = self.api_manager.parse_features(cadastral_result)

                if features_data:
                    self.all_data['cadastral'].extend(features_data)
                    total_features_found += len(features_data)

                    self.all_data['land_forest'].extend(features_data)
                    self.all_data['land_character'].extend(features_data)
                    self.all_data['land_price'].extend(features_data)
                    self.all_data['land_owner'].extend(features_data)

                    self.api_manager.debug_log.append(f"Found {len(features_data)} parcels")
                else:
                    self.api_manager.debug_log.append("No features found in response")
            else:
                self.api_manager.debug_log.append(f"API call failed: {self.api_manager.last_error}")

            progress += 1
            self.progress_bar.setValue(progress)
            self.status_label.setText(f"연속지적도 조회 중... ({progress}/{len(features)})")
            QApplication.processEvents()

        # 단계 2: PNU 추출 및 추가 정보 조회 (토지이용계획, 토지소유자)
        self.status_label.setText("추가 속성정보 조회 중...")
        QApplication.processEvents()
        
        # Collect unique PNUs
        pnus = set()
        for item in self.all_data['cadastral']:
             pnu = item.get('properties', {}).get('pnu')
             if pnu:
                 pnus.add(pnu)
                 
        total_pnus = len(pnus)
        current_pnu_idx = 0
        
        for pnu in pnus:
            # 토지이용계획
            land_use_items = self.api_manager.get_land_use_attr(pnu)
            if land_use_items:
                for item in land_use_items:
                    self.all_data['land_use_plan'].append({
                        'type': 'Feature',
                        'properties': item,
                        'geometry': None
                    })

            # 토지소유자
            land_owner_items = self.api_manager.get_land_owner_info(pnu)
            if land_owner_items:
                for item in land_owner_items:
                    self.all_data['land_owner'].append({
                        'type': 'Feature',
                        'properties': item,
                        'geometry': None
                    })
            
            current_pnu_idx += 1
            if total_pnus > 0:
                 self.progress_bar.setValue(len(features) + int((current_pnu_idx / total_pnus) * 2)) # Adjust progress logic subtly

        progress += 1 
        self.progress_bar.setValue(progress)

        # 단계 3: 건축물대장 조회 (User disabled)
        # self.status_label.setText("건축물대장 조회 중...")
        # QApplication.processEvents()
        # for feature in features:
        #     pass
        progress += 1
        self.progress_bar.setValue(progress)

        # 단계 4: 지형 분석
        self.status_label.setText("지형 분석 중...")
        QApplication.processEvents()
        terrain_result = {}
        building_age_result = {}
        if not combined_geometry_wgs84.isEmpty():
            terrain_result = self.terrain_analyzer.analyze_terrain(combined_geometry_wgs84)
            building_age_result = self.terrain_analyzer.analyze_building_age(
                self.all_data.get('building_register', [])
            )
        progress += 1
        self.progress_bar.setValue(progress)

        # 단계 5: 가로경관 사진 수집
        self.status_label.setText("가로경관 사진 수집 중...")
        QApplication.processEvents()
        streetview_images = []
        streetview_save_dir = ""
        if not combined_geometry_wgs84.isEmpty():
            project_path = QgsProject.instance().homePath()
            if project_path:
                streetview_save_dir = os.path.join(project_path, 'streetview')
            else:
                streetview_save_dir = os.path.join(os.path.expanduser('~'), 'QGIS_streetview')
            streetview_images = self.streetview_manager.capture_streetview(
                combined_geometry_wgs84, streetview_save_dir
            )
        progress += 1
        self.progress_bar.setValue(progress)

        self.progress_bar.setVisible(False)
        self.status_label.setText(f"조회 완료: {total_features_found}개 필지")

        self.debug_text.setText(self.api_manager.get_debug_log())

        self.update_tree()
        self.update_tabs()

        # 지형분석 탭 업데이트
        if terrain_result:
            self.terrain_tab.update_terrain_data(terrain_result)
        if building_age_result:
            self.terrain_tab.update_building_age_data(building_age_result)

        # 가로경관 탭 업데이트
        if streetview_images:
            self.streetview_tab.update_images(streetview_images, streetview_save_dir)

        # AI 분석 탭에 토지 정보 설정
        ai_land_info = {
            'cadastral': self.all_data.get('cadastral', []),
            'land_use_plan': self.all_data.get('land_use_plan', []),
            'building_register': self.all_data.get('building_register', []),
            'terrain': terrain_result
        }
        self.ai_tab.set_land_info(ai_land_info)

        # 기반비용 탭에 연속지적도 및 선택 지오메트리 설정
        self.cost_tab.set_land_info(
            self.all_data.get('cadastral', []),
            combined_geometry_wgs84 if not combined_geometry_wgs84.isEmpty() else None
        )

        # 신규 분석 탭 데이터 주입 (v1.5.0 - 기능 이식)
        self.last_query_geometry = (
            combined_geometry_wgs84
            if not combined_geometry_wgs84.isEmpty() else None)
        self.shape_tab.set_land_info(
            self.all_data.get('cadastral', []),
            self.all_data.get('land_use_plan', []))
        self.permitted_tab.set_land_info(
            self.all_data.get('land_use_plan', []))
        self.redev_tab.set_land_info(
            self.last_query_geometry,
            self.all_data.get('cadastral', []),
            self.all_data.get('building_register', []))
        self.project_cost_tab.set_land_info(
            self.all_data.get('cadastral', []),
            self.last_query_geometry)
        self.feasibility_tab.set_context(
            self.last_query_geometry, terrain_result)
        # 개발성 분석 탭 데이터 주입 (v1.6.0)
        self.capacity_tab.set_land_info(
            self.all_data.get('cadastral', []),
            self.all_data.get('land_use_plan', []))
        self.road_access_tab.set_land_info(
            self.all_data.get('cadastral', []))
        self.parcel_split_tab.set_land_info(
            self.all_data.get('cadastral', []),
            self.last_query_geometry)
        self.layer_tab.refresh_layer_list()

        if total_features_found == 0:
            QMessageBox.warning(
                self,
                "조회 결과 없음",
                f"조회된 필지가 없습니다.\n\n"
                f"가능한 원인:\n"
                f"1. API 키가 올바르지 않음\n"
                f"2. 선택한 영역이 대한민국 밖임\n"
                f"3. 선택한 영역에 연속지적도 데이터가 없음\n\n"
                f"'디버그 로그' 탭에서 상세 정보를 확인하세요."
            )
        else:
            QMessageBox.information(
                self,
                "완료",
                f"조회가 완료되었습니다.\n총 {total_features_found}개의 필지가 조회되었습니다."
            )
            reply = QMessageBox.question(
                self,
                "편입 지번 SHP 저장",
                "편입된 지번의 교차 영역을 SHP 파일로 저장하시겠습니까?\n\n"
                "선택된 폴리곤과 교차하는 부분만 잘라내어 저장합니다.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.save_inclusion_shp(combined_geometry_wgs84, crs)

    def create_buffer_layer(self):
        """선택 영역 기준 반경 버퍼를 메모리 레이어로 생성하여 지도에 추가
        (PSS '필지 주변정보' 반경 분석 참조 / PyQGIS 3.36 호환)"""
        from qgis.core import QgsFillSymbol

        layer_id = self.layer_combo.currentData()
        if not layer_id:
            QMessageBox.warning(self, "경고", "폴리곤 레이어를 선택하세요.")
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if not layer:
            QMessageBox.warning(self, "경고", "레이어를 찾을 수 없습니다.")
            return

        if self.use_selected_cb.isChecked():
            features = layer.selectedFeatures()
            if not features:
                QMessageBox.warning(self, "경고", "선택된 피처가 없습니다.")
                return
        else:
            features = list(layer.getFeatures())
            if not features:
                QMessageBox.warning(self, "경고", "레이어에 피처가 없습니다.")
                return

        # 선택 폴리곤 병합
        combined = QgsGeometry()
        for feature in features:
            if combined.isEmpty():
                combined = QgsGeometry(feature.geometry())
            else:
                combined = combined.combine(feature.geometry())
        if combined.isEmpty():
            QMessageBox.warning(self, "경고", "유효한 지오메트리가 없습니다.")
            return

        # 반경(m) 파싱
        radius_text = self.buffer_radius_combo.currentText()  # "1km" 등
        try:
            radius_m = float(radius_text.replace("km", "").strip()) * 1000.0
        except ValueError:
            radius_m = 1000.0

        # EPSG:5186(m 단위)으로 변환 후 버퍼 → 정확한 거리 버퍼
        crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")
        transform_to_5186 = QgsCoordinateTransform(layer.crs(), crs_5186, QgsProject.instance())
        geom_5186 = QgsGeometry(combined)
        geom_5186.transform(transform_to_5186)

        buffer_geom = geom_5186.buffer(radius_m, 24)  # 24 segments per quadrant
        if buffer_geom.isEmpty():
            QMessageBox.warning(self, "경고", "버퍼 생성에 실패했습니다.")
            return
        if not buffer_geom.isMultipart():
            buffer_geom.convertToMultiType()

        # 메모리 레이어 생성 (EPSG:5186)
        layer_name = f"주변정보_버퍼_{radius_text}"
        mem_layer = QgsVectorLayer(
            "MultiPolygon?crs=EPSG:5186&field=name:string(50)&field=radius_m:double",
            layer_name, "memory")
        if not mem_layer.isValid():
            QMessageBox.critical(self, "오류", "메모리 레이어 생성에 실패했습니다.")
            return

        provider = mem_layer.dataProvider()
        feat = QgsFeature(mem_layer.fields())
        feat.setGeometry(buffer_geom)
        feat.setAttributes([f"반경 {radius_text}", radius_m])
        provider.addFeatures([feat])
        mem_layer.updateExtents()

        # 반투명 채움 스타일 적용
        try:
            symbol = QgsFillSymbol.createSimple({
                'color': '255,140,0,40',
                'outline_color': '255,80,0,220',
                'outline_width': '0.6',
            })
            mem_layer.renderer().setSymbol(symbol)
            mem_layer.triggerRepaint()
        except Exception:
            pass

        QgsProject.instance().addMapLayer(mem_layer)
        QMessageBox.information(
            self, "완료",
            f"'{layer_name}' 버퍼 레이어가 지도에 추가되었습니다.\n"
            f"반경: {radius_text} ({radius_m:,.0f} m)\n"
            f"좌표계: EPSG:5186")

    def save_inclusion_shp(self, combined_geometry_wgs84, source_crs):
        """편입된 지번의 교차 영역을 SHP 파일로 저장하고 지도에 불러오기"""
        save_dir = QFileDialog.getExistingDirectory(
            self, "편입 지번 SHP 저장 폴더 선택", "",
            QFileDialog.ShowDirsOnly
        )
        if not save_dir:
            return

        cadastral_data = self.all_data.get('cadastral', [])
        if not cadastral_data:
            QMessageBox.warning(self, "경고", "저장할 연속지적도 데이터가 없습니다.")
            return

        crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")
        transform_to_5186 = QgsCoordinateTransform(crs_wgs84, crs_5186, QgsProject.instance())

        selected_geom_5186 = QgsGeometry(combined_geometry_wgs84)
        selected_geom_5186.transform(transform_to_5186)

        fields = QgsFields()
        fields.append(QgsField("pnu", QVariant.String, "String", 20))
        fields.append(QgsField("jibun", QVariant.String, "String", 50))
        fields.append(QgsField("addr", QVariant.String, "String", 100))
        fields.append(QgsField("jimok", QVariant.String, "String", 20))
        fields.append(QgsField("bchk", QVariant.String, "String", 5))
        fields.append(QgsField("jiga", QVariant.Double, "double", 15, 2))
        fields.append(QgsField("total_area", QVariant.Double, "double", 15, 2))
        fields.append(QgsField("incl_area", QVariant.Double, "double", 15, 2))
        fields.append(QgsField("incl_ratio", QVariant.Double, "double", 8, 2))

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shp_filename = f"편입지번_{timestamp}.shp"
        shp_path = os.path.join(save_dir, shp_filename)

        writer = QgsVectorFileWriter(
            shp_path, "UTF-8", fields,
            QgsWkbTypes.MultiPolygon, crs_5186, "ESRI Shapefile"
        )

        if writer.hasError() != QgsVectorFileWriter.NoError:
            QMessageBox.critical(self, "오류", f"SHP 파일 생성 실패:\n{writer.errorMessage()}")
            del writer
            return

        saved_count = 0

        for item in cadastral_data:
            props = item.get('properties', {})
            geom_data = item.get('geometry', {})

            if not geom_data:
                continue

            wkt = self.dashboard._geojson_to_wkt(geom_data)
            if not wkt:
                continue

            parcel_geom = QgsGeometry.fromWkt(wkt)
            if parcel_geom.isEmpty():
                continue

            parcel_geom_5186 = QgsGeometry(parcel_geom)
            parcel_geom_5186.transform(transform_to_5186)

            intersection = parcel_geom_5186.intersection(selected_geom_5186)
            if intersection.isEmpty():
                continue

            total_area = parcel_geom_5186.area()
            inclusion_area = intersection.area()
            inclusion_ratio = (inclusion_area / total_area * 100) if total_area > 0 else 0

            if intersection.type() == QgsWkbTypes.PolygonGeometry:
                if not intersection.isMultipart():
                    intersection.convertToMultiType()

                feat = QgsFeature()
                feat.setGeometry(intersection)

                jibun = props.get('jibun', '')
                pnu = props.get('pnu', '')
                jimok = extract_jimok_from_jibun(jibun)
                if jimok == '미분류':
                    jimok = extract_jimok_from_pnu(pnu)

                jiga = 0
                try:
                    jiga = float(props.get('jiga', 0))
                except:
                    pass

                feat.setAttributes([
                    pnu, jibun, props.get('addr', ''),
                    jimok, props.get('bchk', ''), jiga,
                    round(total_area, 2), round(inclusion_area, 2),
                    round(inclusion_ratio, 2)
                ])

                writer.addFeature(feat)
                saved_count += 1

        del writer

        if saved_count == 0:
            QMessageBox.warning(self, "경고", "교차하는 필지가 없어 SHP 파일이 생성되지 않았습니다.")
            return

        layer_name = f"편입지번_{timestamp}"
        shp_layer = QgsVectorLayer(shp_path, layer_name, "ogr")

        if shp_layer.isValid():
            QgsProject.instance().addMapLayer(shp_layer)
            QMessageBox.information(
                self,
                "SHP 저장 완료",
                f"편입 지번 SHP 파일이 저장되었습니다.\n\n"
                f"저장 경로: {shp_path}\n"
                f"저장 필지 수: {saved_count}개\n"
                f"좌표계: EPSG:5186 (Korea 2000 / Central Belt)\n\n"
                f"필드 정보:\n"
                f"- pnu: 필지번호\n"
                f"- jibun: 지번\n"
                f"- addr: 주소\n"
                f"- jimok: 지목\n"
                f"- bchk: 토지/임야 구분\n"
                f"- jiga: 공시지가(원/m2)\n"
                f"- total_area: 전체 토지면적(m2)\n"
                f"- incl_area: 편입면적(m2)\n"
                f"- incl_ratio: 편입비율(%)\n\n"
                f"지도에 '{layer_name}' 레이어가 추가되었습니다."
            )
        else:
            QMessageBox.warning(
                self, "경고",
                f"SHP 파일은 저장되었으나 레이어 로드에 실패했습니다.\n경로: {shp_path}"
            )

    def update_tree(self):
        self.result_tree.clear()

        for data_type, items in self.all_data.items():
            if not items:
                continue

            type_name = VWorldAPIManager.DATA_TYPES.get(data_type, {}).get('name', data_type)
            type_item = QTreeWidgetItem(self.result_tree, [type_name, f"({len(items)}건)"])
            type_item.setExpanded(True)

            for i, item in enumerate(items[:50]):
                props = item.get('properties', {})
                pnu = props.get('pnu', f'항목 {i+1}')
                addr = props.get('addr', '')

                jibun = props.get('jibun', '')
                jimok = extract_jimok_from_jibun(jibun)
                if jimok == '미분류':
                    jimok = extract_jimok_from_pnu(pnu)

                item_node = QTreeWidgetItem(type_item, [str(pnu), f"{addr} [{jimok}]"])

                for key, value in props.items():
                    QTreeWidgetItem(item_node, [str(key), str(value)])

    def update_tabs(self):
        self.dashboard.update_data(self.all_data)
        self.cadastral_tab.update_data(self.all_data.get('cadastral', []))
        self.land_forest_tab.update_data(self.all_data.get('land_forest', []))
        self.land_char_tab.update_data(self.all_data.get('land_character', []))
        self.land_price_tab.update_data(self.all_data.get('land_price', []))
        self.land_use_tab.update_data(self.all_data.get('land_use_plan', []))
        self.land_owner_tab.update_data(self.all_data.get('land_owner', []))
        # self.building_tab.update_data(self.all_data.get('building_register', []))

    def _collect_analysis_summaries(self):
        """모든 분석 탭의 보고서 섹션 수집 (보고서/엑셀 내보내기용 - v1.5.1)

        각 탭의 get_report_data()가 None이 아닌 것만 탭 순서대로 모은다.
        """
        sections = []
        report_tabs = [
            'district_tab',      # 구역계 (소재지 목록)
            'terrain_tab',       # 지형분석
            'streetview_tab',    # 가로경관
            'cost_tab',          # 기반비용
            'section_tab',       # 입지분석 - 단면분석
            'feasibility_tab',   # 입지분석 - 개발가능지
            'shape_tab',         # 입지분석 - 토지형상
            'permitted_tab',     # 법률분석 - 허용용도
            'redev_tab',         # 법률분석 - 정비사업
            'project_cost_tab',  # 사업비분석
            'capacity_tab',      # 개발성 분석 - 개발용량
            'road_access_tab',   # 개발성 분석 - 접도·맹지
            'parcel_split_tab',  # 개발성 분석 - 분할 시뮬레이션
            'ai_tab',            # AI 분석
        ]
        for tab_name in report_tabs:
            tab = getattr(self, tab_name, None)
            if tab is None or not hasattr(tab, 'get_report_data'):
                continue
            try:
                section = tab.get_report_data()
                if section:
                    sections.append(section)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"Report section error ({tab_name}): {e}",
                    "VWorld", Qgis.Warning)
        return sections

    def export_to_excel(self):
        if not self.all_data.get('cadastral'):
            QMessageBox.warning(self, "경고", "내보낼 데이터가 없습니다.")
            return

        filepath, _ = QFileDialog.getSaveFileName(
            self, "엑셀 파일 저장", "", "CSV Files (*.csv);;All Files (*)"
        )

        if filepath:
            if not filepath.endswith('.csv'):
                filepath += '.csv'

            dashboard_stats = self.dashboard.get_dashboard_stats()
            debug_log = self.api_manager.get_debug_log()
            analysis_summaries = self._collect_analysis_summaries()

            if ExportManager.export_to_excel(
                    self.all_data, filepath, dashboard_stats, debug_log,
                    analysis_summaries=analysis_summaries):
                QMessageBox.information(self, "완료", f"파일이 저장되었습니다.\n{filepath}")
            else:
                QMessageBox.critical(self, "오류", "파일 저장 중 오류가 발생했습니다.")

    def export_to_word(self):
        if not self.all_data.get('cadastral'):
            QMessageBox.warning(self, "경고", "내보낼 데이터가 없습니다.")
            return

        filepath, selected_filter = QFileDialog.getSaveFileName(
            self, "보고서 파일 저장", "",
            "HTML Files (*.html);;MS Word Files (*.docx);;All Files (*)"
        )

        if filepath:
            if selected_filter == "MS Word Files (*.docx)":
                if not filepath.lower().endswith('.docx'):
                    filepath += '.docx'
            else:
                if not filepath.lower().endswith(('.html', '.docx')):
                    filepath += '.html'

            dashboard_stats = self.dashboard.get_dashboard_stats()
            debug_log = self.api_manager.get_debug_log()
            analysis_summaries = self._collect_analysis_summaries()

            if ExportManager.export_to_word(
                    self.all_data, filepath, dashboard_stats, debug_log,
                    analysis_summaries=analysis_summaries):
                QMessageBox.information(self, "완료", f"파일이 저장되었습니다.\n{filepath}")
            else:
                QMessageBox.critical(self, "오류", "파일 저장 중 오류가 발생했습니다.")

    def reset_all(self):
        """모든 GUI 값들을 초기화"""
        reply = QMessageBox.question(
            self, "초기화 확인",
            "모든 조회 결과 및 설정을 초기화하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.No:
            return

        self.all_data = {
            'cadastral': [],
            'land_forest': [],
            'land_character': [],
            'land_price': [],
            'land_use_plan': [],
            'land_owner': [],
            'building_register': []
        }

        self.result_tree.clear()

        self.dashboard.data = {}
        self.dashboard.selected_geometry = None
        self.dashboard.sido_combo.setCurrentIndex(0)
        self.dashboard.sigungu_combo.setCurrentIndex(0)
        self.dashboard.jimok_combo.setCurrentIndex(0)
        self.dashboard.jibun_edit.clear()
        self.dashboard.update_statistics()

        # 각 탭 초기화
        for tab in [self.cadastral_tab, self.land_forest_tab, self.land_char_tab,
                     self.land_price_tab, self.land_use_tab, self.land_owner_tab]:
                     # self.building_tab]:
            tab.data = []
            tab.table.setRowCount(0)
            tab.detail_text.clear()
            tab.search_edit.clear()

        # 기반비용 탭 초기화
        if hasattr(self, 'cost_tab'):
            self.cost_tab.reset()

        # 신규 분석 탭 일괄 초기화 (v1.5.0)
        self._finish_map_tool()
        for tab_name in ('district_tab', 'section_tab', 'feasibility_tab',
                         'shape_tab', 'permitted_tab', 'redev_tab',
                         'project_cost_tab', 'capacity_tab', 'road_access_tab',
                         'parcel_split_tab', 'layer_tab'):
            tab = getattr(self, tab_name, None)
            if tab is not None:
                try:
                    tab.reset()
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"Tab reset error ({tab_name}): {e}",
                        "VWorld", Qgis.Warning)
        self.use_district_cb.setChecked(False)
        self.last_query_geometry = None

        self.api_manager.clear_debug_log()
        self.debug_text.clear()

        self.status_label.setText("")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.tab_widget.setCurrentIndex(0)

        QMessageBox.information(self, "초기화 완료", "모든 데이터가 초기화되었습니다.")

    # ------------------------------------------------------------------
    # 맵툴 수명주기 (v1.5.0 - 구역계/단면분석 도구)
    # ------------------------------------------------------------------
    def _current_region_geometry(self):
        """일괄 내보내기 등에서 사용할 현재 영역 (구역계 우선)"""
        if self.district_manager.confirmed_geometry is not None and \
                not self.district_manager.confirmed_geometry.isEmpty():
            return self.district_manager.confirmed_geometry
        return self.last_query_geometry

    def _ensure_mini_bar(self):
        if self._mini_bar is None:
            self._mini_bar = MiniToolBar(self.iface.mainWindow())
            self._mini_bar.doneClicked.connect(self._finish_map_tool)
            self._mini_bar.cancelClicked.connect(self._finish_map_tool)
        return self._mini_bar

    def _activate_map_tool(self, tool, status_text):
        """맵툴 활성화: 다이얼로그 숨김 + 미니바 표시 (지도 상호작용)"""
        canvas = self.iface.mapCanvas()
        self._prev_map_tool = canvas.mapTool()
        self._active_map_tool = tool
        canvas.setMapTool(tool)

        bar = self._ensure_mini_bar()
        bar.set_status(status_text)
        # 캔버스 우상단에 배치
        try:
            canvas_top_left = canvas.mapToGlobal(canvas.rect().topLeft())
            bar.adjustSize()
            bar.move(canvas_top_left.x() + canvas.width() - bar.width() - 30,
                     canvas_top_left.y() + 30)
        except Exception:
            pass
        bar.show()
        self.hide()

    def _finish_map_tool(self):
        """맵툴 종료: 도구 해제 + 이전 도구 복원 + 다이얼로그 복귀"""
        if self._finishing_tool:
            return
        self._finishing_tool = True
        try:
            canvas = self.iface.mapCanvas()
            if self._active_map_tool is not None:
                try:
                    canvas.unsetMapTool(self._active_map_tool)
                    if self._prev_map_tool is not None:
                        canvas.setMapTool(self._prev_map_tool)
                except Exception:
                    pass
                self._active_map_tool = None
                self._prev_map_tool = None
            if self._mini_bar is not None and self._mini_bar.isVisible():
                self._mini_bar.hide()
            if not self.isVisible():
                self.show()
                self.raise_()
                self.activateWindow()
            # 소재지 목록 카운트 갱신
            if hasattr(self, 'district_tab'):
                self.district_tab.update_count_label()
        finally:
            self._finishing_tool = False

    def _on_district_tool(self, mode):
        """구역계 탭 도구 버튼 → 맵툴 활성화"""
        self._finish_map_tool()
        canvas = self.iface.mapCanvas()

        if mode in ('parcel_add', 'parcel_remove'):
            if mode == 'parcel_add' and not self.api_key_edit.text().strip():
                QMessageBox.warning(
                    self, "API 키 필요",
                    "필지선택은 VWorld API로 필지를 조회합니다.\n"
                    "상단에 API 키를 먼저 입력하세요.")
                return
            tool = ParcelPickTool(canvas)
            tool.parcelClicked.connect(
                lambda lon, lat, m=mode: self._on_parcel_clicked(lon, lat, m))
            tool.finished.connect(self._finish_map_tool)
            label = ("필지추가: 지도에서 필지를 클릭하세요 (우클릭/Esc 종료)"
                     if mode == 'parcel_add'
                     else "필지삭제: 제외할 선택 필지를 클릭하세요 (우클릭/Esc 종료)")
        else:  # area_add / area_erase
            tool = PolygonDrawTool(canvas)
            tool.polygonCompleted.connect(
                lambda geom, m=mode: self._on_polygon_done(geom, m))
            tool.canceled.connect(self._finish_map_tool)
            label = ("영역추가: 클릭으로 꼭짓점 추가, 더블클릭/Enter 완료 "
                     "(필지 모서리 스냅)"
                     if mode == 'area_add'
                     else "영역삭제: 제외할 영역을 그리세요, 더블클릭/Enter 완료")
        self._activate_map_tool(tool, label)

    def _on_parcel_clicked(self, lon, lat, mode):
        self.district_tab.handle_parcel_click(lon, lat, mode)
        if self._mini_bar is not None:
            total = len(self.district_manager.parcels)
            excluded = sum(1 for p in self.district_manager.parcels.values()
                           if p.get('excluded'))
            action = "필지추가" if mode == 'parcel_add' else "필지삭제"
            self._mini_bar.set_status(
                f"{action}: 선택 필지 {total - excluded}개 "
                "(계속 클릭 / [완료]로 종료)")

    def _on_polygon_done(self, geom_wgs84, mode):
        self.district_tab.handle_polygon_drawn(geom_wgs84, mode)
        self._finish_map_tool()
        self.tab_widget.setCurrentWidget(self.district_tab)

    def _on_district_confirmed(self, geom):
        """구역확정 → '구역계 사용' 자동 체크"""
        self.use_district_cb.setChecked(True)

    def _on_section_line_tool(self, idx):
        """단면분석 탭 → 단면선 그리기 도구 활성화"""
        self._finish_map_tool()
        canvas = self.iface.mapCanvas()
        tool = LineDrawTool(canvas)
        tool.lineCompleted.connect(
            lambda geom, i=idx: self._on_section_line_done(i, geom))
        tool.canceled.connect(self._finish_map_tool)
        self._activate_map_tool(
            tool,
            f"단면선{idx + 1} 그리기: 시작점·끝점 클릭, 더블클릭/Enter 완료, "
            "Esc 취소")

    def _on_section_line_done(self, idx, geom_wgs84):
        self.section_tab.set_line(idx, geom_wgs84)
        self._finish_map_tool()
        self.tab_widget.setCurrentWidget(self.location_tabs)
        self.location_tabs.setCurrentWidget(self.section_tab)

    def _on_split_line_tool(self):
        """필지 분할 시뮬레이션 탭 → 분할선 그리기 도구 활성화 (v1.6.0)"""
        self._finish_map_tool()
        canvas = self.iface.mapCanvas()
        tool = LineDrawTool(canvas)
        tool.lineCompleted.connect(self._on_split_line_done)
        tool.canceled.connect(self._finish_map_tool)
        self._activate_map_tool(
            tool,
            "분할선 그리기: 시작점·끝점 클릭, 더블클릭/Enter 완료, Esc 취소")

    def _on_split_line_done(self, geom_wgs84):
        self._finish_map_tool()
        self.tab_widget.setCurrentWidget(self.development_tabs)
        self.development_tabs.setCurrentWidget(self.parcel_split_tab)
        self.parcel_split_tab.set_drawn_line(geom_wgs84)

    def closeEvent(self, event):
        """다이얼로그 종료 시 맵툴/러버밴드/마커 정리 (잔존 방지)"""
        try:
            self._finish_map_tool()
            if self._mini_bar is not None:
                self._mini_bar.deleteLater()
                self._mini_bar = None
            self.district_manager.clear_preview()
            if hasattr(self, 'section_tab'):
                self.section_tab.clear_sections()
        except Exception:
            pass
        super().closeEvent(event)


class VWorldLandInfoPlugin:
    """QGIS 플러그인 메인 클래스"""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.dialog = None
        self.action = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = self.iface.mainWindow().style().standardIcon(QStyle.SP_DialogApplyButton)

        self.action = QAction(icon, "브이월드 토지정보 조회", self.iface.mainWindow())
        self.action.setObjectName("vworldLandInfoAction")
        self.action.setWhatsThis("브이월드 API를 활용한 토지정보 조회")
        self.action.setStatusTip("브이월드 API를 활용하여 토지정보를 조회합니다.")
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&브이월드 토지정보", self.action)

    def unload(self):
        self.iface.removePluginMenu("&브이월드 토지정보", self.action)
        self.iface.removeToolBarIcon(self.action)

        if self.dialog:
            # 맵툴/러버밴드 잔존 방지 (플러그인 재로드 대비)
            try:
                self.dialog._finish_map_tool()
            except Exception:
                pass
            self.dialog.close()

    def run(self):
        if self.dialog is None:
            self.dialog = VWorldLandInfoDialog(self.iface, self.iface.mainWindow())

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
