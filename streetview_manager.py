# -*- coding: utf-8 -*-
"""
가로경관 사진 관리 모듈
- VWorld Street View API 연동
- 도로 포인트 검색 및 heading 자동 계산
- 이미지 다운로드 및 관리
"""

import os
import json
import math
import ssl
import urllib.request
import urllib.parse

from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout,
    QLabel, QPushButton, QScrollArea, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from qgis.core import (
    QgsProject, QgsGeometry, QgsPointXY,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog
)


class StreetViewManager:
    """가로경관 사진 관리 클래스"""

    STREETVIEW_API_URL = "https://api.vworld.kr/req/image"

    def __init__(self, api_key=""):
        self.api_key = api_key
        self.debug_log = []
        self.images = []
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def set_api_key(self, api_key):
        self.api_key = api_key

    def capture_streetview(self, geometry_wgs84, save_dir, num_points=4):
        """대상지 주변 가로경관 사진 수집"""
        self.images = []

        if not self.api_key:
            self.debug_log.append("API 키가 설정되지 않았습니다.")
            return []

        if not geometry_wgs84 or geometry_wgs84.isEmpty():
            self.debug_log.append("지오메트리가 비어있습니다.")
            return []

        try:
            bbox = geometry_wgs84.boundingBox()
            center = bbox.center()
            center_x, center_y = center.x(), center.y()

            # 경계에서 도로 방향 포인트 생성
            road_points = self._get_boundary_road_points(geometry_wgs84, num_points)

            if not road_points:
                # 4방향 기본 포인트 생성
                offset = 0.001  # 약 100m
                road_points = [
                    (center_x, center_y + offset, 'N'),
                    (center_x + offset, center_y, 'E'),
                    (center_x, center_y - offset, 'S'),
                    (center_x - offset, center_y, 'W'),
                ]

            os.makedirs(save_dir, exist_ok=True)

            for i, point_info in enumerate(road_points):
                lon, lat = point_info[0], point_info[1]
                direction = point_info[2] if len(point_info) > 2 else f"P{i+1}"

                # heading 계산 (촬영 지점에서 중심 방향)
                heading = self._calculate_heading(lon, lat, center_x, center_y)

                image_path = self._download_streetview(
                    lon, lat, heading, save_dir,
                    f"streetview_{direction}_{i+1}.jpg"
                )

                if image_path:
                    self.images.append({
                        'path': image_path,
                        'lon': lon,
                        'lat': lat,
                        'heading': heading,
                        'direction': direction,
                        'index': i + 1
                    })

        except Exception as e:
            self.debug_log.append(f"Street view capture error: {e}")
            QgsMessageLog.logMessage(f"Street view error: {e}", "VWorld", Qgis.Warning)

        return self.images

    def _get_boundary_road_points(self, geometry_wgs84, num_points):
        """경계에서 도로 방향 포인트 추출"""
        points = []
        try:
            bbox = geometry_wgs84.boundingBox()
            center = bbox.center()

            # 경계의 4방향 (N, E, S, W) 포인트 선택
            directions = [
                (center.x(), bbox.yMaximum(), 'N'),
                (bbox.xMaximum(), center.y(), 'E'),
                (center.x(), bbox.yMinimum(), 'S'),
                (bbox.xMinimum(), center.y(), 'W'),
            ]

            # 경계 바깥으로 약간 이동 (도로 위치 추정)
            offset = 0.0005  # 약 50m
            for x, y, d in directions[:num_points]:
                if d == 'N':
                    points.append((x, y + offset, d))
                elif d == 'E':
                    points.append((x + offset, y, d))
                elif d == 'S':
                    points.append((x, y - offset, d))
                elif d == 'W':
                    points.append((x - offset, y, d))

        except Exception as e:
            self.debug_log.append(f"Boundary point extraction error: {e}")

        return points

    def _calculate_heading(self, from_lon, from_lat, to_lon, to_lat):
        """두 지점 간 방향각(heading) 계산"""
        d_lon = math.radians(to_lon - from_lon)
        lat1 = math.radians(from_lat)
        lat2 = math.radians(to_lat)

        x = math.sin(d_lon) * math.cos(lat2)
        y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)

        heading = math.degrees(math.atan2(x, y))
        return (heading + 360) % 360

    def _download_streetview(self, lon, lat, heading, save_dir, filename):
        """가로경관 이미지 다운로드"""
        try:
            params = {
                'service': 'image',
                'request': 'getmap',
                'version': '2.0',
                'key': self.api_key,
                'center': f'{lon},{lat}',
                'zoom': '18',
                'size': '640,480',
                'format': 'jpeg',
                'domain': 'localhost'
            }

            url = f"{self.STREETVIEW_API_URL}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(f"Streetview URL: {url}")

            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 QGIS VWorld Plugin')
            req.add_header('Referer', 'http://localhost')

            filepath = os.path.join(save_dir, filename)

            with urllib.request.urlopen(req, timeout=15, context=self.ssl_context) as response:
                content_type = response.headers.get('Content-Type', '')
                if 'image' in content_type or 'octet-stream' in content_type:
                    with open(filepath, 'wb') as f:
                        f.write(response.read())
                    self.debug_log.append(f"Image saved: {filepath}")
                    return filepath
                else:
                    data = response.read().decode('utf-8')
                    self.debug_log.append(f"Non-image response: {data[:200]}")
                    return None

        except Exception as e:
            self.debug_log.append(f"Streetview download error: {e}")
            return None


