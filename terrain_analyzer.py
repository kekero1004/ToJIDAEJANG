# -*- coding: utf-8 -*-
"""
지형 분석 모듈
- 경사도 분석 (VWorld DEM API)
- 고도 분석 (최저/최고/평균 표고)
- 노후도 분석 (건축물 연한 계산)
"""

import json
import math
import ssl
import urllib.request
import urllib.parse
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QGridLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QPushButton, QProgressBar
)
from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsPointXY, Qgis, QgsMessageLog
)


class TerrainAnalyzer:
    """지형 분석 클래스"""

    DEM_API_URL = "https://api.opentopodata.org/v1/srtm30m"

    def __init__(self, api_key=""):
        self.api_key = api_key
        self.debug_log = []
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def set_api_key(self, api_key):
        self.api_key = api_key

    def analyze_terrain(self, geometry_wgs84, sample_count=25):
        """대상지 영역의 지형 분석 (경사도, 고도)"""
        result = {
            'elevations': [],
            'min_elevation': 0,
            'max_elevation': 0,
            'avg_elevation': 0,
            'avg_slope': 0,
            'center_point': None,
            'boundary_points': [],
            # 경사도 등급 분포 (PSS 인허가 사전진단 기준: 20°미만 / 20~49° / 50°이상)
            'slope_distribution': {'20° 미만': 0, '20° ~ 49°': 0, '50° 이상': 0},
            'slope_grade_area': {'20° 미만': 0.0, '20° ~ 49°': 0.0, '50° 이상': 0.0},
            'max_slope': 0,
        }

        if not geometry_wgs84 or geometry_wgs84.isEmpty():
            return result

        try:
            bbox = geometry_wgs84.boundingBox()
            center = bbox.center()
            result['center_point'] = (center.x(), center.y())

            # 구조화 격자(grid) 생성: 노드별 고도를 조회하여 셀 경사도까지 산출
            grid_size = max(int(math.sqrt(sample_count)), 2)
            x_step = (bbox.xMaximum() - bbox.xMinimum()) / grid_size
            y_step = (bbox.yMaximum() - bbox.yMinimum()) / grid_size

            # 미터 단위 격자 간격 (위도 보정 포함)
            lat_rad = math.radians(center.y())
            dx_m = x_step * 111000 * math.cos(lat_rad)
            dy_m = y_step * 111000
            cell_area = abs(dx_m * dy_m)

            # (i, j) 격자 노드 좌표 — 전체 격자를 순서대로 조회
            nodes = []  # [(i, j, x, y), ...]
            for i in range(grid_size + 1):
                for j in range(grid_size + 1):
                    x = bbox.xMinimum() + i * x_step
                    y = bbox.yMinimum() + j * y_step
                    nodes.append((i, j, x, y))

            locations = [f"{y},{x}" for (_, _, x, y) in nodes]
            node_elevs = self._get_elevations_batch(locations)

            # 노드 고도 매핑 (조회 성공한 경우만)
            elev_grid = {}
            if node_elevs and len(node_elevs) == len(nodes):
                for (i, j, x, y), elev in zip(nodes, node_elevs):
                    elev_grid[(i, j)] = elev

            # 폴리곤 내부 노드의 고도로 표고 통계 산출
            interior_elevs = []
            for (i, j, x, y) in nodes:
                if (i, j) not in elev_grid:
                    continue
                point_geom = QgsGeometry.fromPointXY(QgsPointXY(x, y))
                if geometry_wgs84.contains(point_geom):
                    interior_elevs.append(elev_grid[(i, j)])

            if not interior_elevs and elev_grid:
                interior_elevs = list(elev_grid.values())

            if interior_elevs:
                result['elevations'] = interior_elevs
                result['min_elevation'] = min(interior_elevs)
                result['max_elevation'] = max(interior_elevs)
                result['avg_elevation'] = sum(interior_elevs) / len(interior_elevs)

            # 셀별 경사도 산출 및 등급 분류 (PSS: 20°미만/20~49°/50°이상)
            cell_slopes = []
            for i in range(grid_size):
                for j in range(grid_size):
                    corners = [(i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)]
                    if any(c not in elev_grid for c in corners):
                        continue
                    z00 = elev_grid[(i, j)]
                    z10 = elev_grid[(i + 1, j)]
                    z01 = elev_grid[(i, j + 1)]
                    z11 = elev_grid[(i + 1, j + 1)]

                    # 셀 중심이 폴리곤 내부인 경우만 집계
                    cx = bbox.xMinimum() + (i + 0.5) * x_step
                    cy = bbox.yMinimum() + (j + 0.5) * y_step
                    if not geometry_wgs84.contains(QgsGeometry.fromPointXY(QgsPointXY(cx, cy))):
                        continue

                    if dx_m == 0 or dy_m == 0:
                        continue
                    dz_dx = ((z10 + z11) - (z00 + z01)) / (2 * dx_m)
                    dz_dy = ((z01 + z11) - (z00 + z10)) / (2 * dy_m)
                    slope_deg = math.degrees(math.atan(math.sqrt(dz_dx ** 2 + dz_dy ** 2)))
                    cell_slopes.append(slope_deg)

                    if slope_deg < 20:
                        grade = '20° 미만'
                    elif slope_deg < 50:
                        grade = '20° ~ 49°'
                    else:
                        grade = '50° 이상'
                    result['slope_distribution'][grade] += 1
                    result['slope_grade_area'][grade] += cell_area

            if cell_slopes:
                result['avg_slope'] = sum(cell_slopes) / len(cell_slopes)
                result['max_slope'] = max(cell_slopes)
            elif interior_elevs and len(interior_elevs) >= 2:
                # 셀 산출 불가 시 기존 간이 경사(표고차/대각거리) 폴백
                elev_range = max(interior_elevs) - min(interior_elevs)
                diag_dist = math.sqrt(
                    ((bbox.xMaximum() - bbox.xMinimum()) * 111000) ** 2 +
                    ((bbox.yMaximum() - bbox.yMinimum()) * 111000) ** 2
                )
                if diag_dist > 0:
                    result['avg_slope'] = math.degrees(math.atan(elev_range / diag_dist))

            # 경계 좌표 추출
            if geometry_wgs84.type() == 2:  # Polygon
                vertices = geometry_wgs84.asMultiPolygon() if geometry_wgs84.isMultipart() else [geometry_wgs84.asPolygon()]
                for polygon in vertices:
                    if polygon:
                        for point in polygon[0]:
                            result['boundary_points'].append((point.x(), point.y()))

        except Exception as e:
            self.debug_log.append(f"Terrain analysis error: {e}")
            QgsMessageLog.logMessage(f"Terrain analysis error: {e}", "VWorld", Qgis.Warning)

        return result

    def _get_elevations_batch(self, locations):
        """배치 고도 조회"""
        elevations = []
        chunk_size = 50 # Safe limit
        
        for i in range(0, len(locations), chunk_size):
            chunk = locations[i:i + chunk_size]
            locations_str = "|".join(chunk)
            
            try:
                url = f"{self.DEM_API_URL}?locations={locations_str}"
                
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'QGIS_Plugin/1.0')
                
                with urllib.request.urlopen(req, timeout=20, context=self.ssl_context) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    if 'results' in data:
                        for res in data['results']:
                            elev = res.get('elevation')
                            if elev is not None:
                                elevations.append(float(elev))
            except Exception as e:
                self.debug_log.append(f"Batch elevation error: {e}")
                
        return elevations

    def _get_elevation(self, lon, lat):
        """
        OpenTopoData API를 통한 고도 조회
        참고: 이 메서드는 단일 포인트 조회용입니다. 
        실제 analyze_terrain에서는 배치 처리를 권장하지만, 일단 구조 유지를 위해 구현합니다.
        """
        try:
            # OpenTopoData locations format: lat,lon
            params = {
                'locations': f'{lat},{lon}'
            }
            
            url = f"{self.DEM_API_URL}?{urllib.parse.urlencode(params)}"
            
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'QGIS_Plugin/1.0')
            
            with urllib.request.urlopen(req, timeout=10, context=self.ssl_context) as response:
                data = json.loads(response.read().decode('utf-8'))
                if 'results' in data and len(data['results']) > 0:
                    elevation = data['results'][0].get('elevation')
                    if elevation is not None:
                        return float(elevation)
            return None
            
        except Exception as e:
            self.debug_log.append(f"Elevation query error at ({lon},{lat}): {e}")
            return None

    def analyze_building_age(self, building_data):
        """건축물 노후도 분석"""
        result = {
            'buildings': [],
            'avg_age': 0,
            'max_age': 0,
            'min_age': 0,
            'age_distribution': {}
        }

        if not building_data:
            return result

        current_year = datetime.now().year
        ages = []

        for item in building_data:
            props = item.get('properties', {})
            use_apr_day = props.get('use_apr_day', '')

            building_info = {
                'pnu': props.get('pnu', ''),
                'bld_nm': props.get('bld_nm', ''),
                'use_apr_day': use_apr_day,
                'age': 0,
                'main_purps': props.get('main_purps_cd_nm', '')
            }

            if use_apr_day and len(str(use_apr_day)) >= 4:
                try:
                    apr_year = int(str(use_apr_day)[:4])
                    age = current_year - apr_year
                    building_info['age'] = age
                    ages.append(age)
                except ValueError:
                    pass

            result['buildings'].append(building_info)

        if ages:
            result['avg_age'] = sum(ages) / len(ages)
            result['max_age'] = max(ages)
            result['min_age'] = min(ages)

            # 연한별 분포
            ranges = [
                (0, 10, '10년 미만'),
                (10, 20, '10~20년'),
                (20, 30, '20~30년'),
                (30, 50, '30~50년'),
                (50, 100, '50년 이상')
            ]
            for min_age, max_age, label in ranges:
                count = sum(1 for a in ages if min_age <= a < max_age)
                result['age_distribution'][label] = count

        return result


