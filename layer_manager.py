# -*- coding: utf-8 -*-
"""
레이어 관리 모듈 ('레이어 활용법'·'공통 기능 활용법' 매뉴얼 이식)
- 일반레이어: VWorld WMS 공간정보 켜기/끄기 + 키워드 검색 + 전체 선택 해제
  (전국 760여 종 중 대표 카탈로그로 간이화, 임의 레이어ID 직접 추가 지원)
- 사용자레이어: 위계 맨앞으로 / 구역계로 이동 / 범례편집 / 스타일 /
  DXF 내보내기(도형만) / SHP 내보내기(도형+속성)
- 공통기능: 속성보기(+엑셀 내보내기) / 범례편집(구간수·컬러램프·구간값) /
  SHP 일괄 내보내기(기본 EPSG:5174, 구역 주변 1km 포함) / 지도 캡처
"""

import os
import urllib.parse

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QFileDialog, QMessageBox, QDialog, QSpinBox,
    QApplication, QAbstractItemView, QListWidget, QListWidgetItem,
    QDialogButtonBox,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsGeometry,
    QgsField, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsVectorFileWriter, Qgis, QgsMessageLog,
    QgsGraduatedSymbolRenderer, QgsRendererRange, QgsSymbol, QgsStyle,
)
from PyQt5.QtCore import QVariant

from .cost_calculator import geojson_to_wkt
from .export_manager import ExportManager

# VWorld WMS 대표 레이어 카탈로그 (레이어ID, 명칭, 분류)
# ※ 일반레이어(약 760종)의 간이판. 그 외 레이어는 하단 'ID 직접 추가'로 사용.
#   레이어ID는 VWorld WMS API 명세의 데이터셋 ID(소문자)를 따른다.
VWORLD_WMS_CATALOG = [
    ('lp_pa_cbnd_bubun', '연속지적도', '지적'),
    ('lt_c_spbd', 'GIS건물통합정보', '건물'),
    ('lt_c_uq111', '용도지역 (도시지역)', '용도'),
    ('lt_c_uq112', '용도지구', '용도'),
    ('lt_c_uq113', '용도구역', '용도'),
    ('lt_c_uq141', '개발행위허가제한지역', '규제'),
    ('lt_c_ud801', '개발제한구역', '규제'),
    ('lt_c_uf101', '농업진흥지역', '농지'),
    ('lt_c_um710', '보전산지', '산지'),
    ('lt_c_adsido', '행정경계 (시도)', '행정'),
    ('lt_c_adsigg', '행정경계 (시군구)', '행정'),
    ('lt_c_ademd', '행정경계 (읍면동)', '행정'),
    ('lt_c_adri', '행정경계 (리)', '행정'),
    ('lt_l_moctlink', '도로망 (링크)', '교통'),
    ('lt_p_moctnode', '도로망 (노드)', '교통'),
    ('lt_c_wgisnpgug', '국립공원', '환경'),
    ('lt_c_uo301', '관광특구', '관광'),
]

WMS_LAYER_PREFIX = "VWorld_"

# SHP 일괄 내보내기 좌표계 (기본: EPSG:5174 KLIS 중부)
BATCH_EXPORT_CRS_CHOICES = [
    ('EPSG:5174', 'EPSG:5174 (KLIS 중부 - 기본)'),
    ('EPSG:5186', 'EPSG:5186 (Korea 2000 중부)'),
    ('EPSG:4326', 'EPSG:4326 (WGS84)'),
]


