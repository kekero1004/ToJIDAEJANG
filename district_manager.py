# -*- coding: utf-8 -*-
"""
구역계 설정/편집 모듈 ('구역계 설정'·'구역계 편집' 매뉴얼 이식)
- 필지선택: 지도 클릭으로 필지 추가/삭제 (선택 필지 빨간 표시)
- 영역그리기: 폴리곤 직접 그리기(스냅)로 영역 추가/삭제
- 파일업로드: DXF / ZIP(SHP) 업로드 + 좌표계 선택 (DWG는 미지원 - DXF 변환 필요)
- 주소업로드: CSV/XLSX 주소 목록 → VWorld 주소검색 → 필지 추가
- 소재지 목록: 필지별 면적/편입면적/편입상태 + 제외 토글
  (면적 0.1m2 미만 + 부분편입 필지 자동제외, 해제 가능 - Check Point)
- 구역확정: 메모리 레이어 '구역계' 생성 + 스타일 편집(선/면 색상·두께·투명도)

좌표계 규약: 필지/구역 지오메트리는 EPSG:4326 보관, 면적 계산은 EPSG:5186.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QFileDialog, QMessageBox, QColorDialog, QSpinBox, QSlider,
    QApplication, QAbstractItemView,
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFillSymbol, QgsWkbTypes, Qgis, QgsMessageLog,
)
from qgis.gui import QgsRubberBand
from PyQt5.QtCore import QVariant

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu
from .cost_calculator import geojson_to_wkt

# 자동제외 기준 (Check Point: 면적 0.1m2 미만 + 부분편입 → 자동 제외)
AUTO_EXCLUDE_MIN_AREA = 0.1

# 파일 업로드 좌표계 후보 (업로드 시 좌표계 선택)
UPLOAD_CRS_CHOICES = [
    ('EPSG:5186', 'EPSG:5186 (Korea 2000 / Central Belt 2010)'),
    ('EPSG:5174', 'EPSG:5174 (Korean 1985 / Modified Central Belt - KLIS)'),
    ('EPSG:5181', 'EPSG:5181 (Korea 2000 / Central Belt)'),
    ('EPSG:5187', 'EPSG:5187 (Korea 2000 / East Belt 2010)'),
    ('EPSG:4326', 'EPSG:4326 (WGS84 경위도)'),
    ('EPSG:3857', 'EPSG:3857 (Web Mercator)'),
]

DISTRICT_LAYER_NAME = "구역계"


class DistrictManager:
    """구역계 상태/지오메트리/레이어 관리 클래스"""

    def __init__(self, iface, api_manager):
        self.iface = iface
        self.api_manager = api_manager
        # 필지선택 분: {pnu: {'properties', 'geometry'(GeoJSON), 'excluded',
        #                     'area', 'incl_area', 'incl_state'}}
        self.parcels = {}
        self.drawn_polygons = []   # 영역추가 분 [QgsGeometry WGS84]
        self.erased_polygons = []  # 영역삭제 분 [QgsGeometry WGS84]
        self.confirmed_geometry = None  # 구역확정된 WGS84 QgsGeometry
        self._preview_band = None
        self._editing_layer = False

        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    # ------------------------------------------------------------------
    # 필지/영역 편집
    # ------------------------------------------------------------------
    def _parcel_qgsgeom(self, parcel):
        wkt = geojson_to_wkt(parcel.get('geometry', {}) or {})
        if not wkt:
            return None
        geom = QgsGeometry.fromWkt(wkt)
        return None if geom.isEmpty() else geom

    def add_parcel_at(self, lon, lat):
        """클릭 좌표의 필지를 추가. 이미 선택된 필지면 무시.

        반환: ('added', pnu) / ('exists', pnu) / ('notfound', None)
        """
        # 이미 선택된 필지를 클릭한 경우
        existing = self.find_parcel_at(lon, lat)
        if existing:
            return ('exists', existing)

        feature = self.api_manager.get_parcel_by_point(lon, lat)
        if not feature:
            return ('notfound', None)
        props = feature.get('properties', {})
        pnu = str(props.get('pnu', '') or '')
        if not pnu:
            return ('notfound', None)
        self.parcels[pnu] = {
            'properties': props,
            'geometry': feature.get('geometry', {}),
            'excluded': False,
            'area': 0.0,
            'incl_area': 0.0,
            'incl_state': '전체편입',
        }
        self.update_preview()
        return ('added', pnu)

    def remove_parcel_at(self, lon, lat):
        """클릭 좌표를 포함하는 선택 필지를 제거. 반환: pnu 또는 None"""
        pnu = self.find_parcel_at(lon, lat)
        if pnu:
            del self.parcels[pnu]
            self.update_preview()
        return pnu

    def find_parcel_at(self, lon, lat):
        """선택 목록 중 좌표를 포함하는 필지 PNU 탐색 (API 호출 없음)"""
        point = QgsGeometry.fromWkt(f'POINT({lon} {lat})')
        for pnu, parcel in self.parcels.items():
            geom = self._parcel_qgsgeom(parcel)
            if geom is not None and geom.contains(point):
                return pnu
        return None

    def add_drawn_polygon(self, geom_wgs84):
        if geom_wgs84 and not geom_wgs84.isEmpty():
            self.drawn_polygons.append(QgsGeometry(geom_wgs84))
            self.update_preview()

    def add_erase_polygon(self, geom_wgs84):
        if geom_wgs84 and not geom_wgs84.isEmpty():
            self.erased_polygons.append(QgsGeometry(geom_wgs84))
            self.update_preview()

    # ------------------------------------------------------------------
    # 파일/주소 업로드
    # ------------------------------------------------------------------
    def load_vector_file(self, path, crs_authid):
        """DXF/SHP/ZIP(SHP)/GeoJSON 파일에서 폴리곤을 읽어 영역추가 분으로 등록.

        반환: 추가된 폴리곤 수. CAD(DXF)는 닫힌 폴리라인을 폴리곤으로 변환한다.
        (Check Point: CAD는 dwg/dxf - 단 OGR이 DWG를 지원하지 않아 DXF만,
         GIS는 zip 안에 shp/dbf/shx 필요)
        """
        lower = path.lower()
        if lower.endswith('.zip'):
            uri = f"/vsizip/{path}"
        else:
            uri = path

        layer = QgsVectorLayer(uri, "upload", "ogr")
        if not layer.isValid():
            # zip 안 shp 직접 지정 재시도
            if lower.endswith('.zip'):
                import zipfile
                try:
                    with zipfile.ZipFile(path) as zf:
                        shp_names = [n for n in zf.namelist()
                                     if n.lower().endswith('.shp')]
                    if shp_names:
                        layer = QgsVectorLayer(
                            f"/vsizip/{path}/{shp_names[0]}", "upload", "ogr")
                except Exception:
                    pass
        if not layer.isValid():
            return 0

        src_crs = QgsCoordinateReferenceSystem(crs_authid)
        transform = QgsCoordinateTransform(
            src_crs, self.crs_wgs84, QgsProject.instance())

        count = 0
        for feature in layer.getFeatures():
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            polygons = []
            if geom.type() == QgsWkbTypes.PolygonGeometry:
                polygons.append(QgsGeometry(geom))
            elif geom.type() == QgsWkbTypes.LineGeometry:
                # 닫힌 폴리라인 → 폴리곤 (CAD 구역계)
                lines = (geom.asMultiPolyline() if geom.isMultipart()
                         else [geom.asPolyline()])
                for line in lines:
                    if len(line) >= 4 and line[0] == line[-1]:
                        poly = QgsGeometry.fromPolygonXY([line])
                        if not poly.isEmpty():
                            polygons.append(poly)
            for poly in polygons:
                try:
                    poly.transform(transform)
                    if poly.isGeosValid() is False:
                        poly = poly.makeValid()
                    if not poly.isEmpty():
                        self.drawn_polygons.append(poly)
                        count += 1
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"Upload transform error: {e}", "VWorld", Qgis.Warning)
        if count:
            self.update_preview()
        return count

    def import_addresses(self, addr_list, progress_cb=None):
        """주소 목록 → VWorld 주소검색 → 필지 추가. 반환: (성공수, 실패목록)"""
        ok = 0
        failed = []
        for idx, addr in enumerate(addr_list):
            addr = str(addr).strip()
            if not addr:
                continue
            if progress_cb:
                progress_cb(idx + 1, len(addr_list), addr)
            found = self.api_manager.search_parcel_by_address(addr)
            if not found or not found.get('pnu'):
                failed.append(addr)
                continue
            status, _ = self.add_parcel_at(found['lon'], found['lat'])
            if status in ('added', 'exists'):
                ok += 1
            else:
                failed.append(addr)
        return ok, failed

    # ------------------------------------------------------------------
    # 지오메트리 산출/소재지 목록
    # ------------------------------------------------------------------
    def build_geometry(self, include_excluded=False):
        """현재 작업분(선택필지 ∪ 그리기영역 − 삭제영역)의 WGS84 지오메트리"""
        geoms = []
        for parcel in self.parcels.values():
            if parcel.get('excluded') and not include_excluded:
                continue
            geom = self._parcel_qgsgeom(parcel)
            if geom is not None:
                geoms.append(geom)
        geoms.extend(g for g in self.drawn_polygons if not g.isEmpty())

        if not geoms:
            return None
        region = QgsGeometry.unaryUnion(geoms)
        for erase in self.erased_polygons:
            if not erase.isEmpty():
                region = region.difference(erase)
        if region is None or region.isEmpty():
            return None
        # 제외 필지 영역도 구역에서 빼기 (그리기영역에 걸친 제외 필지 반영)
        if not include_excluded:
            for parcel in self.parcels.values():
                if not parcel.get('excluded'):
                    continue
                geom = self._parcel_qgsgeom(parcel)
                if geom is not None:
                    region = region.difference(geom)
        return None if (region is None or region.isEmpty()) else region

    def refresh_location_list(self, auto_exclude_tiny=True,
                              exclude_partial=False, progress_cb=None):
        """소재지 목록 갱신 ([적용] 단계).

        그리기영역이 있으면 VWorld에 폴리곤 질의해 교차 필지를 보강하고,
        필지별 (면적, 편입면적, 편입상태)를 EPSG:5186 기준으로 계산한다.
        자동제외: 편입면적 0.1m2 미만 + 부분편입 필지 (해제 가능).
        반환: 소재지 행 목록.
        """
        region_all = self.build_geometry(include_excluded=True)
        if region_all is None:
            return []

        # 그리기 영역에 걸친 필지 보강 조회
        if self.drawn_polygons:
            if progress_cb:
                progress_cb("구역 내 필지 조회 중...")
            response = self.api_manager.get_cadastral_by_polygon(region_all)
            for feature in self.api_manager.parse_features(response):
                props = feature.get('properties', {})
                pnu = str(props.get('pnu', '') or '')
                if pnu and pnu not in self.parcels:
                    self.parcels[pnu] = {
                        'properties': props,
                        'geometry': feature.get('geometry', {}),
                        'excluded': False,
                        'area': 0.0,
                        'incl_area': 0.0,
                        'incl_state': '',
                    }

        transform = QgsCoordinateTransform(
            self.crs_wgs84, self.crs_5186, QgsProject.instance())
        region_5186 = QgsGeometry(region_all)
        try:
            region_5186.transform(transform)
        except Exception:
            region_5186 = None

        rows = []
        for pnu, parcel in self.parcels.items():
            geom = self._parcel_qgsgeom(parcel)
            if geom is None:
                continue
            parcel_5186 = QgsGeometry(geom)
            try:
                parcel_5186.transform(transform)
            except Exception:
                continue
            area = parcel_5186.area()
            if region_5186 is not None and not region_5186.isEmpty():
                inter = parcel_5186.intersection(region_5186)
                incl_area = inter.area() if (inter and not inter.isEmpty()) else 0.0
            else:
                incl_area = area
            ratio = (incl_area / area * 100.0) if area > 0 else 0.0
            incl_state = '전체편입' if ratio >= 99.9 else '부분편입'
            parcel['area'] = area
            parcel['incl_area'] = incl_area
            parcel['incl_state'] = incl_state

            # 자동제외: 0.1m2 미만 + 부분편입 (Check Point)
            if auto_exclude_tiny and incl_area < AUTO_EXCLUDE_MIN_AREA \
                    and incl_state == '부분편입':
                parcel['excluded'] = True
            if exclude_partial and incl_state == '부분편입':
                parcel['excluded'] = True

            props = parcel.get('properties', {})
            jibun = str(props.get('jibun', '') or '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)
            rows.append({
                'pnu': pnu,
                'jibun': jibun,
                'addr': str(props.get('addr', '') or ''),
                'jimok': jimok,
                'area': area,
                'incl_area': incl_area,
                'incl_state': incl_state,
                'excluded': parcel['excluded'],
            })
        self.update_preview()
        return rows

    def set_parcel_excluded(self, pnu, excluded):
        if pnu in self.parcels:
            self.parcels[pnu]['excluded'] = bool(excluded)
            self.update_preview()

    # ------------------------------------------------------------------
    # 구역확정/레이어
    # ------------------------------------------------------------------
    def confirm(self):
        """구역확정: 최종 지오메트리 확정 + '구역계' 메모리 레이어 생성/갱신"""
        region = self.build_geometry()
        if region is None:
            return None
        self.confirmed_geometry = region
        self._update_district_layer(region)
        self.clear_preview()
        return region

    def get_district_layer(self):
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == DISTRICT_LAYER_NAME and \
                    isinstance(layer, QgsVectorLayer):
                return layer
        return None

    def _update_district_layer(self, geom_wgs84):
        layer = self.get_district_layer()
        if layer is None:
            layer = QgsVectorLayer(
                "Polygon?crs=EPSG:4326", DISTRICT_LAYER_NAME, "memory")
            provider = layer.dataProvider()
            provider.addAttributes([
                QgsField("name", QVariant.String),
                QgsField("area_m2", QVariant.Double),
            ])
            layer.updateFields()
            QgsProject.instance().addMapLayer(layer)
            self.apply_layer_style(layer)
        provider = layer.dataProvider()
        provider.truncate()

        transform = QgsCoordinateTransform(
            self.crs_wgs84, self.crs_5186, QgsProject.instance())
        geom_5186 = QgsGeometry(geom_wgs84)
        try:
            geom_5186.transform(transform)
            area = geom_5186.area()
        except Exception:
            area = 0.0

        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry(geom_wgs84))
        feature.setAttributes([DISTRICT_LAYER_NAME, area])
        provider.addFeatures([feature])
        layer.updateExtents()
        layer.triggerRepaint()
        return layer

    def apply_layer_style(self, layer=None, line_color='#e74c3c',
                          fill_color='#e74c3c', line_width=0.8, opacity=25):
        """구역계 스타일 편집 (선/면 색상, 두께, 투명도) - '구역계 스타일 편집'"""
        layer = layer or self.get_district_layer()
        if layer is None:
            return False
        fill = QColor(fill_color)
        fill.setAlpha(int(255 * opacity / 100.0))
        symbol = QgsFillSymbol.createSimple({
            'color': fill.name(QColor.HexArgb),
            'outline_color': line_color,
            'outline_width': str(line_width),
            'outline_style': 'solid',
        })
        layer.renderer().setSymbol(symbol)
        layer.triggerRepaint()
        return True

    def bring_layer_to_front(self):
        """위계조정: 구역계 레이어를 레이어 트리 맨 앞으로 ('위계 맨앞으로')"""
        layer = self.get_district_layer()
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

    def zoom_to_district(self):
        """구역계로 이동 ('구역계로 이동')"""
        canvas = self.iface.mapCanvas()
        geom = self.confirmed_geometry or self.build_geometry()
        if geom is None:
            return False
        transform = QgsCoordinateTransform(
            self.crs_wgs84, canvas.mapSettings().destinationCrs(),
            QgsProject.instance())
        canvas_geom = QgsGeometry(geom)
        try:
            canvas_geom.transform(transform)
        except Exception:
            return False
        rect = canvas_geom.boundingBox()
        rect.scale(1.2)
        canvas.setExtent(rect)
        canvas.refresh()
        return True

    def load_from_layer(self, layer):
        """기존 폴리곤 레이어의 (선택)피처를 구역계 작업분으로 불러오기
        ('결제한 구역계 보기 → 구역계 편집' 대응)"""
        if layer is None or layer.type() != layer.VectorLayer:
            return 0
        features = layer.selectedFeatures() or list(layer.getFeatures())
        transform = QgsCoordinateTransform(
            layer.crs(), self.crs_wgs84, QgsProject.instance())
        count = 0
        for feature in features:
            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue
            if geom.type() != QgsWkbTypes.PolygonGeometry:
                continue
            wgs = QgsGeometry(geom)
            try:
                wgs.transform(transform)
            except Exception:
                continue
            self.drawn_polygons.append(wgs)
            count += 1
        if count:
            self.update_preview()
        return count

    # ------------------------------------------------------------------
    # 버텍스(꼭짓점) 편집 - QGIS 기본 꼭짓점 도구 위임
    # ------------------------------------------------------------------
    def begin_vertex_edit(self):
        """구역계 레이어를 편집 모드로 전환하고 QGIS 꼭짓점 도구 활성화"""
        layer = self.get_district_layer()
        if layer is None:
            return False
        self.iface.setActiveLayer(layer)
        layer.startEditing()
        try:
            self.iface.actionVertexTool().trigger()
        except Exception:
            return False
        self._editing_layer = True
        return True

    def end_vertex_edit(self, commit=True):
        """버텍스 편집 종료. commit=True면 저장 후 확정 지오메트리 동기화"""
        layer = self.get_district_layer()
        if layer is None:
            self._editing_layer = False
            return False
        if layer.isEditable():
            if commit:
                layer.commitChanges()
            else:
                layer.rollBack()
        # 레이어 → 확정 지오메트리 재동기화
        geoms = [f.geometry() for f in layer.getFeatures()
                 if f.geometry() and not f.geometry().isEmpty()]
        if geoms:
            self.confirmed_geometry = QgsGeometry.unaryUnion(geoms)
        self._editing_layer = False
        return True

    # ------------------------------------------------------------------
    # 미리보기 러버밴드 (선택 필지 빨간 표시)
    # ------------------------------------------------------------------
    def update_preview(self):
        canvas = self.iface.mapCanvas()
        geom = self.build_geometry()
        if geom is None:
            self.clear_preview()
            return
        if self._preview_band is None:
            self._preview_band = QgsRubberBand(
                canvas, QgsWkbTypes.PolygonGeometry)
            self._preview_band.setColor(QColor(231, 76, 60, 180))
            self._preview_band.setFillColor(QColor(231, 76, 60, 70))
            self._preview_band.setWidth(2)
        transform = QgsCoordinateTransform(
            self.crs_wgs84, canvas.mapSettings().destinationCrs(),
            QgsProject.instance())
        canvas_geom = QgsGeometry(geom)
        try:
            canvas_geom.transform(transform)
            self._preview_band.setToGeometry(canvas_geom, None)
        except Exception:
            pass
        canvas.refresh()

    def clear_preview(self):
        if self._preview_band is not None:
            try:
                self._preview_band.reset(QgsWkbTypes.PolygonGeometry)
                self.iface.mapCanvas().scene().removeItem(self._preview_band)
            except Exception:
                pass
            self._preview_band = None
            self.iface.mapCanvas().refresh()

    def clear(self):
        """전체 초기화 (확정 지오메트리/레이어는 유지하지 않음)"""
        self.parcels = {}
        self.drawn_polygons = []
        self.erased_polygons = []
        self.confirmed_geometry = None
        self.clear_preview()


class MiniToolBar(QWidget):
    """맵툴 사용 중 캔버스 위에 떠 있는 미니 컨트롤바

    메인 다이얼로그를 hide()한 상태에서 [완료]/[취소]와 상태 안내를 제공한다.
    """

    doneClicked = pyqtSignal()
    cancelClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("구역계 도구")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        self.status_label = QLabel("진행 중...")
        self.status_label.setMinimumWidth(320)
        layout.addWidget(self.status_label)
        done_btn = QPushButton("완료")
        done_btn.clicked.connect(self.doneClicked.emit)
        layout.addWidget(done_btn)
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.cancelClicked.emit)
        layout.addWidget(cancel_btn)

    def set_status(self, text):
        self.status_label.setText(text)

    def closeEvent(self, event):
        self.cancelClicked.emit()
        super().closeEvent(event)


class DistrictTab(QWidget):
    """'구역계' 탭 위젯 - 도구/업로드/소재지 목록/스타일/확정 UI"""

    # mode: 'parcel_add' | 'parcel_remove' | 'area_add' | 'area_erase'
    toolRequested = pyqtSignal(str)
    vertexEditRequested = pyqtSignal()
    districtConfirmed = pyqtSignal(QgsGeometry)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._line_color = '#e74c3c'
        self._fill_color = '#e74c3c'
        self._updating_table = False
        self._vertex_editing = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "① 도구로 분석영역을 만들고 → ② [적용(소재지 목록 갱신)] → "
            "③ 목록 확인(제외 체크 조정) → ④ [구역 확정] 후 조회 조건에서 "
            "'구역계 사용'을 선택하세요.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #2c3e50; background: #ecf0f1; padding: 6px;")
        layout.addWidget(info)

        # 도구 그룹
        tool_group = QGroupBox("구역계 설정 도구 (지도에서 직접 선택/그리기)")
        tool_layout = QGridLayout()
        self.parcel_add_btn = QPushButton("필지추가 (클릭선택)")
        self.parcel_add_btn.clicked.connect(
            lambda: self.toolRequested.emit('parcel_add'))
        self.parcel_remove_btn = QPushButton("필지삭제")
        self.parcel_remove_btn.clicked.connect(
            lambda: self.toolRequested.emit('parcel_remove'))
        self.area_add_btn = QPushButton("영역추가 (그리기)")
        self.area_add_btn.clicked.connect(
            lambda: self.toolRequested.emit('area_add'))
        self.area_erase_btn = QPushButton("영역삭제 (그리기)")
        self.area_erase_btn.clicked.connect(
            lambda: self.toolRequested.emit('area_erase'))
        self.vertex_btn = QPushButton("버텍스편집 시작")
        self.vertex_btn.clicked.connect(self.toggle_vertex_edit)
        self.clear_btn = QPushButton("전체 초기화")
        self.clear_btn.clicked.connect(self.reset_working)
        tool_layout.addWidget(self.parcel_add_btn, 0, 0)
        tool_layout.addWidget(self.parcel_remove_btn, 0, 1)
        tool_layout.addWidget(self.area_add_btn, 0, 2)
        tool_layout.addWidget(self.area_erase_btn, 0, 3)
        tool_layout.addWidget(self.vertex_btn, 1, 0, 1, 2)
        tool_layout.addWidget(self.clear_btn, 1, 2, 1, 2)
        tool_group.setLayout(tool_layout)
        layout.addWidget(tool_group)

        # 업로드 그룹
        upload_group = QGroupBox("파일/주소 업로드")
        upload_layout = QGridLayout()
        self.file_btn = QPushButton("파일 업로드 (DXF / ZIP-SHP / GeoJSON)")
        self.file_btn.clicked.connect(self.upload_file)
        upload_layout.addWidget(self.file_btn, 0, 0)
        upload_layout.addWidget(QLabel("파일 좌표계:"), 0, 1)
        self.crs_combo = QComboBox()
        for authid, label in UPLOAD_CRS_CHOICES:
            self.crs_combo.addItem(label, authid)
        upload_layout.addWidget(self.crs_combo, 0, 2)
        self.addr_btn = QPushButton("주소 업로드 (CSV/XLSX 1열=주소)")
        self.addr_btn.clicked.connect(self.upload_addresses)
        upload_layout.addWidget(self.addr_btn, 1, 0)
        layer_btn = QPushButton("QGIS 레이어에서 불러오기")
        layer_btn.clicked.connect(self.load_from_project_layer)
        upload_layout.addWidget(layer_btn, 1, 1, 1, 2)
        note = QLabel("※ DWG는 미지원 - CAD에서 DXF로 변환 후 업로드 "
                      "(구역계 레이어 1개·닫힌 폴리라인 권장)")
        note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        upload_layout.addWidget(note, 2, 0, 1, 3)
        upload_group.setLayout(upload_layout)
        layout.addWidget(upload_group)

        # 자동제외 + 적용
        apply_group = QGroupBox("소재지 목록 ([적용] 시 편입면적 계산)")
        apply_layout = QVBoxLayout()
        cb_row = QHBoxLayout()
        self.auto_exclude_cb = QCheckBox(
            "0.1m2 미만 부분편입 필지 자동제외")
        self.auto_exclude_cb.setChecked(True)
        cb_row.addWidget(self.auto_exclude_cb)
        self.partial_exclude_cb = QCheckBox("부분편입 필지 전체 제외")
        cb_row.addWidget(self.partial_exclude_cb)
        self.apply_btn = QPushButton("적용 (소재지 목록 갱신)")
        self.apply_btn.clicked.connect(self.apply_region)
        cb_row.addWidget(self.apply_btn)
        apply_layout.addLayout(cb_row)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["제외", "PNU", "지번", "지목", "면적(m2)", "편입면적(m2)", "편입상태"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemChanged.connect(self.on_table_item_changed)
        self.table.setMinimumHeight(180)
        apply_layout.addWidget(self.table)
        self.count_label = QLabel("필지 0개 (제외 0개)")
        apply_layout.addWidget(self.count_label)
        apply_group.setLayout(apply_layout)
        layout.addWidget(apply_group)

        # 스타일 + 확정
        bottom_row = QHBoxLayout()
        style_group = QGroupBox("구역계 스타일 편집")
        style_layout = QGridLayout()
        self.line_color_btn = QPushButton("선 색상")
        self.line_color_btn.setStyleSheet(
            f"background-color: {self._line_color};")
        self.line_color_btn.clicked.connect(self.pick_line_color)
        style_layout.addWidget(self.line_color_btn, 0, 0)
        self.fill_color_btn = QPushButton("면 색상")
        self.fill_color_btn.setStyleSheet(
            f"background-color: {self._fill_color};")
        self.fill_color_btn.clicked.connect(self.pick_fill_color)
        style_layout.addWidget(self.fill_color_btn, 0, 1)
        style_layout.addWidget(QLabel("선 두께(0.1mm):"), 1, 0)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 50)
        self.width_spin.setValue(8)
        style_layout.addWidget(self.width_spin, 1, 1)
        style_layout.addWidget(QLabel("면 투명도(%):"), 2, 0)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(25)
        style_layout.addWidget(self.opacity_slider, 2, 1)
        style_btn = QPushButton("스타일 적용")
        style_btn.clicked.connect(self.apply_style)
        style_layout.addWidget(style_btn, 3, 0)
        front_btn = QPushButton("위계 맨앞으로")
        front_btn.clicked.connect(self.manager.bring_layer_to_front)
        style_layout.addWidget(front_btn, 3, 1)
        style_group.setLayout(style_layout)
        bottom_row.addWidget(style_group)

        confirm_group = QGroupBox("구역 확정")
        confirm_layout = QVBoxLayout()
        self.confirm_btn = QPushButton("구역 확정 (구역계 레이어 생성)")
        self.confirm_btn.setStyleSheet(
            "font-weight: bold; background-color: #27ae60; color: white; "
            "padding: 8px;")
        self.confirm_btn.clicked.connect(self.confirm_district)
        confirm_layout.addWidget(self.confirm_btn)
        zoom_btn = QPushButton("구역계로 이동")
        zoom_btn.clicked.connect(self.manager.zoom_to_district)
        confirm_layout.addWidget(zoom_btn)
        self.confirm_label = QLabel("확정된 구역계: 없음")
        self.confirm_label.setWordWrap(True)
        confirm_layout.addWidget(self.confirm_label)
        confirm_group.setLayout(confirm_layout)
        bottom_row.addWidget(confirm_group)
        layout.addLayout(bottom_row)

    # ------------------------------------------------------------------
    # 맵툴 결과 슬롯 (main에서 연결)
    # ------------------------------------------------------------------
    def handle_parcel_click(self, lon, lat, mode):
        if mode == 'parcel_add':
            status, pnu = self.manager.add_parcel_at(lon, lat)
            if status == 'notfound':
                self.count_label.setText("해당 지점의 필지를 찾지 못했습니다 "
                                         "(API 키/네트워크 확인)")
        else:  # parcel_remove
            self.manager.remove_parcel_at(lon, lat)
        self.update_count_label()

    def handle_polygon_drawn(self, geom_wgs84, mode):
        if mode == 'area_add':
            self.manager.add_drawn_polygon(geom_wgs84)
        else:  # area_erase
            self.manager.add_erase_polygon(geom_wgs84)
        self.update_count_label()

    # ------------------------------------------------------------------
    # 업로드
    # ------------------------------------------------------------------
    def upload_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "구역계 파일 선택", "",
            "구역계 파일 (*.dxf *.zip *.shp *.geojson *.json);;모든 파일 (*)")
        if not path:
            return
        if path.lower().endswith('.dwg'):
            QMessageBox.warning(
                self, "미지원 형식",
                "DWG 파일은 직접 지원되지 않습니다.\n"
                "CAD에서 DXF로 변환한 후 업로드해 주세요.")
            return
        crs_authid = self.crs_combo.currentData()
        count = self.manager.load_vector_file(path, crs_authid)
        if count == 0:
            QMessageBox.warning(
                self, "업로드 실패",
                "폴리곤(또는 닫힌 폴리라인)을 읽지 못했습니다.\n\n"
                "확인사항:\n"
                "1. CAD: .dxf 형식, 구역계 레이어 1개, 닫힌 도형\n"
                "2. GIS: .zip 안에 .shp/.dbf/.shx 포함\n"
                "3. 좌표계 선택이 올바른지")
            return
        QMessageBox.information(
            self, "업로드 완료",
            f"{count}개 폴리곤을 영역으로 추가했습니다.\n"
            "[적용]을 눌러 소재지 목록을 갱신하세요.")
        self.update_count_label()

    def upload_addresses(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "주소 목록 선택", "",
            "주소 목록 (*.csv *.xlsx *.txt);;모든 파일 (*)")
        if not path:
            return
        addr_list = self._read_address_file(path)
        if not addr_list:
            QMessageBox.warning(
                self, "읽기 실패",
                "주소를 읽지 못했습니다.\n1열에 주소(지번주소)를 입력한 "
                "CSV/XLSX 파일을 사용하세요.")
            return
        if len(addr_list) > 500:
            QMessageBox.warning(
                self, "행 수 초과",
                f"주소가 {len(addr_list)}건입니다. 한 번에 최대 500건까지 "
                "처리합니다 (앞 500건만 진행).")
            addr_list = addr_list[:500]

        def progress(idx, total, addr):
            self.count_label.setText(f"주소 변환 중 {idx}/{total}: {addr[:30]}")
            QApplication.processEvents()

        ok, failed = self.manager.import_addresses(addr_list, progress)
        msg = f"성공 {ok}건 / 실패 {len(failed)}건"
        if failed:
            msg += "\n\n실패 주소(최대 10건):\n" + "\n".join(failed[:10])
        QMessageBox.information(self, "주소 업로드 결과", msg)
        self.update_count_label()

    @staticmethod
    def _read_address_file(path):
        """CSV/XLSX/TXT 1열 주소 목록 읽기 (헤더 행 자동 감지)"""
        addrs = []
        lower = path.lower()
        try:
            if lower.endswith('.xlsx'):
                import openpyxl
                wb = openpyxl.load_workbook(path, read_only=True)
                ws = wb.active
                for row in ws.iter_rows(values_only=True):
                    if row and row[0] is not None:
                        addrs.append(str(row[0]).strip())
                wb.close()
            else:
                import csv
                for encoding in ('utf-8-sig', 'cp949', 'utf-8'):
                    try:
                        with open(path, newline='', encoding=encoding) as f:
                            addrs = [row[0].strip() for row in csv.reader(f)
                                     if row and row[0].strip()]
                        break
                    except (UnicodeDecodeError, IndexError):
                        addrs = []
                        continue
        except ImportError:
            return []
        except Exception:
            return []
        # 헤더 행 제거 ('주소' 등 키워드)
        if addrs and any(k in addrs[0] for k in ('주소', 'PNU', 'pnu', 'address')):
            addrs = addrs[1:]
        return addrs

    def load_from_project_layer(self):
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
                  if isinstance(lyr, QgsVectorLayer)
                  and lyr.geometryType() == QgsWkbTypes.PolygonGeometry
                  and lyr.name() != DISTRICT_LAYER_NAME]
        if not layers:
            QMessageBox.warning(self, "레이어 없음",
                                "프로젝트에 폴리곤 레이어가 없습니다.")
            return
        from qgis.PyQt.QtWidgets import QInputDialog
        names = [lyr.name() for lyr in layers]
        name, ok = QInputDialog.getItem(
            self, "레이어 선택",
            "구역계로 불러올 폴리곤 레이어 선택\n(선택 피처가 있으면 선택분만):",
            names, 0, False)
        if not ok:
            return
        layer = layers[names.index(name)]
        count = self.manager.load_from_layer(layer)
        QMessageBox.information(
            self, "불러오기 완료",
            f"{count}개 폴리곤을 영역으로 추가했습니다.\n"
            "[적용]을 눌러 소재지 목록을 갱신하세요.")
        self.update_count_label()

    # ------------------------------------------------------------------
    # 적용/목록/확정
    # ------------------------------------------------------------------
    def apply_region(self):
        if not self.manager.parcels and not self.manager.drawn_polygons:
            QMessageBox.warning(
                self, "영역 없음",
                "먼저 필지선택/영역그리기/업로드로 영역을 만들어 주세요.")
            return
        self.apply_btn.setEnabled(False)
        try:
            def progress(text):
                self.count_label.setText(text)
                QApplication.processEvents()

            rows = self.manager.refresh_location_list(
                auto_exclude_tiny=self.auto_exclude_cb.isChecked(),
                exclude_partial=self.partial_exclude_cb.isChecked(),
                progress_cb=progress)
            self.populate_table(rows)
        finally:
            self.apply_btn.setEnabled(True)

    def populate_table(self, rows):
        self._updating_table = True
        try:
            self.table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                cb_item = QTableWidgetItem()
                cb_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                cb_item.setCheckState(
                    Qt.Checked if row['excluded'] else Qt.Unchecked)
                cb_item.setData(Qt.UserRole, row['pnu'])
                self.table.setItem(i, 0, cb_item)
                self.table.setItem(i, 1, QTableWidgetItem(row['pnu']))
                self.table.setItem(
                    i, 2, QTableWidgetItem(row['jibun'] or row['addr']))
                self.table.setItem(i, 3, QTableWidgetItem(row['jimok']))
                self.table.setItem(
                    i, 4, QTableWidgetItem(f"{row['area']:,.2f}"))
                self.table.setItem(
                    i, 5, QTableWidgetItem(f"{row['incl_area']:,.2f}"))
                self.table.setItem(
                    i, 6, QTableWidgetItem(row['incl_state']))
        finally:
            self._updating_table = False
        self.update_count_label()

    def on_table_item_changed(self, item):
        if self._updating_table or item.column() != 0:
            return
        pnu = item.data(Qt.UserRole)
        self.manager.set_parcel_excluded(pnu, item.checkState() == Qt.Checked)
        self.update_count_label()

    def update_count_label(self):
        total = len(self.manager.parcels)
        excluded = sum(1 for p in self.manager.parcels.values()
                       if p.get('excluded'))
        drawn = len(self.manager.drawn_polygons)
        erased = len(self.manager.erased_polygons)
        self.count_label.setText(
            f"필지 {total}개 (제외 {excluded}개) / 그리기영역 {drawn}개 / "
            f"삭제영역 {erased}개")

    def confirm_district(self):
        geom = self.manager.confirm()
        if geom is None:
            QMessageBox.warning(
                self, "구역확정 실패",
                "확정할 영역이 없습니다. 필지선택 또는 영역그리기 후 "
                "[적용]을 먼저 실행하세요.")
            return
        self.apply_style()
        # 면적 표시 (EPSG:5186)
        transform = QgsCoordinateTransform(
            self.manager.crs_wgs84, self.manager.crs_5186,
            QgsProject.instance())
        geom_5186 = QgsGeometry(geom)
        try:
            geom_5186.transform(transform)
            area = geom_5186.area()
        except Exception:
            area = 0.0
        self.confirm_label.setText(
            f"확정된 구역계: 면적 {area:,.2f} m2 "
            f"({area / 10000.0:,.4f} ha)\n조회 조건에서 '구역계 사용'을 "
            "체크하면 이 구역으로 조회합니다.")
        self.districtConfirmed.emit(geom)
        QMessageBox.information(
            self, "구역확정 완료",
            f"구역계가 확정되었습니다.\n면적: {area:,.2f} m2\n\n"
            f"'{DISTRICT_LAYER_NAME}' 레이어가 지도에 추가되었으며,\n"
            "조회 조건에서 '구역계 사용'을 체크해 토지정보를 조회하세요.")

    # ------------------------------------------------------------------
    # 스타일/버텍스/초기화
    # ------------------------------------------------------------------
    def pick_line_color(self):
        color = QColorDialog.getColor(QColor(self._line_color), self, "선 색상")
        if color.isValid():
            self._line_color = color.name()
            self.line_color_btn.setStyleSheet(
                f"background-color: {self._line_color};")

    def pick_fill_color(self):
        color = QColorDialog.getColor(QColor(self._fill_color), self, "면 색상")
        if color.isValid():
            self._fill_color = color.name()
            self.fill_color_btn.setStyleSheet(
                f"background-color: {self._fill_color};")

    def apply_style(self):
        ok = self.manager.apply_layer_style(
            line_color=self._line_color,
            fill_color=self._fill_color,
            line_width=self.width_spin.value() / 10.0,
            opacity=self.opacity_slider.value())
        if not ok:
            QMessageBox.information(
                self, "안내", "구역계 레이어가 아직 없습니다. "
                "[구역 확정] 후 스타일을 적용하세요.")

    def toggle_vertex_edit(self):
        if not self._vertex_editing:
            if not self.manager.get_district_layer():
                QMessageBox.information(
                    self, "안내",
                    "버텍스편집은 [구역 확정] 후 구역계 레이어에서 가능합니다.\n"
                    "(지적 불부합으로 남은 지적선 꼭짓점을 클릭해 삭제)")
                return
            if self.manager.begin_vertex_edit():
                self._vertex_editing = True
                self.vertex_btn.setText("버텍스편집 종료 (저장)")
                self.vertexEditRequested.emit()
        else:
            self.manager.end_vertex_edit(commit=True)
            self._vertex_editing = False
            self.vertex_btn.setText("버텍스편집 시작")

    def reset_working(self):
        reply = QMessageBox.question(
            self, "전체 초기화",
            "선택 필지/그리기 영역을 모두 초기화할까요?\n"
            "(확정된 구역계 레이어는 유지됩니다)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        confirmed = self.manager.confirmed_geometry
        self.manager.clear()
        self.manager.confirmed_geometry = confirmed
        self.table.setRowCount(0)
        self.update_count_label()

    def reset(self):
        """전체 reset (메인 reset_all 연동)"""
        if self._vertex_editing:
            self.manager.end_vertex_edit(commit=False)
            self._vertex_editing = False
            self.vertex_btn.setText("버텍스편집 시작")
        self.manager.clear()
        self.table.setRowCount(0)
        self.confirm_label.setText("확정된 구역계: 없음")
        self.update_count_label()

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        parcels = self.manager.parcels
        if not parcels and self.manager.confirmed_geometry is None:
            return None
        section = {'title': '구역계 (소재지 목록)', 'kv': [], 'tables': []}

        geom = self.manager.confirmed_geometry
        if geom is not None and not geom.isEmpty():
            transform = QgsCoordinateTransform(
                self.manager.crs_wgs84, self.manager.crs_5186,
                QgsProject.instance())
            geom_5186 = QgsGeometry(geom)
            try:
                geom_5186.transform(transform)
                area = geom_5186.area()
                section['kv'].append(
                    ('확정 구역계 면적',
                     f"{area:,.2f} m2 ({area / 10000.0:,.4f} ha)"))
            except Exception:
                pass

        if parcels:
            excluded = sum(1 for p in parcels.values() if p.get('excluded'))
            section['kv'].extend([
                ('소재지 필지 수', f"{len(parcels)}개 (제외 {excluded}개)"),
                ('그리기영역/삭제영역',
                 f"{len(self.manager.drawn_polygons)}개 / "
                 f"{len(self.manager.erased_polygons)}개"),
            ])
            rows = []
            for pnu, parcel in parcels.items():
                props = parcel.get('properties', {})
                jibun = str(props.get('jibun', '') or '')
                jimok = extract_jimok_from_jibun(jibun)
                if jimok == '미분류':
                    jimok = extract_jimok_from_pnu(pnu)
                rows.append([
                    pnu, jibun or str(props.get('addr', '') or ''), jimok,
                    f"{parcel.get('area', 0):,.2f}",
                    f"{parcel.get('incl_area', 0):,.2f}",
                    parcel.get('incl_state', ''),
                    '제외' if parcel.get('excluded') else '포함',
                ])
            section['tables'].append({
                'title': '소재지 목록',
                'headers': ['PNU', '지번', '지목', '면적(m2)',
                            '편입면적(m2)', '편입상태', '포함여부'],
                'rows': rows[:200],
            })
        return section