class StreetViewTab(QWidget):
    """가로경관 탭 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.images = []
        self.save_dir = ""
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 상태 정보
        info_group = QGroupBox("가로경관 사진 정보")
        info_layout = QHBoxLayout()
        self.status_label = QLabel("수집된 사진: 0장")
        info_layout.addWidget(self.status_label)
        info_layout.addStretch()

        self.save_folder_label = QLabel("저장 폴더: -")
        info_layout.addWidget(self.save_folder_label)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # 사진 갤러리 (스크롤 영역)
        gallery_group = QGroupBox("사진 미리보기")
        gallery_layout = QVBoxLayout()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.gallery_widget = QWidget()
        self.gallery_grid = QGridLayout(self.gallery_widget)
        scroll.setWidget(self.gallery_widget)

        gallery_layout.addWidget(scroll)
        gallery_group.setLayout(gallery_layout)
        layout.addWidget(gallery_group)

        # 사진 상세 정보
        detail_group = QGroupBox("촬영 정보")
        detail_layout = QVBoxLayout()
        self.detail_table = QTableWidget()
        self.detail_table.setColumnCount(5)
        self.detail_table.setHorizontalHeaderLabels(["번호", "방향", "위도", "경도", "방향각"])
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        self.detail_table.setMaximumHeight(150)
        detail_layout.addWidget(self.detail_table)
        detail_group.setLayout(detail_layout)
        layout.addWidget(detail_group)

    def update_images(self, images, save_dir=""):
        """가로경관 사진 목록 업데이트"""
        self.images = images
        self.save_dir = save_dir
        self.status_label.setText(f"수집된 사진: {len(images)}장")
        if save_dir:
            self.save_folder_label.setText(f"저장 폴더: {save_dir}")

        # 갤러리 클리어
        while self.gallery_grid.count():
            child = self.gallery_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # 사진 썸네일 표시
        cols = 2
        for i, img_info in enumerate(images):
            row = i // cols
            col = i % cols

            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(5, 5, 5, 5)

            # 썸네일
            thumb_label = QLabel()
            if os.path.exists(img_info.get('path', '')):
                pixmap = QPixmap(img_info['path'])
                if not pixmap.isNull():
                    scaled = pixmap.scaled(QSize(300, 225), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    thumb_label.setPixmap(scaled)
                else:
                    thumb_label.setText("이미지 로드 실패")
            else:
                thumb_label.setText("파일 없음")

            thumb_label.setAlignment(Qt.AlignCenter)
            container_layout.addWidget(thumb_label)

            # 캡션
            direction = img_info.get('direction', '')
            heading = img_info.get('heading', 0)
            caption = QLabel(f"방향: {direction} / heading: {heading:.1f}")
            caption.setAlignment(Qt.AlignCenter)
            container_layout.addWidget(caption)

            self.gallery_grid.addWidget(container, row, col)

        # 상세 테이블 업데이트
        self.detail_table.setRowCount(len(images))
        for i, img_info in enumerate(images):
            self.detail_table.setItem(i, 0, QTableWidgetItem(str(img_info.get('index', i+1))))
            self.detail_table.setItem(i, 1, QTableWidgetItem(str(img_info.get('direction', ''))))
            self.detail_table.setItem(i, 2, QTableWidgetItem(f"{img_info.get('lat', 0):.6f}"))
            self.detail_table.setItem(i, 3, QTableWidgetItem(f"{img_info.get('lon', 0):.6f}"))
            self.detail_table.setItem(i, 4, QTableWidgetItem(f"{img_info.get('heading', 0):.1f}"))

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        if not self.images:
            return None
        section = {
            'title': '가로경관',
            'kv': [('수집된 사진', f"{len(self.images)}장")],
            'tables': [{
                'title': '촬영 정보',
                'headers': ['번호', '방향', '위도', '경도', '방향각', '파일'],
                'rows': [[img.get('index', i + 1), img.get('direction', ''),
                          f"{img.get('lat', 0):.6f}",
                          f"{img.get('lon', 0):.6f}",
                          f"{img.get('heading', 0):.1f}",
                          img.get('path', '')]
                         for i, img in enumerate(self.images)],
            }],
            'images': [img.get('path', '') for img in self.images
                       if img.get('path') and os.path.exists(img.get('path'))],
        }
        if self.save_dir:
            section['kv'].append(('저장 폴더', self.save_dir))
        return section