class LayerManager:
    """WMS/사용자 레이어 관리 + 내보내기 클래스"""

    def __init__(self, iface):
        self.iface = iface
        self._wms_layers = {}  # layer_id -> qgis layer id

    # ------------------------------------------------------------------
    # 일반레이어 (VWorld WMS)
    # ------------------------------------------------------------------
    def add_wms_layer(self, api_key, layer_id, name):
        """VWorld WMS 레이어 추가. 성공 시 True"""
        if not api_key:
            return False
        if layer_id in self._wms_layers:
            return True
        wms_url = (f"http://api.vworld.kr/req/wms?key={api_key}"
                   f"&domain=localhost&")
        uri = (
            "contextualWMSLegend=0&crs=EPSG:3857&dpiMode=7&featureCount=10"
            f"&format=image/png&layers={layer_id}&styles={layer_id}"
            f"&url={urllib.parse.quote(wms_url, safe='')}"
        )
        layer = QgsRasterLayer(uri, f"{WMS_LAYER_PREFIX}{name}", "wms")
        if not layer.isValid():
            QgsMessageLog.logMessage(
                f"WMS layer invalid: {layer_id}", "VWorld", Qgis.Warning)
            return False
        QgsProject.instance().addMapLayer(layer)
        self._wms_layers[layer_id] = layer.id()
        return True

    def remove_wms_layer(self, layer_id):
        qgis_id = self._wms_layers.pop(layer_id, None)
        if qgis_id:
            QgsProject.instance().removeMapLayer(qgis_id)
            return True
        return False

    def remove_all_wms_layers(self):
        """전체 선택 해제 (활성화된 모든 레이어 한 번에 끄기)"""
        count = 0
        for layer_id in list(self._wms_layers.keys()):
            if self.remove_wms_layer(layer_id):
                count += 1
        return count

    def is_wms_active(self, layer_id):
        return layer_id in self._wms_layers

    # ------------------------------------------------------------------
    # 사용자레이어 (위계/내보내기)
    # ------------------------------------------------------------------
    def bring_to_front(self, layer):
        """위계 맨앞으로: 레이어 트리 최상단으로 이동"""
        if layer is None:
            return False
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node is None:
            return False
        clone = node.clone()
        parent = node.parent() or root
        parent.insertChildNode(0, clone)
        parent.removeChildNode(node)
        return True

    def export_dxf(self, layer, path):
        """DXF 내보내기 - 속성값 없이 도형만 저장됨 (안내와 동일)"""
        if layer is None or not isinstance(layer, QgsVectorLayer):
            return (False, "벡터 레이어가 아닙니다")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'DXF'
        options.fileEncoding = 'cp949'
        err = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options)
        code = err[0] if isinstance(err, tuple) else err
        if code == QgsVectorFileWriter.NoError:
            return (True, path)
        return (False, str(err[1] if isinstance(err, tuple) and len(err) > 1
                           else code))

    def export_shp(self, layer, path, crs_authid='EPSG:5186'):
        """SHP 내보내기 - 도형과 속성값 모두 저장 (좌표계 재투영)"""
        if layer is None or not isinstance(layer, QgsVectorLayer):
            return (False, "벡터 레이어가 아닙니다")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'ESRI Shapefile'
        options.fileEncoding = 'cp949'
        dest_crs = QgsCoordinateReferenceSystem(crs_authid)
        options.ct = QgsCoordinateTransform(
            layer.crs(), dest_crs, QgsProject.instance())
        err = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options)
        code = err[0] if isinstance(err, tuple) else err
        if code == QgsVectorFileWriter.NoError:
            return (True, path)
        return (False, str(err[1] if isinstance(err, tuple) and len(err) > 1
                           else code))

    # ------------------------------------------------------------------
    # SHP 일괄 내보내기 (기초조사 데이터 + 주변 1km)
    # ------------------------------------------------------------------
    @staticmethod
    def build_memory_layer(features, name, crs_authid='EPSG:4326'):
        """GeoJSON 스타일 feature 리스트 → 메모리 폴리곤 레이어

        SHP 필드명 10자 제한을 고려해 속성 키를 10자로 절단(중복 시 번호)한다.
        """
        if not features:
            return None
        # 속성 키 수집
        keys = []
        for feature in features:
            for k in (feature.get('properties') or {}).keys():
                if k not in keys:
                    keys.append(k)
        keys = keys[:30]

        field_names = []
        used = set()
        for k in keys:
            short = str(k)[:10]
            base = short
            n = 1
            while short in used:
                suffix = str(n)
                short = base[:10 - len(suffix)] + suffix
                n += 1
            used.add(short)
            field_names.append(short)

        layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", name, "memory")
        provider = layer.dataProvider()
        provider.addAttributes(
            [QgsField(fn, QVariant.String) for fn in field_names])
        layer.updateFields()

        qgs_features = []
        for feature in features:
            geom_data = feature.get('geometry')
            if not geom_data:
                continue
            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                continue
            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue
            props = feature.get('properties') or {}
            qf = QgsFeature(layer.fields())
            qf.setGeometry(geom)
            qf.setAttributes(
                [str(props.get(k, '') or '') for k in keys])
            qgs_features.append(qf)
        if not qgs_features:
            return None
        provider.addFeatures(qgs_features)
        layer.updateExtents()
        return layer

    def export_batch_shp(self, all_data, district_geom_wgs84, out_dir,
                         crs_authid='EPSG:5174', radius_m=1000,
                         api_manager=None, progress_cb=None):
        """조회 데이터 일괄 SHP 내보내기 + 구역 주변(반경 1km) 지적 포함.

        'SHP 내보내기': 다운로드 폴더에 prom_around_area 폴더 생성과
        동일하게 out_dir 아래 vworld_batch_shp 폴더에 저장한다.
        반환: (저장된 파일 수, 폴더 경로)
        """
        target_dir = os.path.join(out_dir, 'vworld_batch_shp')
        os.makedirs(target_dir, exist_ok=True)
        saved = 0

        type_names = {
            'cadastral': '연속지적도',
            'land_forest': '토지임야정보',
            'land_character': '토지특성정보',
            'land_price': '개별공시지가',
            'land_use_plan': '토지이용계획',
            'land_owner': '토지소유자정보',
        }
        for data_type, label in type_names.items():
            features = (all_data or {}).get(data_type) or []
            if not features:
                continue
            if progress_cb:
                progress_cb(f"{label} 내보내는 중...")
            layer = self.build_memory_layer(features, label)
            if layer is None:
                continue
            path = os.path.join(target_dir, f"{data_type}.shp")
            ok, _ = self.export_shp(layer, path, crs_authid)
            if ok:
                saved += 1

        # 주변 1km 연속지적도 (구역 반경 1km 이내 주변 데이터 포함)
        if district_geom_wgs84 is not None and api_manager is not None \
                and not district_geom_wgs84.isEmpty():
            if progress_cb:
                progress_cb(f"주변 {radius_m}m 지적 조회 중...")
            try:
                crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")
                to_5186 = QgsCoordinateTransform(
                    crs_wgs84, crs_5186, QgsProject.instance())
                to_wgs84 = QgsCoordinateTransform(
                    crs_5186, crs_wgs84, QgsProject.instance())
                buffered = QgsGeometry(district_geom_wgs84)
                buffered.transform(to_5186)
                buffered = buffered.buffer(float(radius_m), 16)
                buffered.transform(to_wgs84)
                response = api_manager.get_cadastral_by_polygon(buffered)
                around = api_manager.parse_features(response)
                if around:
                    layer = self.build_memory_layer(around, '주변지적')
                    if layer is not None:
                        path = os.path.join(target_dir, 'around_cadastral.shp')
                        ok, _ = self.export_shp(layer, path, crs_authid)
                        if ok:
                            saved += 1
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"Around export error: {e}", "VWorld", Qgis.Warning)

        return saved, target_dir

    # ------------------------------------------------------------------
    # 지도 캡처
    # ------------------------------------------------------------------
    def capture_map(self, path):
        """현재 지도화면을 이미지로 저장 ('지도 캡처')"""
        try:
            self.iface.mapCanvas().saveAsImage(path)
            return True
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Map capture error: {e}", "VWorld", Qgis.Warning)
            return False


