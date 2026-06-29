# -*- coding: utf-8 -*-
"""
차트 위젯 모듈
- 파이 차트 및 바 차트 그리기
- 라인 차트 (단면분석 고도 프로파일용, 마우스 호버 연동)
"""

from qgis.PyQt.QtCore import Qt, QRectF, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen, QBrush
from qgis.PyQt.QtWidgets import QWidget, QDialog, QVBoxLayout


class ChartWidget(QWidget):
    """차트 위젯 - 파이/바/라인 차트 그리기

    'line' 타입은 data를 [(x값, y값), ...] 숫자쌍으로 해석하며
    (단면분석: x=거리 m, y=고도 m), 마우스 호버 시 최근접 데이터
    인덱스를 hoverIndexChanged 시그널로 알린다.
    """

    hoverIndexChanged = pyqtSignal(int)  # 라인 차트 호버 인덱스 (-1 = 벗어남)

    # 차트 색상 팔레트
    COLORS = [
        QColor(65, 131, 215),   # 파랑
        QColor(231, 76, 60),    # 빨강
        QColor(46, 204, 113),   # 초록
        QColor(241, 196, 15),   # 노랑
        QColor(155, 89, 182),   # 보라
        QColor(230, 126, 34),   # 주황
        QColor(52, 73, 94),     # 남색
        QColor(26, 188, 156),   # 청록
        QColor(192, 57, 43),    # 진빨강
        QColor(142, 68, 173),   # 진보라
        QColor(39, 174, 96),    # 진초록
        QColor(243, 156, 18),   # 진노랑
    ]

    def __init__(self, chart_type='pie', parent=None):
        super().__init__(parent)
        self.chart_type = chart_type  # 'pie' / 'bar' / 'line'
        self.data = []  # [(label, value), ...] - line은 [(x, y), ...]
        self.title = ""
        self.hover_index = -1
        self._line_geom = None  # (margin_left, chart_width, x_min, x_max)
        self.setMinimumSize(300, 250)
        if chart_type == 'line':
            self.setMouseTracking(True)

    def set_data(self, data, title=""):
        """차트 데이터 설정 - data는 [(label, value), ...] 형식"""
        self.data = data
        self.title = title
        self.hover_index = -1
        self.update()

    def paintEvent(self, event):
        if not self.data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 배경색
        painter.fillRect(self.rect(), QColor(255, 255, 255))

        if self.chart_type == 'pie':
            self._draw_pie_chart(painter)
        elif self.chart_type == 'line':
            self._draw_line_chart(painter)
        else:
            self._draw_bar_chart(painter)

        painter.end()

    # ------------------------------------------------------------------
    # 라인 차트 (단면분석 고도 프로파일)
    # ------------------------------------------------------------------
    def _draw_line_chart(self, painter):
        """라인 차트 그리기 - data는 [(x, y), ...] 숫자쌍"""
        width = self.width()
        height = self.height()

        if self.title:
            painter.setPen(QPen(Qt.black))
            title_font = QFont()
            title_font.setBold(True)
            title_font.setPointSize(10)
            painter.setFont(title_font)
            painter.drawText(10, 20, self.title)

        margin_left = 60
        margin_right = 20
        margin_top = 35
        margin_bottom = 35
        chart_width = width - margin_left - margin_right
        chart_height = height - margin_top - margin_bottom
        if chart_width <= 0 or chart_height <= 0 or len(self.data) < 2:
            self._line_geom = None
            return

        xs = [float(p[0]) for p in self.data]
        ys = [float(p[1]) for p in self.data]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        if x_max <= x_min:
            x_max = x_min + 1.0
        # Y 범위에 여유 5%
        y_pad = max((y_max - y_min) * 0.05, 1.0)
        y_min -= y_pad
        y_max += y_pad
        self._line_geom = (margin_left, chart_width, x_min, x_max)

        def px(x):
            return margin_left + (x - x_min) / (x_max - x_min) * chart_width

        def py(y):
            return margin_top + chart_height - (y - y_min) / (y_max - y_min) * chart_height

        # 그리드 + 축 눈금
        grid_pen = QPen(QColor(220, 220, 220), 1, Qt.DashLine)
        label_font = QFont()
        label_font.setPointSize(7)
        painter.setFont(label_font)
        for i in range(5):
            gy = margin_top + chart_height * i / 4
            val = y_max - (y_max - y_min) * i / 4
            painter.setPen(grid_pen)
            painter.drawLine(margin_left, int(gy), margin_left + chart_width, int(gy))
            painter.setPen(QPen(Qt.black))
            painter.drawText(5, int(gy + 4), f"{val:,.0f}m")
        for i in range(5):
            gx = margin_left + chart_width * i / 4
            val = x_min + (x_max - x_min) * i / 4
            painter.setPen(grid_pen)
            painter.drawLine(int(gx), margin_top, int(gx), margin_top + chart_height)
            painter.setPen(QPen(Qt.black))
            painter.drawText(int(gx - 15), margin_top + chart_height + 15, f"{val:,.0f}m")

        # 축
        painter.setPen(QPen(Qt.black, 1))
        painter.drawLine(margin_left, margin_top, margin_left, margin_top + chart_height)
        painter.drawLine(margin_left, margin_top + chart_height,
                         margin_left + chart_width, margin_top + chart_height)

        # 면 채움 (프로파일 아래)
        fill_color = QColor(65, 131, 215, 60)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill_color))
        from qgis.PyQt.QtGui import QPolygonF
        from qgis.PyQt.QtCore import QPointF
        poly = QPolygonF()
        poly.append(QPointF(px(xs[0]), margin_top + chart_height))
        for x, y in zip(xs, ys):
            poly.append(QPointF(px(x), py(y)))
        poly.append(QPointF(px(xs[-1]), margin_top + chart_height))
        painter.drawPolygon(poly)

        # 프로파일 선
        line_pen = QPen(self.COLORS[0], 2)
        painter.setPen(line_pen)
        painter.setBrush(Qt.NoBrush)
        for i in range(len(xs) - 1):
            painter.drawLine(
                int(px(xs[i])), int(py(ys[i])),
                int(px(xs[i + 1])), int(py(ys[i + 1])))

        # 호버 십자선 + 값 표시
        if 0 <= self.hover_index < len(self.data):
            hx = px(xs[self.hover_index])
            hy = py(ys[self.hover_index])
            painter.setPen(QPen(QColor(231, 76, 60), 1, Qt.DashLine))
            painter.drawLine(int(hx), margin_top, int(hx), margin_top + chart_height)
            painter.setPen(QPen(QColor(231, 76, 60)))
            painter.setBrush(QBrush(QColor(231, 76, 60)))
            painter.drawEllipse(int(hx) - 3, int(hy) - 3, 6, 6)
            painter.setPen(QPen(Qt.black))
            info = f"거리 {xs[self.hover_index]:,.0f}m / 고도 {ys[self.hover_index]:,.1f}m"
            painter.drawText(margin_left + 5, margin_top - 5, info)

    def mouseMoveEvent(self, event):
        if self.chart_type != 'line' or not self.data or not self._line_geom:
            super().mouseMoveEvent(event)
            return
        margin_left, chart_width, x_min, x_max = self._line_geom
        if chart_width <= 0:
            return
        mx = event.pos().x()
        ratio = (mx - margin_left) / chart_width
        if ratio < 0 or ratio > 1:
            new_index = -1
        else:
            target_x = x_min + ratio * (x_max - x_min)
            xs = [float(p[0]) for p in self.data]
            new_index = min(range(len(xs)), key=lambda i: abs(xs[i] - target_x))
        if new_index != self.hover_index:
            self.hover_index = new_index
            self.hoverIndexChanged.emit(new_index)
            self.update()

    def leaveEvent(self, event):
        if self.chart_type == 'line' and self.hover_index != -1:
            self.hover_index = -1
            self.hoverIndexChanged.emit(-1)
            self.update()
        super().leaveEvent(event)

    def _draw_pie_chart(self, painter):
        """파이 차트 그리기"""
        width = self.width()
        height = self.height()

        # 제목
        if self.title:
            painter.setPen(QPen(Qt.black))
            title_font = QFont()
            title_font.setBold(True)
            title_font.setPointSize(10)
            painter.setFont(title_font)
            painter.drawText(10, 20, self.title)

        # 차트 영역 계산
        chart_size = min(width - 150, height - 60)
        chart_x = 10
        chart_y = 35
        chart_rect = QRectF(chart_x, chart_y, chart_size, chart_size)

        # 총합 계산
        total = sum(value for _, value in self.data if value > 0)
        if total <= 0:
            return

        # 파이 조각 그리기
        start_angle = 0
        for i, (label, value) in enumerate(self.data):
            if value <= 0:
                continue

            span_angle = int((value / total) * 360 * 16)  # Qt는 1/16도 단위 사용
            color = self.COLORS[i % len(self.COLORS)]

            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.white, 1))
            painter.drawPie(chart_rect, start_angle, span_angle)

            start_angle += span_angle

        # 범례 그리기
        legend_x = chart_x + chart_size + 15
        legend_y = chart_y + 10
        legend_font = QFont()
        legend_font.setPointSize(8)
        painter.setFont(legend_font)

        for i, (label, value) in enumerate(self.data):
            if value <= 0:
                continue

            color = self.COLORS[i % len(self.COLORS)]
            percent = (value / total) * 100

            # 색상 박스
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.black))
            painter.drawRect(legend_x, legend_y + i * 18, 12, 12)

            # 텍스트
            painter.setPen(QPen(Qt.black))
            text = f"{label}: {percent:.1f}%"
            painter.drawText(legend_x + 18, legend_y + i * 18 + 10, text)

    def _draw_bar_chart(self, painter):
        """바 차트 그리기"""
        width = self.width()
        height = self.height()

        # 제목
        if self.title:
            painter.setPen(QPen(Qt.black))
            title_font = QFont()
            title_font.setBold(True)
            title_font.setPointSize(10)
            painter.setFont(title_font)
            painter.drawText(10, 20, self.title)

        if not self.data:
            return

        # 차트 영역
        margin_left = 80
        margin_right = 20
        margin_top = 40
        margin_bottom = 60

        chart_width = width - margin_left - margin_right
        chart_height = height - margin_top - margin_bottom

        if chart_width <= 0 or chart_height <= 0:
            return

        # 최대값 계산
        max_value = max(value for _, value in self.data) if self.data else 0
        if max_value <= 0:
            max_value = 1

        # 바 그리기
        bar_count = len(self.data)
        bar_width = max(10, (chart_width - (bar_count + 1) * 5) / bar_count)
        bar_spacing = 5

        label_font = QFont()
        label_font.setPointSize(7)
        painter.setFont(label_font)

        for i, (label, value) in enumerate(self.data):
            bar_height = (value / max_value) * chart_height if max_value > 0 else 0
            bar_x = margin_left + i * (bar_width + bar_spacing) + bar_spacing
            bar_y = margin_top + chart_height - bar_height

            color = self.COLORS[i % len(self.COLORS)]
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(Qt.black, 1))
            painter.drawRect(int(bar_x), int(bar_y), int(bar_width), int(bar_height))

            # 값 표시
            painter.setPen(QPen(Qt.black))
            value_text = f"{value:,.0f}" if value >= 1 else f"{value:.2f}"
            painter.drawText(int(bar_x), int(bar_y - 5), value_text)

            # 라벨 (회전)
            painter.save()
            painter.translate(bar_x + bar_width / 2, margin_top + chart_height + 5)
            painter.rotate(45)
            painter.drawText(0, 10, label[:8])  # 라벨 길이 제한
            painter.restore()

        # Y축 그리기
        painter.setPen(QPen(Qt.black, 1))
        painter.drawLine(margin_left, margin_top, margin_left, margin_top + chart_height)
        painter.drawLine(margin_left, margin_top + chart_height, width - margin_right, margin_top + chart_height)

        # Y축 눈금
        for i in range(5):
            y = margin_top + chart_height - (chart_height * i / 4)
            value = max_value * i / 4
            painter.drawLine(margin_left - 5, int(y), margin_left, int(y))
            painter.drawText(5, int(y + 4), f"{value:,.0f}")


class ChartZoomDialog(QDialog):
    """차트 확대보기 다이얼로그 (단면분석 '차트 확대보기' 기능)

    비모달(show)로 띄우며, 내부 라인 차트의 호버 시그널을 그대로 중계한다.
    """

    hoverIndexChanged = pyqtSignal(int)

    def __init__(self, data, title="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"차트 확대보기 - {title}" if title else "차트 확대보기")
        self.setMinimumSize(900, 450)
        layout = QVBoxLayout(self)
        self.chart = ChartWidget(chart_type='line')
        self.chart.set_data(data, title)
        self.chart.hoverIndexChanged.connect(self.hoverIndexChanged.emit)
        layout.addWidget(self.chart)
