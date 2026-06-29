# -*- coding: utf-8 -*-
"""
대시보드 위젯 모듈
- 지목별 편입면적 분석 포함
"""

import json

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QGroupBox, QGridLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QScrollArea
)
from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog
)

from .constants import extract_jimok_from_jibun, extract_jimok_from_pnu
from .chart_widget import ChartWidget


class DashboardWidget(QWidget):
    """대시보드 위젯 - 지목별 편입면적 분석 포함"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = {}
        self.selected_geometry = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # 스크롤 영역 생성
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        # 필터 그룹
        filter_group = QGroupBox("필터 조건")
        filter_layout = QGridLayout()

        filter_layout.addWidget(QLabel("시도:"), 0, 0)
        self.sido_combo = QComboBox()
        self.sido_combo.addItem("전체")
        self.sido_combo.currentTextChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.sido_combo, 0, 1)

        filter_layout.addWidget(QLabel("시군구:"), 0, 2)
        self.sigungu_combo = QComboBox()
        self.sigungu_combo.addItem("전체")
        self.sigungu_combo.currentTextChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.sigungu_combo, 0, 3)

        filter_layout.addWidget(QLabel("지목:"), 1, 0)
        self.jimok_combo = QComboBox()
        self.jimok_combo.addItem("전체")
        self.jimok_combo.currentTextChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.jimok_combo, 1, 1)

        filter_layout.addWidget(QLabel("지번검색:"), 1, 2)
        self.jibun_edit = QLineEdit()
        self.jibun_edit.setPlaceholderText("지번 입력...")
        self.jibun_edit.textChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.jibun_edit, 1, 3)

        filter_group.setLayout(filter_layout)
        scroll_layout.addWidget(filter_group)

        # 통계 요약 그룹
        stats_group = QGroupBox("통계 요약")
        stats_layout = QGridLayout()

        self.total_count_label = QLabel("총 필지 수: 0")
        self.total_area_label = QLabel("총 토지면적: 0 m2")
        self.total_inclusion_area_label = QLabel("총 편입면적: 0 m2")
        self.avg_price_label = QLabel("평균 공시지가: 0 원/m2")
        self.max_price_label = QLabel("최고 공시지가: 0 원/m2")
        self.min_price_label = QLabel("최저 공시지가: 0 원/m2")
        self.total_price_label = QLabel("총 공시지가 합계: 0 원")
        self.land_count_label = QLabel("토지 수: 0")
        self.forest_count_label = QLabel("임야 수: 0")
        self.owner_count_label = QLabel("소유자 수: 0")

        stats_layout.addWidget(self.total_count_label, 0, 0)
        stats_layout.addWidget(self.total_area_label, 0, 1)
        stats_layout.addWidget(self.total_inclusion_area_label, 0, 2)
        stats_layout.addWidget(self.avg_price_label, 1, 0)
        stats_layout.addWidget(self.max_price_label, 1, 1)
        stats_layout.addWidget(self.min_price_label, 1, 2)
        stats_layout.addWidget(self.total_price_label, 2, 0)
        stats_layout.addWidget(self.land_count_label, 2, 1)
        stats_layout.addWidget(self.forest_count_label, 2, 2)
        stats_layout.addWidget(self.owner_count_label, 3, 0)

        stats_group.setLayout(stats_layout)
        scroll_layout.addWidget(stats_group)

        # 시도별 분석 테이블
        sido_group = QGroupBox("시도별 편입면적 분석")
        sido_layout = QVBoxLayout()

        self.sido_table = QTableWidget()
        self.sido_table.setColumnCount(5)
        self.sido_table.setHorizontalHeaderLabels(["시도", "필지 수", "토지면적(m2)", "편입면적(m2)", "공시지가합계(원)"])
        self.sido_table.horizontalHeader().setStretchLastSection(True)
        self.sido_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sido_table.setMaximumHeight(150)

        sido_layout.addWidget(self.sido_table)
        sido_group.setLayout(sido_layout)
        scroll_layout.addWidget(sido_group)

        # 시군구별 분석 테이블
        sigungu_group = QGroupBox("시군구별 편입면적 분석")
        sigungu_layout = QVBoxLayout()

        self.sigungu_table = QTableWidget()
        self.sigungu_table.setColumnCount(5)
        self.sigungu_table.setHorizontalHeaderLabels(["시군구", "필지 수", "토지면적(m2)", "편입면적(m2)", "공시지가합계(원)"])
        self.sigungu_table.horizontalHeader().setStretchLastSection(True)
        self.sigungu_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sigungu_table.setMaximumHeight(150)

        sigungu_layout.addWidget(self.sigungu_table)
        sigungu_group.setLayout(sigungu_layout)
        scroll_layout.addWidget(sigungu_group)

        # 지목별 분석 테이블 (편입면적 포함)
        jimok_group = QGroupBox("지목별 편입면적 분석")
        jimok_layout = QVBoxLayout()

        self.jimok_table = QTableWidget()
        self.jimok_table.setColumnCount(6)
        self.jimok_table.setHorizontalHeaderLabels(["지목", "필지 수", "토지면적(m2)", "편입면적(m2)", "공시지가합계(원)", "비율(%)"])
        self.jimok_table.horizontalHeader().setStretchLastSection(True)
        self.jimok_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        jimok_layout.addWidget(self.jimok_table)
        jimok_group.setLayout(jimok_layout)
        scroll_layout.addWidget(jimok_group)

        # 소유자별 분석 테이블
        owner_group = QGroupBox("소유자별 분석")
        owner_layout = QVBoxLayout()

        self.owner_table = QTableWidget()
        self.owner_table.setColumnCount(5)
        self.owner_table.setHorizontalHeaderLabels(["소유구분", "필지 수", "토지면적(m2)", "편입면적(m2)", "공시지가합계(원)"])
        self.owner_table.horizontalHeader().setStretchLastSection(True)
        self.owner_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.owner_table.setMaximumHeight(150)

        owner_layout.addWidget(self.owner_table)
        owner_group.setLayout(owner_layout)
        scroll_layout.addWidget(owner_group)

        # 공시지가 분포 테이블
        price_group = QGroupBox("공시지가 분포")
        price_layout = QVBoxLayout()

        self.price_table = QTableWidget()
        self.price_table.setColumnCount(4)
        self.price_table.setHorizontalHeaderLabels(["가격대", "필지 수", "편입면적(m2)", "비율(%)"])
        self.price_table.horizontalHeader().setStretchLastSection(True)
        self.price_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        price_layout.addWidget(self.price_table)
        price_group.setLayout(price_layout)
        scroll_layout.addWidget(price_group)

        # 차트 그룹 추가
        chart_group = QGroupBox("통계 그래프")
        chart_layout = QHBoxLayout()

        # 지목별 파이 차트
        self.jimok_chart = ChartWidget(chart_type='pie')
        self.jimok_chart.setMinimumHeight(280)
        chart_layout.addWidget(self.jimok_chart)

        # 공시지가 바 차트
        self.price_chart = ChartWidget(chart_type='bar')
        self.price_chart.setMinimumHeight(280)
        chart_layout.addWidget(self.price_chart)

        chart_group.setLayout(chart_layout)
        scroll_layout.addWidget(chart_group)

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def set_selected_geometry(self, geometry):
        """선택된 폴리곤 지오메트리 설정"""
        self.selected_geometry = geometry

    def update_data(self, data):
        self.data = data
        self.update_filters()
        self.update_statistics()

    def update_filters(self):
        sidos = set()
        sigungus = set()
        jimoks = set()

        for item in self.data.get('cadastral', []):
            props = item.get('properties', {})
            addr = props.get('addr', '')
            if addr:
                parts = addr.split()
                if len(parts) >= 1:
                    sidos.add(parts[0])
                if len(parts) >= 2:
                    sigungus.add(parts[1])

            jibun = props.get('jibun', '')
            pnu = props.get('pnu', '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)
            if jimok != '미분류':
                jimoks.add(jimok)

        self.sido_combo.blockSignals(True)
        self.sido_combo.clear()
        self.sido_combo.addItem("전체")
        self.sido_combo.addItems(sorted(sidos))
        self.sido_combo.blockSignals(False)

        self.sigungu_combo.blockSignals(True)
        self.sigungu_combo.clear()
        self.sigungu_combo.addItem("전체")
        self.sigungu_combo.addItems(sorted(sigungus))
        self.sigungu_combo.blockSignals(False)

        self.jimok_combo.blockSignals(True)
        self.jimok_combo.clear()
        self.jimok_combo.addItem("전체")
        self.jimok_combo.addItems(sorted(jimoks))
        self.jimok_combo.blockSignals(False)

    def apply_filter(self):
        self.update_statistics()

    def calculate_area_from_geometry(self, geom_data, return_inclusion=False):
        """지오메트리에서 면적 계산"""
        try:
            if not geom_data:
                return (0, 0) if return_inclusion else 0

            geom_type = geom_data.get('type', '')
            coordinates = geom_data.get('coordinates', [])

            if not coordinates:
                return (0, 0) if return_inclusion else 0

            qgs_geom = QgsGeometry.fromWkt(self._geojson_to_wkt(geom_data))

            if qgs_geom.isEmpty():
                return (0, 0) if return_inclusion else 0

            source_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            target_crs = QgsCoordinateReferenceSystem("EPSG:5186")
            transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())

            geom_transformed = QgsGeometry(qgs_geom)
            geom_transformed.transform(transform)

            total_area = geom_transformed.area()
            inclusion_area = total_area

            if return_inclusion and self.selected_geometry:
                selected_transformed = QgsGeometry(self.selected_geometry)
                selected_transformed.transform(transform)

                intersection = geom_transformed.intersection(selected_transformed)
                if not intersection.isEmpty():
                    inclusion_area = intersection.area()
                else:
                    inclusion_area = 0

            if return_inclusion:
                return (total_area, inclusion_area)
            return total_area

        except Exception as e:
            QgsMessageLog.logMessage(f"Area calculation error: {e}", "VWorld", Qgis.Warning)
            return (0, 0) if return_inclusion else 0

    def _geojson_to_wkt(self, geom_data):
        """GeoJSON 지오메트리를 WKT로 변환"""
        try:
            geom_type = geom_data.get('type', '')
            coordinates = geom_data.get('coordinates', [])

            if geom_type == 'Polygon':
                rings = []
                for ring in coordinates:
                    points = ', '.join([f"{coord[0]} {coord[1]}" for coord in ring])
                    rings.append(f"({points})")
                return f"POLYGON({', '.join(rings)})"

            elif geom_type == 'MultiPolygon':
                polygons = []
                for polygon in coordinates:
                    rings = []
                    for ring in polygon:
                        points = ', '.join([f"{coord[0]} {coord[1]}" for coord in ring])
                        rings.append(f"({points})")
                    polygons.append(f"({', '.join(rings)})")
                return f"MULTIPOLYGON({', '.join(polygons)})"

            elif geom_type == 'Point':
                return f"POINT({coordinates[0]} {coordinates[1]})"

            elif geom_type == 'LineString':
                points = ', '.join([f"{coord[0]} {coord[1]}" for coord in coordinates])
                return f"LINESTRING({points})"

            return ""
        except Exception as e:
            return ""

    def update_statistics(self):
        filtered_data = self.get_filtered_data()

        total_count = len(filtered_data)
        total_area = 0
        total_inclusion_area = 0
        prices = []
        total_price_sum = 0
        land_count = 0
        forest_count = 0
        owners = set()

        sido_stats = {}
        sigungu_stats = {}
        jimok_stats = {}
        owner_stats = {}

        for item in filtered_data:
            props = item.get('properties', {})
            geom = item.get('geometry', {})

            area_tuple = self.calculate_area_from_geometry(geom, return_inclusion=True)
            area = area_tuple[0] if isinstance(area_tuple, tuple) else area_tuple
            inclusion_area = area_tuple[1] if isinstance(area_tuple, tuple) else area_tuple
            if area > 0:
                total_area += area
                total_inclusion_area += inclusion_area

            price = props.get('jiga', 0)
            if price:
                try:
                    price_val = float(price)
                    prices.append(price_val)
                    if area > 0:
                        total_price_sum += price_val * area
                except:
                    pass

            jibun = props.get('jibun', '')
            pnu = props.get('pnu', '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)

            addr = props.get('addr', '')
            parts = addr.split() if addr else []
            sido = parts[0] if len(parts) >= 1 else '미분류'
            sigungu = parts[1] if len(parts) >= 2 else '미분류'

            bchk = props.get('bchk', '')
            owner_type = '토지' if bchk == '1' else ('임야' if bchk == '2' else '기타')

            if bchk == '1':
                land_count += 1
            elif bchk == '2':
                forest_count += 1

            owner_id = props.get('pnu', '')[:10] if props.get('pnu') else 'unknown'
            owners.add(owner_id)

            # 시도별 통계
            if sido not in sido_stats:
                sido_stats[sido] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            sido_stats[sido]['count'] += 1
            sido_stats[sido]['area'] += area
            sido_stats[sido]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    sido_stats[sido]['price_sum'] += float(price) * area
                except:
                    pass

            # 시군구별 통계
            sigungu_key = f"{sido} {sigungu}"
            if sigungu_key not in sigungu_stats:
                sigungu_stats[sigungu_key] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            sigungu_stats[sigungu_key]['count'] += 1
            sigungu_stats[sigungu_key]['area'] += area
            sigungu_stats[sigungu_key]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    sigungu_stats[sigungu_key]['price_sum'] += float(price) * area
                except:
                    pass

            # 지목별 통계
            if jimok not in jimok_stats:
                jimok_stats[jimok] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            jimok_stats[jimok]['count'] += 1
            jimok_stats[jimok]['area'] += area
            jimok_stats[jimok]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    jimok_stats[jimok]['price_sum'] += float(price) * area
                except:
                    pass

            # 소유구분별 통계
            if owner_type not in owner_stats:
                owner_stats[owner_type] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            owner_stats[owner_type]['count'] += 1
            owner_stats[owner_type]['area'] += area
            owner_stats[owner_type]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    owner_stats[owner_type]['price_sum'] += float(price) * area
                except:
                    pass

        # 라벨 업데이트
        self.total_count_label.setText(f"총 필지 수: {total_count:,}")
        self.total_area_label.setText(f"총 토지면적: {total_area:,.2f} m2")
        self.total_inclusion_area_label.setText(f"총 편입면적: {total_inclusion_area:,.2f} m2")

        if prices:
            self.avg_price_label.setText(f"평균 공시지가: {sum(prices)/len(prices):,.0f} 원/m2")
            self.max_price_label.setText(f"최고 공시지가: {max(prices):,.0f} 원/m2")
            self.min_price_label.setText(f"최저 공시지가: {min(prices):,.0f} 원/m2")
        else:
            self.avg_price_label.setText("평균 공시지가: - 원/m2")
            self.max_price_label.setText("최고 공시지가: - 원/m2")
            self.min_price_label.setText("최저 공시지가: - 원/m2")

        self.total_price_label.setText(f"총 공시지가 합계: {total_price_sum:,.0f} 원")
        self.land_count_label.setText(f"토지 수: {land_count:,}")
        self.forest_count_label.setText(f"임야 수: {forest_count:,}")
        self.owner_count_label.setText(f"소유자 수: {len(owners):,}")

        # 시도별 테이블 업데이트
        self.sido_table.setRowCount(len(sido_stats))
        for i, (sido, stats) in enumerate(sorted(sido_stats.items())):
            self.sido_table.setItem(i, 0, QTableWidgetItem(sido))
            self.sido_table.setItem(i, 1, QTableWidgetItem(f"{stats['count']:,}"))
            self.sido_table.setItem(i, 2, QTableWidgetItem(f"{stats['area']:,.2f}"))
            self.sido_table.setItem(i, 3, QTableWidgetItem(f"{stats['inclusion_area']:,.2f}"))
            self.sido_table.setItem(i, 4, QTableWidgetItem(f"{stats['price_sum']:,.0f}"))

        # 시군구별 테이블 업데이트
        self.sigungu_table.setRowCount(len(sigungu_stats))
        for i, (sigungu, stats) in enumerate(sorted(sigungu_stats.items())):
            self.sigungu_table.setItem(i, 0, QTableWidgetItem(sigungu))
            self.sigungu_table.setItem(i, 1, QTableWidgetItem(f"{stats['count']:,}"))
            self.sigungu_table.setItem(i, 2, QTableWidgetItem(f"{stats['area']:,.2f}"))
            self.sigungu_table.setItem(i, 3, QTableWidgetItem(f"{stats['inclusion_area']:,.2f}"))
            self.sigungu_table.setItem(i, 4, QTableWidgetItem(f"{stats['price_sum']:,.0f}"))

        # 지목별 테이블 업데이트
        self.jimok_table.setRowCount(len(jimok_stats))
        for i, (jimok, stats) in enumerate(sorted(jimok_stats.items())):
            self.jimok_table.setItem(i, 0, QTableWidgetItem(jimok))
            self.jimok_table.setItem(i, 1, QTableWidgetItem(f"{stats['count']:,}"))
            self.jimok_table.setItem(i, 2, QTableWidgetItem(f"{stats['area']:,.2f}"))
            self.jimok_table.setItem(i, 3, QTableWidgetItem(f"{stats['inclusion_area']:,.2f}"))
            self.jimok_table.setItem(i, 4, QTableWidgetItem(f"{stats['price_sum']:,.0f}"))
            ratio = (stats['count'] / total_count * 100) if total_count > 0 else 0
            self.jimok_table.setItem(i, 5, QTableWidgetItem(f"{ratio:.1f}%"))

        # 소유자별 테이블 업데이트
        self.owner_table.setRowCount(len(owner_stats))
        for i, (owner_type, stats) in enumerate(sorted(owner_stats.items())):
            self.owner_table.setItem(i, 0, QTableWidgetItem(owner_type))
            self.owner_table.setItem(i, 1, QTableWidgetItem(f"{stats['count']:,}"))
            self.owner_table.setItem(i, 2, QTableWidgetItem(f"{stats['area']:,.2f}"))
            self.owner_table.setItem(i, 3, QTableWidgetItem(f"{stats['inclusion_area']:,.2f}"))
            self.owner_table.setItem(i, 4, QTableWidgetItem(f"{stats['price_sum']:,.0f}"))

        # 공시지가 분포 테이블 업데이트
        price_ranges = [
            (0, 10000, "1만원 미만"),
            (10000, 50000, "1~5만원"),
            (50000, 100000, "5~10만원"),
            (100000, 500000, "10~50만원"),
            (500000, 1000000, "50~100만원"),
            (1000000, float('inf'), "100만원 이상")
        ]

        self.price_table.setRowCount(len(price_ranges))
        for i, (min_p, max_p, label) in enumerate(price_ranges):
            count = sum(1 for p in prices if min_p <= p < max_p)
            inclusion_area = 0
            for item in filtered_data:
                props = item.get('properties', {})
                geom = item.get('geometry', {})
                price = props.get('jiga', 0)
                if price:
                    try:
                        price_val = float(price)
                        if min_p <= price_val < max_p:
                            inclusion_area += self.calculate_area_from_geometry(geom)
                    except:
                        pass

            ratio = (count / len(prices) * 100) if prices else 0
            self.price_table.setItem(i, 0, QTableWidgetItem(label))
            self.price_table.setItem(i, 1, QTableWidgetItem(f"{count:,}"))
            self.price_table.setItem(i, 2, QTableWidgetItem(f"{inclusion_area:,.2f}"))
            self.price_table.setItem(i, 3, QTableWidgetItem(f"{ratio:.1f}%"))

        # 차트 데이터 업데이트
        jimok_chart_data = [(jimok, stats['count']) for jimok, stats in sorted(jimok_stats.items()) if stats['count'] > 0]
        self.jimok_chart.set_data(jimok_chart_data, "지목별 필지 분포")

        price_chart_data = []
        for min_p, max_p, label in price_ranges:
            count = sum(1 for p in prices if min_p <= p < max_p)
            if count > 0:
                price_chart_data.append((label, count))
        self.price_chart.set_data(price_chart_data, "공시지가 분포")

    def get_filtered_data(self):
        filtered = []

        sido = self.sido_combo.currentText()
        sigungu = self.sigungu_combo.currentText()
        jimok_filter = self.jimok_combo.currentText()
        jibun_search = self.jibun_edit.text().strip()

        for item in self.data.get('cadastral', []):
            props = item.get('properties', {})
            addr = props.get('addr', '')
            jibun = props.get('jibun', '')
            pnu = props.get('pnu', '')

            item_jimok = extract_jimok_from_jibun(jibun)
            if item_jimok == '미분류':
                item_jimok = extract_jimok_from_pnu(pnu)

            if sido != "전체" and sido not in addr:
                continue
            if sigungu != "전체" and sigungu not in addr:
                continue
            if jimok_filter != "전체" and jimok_filter != item_jimok:
                continue
            if jibun_search and jibun_search not in jibun:
                continue

            filtered.append(item)

        return filtered

    def get_dashboard_stats(self):
        """대시보드 통계 데이터 반환 (내보내기용)"""
        filtered_data = self.get_filtered_data()

        stats = {
            'summary': {
                'total_count': 0, 'total_area': 0, 'total_inclusion_area': 0,
                'avg_price': 0, 'max_price': 0, 'min_price': 0,
                'total_price_sum': 0, 'land_count': 0, 'forest_count': 0, 'owner_count': 0
            },
            'sido_stats': {},
            'sigungu_stats': {},
            'jimok_stats': {},
            'owner_stats': {},
            'price_distribution': []
        }

        total_count = len(filtered_data)
        total_area = 0
        total_inclusion_area = 0
        prices = []
        total_price_sum = 0
        land_count = 0
        forest_count = 0
        owners = set()

        for item in filtered_data:
            props = item.get('properties', {})
            geom = item.get('geometry', {})

            area_tuple = self.calculate_area_from_geometry(geom, return_inclusion=True)
            area = area_tuple[0] if isinstance(area_tuple, tuple) else area_tuple
            inclusion_area = area_tuple[1] if isinstance(area_tuple, tuple) else area_tuple
            if area > 0:
                total_area += area
                total_inclusion_area += inclusion_area

            price = props.get('jiga', 0)
            if price:
                try:
                    price_val = float(price)
                    prices.append(price_val)
                    if area > 0:
                        total_price_sum += price_val * area
                except:
                    pass

            jibun = props.get('jibun', '')
            pnu = props.get('pnu', '')
            jimok = extract_jimok_from_jibun(jibun)
            if jimok == '미분류':
                jimok = extract_jimok_from_pnu(pnu)

            addr = props.get('addr', '')
            parts = addr.split() if addr else []
            sido = parts[0] if len(parts) >= 1 else '미분류'
            sigungu = parts[1] if len(parts) >= 2 else '미분류'

            bchk = props.get('bchk', '')
            owner_type = '토지' if bchk == '1' else ('임야' if bchk == '2' else '기타')

            if bchk == '1':
                land_count += 1
            elif bchk == '2':
                forest_count += 1

            owner_id = props.get('pnu', '')[:10] if props.get('pnu') else 'unknown'
            owners.add(owner_id)

            if sido not in stats['sido_stats']:
                stats['sido_stats'][sido] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            stats['sido_stats'][sido]['count'] += 1
            stats['sido_stats'][sido]['area'] += area
            stats['sido_stats'][sido]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    stats['sido_stats'][sido]['price_sum'] += float(price) * area
                except:
                    pass

            sigungu_key = f"{sido} {sigungu}"
            if sigungu_key not in stats['sigungu_stats']:
                stats['sigungu_stats'][sigungu_key] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            stats['sigungu_stats'][sigungu_key]['count'] += 1
            stats['sigungu_stats'][sigungu_key]['area'] += area
            stats['sigungu_stats'][sigungu_key]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    stats['sigungu_stats'][sigungu_key]['price_sum'] += float(price) * area
                except:
                    pass

            if jimok not in stats['jimok_stats']:
                stats['jimok_stats'][jimok] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            stats['jimok_stats'][jimok]['count'] += 1
            stats['jimok_stats'][jimok]['area'] += area
            stats['jimok_stats'][jimok]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    stats['jimok_stats'][jimok]['price_sum'] += float(price) * area
                except:
                    pass

            if owner_type not in stats['owner_stats']:
                stats['owner_stats'][owner_type] = {'count': 0, 'area': 0, 'inclusion_area': 0, 'price_sum': 0}
            stats['owner_stats'][owner_type]['count'] += 1
            stats['owner_stats'][owner_type]['area'] += area
            stats['owner_stats'][owner_type]['inclusion_area'] += inclusion_area
            if price and area > 0:
                try:
                    stats['owner_stats'][owner_type]['price_sum'] += float(price) * area
                except:
                    pass

        stats['summary']['total_count'] = total_count
        stats['summary']['total_area'] = total_area
        stats['summary']['total_inclusion_area'] = total_inclusion_area
        stats['summary']['avg_price'] = sum(prices) / len(prices) if prices else 0
        stats['summary']['max_price'] = max(prices) if prices else 0
        stats['summary']['min_price'] = min(prices) if prices else 0
        stats['summary']['total_price_sum'] = total_price_sum
        stats['summary']['land_count'] = land_count
        stats['summary']['forest_count'] = forest_count
        stats['summary']['owner_count'] = len(owners)

        price_ranges = [
            (0, 10000, "1만원 미만"),
            (10000, 50000, "1~5만원"),
            (50000, 100000, "5~10만원"),
            (100000, 500000, "10~50만원"),
            (500000, 1000000, "50~100만원"),
            (1000000, float('inf'), "100만원 이상")
        ]

        for min_p, max_p, label in price_ranges:
            count = sum(1 for p in prices if min_p <= p < max_p)
            ratio = (count / len(prices) * 100) if prices else 0
            stats['price_distribution'].append({
                'label': label,
                'count': count,
                'ratio': ratio
            })

        return stats
