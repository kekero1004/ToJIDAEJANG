# -*- coding: utf-8 -*-
"""
단면분석 모듈 ('입지분석 > 단면분석' 매뉴얼 이식)
- 지도에서 단면선 그리기 (최대 2개, 더블클릭/Enter 완료)
- OpenTopoData 고도 조회로 거리-고도 프로파일 산출
- 라인 차트 표시 + 마우스 호버 시 지도에 해당 지점 마커 표시
- 차트 확대보기 / 차트 데이터 엑셀 내보내기 / 단면 지우기

고도 출처: OpenTopoData srtm30m (terrain_analyzer와 동일, 참고용 정밀도)
"""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QFileDialog, QMessageBox, QApplication,
)
from qgis.core import (
    QgsProject, QgsGeometry, QgsPointXY, QgsWkbTypes,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog,
)
from qgis.gui import QgsRubberBand, QgsVertexMarker

from .chart_widget import ChartWidget, ChartZoomDialog
from .export_manager import ExportManager

MAX_SECTIONS = 2  # 단면선 최대 2개
SECTION_COLORS = [QColor(231, 76, 60), QColor(41, 128, 185)]


class SectionAnalyzer:
    """단면(프로파일) 분석 클래스 - terrain_analyzer의 고도 배치조회 재사용"""

    def __init__(self, terrain_analyzer):
        self.terrain_analyzer = terrain_analyzer
        self.crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        self.crs_5186 = QgsCoordinateReferenceSystem("EPSG:5186")

    def analyze(self, line_wgs84, sample_count=50):
        """단면선(WGS84 라인) → 거리-고도 프로파일.

        반환: {'profile': [(dist_m, elev_m)], 'points_wgs84': [(lon, lat)],
               'length_m', 'min_elev', 'max_elev', 'avg_elev'} 또는 None
        """
        if line_wgs84 is None or line_wgs84.isEmpty():
            return None
        try:
            to_5186 = QgsCoordinateTransform(
                self.crs_wgs84, self.crs_5186, QgsProject.instance())
            to_wgs84 = QgsCoordinateTransform(
                self.crs_5186, self.crs_wgs84, QgsProject.instance())

            line_5186 = QgsGeometry(line_wgs84)
            line_5186.transform(to_5186)
            length = line_5186.length()
            if length <= 0:
                return None

            sample_count = max(2, min(int(sample_count), 200))
            step = length / (sample_count - 1)

            distances = []
            points_wgs84 = []
            for i in range(sample_count):
                dist = min(i * step, length)
                point_geom = line_5186.interpolate(dist)
                if point_geom is None or point_geom.isEmpty():
                    continue
                point_geom.transform(to_wgs84)
                p = point_geom.asPoint()
                distances.append(dist)
                points_wgs84.append((p.x(), p.y()))

            if len(points_wgs84) < 2:
                return None

            locations = [f"{lat},{lon}" for lon, lat in points_wgs84]
            elevations = self.terrain_analyzer._get_elevations_batch(locations)
            if not elevations or len(elevations) != len(points_wgs84):
                return None

            profile = list(zip(distances, elevations))
            return {
                'profile': profile,
                'points_wgs84': points_wgs84,
                'length_m': length,
                'min_elev': min(elevations),
                'max_elev': max(elevations),
                'avg_elev': sum(elevations) / len(elevations),
            }
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Section analysis error: {e}", "VWorld", Qgis.Warning)
            return None


