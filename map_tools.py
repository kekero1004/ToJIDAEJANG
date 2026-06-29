# -*- coding: utf-8 -*-
"""
맵 도구 모듈 (구역계 설정/단면분석용 QgsMapTool 모음)
- ParcelPickTool: 지도 클릭 → WGS84 좌표 시그널 (필지선택/삭제)
- PolygonDrawTool: 폴리곤 직접 그리기 (영역추가/영역삭제, 스냅 지원)
- LineDrawTool: 라인 그리기 (단면분석 단면선)

설계 규칙:
- 도구는 결과를 pyqtSignal로만 통지한다 (다이얼로그 직접 참조 금지).
- deactivate()에서 러버밴드를 반드시 정리한다.
- 좌표는 캔버스 CRS → EPSG:4326으로 변환해 전달한다.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.core import (
    QgsProject, QgsGeometry, QgsPointXY, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
)
from qgis.gui import QgsMapTool, QgsMapToolEmitPoint, QgsRubberBand


def canvas_point_to_wgs84(canvas, point_xy):
    """캔버스 CRS의 QgsPointXY → WGS84 QgsPointXY"""
    src_crs = canvas.mapSettings().destinationCrs()
    dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
    return transform.transform(point_xy)


def canvas_geometry_to_wgs84(canvas, geometry):
    """캔버스 CRS의 QgsGeometry → WGS84 QgsGeometry (복사본 반환)"""
    src_crs = canvas.mapSettings().destinationCrs()
    dst_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
    geom = QgsGeometry(geometry)
    geom.transform(transform)
    return geom


class ParcelPickTool(QgsMapToolEmitPoint):
    """필지선택 도구 - 클릭 지점의 WGS84 경위도를 시그널로 전달

    좌클릭: parcelClicked(lon, lat) / 우클릭 또는 Esc: finished()
    """

    parcelClicked = pyqtSignal(float, float)
    finished = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(QCursor(Qt.CrossCursor))

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.finished.emit()
            return
        if event.button() != Qt.LeftButton:
            return
        try:
            wgs84_point = canvas_point_to_wgs84(self.canvas(), event.mapPoint())
            self.parcelClicked.emit(wgs84_point.x(), wgs84_point.y())
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finished.emit()


class _BaseDrawTool(QgsMapTool):
    """폴리곤/라인 그리기 공통 베이스 (러버밴드 + 스냅 + 키 처리)"""

    canceled = pyqtSignal()

    def __init__(self, canvas, geometry_type):
        super().__init__(canvas)
        self.geometry_type = geometry_type
        self.points = []  # 캔버스 CRS QgsPointXY 목록
        self.rubber_band = None
        self.temp_band = None
        self.setCursor(QCursor(Qt.CrossCursor))

    # ------------------------------------------------------------------
    def _ensure_bands(self):
        if self.rubber_band is None:
            self.rubber_band = QgsRubberBand(self.canvas(), self.geometry_type)
            if self.geometry_type == QgsWkbTypes.PolygonGeometry:
                self.rubber_band.setColor(QColor(231, 76, 60, 100))
                self.rubber_band.setFillColor(QColor(231, 76, 60, 60))
            else:
                self.rubber_band.setColor(QColor(41, 128, 185, 200))
            self.rubber_band.setWidth(2)

    def _snap_or_map_point(self, event):
        """스냅 활성 시 스냅 포인트, 아니면 맵 포인트 (필지 모서리 스냅 지원)"""
        try:
            match = self.canvas().snappingUtils().snapToMap(event.pos())
            if match.isValid():
                return QgsPointXY(match.point())
        except Exception:
            pass
        return QgsPointXY(event.mapPoint())

    def _update_band(self, moving_point=None):
        self._ensure_bands()
        self.rubber_band.reset(self.geometry_type)
        pts = list(self.points)
        if moving_point is not None:
            pts = pts + [moving_point]
        for p in pts:
            self.rubber_band.addPoint(p, False)
        self.rubber_band.show()
        if pts:
            self.rubber_band.updatePosition()
        self.canvas().refresh()

    def _min_points(self):
        return 3 if self.geometry_type == QgsWkbTypes.PolygonGeometry else 2

    def _build_geometry_wgs84(self):
        if self.geometry_type == QgsWkbTypes.PolygonGeometry:
            geom = QgsGeometry.fromPolygonXY([list(self.points)])
        else:
            geom = QgsGeometry.fromPolylineXY(list(self.points))
        if geom.isEmpty():
            return None
        return canvas_geometry_to_wgs84(self.canvas(), geom)

    def _complete(self):
        if len(self.points) < self._min_points():
            self._cancel()
            return
        geom = self._build_geometry_wgs84()
        self._clear()
        if geom is not None and not geom.isEmpty():
            self._emit_completed(geom)
        else:
            self.canceled.emit()

    def _emit_completed(self, geom_wgs84):
        raise NotImplementedError

    def _cancel(self):
        self._clear()
        self.canceled.emit()

    def _clear(self):
        self.points = []
        if self.rubber_band is not None:
            self.rubber_band.reset(self.geometry_type)
            self.canvas().scene().removeItem(self.rubber_band)
            self.rubber_band = None
        self.canvas().refresh()

    # ------------------------------------------------------------------
    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            # 우클릭: 점이 충분하면 완료, 아니면 취소 (QGIS 관행)
            if len(self.points) >= self._min_points():
                self._complete()
            else:
                self._cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        self.points.append(self._snap_or_map_point(event))
        self._update_band()

    def canvasMoveEvent(self, event):
        if self.points:
            self._update_band(self._snap_or_map_point(event))

    def canvasDoubleClickEvent(self, event):
        # 더블클릭 완료 (직전 클릭으로 점이 1개 추가된 상태)
        if len(self.points) >= self._min_points():
            self._complete()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._complete()
        elif event.key() == Qt.Key_Backspace and self.points:
            self.points.pop()
            self._update_band()

    def deactivate(self):
        self._clear()
        super().deactivate()


class PolygonDrawTool(_BaseDrawTool):
    """영역그리기 도구 - 클릭으로 꼭짓점 추가, 더블클릭/Enter 완료, Esc 취소

    완료 시 polygonCompleted(WGS84 QgsGeometry) 시그널.
    필지 모서리에 마우스가 위치하면 프로젝트 스냅 설정에 따라 스냅된다.
    """

    polygonCompleted = pyqtSignal(QgsGeometry)

    def __init__(self, canvas):
        super().__init__(canvas, QgsWkbTypes.PolygonGeometry)

    def _emit_completed(self, geom_wgs84):
        self.polygonCompleted.emit(geom_wgs84)


class LineDrawTool(_BaseDrawTool):
    """단면선 그리기 도구 - 시작/끝점 클릭, 더블클릭 또는 Enter로 완료

    완료 시 lineCompleted(WGS84 QgsGeometry) 시그널.
    """

    lineCompleted = pyqtSignal(QgsGeometry)

    def __init__(self, canvas):
        super().__init__(canvas, QgsWkbTypes.LineGeometry)

    def _emit_completed(self, geom_wgs84):
        self.lineCompleted.emit(geom_wgs84)