class LegendEditorDialog(QDialog):
    """범례편집 다이얼로그 ('범례편집')

    구간 수 / 컬러램프 / 구간값(최소·최대)을 수정해 단계구분(Graduated)
    렌더러로 지도에 즉시 반영한다.
    """

    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.setWindowTitle(f"범례편집 - {layer.name()}")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)

        top = QGridLayout()
        top.addWidget(QLabel("대상 필드(숫자):"), 0, 0)
        self.field_combo = QComboBox()
        for field in layer.fields():
            if field.isNumeric():
                self.field_combo.addItem(field.name())
        # 숫자 필드가 없으면 문자열 필드도 허용 (숫자 변환 시도)
        if self.field_combo.count() == 0:
            for field in layer.fields():
                self.field_combo.addItem(field.name())
        top.addWidget(self.field_combo, 0, 1)

        top.addWidget(QLabel("구간 수:"), 0, 2)
        self.class_spin = QSpinBox()
        self.class_spin.setRange(2, 12)
        self.class_spin.setValue(5)
        top.addWidget(self.class_spin, 0, 3)

        top.addWidget(QLabel("컬러램프:"), 1, 0)
        self.ramp_combo = QComboBox()
        style = QgsStyle.defaultStyle()
        preferred = ['Reds', 'Blues', 'Greens', 'Oranges', 'Purples',
                     'YlOrRd', 'RdYlGn', 'Spectral', 'Viridis']
        names = style.colorRampNames()
        for name in preferred:
            if name in names:
                self.ramp_combo.addItem(name)
        for name in names:
            if self.ramp_combo.findText(name) < 0:
                self.ramp_combo.addItem(name)
        top.addWidget(self.ramp_combo, 1, 1)

        calc_btn = QPushButton("구간 계산 (등간격)")
        calc_btn.clicked.connect(self.calculate_ranges)
        top.addWidget(calc_btn, 1, 2, 1, 2)
        layout.addLayout(top)

        self.range_table = QTableWidget()
        self.range_table.setColumnCount(3)
        self.range_table.setHorizontalHeaderLabels(
            ["최소값", "최대값", "라벨"])
        self.range_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        layout.addWidget(self.range_table)

        note = QLabel("※ 구간값을 직접 수정한 후 [적용]을 누르면 지도에 반영됩니다.")
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Apply).setText("적용")
        buttons.button(QDialogButtonBox.Close).setText("닫기")
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.calculate_ranges()

    def _field_values(self):
        field_name = self.field_combo.currentText()
        values = []
        for feature in self.layer.getFeatures():
            try:
                v = feature[field_name]
                if v is not None and str(v).strip() != '':
                    values.append(float(str(v).replace(',', '')))
            except (ValueError, TypeError, KeyError):
                continue
        return values

    def calculate_ranges(self):
        values = self._field_values()
        n = self.class_spin.value()
        if not values:
            self.range_table.setRowCount(0)
            return
        vmin, vmax = min(values), max(values)
        if vmax <= vmin:
            vmax = vmin + 1.0
        step = (vmax - vmin) / n
        self.range_table.setRowCount(n)
        for i in range(n):
            lower = vmin + step * i
            upper = vmin + step * (i + 1)
            self.range_table.setItem(i, 0, QTableWidgetItem(f"{lower:.2f}"))
            self.range_table.setItem(i, 1, QTableWidgetItem(f"{upper:.2f}"))
            self.range_table.setItem(
                i, 2, QTableWidgetItem(f"{lower:,.0f} ~ {upper:,.0f}"))

    def apply(self):
        field_name = self.field_combo.currentText()
        style = QgsStyle.defaultStyle()
        ramp = style.colorRamp(self.ramp_combo.currentText())
        if ramp is None:
            QMessageBox.warning(self, "오류", "컬러램프를 불러오지 못했습니다.")
            return
        n = self.range_table.rowCount()
        if n == 0:
            return
        ranges = []
        for i in range(n):
            try:
                lower = float(self.range_table.item(i, 0).text().replace(',', ''))
                upper = float(self.range_table.item(i, 1).text().replace(',', ''))
            except (ValueError, AttributeError):
                QMessageBox.warning(
                    self, "오류", f"{i + 1}행 구간값이 숫자가 아닙니다.")
                return
            label_item = self.range_table.item(i, 2)
            label = label_item.text() if label_item else f"{lower}~{upper}"
            symbol = QgsSymbol.defaultSymbol(self.layer.geometryType())
            ratio = i / (n - 1) if n > 1 else 0.0
            symbol.setColor(ramp.color(ratio))
            ranges.append(QgsRendererRange(lower, upper, symbol, label))
        renderer = QgsGraduatedSymbolRenderer(field_name, ranges)
        self.layer.setRenderer(renderer)
        self.layer.triggerRepaint()
        self.iface_refresh()

    def iface_refresh(self):
        try:
            from qgis.utils import iface
            if iface:
                iface.layerTreeView().refreshLayerSymbology(self.layer.id())
        except Exception:
            pass