class TerrainAnalysisTab(QWidget):
    """지형분석 탭 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.terrain_data = None
        self.building_age_data = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 지형 분석 결과
        terrain_group = QGroupBox("지형 분석 결과")
        terrain_layout = QGridLayout()

        self.center_label = QLabel("중심점: -")
        self.min_elev_label = QLabel("최저 표고: -")
        self.max_elev_label = QLabel("최고 표고: -")
        self.avg_elev_label = QLabel("평균 표고: -")
        self.avg_slope_label = QLabel("평균 경사도: -")
        self.elev_range_label = QLabel("표고차: -")

        terrain_layout.addWidget(self.center_label, 0, 0)
        terrain_layout.addWidget(self.min_elev_label, 0, 1)
        terrain_layout.addWidget(self.max_elev_label, 0, 2)
        terrain_layout.addWidget(self.avg_elev_label, 1, 0)
        terrain_layout.addWidget(self.avg_slope_label, 1, 1)
        terrain_layout.addWidget(self.elev_range_label, 1, 2)

        terrain_group.setLayout(terrain_layout)
        layout.addWidget(terrain_group)

        # 경사도 등급 분석 (PSS 인허가 사전진단 기준)
        slope_group = QGroupBox("경사도 등급 분석 (개발 적합성)")
        slope_layout = QVBoxLayout()
        self.max_slope_label = QLabel("최대 경사도: -")
        slope_layout.addWidget(self.max_slope_label)
        self.slope_table = QTableWidget()
        self.slope_table.setColumnCount(4)
        self.slope_table.setHorizontalHeaderLabels(
            ["경사 등급", "셀 수", "면적(m2)", "비율(%)"])
        self.slope_table.horizontalHeader().setStretchLastSection(True)
        self.slope_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.slope_table.setMaximumHeight(140)
        slope_layout.addWidget(self.slope_table)
        slope_note = QLabel(
            "※ 20° 미만: 개발 양호 / 20°~49°: 제한적 / 50° 이상: 개발 곤란 (참고용 추정)")
        slope_note.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        slope_layout.addWidget(slope_note)
        slope_group.setLayout(slope_layout)
        layout.addWidget(slope_group)

        # 건축물 노후도 분석
        age_group = QGroupBox("건축물 노후도 분석")
        age_layout = QVBoxLayout()

        age_stats = QGridLayout()
        self.avg_age_label = QLabel("평균 건축 연한: -")
        self.max_age_label = QLabel("최고 건축 연한: -")
        self.min_age_label = QLabel("최저 건축 연한: -")
        self.building_count_label = QLabel("건축물 수: -")

        age_stats.addWidget(self.avg_age_label, 0, 0)
        age_stats.addWidget(self.max_age_label, 0, 1)
        age_stats.addWidget(self.min_age_label, 1, 0)
        age_stats.addWidget(self.building_count_label, 1, 1)
        age_layout.addLayout(age_stats)

        # 노후도 분포 테이블
        self.age_table = QTableWidget()
        self.age_table.setColumnCount(2)
        self.age_table.setHorizontalHeaderLabels(["연한 구간", "건축물 수"])
        self.age_table.horizontalHeader().setStretchLastSection(True)
        self.age_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.age_table.setMaximumHeight(180)
        age_layout.addWidget(self.age_table)

        # 건축물 목록 테이블
        self.building_table = QTableWidget()
        self.building_table.setColumnCount(5)
        self.building_table.setHorizontalHeaderLabels(["PNU", "건물명", "주용도", "사용승인일", "건축연한(년)"])
        self.building_table.horizontalHeader().setStretchLastSection(True)
        self.building_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        age_layout.addWidget(self.building_table)

        age_group.setLayout(age_layout)
        layout.addWidget(age_group)

        # 경계 좌표 정보
        boundary_group = QGroupBox("대상지 경계 좌표")
        boundary_layout = QVBoxLayout()
        self.boundary_text = QTextEdit()
        self.boundary_text.setReadOnly(True)
        self.boundary_text.setMaximumHeight(120)
        boundary_layout.addWidget(self.boundary_text)
        boundary_group.setLayout(boundary_layout)
        layout.addWidget(boundary_group)

    def update_terrain_data(self, terrain_result):
        """지형 분석 결과 업데이트"""
        self.terrain_data = terrain_result

        if terrain_result.get('center_point'):
            cp = terrain_result['center_point']
            self.center_label.setText(f"중심점: {cp[1]:.6f}, {cp[0]:.6f}")

        min_e = terrain_result.get('min_elevation', 0)
        max_e = terrain_result.get('max_elevation', 0)
        avg_e = terrain_result.get('avg_elevation', 0)
        slope = terrain_result.get('avg_slope', 0)

        self.min_elev_label.setText(f"최저 표고: {min_e:.1f} m")
        self.max_elev_label.setText(f"최고 표고: {max_e:.1f} m")
        self.avg_elev_label.setText(f"평균 표고: {avg_e:.1f} m")
        self.avg_slope_label.setText(f"평균 경사도: {slope:.1f} 도")
        self.elev_range_label.setText(f"표고차: {max_e - min_e:.1f} m")

        # 경사도 등급 분포 표시
        max_slope = terrain_result.get('max_slope', 0)
        self.max_slope_label.setText(f"최대 경사도: {max_slope:.1f} 도")
        slope_dist = terrain_result.get('slope_distribution', {})
        slope_area = terrain_result.get('slope_grade_area', {})
        total_cells = sum(slope_dist.values())
        grades = ['20° 미만', '20° ~ 49°', '50° 이상']
        self.slope_table.setRowCount(len(grades))
        for i, grade in enumerate(grades):
            cnt = slope_dist.get(grade, 0)
            area = slope_area.get(grade, 0.0)
            ratio = (cnt / total_cells * 100) if total_cells > 0 else 0
            self.slope_table.setItem(i, 0, QTableWidgetItem(grade))
            self.slope_table.setItem(i, 1, QTableWidgetItem(f"{cnt:,}"))
            self.slope_table.setItem(i, 2, QTableWidgetItem(f"{area:,.2f}"))
            self.slope_table.setItem(i, 3, QTableWidgetItem(f"{ratio:.1f}%"))

        # 경계 좌표 표시
        boundary_points = terrain_result.get('boundary_points', [])
        if boundary_points:
            coords_text = "경계 좌표 (위도, 경도):\n"
            for i, (x, y) in enumerate(boundary_points[:20]):
                coords_text += f"  {i+1}. ({y:.6f}, {x:.6f})\n"
            if len(boundary_points) > 20:
                coords_text += f"  ... 외 {len(boundary_points) - 20}개\n"
            self.boundary_text.setText(coords_text)

    def update_building_age_data(self, age_result):
        """건축물 노후도 데이터 업데이트"""
        self.building_age_data = age_result

        buildings = age_result.get('buildings', [])
        self.building_count_label.setText(f"건축물 수: {len(buildings)}")

        if age_result.get('avg_age', 0) > 0:
            self.avg_age_label.setText(f"평균 건축 연한: {age_result['avg_age']:.1f}년")
            self.max_age_label.setText(f"최고 건축 연한: {age_result['max_age']}년")
            self.min_age_label.setText(f"최저 건축 연한: {age_result['min_age']}년")

        # 노후도 분포 테이블
        age_dist = age_result.get('age_distribution', {})
        self.age_table.setRowCount(len(age_dist))
        for i, (label, count) in enumerate(age_dist.items()):
            self.age_table.setItem(i, 0, QTableWidgetItem(label))
            self.age_table.setItem(i, 1, QTableWidgetItem(str(count)))

        # 건축물 목록 테이블
        self.building_table.setRowCount(len(buildings))
        for i, bld in enumerate(buildings):
            self.building_table.setItem(i, 0, QTableWidgetItem(str(bld.get('pnu', ''))))
            self.building_table.setItem(i, 1, QTableWidgetItem(str(bld.get('bld_nm', ''))))
            self.building_table.setItem(i, 2, QTableWidgetItem(str(bld.get('main_purps', ''))))
            self.building_table.setItem(i, 3, QTableWidgetItem(str(bld.get('use_apr_day', ''))))
            self.building_table.setItem(i, 4, QTableWidgetItem(str(bld.get('age', ''))))

    def get_report_data(self):
        """보고서 내보내기용 섹션 (export_manager 공통 형식)"""
        terrain = self.terrain_data or {}
        age = self.building_age_data or {}
        if not terrain and not age.get('buildings'):
            return None
        section = {'title': '지형분석', 'kv': [], 'tables': []}

        if terrain:
            min_e = terrain.get('min_elevation', 0)
            max_e = terrain.get('max_elevation', 0)
            section['kv'].extend([
                ('최저 표고', f"{min_e:,.1f} m"),
                ('최고 표고', f"{max_e:,.1f} m"),
                ('평균 표고', f"{terrain.get('avg_elevation', 0):,.1f} m"),
                ('표고차', f"{max_e - min_e:,.1f} m"),
                ('평균 경사도', f"{terrain.get('avg_slope', 0):,.1f} 도"),
                ('최대 경사도', f"{terrain.get('max_slope', 0):,.1f} 도"),
            ])
            slope_dist = terrain.get('slope_distribution', {})
            slope_area = terrain.get('slope_grade_area', {})
            total_cells = sum(slope_dist.values())
            if total_cells > 0:
                rows = []
                for grade in ['20° 미만', '20° ~ 49°', '50° 이상']:
                    cnt = slope_dist.get(grade, 0)
                    area = slope_area.get(grade, 0.0)
                    ratio = cnt / total_cells * 100 if total_cells else 0
                    rows.append([grade, f"{cnt:,}", f"{area:,.2f}",
                                 f"{ratio:.1f}%"])
                section['tables'].append({
                    'title': '경사도 등급 분석 (개발 적합성)',
                    'headers': ['경사 등급', '셀 수', '면적(m2)', '비율(%)'],
                    'rows': rows,
                })

        buildings = age.get('buildings', [])
        if buildings:
            section['kv'].extend([
                ('건축물 수', f"{len(buildings)}"),
                ('평균 건축 연한', f"{age.get('avg_age', 0):.1f}년"),
                ('최고 건축 연한', f"{age.get('max_age', 0)}년"),
            ])
            dist = age.get('age_distribution', {})
            if dist:
                section['tables'].append({
                    'title': '건축물 노후도 분포',
                    'headers': ['연한 구간', '건축물 수'],
                    'rows': [[label, str(count)]
                             for label, count in dist.items()],
                })
            section['tables'].append({
                'title': '건축물 목록',
                'headers': ['PNU', '건물명', '주용도', '사용승인일', '건축연한(년)'],
                'rows': [[b.get('pnu', ''), b.get('bld_nm', ''),
                          b.get('main_purps', ''), b.get('use_apr_day', ''),
                          b.get('age', '')] for b in buildings[:100]],
            })
        return section