class SectionAnalysisTab(QWidget):
    """단면분석 탭 (입지분석 서브탭)"""

    requestDrawLine = pyqtSignal(int)  # 단면선 번호 (0/1) - main이 도구 활성화

    def __init__(self, iface, section_analyzer, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.analyzer = section_analyzer
        self.lines = [None] * MAX_SECTIONS        # WGS84 QgsGeometry
        self.results = [None] * MAX_SECTIONS
        self._bands = [None] * MAX_SECTIONS
        self._marker = None
        self._zoom_dialogs = [None] * MAX_SECTIONS
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        tool_group = QGroupBox(
            "단면선 그리기 (최대 2개, 더블클릭/Enter 완료, Esc 취소)")
        tool_layout = QHBoxLayout()
        self.draw_btns = []
        for i in range(MAX_SECTIONS):
            btn = QPushButton(f"단면선{i + 1} 그리기")
            btn.clicked.connect(lambda _, idx=i: self.requestDrawLine.emit(idx))
            tool_layout.addWidget(btn)
            self.draw_btns.append(btn)
        self.analyze_btn = QPushButton("분석")
        self.analyze_btn.setStyleSheet("font-weight: bold;")
        self.analyze_btn.clicked.connect(self.run_analysis)
        tool_layout.addWidget(self.analyze_btn)
        clear_btn = QPushButton("단면 지우기")
        clear_btn.clicked.connect(self.clear_sections)
        tool_layout.addWidget(clear_btn)
        tool_group.setLayout(tool_layout)
        layout.addWidget(tool_group)

        self.status_label = QLabel(
            "단면선을 그린 후 [분석]을 클릭하세요. "
            "차트에 마우스를 올리면 지도에 위치가 표시됩니다.")
        self.status_label.setStyleSheet("color: #2c3e50;")
        layout.addWidget(self.status_label)

        # 단면 차트 2개
        self.charts = []
        self.chart_groups = []
        for i in range(MAX_SECTIONS):
            group = QGroupBox(f"단면 {i + 1}")
            g_layout = QVBoxLayout()
            chart = ChartWidget(chart_type='line')
            chart.setMinimumHeight(200)
            chart.hoverIndexChanged.connect(
                lambda idx, sec=i: self.on_chart_hover(sec, idx))
            g_layout.addWidget(chart)
            btn_row = QHBoxLayout()
            zoom_btn = QPushButton("차트 확대보기")
            zoom_btn.clicked.connect(lambda _, sec=i: self.zoom_chart(sec))
            btn_row.addWidget(zoom_btn)
            export_btn = QPushButton("차트 내보내기 (엑셀)")
            export_btn.clicked.connect(lambda _, sec=i: self.export_chart(sec))
            btn_row.addWidget(export_btn)
            btn_row.addStretch()
            g_layout.addLayout(btn_row)
            group.setLayout(g_layout)
            layout.addWidget(group)
            self.charts.append(chart)
            self.chart_groups.append(group)

    # ------------------------------------------------------------------
    # 단면선 입력 (main에서 LineDrawTool 결과 전달)
    # ------------------------------------------------------------------
    def set_line(self, idx, line_wgs84):
        if idx < 0 or idx >= MAX_SECTIONS:
            return
        self.lines[idx] = line_wgs84
        self._show_line_band(idx, line_wgs84)
        self.status_label.setText(
            f"단면선{idx + 1} 설정 완료. [분석]을 클릭하세요.")

    def _show_line_band(self, idx, line_wgs84):
        canvas = self.iface.mapCanvas()
        if self._bands[idx] is not None:
            try:
                self._bands[idx].reset(QgsWkbTypes.LineGeometry)
                canvas.scene().removeItem(self._bands[idx])
            except Exception:
                pass
            self._bands[idx] = None
        if line_wgs84 is None:
            return
        band = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        band.setColor(SECTION_COLORS[idx % len(SECTION_COLORS)])
        band.setWidth(3)
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"),
            canvas.mapSettings().destinationCrs(), QgsProject.instance())
        canvas_geom = QgsGeometry(line_wgs84)
        try:
            canvas_geom.transform(transform)
            band.setToGeometry(canvas_geom, None)
        except Exception:
            pass
        self._bands[idx] = band
        canvas.refresh()

    # ------------------------------------------------------------------
    # 분석/차트
    # ------------------------------------------------------------------
    def run_analysis(self):
        if not any(line is not None for line in self.lines):
            QMessageBox.warning(
                self, "단면선 없음", "먼저 단면선을 그려주세요.")
            return
        self.analyze_btn.setEnabled(False)
        try:
            for i, line in enumerate(self.lines):
                if line is None:
                    continue
                self.status_label.setText(f"단면 {i + 1} 고도 조회 중...")
                QApplication.processEvents()
                result = self.analyzer.analyze(line)
                self.results[i] = result
                if result is None:
                    self.status_label.setText(
                        f"단면 {i + 1} 분석 실패 (고도 API 응답 없음 - "
                        "잠시 후 재시도)")
                    continue
                self.charts[i].set_data(
                    result['profile'],
                    f"단면 {i + 1}: 길이 {result['length_m']:,.0f}m / "
                    f"고도 {result['min_elev']:,.0f}~{result['max_elev']:,.0f}m")
                self.chart_groups[i].setTitle(
                    f"단면 {i + 1} - 길이 {result['length_m']:,.0f}m, "
                    f"평균고도 {result['avg_elev']:,.1f}m")
            if any(r is not None for r in self.results):
                self.status_label.setText(
                    "분석 완료. 차트에 마우스를 올리면 지도에 위치가 표시됩니다.")
        finally:
            self.analyze_btn.setEnabled(True)

    def on_chart_hover(self, section_idx, point_idx):
        canvas = self.iface.mapCanvas()
        result = self.results[section_idx]
        if point_idx < 0 or result is None or \
                point_idx >= len(result['points_wgs84']):
            if self._marker is not None:
                try:
                    canvas.scene().removeItem(self._marker)
                except Exception:
                    pass
                self._marker = None
                canvas.refresh()
            return
        lon, lat = result['points_wgs84'][point_idx]
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"),
            canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            point = transform.transform(QgsPointXY(lon, lat))
        except Exception:
            return
        if self._marker is None:
            self._marker = QgsVertexMarker(canvas)
            self._marker.setColor(QColor(231, 76, 60))
            self._marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
            self._marker.setIconSize(14)
            self._marker.setPenWidth(3)
        self._marker.setCenter(point)
        canvas.refresh()

    def zoom_chart(self, idx):
        result = self.results[idx]
        if result is None:
            QMessageBox.information(self, "안내", "먼저 [분석]을 실행하세요.")
            return
        dialog = ChartZoomDialog(
            result['profile'], f"단면 {idx + 1}", self)
        dialog.hoverIndexChanged.connect(
            lambda pi, sec=idx: self.on_chart_hover(sec, pi))
        dialog.show()
        self._zoom_dialogs[idx] = dialog

    def export_chart(self, idx):
        result = self.results[idx]
        if result is None:
            QMessageBox.information(self, "안내", "먼저 [분석]을 실행하세요.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "단면 데이터 저장", f"단면분석_{idx + 1}.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        rows = []
        for (dist, elev), (lon, lat) in zip(
                result['profile'], result['points_wgs84']):
            rows.append([f"{dist:.1f}", f"{elev:.1f}",
                         f"{lon:.6f}", f"{lat:.6f}"])
        saved = ExportManager.export_table_xlsx(
            ['거리(m)', '고도(m)', '경도', '위도'], rows, path)
        QMessageBox.information(self, "저장 완료", f"저장됨: {saved}")

    # ------------------------------------------------------------------
    def clear_sections(self):
        """단면 지우기 ('단면 지우기')"""
        self.lines = [None] * MAX_SECTIONS
        self.results = [None] * MAX_SECTIONS
        for i in range(MAX_SECTIONS):
            self._show_line_band(i, None)
            self.charts[i].set_data([], "")
            self.chart_groups[i].setTitle(f"단면 {i + 1}")
        if self._marker is not None:
            try:
                self.iface.mapCanvas().scene().removeItem(self._marker)
            except Exception:
                pass
            self._marker = None
        self.iface.mapCanvas().refresh()
        self.status_label.setText("단면이 초기화되었습니다.")

    def reset(self):
        self.clear_sections()

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if not any(r is not None for r in self.results):
            return None
        section = {'title': '입지분석 - 단면분석', 'kv': [], 'tables': []}
        for i, result in enumerate(self.results):
            if result is None:
                continue
            section['kv'].extend([
                (f"단면 {i + 1} 길이", f"{result['length_m']:,.1f} m"),
                (f"단면 {i + 1} 고도",
                 f"최저 {result['min_elev']:,.1f} / 최고 "
                 f"{result['max_elev']:,.1f} / 평균 "
                 f"{result['avg_elev']:,.1f} m"),
            ])
            section['tables'].append({
                'title': f"단면 {i + 1} 프로파일 (거리-고도)",
                'headers': ['거리(m)', '고도(m)', '경도', '위도'],
                'rows': [[f"{dist:,.1f}", f"{elev:,.1f}",
                          f"{lon:.6f}", f"{lat:.6f}"]
                         for (dist, elev), (lon, lat) in zip(
                             result['profile'], result['points_wgs84'])],
            })
        return section