class AttributeViewDialog(QDialog):
    """속성보기 다이얼로그 ('속성보기' + 엑셀 내보내기)"""

    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.setWindowTitle(f"속성보기 - {layer.name()}")
        self.setMinimumSize(760, 480)
        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        fields = [f.name() for f in layer.fields()]
        self.table.setColumnCount(len(fields))
        self.table.setHorizontalHeaderLabels(fields)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        features = list(layer.getFeatures())
        self.table.setRowCount(len(features))
        for i, feature in enumerate(features):
            for j, name in enumerate(fields):
                try:
                    v = feature[name]
                except KeyError:
                    v = ''
                self.table.setItem(
                    i, j, QTableWidgetItem('' if v is None else str(v)))
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        export_btn = QPushButton("엑셀 내보내기 (.xlsx)")
        export_btn.clicked.connect(self.export_xlsx)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._fields = fields
        self._features = features

    def export_xlsx(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "엑셀로 저장", f"{self.layer.name()}_속성.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        rows = []
        for feature in self._features:
            row = []
            for name in self._fields:
                try:
                    v = feature[name]
                except KeyError:
                    v = ''
                row.append('' if v is None else str(v))
            rows.append(row)
        saved = ExportManager.export_table_xlsx(self._fields, rows, path)
        QMessageBox.information(self, "저장 완료", f"저장됨: {saved}")


class LayerTab(QWidget):
    """'레이어' 탭 위젯 - 일반레이어(WMS) + 사용자레이어 + 공통기능"""

    def __init__(self, iface, layer_manager, get_api_key,
                 district_manager=None, get_all_data=None,
                 get_district_geometry=None, api_manager=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.manager = layer_manager
        self.get_api_key = get_api_key
        self.district_manager = district_manager
        self.get_all_data = get_all_data or (lambda: {})
        self.get_district_geometry = get_district_geometry or (lambda: None)
        self.api_manager = api_manager
        self._catalog_updating = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 일반레이어 그룹
        general_group = QGroupBox(
            "일반레이어 - VWorld 공간정보 (체크=지도에 표시)")
        general_layout = QVBoxLayout()
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("검색:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "키워드 입력 (예: 주거, 용도, 행정)")
        self.search_edit.textChanged.connect(self.filter_catalog)
        search_row.addWidget(self.search_edit)
        clear_all_btn = QPushButton("전체 선택 해제")
        clear_all_btn.clicked.connect(self.clear_all_wms)
        search_row.addWidget(clear_all_btn)
        general_layout.addLayout(search_row)

        self.catalog_table = QTableWidget()
        self.catalog_table.setColumnCount(3)
        self.catalog_table.setHorizontalHeaderLabels(["표시", "레이어명", "분류"])
        self.catalog_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.catalog_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.catalog_table.setMinimumHeight(170)
        self.catalog_table.itemChanged.connect(self.on_catalog_item_changed)
        general_layout.addWidget(self.catalog_table)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("레이어ID 직접 추가:"))
        self.custom_id_edit = QLineEdit()
        self.custom_id_edit.setPlaceholderText("예: lt_c_uq111")
        custom_row.addWidget(self.custom_id_edit)
        self.custom_name_edit = QLineEdit()
        self.custom_name_edit.setPlaceholderText("표시 이름")
        custom_row.addWidget(self.custom_name_edit)
        custom_add_btn = QPushButton("추가")
        custom_add_btn.clicked.connect(self.add_custom_wms)
        custom_row.addWidget(custom_add_btn)
        general_layout.addLayout(custom_row)
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)

        # 사용자레이어 그룹
        user_group = QGroupBox("사용자레이어 - 프로젝트 벡터 레이어 관리")
        user_layout = QVBoxLayout()
        list_row = QHBoxLayout()
        self.layer_list = QListWidget()
        self.layer_list.setMinimumHeight(130)
        list_row.addWidget(self.layer_list)
        btn_col = QVBoxLayout()
        refresh_btn = QPushButton("목록 새로고침")
        refresh_btn.clicked.connect(self.refresh_layer_list)
        btn_col.addWidget(refresh_btn)
        front_btn = QPushButton("위계 맨앞으로")
        front_btn.clicked.connect(self.bring_front)
        btn_col.addWidget(front_btn)
        zoom_btn = QPushButton("구역계로 이동")
        zoom_btn.clicked.connect(self.zoom_district)
        btn_col.addWidget(zoom_btn)
        legend_btn = QPushButton("범례편집")
        legend_btn.clicked.connect(self.edit_legend)
        btn_col.addWidget(legend_btn)
        attr_btn = QPushButton("속성보기")
        attr_btn.clicked.connect(self.view_attributes)
        btn_col.addWidget(attr_btn)
        dxf_btn = QPushButton("DXF 내보내기")
        dxf_btn.clicked.connect(self.export_dxf)
        btn_col.addWidget(dxf_btn)
        shp_btn = QPushButton("SHP 내보내기")
        shp_btn.clicked.connect(self.export_shp)
        btn_col.addWidget(shp_btn)
        btn_col.addStretch()
        list_row.addLayout(btn_col)
        user_layout.addLayout(list_row)
        note = QLabel("※ DXF에는 속성값 없이 도형만, SHP에는 도형+속성이 저장됩니다.")
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        user_layout.addWidget(note)
        user_group.setLayout(user_layout)
        layout.addWidget(user_group)

        # 공통기능 그룹 (일괄 내보내기/지도 캡처)
        common_group = QGroupBox("일괄 내보내기 / 지도 캡처")
        common_layout = QGridLayout()
        common_layout.addWidget(QLabel("일괄 SHP 좌표계:"), 0, 0)
        self.batch_crs_combo = QComboBox()
        for authid, label in BATCH_EXPORT_CRS_CHOICES:
            self.batch_crs_combo.addItem(label, authid)
        common_layout.addWidget(self.batch_crs_combo, 0, 1)
        self.around_cb = QCheckBox("구역 주변 1km 지적 포함")
        self.around_cb.setChecked(True)
        common_layout.addWidget(self.around_cb, 0, 2)
        batch_btn = QPushButton("SHP 일괄 내보내기 (조회 데이터 전체)")
        batch_btn.clicked.connect(self.batch_export)
        common_layout.addWidget(batch_btn, 1, 0, 1, 2)
        capture_btn = QPushButton("지도 캡처 (PNG 저장)")
        capture_btn.clicked.connect(self.capture_map)
        common_layout.addWidget(capture_btn, 1, 2)
        crs_note = QLabel(
            "※ EPSG:5174는 KLIS 호환용(구 측지계)으로 변환 시 수 m 오차가 "
            "있을 수 있습니다. 정밀 작업은 EPSG:5186 권장.")
        crs_note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        crs_note.setWordWrap(True)
        common_layout.addWidget(crs_note, 2, 0, 1, 3)
        common_group.setLayout(common_layout)
        layout.addWidget(common_group)
        layout.addStretch()

        self.populate_catalog()
        self.refresh_layer_list()

    # ------------------------------------------------------------------
    # 일반레이어
    # ------------------------------------------------------------------
    def populate_catalog(self, keyword=""):
        self._catalog_updating = True
        try:
            rows = [(lid, name, cat) for lid, name, cat in VWORLD_WMS_CATALOG
                    if not keyword or keyword in name or keyword in cat
                    or keyword.lower() in lid]
            self.catalog_table.setRowCount(len(rows))
            for i, (lid, name, cat) in enumerate(rows):
                cb = QTableWidgetItem()
                cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                cb.setCheckState(
                    Qt.Checked if self.manager.is_wms_active(lid)
                    else Qt.Unchecked)
                cb.setData(Qt.UserRole, (lid, name))
                self.catalog_table.setItem(i, 0, cb)
                self.catalog_table.setItem(i, 1, QTableWidgetItem(name))
                self.catalog_table.setItem(i, 2, QTableWidgetItem(cat))
        finally:
            self._catalog_updating = False

    def filter_catalog(self, text):
        self.populate_catalog(text.strip())

    def on_catalog_item_changed(self, item):
        if self._catalog_updating or item.column() != 0:
            return
        lid, name = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            api_key = (self.get_api_key() or '').strip()
            if not api_key:
                QMessageBox.warning(
                    self, "API 키 필요",
                    "VWorld API 키를 먼저 입력하세요 (상단 API 설정).")
                self._catalog_updating = True
                item.setCheckState(Qt.Unchecked)
                self._catalog_updating = False
                return
            if not self.manager.add_wms_layer(api_key, lid, name):
                QMessageBox.warning(
                    self, "레이어 오류",
                    f"WMS 레이어를 불러오지 못했습니다: {lid}\n"
                    "(API 키 또는 레이어ID 확인)")
                self._catalog_updating = True
                item.setCheckState(Qt.Unchecked)
                self._catalog_updating = False
        else:
            self.manager.remove_wms_layer(lid)

    def add_custom_wms(self):
        lid = self.custom_id_edit.text().strip().lower()
        name = self.custom_name_edit.text().strip() or lid
        if not lid:
            return
        api_key = (self.get_api_key() or '').strip()
        if not api_key:
            QMessageBox.warning(self, "API 키 필요",
                                "VWorld API 키를 먼저 입력하세요.")
            return
        if self.manager.add_wms_layer(api_key, lid, name):
            QMessageBox.information(
                self, "추가 완료", f"WMS 레이어 추가됨: {name} ({lid})")
        else:
            QMessageBox.warning(
                self, "추가 실패", f"레이어를 불러오지 못했습니다: {lid}")

    def clear_all_wms(self):
        count = self.manager.remove_all_wms_layers()
        self.populate_catalog(self.search_edit.text().strip())
        QMessageBox.information(
            self, "전체 해제", f"{count}개 WMS 레이어를 껐습니다.")

    # ------------------------------------------------------------------
    # 사용자레이어
    # ------------------------------------------------------------------
    def refresh_layer_list(self):
        self.layer_list.clear()
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                item = QListWidgetItem(layer.name())
                item.setData(Qt.UserRole, layer.id())
                self.layer_list.addItem(item)

    def _selected_layer(self):
        item = self.layer_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "선택 없음",
                                "사용자레이어 목록에서 레이어를 선택하세요.")
            return None
        layer = QgsProject.instance().mapLayer(item.data(Qt.UserRole))
        if layer is None:
            QMessageBox.warning(self, "레이어 없음",
                                "레이어가 삭제되었습니다. 목록을 새로고침하세요.")
        return layer

    def bring_front(self):
        layer = self._selected_layer()
        if layer is not None:
            self.manager.bring_to_front(layer)

    def zoom_district(self):
        if self.district_manager is not None:
            if not self.district_manager.zoom_to_district():
                QMessageBox.information(
                    self, "안내", "확정된 구역계가 없습니다.")

    def edit_legend(self):
        layer = self._selected_layer()
        if layer is None:
            return
        dialog = LegendEditorDialog(layer, self)
        dialog.exec_()

    def view_attributes(self):
        layer = self._selected_layer()
        if layer is None:
            return
        dialog = AttributeViewDialog(layer, self)
        dialog.show()

    def export_dxf(self):
        layer = self._selected_layer()
        if layer is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "DXF로 저장", f"{layer.name()}.dxf", "DXF (*.dxf)")
        if not path:
            return
        ok, msg = self.manager.export_dxf(layer, path)
        if ok:
            QMessageBox.information(
                self, "저장 완료",
                f"DXF 저장됨: {msg}\n※ 속성값 없이 도형만 저장됩니다.")
        else:
            QMessageBox.warning(self, "저장 실패", f"DXF 저장 실패: {msg}")

    def export_shp(self):
        layer = self._selected_layer()
        if layer is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "SHP로 저장", f"{layer.name()}.shp",
            "ESRI Shapefile (*.shp)")
        if not path:
            return
        crs_authid = self.batch_crs_combo.currentData()
        ok, msg = self.manager.export_shp(layer, path, crs_authid)
        if ok:
            QMessageBox.information(self, "저장 완료", f"SHP 저장됨: {msg}")
        else:
            QMessageBox.warning(self, "저장 실패", f"SHP 저장 실패: {msg}")

    # ------------------------------------------------------------------
    # 일괄 내보내기/캡처
    # ------------------------------------------------------------------
    def batch_export(self):
        all_data = self.get_all_data() or {}
        if not any(all_data.get(k) for k in
                   ('cadastral', 'land_use_plan', 'land_price')):
            QMessageBox.warning(
                self, "데이터 없음", "먼저 토지정보를 조회하세요.")
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "저장 폴더 선택", os.path.expanduser('~'))
        if not out_dir:
            return

        def progress(text):
            self.iface.statusBarIface().showMessage(text)
            QApplication.processEvents()

        district_geom = (self.get_district_geometry()
                         if self.around_cb.isChecked() else None)
        saved, target = self.manager.export_batch_shp(
            all_data, district_geom, out_dir,
            crs_authid=self.batch_crs_combo.currentData(),
            radius_m=1000,
            api_manager=self.api_manager,
            progress_cb=progress)
        self.iface.statusBarIface().clearMessage()
        QMessageBox.information(
            self, "일괄 내보내기 완료",
            f"{saved}개 SHP 파일을 저장했습니다.\n폴더: {target}")

    def capture_map(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "지도 캡처 저장", "map_capture.png", "PNG (*.png)")
        if not path:
            return
        if self.manager.capture_map(path):
            QMessageBox.information(self, "캡처 완료", f"저장됨: {path}")
        else:
            QMessageBox.warning(self, "캡처 실패", "지도 캡처에 실패했습니다.")

    def reset(self):
        """메인 reset_all 연동 (WMS 레이어는 유지 - 사용자가 명시적으로 해제)"""
        self.refresh_layer_list()
