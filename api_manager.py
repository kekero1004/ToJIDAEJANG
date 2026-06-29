# -*- coding: utf-8 -*-
"""
VWorld API 관리 모듈
- 브이월드 데이터 API 호출
- 건축물대장 API 호출
"""

import json
import ssl
import urllib.request
import urllib.parse

from qgis.core import (
    QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    Qgis, QgsMessageLog
)


class VWorldAPIManager:
    """브이월드 API 관리 클래스"""

    DATA_API_URL = "https://api.vworld.kr/req/data"
    SEARCH_API_URL = "https://api.vworld.kr/req/search"
    BUILDING_API_BASE_URL = "http://apis.data.go.kr/1613000/BldgRgstService_v2/"

    DATA_TYPES = {
        'cadastral': {
            'name': '연속지적도',
            'data': 'LP_PA_CBND_BUBUN',
            'fields': ['pnu', 'jibun', 'bchk', 'addr', 'gosi_year', 'gosi_month', 'jiga']
        },
        'land_forest': {
            'name': '토지임야정보',
            'data': 'LP_PA_CBND_BUBUN',
            'fields': ['pnu', 'ldcg_code', 'ldcg_nm', 'addr']
        },
        'land_character': {
            'name': '토지특성정보',
            'data': 'LP_PA_CBND_BUBUN',
            'fields': ['pnu', 'jimok', 'jibun', 'addr']
        },
        'land_price': {
            'name': '개별공시지가',
            'data': 'LP_PA_CBND_BUBUN',
            'fields': ['pnu', 'jiga', 'gosi_year', 'gosi_month']
        },
        'land_use_plan': {
            'name': '토지이용계획',
            'data': 'getLandUseAttr',
            'fields': ['pnu', 'prposAreaDstrcCodeNm', 'cnflcAt', 'lawNm'] 
        },
        'land_owner': {
            'name': '토지소유자정보',
            'data': 'getPossessionAttr', 
            'fields': ['pnu', 'ownGbaNm', 'ownNm', 'ownAddr'] 
        },
        'building_register': {
            'name': '건축물대장',
            'data': 'building_register', # Custom identifier
            'fields': ['bldNm', 'dongNm', 'mainPurpsCdNm', 'etcPurps', 
                       'platPlc', 'newPlatPlc', 'sigunguCd', 'bjdongCd', 'bun', 'ji',
                       'totArea', 'archArea', 'bcRat', 'vlRat', 'useAprDay', 
                       'grndFlrCnt', 'ugrndFlrCnt', 'hhlCnt', 'fmlyCnt']
        }
    }

    def __init__(self, api_key=""):
        self.api_key = api_key
        self.building_api_key = ""
        self.last_response = None
        self.last_error = None
        self.debug_log = []

        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def set_api_key(self, api_key):
        self.api_key = api_key

    def set_building_api_key(self, key):
        self.building_api_key = key

    def get_cadastral_by_geometry(self, geometry, crs):
        return self._get_data_by_geometry('cadastral', geometry, crs)

    def get_land_use_plan_by_geometry(self, geometry, crs):
        return self._get_data_by_geometry('land_use_plan', geometry, crs)

    def get_building_register_by_geometry(self, geometry, crs):
        """
        geometry를 통해 PNU를 추론하여 건축물대장 API 호출 (Gov Data Portal)
        기본정보(getBrBasisOulnInfo)와 표제부(getBrTitleInfo)만 조회
        """
        if not self.building_api_key:
            self.last_error = "건축물대장 API 키가 설정되지 않았습니다."
            self.debug_log.append("Building API Key missing")
            return None

        # 1. 좌표로 PNU 구하기 (VWorld Cadastral)
        cadastral_data = self.get_cadastral_by_geometry(geometry, crs)
        features = self.parse_features(cadastral_data)
        
        all_buildings = []
        
        for feature in features:
            props = feature.get('properties', {})
            pnu = props.get('pnu')
            if not pnu or len(pnu) < 19:
                continue
                
            # PNU Parsing
            sigungu_cd = pnu[0:5]
            bjdong_cd = pnu[5:10]
            bun = pnu[11:15]
            ji = pnu[15:19]
            
            # Fetch Basic Info
            basic_infos = self._get_gov_building_step(sigungu_cd, bjdong_cd, bun, ji, "getBrBasisOulnInfo")
            if basic_infos:
                for b in basic_infos:
                    b['type_name'] = '기본개요'
                    all_buildings.append({
                        'type': 'Feature',
                        'properties': b,
                        'geometry': None
                    })

            # Fetch Title Info
            title_infos = self._get_gov_building_step(sigungu_cd, bjdong_cd, bun, ji, "getBrTitleInfo")
            if title_infos:
                for b in title_infos:
                    b['type_name'] = '표제부'
                    all_buildings.append({
                        'type': 'Feature',
                        'properties': b,
                        'geometry': None
                    })
                
        return {'features': all_buildings}

    def _get_gov_building_step(self, sigungu_cd, bjdong_cd, bun, ji, operation):
        try:
            params = {
                'serviceKey': urllib.parse.unquote(self.building_api_key), 
                'sigunguCd': sigungu_cd,
                'bjdongCd': bjdong_cd,
                'bun': bun,
                'ji': ji,
                'numOfRows': '100',
                'pageNo': '1',
                '_type': 'json'
            }
            
            query_string = urllib.parse.urlencode({k: v for k, v in params.items() if k != 'serviceKey'})
            url = f"{self.BUILDING_API_BASE_URL}{operation}?serviceKey={self.building_api_key}&{query_string}"
            
            self.debug_log.append(f"Gov Building API ({operation}) Request: {url}")
            
            response = self._make_request(url)
            
            if response:
                body = response.get('response', {}).get('body', {})
                items = body.get('items', {})
                if isinstance(items, dict):
                    # Check if 'item' key exists
                    item_list = items.get('item')
                    if item_list:
                        return item_list if isinstance(item_list, list) else [item_list]
                elif isinstance(items, list):
                    return items
            return []
            
        except Exception as e:
            self.debug_log.append(f"Gov Building API ({operation}) Error: {e}")
            return []

    def get_land_owner_info(self, pnu):
        """토지소유자 정보 조회 (getPossessionAttr)"""
        if not self.api_key:
            return None
            
        url = "http://api.vworld.kr/ned/data/getPossessionAttr"
        params = {
            'key': self.api_key,
            'pnu': pnu,
            'format': 'json',
            'numOfRows': '10',
            'pageNo': '1',
            'domain': 'http://localhost' 
        }
        
        try:
            full_url = f"{url}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(f"Land Owner Request: {full_url}")
            response = self._make_request(full_url)
            
            # Response parsing specific to these APIs
            if response and 'possessions' in response:
                 return response['possessions'].get('field', [])
            return []
        except Exception as e:
            self.debug_log.append(f"Land Owner Error: {e}")
            return None

    def get_land_use_attr(self, pnu):
        """토지이용계획 속성정보 조회 (getLandUseAttr)"""
        if not self.api_key:
            return None
            
        url = "http://api.vworld.kr/ned/data/getLandUseAttr"
        params = {
            'key': self.api_key,
            'pnu': pnu,
            'format': 'json',
            'numOfRows': '10',
            'pageNo': '1',
            'domain': 'http://localhost'
        }
        
        try:
            full_url = f"{url}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(f"Land Use Attr Request: {full_url}")
            response = self._make_request(full_url)
            
            if response and 'landUses' in response:
                 return response['landUses'].get('field', [])
            return []
        except Exception as e:
            self.debug_log.append(f"Land Use Attr Error: {e}")
            return None

    def get_parcel_by_point(self, lon, lat):
        """클릭 지점(WGS84)의 필지 1건 조회 (구역계 필지선택용).

        VWorld Data API GetFeature를 POINT geomFilter + buffer(1m)로 호출해
        해당 좌표를 포함하는 연속지적도 필지 feature(GeoJSON 스타일)를 반환한다.
        실패/없음 시 None.
        """
        if not self.api_key:
            self.last_error = "API 키가 설정되지 않았습니다."
            return None

        try:
            params = {
                'service': 'data',
                'version': '2.0',
                'request': 'GetFeature',
                'key': self.api_key,
                'data': 'LP_PA_CBND_BUBUN',
                'geomFilter': f'POINT({lon} {lat})',
                'buffer': '1',
                'format': 'json',
                'size': '10',
                'crs': 'EPSG:4326',
                'domain': 'localhost'
            }
            url = f"{self.DATA_API_URL}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(f"Parcel by point Request: {url}")

            response = self._make_request(url)
            features = self.parse_features(response)
            if not features:
                return None

            # 여러 건이면 클릭 지점을 실제 포함하는 필지를 우선 선택
            click_geom = QgsGeometry.fromWkt(f'POINT({lon} {lat})')
            for feature in features:
                geom_data = feature.get('geometry')
                if not geom_data:
                    continue
                try:
                    parcel_geom = QgsGeometry.fromWkt(
                        self._geojson_to_wkt(geom_data))
                    if not parcel_geom.isEmpty() and parcel_geom.contains(click_geom):
                        return feature
                except Exception:
                    continue
            return features[0]

        except Exception as e:
            self.last_error = str(e)
            self.debug_log.append(f"Parcel by point Error: {e}")
            return None

    def get_cadastral_by_polygon(self, geometry_wgs84, max_vertices=150):
        """폴리곤(WGS84) 정밀 geomFilter로 연속지적도 조회 (구역계 기반 조회용).

        BOX(바운딩박스) 대신 실제 폴리곤 외곽 링으로 질의해 불필요한 주변 필지를
        줄인다. 꼭짓점이 max_vertices를 초과하면 단계적으로 단순화하고,
        그래도 실패하면 기존 BOX 질의로 폴백한다.
        """
        if not self.api_key:
            self.last_error = "API 키가 설정되지 않았습니다."
            return None

        try:
            geom = QgsGeometry(geometry_wgs84)
            # 멀티폴리곤이면 외곽 헐(convex hull은 과대 — 우선 단순화로 시도)
            tolerance = 0.00005  # 약 5m
            for _ in range(6):
                ring = self._exterior_ring_points(geom)
                if ring and len(ring) <= max_vertices:
                    break
                geom = geom.simplify(tolerance)
                tolerance *= 2
            else:
                ring = None

            if not ring or len(ring) < 4:
                self.debug_log.append(
                    "Polygon filter unavailable -> BOX fallback")
                crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                return self._get_data_by_geometry(
                    'cadastral', geometry_wgs84, crs_wgs84)

            coords = ', '.join(f"{x:.7f} {y:.7f}" for x, y in ring)
            params = {
                'service': 'data',
                'version': '2.0',
                'request': 'GetFeature',
                'key': self.api_key,
                'data': 'LP_PA_CBND_BUBUN',
                'geomFilter': f'POLYGON(({coords}))',
                'format': 'json',
                'size': '1000',
                'crs': 'EPSG:4326',
                'domain': 'localhost'
            }
            url = f"{self.DATA_API_URL}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(
                f"Cadastral by polygon Request ({len(ring)} pts): {url[:300]}...")

            response = self._make_request(url)
            if response is None:
                # URL 길이 초과 등 실패 시 BOX 폴백
                self.debug_log.append("Polygon filter failed -> BOX fallback")
                crs_wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                return self._get_data_by_geometry(
                    'cadastral', geometry_wgs84, crs_wgs84)
            return response

        except Exception as e:
            self.last_error = str(e)
            self.debug_log.append(f"Cadastral by polygon Error: {e}")
            return None

    @staticmethod
    def _exterior_ring_points(geometry):
        """폴리곤/멀티폴리곤의 최대 면적 파트 외곽 링 좌표 [(x, y), ...] 반환"""
        try:
            if geometry.isMultipart():
                parts = geometry.asMultiPolygon()
                if not parts:
                    return None
                largest = max(
                    parts,
                    key=lambda poly: QgsGeometry.fromPolygonXY(poly).area()
                    if poly else 0)
                ring = largest[0] if largest else []
            else:
                poly = geometry.asPolygon()
                ring = poly[0] if poly else []
            return [(p.x(), p.y()) for p in ring]
        except Exception:
            return None

    @staticmethod
    def _geojson_to_wkt(geom_data):
        """GeoJSON 지오메트리 → WKT (Polygon/MultiPolygon만, 내부 유틸)"""
        geom_type = geom_data.get('type', '')
        coordinates = geom_data.get('coordinates', [])
        if geom_type == 'Polygon':
            rings = []
            for ring in coordinates:
                points = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                rings.append(f"({points})")
            return f"POLYGON({', '.join(rings)})"
        if geom_type == 'MultiPolygon':
            polygons = []
            for polygon in coordinates:
                rings = []
                for ring in polygon:
                    points = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON({', '.join(polygons)})"
        return ""

    def search_parcel_by_address(self, address):
        """주소/지번 검색 → 필지 정보 (구역계 주소 업로드용).

        VWorld Search API(type=address, category=parcel)로 주소를 검색해
        {'pnu', 'lon', 'lat', 'title'}를 반환한다. 실패 시 None.
        """
        if not self.api_key or not address:
            return None

        try:
            params = {
                'service': 'search',
                'request': 'search',
                'version': '2.0',
                'key': self.api_key,
                'query': address,
                'type': 'address',
                'category': 'parcel',
                'format': 'json',
                'size': '1',
                'domain': 'localhost'
            }
            url = f"{self.SEARCH_API_URL}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(f"Address Search Request: {url}")

            response = self._make_request(url)
            if not response:
                return None
            resp = response.get('response', {})
            if resp.get('status') != 'OK':
                self.debug_log.append(
                    f"Address search status: {resp.get('status')}")
                return None
            items = resp.get('result', {}).get('items', [])
            if not items:
                return None
            item = items[0]
            point = item.get('point', {})
            return {
                'pnu': item.get('id', ''),
                'lon': float(point.get('x', 0)),
                'lat': float(point.get('y', 0)),
                'title': item.get('title', address),
            }
        except Exception as e:
            self.debug_log.append(f"Address Search Error: {e}")
            return None

    def _get_data_by_geometry(self, data_type, geometry, crs):
        if not self.api_key:
            self.last_error = "API 키가 설정되지 않았습니다."
            return None

        try:
            target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            transform = QgsCoordinateTransform(crs, target_crs, QgsProject.instance())

            geom_transformed = QgsGeometry(geometry)
            geom_transformed.transform(transform)

            bbox = geom_transformed.boundingBox()

            self.debug_log.append(f"BBox: {bbox.xMinimum()}, {bbox.yMinimum()}, {bbox.xMaximum()}, {bbox.yMaximum()}")

            data_config = self.DATA_TYPES.get(data_type, {})

            params = {
                'service': 'data',
                'version': '2.0',
                'request': 'GetFeature',
                'key': self.api_key,
                'data': data_config.get('data', 'LP_PA_CBND_BUBUN'),
                'geomFilter': f'BOX({bbox.xMinimum()},{bbox.yMinimum()},{bbox.xMaximum()},{bbox.yMaximum()})',
                'format': 'json',
                'size': '1000',
                'crs': 'EPSG:4326',
                'domain': 'localhost'
            }

            url = f"{self.DATA_API_URL}?{urllib.parse.urlencode(params)}"
            self.debug_log.append(f"Request URL: {url}")

            response = self._make_request(url)
            return response

        except Exception as e:
            self.last_error = str(e)
            self.debug_log.append(f"Error: {e}")
            QgsMessageLog.logMessage(f"API Error: {e}", "VWorld", Qgis.Critical)
            return None

    def _make_request(self, url):
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) QGIS VWorld Plugin')
            req.add_header('Accept', 'application/json')
            req.add_header('Referer', 'http://localhost')

            with urllib.request.urlopen(req, timeout=30, context=self.ssl_context) as response:
                data = response.read().decode('utf-8')
                self.debug_log.append(f"Response length: {len(data)}")
                self.debug_log.append(f"Response preview: {data[:500]}")

                self.last_response = json.loads(data)
                return self.last_response

        except urllib.error.HTTPError as e:
            self.last_error = f"HTTP 오류 {e.code}: {e.reason}"
            self.debug_log.append(f"HTTP Error: {e.code} - {e.reason}")
            try:
                error_body = e.read().decode('utf-8')
                self.debug_log.append(f"Error body: {error_body[:500]}")
            except:
                pass
            return None
        except urllib.error.URLError as e:
            self.last_error = f"네트워크 오류: {e}"
            self.debug_log.append(f"URL Error: {e}")
            return None
        except json.JSONDecodeError as e:
            self.last_error = f"JSON 파싱 오류: {e}"
            self.debug_log.append(f"JSON Error: {e}")
            return None
        except Exception as e:
            self.last_error = f"요청 오류: {e}"
            self.debug_log.append(f"General Error: {e}")
            return None

    def parse_features(self, response):
        if not response:
            return []

        features = []

        if 'response' in response:
            resp = response['response']
            status = resp.get('status', '')

            self.debug_log.append(f"Response status: {status}")

            if status == 'OK':
                result = resp.get('result', {})

                if 'featureCollection' in result:
                    fc = result['featureCollection']
                    features = fc.get('features', [])
                elif 'features' in result:
                    features = result['features']
                elif 'items' in result:
                    features = result['items']

            elif status == 'NOT_FOUND':
                self.debug_log.append("No data found for the given query")
            else:
                error_msg = resp.get('error', {}).get('text', 'Unknown error')
                self.debug_log.append(f"API Error: {error_msg}")

        elif 'features' in response:
            features = response['features']
        elif 'result' in response and isinstance(response['result'], list):
            features = response['result']

        self.debug_log.append(f"Parsed features count: {len(features)}")
        return features

    def get_debug_log(self):
        return "\n".join(self.debug_log)

    def clear_debug_log(self):
        self.debug_log = []
